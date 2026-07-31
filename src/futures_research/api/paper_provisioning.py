"""One-off P6 provisioning authorization and read-only preflight wire."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from futures_research.api.paper_traders import (
    PaperExternalReadinessProvider,
    PaperReadiness,
    PaperRequestConflictError,
    PaperTraderSelection,
    evaluate_paper_provisioning_runtime_readiness,
)
from futures_research.api.promotion_decisions import PromotionDecisionStore
from futures_research.api.results_catalog import ResultsCatalog
from futures_research.data.contracts import ContractRegistry

PaperProvisioningAuthorizationState = Literal[
    "disabled",
    "armed",
    "claimed",
    "consumed",
    "expired",
]

PAPER_PROVISIONING_AUTHORIZATION_REASON = (
    "Owner-approved one-off P6 provision-only flow verification"
)
PAPER_PROVISIONING_DISABLED_REASON = (
    "paper trader provisioning is disabled in the normal runtime"
)

_OPERATION_ID_PATTERN = re.compile(r"^paper-provision-[0-9a-f]{32}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_AUTHORIZATION_WINDOW = timedelta(minutes=30)
_PROCESS_POLICY_LOCK = RLock()


class PaperProvisioningNotAuthorizedError(RuntimeError):
    """The normal or expired runtime has no usable one-off permit."""


class PaperProvisioningSelectionMismatchError(RuntimeError):
    """The create selection differs from the one authorized operation."""


class PaperProvisioningRequestConflictError(RuntimeError):
    """The one-off permit is already bound to another browser request."""


class _StrictProvisioningModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_by_alias=True,
        validate_by_name=False,
    )


class PaperProvisioningReadinessRequest(_StrictProvisioningModel):
    schema_version: Literal["paper_provisioning_readiness_request.v1"] = Field(
        alias="schema"
    )
    selection: PaperTraderSelection


class PaperProvisioningClaim(_StrictProvisioningModel):
    request_id: str = Field(min_length=1, max_length=64)
    request_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection: PaperTraderSelection
    claimed_at: str

    @field_validator("request_id")
    @classmethod
    def require_canonical_uuid4(cls, value: str) -> str:
        return _canonical_uuid4(value)

    @field_validator("claimed_at")
    @classmethod
    def require_claimed_at(cls, value: str) -> str:
        return _canonical_utc_text(value)


class PaperProvisioningAuthorization(_StrictProvisioningModel):
    schema_version: Literal["paper_provisioning_authorization.v1"] = Field(
        alias="schema"
    )
    state: PaperProvisioningAuthorizationState
    operation_id: str | None
    authorized_at: str | None
    expires_at: str | None
    reason: str

    @field_validator("operation_id")
    @classmethod
    def require_operation_id(cls, value: str | None) -> str | None:
        if value is not None and _OPERATION_ID_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "operation_id must use paper-provision plus 32 lowercase hex"
            )
        return value

    @field_validator("authorized_at", "expires_at")
    @classmethod
    def require_authorization_timestamp(
        cls,
        value: str | None,
    ) -> str | None:
        return _canonical_utc_text(value) if value is not None else None

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="authorization reason")

    @model_validator(mode="after")
    def require_exact_state_shape(self) -> PaperProvisioningAuthorization:
        identity = (
            self.operation_id,
            self.authorized_at,
            self.expires_at,
        )
        if self.state == "disabled":
            if any(value is not None for value in identity):
                raise ValueError(
                    "disabled authorization must hide all operation identity"
                )
            return self
        if any(value is None for value in identity):
            raise ValueError(
                "authorized states require complete operation identity"
            )
        if self.authorized_at is None or self.expires_at is None:
            raise AssertionError("non-disabled identity was not narrowed")
        authorized_at = _parse_canonical_utc(self.authorized_at)
        expires_at = _parse_canonical_utc(self.expires_at)
        if expires_at - authorized_at != _AUTHORIZATION_WINDOW:
            raise ValueError(
                "expires_at must equal authorized_at plus exactly 30 minutes"
            )
        return self


class PaperProvisioningReadiness(_StrictProvisioningModel):
    schema_version: Literal["paper_provisioning_readiness.v1"] = Field(
        alias="schema"
    )
    selection: PaperTraderSelection
    can_provision: bool
    authorization: PaperProvisioningAuthorization
    runtime_readiness: PaperReadiness

    @model_validator(mode="after")
    def require_consistent_projection(self) -> PaperProvisioningReadiness:
        if self.selection != self.runtime_readiness.selection:
            raise ValueError(
                "selection must equal runtime_readiness selection"
            )
        expected = _can_provision(
            self.authorization,
            self.runtime_readiness,
        )
        if self.can_provision is not expected:
            raise ValueError(
                "can_provision must be true exactly for an armed projection"
            )
        return self


@dataclass(frozen=True, slots=True)
class PaperProvisioningCreateInspection:
    """Server-only exact permit truth used before any P6 store access."""

    authorization: PaperProvisioningAuthorization
    claim: PaperProvisioningClaim | None


@runtime_checkable
class PaperProvisioningAuthorizationPolicy(Protocol):
    def authorization_for(
        self,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        """Return a request-relative projection without claiming the permit."""

    def inspect_for_create(
        self,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningCreateInspection:
        """Return exact server truth without claiming or concealing mismatch."""

    def claim(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        """Atomically bind the first valid browser request."""

    def record_append_failure(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        """Keep a claimed binding claimed after an append failure."""

    def mark_consumed(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        """Mark the exact claimed binding consumed after append or replay."""


class DisabledPaperProvisioningAuthorizationPolicy:
    """Normal restart behavior: no operation exists and no state is recovered."""

    __slots__ = ()

    def authorization_for(
        self,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        del selection
        return _disabled_authorization()

    def inspect_for_create(
        self,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningCreateInspection:
        del selection
        raise PaperProvisioningNotAuthorizedError(
            "paper trader provisioning is disabled in the normal runtime"
        )

    def claim(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        del request_id, request_payload_sha256, selection
        raise PaperProvisioningNotAuthorizedError(
            "paper trader provisioning is disabled in the normal runtime"
        )

    def record_append_failure(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        del request_id, request_payload_sha256, selection
        raise PaperProvisioningNotAuthorizedError(
            "paper trader provisioning is disabled in the normal runtime"
        )

    def mark_consumed(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        del request_id, request_payload_sha256, selection
        raise PaperProvisioningNotAuthorizedError(
            "paper trader provisioning is disabled in the normal runtime"
        )


class OneOffPaperProvisioningAuthorizationPolicy:
    """Process-local exact-selection permit with an atomic one-request claim."""

    __slots__ = (
        "_authorized_at",
        "_authorized_selection",
        "_binding",
        "_clock",
        "_expires_at",
        "_operation_id",
        "_state",
    )

    def __init__(
        self,
        *,
        operation_id: str,
        authorized_selection: PaperTraderSelection,
        authorized_at: datetime,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(authorized_selection, PaperTraderSelection):
            raise TypeError(
                "authorized_selection must be an exact PaperTraderSelection"
            )
        normalized_authorized_at = _utc_datetime(authorized_at)
        expires_at = normalized_authorized_at + _AUTHORIZATION_WINDOW
        authorization = PaperProvisioningAuthorization(
            schema="paper_provisioning_authorization.v1",
            state="armed",
            operation_id=operation_id,
            authorized_at=_timestamp_text(normalized_authorized_at),
            expires_at=_timestamp_text(expires_at),
            reason=PAPER_PROVISIONING_AUTHORIZATION_REASON,
        )
        self._operation_id = authorization.operation_id
        self._authorized_selection = authorized_selection
        self._authorized_at = normalized_authorized_at
        self._expires_at = expires_at
        self._clock = clock or (lambda: datetime.now(UTC))
        self._state: PaperProvisioningAuthorizationState = "armed"
        self._binding: PaperProvisioningClaim | None = None

    def authorization_for(
        self,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        with _PROCESS_POLICY_LOCK:
            if selection != self._authorized_selection:
                return _disabled_authorization()
            self._expire_unclaimed_if_needed()
            return self._authorization()

    def inspect_for_create(
        self,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningCreateInspection:
        with _PROCESS_POLICY_LOCK:
            if selection != self._authorized_selection:
                raise PaperProvisioningSelectionMismatchError(
                    "paper provisioning selection differs from the authorized operation"
                )
            self._expire_unclaimed_if_needed()
            if self._state == "expired":
                raise PaperProvisioningNotAuthorizedError(
                    "paper provisioning authorization expired before first claim"
                )
            if self._state not in ("armed", "claimed", "consumed"):
                raise PaperProvisioningNotAuthorizedError(
                    "paper provisioning authorization is not available"
                )
            return PaperProvisioningCreateInspection(
                authorization=self._authorization(),
                claim=self._binding,
            )

    def claim(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        with _PROCESS_POLICY_LOCK:
            if selection != self._authorized_selection:
                raise PaperProvisioningSelectionMismatchError(
                    "paper provisioning selection differs from the authorized operation"
                )
            now = self._now()
            candidate = PaperProvisioningClaim(
                request_id=request_id,
                request_payload_sha256=request_payload_sha256,
                selection=selection,
                claimed_at=_timestamp_text(now),
            )
            if self._state == "armed":
                if not now < self._expires_at:
                    self._state = "expired"
                    raise PaperProvisioningNotAuthorizedError(
                        "paper provisioning authorization expired before first claim"
                    )
                self._binding = candidate
                self._state = "claimed"
                return self._authorization()
            if self._state in ("claimed", "consumed"):
                self._assert_same_binding(candidate)
                return self._authorization()
            raise PaperProvisioningNotAuthorizedError(
                "paper provisioning authorization is not available"
            )

    def record_append_failure(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        with _PROCESS_POLICY_LOCK:
            candidate = self._candidate_binding(
                request_id=request_id,
                request_payload_sha256=request_payload_sha256,
                selection=selection,
            )
            if self._state != "claimed":
                raise PaperProvisioningNotAuthorizedError(
                    "only a claimed paper provisioning request can record failure"
                )
            self._assert_same_binding(candidate)
            return self._authorization()

    def mark_consumed(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        with _PROCESS_POLICY_LOCK:
            candidate = self._candidate_binding(
                request_id=request_id,
                request_payload_sha256=request_payload_sha256,
                selection=selection,
            )
            if self._state not in ("claimed", "consumed"):
                raise PaperProvisioningNotAuthorizedError(
                    "only a claimed paper provisioning request can be consumed"
                )
            self._assert_same_binding(candidate)
            self._state = "consumed"
            return self._authorization()

    def _candidate_binding(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningClaim:
        if selection != self._authorized_selection:
            raise PaperProvisioningSelectionMismatchError(
                "paper provisioning selection differs from the authorized operation"
            )
        return PaperProvisioningClaim(
            request_id=request_id,
            request_payload_sha256=request_payload_sha256,
            selection=selection,
            claimed_at=_timestamp_text(self._now()),
        )

    def _assert_same_binding(
        self,
        candidate: PaperProvisioningClaim,
    ) -> None:
        binding = self._binding
        if binding is None:
            raise PaperProvisioningNotAuthorizedError(
                "paper provisioning authorization has no claimed binding"
            )
        if (
            candidate.request_id == binding.request_id
            and candidate.request_payload_sha256
            != binding.request_payload_sha256
        ):
            raise PaperRequestConflictError(
                f"request_id {candidate.request_id} is already bound "
                "to a different intent"
            )
        if (
            candidate.request_id != binding.request_id
            or candidate.request_payload_sha256
            != binding.request_payload_sha256
            or candidate.selection != binding.selection
        ):
            raise PaperProvisioningRequestConflictError(
                "paper provisioning authorization is already claimed "
                "by another request"
            )

    def _expire_unclaimed_if_needed(self) -> None:
        if self._state == "armed" and not self._now() < self._expires_at:
            self._state = "expired"

    def _now(self) -> datetime:
        return _utc_datetime(self._clock())

    def _authorization(self) -> PaperProvisioningAuthorization:
        if self._operation_id is None:
            raise AssertionError("one-off operation identity was not narrowed")
        return PaperProvisioningAuthorization(
            schema="paper_provisioning_authorization.v1",
            state=self._state,
            operation_id=self._operation_id,
            authorized_at=_timestamp_text(self._authorized_at),
            expires_at=_timestamp_text(self._expires_at),
            reason=PAPER_PROVISIONING_AUTHORIZATION_REASON,
        )


def evaluate_paper_provisioning_readiness(
    *,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    eligibility_store: PromotionDecisionStore,
    provider: PaperExternalReadinessProvider,
    policy: PaperProvisioningAuthorizationPolicy,
    selection: PaperTraderSelection,
) -> PaperProvisioningReadiness:
    """Validate immutable truth, then project authorization without claiming."""
    runtime_readiness, _baseline = evaluate_paper_provisioning_runtime_readiness(
        catalog=catalog,
        registry=registry,
        eligibility_store=eligibility_store,
        provider=provider,
        selection=selection,
    )
    authorization = policy.authorization_for(selection)
    return PaperProvisioningReadiness(
        schema="paper_provisioning_readiness.v1",
        selection=selection,
        can_provision=_can_provision(authorization, runtime_readiness),
        authorization=authorization,
        runtime_readiness=runtime_readiness,
    )


def _disabled_authorization() -> PaperProvisioningAuthorization:
    return PaperProvisioningAuthorization(
        schema="paper_provisioning_authorization.v1",
        state="disabled",
        operation_id=None,
        authorized_at=None,
        expires_at=None,
        reason=PAPER_PROVISIONING_DISABLED_REASON,
    )


def _can_provision(
    authorization: PaperProvisioningAuthorization,
    runtime_readiness: PaperReadiness,
) -> bool:
    baseline_integrity = next(
        check
        for check in runtime_readiness.checks
        if check.key == "baseline_integrity"
    )
    return (
        authorization.state == "armed"
        and baseline_integrity.status == "ready"
    )


def _canonical_uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ValueError(
            "request_id must be a canonical lowercase UUID4"
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("request_id must be a canonical lowercase UUID4")
    return value


def _canonical_utc_text(value: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be canonical timezone-aware UTC Z text")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(
            "timestamp must be canonical timezone-aware UTC Z text"
        ) from exc
    if _timestamp_text(parsed) != value:
        raise ValueError("timestamp must be canonical timezone-aware UTC Z text")
    return value


def _parse_canonical_utc(value: str) -> datetime:
    _canonical_utc_text(value)
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _utc_datetime(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("paper provisioning clock must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    return _utc_datetime(value).isoformat().replace("+00:00", "Z")


def _untrimmed_text(value: str, *, field_name: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-blank untrimmed text")
    return value
