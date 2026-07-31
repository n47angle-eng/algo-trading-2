"""Immutable run artifacts, record-layer tags, and data-provenance helpers.

This module deliberately runs after strategy/execution work.  It has no per-bar
callback responsibilities: it prepares the immutable A4 manifest, converts
completed execution trades into A5 record-layer facts, and builds the data that
the SQLite repository and ``result.v1`` exporter persist.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from math import isclose, isfinite
from typing import Final, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from futures_research.backtest.evidence import (
    EvidenceSummary,
    RejectionEvidence,
    TradeDecisionEvidence,
    build_evidence_summary,
    build_trade_decision_evidence,
    validate_evidence_bundle,
)
from futures_research.backtest.execution import ExecutionTrade, ExitReason
from futures_research.backtest.strategy import (
    Direction,
    SignalKind,
    StrategyEvent,
    StrategyEventType,
)
from futures_research.data.contracts import ContractSpec, ExecutionCostSpec
from futures_research.data.models import CanonicalBar

_ONE_MINUTE: Final = timedelta(minutes=1)
_RUN_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class DataFingerprint(BaseModel):
    """Stable hash of the canonical bars actually admitted to a run."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal["canonical-bars.v1"] = Field(
        default="canonical-bars.v1",
        alias="schema",
        serialization_alias="schema",
    )
    algorithm: Literal["sha256"] = "sha256"
    digest: str = Field(min_length=64, max_length=64)
    bar_count: int = Field(ge=0)
    first_timestamp: datetime | None = None
    end_timestamp: datetime | None = None
    source_partitions: tuple[str, ...] = ()
    quality_report_ids: tuple[str, ...] = ()

    @field_validator("digest")
    @classmethod
    def require_hex_digest(cls, value: str) -> str:
        """Reject anything other than a complete lowercase SHA-256 digest."""
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            msg = "digest must be a lowercase 64-character SHA-256 hex string"
            raise ValueError(msg)
        return value

    @field_validator("first_timestamp", "end_timestamp")
    @classmethod
    def normalize_optional_timestamp(cls, value: datetime | None) -> datetime | None:
        """Keep optional fingerprint bounds in the canonical UTC policy."""
        return _as_utc(value, field_name="fingerprint timestamp") if value is not None else None

    @field_validator("source_partitions", "quality_report_ids")
    @classmethod
    def require_sorted_unique_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep optional A1 provenance references deterministic and non-empty."""
        if any(not reference.strip() for reference in value):
            msg = "provenance references must not be blank"
            raise ValueError(msg)
        if tuple(sorted(set(value))) != value:
            msg = "provenance references must be sorted and unique"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        """Require bounds to match whether the digest contains any bars."""
        if self.bar_count == 0 and (
            self.first_timestamp is not None or self.end_timestamp is not None
        ):
            msg = "an empty data fingerprint must not contain timestamp bounds"
            raise ValueError(msg)
        if self.bar_count > 0 and (self.first_timestamp is None or self.end_timestamp is None):
            msg = "a non-empty data fingerprint requires timestamp bounds"
            raise ValueError(msg)
        if (
            self.first_timestamp is not None
            and self.end_timestamp is not None
            and self.first_timestamp >= self.end_timestamp
        ):
            msg = "fingerprint first_timestamp must precede end_timestamp"
            raise ValueError(msg)
        return self


class CalibrationProvenance(BaseModel):
    """Immutable evidence for the pre-run history that selected regime thresholds."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal["calibration.v1"] = Field(
        default="calibration.v1",
        alias="schema",
        serialization_alias="schema",
    )
    source: Literal["pre_run_canonical_bars"] = "pre_run_canonical_bars"
    daily_source: Literal["ib_native_daily"] = "ib_native_daily"
    window_start: datetime | None = None
    window_end: datetime
    data_fingerprint: DataFingerprint
    daily_bar_count: int = Field(ge=0)
    ready_daily_bar_count: int = Field(ge=0)
    sample_size: int = Field(ge=0)
    status: Literal["calibrated", "unavailable"]
    separation_threshold: float | None = None
    slope_threshold: float | None = None

    @field_validator("window_start", "window_end")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        """Keep calibration bounds in the canonical UTC convention."""
        return _as_utc(value, field_name="calibration timestamp") if value is not None else None

    @field_validator("separation_threshold", "slope_threshold")
    @classmethod
    def require_finite_optional_threshold(cls, value: float | None) -> float | None:
        """Refuse non-finite threshold values in reproducibility metadata."""
        if value is not None:
            _require_finite(value, field_name="calibration threshold")
        return value

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        """Ensure each status has a complete, non-ambiguous audit shape."""
        if self.ready_daily_bar_count > self.daily_bar_count:
            msg = "ready_daily_bar_count cannot exceed daily_bar_count"
            raise ValueError(msg)
        if self.data_fingerprint.bar_count == 0 and self.window_start is not None:
            msg = "an empty calibration fingerprint must not have a window_start"
            raise ValueError(msg)
        if self.data_fingerprint.bar_count > 0 and (
            self.window_start is None or self.window_start >= self.window_end
        ):
            msg = "non-empty calibration provenance needs a valid half-open window"
            raise ValueError(msg)
        if self.status == "calibrated":
            if (
                self.sample_size == 0
                or self.separation_threshold is None
                or self.slope_threshold is None
            ):
                msg = "a calibrated provenance record requires samples and both thresholds"
                raise ValueError(msg)
        elif (
            self.sample_size != 0
            or self.separation_threshold is not None
            or self.slope_threshold is not None
        ):
            msg = "an unavailable calibration provenance record must not expose thresholds"
            raise ValueError(msg)
        return self


