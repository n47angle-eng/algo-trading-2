"""Strict P6 Stage B ledger resource and immutable review-state boundary."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from threading import RLock
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from futures_research.api.paper_traders import (
    PaperReadinessSnapshot,
    PaperTrader,
    PaperTraderStore,
    PaperTraderStoreIntegrityError,
    PaperTraderStoreSchemaUpgradeRequiredError,
)

PaperReviewErrorCode = Literal[
    "trader_not_found",
    "ledger_not_ready",
    "ledger_integrity_failed",
    "store_schema_upgrade_required",
    "request_not_found",
    "request_id_conflict",
    "snapshot_not_found",
    "snapshot_not_ready",
    "snapshot_failed",
    "snapshot_integrity_failed",
    "artifact_build_failed",
    "artifact_unavailable",
    "build_interrupted",
]
PaperReviewIssueKind = Literal[
    "missing_member",
    "unsafe_path",
    "unresolved_ref",
    "hash_mismatch",
    "identity_mismatch",
    "count_mismatch",
    "cutoff_unavailable",
    "artifact_build_failed",
    "artifact_unavailable",
    "build_interrupted",
]

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TRADER_ID_PATTERN = re.compile(r"^trader-[0-9a-f]{32}$")
_ACCOUNT_ID_PATTERN = re.compile(r"^paper-account-[0-9a-f]{32}$")
_LEDGER_ID_PATTERN = re.compile(r"^paper-ledger-[0-9a-f]{32}$")
_SNAPSHOT_ID_PATTERN = re.compile(r"^paper-review-[0-9a-f]{32}$")
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_UTC_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{6})?Z$"
)
_ARTIFACT_RELPATH_PATTERN = re.compile(
    r"^sha256/([0-9a-f]{2})/([0-9a-f]{64})\.zip$"
)
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_TOTAL_PARTS = 10
_CLOSEST_ALGORITHM = "p5_structural_closest.v1"
_ERROR_HTTP_STATUS: Mapping[PaperReviewErrorCode, int] = {
    "trader_not_found": 404,
    "ledger_not_ready": 409,
    "ledger_integrity_failed": 503,
    "store_schema_upgrade_required": 503,
    "request_not_found": 404,
    "request_id_conflict": 409,
    "snapshot_not_found": 404,
    "snapshot_not_ready": 409,
    "snapshot_failed": 409,
    "snapshot_integrity_failed": 503,
    "artifact_build_failed": 503,
    "artifact_unavailable": 503,
    "build_interrupted": 503,
}
_RETRYABLE_CODES = frozenset({"snapshot_not_ready"})
_ACCEPT_LOCK = RLock()


def paper_review_error_http_status(code: PaperReviewErrorCode) -> int:
    """Return the closed HTTP status assigned to one review error code."""
    return _ERROR_HTTP_STATUS[code]


class PaperReviewDomainError(RuntimeError):
    """A Phase 2 logical outcome, before Phase 4 assigns an HTTP envelope."""

    def __init__(
        self,
        code: PaperReviewErrorCode,
        message: str,
        *,
        request_id: str | None = None,
        snapshot_id: str | None = None,
        progress: PaperReviewProgress | None = None,
        issues: Sequence[PaperReviewIssue] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.payload = PaperReviewErrorPayload(
            schema="paper_review_error.v1",
            code=code,
            message=message,
            retryable=code in _RETRYABLE_CODES,
            request_id=request_id,
            snapshot_id=snapshot_id,
            progress=progress,
            issues=tuple(issues),
        )


class _StrictReviewModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        populate_by_name=True,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_canonical_json(value: object) -> object:
    if not isinstance(value, str):
        raise ValueError("persisted JSON must be text")
    parsed = json.loads(
        value,
        parse_constant=lambda constant: (_raise_nonstandard_constant(constant)),
    )
    if _canonical_json(parsed) != value:
        raise ValueError("persisted JSON must use the canonical serializer")
    return parsed


def _raise_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _canonical_uuid4(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("identity must be a canonical lowercase UUID4")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError("identity must be a canonical lowercase UUID4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("identity must be a canonical lowercase UUID4")
    return value


def _canonical_snapshot_id(value: object) -> str:
    snapshot_id = _require_pattern(
        value,
        _SNAPSHOT_ID_PATTERN,
        label="snapshot ID",
    )
    snapshot_hex = snapshot_id.removeprefix("paper-review-")
    parsed = UUID(hex=snapshot_hex)
    if parsed.version != 4 or parsed.hex != snapshot_hex:
        raise ValueError("snapshot ID must contain a canonical UUID4 hex value")
    return snapshot_id


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("paper review timestamp must include a timezone")
    utc = value.astimezone(UTC)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


def _canonical_utc(value: object) -> str:
    if not isinstance(value, str) or _UTC_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp must be canonical UTC Z text")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be canonical UTC Z text") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
        or _timestamp_text(parsed) != value
    ):
        raise ValueError("timestamp must be canonical UTC Z text")
    return value


def _untrimmed_text(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise ValueError(f"{label} must be non-empty untrimmed text without NUL")
    return value


def _human_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{label} must be non-empty text without NUL")
    return value


def _finite_number(value: object, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or (value == 0 and str(value).startswith("-"))
    ):
        raise ValueError(f"{label} must be a finite JSON number without negative zero")
    return float(value)


def _safe_count(value: object, *, positive: bool = False) -> int:
    if (
        type(value) is not int
        or value < (1 if positive else 0)
        or value > _MAX_SAFE_INTEGER
    ):
        boundary = "positive" if positive else "non-negative"
        raise ValueError(f"value must be a {boundary} safe JSON integer")
    return value


def _wire_array(value: object, *, label: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a JSON array")
    return tuple(value)


def _require_pattern(value: object, pattern: re.Pattern[str], *, label: str) -> str:
    text = _untrimmed_text(value, label=label)
    if pattern.fullmatch(text) is None:
        raise ValueError(f"{label} is not canonical")
    return text


def _run_id(value: object) -> str:
    return _require_pattern(value, _RUN_ID_PATTERN, label="baseline run ID")


def _ledger_member_paths(run_id: str) -> tuple[str, ...]:
    _run_id(run_id)
    return (
        "baseline/result.json",
        f"baseline/trades/{run_id}.json",
        f"baseline/equity/{run_id}.json",
        f"baseline/events/{run_id}.json",
    )


def _review_member_paths(run_id: str) -> tuple[str, ...]:
    _run_id(run_id)
    return (
        "paper-review.json",
        "paper/ledger-origin.json",
        "paper/trades.json",
        "paper/equity.json",
        "paper/events.json",
        "divergence/expected-actual.json",
        "baseline/result.json",
        f"baseline/trades/{run_id}.json",
        f"baseline/equity/{run_id}.json",
        f"baseline/events/{run_id}.json",
    )


class PaperEvidenceMember(_StrictReviewModel):
    path: str
    bytes: int
    sha256: str

    @field_validator("path")
    @classmethod
    def require_path(cls, value: str) -> str:
        return _untrimmed_text(value, label="evidence member path")

    @field_validator("bytes", mode="before")
    @classmethod
    def require_bytes(cls, value: object) -> int:
        return _safe_count(value, positive=True)

    @field_validator("sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        return _require_pattern(value, _SHA256_PATTERN, label="member SHA-256")


class PaperLedgerLifecycle(_StrictReviewModel):
    status: Literal["provisioned"]
    engine_status: Literal["not_enabled"]


class PaperLedgerStrategy(_StrictReviewModel):
    strategy_id: str
    name: str
    content_sha256: str

    @field_validator("strategy_id")
    @classmethod
    def require_strategy_id(cls, value: str) -> str:
        return _untrimmed_text(value, label="strategy_id")

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        return _human_text(value, label="strategy name")

    @field_validator("content_sha256")
    @classmethod
    def require_content_sha(cls, value: str) -> str:
        return _require_pattern(
            value,
            _SHA256_PATTERN,
            label="strategy content SHA-256",
        )


class PaperLedgerContract(_StrictReviewModel):
    contract_id: str
    exchange: str
    timezone: str

    @field_validator("contract_id", "exchange", "timezone")
    @classmethod
    def require_contract_text(cls, value: str) -> str:
        return _untrimmed_text(value, label="contract identity")


class PaperLedgerBaseline(_StrictReviewModel):
    run_id: str
    result_sha256: str
    range_start: str
    range_end: str
    rejection_count: int
    closest_algorithm: Literal["p5_structural_closest.v1"]
    closest_rejection_refs: tuple[str, ...]
    members: tuple[PaperEvidenceMember, ...]

    @field_validator("run_id")
    @classmethod
    def require_run_id(cls, value: str) -> str:
        return _run_id(value)

    @field_validator("result_sha256")
    @classmethod
    def require_result_sha(cls, value: str) -> str:
        return _require_pattern(value, _SHA256_PATTERN, label="result SHA-256")

    @field_validator("range_start", "range_end")
    @classmethod
    def require_range_utc(cls, value: str) -> str:
        return _canonical_utc(value)

    @field_validator("rejection_count", mode="before")
    @classmethod
    def require_rejection_count(cls, value: object) -> int:
        return _safe_count(value)

    @field_validator("closest_rejection_refs", "members", mode="before")
    @classmethod
    def require_arrays(cls, value: object) -> tuple[object, ...]:
        return _wire_array(value, label="baseline evidence")

    @field_validator("closest_rejection_refs")
    @classmethod
    def require_ref_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ref in value:
            _untrimmed_text(ref, label="closest rejection ref")
        if len(set(value)) != len(value):
            raise ValueError("closest rejection refs must be unique")
        return value

    @model_validator(mode="after")
    def require_reconciled_evidence(self) -> PaperLedgerBaseline:
        if len(self.closest_rejection_refs) != min(3, self.rejection_count):
            raise ValueError("closest refs count must equal min(3, rejection count)")
        expected_paths = _ledger_member_paths(self.run_id)
        if tuple(member.path for member in self.members) != expected_paths:
            raise ValueError("baseline members must use the exact four ordered paths")
        if self.members[0].sha256 != self.result_sha256:
            raise ValueError("first baseline member SHA must equal result SHA")
        return self


class PaperLedgerAccount(_StrictReviewModel):
    account_id: str
    currency: str
    initial_capital: float
    independent_account: Literal[True]

    @field_validator("account_id")
    @classmethod
    def require_account_id(cls, value: str) -> str:
        return _require_pattern(
            value,
            _ACCOUNT_ID_PATTERN,
            label="paper account ID",
        )

    @field_validator("currency")
    @classmethod
    def require_currency(cls, value: str) -> str:
        return _untrimmed_text(value, label="account currency")

    @field_validator("initial_capital", mode="before")
    @classmethod
    def require_capital(cls, value: object) -> float:
        capital = _finite_number(value, label="initial capital")
        if capital <= 0:
            raise ValueError("initial capital must be positive")
        return capital


class PaperLedgerBalances(_StrictReviewModel):
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float

    @field_validator("cash", "equity", "realized_pnl", "unrealized_pnl", mode="before")
    @classmethod
    def require_finite_money(cls, value: object) -> float:
        return _finite_number(value, label="ledger balance")


class PaperLedgerHighWaterMarks(_StrictReviewModel):
    trades: Literal[0]
    equity: Literal[1]
    events: Literal[4]
    expected_decisions: Literal[0]
    positions: Literal[0]
    orders: Literal[0]


class PaperLedgerSafety(_StrictReviewModel):
    state: Literal["not_running"]
    drawdown_r: float
    loss_streak: Literal[0]
    max_drawdown_r: Literal[8]
    max_losing_streak: Literal[8]
    blind_minutes: Literal[5]

    @field_validator("drawdown_r", mode="before")
    @classmethod
    def require_zero_drawdown(cls, value: object) -> float:
        number = _finite_number(value, label="drawdown_r")
        if number != 0:
            raise ValueError("drawdown_r must be exact zero")
        return number


class PaperSupportingEvidenceRef(_StrictReviewModel):
    path: str
    evidence_id: str

    @field_validator("path", "evidence_id")
    @classmethod
    def require_ref_text(cls, value: str) -> str:
        return _untrimmed_text(value, label="supporting evidence ref")


class PaperLedgerInterpretation(_StrictReviewModel):
    evaluation_status: Literal["not_evaluable"]
    reason: Literal["engine_not_enabled"]
    owner_view: str
    categories: tuple[Literal["unknown"]]
    supporting_evidence_refs: tuple[PaperSupportingEvidenceRef, ...]

    @field_validator("owner_view")
    @classmethod
    def require_owner_view(cls, value: str) -> str:
        return _human_text(value, label="owner interpretation")

    @field_validator("categories", "supporting_evidence_refs", mode="before")
    @classmethod
    def require_arrays(cls, value: object) -> tuple[object, ...]:
        return _wire_array(value, label="ledger interpretation")


class PaperLedgerOrigin(_StrictReviewModel):
    schema_version: Literal["paper_ledger_origin.v1"] = Field(alias="schema")
    ledger_origin_id: str
    trader_id: str
    origin_at: str
    lifecycle: PaperLedgerLifecycle
    strategy: PaperLedgerStrategy
    contract: PaperLedgerContract
    baseline: PaperLedgerBaseline
    account: PaperLedgerAccount
    balances: PaperLedgerBalances
    high_water_marks: PaperLedgerHighWaterMarks
    positions: tuple[object, ...]
    orders: tuple[object, ...]
    safety: PaperLedgerSafety
    readiness_snapshot: PaperReadinessSnapshot
    interpretation: PaperLedgerInterpretation

    @field_validator("ledger_origin_id")
    @classmethod
    def require_ledger_id(cls, value: str) -> str:
        return _require_pattern(value, _LEDGER_ID_PATTERN, label="ledger origin ID")

    @field_validator("trader_id")
    @classmethod
    def require_trader_id(cls, value: str) -> str:
        return _require_pattern(value, _TRADER_ID_PATTERN, label="trader ID")

    @field_validator("origin_at")
    @classmethod
    def require_origin_utc(cls, value: str) -> str:
        return _canonical_utc(value)

    @field_validator("positions", "orders", mode="before")
    @classmethod
    def require_known_arrays(cls, value: object) -> tuple[object, ...]:
        return _wire_array(value, label="known-empty ledger stream")

    @model_validator(mode="after")
    def require_origin_reconciliation(self) -> PaperLedgerOrigin:
        if self.positions or self.orders:
            raise ValueError("provisioned positions and orders must be known-empty")
        if (
            self.balances.cash != self.account.initial_capital
            or self.balances.equity != self.account.initial_capital
            or self.balances.realized_pnl != 0
            or self.balances.unrealized_pnl != 0
        ):
            raise ValueError("ledger balances differ from the persisted zero origin")
        expected_refs = self.baseline.closest_rejection_refs
        support = self.interpretation.supporting_evidence_refs
        if len(support) != len(expected_refs):
            raise ValueError("interpretation support count differs from closest refs")
        events_path = self.baseline.members[3].path
        if any(
            item.path != events_path or item.evidence_id != expected_refs[index]
            for index, item in enumerate(support)
        ):
            raise ValueError("interpretation support differs from persisted evidence")
        return self


class PaperReviewCreateRequest(_StrictReviewModel):
    schema_version: Literal["paper_review_create_request.v1"] = Field(alias="schema")
    request_id: str

    @field_validator("request_id")
    @classmethod
    def require_request_id(cls, value: str) -> str:
        return _canonical_uuid4(value)


class PaperReviewProgress(_StrictReviewModel):
    completed_parts: int
    total_parts: Literal[10]
    current_part: str | None

    @field_validator("completed_parts", mode="before")
    @classmethod
    def require_completed_parts(cls, value: object) -> int:
        completed = _safe_count(value)
        if completed > _TOTAL_PARTS:
            raise ValueError("completed parts cannot exceed total parts")
        return completed

    @field_validator("current_part")
    @classmethod
    def require_current_part(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _untrimmed_text(value, label="current review part")


class PaperReviewIssue(_StrictReviewModel):
    kind: PaperReviewIssueKind
    path: str | None
    source_ref: str | None
    expected_sha256: str | None
    actual_sha256: str | None
    ref_chain: tuple[str, ...]

    @field_validator("path", "source_ref")
    @classmethod
    def require_nullable_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _untrimmed_text(value, label="review issue ref")

    @field_validator("expected_sha256", "actual_sha256")
    @classmethod
    def require_nullable_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_pattern(value, _SHA256_PATTERN, label="review issue SHA")

    @field_validator("ref_chain", mode="before")
    @classmethod
    def require_ref_chain_array(cls, value: object) -> tuple[object, ...]:
        return _wire_array(value, label="review issue ref chain")

    @field_validator("ref_chain")
    @classmethod
    def require_chain(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ref in value:
            _untrimmed_text(ref, label="review issue ref chain")
        return value


class PaperReviewErrorPayload(_StrictReviewModel):
    schema_version: Literal["paper_review_error.v1"] = Field(alias="schema")
    code: PaperReviewErrorCode
    message: str
    retryable: bool
    request_id: str | None
    snapshot_id: str | None
    progress: PaperReviewProgress | None
    issues: tuple[PaperReviewIssue, ...]

    @field_validator("message")
    @classmethod
    def require_message(cls, value: str) -> str:
        return _human_text(value, label="paper review error message")

    @field_validator("request_id")
    @classmethod
    def require_nullable_request(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _canonical_uuid4(value)

    @field_validator("snapshot_id")
    @classmethod
    def require_nullable_snapshot(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _canonical_snapshot_id(value)

    @field_validator("issues", mode="before")
    @classmethod
    def require_issue_array(cls, value: object) -> tuple[object, ...]:
        return _wire_array(value, label="paper review issues")

    @model_validator(mode="after")
    def require_retryability(self) -> PaperReviewErrorPayload:
        if self.retryable != (self.code in _RETRYABLE_CODES):
            raise ValueError("review error retryable flag differs from its code")
        return self


class PaperTerminalOpenerClaim(_StrictReviewModel):
    bytes: int
    sha256: str

    @field_validator("bytes", mode="before")
    @classmethod
    def require_bytes(cls, value: object) -> int:
        return _safe_count(value, positive=True)

    @field_validator("sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        return _require_pattern(
            value,
            _SHA256_PATTERN,
            label="terminal opener SHA-256",
        )


class PaperReviewReady(_StrictReviewModel):
    schema_version: Literal["paper_review_ready.v1"] = Field(alias="schema")
    display_filename: str
    artifact_bytes: int
    artifact_sha256: str
    member_count: Literal[10]
    members: tuple[PaperEvidenceMember, ...]
    terminal_opener: PaperTerminalOpenerClaim
    ready_at: str

    @field_validator("display_filename")
    @classmethod
    def require_filename(cls, value: str) -> str:
        return _untrimmed_text(value, label="paper review display filename")

    @field_validator("artifact_bytes", mode="before")
    @classmethod
    def require_artifact_bytes(cls, value: object) -> int:
        return _safe_count(value, positive=True)

    @field_validator("artifact_sha256")
    @classmethod
    def require_artifact_sha(cls, value: str) -> str:
        return _require_pattern(value, _SHA256_PATTERN, label="artifact SHA-256")

    @field_validator("ready_at")
    @classmethod
    def require_ready_at(cls, value: str) -> str:
        return _canonical_utc(value)

    @field_validator("members", mode="before")
    @classmethod
    def require_member_array(cls, value: object) -> tuple[object, ...]:
        return _wire_array(value, label="paper review ready members")

    @model_validator(mode="after")
    def require_members(self) -> PaperReviewReady:
        if len(self.members) != self.member_count:
            raise ValueError("ready member count differs from exact manifest length")
        return self


class PaperReviewTerminalOpener(_StrictReviewModel):
    schema_version: Literal["paper_review_terminal_opener.v1"] = Field(
        alias="schema"
    )
    snapshot_id: str
    text: str
    bytes: int
    sha256: str

    @field_validator("snapshot_id")
    @classmethod
    def require_snapshot_id(cls, value: str) -> str:
        return _canonical_snapshot_id(value)

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        return _human_text(value, label="persisted terminal opener")

    @field_validator("bytes", mode="before")
    @classmethod
    def require_bytes(cls, value: object) -> int:
        return _safe_count(value, positive=True)

    @field_validator("sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        return _require_pattern(
            value,
            _SHA256_PATTERN,
            label="terminal opener SHA-256",
        )

    @model_validator(mode="after")
    def require_persisted_claim(self) -> PaperReviewTerminalOpener:
        payload = self.text.encode("utf-8")
        if len(payload) != self.bytes or sha256(payload).hexdigest() != self.sha256:
            raise ValueError("terminal opener bytes or SHA differ from persisted text")
        return self


class PaperReviewStatus(_StrictReviewModel):
    schema_version: Literal["paper_review_status.v1"] = Field(alias="schema")
    request_id: str
    snapshot_id: str
    trader_id: str
    status: Literal["preparing", "ready", "failed"]
    captured_at: str
    progress: PaperReviewProgress
    ready: PaperReviewReady | None
    error: PaperReviewErrorPayload | None

    @field_validator("request_id")
    @classmethod
    def require_request_id(cls, value: str) -> str:
        return _canonical_uuid4(value)

    @field_validator("snapshot_id")
    @classmethod
    def require_snapshot_id(cls, value: str) -> str:
        return _canonical_snapshot_id(value)

    @field_validator("trader_id")
    @classmethod
    def require_trader_id(cls, value: str) -> str:
        return _require_pattern(value, _TRADER_ID_PATTERN, label="trader ID")

    @field_validator("captured_at")
    @classmethod
    def require_captured_at(cls, value: str) -> str:
        return _canonical_utc(value)

    @model_validator(mode="after")
    def require_discriminated_state(self) -> PaperReviewStatus:
        if self.status == "preparing":
            if self.ready is not None or self.error is not None:
                raise ValueError("preparing must not contain ready or error")
        elif self.status == "ready":
            if (
                self.ready is None
                or self.error is not None
                or self.progress.completed_parts != _TOTAL_PARTS
                or self.progress.current_part is not None
            ):
                raise ValueError("ready status has an invalid terminal boundary")
        elif (
            self.ready is not None
            or self.error is None
            or self.progress.current_part is not None
            or self.error.request_id != self.request_id
            or self.error.snapshot_id != self.snapshot_id
            or self.error.progress != self.progress
        ):
            raise ValueError("failed status has an invalid terminal boundary")
        return self


@dataclass(frozen=True, slots=True)
class _RequestIdentity:
    request_id: str
    snapshot_id: str
    request_fingerprint: str
    trader_id: str
    ledger_origin_id: str
    captured_at: str
    builder_instance_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class _RequestRecord(_RequestIdentity):
    trader: PaperTrader


@dataclass(frozen=True, slots=True)
class _ActiveBuilder:
    builder_instance_id: str
    progress: PaperReviewProgress
    claimed: bool


@dataclass(frozen=True, slots=True)
class PaperReviewReadyResource:
    """Validated persisted ready state plus its server-only read authorities."""

    status: PaperReviewStatus
    artifact_relpath: str
    terminal_opener_text: str


class PaperReviewBuilderRegistry:
    """Process-local active builder identity and ephemeral progress."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._active: dict[tuple[str, str], _ActiveBuilder] = {}

    def activate(
        self,
        *,
        store_key: str,
        request_id: str,
        builder_instance_id: str,
    ) -> None:
        _canonical_uuid4(request_id)
        _canonical_uuid4(builder_instance_id)
        with self._lock:
            self._active[(store_key, request_id)] = _ActiveBuilder(
                builder_instance_id=builder_instance_id,
                progress=PaperReviewProgress(
                    completed_parts=0,
                    total_parts=10,
                    current_part=None,
                ),
                claimed=False,
            )

    def claim(
        self,
        *,
        store_key: str,
        request_id: str,
        builder_instance_id: str,
    ) -> bool:
        """Atomically grant the active request to exactly one artifact builder."""
        with self._lock:
            key = (store_key, request_id)
            active = self._active.get(key)
            if (
                active is None
                or active.builder_instance_id != builder_instance_id
                or active.claimed
            ):
                return False
            self._active[key] = _ActiveBuilder(
                builder_instance_id=active.builder_instance_id,
                progress=active.progress,
                claimed=True,
            )
            return True

    def progress(
        self,
        *,
        store_key: str,
        request_id: str,
        builder_instance_id: str,
    ) -> PaperReviewProgress | None:
        with self._lock:
            active = self._active.get((store_key, request_id))
            if (
                active is None
                or active.builder_instance_id != builder_instance_id
            ):
                return None
            return active.progress

    def update(
        self,
        *,
        store_key: str,
        request_id: str,
        builder_instance_id: str,
        progress: PaperReviewProgress,
    ) -> None:
        with self._lock:
            key = (store_key, request_id)
            active = self._active.get(key)
            if (
                active is None
                or active.builder_instance_id != builder_instance_id
            ):
                raise ValueError("paper review builder is not active")
            self._active[key] = _ActiveBuilder(
                builder_instance_id=builder_instance_id,
                progress=progress,
                claimed=active.claimed,
            )

    def deactivate(self, *, store_key: str, request_id: str) -> None:
        with self._lock:
            self._active.pop((store_key, request_id), None)


