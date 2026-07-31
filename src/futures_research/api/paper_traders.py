"""Isolated P6 Stage A baseline, readiness, and immutable provisioning seams."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Literal, Protocol, cast, runtime_checkable
from urllib.parse import quote
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from futures_research.api.paper_ledger import (
    PAPER_LEDGER_EXPECTED_TABLE_SQL,
    PAPER_LEDGER_EXPECTED_TRIGGER_SQL,
    PAPER_LEDGER_SCHEMA_SQL,
    PaperLedgerBaselineCapture,
    PaperLedgerEvidenceError,
    PaperLedgerExpectedTrader,
    PaperLedgerOriginSeed,
    PaperLedgerPersistenceError,
    assert_paper_ledger_for_trader,
    capture_paper_ledger_baseline,
    insert_paper_ledger_origin,
)
from futures_research.api.promotion_decisions import PromotionDecisionStore
from futures_research.api.results_catalog import (
    ResultArtifactIntegrityError,
    ResultNotFoundError,
    ResultsCatalog,
)
from futures_research.data.contracts import ContractRegistry, ContractSpec

if TYPE_CHECKING:
    from futures_research.api.paper_provisioning import (
        PaperProvisioningAuthorization,
        PaperProvisioningAuthorizationPolicy,
    )

PaperReadinessStatus = Literal["ready", "blocked", "unknown"]
PaperOverallReadiness = Literal["ready", "blocked"]
PaperMarketSession = Literal["open", "closed", "unknown"]
PaperReadinessKey = Literal[
    "ib_realtime",
    "exchange_calendar",
    "telegram",
    "baseline_integrity",
]

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROVISIONING_OPERATION_ID_PATTERN = re.compile(
    r"^paper-provision-[0-9a-f]{32}$"
)
_TRADER_ID_PATTERN = re.compile(r"^trader-[0-9a-f]{32}$")
_ACCOUNT_ID_PATTERN = re.compile(r"^paper-account-[0-9a-f]{32}$")
_READINESS_KEYS: tuple[PaperReadinessKey, ...] = (
    "ib_realtime",
    "exchange_calendar",
    "telegram",
    "baseline_integrity",
)
_INITIAL_EVENTS = (
    "trader_created",
    "account_created",
    "provisioning_authorized",
    "provisioned",
)
_LIFECYCLE_REASON: Literal[
    "simulation runtime is not activated in Stage A"
] = "simulation runtime is not activated in Stage A"


class PaperEligibilityRequiredError(RuntimeError):
    """Reserved transport error for a strategy identity that is not available.

    Historical note: this used to mean "no PromotionDecision ``use`` record".
    Page independence no longer gates paper on promotion; call sites either
    soft-empty (no candidates) or enforce identity via baseline resolution.
    The exception type remains so API error mapping stays stable if a future
    confirmed-strategy check reuses the same code path.
    """


class PaperBaselineNotFoundError(LookupError):
    """The selected immutable baseline run does not exist."""


class PaperSelectionIdentityMismatchError(RuntimeError):
    """A stale client selection differs from server-derived immutable truth."""


class PaperBaselineIntegrityError(RuntimeError):
    """The selected result package or contract truth is not trustworthy."""


class PaperReadinessBlockedError(RuntimeError):
    """At least one fresh readiness check is blocked or unknown."""


class PaperExternalReadinessInvalidError(RuntimeError):
    """The external readiness provider returned structurally invalid truth."""


class PaperActivationNotAuthorizedError(RuntimeError):
    """The normal Stage A runtime is intentionally default-deny."""


class PaperRequestConflictError(RuntimeError):
    """A request UUID is already bound to a different canonical intent."""


class PaperRequestNotFoundError(LookupError):
    """A trader or create request does not exist in the isolated store."""


class PaperTraderStoreIntegrityError(RuntimeError):
    """The append-only P6 store is malformed or is missing a required guard."""


class PaperTraderStoreSchemaUpgradeRequiredError(PaperTraderStoreIntegrityError):
    """An unversioned or Stage A v1 store is never migrated or self-healed."""


class _StrictPaperModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        populate_by_name=True,
    )


class PaperContractSelection(_StrictPaperModel):
    strategy_id: str = Field(min_length=1, max_length=256)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("strategy_id")
    @classmethod
    def require_untrimmed_identity(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="selection identity")


class PaperBaselineSelection(PaperContractSelection):
    contract_id: str = Field(min_length=1, max_length=256)

    @field_validator("contract_id")
    @classmethod
    def require_untrimmed_contract_id(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="contract_id")


class PaperTraderSelection(PaperBaselineSelection):
    baseline_run_id: str = Field(min_length=1, max_length=256)
    baseline_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("baseline_run_id")
    @classmethod
    def require_untrimmed_run_id(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="baseline_run_id")


class PaperReadinessRequest(_StrictPaperModel):
    schema_version: Literal["paper_readiness_request.v1"] = Field(alias="schema")
    selection: PaperTraderSelection


class PaperTraderCreateRequest(_StrictPaperModel):
    schema_version: Literal["paper_trader_create_request.v1"] = Field(alias="schema")
    request_id: str = Field(min_length=1, max_length=64)
    selection: PaperTraderSelection

    @field_validator("request_id")
    @classmethod
    def require_canonical_uuid4(cls, value: str) -> str:
        return _canonical_uuid4(value)


class PaperBaseline(_StrictPaperModel):
    run_id: str
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    range_start: str
    range_end: str
    currency: str
    initial_capital: float
    trade_count: int
    net_r: float
    validation_run: Literal[False]
    integrity: Literal["verified"]

    @field_validator("run_id", "currency")
    @classmethod
    def require_untrimmed_text(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="baseline text")

    @field_validator("range_start", "range_end")
    @classmethod
    def require_utc_timestamp(cls, value: str) -> str:
        return _canonical_utc_text(value)

    @field_validator("initial_capital", mode="before")
    @classmethod
    def require_positive_capital(cls, value: object) -> float:
        number = _finite_number(value, field_name="initial_capital")
        if number <= 0:
            raise ValueError("initial_capital must be greater than zero")
        return float(number)

    @field_validator("net_r", mode="before")
    @classmethod
    def require_finite_net_r(cls, value: object) -> float:
        return float(_finite_number(value, field_name="net_r"))

    @field_validator("trade_count", mode="before")
    @classmethod
    def require_nonnegative_trade_count(cls, value: object) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("trade_count must be a non-negative JSON integer")
        return value


class PaperBaselineList(_StrictPaperModel):
    schema_version: Literal["paper_baseline_list.v1"] = Field(alias="schema")
    selection: PaperBaselineSelection
    count: int
    baselines: tuple[PaperBaseline, ...]

    @model_validator(mode="after")
    def require_exact_count(self) -> PaperBaselineList:
        if self.count != len(self.baselines):
            raise ValueError("baseline count must exactly equal baselines length")
        return self


class PaperContractCandidate(_StrictPaperModel):
    contract_id: str
    symbol: str
    display_name: str

    @field_validator("contract_id", "symbol", "display_name")
    @classmethod
    def require_canonical_text(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="contract candidate")


class PaperContractList(_StrictPaperModel):
    schema_version: Literal["paper_contract_list.v1"] = Field(alias="schema")
    selection: PaperContractSelection
    count: int
    contracts: tuple[PaperContractCandidate, ...]

    @model_validator(mode="after")
    def require_exact_unique_count(self) -> PaperContractList:
        identities = tuple(contract.contract_id for contract in self.contracts)
        if self.count != len(self.contracts):
            raise ValueError("contract count must exactly equal contracts length")
        if len(set(identities)) != len(identities):
            raise ValueError("contract_id must be unique")
        if identities != tuple(sorted(identities)):
            raise ValueError("contract candidates must use canonical order")
        return self


class PaperReadinessCheck(_StrictPaperModel):
    key: PaperReadinessKey
    status: PaperReadinessStatus
    reason: str
    checked_at: str

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="readiness reason")

    @field_validator("checked_at")
    @classmethod
    def require_checked_at(cls, value: str) -> str:
        return _canonical_utc_text(value)


class PaperReadinessSnapshot(_StrictPaperModel):
    schema_version: Literal["paper_readiness_snapshot.v1"] = Field(alias="schema")
    overall: PaperOverallReadiness
    market_session: PaperMarketSession
    checked_at: str
    checks: tuple[PaperReadinessCheck, ...]

    @field_validator("checked_at")
    @classmethod
    def require_checked_at(cls, value: str) -> str:
        return _canonical_utc_text(value)

    @model_validator(mode="after")
    def require_consistent_checks(self) -> PaperReadinessSnapshot:
        _assert_readiness_projection(self.overall, self.checked_at, self.checks)
        return self


class PaperReadiness(_StrictPaperModel):
    schema_version: Literal["paper_readiness.v1"] = Field(alias="schema")
    selection: PaperTraderSelection
    overall: PaperOverallReadiness
    market_session: PaperMarketSession
    checked_at: str
    checks: tuple[PaperReadinessCheck, ...]

    @field_validator("checked_at")
    @classmethod
    def require_checked_at(cls, value: str) -> str:
        return _canonical_utc_text(value)

    @model_validator(mode="after")
    def require_consistent_checks(self) -> PaperReadiness:
        _assert_readiness_projection(self.overall, self.checked_at, self.checks)
        return self

    def snapshot(self) -> PaperReadinessSnapshot:
        return PaperReadinessSnapshot(
            schema="paper_readiness_snapshot.v1",
            overall=self.overall,
            market_session=self.market_session,
            checked_at=self.checked_at,
            checks=self.checks,
        )


class PaperProvisioningOrigin(_StrictPaperModel):
    """Exact immutable authorization truth stored as creation event ordinal 3."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    schema_version: Literal["paper_provisioning_origin.v1"] = Field(
        alias="schema"
    )
    operation_id: str
    request_id: str
    request_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection: PaperTraderSelection
    authorized_at: str
    expires_at: str
    claimed_at: str
    reason: str
    runtime_readiness: PaperReadinessSnapshot

    @field_validator("operation_id")
    @classmethod
    def require_operation_id(cls, value: str) -> str:
        if _PROVISIONING_OPERATION_ID_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "operation_id must use paper-provision plus 32 lowercase hex"
            )
        return value

    @field_validator("request_id")
    @classmethod
    def require_request_id(cls, value: str) -> str:
        return _canonical_uuid4(value)

    @field_validator("authorized_at", "expires_at", "claimed_at")
    @classmethod
    def require_timestamp(cls, value: str) -> str:
        return _canonical_utc_text(value)

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="provisioning reason")

    @model_validator(mode="after")
    def require_timestamp_order(self) -> PaperProvisioningOrigin:
        authorized_at = _utc_timestamp_value(self.authorized_at)
        expires_at = _utc_timestamp_value(self.expires_at)
        claimed_at = _utc_timestamp_value(self.claimed_at)
        if expires_at - authorized_at != timedelta(minutes=30):
            raise ValueError(
                "provisioning expiry must be exactly 30 minutes after authorization"
            )
        if not authorized_at <= claimed_at < expires_at:
            raise ValueError(
                "provisioning timestamps must satisfy authorized <= claimed < expires"
            )
        return self