class StrategyParamOverride(BaseModel):
    """One strategy-file parameter deliberately overridden for a validation run.

    Recorded so a run that deviates from its strategy document can never look
    like one that did not (WO-006 / 6-5, channel [083] Q1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    spec_value: str
    applied_value: str

    @field_validator("path", "spec_value", "applied_value")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        """An override entry without all three parts is not auditable."""
        cleaned = value.strip()
        if not cleaned:
            msg = "override path/spec_value/applied_value must not be empty"
            raise ValueError(msg)
        return cleaned


class StrategyBinding(BaseModel):
    """How the replayed ``StrategySpec`` was obtained for this run.

    ``strategy_file`` means a confirmed A2 StrategyVersion drove the replay;
    ``engine_default`` means the run used operator knobs with no strategy
    document bound (the pre-6-5 path, kept for CLI/legacy submissions).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal["strategy_binding.v1"] = Field(
        default="strategy_binding.v1",
        alias="schema",
        serialization_alias="schema",
    )
    source: Literal["strategy_file", "engine_default"]
    strategy_id: str | None = None
    strategy_name: str | None = None
    content_sha256: str | None = None
    universe_contracts: tuple[str, ...] = ()
    universe_authorized: bool = True
    overrides: tuple[StrategyParamOverride, ...] = ()

    @field_validator("content_sha256")
    @classmethod
    def require_hex_digest(cls, value: str | None) -> str | None:
        """Keep the strategy-document digest in the same format as data digests."""
        if value is None:
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            msg = "content_sha256 must be a lowercase 64-character SHA-256 hex string"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def require_consistent_source(self) -> Self:
        """A file-sourced binding must identify the document it came from."""
        if self.source == "strategy_file":
            if not self.strategy_id or not self.content_sha256:
                msg = "a strategy_file binding requires strategy_id and content_sha256"
                raise ValueError(msg)
        elif self.strategy_id is not None or self.overrides:
            msg = "an engine_default binding must not claim a strategy id or overrides"
            raise ValueError(msg)
        return self


class RunManifest(BaseModel):
    """Immutable A4 definition of one auditable backtest run."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal["run_manifest.v1"] = Field(
        default="run_manifest.v1",
        alias="schema",
        serialization_alias="schema",
    )
    run_id: str
    strategy_version: str
    contract_id: str
    session_name: str
    range_start: datetime
    range_end: datetime
    initial_capital: float = Field(gt=0)
    quantity: int = Field(gt=0)
    costs: ExecutionCostSpec
    fill_model: Literal["conservative"] = "conservative"
    simulation_precision: Literal["one_minute"] = "one_minute"
    data_fingerprint: DataFingerprint
    calibration: CalibrationProvenance | None = None
    strategy_binding: StrategyBinding | None = None
    quality_gate_mode: Literal["enforce", "disabled"] = "enforce"
    validation_run: bool = False
    excluded_trading_dates: tuple[date, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("run_id")
    @classmethod
    def require_safe_run_id(cls, value: str) -> str:
        """Keep the database key and result-file stem unambiguous and path-safe."""
        if not _RUN_ID_PATTERN.fullmatch(value):
            msg = "run_id must contain only letters, digits, underscores, and hyphens"
            raise ValueError(msg)
        return value

    @field_validator("strategy_version", "contract_id", "session_name")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        """Reject blank A4 identity fields before persistence."""
        normalized = value.strip()
        if not normalized:
            msg = "manifest identity fields must not be empty"
            raise ValueError(msg)
        return normalized

    @field_validator("range_start", "range_end", "created_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Normalize all artifact timestamps to UTC."""
        return _as_utc(value, field_name="manifest timestamp")

    @field_validator("initial_capital")
    @classmethod
    def require_finite_capital(cls, value: float) -> float:
        """Avoid serializing NaN/Infinity into a financial run definition."""
        _require_finite(value, field_name="initial_capital")
        return value

    @field_validator("excluded_trading_dates")
    @classmethod
    def require_sorted_unique_dates(cls, value: tuple[date, ...]) -> tuple[date, ...]:
        """Ensure the manifested blackout gate has one deterministic representation."""
        if tuple(sorted(set(value))) != value:
            msg = "excluded_trading_dates must be sorted and unique"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        """Keep the requested run interval half-open and non-empty."""
        if self.range_start >= self.range_end:
            msg = "range_start must be earlier than range_end"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def restrict_strategy_deviations_to_validation_runs(self) -> Self:
        """Deviating from the strategy document is engineering-only (channel [083]).

        Parameter overrides and running a contract the document does not authorize
        are both permitted, but only on a run that is already labelled
        ``validation_run`` — otherwise a deviating run could be read as a
        strategy claim.
        """
        binding = self.strategy_binding
        if binding is None or self.validation_run:
            return self
        if binding.overrides:
            msg = "strategy parameter overrides require validation_run=True"
            raise ValueError(msg)
        if not binding.universe_authorized:
            msg = "running a contract outside universe.contracts requires validation_run=True"
            raise ValueError(msg)
        return self