_DEFAULT_BUILDERS = PaperReviewBuilderRegistry()


def review_request_fingerprint(*, request_id: str, trader_id: str) -> str:
    canonical_request = _canonical_uuid4(request_id)
    canonical_trader = _require_pattern(
        trader_id,
        _TRADER_ID_PATTERN,
        label="trader ID",
    )
    intent = {
        "request_id": canonical_request,
        "schema": "paper_review_create_intent.v1",
        "trader_id": canonical_trader,
    }
    return sha256(_canonical_json(intent).encode("utf-8")).hexdigest()


class PaperReviewService:
    """API-shaped Phase 2 producer over one exact P6 Stage B store."""

    def __init__(
        self,
        trader_store: PaperTraderStore,
        *,
        clock: Callable[[], datetime] | None = None,
        snapshot_id_factory: Callable[[], str] | None = None,
        builder_instance_id_factory: Callable[[], str] | None = None,
        builder_registry: PaperReviewBuilderRegistry | None = None,
        step_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._store = trader_store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._snapshot_id_factory = snapshot_id_factory or (
            lambda: f"paper-review-{uuid4().hex}"
        )
        self._builder_instance_id_factory = (
            builder_instance_id_factory or (lambda: str(uuid4()))
        )
        self._builders = builder_registry or _DEFAULT_BUILDERS
        self._step_hook = step_hook
        self._store_key = str(self._store.path.resolve())

    @property
    def builder_registry(self) -> PaperReviewBuilderRegistry:
        return self._builders

    def ledger_origin(self, trader_id: str) -> PaperLedgerOrigin:
        canonical_trader_id = _require_pattern(
            trader_id,
            _TRADER_ID_PATTERN,
            label="trader ID",
        )
        if not self._store.path.is_file():
            raise PaperReviewDomainError(
                "trader_not_found",
                "paper trader does not exist",
            )
        try:
            connection = self._store._connect_readonly()
        except PaperTraderStoreSchemaUpgradeRequiredError as exc:
            raise PaperReviewDomainError(
                "store_schema_upgrade_required",
                "paper trader store requires an explicit schema upgrade",
            ) from exc
        except PaperTraderStoreIntegrityError as exc:
            raise PaperReviewDomainError(
                "ledger_integrity_failed",
                "paper ledger store failed its exact schema guard",
            ) from exc
        try:
            row = connection.execute(
                "SELECT * FROM paper_traders WHERE trader_id = ?",
                (canonical_trader_id,),
            ).fetchone()
            if row is None:
                raise PaperReviewDomainError(
                    "trader_not_found",
                    "paper trader does not exist",
                )
            origins = connection.execute(
                """
                SELECT *
                FROM paper_ledger_origins
                WHERE trader_id = ?
                """,
                (canonical_trader_id,),
            ).fetchall()
            if not origins:
                raise PaperReviewDomainError(
                    "ledger_not_ready",
                    "paper trader has no immutable ledger origin",
                )
            if len(origins) != 1:
                raise ValueError("paper trader has multiple ledger origins")
            record = self._store._record_from_row(connection, row)
            return self._build_ledger_origin(
                connection,
                record=record,
                origin=origins[0],
            )
        except PaperReviewDomainError:
            raise
        except (
            json.JSONDecodeError,
            KeyError,
            PaperTraderStoreIntegrityError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise PaperReviewDomainError(
                "ledger_integrity_failed",
                "paper ledger differs from immutable persisted truth",
            ) from exc
        finally:
            connection.close()

    def _build_ledger_origin(
        self,
        connection: sqlite3.Connection,
        *,
        record: PaperTrader,
        origin: sqlite3.Row,
    ) -> PaperLedgerOrigin:
        ledger_origin_id = cast(str, origin["ledger_origin_id"])
        equity_rows = connection.execute(
            """
            SELECT *
            FROM paper_ledger_equity_points
            WHERE ledger_origin_id = ?
            ORDER BY ordinal
            """,
            (ledger_origin_id,),
        ).fetchall()
        if len(equity_rows) != 1:
            raise ValueError("ledger origin requires one persisted equity point")
        equity = equity_rows[0]
        members = connection.execute(
            """
            SELECT ordinal, zip_path, byte_count, sha256
            FROM paper_ledger_baseline_members
            WHERE ledger_origin_id = ?
            ORDER BY ordinal
            """,
            (ledger_origin_id,),
        ).fetchall()
        if len(members) != 4:
            raise ValueError("ledger origin requires four persisted members")
        interpretation_value = _decode_canonical_json(origin["interpretation_json"])
        if not isinstance(interpretation_value, dict):
            raise ValueError("ledger interpretation must be an object")
        closest_value = _decode_canonical_json(
            origin["closest_rejection_refs_json"]
        )
        if not isinstance(closest_value, list):
            raise ValueError("closest rejection refs must be an array")
        return PaperLedgerOrigin.model_validate(
            {
                "schema": "paper_ledger_origin.v1",
                "ledger_origin_id": ledger_origin_id,
                "trader_id": origin["trader_id"],
                "origin_at": origin["origin_at"],
                "lifecycle": {
                    "status": origin["lifecycle_status"],
                    "engine_status": origin["engine_status"],
                },
                "strategy": {
                    "strategy_id": origin["strategy_id"],
                    "name": origin["strategy_name"],
                    "content_sha256": origin["strategy_content_sha256"],
                },
                "contract": {
                    "contract_id": origin["contract_id"],
                    "exchange": origin["contract_exchange"],
                    "timezone": origin["contract_timezone"],
                },
                "baseline": {
                    "run_id": origin["baseline_run_id"],
                    "result_sha256": origin["baseline_result_sha256"],
                    "range_start": origin["baseline_range_start"],
                    "range_end": origin["baseline_range_end"],
                    "rejection_count": origin["baseline_rejection_count"],
                    "closest_algorithm": origin["closest_algorithm"],
                    "closest_rejection_refs": closest_value,
                    "members": [
                        {
                            "path": member["zip_path"],
                            "bytes": member["byte_count"],
                            "sha256": member["sha256"],
                        }
                        for member in members
                    ],
                },
                "account": {
                    "account_id": origin["account_id"],
                    "currency": equity["currency"],
                    "initial_capital": origin["initial_capital"],
                    "independent_account": bool(origin["independent_account"]),
                },
                "balances": {
                    "cash": equity["cash"],
                    "equity": equity["equity"],
                    "realized_pnl": equity["realized_pnl"],
                    "unrealized_pnl": equity["unrealized_pnl"],
                },
                "high_water_marks": {
                    "trades": origin["trades_hwm"],
                    "equity": origin["equity_hwm"],
                    "events": origin["events_hwm"],
                    "expected_decisions": origin["expected_decisions_hwm"],
                    "positions": origin["positions_hwm"],
                    "orders": origin["orders_hwm"],
                },
                "positions": [],
                "orders": [],
                "safety": {
                    "state": origin["safety_state"],
                    "drawdown_r": origin["drawdown_r"],
                    "loss_streak": origin["loss_streak"],
                    "max_drawdown_r": record.safeguards.max_drawdown_r,
                    "max_losing_streak": record.safeguards.max_losing_streak,
                    "blind_minutes": record.safeguards.blind_minutes,
                },
                "readiness_snapshot": record.readiness_snapshot,
                "interpretation": interpretation_value,
            },
            strict=True,
        )

    def accept_review_snapshot(
        self,
        *,
        trader_id: str,
        body: PaperReviewCreateRequest,
    ) -> tuple[PaperReviewStatus, bool]:
        canonical_trader = _require_pattern(
            trader_id,
            _TRADER_ID_PATTERN,
            label="trader ID",
        )
        if not isinstance(body, PaperReviewCreateRequest):
            raise TypeError("body must be a validated PaperReviewCreateRequest")
        try:
            ledger = self.ledger_origin(canonical_trader)
        except PaperReviewDomainError as exc:
            raise PaperReviewDomainError(
                exc.code,
                str(exc),
                request_id=body.request_id,
                issues=exc.payload.issues,
            ) from exc
        fingerprint = review_request_fingerprint(
            request_id=body.request_id,
            trader_id=canonical_trader,
        )
        with _ACCEPT_LOCK:
            existing = self._read_request_if_present(body.request_id)
            if existing is not None:
                self._assert_request_intent(
                    existing,
                    trader_id=canonical_trader,
                    ledger_origin_id=ledger.ledger_origin_id,
                    fingerprint=fingerprint,
                )
                return self.review_request_status(body.request_id), False

            snapshot_id = _canonical_snapshot_id(
                self._snapshot_id_factory()
            )
            captured_at = _timestamp_text(self._clock())
            _canonical_utc(captured_at)
            builder_instance_id = _canonical_uuid4(
                self._builder_instance_id_factory()
            )

            connection = self._connect_write_for_review()
            existing_after_lock: _RequestRecord | None = None
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT *
                    FROM paper_review_requests
                    WHERE request_id = ?
                    """,
                    (body.request_id,),
                ).fetchone()
                if row is not None:
                    existing_after_lock = self._request_record(connection, row)
                    connection.rollback()
                else:
                    connection.execute(
                        """
                        INSERT INTO paper_review_requests (
                            request_id, snapshot_id, request_fingerprint,
                            trader_id, ledger_origin_id, captured_at,
                            builder_instance_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            body.request_id,
                            snapshot_id,
                            fingerprint,
                            canonical_trader,
                            ledger.ledger_origin_id,
                            captured_at,
                            builder_instance_id,
                            captured_at,
                        ),
                    )
                    self._call_step_hook("review_request")
                    connection.execute(
                        """
                        INSERT INTO paper_review_status_events (
                            request_id, ordinal, status, occurred_at, payload_json
                        ) VALUES (?, 1, 'preparing', ?, ?)
                        """,
                        (
                            body.request_id,
                            captured_at,
                            _canonical_json(
                                {
                                    "completed_parts": 0,
                                    "current_part": None,
                                    "total_parts": 10,
                                }
                            ),
                        ),
                    )
                    self._call_step_hook("preparing_event")
                    connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

            if existing_after_lock is not None:
                self._assert_request_intent(
                    existing_after_lock,
                    trader_id=canonical_trader,
                    ledger_origin_id=ledger.ledger_origin_id,
                    fingerprint=fingerprint,
                )
                return self.review_request_status(body.request_id), False

            self._builders.activate(
                store_key=self._store_key,
                request_id=body.request_id,
                builder_instance_id=builder_instance_id,
            )
            return self.review_request_status(body.request_id), True

    def review_request_status(self, request_id: str) -> PaperReviewStatus:
        with _ACCEPT_LOCK:
            return self._review_request_status_visible(request_id)

    def ready_snapshot(
        self,
        snapshot_id: str,
        *,
        defer_artifact_path_validation: bool = False,
    ) -> PaperReviewReadyResource:
        """Resolve one ready snapshot without writing, rebuilding, or creating."""
        canonical_snapshot = _require_pattern(
            snapshot_id,
            _SNAPSHOT_ID_PATTERN,
            label="snapshot ID",
        )
        with _ACCEPT_LOCK:
            identity = self._request_identity_for_snapshot(canonical_snapshot)
            try:
                status = self._review_request_status_visible(
                    identity.request_id,
                    validate_artifact_relpath=not defer_artifact_path_validation,
                )
            except PaperReviewDomainError as exc:
                if exc.code != "snapshot_integrity_failed":
                    raise
                raise PaperReviewDomainError(
                    "snapshot_integrity_failed",
                    str(exc),
                    request_id=identity.request_id,
                    snapshot_id=canonical_snapshot,
                    issues=exc.payload.issues,
                ) from exc
            if status.snapshot_id != canonical_snapshot:
                raise PaperReviewDomainError(
                    "snapshot_integrity_failed",
                    "paper review snapshot identity differs from its request",
                    request_id=identity.request_id,
                    snapshot_id=canonical_snapshot,
                )
            if status.status == "preparing":
                raise PaperReviewDomainError(
                    "snapshot_not_ready",
                    "paper review snapshot is not ready",
                    request_id=status.request_id,
                    snapshot_id=status.snapshot_id,
                    progress=status.progress,
                )
            if status.status == "failed":
                issues = status.error.issues if status.error is not None else ()
                raise PaperReviewDomainError(
                    "snapshot_failed",
                    "paper review snapshot failed",
                    request_id=status.request_id,
                    snapshot_id=status.snapshot_id,
                    progress=status.progress,
                    issues=issues,
                )
            if status.ready is None:
                raise PaperReviewDomainError(
                    "snapshot_integrity_failed",
                    "paper review ready status is incomplete",
                    request_id=status.request_id,
                    snapshot_id=status.snapshot_id,
                )
            try:
                artifact_relpath, opener_text = self._ready_read_authorities(
                    request_id=status.request_id,
                    snapshot_id=status.snapshot_id,
                )
            except PaperReviewDomainError as exc:
                raise PaperReviewDomainError(
                    exc.code,
                    str(exc),
                    request_id=status.request_id,
                    snapshot_id=status.snapshot_id,
                    progress=status.progress,
                    issues=exc.payload.issues,
                ) from exc
            opener_bytes = opener_text.encode("utf-8")
            if (
                len(opener_bytes) != status.ready.terminal_opener.bytes
                or sha256(opener_bytes).hexdigest()
                != status.ready.terminal_opener.sha256
            ):
                raise PaperReviewDomainError(
                    "snapshot_integrity_failed",
                    "persisted terminal opener differs from ready metadata",
                    request_id=status.request_id,
                    snapshot_id=status.snapshot_id,
                    progress=status.progress,
                )
            return PaperReviewReadyResource(
                status=status,
                artifact_relpath=artifact_relpath,
                terminal_opener_text=opener_text,
            )

    def terminal_opener(self, snapshot_id: str) -> PaperReviewTerminalOpener:
        """Project only the persisted, byte-verified terminal opener."""
        resource = self.ready_snapshot(snapshot_id)
        ready = resource.status.ready
        if ready is None:  # pragma: no cover - guarded by ready_snapshot
            raise AssertionError("ready snapshot lost its ready metadata")
        return PaperReviewTerminalOpener(
            schema="paper_review_terminal_opener.v1",
            snapshot_id=resource.status.snapshot_id,
            text=resource.terminal_opener_text,
            bytes=ready.terminal_opener.bytes,
            sha256=ready.terminal_opener.sha256,
        )

    def _request_identity_for_snapshot(
        self,
        snapshot_id: str,
    ) -> _RequestIdentity:
        try:
            error_snapshot_id: str | None = _canonical_snapshot_id(snapshot_id)
        except ValueError:
            error_snapshot_id = None
        if not self._store.path.is_file():
            raise PaperReviewDomainError(
                "snapshot_not_found",
                "paper review snapshot does not exist",
                snapshot_id=error_snapshot_id,
            )
        try:
            connection = self._store._connect_readonly()
        except PaperTraderStoreSchemaUpgradeRequiredError as exc:
            raise PaperReviewDomainError(
                "store_schema_upgrade_required",
                "paper trader store requires an explicit schema upgrade",
                snapshot_id=error_snapshot_id,
            ) from exc
        except PaperTraderStoreIntegrityError as exc:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "paper review store failed its exact schema guard",
                snapshot_id=error_snapshot_id,
            ) from exc
        try:
            rows = connection.execute(
                """
                SELECT *
                FROM paper_review_requests
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchall()
            if not rows:
                raise PaperReviewDomainError(
                    "snapshot_not_found",
                    "paper review snapshot does not exist",
                    snapshot_id=error_snapshot_id,
                )
            if len(rows) != 1:
                raise ValueError("paper review snapshot identity is not unique")
            return self._request_identity(rows[0])
        except PaperReviewDomainError:
            raise
        except (
            KeyError,
            PaperTraderStoreIntegrityError,
            TypeError,
            ValueError,
        ) as exc:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "paper review snapshot identity is malformed",
                snapshot_id=error_snapshot_id,
            ) from exc
        finally:
            connection.close()

    def _ready_read_authorities(
        self,
        *,
        request_id: str,
        snapshot_id: str,
    ) -> tuple[str, str]:
        try:
            connection = self._store._connect_readonly()
        except PaperTraderStoreSchemaUpgradeRequiredError as exc:
            raise PaperReviewDomainError(
                "store_schema_upgrade_required",
                "paper trader store requires an explicit schema upgrade",
                request_id=request_id,
                snapshot_id=snapshot_id,
            ) from exc
        except PaperTraderStoreIntegrityError as exc:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "paper review store failed its exact schema guard",
                request_id=request_id,
                snapshot_id=snapshot_id,
            ) from exc
        try:
            rows = connection.execute(
                """
                SELECT artifact_relpath, terminal_opener_text
                FROM paper_review_ready_artifacts
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError("ready snapshot requires one persisted ready row")
            artifact_relpath = rows[0]["artifact_relpath"]
            opener_text = rows[0]["terminal_opener_text"]
            if not isinstance(artifact_relpath, str):
                raise ValueError("artifact relative path must be text")
            return artifact_relpath, _human_text(
                opener_text,
                label="persisted terminal opener",
            )
        except (
            KeyError,
            PaperTraderStoreIntegrityError,
            TypeError,
            ValueError,
        ) as exc:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "paper review ready read authority is malformed",
                request_id=request_id,
                snapshot_id=snapshot_id,
            ) from exc
        finally:
            connection.close()

    def _artifact_build_entry(
        self,
        request_id: str,
    ) -> tuple[PaperReviewStatus, _RequestIdentity, bool]:
        """Read lifecycle state and atomically claim one active artifact build."""
        canonical_request = _canonical_uuid4(request_id)
        with _ACCEPT_LOCK:
            if not self._store.path.is_file():
                raise PaperReviewDomainError(
                    "request_not_found",
                    "paper review request does not exist",
                    request_id=canonical_request,
                )
            try:
                connection = self._store._connect_readonly()
            except PaperTraderStoreSchemaUpgradeRequiredError as exc:
                raise PaperReviewDomainError(
                    "store_schema_upgrade_required",
                    "paper trader store requires an explicit schema upgrade",
                ) from exc
            except PaperTraderStoreIntegrityError as exc:
                raise PaperReviewDomainError(
                    "snapshot_integrity_failed",
                    "paper review store failed its exact schema guard",
                ) from exc
            try:
                row = connection.execute(
                    """
                    SELECT *
                    FROM paper_review_requests
                    WHERE request_id = ?
                    """,
                    (canonical_request,),
                ).fetchone()
                if row is None:
                    raise PaperReviewDomainError(
                        "request_not_found",
                        "paper review request does not exist",
                        request_id=canonical_request,
                    )
                identity = self._request_identity(row)
                events = connection.execute(
                    """
                    SELECT *
                    FROM paper_review_status_events
                    WHERE request_id = ?
                    ORDER BY ordinal
                    """,
                    (canonical_request,),
                ).fetchall()
                ready_rows = connection.execute(
                    """
                    SELECT *
                    FROM paper_review_ready_artifacts
                    WHERE request_id = ?
                    """,
                    (canonical_request,),
                ).fetchall()
                failure_rows = connection.execute(
                    """
                    SELECT *
                    FROM paper_review_failures
                    WHERE request_id = ?
                    """,
                    (canonical_request,),
                ).fetchall()

                if len(events) == 1:
                    progress = self._preparing_event(
                        events[0],
                        request=identity,
                    )
                    if ready_rows or failure_rows:
                        raise ValueError(
                            "non-terminal request has a terminal row"
                        )
                    active = self._builders.progress(
                        store_key=self._store_key,
                        request_id=identity.request_id,
                        builder_instance_id=identity.builder_instance_id,
                    )
                    if active is None:
                        return (
                            self._interrupted_status(
                                identity,
                                progress=progress,
                            ),
                            identity,
                            False,
                        )
                    status = PaperReviewStatus(
                        schema="paper_review_status.v1",
                        request_id=identity.request_id,
                        snapshot_id=identity.snapshot_id,
                        trader_id=identity.trader_id,
                        status="preparing",
                        captured_at=identity.captured_at,
                        progress=active,
                        ready=None,
                        error=None,
                    )
                    claimed = self._builders.claim(
                        store_key=self._store_key,
                        request_id=identity.request_id,
                        builder_instance_id=identity.builder_instance_id,
                    )
                    return status, identity, claimed

                if (
                    len(events) == 2
                    and events[1]["ordinal"] == 2
                    and events[1]["status"] == "failed"
                ):
                    if len(failure_rows) != 1 or ready_rows:
                        raise ValueError(
                            "failed event requires exactly one failure row"
                        )
                    progress = self._preparing_event(
                        events[0],
                        request=identity,
                    )
                    error = self._failure_from_row(
                        failure_rows[0],
                        request=identity,
                        event=events[1],
                    )
                    if error.progress is None or progress.completed_parts != 0:
                        raise ValueError("persisted failure progress is invalid")
                    return (
                        PaperReviewStatus(
                            schema="paper_review_status.v1",
                            request_id=identity.request_id,
                            snapshot_id=identity.snapshot_id,
                            trader_id=identity.trader_id,
                            status="failed",
                            captured_at=identity.captured_at,
                            progress=error.progress,
                            ready=None,
                            error=error,
                        ),
                        identity,
                        False,
                    )

                request = self._request_record(connection, row)
                return (
                    self._project_status(
                        request=request,
                        events=events,
                        ready_rows=ready_rows,
                        failure_rows=failure_rows,
                    ),
                    identity,
                    False,
                )
            except PaperReviewDomainError:
                raise
            except (
                json.JSONDecodeError,
                KeyError,
                PaperTraderStoreIntegrityError,
                TypeError,
                ValueError,
                ValidationError,
            ) as exc:
                raise PaperReviewDomainError(
                    "snapshot_integrity_failed",
                    "paper review request state is malformed or inconsistent",
                    request_id=canonical_request,
                ) from exc
            finally:
                connection.close()

    @staticmethod
    def _interrupted_status(
        request: _RequestIdentity,
        *,
        progress: PaperReviewProgress,
    ) -> PaperReviewStatus:
        error = PaperReviewErrorPayload(
            schema="paper_review_error.v1",
            code="build_interrupted",
            message="Snapshot build is no longer active in this process.",
            retryable=False,
            request_id=request.request_id,
            snapshot_id=request.snapshot_id,
            progress=progress,
            issues=(
                PaperReviewIssue(
                    kind="build_interrupted",
                    path=None,
                    source_ref=None,
                    expected_sha256=None,
                    actual_sha256=None,
                    ref_chain=(),
                ),
            ),
        )
        return PaperReviewStatus(
            schema="paper_review_status.v1",
            request_id=request.request_id,
            snapshot_id=request.snapshot_id,
            trader_id=request.trader_id,
            status="failed",
            captured_at=request.captured_at,
            progress=progress,
            ready=None,
            error=error,
        )

    def _review_request_status_visible(
        self,
        request_id: str,
        *,
        validate_artifact_relpath: bool = True,
    ) -> PaperReviewStatus:
        canonical_request = _canonical_uuid4(request_id)
        if not self._store.path.is_file():
            raise PaperReviewDomainError(
                "request_not_found",
                "paper review request does not exist",
                request_id=canonical_request,
            )
        try:
            connection = self._store._connect_readonly()
        except PaperTraderStoreSchemaUpgradeRequiredError as exc:
            raise PaperReviewDomainError(
                "store_schema_upgrade_required",
                "paper trader store requires an explicit schema upgrade",
                request_id=canonical_request,
            ) from exc
        except PaperTraderStoreIntegrityError as exc:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "paper review store failed its exact schema guard",
                request_id=canonical_request,
            ) from exc
        snapshot_identity: str | None = None
        try:
            row = connection.execute(
                """
                SELECT *
                FROM paper_review_requests
                WHERE request_id = ?
                """,
                (canonical_request,),
            ).fetchone()
            if row is None:
                raise PaperReviewDomainError(
                    "request_not_found",
                    "paper review request does not exist",
                    request_id=canonical_request,
                )
            identity = self._request_identity(row)
            snapshot_identity = identity.snapshot_id
            events = connection.execute(
                """
                SELECT *
                FROM paper_review_status_events
                WHERE request_id = ?
                ORDER BY ordinal
                """,
                (canonical_request,),
            ).fetchall()
            ready_rows = connection.execute(
                """
                SELECT *
                FROM paper_review_ready_artifacts
                WHERE request_id = ?
                """,
                (canonical_request,),
            ).fetchall()
            failure_rows = connection.execute(
                """
                SELECT *
                FROM paper_review_failures
                WHERE request_id = ?
                """,
                (canonical_request,),
            ).fetchall()
            terminal_failure = self._terminal_failure_status(
                request=identity,
                events=events,
                ready_rows=ready_rows,
                failure_rows=failure_rows,
            )
            if terminal_failure is not None:
                return terminal_failure
            request = self._request_record(connection, row)
            return self._project_status(
                request=request,
                events=events,
                ready_rows=ready_rows,
                failure_rows=failure_rows,
                validate_artifact_relpath=validate_artifact_relpath,
            )
        except PaperReviewDomainError:
            raise
        except (
            json.JSONDecodeError,
            KeyError,
            PaperTraderStoreIntegrityError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "paper review request state is malformed or inconsistent",
                request_id=canonical_request,
                snapshot_id=snapshot_identity,
            ) from exc
        finally:
            connection.close()

    def _terminal_failure_status(
        self,
        *,
        request: _RequestIdentity,
        events: Sequence[sqlite3.Row],
        ready_rows: Sequence[sqlite3.Row],
        failure_rows: Sequence[sqlite3.Row],
    ) -> PaperReviewStatus | None:
        if (
            len(events) != 2
            or events[1]["ordinal"] != 2
            or events[1]["status"] != "failed"
        ):
            return None
        self._preparing_event(events[0], request=request)
        if len(failure_rows) != 1 or ready_rows:
            raise ValueError("failed event requires exactly one failure row")
        error = self._failure_from_row(
            failure_rows[0],
            request=request,
            event=events[1],
        )
        if error.progress is None:
            raise ValueError("persisted terminal failure requires progress")
        return PaperReviewStatus(
            schema="paper_review_status.v1",
            request_id=request.request_id,
            snapshot_id=request.snapshot_id,
            trader_id=request.trader_id,
            status="failed",
            captured_at=request.captured_at,
            progress=error.progress,
            ready=None,
            error=error,
        )

    def _project_status(
        self,
        *,
        request: _RequestRecord,
        events: Sequence[sqlite3.Row],
        ready_rows: Sequence[sqlite3.Row],
        failure_rows: Sequence[sqlite3.Row],
        validate_artifact_relpath: bool = True,
    ) -> PaperReviewStatus:
        if not events:
            raise ValueError("review request has no preparing event")
        preparing_progress = self._preparing_event(events[0], request=request)
        allowed_parts = _review_member_paths(request.trader.baseline.run_id)

        if len(events) == 1:
            if ready_rows or failure_rows:
                raise ValueError("non-terminal request has a terminal row")
            active = self._builders.progress(
                store_key=self._store_key,
                request_id=request.request_id,
                builder_instance_id=request.builder_instance_id,
            )
            if active is not None:
                if (
                    active.current_part is not None
                    and active.current_part not in allowed_parts
                ):
                    raise ValueError("active progress has an unknown member path")
                return PaperReviewStatus(
                    schema="paper_review_status.v1",
                    request_id=request.request_id,
                    snapshot_id=request.snapshot_id,
                    trader_id=request.trader_id,
                    status="preparing",
                    captured_at=request.captured_at,
                    progress=active,
                    ready=None,
                    error=None,
                )
            error = PaperReviewErrorPayload(
                schema="paper_review_error.v1",
                code="build_interrupted",
                message="Snapshot build is no longer active in this process.",
                retryable=False,
                request_id=request.request_id,
                snapshot_id=request.snapshot_id,
                progress=preparing_progress,
                issues=(
                    PaperReviewIssue(
                        kind="build_interrupted",
                        path=None,
                        source_ref=None,
                        expected_sha256=None,
                        actual_sha256=None,
                        ref_chain=(),
                    ),
                ),
            )
            return PaperReviewStatus(
                schema="paper_review_status.v1",
                request_id=request.request_id,
                snapshot_id=request.snapshot_id,
                trader_id=request.trader_id,
                status="failed",
                captured_at=request.captured_at,
                progress=preparing_progress,
                ready=None,
                error=error,
            )

        if len(events) != 2:
            raise ValueError("review request has an extra status event")
        terminal = events[1]
        if terminal["ordinal"] != 2:
            raise ValueError("review terminal event must be ordinal two")
        status = terminal["status"]
        if status == "ready":
            if len(ready_rows) != 1 or failure_rows:
                raise ValueError("ready event requires exactly one ready row")
            ready = self._ready_from_row(
                ready_rows[0],
                request=request,
                event=terminal,
                validate_artifact_relpath=validate_artifact_relpath,
            )
            progress = PaperReviewProgress(
                completed_parts=10,
                total_parts=10,
                current_part=None,
            )
            return PaperReviewStatus(
                schema="paper_review_status.v1",
                request_id=request.request_id,
                snapshot_id=request.snapshot_id,
                trader_id=request.trader_id,
                status="ready",
                captured_at=request.captured_at,
                progress=progress,
                ready=ready,
                error=None,
            )
        if status == "failed":
            if len(failure_rows) != 1 or ready_rows:
                raise ValueError("failed event requires exactly one failure row")
            error = self._failure_from_row(
                failure_rows[0],
                request=request,
                event=terminal,
            )
            if error.progress is None:
                raise ValueError("persisted terminal failure requires progress")
            return PaperReviewStatus(
                schema="paper_review_status.v1",
                request_id=request.request_id,
                snapshot_id=request.snapshot_id,
                trader_id=request.trader_id,
                status="failed",
                captured_at=request.captured_at,
                progress=error.progress,
                ready=None,
                error=error,
            )
        raise ValueError("review terminal event has an unknown status")

    def _preparing_event(
        self,
        row: sqlite3.Row,
        *,
        request: _RequestIdentity,
    ) -> PaperReviewProgress:
        if (
            row["request_id"] != request.request_id
            or row["ordinal"] != 1
            or row["status"] != "preparing"
            or row["occurred_at"] != request.captured_at
        ):
            raise ValueError("review preparing event identity is invalid")
        payload = _decode_canonical_json(row["payload_json"])
        expected = {
            "completed_parts": 0,
            "current_part": None,
            "total_parts": 10,
        }
        if payload != expected:
            raise ValueError("review preparing payload is not exact")
        return PaperReviewProgress.model_validate(payload, strict=True)

    def _ready_from_row(
        self,
        row: sqlite3.Row,
        *,
        request: _RequestRecord,
        event: sqlite3.Row,
        validate_artifact_relpath: bool = True,
    ) -> PaperReviewReady:
        if row["request_id"] != request.request_id:
            raise ValueError("ready row request identity differs")
        manifest = _decode_canonical_json(row["member_manifest_json"])
        if not isinstance(manifest, list):
            raise ValueError("ready member manifest must be an array")
        terminal_text = _human_text(
            row["terminal_opener_text"],
            label="persisted terminal opener",
        )
        terminal_bytes = terminal_text.encode("utf-8")
        ready = PaperReviewReady.model_validate(
            {
                "schema": "paper_review_ready.v1",
                "display_filename": row["display_filename"],
                "artifact_bytes": row["artifact_bytes"],
                "artifact_sha256": row["artifact_sha256"],
                "member_count": len(manifest),
                "members": manifest,
                "terminal_opener": {
                    "bytes": row["terminal_opener_bytes"],
                    "sha256": row["terminal_opener_sha256"],
                },
                "ready_at": row["ready_at"],
            },
            strict=True,
        )
        if (
            len(terminal_bytes) != row["terminal_opener_bytes"]
            or sha256(terminal_bytes).hexdigest()
            != row["terminal_opener_sha256"]
        ):
            raise ValueError("persisted terminal opener bytes or SHA differ")
        self._assert_ready_identity(ready, request=request)
        expected_event_payload = {
            "artifact_sha256": ready.artifact_sha256,
            "completed_parts": 10,
            "total_parts": 10,
        }
        if (
            event["request_id"] != request.request_id
            or event["occurred_at"] != ready.ready_at
            or _decode_canonical_json(event["payload_json"])
            != expected_event_payload
        ):
            raise ValueError("ready terminal event differs from ready metadata")
        if validate_artifact_relpath:
            self._validate_artifact_relpath(
                row["artifact_relpath"],
                artifact_sha256=ready.artifact_sha256,
            )
        return ready

    def _failure_from_row(
        self,
        row: sqlite3.Row,
        *,
        request: _RequestIdentity,
        event: sqlite3.Row,
    ) -> PaperReviewErrorPayload:
        if row["request_id"] != request.request_id:
            raise ValueError("failure row request identity differs")
        error_value = _decode_canonical_json(row["error_json"])
        error = PaperReviewErrorPayload.model_validate(error_value, strict=True)
        error_bytes = cast(str, row["error_json"]).encode("utf-8")
        if (
            row["http_status"] != _ERROR_HTTP_STATUS[error.code]
            or row["error_sha256"] != sha256(error_bytes).hexdigest()
            or error.request_id != request.request_id
            or error.snapshot_id != request.snapshot_id
            or error.progress is None
            or error.progress.current_part is not None
            or error.progress.completed_parts >= _TOTAL_PARTS
        ):
            raise ValueError("persisted failure metadata or identity differs")
        expected_event_payload = {
            "completed_parts": error.progress.completed_parts,
            "error_sha256": row["error_sha256"],
            "total_parts": 10,
        }
        if (
            event["request_id"] != request.request_id
            or event["occurred_at"] != row["failed_at"]
            or _decode_canonical_json(event["payload_json"])
            != expected_event_payload
        ):
            raise ValueError("failed terminal event differs from failure metadata")
        _canonical_utc(row["failed_at"])
        return error

    def _read_request_if_present(
        self,
        request_id: str,
    ) -> _RequestRecord | None:
        if not self._store.path.is_file():
            return None
        try:
            connection = self._store._connect_readonly()
        except PaperTraderStoreSchemaUpgradeRequiredError as exc:
            raise PaperReviewDomainError(
                "store_schema_upgrade_required",
                "paper trader store requires an explicit schema upgrade",
            ) from exc
        except PaperTraderStoreIntegrityError as exc:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "paper review store failed its exact schema guard",
            ) from exc
        try:
            row = connection.execute(
                """
                SELECT *
                FROM paper_review_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            return self._request_record(connection, row) if row is not None else None
        except PaperReviewDomainError:
            raise
        except (
            KeyError,
            PaperTraderStoreIntegrityError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "paper review request identity is malformed",
            ) from exc
        finally:
            connection.close()

    def _request_record(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> _RequestRecord:
        identity = self._request_identity(row)
        trader_row = connection.execute(
            "SELECT * FROM paper_traders WHERE trader_id = ?",
            (identity.trader_id,),
        ).fetchone()
        if trader_row is None:
            raise ValueError("review request trader does not exist")
        trader = self._store._record_from_row(connection, trader_row)
        origin = connection.execute(
            """
            SELECT ledger_origin_id
            FROM paper_ledger_origins
            WHERE trader_id = ?
            """,
            (identity.trader_id,),
        ).fetchall()
        if (
            len(origin) != 1
            or origin[0]["ledger_origin_id"] != identity.ledger_origin_id
        ):
            raise ValueError("review request ledger identity differs")
        return _RequestRecord(
            request_id=identity.request_id,
            snapshot_id=identity.snapshot_id,
            request_fingerprint=identity.request_fingerprint,
            trader_id=identity.trader_id,
            ledger_origin_id=identity.ledger_origin_id,
            captured_at=identity.captured_at,
            builder_instance_id=identity.builder_instance_id,
            created_at=identity.created_at,
            trader=trader,
        )

    @staticmethod
    def _request_identity(row: sqlite3.Row) -> _RequestIdentity:
        request_id = _canonical_uuid4(row["request_id"])
        snapshot_id = _canonical_snapshot_id(row["snapshot_id"])
        trader_id = _require_pattern(
            row["trader_id"],
            _TRADER_ID_PATTERN,
            label="trader ID",
        )
        ledger_origin_id = _require_pattern(
            row["ledger_origin_id"],
            _LEDGER_ID_PATTERN,
            label="ledger origin ID",
        )
        captured_at = _canonical_utc(row["captured_at"])
        created_at = _canonical_utc(row["created_at"])
        builder_instance_id = _canonical_uuid4(row["builder_instance_id"])
        fingerprint = _require_pattern(
            row["request_fingerprint"],
            _SHA256_PATTERN,
            label="request fingerprint",
        )
        if (
            created_at != captured_at
            or fingerprint
            != review_request_fingerprint(
                request_id=request_id,
                trader_id=trader_id,
            )
        ):
            raise ValueError("review request timestamp or fingerprint differs")
        return _RequestIdentity(
            request_id=request_id,
            snapshot_id=snapshot_id,
            request_fingerprint=fingerprint,
            trader_id=trader_id,
            ledger_origin_id=ledger_origin_id,
            captured_at=captured_at,
            builder_instance_id=builder_instance_id,
            created_at=created_at,
        )

    @staticmethod
    def _assert_request_intent(
        request: _RequestRecord,
        *,
        trader_id: str,
        ledger_origin_id: str,
        fingerprint: str,
    ) -> None:
        if (
            request.trader_id != trader_id
            or request.ledger_origin_id != ledger_origin_id
            or request.request_fingerprint != fingerprint
        ):
            raise PaperReviewDomainError(
                "request_id_conflict",
                "request ID is already bound to another review intent",
                request_id=request.request_id,
                snapshot_id=request.snapshot_id,
                progress=PaperReviewProgress(
                    completed_parts=0,
                    total_parts=10,
                    current_part=None,
                ),
            )

    def _connect_write_for_review(self) -> sqlite3.Connection:
        try:
            return self._store._connect_write()
        except PaperTraderStoreSchemaUpgradeRequiredError as exc:
            raise PaperReviewDomainError(
                "store_schema_upgrade_required",
                "paper trader store requires an explicit schema upgrade",
            ) from exc
        except PaperTraderStoreIntegrityError as exc:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "paper review store failed its exact schema guard",
            ) from exc

    def _call_step_hook(self, step: str) -> None:
        if self._step_hook is not None:
            self._step_hook(step)

    def _set_progress_for_test(
        self,
        request_id: str,
        *,
        completed_parts: int,
        current_part: str | None,
    ) -> None:
        request = self._read_request_if_present(_canonical_uuid4(request_id))
        if request is None:
            raise PaperReviewDomainError(
                "request_not_found",
                "paper review request does not exist",
                request_id=request_id,
            )
        progress = PaperReviewProgress(
            completed_parts=completed_parts,
            total_parts=10,
            current_part=current_part,
        )
        if (
            progress.current_part is not None
            and progress.current_part
            not in _review_member_paths(request.trader.baseline.run_id)
        ):
            raise ValueError("current review part is not an approved member path")
        self._builders.update(
            store_key=self._store_key,
            request_id=request.request_id,
            builder_instance_id=request.builder_instance_id,
            progress=progress,
        )

    def _deactivate_builder_for_test(self, request_id: str) -> None:
        self._builders.deactivate(
            store_key=self._store_key,
            request_id=_canonical_uuid4(request_id),
        )

    def _save_ready_artifact(
        self,
        *,
        request_id: str,
        artifact_relpath: str,
        ready: PaperReviewReady,
        terminal_opener_text: str,
        step_hook: Callable[[str], None] | None = None,
    ) -> None:
        """Persist the immutable ready row and event as one terminal pair."""
        canonical_request = _canonical_uuid4(request_id)
        if not isinstance(ready, PaperReviewReady):
            raise TypeError("ready must be a validated PaperReviewReady")
        terminal_text = _human_text(
            terminal_opener_text,
            label="terminal opener",
        )
        terminal_bytes = terminal_text.encode("utf-8")
        if (
            ready.terminal_opener.bytes != len(terminal_bytes)
            or ready.terminal_opener.sha256
            != sha256(terminal_bytes).hexdigest()
        ):
            raise ValueError("terminal opener claim differs from supplied text")
        self._validate_artifact_relpath(
            artifact_relpath,
            artifact_sha256=ready.artifact_sha256,
        )
        manifest_json = _canonical_json(
            [member.model_dump(mode="json") for member in ready.members]
        )
        connection = self._connect_write_for_review()
        try:
            connection.execute("BEGIN IMMEDIATE")
            request = self._terminal_request(connection, canonical_request)
            self._assert_ready_identity(ready, request=request)
            connection.execute(
                """
                INSERT INTO paper_review_ready_artifacts (
                    request_id, artifact_relpath, display_filename,
                    artifact_bytes, artifact_sha256, member_manifest_json,
                    terminal_opener_text, terminal_opener_bytes,
                    terminal_opener_sha256, ready_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical_request,
                    artifact_relpath,
                    ready.display_filename,
                    ready.artifact_bytes,
                    ready.artifact_sha256,
                    manifest_json,
                    terminal_text,
                    len(terminal_bytes),
                    sha256(terminal_bytes).hexdigest(),
                    ready.ready_at,
                ),
            )
            if step_hook is not None:
                step_hook("ready_row")
            connection.execute(
                """
                INSERT INTO paper_review_status_events (
                    request_id, ordinal, status, occurred_at, payload_json
                ) VALUES (?, 2, 'ready', ?, ?)
                """,
                (
                    canonical_request,
                    ready.ready_at,
                    _canonical_json(
                        {
                            "artifact_sha256": ready.artifact_sha256,
                            "completed_parts": 10,
                            "total_parts": 10,
                        }
                    ),
                ),
            )
            if step_hook is not None:
                step_hook("ready_event")
                step_hook("commit")
            connection.commit()
        except PaperReviewDomainError:
            connection.rollback()
            raise
        except (
            PaperTraderStoreIntegrityError,
            sqlite3.IntegrityError,
            ValueError,
        ) as exc:
            connection.rollback()
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "review request cannot accept a ready terminal pair",
                request_id=canonical_request,
            ) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _save_artifact_failure(
        self,
        *,
        request_id: str,
        error: PaperReviewErrorPayload,
        failed_at: str,
    ) -> None:
        """Persist one classified artifact failure without rereading its source."""
        canonical_request = _canonical_uuid4(request_id)
        canonical_failed_at = _canonical_utc(failed_at)
        if not isinstance(error, PaperReviewErrorPayload):
            raise TypeError("error must be a validated PaperReviewErrorPayload")
        if (
            error.request_id != canonical_request
            or error.snapshot_id is None
            or error.progress is None
            or error.progress.current_part is not None
            or error.progress.completed_parts >= _TOTAL_PARTS
        ):
            raise ValueError("terminal artifact failure identity is invalid")
        error_json = _canonical_json(
            error.model_dump(by_alias=True, mode="json")
        )
        error_sha = sha256(error_json.encode("utf-8")).hexdigest()
        connection = self._connect_write_for_review()
        try:
            connection.execute("BEGIN IMMEDIATE")
            request = self._terminal_request_identity(
                connection,
                canonical_request,
            )
            if error.snapshot_id != request.snapshot_id:
                raise ValueError("failure snapshot identity differs from request")
            connection.execute(
                """
                INSERT INTO paper_review_failures (
                    request_id, http_status, error_json, error_sha256, failed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    canonical_request,
                    _ERROR_HTTP_STATUS[error.code],
                    error_json,
                    error_sha,
                    canonical_failed_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO paper_review_status_events (
                    request_id, ordinal, status, occurred_at, payload_json
                ) VALUES (?, 2, 'failed', ?, ?)
                """,
                (
                    canonical_request,
                    canonical_failed_at,
                    _canonical_json(
                        {
                            "completed_parts": error.progress.completed_parts,
                            "error_sha256": error_sha,
                            "total_parts": 10,
                        }
                    ),
                ),
            )
            connection.commit()
        except PaperReviewDomainError:
            connection.rollback()
            raise
        except (sqlite3.IntegrityError, ValueError) as exc:
            connection.rollback()
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "review request cannot accept a failed terminal pair",
                request_id=canonical_request,
            ) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _save_ready_for_test(
        self,
        *,
        request_id: str,
        artifact_relpath: str,
        ready_payload: object,
        terminal_opener_text: str,
    ) -> None:
        canonical_request = _canonical_uuid4(request_id)
        ready = self._ready_from_payload(ready_payload)
        terminal_text = _human_text(
            terminal_opener_text,
            label="terminal opener",
        )
        terminal_bytes = terminal_text.encode("utf-8")
        if (
            ready.terminal_opener.bytes != len(terminal_bytes)
            or ready.terminal_opener.sha256
            != sha256(terminal_bytes).hexdigest()
        ):
            raise ValueError("terminal opener claim differs from supplied text")
        self._validate_artifact_relpath(
            artifact_relpath,
            artifact_sha256=ready.artifact_sha256,
        )
        connection = self._connect_write_for_review()
        try:
            connection.execute("BEGIN IMMEDIATE")
            request = self._terminal_request(connection, canonical_request)
            self._assert_ready_identity(ready, request=request)
            manifest_json = _canonical_json(
                [
                    member.model_dump(mode="json")
                    for member in ready.members
                ]
            )
            connection.execute(
                """
                INSERT INTO paper_review_ready_artifacts (
                    request_id, artifact_relpath, display_filename,
                    artifact_bytes, artifact_sha256, member_manifest_json,
                    terminal_opener_text, terminal_opener_bytes,
                    terminal_opener_sha256, ready_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical_request,
                    artifact_relpath,
                    ready.display_filename,
                    ready.artifact_bytes,
                    ready.artifact_sha256,
                    manifest_json,
                    terminal_text,
                    len(terminal_bytes),
                    sha256(terminal_bytes).hexdigest(),
                    ready.ready_at,
                ),
            )
            self._call_step_hook("ready_artifact")
            connection.execute(
                """
                INSERT INTO paper_review_status_events (
                    request_id, ordinal, status, occurred_at, payload_json
                ) VALUES (?, 2, 'ready', ?, ?)
                """,
                (
                    canonical_request,
                    ready.ready_at,
                    _canonical_json(
                        {
                            "artifact_sha256": ready.artifact_sha256,
                            "completed_parts": 10,
                            "total_parts": 10,
                        }
                    ),
                ),
            )
            self._call_step_hook("ready_event")
            connection.commit()
        except PaperReviewDomainError:
            connection.rollback()
            raise
        except (sqlite3.IntegrityError, ValueError) as exc:
            connection.rollback()
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "review request cannot accept a ready terminal pair",
            ) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self._builders.deactivate(
            store_key=self._store_key,
            request_id=canonical_request,
        )

    def _save_failure_for_test(
        self,
        *,
        request_id: str,
        http_status: int,
        error_payload: object,
    ) -> None:
        canonical_request = _canonical_uuid4(request_id)
        error = PaperReviewErrorPayload.model_validate(
            error_payload,
            strict=True,
        )
        if (
            error.request_id != canonical_request
            or error.snapshot_id is None
            or error.progress is None
            or error.progress.current_part is not None
            or error.progress.completed_parts >= _TOTAL_PARTS
            or _ERROR_HTTP_STATUS[error.code] != http_status
        ):
            raise ValueError("terminal failure identity, status, or progress is invalid")
        error_json = _canonical_json(
            error.model_dump(by_alias=True, mode="json")
        )
        error_sha = sha256(error_json.encode("utf-8")).hexdigest()
        failed_at = _timestamp_text(self._clock())
        _canonical_utc(failed_at)
        connection = self._connect_write_for_review()
        try:
            connection.execute("BEGIN IMMEDIATE")
            request = self._terminal_request(connection, canonical_request)
            if error.snapshot_id != request.snapshot_id:
                raise ValueError("failure snapshot identity differs from request")
            connection.execute(
                """
                INSERT INTO paper_review_failures (
                    request_id, http_status, error_json, error_sha256, failed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    canonical_request,
                    http_status,
                    error_json,
                    error_sha,
                    failed_at,
                ),
            )
            self._call_step_hook("failure_row")
            connection.execute(
                """
                INSERT INTO paper_review_status_events (
                    request_id, ordinal, status, occurred_at, payload_json
                ) VALUES (?, 2, 'failed', ?, ?)
                """,
                (
                    canonical_request,
                    failed_at,
                    _canonical_json(
                        {
                            "completed_parts": error.progress.completed_parts,
                            "error_sha256": error_sha,
                            "total_parts": 10,
                        }
                    ),
                ),
            )
            self._call_step_hook("failed_event")
            connection.commit()
        except PaperReviewDomainError:
            connection.rollback()
            raise
        except (sqlite3.IntegrityError, ValueError) as exc:
            connection.rollback()
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "review request cannot accept a failed terminal pair",
            ) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self._builders.deactivate(
            store_key=self._store_key,
            request_id=canonical_request,
        )

    def _terminal_request(
        self,
        connection: sqlite3.Connection,
        request_id: str,
    ) -> _RequestRecord:
        row = connection.execute(
            """
            SELECT *
            FROM paper_review_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise PaperReviewDomainError(
                "request_not_found",
                "paper review request does not exist",
            )
        request = self._request_record(connection, row)
        events = connection.execute(
            """
            SELECT ordinal, status, payload_json
            FROM paper_review_status_events
            WHERE request_id = ?
            ORDER BY ordinal
            """,
            (request_id,),
        ).fetchall()
        ready_count = connection.execute(
            """
            SELECT count(*)
            FROM paper_review_ready_artifacts
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()[0]
        failure_count = connection.execute(
            """
            SELECT count(*)
            FROM paper_review_failures
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()[0]
        if (
            len(events) != 1
            or events[0]["ordinal"] != 1
            or events[0]["status"] != "preparing"
            or ready_count != 0
            or failure_count != 0
        ):
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "review request is already terminal or malformed",
            )
        self._preparing_event(
            connection.execute(
                """
                SELECT *
                FROM paper_review_status_events
                WHERE request_id = ? AND ordinal = 1
                """,
                (request_id,),
            ).fetchone(),
            request=request,
        )
        return request

    def _terminal_request_identity(
        self,
        connection: sqlite3.Connection,
        request_id: str,
    ) -> _RequestIdentity:
        row = connection.execute(
            """
            SELECT *
            FROM paper_review_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise PaperReviewDomainError(
                "request_not_found",
                "paper review request does not exist",
                request_id=request_id,
            )
        request = self._request_identity(row)
        events = connection.execute(
            """
            SELECT *
            FROM paper_review_status_events
            WHERE request_id = ?
            ORDER BY ordinal
            """,
            (request_id,),
        ).fetchall()
        ready_count = connection.execute(
            """
            SELECT count(*)
            FROM paper_review_ready_artifacts
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()[0]
        failure_count = connection.execute(
            """
            SELECT count(*)
            FROM paper_review_failures
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()[0]
        if len(events) != 1 or ready_count != 0 or failure_count != 0:
            raise PaperReviewDomainError(
                "snapshot_integrity_failed",
                "review request is already terminal or malformed",
                request_id=request_id,
                snapshot_id=request.snapshot_id,
            )
        self._preparing_event(events[0], request=request)
        return request

    @staticmethod
    def _ready_from_payload(value: object) -> PaperReviewReady:
        return PaperReviewReady.model_validate(value, strict=True)

    @staticmethod
    def _assert_ready_identity(
        ready: PaperReviewReady,
        *,
        request: _RequestRecord,
    ) -> None:
        expected_paths = _review_member_paths(request.trader.baseline.run_id)
        if tuple(member.path for member in ready.members) != expected_paths:
            raise ValueError("ready manifest paths or order differ")
        captured = datetime.fromisoformat(
            request.captured_at[:-1] + "+00:00"
        )
        compact = captured.strftime("%Y%m%dT%H%M%S") + (
            f"{captured.microsecond:06d}Z"
        )
        expected_filename = (
            f"paper-review-{request.trader_id}-{compact}.zip"
        )
        if ready.display_filename != expected_filename:
            raise ValueError("ready display filename differs from frozen identity")

    @staticmethod
    def _validate_artifact_relpath(
        value: object,
        *,
        artifact_sha256: str,
    ) -> str:
        relpath = _require_pattern(
            value,
            _ARTIFACT_RELPATH_PATTERN,
            label="artifact relative path",
        )
        match = _ARTIFACT_RELPATH_PATTERN.fullmatch(relpath)
        if (
            match is None
            or match.group(1) != artifact_sha256[:2]
            or match.group(2) != artifact_sha256
        ):
            raise ValueError("artifact relative path differs from artifact SHA")
        return relpath