class PaperStrategyIdentity(_StrictPaperModel):
    strategy_id: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PaperLockedBaseline(_StrictPaperModel):
    run_id: str
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    range_start: str
    range_end: str

    @field_validator("range_start", "range_end")
    @classmethod
    def require_utc_timestamp(cls, value: str) -> str:
        return _canonical_utc_text(value)


class PaperAccountOrigin(_StrictPaperModel):
    account_id: str
    currency: str
    initial_capital: float

    @field_validator("account_id")
    @classmethod
    def require_account_id(cls, value: str) -> str:
        if _ACCOUNT_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("account_id must use the canonical paper-account identity")
        return value

    @field_validator("currency")
    @classmethod
    def require_currency(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="account currency")

    @field_validator("initial_capital", mode="before")
    @classmethod
    def require_positive_capital(cls, value: object) -> float:
        number = _finite_number(value, field_name="initial_capital")
        if number <= 0:
            raise ValueError("initial_capital must be greater than zero")
        return float(number)


class PaperSafeguards(_StrictPaperModel):
    max_drawdown_r: Literal[8]
    max_losing_streak: Literal[8]
    blind_minutes: Literal[5]


class PaperLifecycle(_StrictPaperModel):
    status: Literal["provisioned"]
    reason: Literal["simulation runtime is not activated in Stage A"]
    as_of: str

    @field_validator("as_of")
    @classmethod
    def require_as_of(cls, value: str) -> str:
        return _canonical_utc_text(value)


class PaperTrader(_StrictPaperModel):
    schema_version: Literal["paper_trader.v1"] = Field(alias="schema")
    trader_id: str
    request_id: str
    strategy: PaperStrategyIdentity
    contract_id: str
    baseline: PaperLockedBaseline
    account: PaperAccountOrigin
    safeguards: PaperSafeguards
    lifecycle: PaperLifecycle
    readiness_snapshot: PaperReadinessSnapshot
    created_at: str

    @field_validator("trader_id")
    @classmethod
    def require_trader_id(cls, value: str) -> str:
        if _TRADER_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("trader_id must use the canonical trader identity")
        return value

    @field_validator("request_id")
    @classmethod
    def require_request_id(cls, value: str) -> str:
        return _canonical_uuid4(value)

    @field_validator("contract_id")
    @classmethod
    def require_contract_id(cls, value: str) -> str:
        return _untrimmed_text(value, field_name="contract_id")

    @field_validator("created_at")
    @classmethod
    def require_created_at(cls, value: str) -> str:
        return _canonical_utc_text(value)


class PaperTraderRequestStatus(_StrictPaperModel):
    schema_version: Literal["paper_trader_request_status.v1"] = Field(alias="schema")
    request_id: str
    status: Literal["completed"]
    trader: PaperTrader

    @field_validator("request_id")
    @classmethod
    def require_request_id(cls, value: str) -> str:
        return _canonical_uuid4(value)


class PaperTraderList(_StrictPaperModel):
    schema_version: Literal["paper_trader_list.v1"] = Field(alias="schema")
    count: int
    traders: tuple[PaperTrader, ...]

    @model_validator(mode="after")
    def require_exact_count(self) -> PaperTraderList:
        if self.count != len(self.traders):
            raise ValueError("trader count must exactly equal traders length")
        return self


@dataclass(frozen=True, slots=True)
class PaperReadinessSignal:
    status: PaperReadinessStatus
    reason: str


@dataclass(frozen=True, slots=True)
class PaperExternalReadiness:
    checked_at: datetime
    market_session: PaperMarketSession
    ib_realtime: PaperReadinessSignal
    exchange_calendar: PaperReadinessSignal
    telegram: PaperReadinessSignal


@runtime_checkable
class PaperExternalReadinessProvider(Protocol):
    def check(self, selection: PaperTraderSelection) -> PaperExternalReadiness:
        """Return external truth without opening IB or sending Telegram."""


@runtime_checkable
class PaperActivationPolicy(Protocol):
    def is_authorized(self, selection: PaperTraderSelection) -> bool:
        """Return whether this runtime may open an isolated P6 store."""


@dataclass(frozen=True, slots=True)
class DefaultDenyPaperActivationPolicy:
    def is_authorized(self, selection: PaperTraderSelection) -> bool:
        del selection
        return False