class RecordTagInputs(BaseModel):
    """Optional daily context supplied by later strategy/plan integrations.

    Every field is present from day one.  ``None`` deliberately means the source
    was not yet available, rather than silently omitting a record-layer column.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    daily_regime: Literal["trend", "range", "congestion", "unavailable"] = "trend"
    regime_strength: Literal["strong", "weak"] | None = None
    entry_layers: int = Field(default=3, ge=2, le=3)
    has_sweep_bonus: bool | None = None
    lmr_step1_leg_atr: float | None = None
    lmr_step2_leg_atr: float | None = None
    atr_expansion_ratio: float | None = None
    volatility_owner_view: Literal["high", "normal", "low"] | None = None
    volatility_system_daily_atr_percentile: float | None = None
    volatility_system_range_ratio: float | None = None

    @field_validator(
        "lmr_step1_leg_atr",
        "lmr_step2_leg_atr",
        "atr_expansion_ratio",
        "volatility_system_daily_atr_percentile",
        "volatility_system_range_ratio",
    )
    @classmethod
    def require_optional_finite_numbers(cls, value: float | None) -> float | None:
        """Keep optional numerical tags serializable and mathematically meaningful."""
        if value is not None:
            _require_finite(value, field_name="record tag")
        return value


class TradeTags(BaseModel):
    """Full P1 record-layer surface for one completed trade."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_kind: SignalKind
    daily_regime: Literal["trend", "range", "congestion", "unavailable"]
    regime_strength: Literal["strong", "weak"] | None = None
    entry_session: str
    entry_local_time: str
    entry_layers: int = Field(ge=2, le=3)
    inside_count: int | None = Field(default=None, ge=1)
    multiple_inside: bool
    has_sweep_bonus: bool | None = None
    lmr_step1_leg_atr: float | None = None
    lmr_step2_leg_atr: float | None = None
    atr_expansion_ratio: float | None = None
    mfe_r: float = Field(ge=0)
    mae_r: float = Field(ge=0)
    gap_through_target: bool
    volatility_owner_view: Literal["high", "normal", "low"] | None = None
    volatility_system_daily_atr_percentile: float | None = None
    volatility_system_range_ratio: float | None = None
    volatility_actual_daily_range: float | None = Field(default=None, ge=0)

    @field_validator(
        "lmr_step1_leg_atr",
        "lmr_step2_leg_atr",
        "atr_expansion_ratio",
        "mfe_r",
        "mae_r",
        "volatility_system_daily_atr_percentile",
        "volatility_system_range_ratio",
        "volatility_actual_daily_range",
    )
    @classmethod
    def require_finite_tag_numbers(cls, value: float | None) -> float | None:
        """Reject non-finite numbers before a tag becomes a durable experiment fact."""
        if value is not None:
            _require_finite(value, field_name="trade tag")
        return value


class RunMetrics(BaseModel):
    """Compact P1 metrics calculated from immutable completed trade records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_count: int = Field(ge=0)
    gross_pnl: float
    net_pnl: float
    net_r: float
    win_rate: float | None = None
    profit_factor: float | None = None
    expectancy_r: float | None = None
    max_drawdown_pnl: float = Field(ge=0)

    @field_validator(
        "gross_pnl",
        "net_pnl",
        "net_r",
        "win_rate",
        "profit_factor",
        "expectancy_r",
        "max_drawdown_pnl",
    )
    @classmethod
    def require_finite_metrics(cls, value: float | None) -> float | None:
        """Preserve strict JSON numeric semantics for result summaries."""
        if value is not None:
            _require_finite(value, field_name="run metric")
        return value


@dataclass(frozen=True, slots=True)
class RealizedTradeMetrics:
    """Cumulative closed-trade facts shared by final results and day progress."""

    trade_count: int
    net_pnl: float
    net_r: float


class EngineMetadata(BaseModel):
    """Pinned engine metadata carried into a result artifact for reproducibility."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nautilus: str = "1.230.0"
    app: str = "0.1.0"


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One post-trade point in the immutable result-equity sidecar."""

    timestamp: datetime
    equity: float
    cumulative_net_pnl: float

    def __post_init__(self) -> None:
        """Normalize chart timestamps and reject invalid monetary values."""
        object.__setattr__(
            self, "timestamp", _as_utc(self.timestamp, field_name="equity timestamp")
        )
        _require_finite(self.equity, field_name="equity")
        _require_finite(self.cumulative_net_pnl, field_name="cumulative_net_pnl")

    def to_dict(self) -> dict[str, object]:
        """Return the JSON shape consumed by the future P5 equity chart."""
        return {
            "timestamp": _iso(self.timestamp),
            "equity": self.equity,
            "cumulative_net_pnl": self.cumulative_net_pnl,
        }


@dataclass(frozen=True, slots=True)
class RecordedTrade:
    """One immutable execution trade paired with all P1 record-layer tags."""

    trade_id: str
    execution: ExecutionTrade
    tags: TradeTags
    decision_evidence: TradeDecisionEvidence | None = None

    def __post_init__(self) -> None:
        """Keep the durable per-run trade identifier file-safe and deterministic."""
        if not _RUN_ID_PATTERN.fullmatch(self.trade_id):
            msg = "trade_id must contain only letters, digits, underscores, and hyphens"
            raise ValueError(msg)
        if self.decision_evidence is not None and (
            self.decision_evidence.trade_id != self.trade_id
        ):
            msg = "decision evidence trade_id must exactly match its recorded trade"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, object]:
        """Return a flat, UI-ready record without asking consumers to know internals."""
        document: dict[str, object] = {
            "trade_id": self.trade_id,
            **execution_trade_to_dict(self.execution),
            "tags": self.tags.model_dump(mode="json"),
        }
        if self.decision_evidence is not None:
            document["decision_evidence"] = self.decision_evidence.to_dict()
        return document


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """A4 manifest plus the canonical bars that passed the blackout gate."""

    manifest: RunManifest
    bars: tuple[CanonicalBar, ...]


@dataclass(frozen=True, slots=True)
class RunResult:
    """Immutable A5 outcome, ready for SQLite and ``result.v1`` persistence."""

    manifest: RunManifest
    trade_records: tuple[RecordedTrade, ...]
    event_log: tuple[StrategyEvent, ...]
    metrics: RunMetrics
    equity_curve: tuple[EquityPoint, ...]
    warnings: tuple[str, ...]
    engine: EngineMetadata
    completed_at: datetime
    rejection_evidence: tuple[RejectionEvidence, ...] = ()
    evidence_summary: EvidenceSummary | None = None
    evidence_complete: bool = False

    def __post_init__(self) -> None:
        """Normalize the completion instant before it becomes part of A5/A6."""
        object.__setattr__(
            self, "completed_at", _as_utc(self.completed_at, field_name="completed_at")
        )
        if self.evidence_complete:
            if self.evidence_summary is None:
                msg = "complete run evidence requires a deterministic evidence_summary"
                raise ValueError(msg)
            for ordinal, record in enumerate(self.trade_records, start=1):
                evidence = record.decision_evidence
                if evidence is None:
                    msg = "complete run evidence requires one decision record per trade"
                    raise ValueError(msg)
                if evidence.ordinal != ordinal:
                    msg = "decision evidence ordinal must match the recorded trade ordinal"
                    raise ValueError(msg)
            trade_evidence = tuple(
                record.decision_evidence for record in self.trade_records
            )
            assert all(item is not None for item in trade_evidence)
            validate_evidence_bundle(
                events=self.event_log,
                rejection_evidence=self.rejection_evidence,
                evidence_summary=self.evidence_summary,
                trade_evidence=(item for item in trade_evidence if item is not None),
                expected_trade_count=len(self.trade_records),
                complete=True,
            )
        elif self.rejection_evidence or self.evidence_summary is not None:
            msg = "incomplete run evidence must not claim reconstructed records or a summary"
            raise ValueError(msg)

    @property
    def run_id(self) -> str:
        """Expose the immutable run identity without duplicating it in A5."""
        return self.manifest.run_id

    def storage_document(self) -> dict[str, object]:
        """Return durable non-trade A5 state stored in the SQLite runs row."""
        document: dict[str, object] = {
            "schema": "run_result.v1",
            "run_id": self.run_id,
            "metrics": self.metrics.model_dump(mode="json"),
            "warnings": list(self.warnings),
            "events": [strategy_event_to_dict(event) for event in self.event_log],
            "equity_curve": [point.to_dict() for point in self.equity_curve],
            "engine": self.engine.model_dump(mode="json"),
            "completed_at": _iso(self.completed_at),
        }
        if self.evidence_complete:
            assert self.evidence_summary is not None
            document.update(
                {
                    "evidence_complete": True,
                    "evidence_summary": self.evidence_summary.to_dict(),
                    "rejection_evidence": [
                        record.to_dict() for record in self.rejection_evidence
                    ],
                }
            )
        return document

    def result_document(
        self,
        *,
        trades_ref: str,
        equity_curve_ref: str,
        events_ref: str,
    ) -> dict[str, object]:
        """Build the compact ``result.v1`` main file with sidecar references."""
        document: dict[str, object] = {
            "schema": "result.v1",
            "run": {
                "run_id": self.run_id,
                "strategy_version": self.manifest.strategy_version,
                "manifest": self.manifest.model_dump(mode="json", by_alias=True),
                "engine": self.engine.model_dump(mode="json"),
            },
            "metrics": self.metrics.model_dump(mode="json"),
            "scorecard": [],
            "trades_ref": trades_ref,
            "equity_curve_ref": equity_curve_ref,
            "events_ref": events_ref,
            "warnings": list(self.warnings),
            "owner_action": None,
        }
        if self.evidence_complete:
            document["decision_evidence_complete"] = True
        return document


def prepare_run(
    *,
    run_id: str,
    strategy_version: str,
    contract: ContractSpec,
    session_name: str,
    range_start: datetime,
    range_end: datetime,
    initial_capital: float,
    quantity: int,
    canonical_bars: Iterable[CanonicalBar],
    costs: ExecutionCostSpec | None = None,
    source_partitions: Iterable[str] = (),
    quality_report_ids: Iterable[str] = (),
    calibration: CalibrationProvenance | None = None,
    strategy_binding: StrategyBinding | None = None,
    quality_gate_mode: Literal["enforce", "disabled"] = "enforce",
    validation_run: bool = False,
    additional_excluded_trading_dates: Iterable[date] = (),
    created_at: datetime | None = None,
) -> PreparedRun:
    """Apply the A4 blackout gate and return an immutable manifest plus admitted bars."""
    if session_name not in contract.sessions:
        msg = f"unknown session '{session_name}' for {contract.contract_id}"
        raise KeyError(msg)
    start = _as_utc(range_start, field_name="range_start")
    end = _as_utc(range_end, field_name="range_end")
    if start >= end:
        msg = "range_start must be earlier than range_end"
        raise ValueError(msg)
    materialized = tuple(sorted(canonical_bars, key=lambda bar: bar.timestamp))
    _validate_run_bars(materialized, contract=contract, start=start, end=end)
    admitted, excluded_dates = exclude_roll_blackout_bars(
        materialized,
        contract=contract,
        session_name=session_name,
        additional_excluded_trading_dates=additional_excluded_trading_dates,
    )
    manifest = RunManifest(
        run_id=run_id,
        strategy_version=strategy_version,
        contract_id=contract.contract_id,
        session_name=session_name,
        range_start=start,
        range_end=end,
        initial_capital=initial_capital,
        quantity=quantity,
        costs=contract.execution_costs if costs is None else costs,
        data_fingerprint=fingerprint_canonical_bars(
            admitted,
            source_partitions=source_partitions,
            quality_report_ids=quality_report_ids,
        ),
        calibration=calibration,
        strategy_binding=strategy_binding,
        quality_gate_mode=quality_gate_mode,
        validation_run=validation_run,
        excluded_trading_dates=excluded_dates,
        created_at=datetime.now(UTC) if created_at is None else created_at,
    )
    return PreparedRun(manifest=manifest, bars=admitted)


def exclude_roll_blackout_bars(
    bars: Iterable[CanonicalBar],
    *,
    contract: ContractSpec,
    session_name: str,
    additional_excluded_trading_dates: Iterable[date] = (),
) -> tuple[tuple[CanonicalBar, ...], tuple[date, ...]]:
    """Remove configured/Owner dates without altering stored canonical data."""
    admitted: list[CanonicalBar] = []
    explicitly_excluded = set(additional_excluded_trading_dates)
    excluded_dates: set[date] = set(explicitly_excluded)
    for bar in bars:
        if bar.contract_id != contract.contract_id:
            msg = f"bar contract mismatch: expected {contract.contract_id}, got {bar.contract_id}"
            raise ValueError(msg)
        trading_date = trading_date_for_bar(bar, contract=contract, session_name=session_name)
        if contract.is_roll_blackout(trading_date) or trading_date in explicitly_excluded:
            excluded_dates.add(trading_date)
            continue
        admitted.append(bar)
    return tuple(admitted), tuple(sorted(excluded_dates))


def trading_date_for_bar(
    bar: CanonicalBar,
    *,
    contract: ContractSpec,
    session_name: str,
) -> date:
    """Map a canonical bar to its exchange trading date, including overnight ETH starts."""
    return _trading_date_for_timestamp(
        bar.timestamp,
        contract=contract,
        session_name=session_name,
    )


def consumed_trading_dates_for_bars(
    bars: Iterable[CanonicalBar],
    *,
    contract: ContractSpec,
    session_name: str,
) -> tuple[date, ...]:
    """Return sorted unique exchange labels for bars that passed the run's gates.

    The caller must pass already-admitted bars.  This intentionally never derives
    labels from a UTC range, so weekends, holidays, blackout dates, and missing
    sessions cannot become plausible-looking fabricated references.
    """
    return tuple(
        sorted(
            {
                trading_date_for_bar(bar, contract=contract, session_name=session_name)
                for bar in bars
            }
        )
    )


def fingerprint_canonical_bars(
    bars: Iterable[CanonicalBar],
    *,
    source_partitions: Iterable[str] = (),
    quality_report_ids: Iterable[str] = (),
) -> DataFingerprint:
    """Hash canonical market facts deterministically without including ingest-time metadata."""
    materialized = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    digest = sha256()
    previous_timestamp: datetime | None = None
    for bar in materialized:
        timestamp = _as_utc(bar.timestamp, field_name="bar.timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            msg = "fingerprinted bars must have strictly increasing timestamps"
            raise ValueError(msg)
        payload = {
            "timestamp": _iso(timestamp),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "contract_id": bar.contract_id,
            "source": bar.source,
            "source_request_id": bar.source_request_id,
        }
        digest.update(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        digest.update(b"\n")
        previous_timestamp = timestamp
    if not materialized:
        return DataFingerprint(
            digest=digest.hexdigest(),
            bar_count=0,
            source_partitions=_sorted_references(source_partitions),
            quality_report_ids=_sorted_references(quality_report_ids),
        )
    first = materialized[0].timestamp
    last_end = materialized[-1].timestamp + _ONE_MINUTE
    return DataFingerprint(
        digest=digest.hexdigest(),
        bar_count=len(materialized),
        first_timestamp=first,
        end_timestamp=last_end,
        source_partitions=_sorted_references(source_partitions),
        quality_report_ids=_sorted_references(quality_report_ids),
    )


def build_trade_records(
    *,
    run_id: str,
    trades: Sequence[ExecutionTrade],
    events: Sequence[StrategyEvent],
    canonical_bars: Sequence[CanonicalBar],
    contract: ContractSpec,
    session_name: str,
    default_inputs: RecordTagInputs | None = None,
    inputs_by_trading_date: Mapping[date, RecordTagInputs] | None = None,
    include_decision_evidence: bool = False,
    trade_id_prefix: str | None = None,
) -> tuple[RecordedTrade, ...]:
    """Attach every available D14 tag while retaining explicit nulls for future sources."""
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        msg = "run_id must contain only letters, digits, underscores, and hyphens"
        raise ValueError(msg)
    prefix = run_id if trade_id_prefix is None else trade_id_prefix
    if not _RUN_ID_PATTERN.fullmatch(prefix):
        msg = "trade_id_prefix must contain only letters, digits, underscores, and hyphens"
        raise ValueError(msg)
    default = default_inputs or RecordTagInputs()
    sorted_bars = tuple(sorted(canonical_bars, key=lambda bar: bar.timestamp))
    _validate_trade_tag_bars(sorted_bars, contract=contract)
    records: list[RecordedTrade] = []
    for ordinal, trade in enumerate(trades, start=1):
        if trade.contract_id != contract.contract_id:
            msg = (
                f"trade contract mismatch: expected {contract.contract_id}, got {trade.contract_id}"
            )
            raise ValueError(msg)
        trading_date = _trading_date_for_timestamp(
            trade.entry_timestamp,
            contract=contract,
            session_name=session_name,
        )
        inputs = (
            default
            if inputs_by_trading_date is None
            else inputs_by_trading_date.get(trading_date, default)
        )
        tags = _build_trade_tags(
            trade,
            events=events,
            bars=sorted_bars,
            contract=contract,
            session_name=session_name,
            inputs=inputs,
        )
        decision_evidence: TradeDecisionEvidence | None = None
        trade_id = f"{prefix}-{ordinal:05d}"
        if include_decision_evidence:
            if trade.decision_capture is None:
                msg = "new complete runs require engine-captured decision evidence for every trade"
                raise ValueError(msg)
            decision_evidence = build_trade_decision_evidence(
                trade.decision_capture,
                trade_id=trade_id,
                ordinal=ordinal,
                events=events,
            )
        records.append(
            RecordedTrade(
                trade_id=trade_id,
                execution=trade,
                tags=tags,
                decision_evidence=decision_evidence,
            )
        )
    return tuple(records)


def build_run_result(
    *,
    manifest: RunManifest,
    trade_records: Sequence[RecordedTrade],
    event_log: Sequence[StrategyEvent],
    contract: ContractSpec,
    completed_at: datetime | None = None,
    engine: EngineMetadata | None = None,
    additional_warnings: Sequence[str] = (),
    rejection_evidence: Sequence[RejectionEvidence] = (),
    evidence_complete: bool = False,
) -> RunResult:
    """Build immutable A5 facts and compact metrics from a completed execution run."""
    records = tuple(trade_records)
    events = tuple(event_log)
    metrics, equity_curve = _calculate_metrics_and_equity(
        records, manifest=manifest, contract=contract
    )
    warnings = _merge_warnings(_warnings_from_events(events), additional_warnings)
    rejection_records = tuple(rejection_evidence)
    evidence_summary = (
        build_evidence_summary(
            events=events,
            rejection_evidence=rejection_records,
            trade_count=len(records),
            complete=True,
        )
        if evidence_complete
        else None
    )
    return RunResult(
        manifest=manifest,
        trade_records=records,
        event_log=events,
        metrics=metrics,
        equity_curve=equity_curve,
        warnings=warnings,
        engine=engine or EngineMetadata(),
        completed_at=datetime.now(UTC) if completed_at is None else completed_at,
        rejection_evidence=rejection_records,
        evidence_summary=evidence_summary,
        evidence_complete=evidence_complete,
    )


def execution_trade_to_dict(trade: ExecutionTrade) -> dict[str, object]:
    """Return one execution trade in a stable JSON-friendly representation."""
    return {
        "contract_id": trade.contract_id,
        "direction": trade.direction.value,
        "signal_kind": trade.signal_kind.value,
        "quantity": trade.quantity,
        "signal_timestamp": _iso(trade.signal_timestamp),
        "entry_timestamp": _iso(trade.entry_timestamp),
        "entry_ts_init": _iso(trade.entry_ts_init),
        "exit_timestamp": _iso(trade.exit_timestamp),
        "exit_ts_init": _iso(trade.exit_ts_init),
        "entry_reference": trade.entry_reference,
        "entry_price": trade.entry_price,
        "stop_price": trade.stop_price,
        "target_price": trade.target_price,
        "exit_price": trade.exit_price,
        "exit_reason": trade.exit_reason.value,
        "gross_points": trade.gross_points,
        "gross_pnl": trade.gross_pnl,
        "total_commission": trade.total_commission,
        "net_pnl": trade.net_pnl,
        "entry_slippage_ticks": trade.entry_slippage_ticks,
        "exit_slippage_ticks": trade.exit_slippage_ticks,
    }


def strategy_event_to_dict(event: StrategyEvent) -> dict[str, object]:
    """Convert immutable event metadata to JSON without exposing a mapping proxy."""
    return {
        "sequence": event.sequence,
        "timestamp": _iso(event.timestamp),
        "ts_init": _iso(event.ts_init),
        "phase": event.phase.value,
        "machine": event.machine,
        "event_type": event.event_type.value,
        "from_state": event.from_state,
        "to_state": event.to_state,
        "direction": event.direction.value,
        "price": event.price,
        "details": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in event.details.items()
        },
    }


def _build_trade_tags(
    trade: ExecutionTrade,
    *,
    events: Sequence[StrategyEvent],
    bars: Sequence[CanonicalBar],
    contract: ContractSpec,
    session_name: str,
    inputs: RecordTagInputs,
) -> TradeTags:
    """Derive execution-visible tags and retain placeholders for P2-only sources."""
    local_entry = trade.entry_timestamp.astimezone(ZoneInfo(contract.timezone))
    # Only whole one-minute intervals strictly between entry and exit contribute
    # extrema.  The fills themselves are included in ``_mfe_mae_r``; this avoids
    # attributing a later move in a gap/opening or exit minute to an already-flat
    # position.
    trade_bars = tuple(
        bar for bar in bars if trade.entry_ts_init <= bar.timestamp < trade.exit_timestamp
    )
    mfe_r, mae_r = _mfe_mae_r(trade, trade_bars)
    entry_trading_date = _trading_date_for_timestamp(
        trade.entry_timestamp,
        contract=contract,
        session_name=session_name,
    )
    actual_daily_range = _actual_daily_range(
        bars,
        trading_date=entry_trading_date,
        contract=contract,
        session_name=session_name,
    )
    inside_count = _inside_count_for_trade(trade, events)
    return TradeTags(
        signal_kind=trade.signal_kind,
        daily_regime=inputs.daily_regime,
        regime_strength=inputs.regime_strength,
        entry_session=session_name,
        entry_local_time=local_entry.strftime("%H:%M"),
        entry_layers=inputs.entry_layers,
        inside_count=inside_count,
        multiple_inside=inside_count is not None and inside_count > 1,
        has_sweep_bonus=inputs.has_sweep_bonus,
        lmr_step1_leg_atr=inputs.lmr_step1_leg_atr,
        lmr_step2_leg_atr=inputs.lmr_step2_leg_atr,
        atr_expansion_ratio=inputs.atr_expansion_ratio,
        mfe_r=mfe_r,
        mae_r=mae_r,
        gap_through_target=_is_gap_through_target(trade, events),
        volatility_owner_view=inputs.volatility_owner_view,
        volatility_system_daily_atr_percentile=inputs.volatility_system_daily_atr_percentile,
        volatility_system_range_ratio=inputs.volatility_system_range_ratio,
        volatility_actual_daily_range=actual_daily_range,
    )


def _inside_count_for_trade(
    trade: ExecutionTrade,
    events: Sequence[StrategyEvent],
) -> int | None:
    """Recover Inside-run depth from the immutable strategy signal creation event."""
    if trade.signal_kind is not SignalKind.INSIDE:
        return None
    for event in reversed(events):
        if (
            event.event_type is StrategyEventType.SIGNAL_CREATED
            and event.timestamp == trade.signal_timestamp
            and event.direction is trade.direction
            and event.details.get("signal_kind") == trade.signal_kind.value
        ):
            candidate = event.details.get("inside_count")
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
                return candidate
    return None


def _is_gap_through_target(trade: ExecutionTrade, events: Sequence[StrategyEvent]) -> bool:
    """Tag a favourable target fill caused by an opening print through the limit."""
    if trade.exit_reason is not ExitReason.TARGET:
        return False
    for event in events:
        if (
            event.event_type is StrategyEventType.POSITION_CLOSED
            and event.timestamp == trade.exit_timestamp
            and event.direction is trade.direction
            and event.details.get("exit_reason") == ExitReason.TARGET.value
            and event.details.get("fill_mode") == "gap_open"
            and event.price is not None
            and isclose(event.price, trade.exit_price, rel_tol=0.0, abs_tol=1e-9)
        ):
            return True
    return False


def _mfe_mae_r(
    trade: ExecutionTrade,
    bars: Sequence[CanonicalBar],
) -> tuple[float, float]:
    """Compute conservative favourable/adverse excursion in actual-entry R units."""
    risk_points = abs(trade.entry_price - trade.stop_price)
    if risk_points <= 0:
        msg = "execution trade must have positive initial point risk"
        raise ValueError(msg)
    high_prices = [trade.entry_price, trade.exit_price]
    low_prices = [trade.entry_price, trade.exit_price]
    high_prices.extend(bar.high for bar in bars)
    low_prices.extend(bar.low for bar in bars)
    if trade.direction is Direction.LONG:
        mfe = max(0.0, max(high_prices) - trade.entry_price)
        mae = max(0.0, trade.entry_price - min(low_prices))
    else:
        mfe = max(0.0, trade.entry_price - min(low_prices))
        mae = max(0.0, max(high_prices) - trade.entry_price)
    return mfe / risk_points, mae / risk_points


def _actual_daily_range(
    bars: Sequence[CanonicalBar],
    *,
    trading_date: date,
    contract: ContractSpec,
    session_name: str,
) -> float | None:
    """Return the session-aware actual range for the trade's exchange trading date."""
    same_day = tuple(
        bar
        for bar in bars
        if trading_date_for_bar(bar, contract=contract, session_name=session_name) == trading_date
    )
    if not same_day:
        return None
    return max(bar.high for bar in same_day) - min(bar.low for bar in same_day)