@dataclass(frozen=True, slots=True)
class AllowIsolatedPaperActivationPolicy:
    def is_authorized(self, selection: PaperTraderSelection) -> bool:
        del selection
        return True


@dataclass(frozen=True, slots=True)
class DefaultPaperExternalReadinessProvider:
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def check(self, selection: PaperTraderSelection) -> PaperExternalReadiness:
        del selection
        return PaperExternalReadiness(
            checked_at=self.clock(),
            market_session="unknown",
            ib_realtime=PaperReadinessSignal(
                status="unknown",
                reason="IB API real-time session has not been verified",
            ),
            exchange_calendar=PaperReadinessSignal(
                status="unknown",
                reason="exchange calendar readiness has not been verified",
            ),
            telegram=PaperReadinessSignal(
                status="unknown",
                reason="Telegram readiness has not been verified",
            ),
        )


@dataclass(frozen=True, slots=True)
class IsolatedPaperExternalReadinessProvider:
    checked_at: datetime
    market_session: PaperMarketSession = "closed"
    ib_realtime: PaperReadinessSignal = PaperReadinessSignal(
        status="ready",
        reason="isolated provider confirmed for this integration runtime",
    )
    exchange_calendar: PaperReadinessSignal = PaperReadinessSignal(
        status="ready",
        reason="isolated provider confirmed for this integration runtime",
    )
    telegram: PaperReadinessSignal = PaperReadinessSignal(
        status="ready",
        reason="isolated provider confirmed for this integration runtime",
    )

    def check(self, selection: PaperTraderSelection) -> PaperExternalReadiness:
        del selection
        return PaperExternalReadiness(
            checked_at=self.checked_at,
            market_session=self.market_session,
            ib_realtime=self.ib_realtime,
            exchange_calendar=self.exchange_calendar,
            telegram=self.telegram,
        )


@dataclass(frozen=True, slots=True)
class ResolvedPaperBaseline:
    public: PaperBaseline
    strategy_id: str
    strategy_content_sha256: str
    strategy_name: str
    contract: ContractSpec
    ledger_capture: PaperLedgerBaselineCapture

    @property
    def contract_id(self) -> str:
        return self.contract.contract_id


def require_paper_eligibility(
    store: PromotionDecisionStore,
    selection: PaperContractSelection | PaperBaselineSelection | PaperTraderSelection,
) -> None:
    """No hard gate across pages.

    Paper selects strategies from backend/DB truth (confirmed versions and/or
    verified baselines). A missing PromotionDecision ``use`` record is Owner
    history only — never a reason to refuse contracts, baselines, readiness,
    or provisioning. Identity is still enforced when a baseline is resolved.
    """
    del store, selection


def list_paper_contracts(
    *,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    eligibility_store: PromotionDecisionStore,
    selection: PaperContractSelection,
) -> PaperContractList:
    """Project the shared verified-baseline predicate into contract choices."""
    require_paper_eligibility(eligibility_store, selection)
    resolved = _list_paper_baseline_candidates(
        catalog=catalog,
        registry=registry,
        strategy_id=selection.strategy_id,
        strategy_content_sha256=selection.content_sha256,
    )
    contracts: dict[str, PaperContractCandidate] = {}
    try:
        for candidate in resolved:
            contract = candidate.contract
            contracts.setdefault(
                contract.contract_id,
                PaperContractCandidate(
                    contract_id=contract.contract_id,
                    symbol=contract.symbol,
                    display_name=contract.display_name,
                ),
            )
        ordered = tuple(contracts[key] for key in sorted(contracts))
        return PaperContractList(
            schema="paper_contract_list.v1",
            selection=selection,
            count=len(ordered),
            contracts=ordered,
        )
    except ValidationError as exc:
        raise PaperBaselineIntegrityError(
            "contract registry contains invalid candidate identity text"
        ) from exc


def list_paper_baselines(
    *,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    eligibility_store: PromotionDecisionStore,
    selection: PaperBaselineSelection,
) -> PaperBaselineList:
    require_paper_eligibility(eligibility_store, selection)
    resolved = _list_paper_baseline_candidates(
        catalog=catalog,
        registry=registry,
        strategy_id=selection.strategy_id,
        strategy_content_sha256=selection.content_sha256,
        contract_id=selection.contract_id,
    )
    candidates = [candidate.public for candidate in resolved]
    candidates.sort(key=lambda baseline: baseline.run_id)
    candidates.sort(
        key=lambda baseline: _utc_timestamp_value(baseline.range_end),
        reverse=True,
    )
    return PaperBaselineList(
        schema="paper_baseline_list.v1",
        selection=selection,
        count=len(candidates),
        baselines=tuple(candidates),
    )


def evaluate_paper_readiness(
    *,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    eligibility_store: PromotionDecisionStore,
    provider: PaperExternalReadinessProvider,
    selection: PaperTraderSelection,
) -> tuple[PaperReadiness, ResolvedPaperBaseline]:
    require_paper_eligibility(eligibility_store, selection)
    baseline = resolve_selected_paper_baseline(
        catalog=catalog,
        registry=registry,
        selection=selection,
    )
    try:
        external = provider.check(selection)
        checked_at = _timestamp_text(external.checked_at)
        checks = (
            _readiness_check("ib_realtime", external.ib_realtime, checked_at),
            _readiness_check(
                "exchange_calendar",
                external.exchange_calendar,
                checked_at,
            ),
            _readiness_check("telegram", external.telegram, checked_at),
            PaperReadinessCheck(
                key="baseline_integrity",
                status="ready",
                reason="locked result package verified",
                checked_at=checked_at,
            ),
        )
        overall: PaperOverallReadiness = (
            "ready" if all(check.status == "ready" for check in checks) else "blocked"
        )
        readiness = PaperReadiness(
            schema="paper_readiness.v1",
            selection=selection,
            overall=overall,
            market_session=external.market_session,
            checked_at=checked_at,
            checks=checks,
        )
    except PaperReadinessBlockedError:
        raise
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise PaperReadinessBlockedError(
            "external readiness provider returned malformed truth"
        ) from exc
    return readiness, baseline


def evaluate_paper_provisioning_runtime_readiness(
    *,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    eligibility_store: PromotionDecisionStore,
    provider: PaperExternalReadinessProvider,
    selection: PaperTraderSelection,
) -> tuple[PaperReadiness, ResolvedPaperBaseline]:
    """Share one malformed-provider semantic across provisioning seams."""
    try:
        return evaluate_paper_readiness(
            catalog=catalog,
            registry=registry,
            eligibility_store=eligibility_store,
            provider=provider,
            selection=selection,
        )
    except PaperReadinessBlockedError as exc:
        raise PaperExternalReadinessInvalidError(str(exc)) from exc


def resolve_selected_paper_baseline(
    *,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    selection: PaperTraderSelection,
) -> ResolvedPaperBaseline:
    try:
        resolved = _resolve_baseline_run(
            catalog=catalog,
            registry=registry,
            run_id=selection.baseline_run_id,
        )
    except ResultNotFoundError as exc:
        raise PaperBaselineNotFoundError(
            f"baseline run not found: {selection.baseline_run_id}"
        ) from exc
    if (
        resolved.strategy_id != selection.strategy_id
        or resolved.strategy_content_sha256 != selection.content_sha256
        or resolved.contract_id != selection.contract_id
        or resolved.public.run_id != selection.baseline_run_id
        or resolved.public.result_sha256 != selection.baseline_result_sha256
        or resolved.public.validation_run is not False
    ):
        raise PaperSelectionIdentityMismatchError(
            "selected strategy, contract, baseline, or result SHA is stale"
        )
    return resolved


def paper_request_payload_sha256(body: PaperTraderCreateRequest) -> str:
    return sha256(
        _json_text(body.model_dump(by_alias=True, mode="json")).encode("utf-8")
    ).hexdigest()