def _calculate_metrics_and_equity(
    records: Sequence[RecordedTrade],
    *,
    manifest: RunManifest,
    contract: ContractSpec,
) -> tuple[RunMetrics, tuple[EquityPoint, ...]]:
    """Calculate compact P1 run metrics without a tabular callback dependency."""
    executions = tuple(record.execution for record in records)
    realized = calculate_realized_trade_metrics(executions, contract=contract)
    gross_pnl = sum(record.execution.gross_pnl for record in records)
    net_values = tuple(record.execution.net_pnl for record in records)
    wins = tuple(value for value in net_values if value > 0)
    losses = tuple(value for value in net_values if value < 0)
    r_values = tuple(_trade_net_r(trade, contract) for trade in executions)
    equity: list[EquityPoint] = []
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for record in records:
        cumulative += record.execution.net_pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        equity.append(
            EquityPoint(
                timestamp=record.execution.exit_ts_init,
                equity=manifest.initial_capital + cumulative,
                cumulative_net_pnl=cumulative,
            )
        )
    metrics = RunMetrics(
        trade_count=realized.trade_count,
        gross_pnl=gross_pnl,
        net_pnl=realized.net_pnl,
        net_r=realized.net_r,
        win_rate=(len(wins) / len(records)) if records else None,
        profit_factor=(sum(wins) / abs(sum(losses))) if losses else None,
        expectancy_r=(sum(r_values) / len(r_values)) if r_values else None,
        max_drawdown_pnl=max_drawdown,
    )
    return metrics, tuple(equity)


def calculate_realized_trade_metrics(
    trades: Sequence[ExecutionTrade],
    *,
    contract: ContractSpec,
) -> RealizedTradeMetrics:
    """Return exact cumulative closed-trade metrics using final result semantics."""
    materialized = tuple(trades)
    return RealizedTradeMetrics(
        trade_count=len(materialized),
        net_pnl=sum((trade.net_pnl for trade in materialized), 0.0),
        net_r=sum((_trade_net_r(trade, contract) for trade in materialized), 0.0),
    )


def _trade_net_r(trade: ExecutionTrade, contract: ContractSpec) -> float:
    """Express a net trade outcome in its actual-entry, fixed-stop risk unit."""
    risk_dollars = abs(trade.entry_price - trade.stop_price) * contract.point_value * trade.quantity
    if risk_dollars <= 0:
        msg = "execution trade must have positive initial dollar risk"
        raise ValueError(msg)
    return trade.net_pnl / risk_dollars


def _warnings_from_events(events: Sequence[StrategyEvent]) -> tuple[str, ...]:
    """Expose conservative same-minute fallbacks in the human-readable result summary."""
    ambiguity_count = sum(
        event.details.get("resolution") == "same_minute_stop_first" for event in events
    )
    if ambiguity_count == 0:
        return ()
    return (f"same_bar_ambiguous ×{ambiguity_count} (conservative stop-first)",)