def _provisioning_authorization_identity(
    authorization: PaperProvisioningAuthorization,
) -> tuple[str, str | None, str | None, str | None, str]:
    return (
        authorization.schema_version,
        authorization.operation_id,
        authorization.authorized_at,
        authorization.expires_at,
        authorization.reason,
    )


def _is_monotonic_provisioning_state(before: str, after: str) -> bool:
    allowed = {
        "armed": ("claimed", "consumed"),
        "claimed": ("claimed", "consumed"),
        "consumed": ("consumed",),
    }
    return after in allowed.get(before, ())


def create_paper_trader(
    *,
    body: PaperTraderCreateRequest,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    eligibility_store: PromotionDecisionStore,
    readiness_provider: PaperExternalReadinessProvider,
    authorization_policy: PaperProvisioningAuthorizationPolicy,
    trader_store: PaperTraderStore,
) -> tuple[PaperTrader, bool]:
    """Claim one approved permit, then append or recover one immutable record."""
    fingerprint = paper_request_payload_sha256(body)
    initial_inspection = authorization_policy.inspect_for_create(body.selection)

    readiness, baseline = evaluate_paper_provisioning_runtime_readiness(
        catalog=catalog,
        registry=registry,
        eligibility_store=eligibility_store,
        provider=readiness_provider,
        selection=body.selection,
    )
    authorization = authorization_policy.claim(
        request_id=body.request_id,
        request_payload_sha256=fingerprint,
        selection=body.selection,
    )
    inspection = authorization_policy.inspect_for_create(body.selection)
    binding = inspection.claim
    if (
        not _is_monotonic_provisioning_state(
            initial_inspection.authorization.state,
            authorization.state,
        )
        or not _is_monotonic_provisioning_state(
            authorization.state,
            inspection.authorization.state,
        )
        or _provisioning_authorization_identity(
            initial_inspection.authorization
        )
        != _provisioning_authorization_identity(authorization)
        or _provisioning_authorization_identity(authorization)
        != _provisioning_authorization_identity(inspection.authorization)
        or binding is None
        or (
            initial_inspection.claim is not None
            and binding != initial_inspection.claim
        )
        or binding.request_id != body.request_id
        or binding.request_payload_sha256 != fingerprint
        or binding.selection != body.selection
    ):
        raise PaperTraderStoreIntegrityError(
            "claimed provisioning authority has inconsistent request identity"
        )
    if (
        authorization.operation_id is None
        or authorization.authorized_at is None
        or authorization.expires_at is None
    ):
        raise PaperTraderStoreIntegrityError(
            "claimed provisioning authority is missing immutable identity"
        )
    provisioning_origin = PaperProvisioningOrigin(
        schema="paper_provisioning_origin.v1",
        operation_id=authorization.operation_id,
        request_id=body.request_id,
        request_payload_sha256=fingerprint,
        selection=body.selection,
        authorized_at=authorization.authorized_at,
        expires_at=authorization.expires_at,
        claimed_at=binding.claimed_at,
        reason=authorization.reason,
        runtime_readiness=readiness.snapshot(),
    )
    try:
        replay = trader_store.replay(
            request_id=body.request_id,
            request_payload_sha256=fingerprint,
        )
        if replay is not None:
            result = (replay, False)
        elif inspection.authorization.state == "consumed":
            raise PaperTraderStoreIntegrityError(
                "consumed provisioning authority has no immutable record"
            )
        else:
            result = trader_store.append(
                request_id=body.request_id,
                request_payload_sha256=fingerprint,
                selection=body.selection,
                baseline=baseline,
                readiness=readiness.snapshot(),
                provisioning_origin=provisioning_origin,
            )
    except Exception:
        if inspection.authorization.state == "claimed":
            authorization_policy.record_append_failure(
                request_id=body.request_id,
                request_payload_sha256=fingerprint,
                selection=body.selection,
            )
        raise
    authorization_policy.mark_consumed(
        request_id=body.request_id,
        request_payload_sha256=fingerprint,
        selection=body.selection,
    )
    return result


_STAGE_A_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS paper_traders (
    trader_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    request_payload_sha256 TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_content_sha256 TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    baseline_run_id TEXT NOT NULL,
    baseline_result_sha256 TEXT NOT NULL,
    baseline_range_start TEXT NOT NULL,
    baseline_range_end TEXT NOT NULL,
    account_id TEXT NOT NULL UNIQUE,
    max_drawdown_r INTEGER NOT NULL CHECK (max_drawdown_r = 8),
    max_losing_streak INTEGER NOT NULL CHECK (max_losing_streak = 8),
    blind_minutes INTEGER NOT NULL CHECK (blind_minutes = 5),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status = 'provisioned'),
    lifecycle_reason TEXT NOT NULL,
    lifecycle_as_of TEXT NOT NULL,
    readiness_snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_accounts (
    account_id TEXT PRIMARY KEY,
    trader_id TEXT NOT NULL UNIQUE,
    currency TEXT NOT NULL,
    initial_capital REAL NOT NULL CHECK (initial_capital > 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY (trader_id) REFERENCES paper_traders (trader_id)
);

CREATE TABLE IF NOT EXISTS paper_trader_events (
    trader_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 4),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'trader_created',
            'account_created',
            'provisioning_authorized',
            'provisioned'
        )
    ),
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    PRIMARY KEY (trader_id, ordinal),
    FOREIGN KEY (trader_id) REFERENCES paper_traders (trader_id)
);

CREATE INDEX IF NOT EXISTS paper_traders_by_created
ON paper_traders (created_at, trader_id);

CREATE INDEX IF NOT EXISTS paper_trader_events_by_trader
ON paper_trader_events (trader_id, ordinal);

CREATE TRIGGER IF NOT EXISTS paper_traders_are_immutable
BEFORE UPDATE ON paper_traders
BEGIN
    SELECT RAISE(ABORT, 'paper trader provisioning is immutable');
END;

CREATE TRIGGER IF NOT EXISTS paper_traders_cannot_be_deleted
BEFORE DELETE ON paper_traders
BEGIN
    SELECT RAISE(ABORT, 'paper trader provisioning is immutable');
END;

CREATE TRIGGER IF NOT EXISTS paper_accounts_are_immutable
BEFORE UPDATE ON paper_accounts
BEGIN
    SELECT RAISE(ABORT, 'paper account origin is immutable');
END;

CREATE TRIGGER IF NOT EXISTS paper_accounts_cannot_be_deleted
BEFORE DELETE ON paper_accounts
BEGIN
    SELECT RAISE(ABORT, 'paper account origin is immutable');
END;