def _merge_warnings(
    event_warnings: Sequence[str],
    additional_warnings: Sequence[str],
) -> tuple[str, ...]:
    """Retain stable, de-duplicated run warnings from execution and run setup."""
    merged: list[str] = []
    for warning in (*event_warnings, *additional_warnings):
        normalized = warning.strip()
        if not normalized:
            msg = "run warnings must not be blank"
            raise ValueError(msg)
        if normalized not in merged:
            merged.append(normalized)
    return tuple(merged)


def _validate_run_bars(
    bars: Sequence[CanonicalBar],
    *,
    contract: ContractSpec,
    start: datetime,
    end: datetime,
) -> None:
    """Ensure the A4 fingerprint can only cover the requested contract/range."""
    previous: datetime | None = None
    for bar in bars:
        if bar.contract_id != contract.contract_id:
            msg = f"bar contract mismatch: expected {contract.contract_id}, got {bar.contract_id}"
            raise ValueError(msg)
        timestamp = _as_utc(bar.timestamp, field_name="bar.timestamp")
        if not start <= timestamp < end:
            msg = "canonical bar falls outside the requested manifest range"
            raise ValueError(msg)
        if previous is not None and timestamp <= previous:
            msg = "run bars must have strictly increasing timestamps"
            raise ValueError(msg)
        previous = timestamp