CREATE TRIGGER IF NOT EXISTS paper_trader_events_are_append_only
BEFORE UPDATE ON paper_trader_events
BEGIN
    SELECT RAISE(ABORT, 'paper trader events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS paper_trader_events_cannot_be_deleted
BEFORE DELETE ON paper_trader_events
BEGIN
    SELECT RAISE(ABORT, 'paper trader events are append-only');
END;
"""

_SCHEMA_SQL = (
    _STAGE_A_SCHEMA_SQL
    + "\n"
    + PAPER_LEDGER_SCHEMA_SQL
    + "\nPRAGMA user_version = 3;\n"
)

_EXPECTED_TABLE_SQL = {
    "paper_traders": """
        CREATE TABLE paper_traders (
            trader_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            request_payload_sha256 TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            strategy_content_sha256 TEXT NOT NULL,
            contract_id TEXT NOT NULL,
            baseline_run_id TEXT NOT NULL,
            baseline_result_sha256 TEXT NOT NULL,
            baseline_range_start TEXT NOT NULL,
            baseline_range_end TEXT NOT NULL,
            account_id TEXT NOT NULL UNIQUE,
            max_drawdown_r INTEGER NOT NULL CHECK (max_drawdown_r = 8),
            max_losing_streak INTEGER NOT NULL CHECK (max_losing_streak = 8),
            blind_minutes INTEGER NOT NULL CHECK (blind_minutes = 5),
            lifecycle_status TEXT NOT NULL CHECK (lifecycle_status = 'provisioned'),
            lifecycle_reason TEXT NOT NULL,
            lifecycle_as_of TEXT NOT NULL,
            readiness_snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """,
    "paper_accounts": """
        CREATE TABLE paper_accounts (
            account_id TEXT PRIMARY KEY,
            trader_id TEXT NOT NULL UNIQUE,
            currency TEXT NOT NULL,
            initial_capital REAL NOT NULL CHECK (initial_capital > 0),
            created_at TEXT NOT NULL,
            FOREIGN KEY (trader_id) REFERENCES paper_traders (trader_id)
        )
    """,
    "paper_trader_events": """
        CREATE TABLE paper_trader_events (
            trader_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 4),
            event_type TEXT NOT NULL CHECK (
                event_type IN (
                    'trader_created',
                    'account_created',
                    'provisioning_authorized',
                    'provisioned'
                )
            ),
            payload_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            PRIMARY KEY (trader_id, ordinal),
            FOREIGN KEY (trader_id) REFERENCES paper_traders (trader_id)
        )
    """,
}

_EXPECTED_INDEX_SQL = {
    "paper_traders_by_created": """
        CREATE INDEX paper_traders_by_created
        ON paper_traders (created_at, trader_id)
    """,
    "paper_trader_events_by_trader": """
        CREATE INDEX paper_trader_events_by_trader
        ON paper_trader_events (trader_id, ordinal)
    """,
}

_EXPECTED_TRIGGER_SQL = {
    "paper_traders_are_immutable": """
        CREATE TRIGGER paper_traders_are_immutable
        BEFORE UPDATE ON paper_traders
        BEGIN
            SELECT RAISE(ABORT, 'paper trader provisioning is immutable');
        END
    """,
    "paper_traders_cannot_be_deleted": """
        CREATE TRIGGER paper_traders_cannot_be_deleted
        BEFORE DELETE ON paper_traders
        BEGIN
            SELECT RAISE(ABORT, 'paper trader provisioning is immutable');
        END
    """,
    "paper_accounts_are_immutable": """
        CREATE TRIGGER paper_accounts_are_immutable
        BEFORE UPDATE ON paper_accounts
        BEGIN
            SELECT RAISE(ABORT, 'paper account origin is immutable');
        END
    """,
    "paper_accounts_cannot_be_deleted": """
        CREATE TRIGGER paper_accounts_cannot_be_deleted
        BEFORE DELETE ON paper_accounts
        BEGIN
            SELECT RAISE(ABORT, 'paper account origin is immutable');
        END
    """,
    "paper_trader_events_are_append_only": """
        CREATE TRIGGER paper_trader_events_are_append_only
        BEFORE UPDATE ON paper_trader_events
        BEGIN
            SELECT RAISE(ABORT, 'paper trader events are append-only');
        END
    """,
    "paper_trader_events_cannot_be_deleted": """
        CREATE TRIGGER paper_trader_events_cannot_be_deleted
        BEFORE DELETE ON paper_trader_events
        BEGIN
            SELECT RAISE(ABORT, 'paper trader events are append-only');
        END
    """,
}

_EXPECTED_TABLE_SQL = {
    **_EXPECTED_TABLE_SQL,
    **PAPER_LEDGER_EXPECTED_TABLE_SQL,
}
_EXPECTED_TRIGGER_SQL = {
    **_EXPECTED_TRIGGER_SQL,
    **PAPER_LEDGER_EXPECTED_TRIGGER_SQL,
}


class PaperTraderStore:
    """SQLite store for immutable Stage A provisioning and request recovery."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        trader_id_factory: Callable[[], str] | None = None,
        account_id_factory: Callable[[], str] | None = None,
        ledger_origin_id_factory: Callable[[], str] | None = None,
        append_step_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._path = path
        self._clock = clock or (lambda: datetime.now(UTC))
        # Default identities align with v4 PaperRuntimeStore (_derived_id).
        # Custom factories remain available for isolated tests only.
        self._custom_id_factories = trader_id_factory is not None
        self._trader_id_factory = trader_id_factory or (
            lambda: f"trader-{uuid4().hex}"
        )
        self._account_id_factory = account_id_factory or (
            lambda: f"paper-account-{uuid4().hex}"
        )
        self._ledger_origin_id_factory = ledger_origin_id_factory or (
            lambda: f"paper-ledger-{uuid4().hex}"
        )
        self._append_step_hook = append_step_hook
        self._schema_lock = Lock()

    @property
    def path(self) -> Path:
        return self._path

    def replay(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
    ) -> PaperTrader | None:
        if not self._path.is_file():
            return None
        connection = self._connect_readonly()
        try:
            row = connection.execute(
                "SELECT * FROM paper_traders WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            self._assert_same_payload(row, request_payload_sha256)
            return self._record_from_row(connection, row)
        finally:
            connection.close()

    def append(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
        baseline: ResolvedPaperBaseline,
        readiness: PaperReadinessSnapshot,
        provisioning_origin: PaperProvisioningOrigin,
    ) -> tuple[PaperTrader, bool]:
        # Align Stage A identities with v4 PaperRuntimeStore derivation so the
        # product journey (create → runtime → review-v2) shares one trader_id.
        from futures_research.paper.store import _derived_id as runtime_derived_id

        if self._custom_id_factories:
            trader_id = self._trader_id_factory()
            account_id = self._account_id_factory()
            ledger_origin_id = self._ledger_origin_id_factory()
        else:
            trader_id = runtime_derived_id("trader", request_id)
            account_id = runtime_derived_id("paper-account", request_id)
            ledger_origin_id = runtime_derived_id("paper-ledger", request_id)
        if _TRADER_ID_PATTERN.fullmatch(trader_id) is None:
            raise ValueError("trader_id factory returned a non-canonical identity")
        if _ACCOUNT_ID_PATTERN.fullmatch(account_id) is None:
            raise ValueError("account_id factory returned a non-canonical identity")
        created_at = _timestamp_text(self._clock())
        public_baseline = baseline.public
        record = PaperTrader(
            schema="paper_trader.v1",
            trader_id=trader_id,
            request_id=request_id,
            strategy=PaperStrategyIdentity(
                strategy_id=selection.strategy_id,
                content_sha256=selection.content_sha256,
            ),
            contract_id=selection.contract_id,
            baseline=PaperLockedBaseline(
                run_id=public_baseline.run_id,
                result_sha256=public_baseline.result_sha256,
                range_start=public_baseline.range_start,
                range_end=public_baseline.range_end,
            ),
            account=PaperAccountOrigin(
                account_id=account_id,
                currency=public_baseline.currency,
                initial_capital=public_baseline.initial_capital,
            ),
            safeguards=PaperSafeguards(
                max_drawdown_r=8,
                max_losing_streak=8,
                blind_minutes=5,
            ),
            lifecycle=PaperLifecycle(
                status="provisioned",
                reason=_LIFECYCLE_REASON,
                as_of=created_at,
            ),
            readiness_snapshot=readiness,
            created_at=created_at,
        )
        try:
            self._assert_provisioning_origin(
                record=record,
                request_payload_sha256=request_payload_sha256,
                origin=provisioning_origin,
            )
        except ValueError as exc:
            raise PaperTraderStoreIntegrityError(
                "paper provisioning origin differs from immutable trader truth"
            ) from exc

        connection = self._connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM paper_traders WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is not None:
                self._assert_same_payload(row, request_payload_sha256)
                existing = self._record_from_row(connection, row)
                connection.commit()
                return existing, False
            connection.execute(
                """
                INSERT INTO paper_traders (
                    trader_id, request_id, request_payload_sha256,
                    strategy_id, strategy_content_sha256, contract_id,
                    baseline_run_id, baseline_result_sha256,
                    baseline_range_start, baseline_range_end, account_id,
                    max_drawdown_r, max_losing_streak, blind_minutes,
                    lifecycle_status, lifecycle_reason, lifecycle_as_of,
                    readiness_snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trader_id,
                    record.request_id,
                    request_payload_sha256,
                    record.strategy.strategy_id,
                    record.strategy.content_sha256,
                    record.contract_id,
                    record.baseline.run_id,
                    record.baseline.result_sha256,
                    record.baseline.range_start,
                    record.baseline.range_end,
                    record.account.account_id,
                    record.safeguards.max_drawdown_r,
                    record.safeguards.max_losing_streak,
                    record.safeguards.blind_minutes,
                    record.lifecycle.status,
                    record.lifecycle.reason,
                    record.lifecycle.as_of,
                    _json_text(
                        record.readiness_snapshot.model_dump(
                            by_alias=True,
                            mode="json",
                        )
                    ),
                    record.created_at,
                ),
            )
            self._call_append_step_hook("paper_trader")
            connection.execute(
                """
                INSERT INTO paper_accounts (
                    account_id, trader_id, currency, initial_capital, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.account.account_id,
                    record.trader_id,
                    record.account.currency,
                    record.account.initial_capital,
                    record.created_at,
                ),
            )
            self._call_append_step_hook("paper_account")
            self._insert_initial_events(
                connection,
                record,
                provisioning_origin,
            )
            self._call_append_step_hook("paper_trader_events")
            insert_paper_ledger_origin(
                connection,
                seed=PaperLedgerOriginSeed(
                    ledger_origin_id=ledger_origin_id,
                    trader_id=record.trader_id,
                    account_id=record.account.account_id,
                    origin_at=record.created_at,
                    strategy_id=record.strategy.strategy_id,
                    strategy_name=baseline.strategy_name,
                    strategy_content_sha256=record.strategy.content_sha256,
                    contract_id=record.contract_id,
                    contract_exchange=baseline.contract.exchange,
                    contract_timezone=baseline.contract.timezone,
                    baseline_run_id=record.baseline.run_id,
                    baseline_result_sha256=record.baseline.result_sha256,
                    baseline_range_start=record.baseline.range_start,
                    baseline_range_end=record.baseline.range_end,
                    currency=record.account.currency,
                    initial_capital=record.account.initial_capital,
                    baseline_capture=baseline.ledger_capture,
                ),
                step_hook=self._append_step_hook,
            )
            persisted = self._record_for_trader(connection, record.trader_id)
            connection.commit()
            return persisted, True
        except PaperLedgerPersistenceError as exc:
            connection.rollback()
            raise PaperTraderStoreIntegrityError(str(exc)) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _call_append_step_hook(self, step: str) -> None:
        if self._append_step_hook is not None:
            self._append_step_hook(step)

    def request_status(self, request_id: str) -> PaperTraderRequestStatus | None:
        record = self.get_by_request(request_id)
        if record is None:
            return None
        return PaperTraderRequestStatus(
            schema="paper_trader_request_status.v1",
            request_id=request_id,
            status="completed",
            trader=record,
        )

    def get_by_request(self, request_id: str) -> PaperTrader | None:
        if not self._path.is_file():
            return None
        connection = self._connect_readonly()
        try:
            row = connection.execute(
                "SELECT * FROM paper_traders WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return (
                self._record_from_row(connection, row)
                if row is not None
                else None
            )
        finally:
            connection.close()

    def get(self, trader_id: str) -> PaperTrader | None:
        if not self._path.is_file():
            return None
        connection = self._connect_readonly()
        try:
            row = connection.execute(
                "SELECT * FROM paper_traders WHERE trader_id = ?",
                (trader_id,),
            ).fetchone()
            return (
                self._record_from_row(connection, row)
                if row is not None
                else None
            )
        finally:
            connection.close()

    def list(self) -> PaperTraderList:
        if not self._path.is_file():
            return PaperTraderList(
                schema="paper_trader_list.v1",
                count=0,
                traders=(),
            )
        connection = self._connect_readonly()
        try:
            rows = connection.execute(
                "SELECT * FROM paper_traders ORDER BY created_at, trader_id"
            ).fetchall()
            records = tuple(self._record_from_row(connection, row) for row in rows)
            return PaperTraderList(
                schema="paper_trader_list.v1",
                count=len(records),
                traders=records,
            )
        finally:
            connection.close()

    def _connect_write(self) -> sqlite3.Connection:
        with self._schema_lock:
            existed = self._path.is_file()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self._path,
                timeout=30.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                if existed:
                    self._assert_schema_guards(connection)
                connection.executescript(_SCHEMA_SQL)
                self._assert_schema_guards(connection)
            except Exception:
                connection.close()
                raise
            return connection

    def _connect_readonly(self) -> sqlite3.Connection:
        with self._schema_lock:
            uri_path = quote(self._path.resolve().as_posix(), safe="/:")
            connection = sqlite3.connect(
                f"file:{uri_path}?mode=ro",
                timeout=30.0,
                isolation_level=None,
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                self._assert_schema_guards(connection)
            except Exception:
                connection.close()
                raise
            return connection

    @classmethod
    def _assert_schema_guards(cls, connection: sqlite3.Connection) -> None:
        version_row = connection.execute("PRAGMA user_version").fetchone()
        version = version_row[0] if version_row is not None else None
        if version != 3:
            raise PaperTraderStoreSchemaUpgradeRequiredError(
                "store_schema_upgrade_required: paper trader store must use "
                "the exact Stage B schema v3"
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if [tuple(row) for row in integrity] != [("ok",)]:
            raise PaperTraderStoreIntegrityError(
                "paper trader store failed SQLite integrity_check"
            )
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise PaperTraderStoreIntegrityError(
                "paper trader store failed SQLite foreign_key_check"
            )
        objects = connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger')
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        tables = {
            cast(str, row["name"]): _normalized_sql(cast(str, row["sql"]))
            for row in objects
            if row["type"] == "table" and row["sql"] is not None
        }
        indexes = {
            cast(str, row["name"]): _normalized_sql(cast(str, row["sql"]))
            for row in objects
            if row["type"] == "index" and row["sql"] is not None
        }
        triggers = {
            cast(str, row["name"]): _normalized_sql(cast(str, row["sql"]))
            for row in objects
            if row["type"] == "trigger" and row["sql"] is not None
        }
        expected_tables = {
            name: _normalized_sql(sql) for name, sql in _EXPECTED_TABLE_SQL.items()
        }
        expected_indexes = {
            name: _normalized_sql(sql) for name, sql in _EXPECTED_INDEX_SQL.items()
        }
        expected_triggers = {
            name: _normalized_sql(sql) for name, sql in _EXPECTED_TRIGGER_SQL.items()
        }
        if (
            tables != expected_tables
            or indexes != expected_indexes
            or triggers != expected_triggers
            or not cls._has_unique_columns(
                connection,
                table="paper_traders",
                columns=("request_id",),
            )
            or not cls._has_unique_columns(
                connection,
                table="paper_traders",
                columns=("account_id",),
            )
            or not cls._has_unique_columns(
                connection,
                table="paper_accounts",
                columns=("trader_id",),
            )
            or not cls._has_unique_columns(
                connection,
                table="paper_ledger_origins",
                columns=("trader_id",),
            )
            or not cls._has_unique_columns(
                connection,
                table="paper_ledger_origins",
                columns=("account_id",),
            )
            or not cls._has_unique_columns(
                connection,
                table="paper_ledger_baseline_members",
                columns=("ledger_origin_id", "zip_path"),
            )
            or not cls._has_unique_columns(
                connection,
                table="paper_review_requests",
                columns=("snapshot_id",),
            )
        ):
            raise PaperTraderStoreIntegrityError(
                "paper trader store has an invalid table, index, unique "
                "constraint, or append-only guard"
            )

    @staticmethod
    def _has_unique_columns(
        connection: sqlite3.Connection,
        *,
        table: str,
        columns: tuple[str, ...],
    ) -> bool:
        rows = connection.execute(
            "SELECT name, [unique] FROM pragma_index_list(?)",
            (table,),
        ).fetchall()
        return any(
            row["unique"] == 1
            and tuple(
                cast(str, field["name"])
                for field in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (row["name"],),
                ).fetchall()
            )
            == columns
            for row in rows
        )

    @staticmethod
    def _assert_same_payload(row: sqlite3.Row, expected_sha256: str) -> None:
        if row["request_payload_sha256"] != expected_sha256:
            raise PaperRequestConflictError(
                f"request_id {row['request_id']} is already bound to a different intent"
            )

    @staticmethod
    def _assert_provisioning_origin(
        *,
        record: PaperTrader,
        request_payload_sha256: str,
        origin: PaperProvisioningOrigin,
    ) -> None:
        expected_selection = PaperTraderSelection(
            strategy_id=record.strategy.strategy_id,
            content_sha256=record.strategy.content_sha256,
            contract_id=record.contract_id,
            baseline_run_id=record.baseline.run_id,
            baseline_result_sha256=record.baseline.result_sha256,
        )
        if (
            origin.request_id != record.request_id
            or origin.request_payload_sha256 != request_payload_sha256
            or origin.selection != expected_selection
            or origin.runtime_readiness != record.readiness_snapshot
            or _utc_timestamp_value(origin.claimed_at)
            > _utc_timestamp_value(record.created_at)
        ):
            raise ValueError(
                "paper provisioning origin differs from immutable trader truth"
            )

    @staticmethod
    def _insert_initial_events(
        connection: sqlite3.Connection,
        record: PaperTrader,
        provisioning_origin: PaperProvisioningOrigin,
    ) -> None:
        payloads = (
            {
                "strategy": record.strategy.model_dump(mode="json"),
                "contract_id": record.contract_id,
                "baseline": record.baseline.model_dump(mode="json"),
            },
            record.account.model_dump(mode="json"),
            provisioning_origin.model_dump(by_alias=True, mode="json"),
            record.lifecycle.model_dump(mode="json"),
        )
        for ordinal, (event_type, payload) in enumerate(
            zip(_INITIAL_EVENTS, payloads, strict=True),
            start=1,
        ):
            connection.execute(
                """
                INSERT INTO paper_trader_events (
                    trader_id, ordinal, event_type, payload_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.trader_id,
                    ordinal,
                    event_type,
                    _json_text(payload),
                    record.created_at,
                ),
            )

    def _record_for_trader(
        self,
        connection: sqlite3.Connection,
        trader_id: str,
    ) -> PaperTrader:
        row = connection.execute(
            "SELECT * FROM paper_traders WHERE trader_id = ?",
            (trader_id,),
        ).fetchone()
        if row is None:
            raise PaperTraderStoreIntegrityError(
                "paper trader disappeared during its provisioning transaction"
            )
        return self._record_from_row(connection, row)

    @staticmethod
    def _record_from_row(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> PaperTrader:
        try:
            trader_id = cast(str, row["trader_id"])
            account = connection.execute(
                "SELECT * FROM paper_accounts WHERE trader_id = ?",
                (trader_id,),
            ).fetchall()
            if (
                len(account) != 1
                or account[0]["account_id"] != row["account_id"]
                or account[0]["created_at"] != row["created_at"]
            ):
                raise ValueError("paper trader requires exactly one matching account")
            events = connection.execute(
                """
                SELECT ordinal, event_type, payload_json, occurred_at
                FROM paper_trader_events
                WHERE trader_id = ?
                ORDER BY ordinal
                """,
                (trader_id,),
            ).fetchall()
            if (
                tuple((item["ordinal"], item["event_type"]) for item in events)
                != tuple(enumerate(_INITIAL_EVENTS, start=1))
                or any(item["occurred_at"] != row["created_at"] for item in events)
            ):
                raise ValueError("paper trader requires its exact four initial events")
            request_payload_sha256 = cast(str, row["request_payload_sha256"])
            if _SHA256_PATTERN.fullmatch(request_payload_sha256) is None:
                raise ValueError("request payload fingerprint must be lowercase SHA-256")
            readiness = PaperReadinessSnapshot.model_validate_json(
                cast(str, row["readiness_snapshot_json"]),
                strict=True,
            )
            account_row = account[0]
            record = PaperTrader.model_validate(
                {
                    "schema": "paper_trader.v1",
                    "trader_id": trader_id,
                    "request_id": row["request_id"],
                    "strategy": {
                        "strategy_id": row["strategy_id"],
                        "content_sha256": row["strategy_content_sha256"],
                    },
                    "contract_id": row["contract_id"],
                    "baseline": {
                        "run_id": row["baseline_run_id"],
                        "result_sha256": row["baseline_result_sha256"],
                        "range_start": row["baseline_range_start"],
                        "range_end": row["baseline_range_end"],
                    },
                    "account": {
                        "account_id": account_row["account_id"],
                        "currency": account_row["currency"],
                        "initial_capital": account_row["initial_capital"],
                    },
                    "safeguards": {
                        "max_drawdown_r": row["max_drawdown_r"],
                        "max_losing_streak": row["max_losing_streak"],
                        "blind_minutes": row["blind_minutes"],
                    },
                    "lifecycle": {
                        "status": row["lifecycle_status"],
                        "reason": row["lifecycle_reason"],
                        "as_of": row["lifecycle_as_of"],
                    },
                    "readiness_snapshot": readiness,
                    "created_at": row["created_at"],
                },
                strict=True,
            )
            request = PaperTraderCreateRequest(
                schema="paper_trader_create_request.v1",
                request_id=record.request_id,
                selection=PaperTraderSelection(
                    strategy_id=record.strategy.strategy_id,
                    content_sha256=record.strategy.content_sha256,
                    contract_id=record.contract_id,
                    baseline_run_id=record.baseline.run_id,
                    baseline_result_sha256=record.baseline.result_sha256,
                ),
            )
            if paper_request_payload_sha256(request) != request_payload_sha256:
                raise ValueError(
                    "request payload fingerprint differs from immutable intent"
                )
            provisioning_origin = PaperProvisioningOrigin.model_validate_json(
                cast(str, events[2]["payload_json"]),
                strict=True,
            )
            PaperTraderStore._assert_provisioning_origin(
                record=record,
                request_payload_sha256=request_payload_sha256,
                origin=provisioning_origin,
            )
            expected_payloads = (
                {
                    "strategy": record.strategy.model_dump(mode="json"),
                    "contract_id": record.contract_id,
                    "baseline": record.baseline.model_dump(mode="json"),
                },
                record.account.model_dump(mode="json"),
                provisioning_origin.model_dump(by_alias=True, mode="json"),
                record.lifecycle.model_dump(mode="json"),
            )
            for event, expected_payload in zip(
                events,
                expected_payloads,
                strict=True,
            ):
                if cast(str, event["payload_json"]) != _json_text(expected_payload):
                    raise ValueError(
                        "paper trader event payload differs from immutable record"
                    )
            if cast(str, row["readiness_snapshot_json"]) != _json_text(
                readiness.model_dump(by_alias=True, mode="json")
            ):
                raise ValueError("readiness snapshot is not canonical immutable JSON")
            assert_paper_ledger_for_trader(
                connection,
                expected=PaperLedgerExpectedTrader(
                    trader_id=record.trader_id,
                    account_id=record.account.account_id,
                    created_at=record.created_at,
                    strategy_id=record.strategy.strategy_id,
                    strategy_content_sha256=record.strategy.content_sha256,
                    contract_id=record.contract_id,
                    baseline_run_id=record.baseline.run_id,
                    baseline_result_sha256=record.baseline.result_sha256,
                    baseline_range_start=record.baseline.range_start,
                    baseline_range_end=record.baseline.range_end,
                    currency=record.account.currency,
                    initial_capital=record.account.initial_capital,
                ),
            )
            return record
        except (
            json.JSONDecodeError,
            KeyError,
            PaperLedgerEvidenceError,
            PaperLedgerPersistenceError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise PaperTraderStoreIntegrityError(
                "paper trader store contains an invalid immutable record"
            ) from exc


def _list_paper_baseline_candidates(
    *,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    strategy_id: str,
    strategy_content_sha256: str,
    contract_id: str | None = None,
) -> list[ResolvedPaperBaseline]:
    """Return the one strict candidate set shared by contract and baseline APIs."""
    try:
        summaries = catalog.list_run_summaries()
    except ResultArtifactIntegrityError as exc:
        raise PaperBaselineIntegrityError(str(exc)) from exc
    candidates: list[ResolvedPaperBaseline] = []
    for summary in summaries:
        if (
            summary.get("strategy_version") != strategy_id
            or summary.get("validation_run") is not False
            or (
                contract_id is not None
                and summary.get("contract_id") != contract_id
            )
        ):
            continue
        run_id = summary.get("run_id")
        if not isinstance(run_id, str):
            raise PaperBaselineIntegrityError(
                "baseline catalog returned a malformed run identity"
            )
        resolved = _resolve_baseline_run(
            catalog=catalog,
            registry=registry,
            run_id=run_id,
        )
        if (
            resolved.strategy_id != strategy_id
            or resolved.strategy_content_sha256 != strategy_content_sha256
            or (contract_id is not None and resolved.contract_id != contract_id)
            or resolved.public.validation_run is not False
        ):
            continue
        candidates.append(resolved)
    return candidates


def _resolve_baseline_run(
    *,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    run_id: str,
) -> ResolvedPaperBaseline:
    try:
        snapshot = catalog.get_verified_snapshot(run_id)
        export_bundle = catalog.get_export_artifacts(run_id)
    except ResultNotFoundError:
        raise
    except ResultArtifactIntegrityError as exc:
        raise PaperBaselineIntegrityError(str(exc)) from exc
    main_member = export_bundle.members[0]
    if main_member != ("result.json", snapshot.main_bytes):
        raise PaperBaselineIntegrityError(
            "baseline result bytes changed while its sidecars were being verified"
        )
    validated = snapshot.validated_main
    manifest = validated.manifest
    if manifest.validation_run:
        raise PaperSelectionIdentityMismatchError(
            "validation runs cannot be selected as paper baselines"
        )
    contract = _contract_truth(registry, manifest.contract_id)
    if contract.currency != "USD":
        raise PaperBaselineIntegrityError(
            f"baseline contract currency must be exact USD, got {contract.currency!r}"
        )
    if manifest.session_name not in contract.sessions:
        raise PaperBaselineIntegrityError(
            "baseline session is absent from immutable contract truth"
        )
    result_sha256 = sha256(snapshot.main_bytes).hexdigest()
    binding = manifest.strategy_binding
    if (
        binding is None
        or binding.strategy_name is None
        or not binding.strategy_name
        or binding.strategy_name != binding.strategy_name.strip()
        or not contract.exchange
        or contract.exchange != contract.exchange.strip()
        or not contract.timezone
        or contract.timezone != contract.timezone.strip()
    ):
        raise PaperBaselineIntegrityError(
            "baseline strategy name, contract exchange, or timezone is unavailable"
        )
    try:
        ledger_capture = capture_paper_ledger_baseline(
            artifacts=export_bundle,
            expected_run_id=manifest.run_id,
            expected_result_sha256=result_sha256,
        )
    except PaperLedgerEvidenceError as exc:
        raise PaperBaselineIntegrityError(str(exc)) from exc
    try:
        public = PaperBaseline(
            run_id=manifest.run_id,
            result_sha256=result_sha256,
            range_start=_timestamp_text(manifest.range_start),
            range_end=_timestamp_text(manifest.range_end),
            currency=contract.currency,
            initial_capital=manifest.initial_capital,
            trade_count=validated.metrics.trade_count,
            net_r=validated.metrics.net_r,
            validation_run=False,
            integrity="verified",
        )
    except ValidationError as exc:
        raise PaperBaselineIntegrityError(
            "baseline contains an invalid currency, capital, range, or metric"
        ) from exc
    return ResolvedPaperBaseline(
        public=public,
        strategy_id=validated.strategy_id,
        strategy_content_sha256=validated.strategy_content_sha256,
        strategy_name=binding.strategy_name,
        contract=contract,
        ledger_capture=ledger_capture,
    )


def _contract_truth(registry: ContractRegistry, contract_id: str) -> ContractSpec:
    matches = tuple(
        contract
        for contract in registry.contracts.values()
        if contract.contract_id == contract_id
    )
    if len(matches) != 1:
        raise PaperBaselineIntegrityError(
            "baseline contract must resolve to exactly one configured contract"
        )
    contract = matches[0]
    if not contract.currency or contract.currency != contract.currency.strip():
        raise PaperBaselineIntegrityError(
            "baseline contract currency is missing or ambiguous"
        )
    return contract


def _readiness_check(
    key: PaperReadinessKey,
    signal: PaperReadinessSignal,
    checked_at: str,
) -> PaperReadinessCheck:
    try:
        return PaperReadinessCheck(
            key=key,
            status=signal.status,
            reason=signal.reason,
            checked_at=checked_at,
        )
    except ValidationError as exc:
        raise PaperReadinessBlockedError(
            f"{key} readiness provider returned malformed truth"
        ) from exc


def _assert_readiness_projection(
    overall: PaperOverallReadiness,
    checked_at: str,
    checks: tuple[PaperReadinessCheck, ...],
) -> None:
    if tuple(check.key for check in checks) != _READINESS_KEYS:
        raise ValueError("readiness checks must contain the exact four keys in order")
    if any(check.checked_at != checked_at for check in checks):
        raise ValueError("all readiness checks must share the top-level checked_at")
    expected: PaperOverallReadiness = (
        "ready" if all(check.status == "ready" for check in checks) else "blocked"
    )
    if overall != expected:
        raise ValueError("overall readiness must be derived from the exact four checks")


def _canonical_uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ValueError("request_id must be a canonical lowercase UUID4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("request_id must be a canonical lowercase UUID4")
    return value


def _canonical_utc_text(value: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be canonical timezone-aware UTC Z text")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be canonical timezone-aware UTC Z text") from exc
    offset = parsed.utcoffset()
    if (
        parsed.tzinfo is None
        or offset is None
        or offset.total_seconds() != 0
        or _timestamp_text(parsed) != value
    ):
        raise ValueError("timestamp must be canonical timezone-aware UTC Z text")
    return value


def _utc_timestamp_value(value: str) -> datetime:
    _canonical_utc_text(value)
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("paper timestamp must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _finite_number(value: object, *, field_name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite JSON number")
    if isinstance(value, float) and not isfinite(value):
        raise ValueError(f"{field_name} must be a finite JSON number")
    return value


def _untrimmed_text(value: str, *, field_name: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-blank untrimmed text")
    return value


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalized_sql(value: str) -> str:
    return " ".join(value.split()).lower()