def _validate_trade_tag_bars(bars: Sequence[CanonicalBar], *, contract: ContractSpec) -> None:
    """Reject a mixed-contract tag calculation before excursions become durable facts."""
    previous: datetime | None = None
    for bar in bars:
        if bar.contract_id != contract.contract_id:
            msg = f"bar contract mismatch: expected {contract.contract_id}, got {bar.contract_id}"
            raise ValueError(msg)
        if previous is not None and bar.timestamp <= previous:
            msg = "tag calculation bars must have strictly increasing timestamps"
            raise ValueError(msg)
        previous = bar.timestamp


def _trading_date_for_timestamp(
    timestamp: datetime,
    *,
    contract: ContractSpec,
    session_name: str,
) -> date:
    """Map non-bar artifact timestamps with the same exchange-session policy."""
    try:
        hours = contract.sessions[session_name]
    except KeyError as exc:
        msg = f"unknown session '{session_name}' for {contract.contract_id}"
        raise KeyError(msg) from exc
    local = _as_utc(timestamp, field_name="artifact timestamp").astimezone(
        ZoneInfo(contract.timezone)
    )
    if hours.start > hours.end and local.time() >= hours.start:
        return local.date() + timedelta(days=1)
    return local.date()


def trading_date_for_timestamp(
    timestamp: datetime,
    *,
    contract: ContractSpec,
    session_name: str,
) -> date:
    """Return the exchange date label without treating it as a timezone-convertible instant."""
    return _trading_date_for_timestamp(
        timestamp,
        contract=contract,
        session_name=session_name,
    )


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    """Require aware values and normalize all artifact timestamps to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name} must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    """Format a canonical UTC instant in compact JSON-safe ISO-8601 form."""
    return _as_utc(value, field_name="timestamp").isoformat().replace("+00:00", "Z")


def _require_finite(value: float, *, field_name: str) -> None:
    """Reject NaN/Infinity before a durable artifact is created."""
    if not isfinite(value):
        msg = f"{field_name} must be finite"
        raise ValueError(msg)


def _sorted_references(references: Iterable[str]) -> tuple[str, ...]:
    """Normalize optional provenance references before freezing them into A4."""
    return tuple(sorted(set(references)))
