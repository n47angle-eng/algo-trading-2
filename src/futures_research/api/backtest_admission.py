"""Shared P4-A precheck, submit, and job-start admission truth."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from futures_research.api.strategy_resolution import (
    ResolvedStrategy,
    StrategyResolutionError,
    resolve_strategy,
)
from futures_research.backtest.mtf import calibrate_regime_thresholds
from futures_research.backtest.native_daily_mtf import (
    build_native_daily_mtf_series,
    required_daily_regime_history_count,
)
from futures_research.backtest.persistence import RunIndexIntegrityError
from futures_research.backtest.run_reference_catalog import (
    RunReferenceCatalog,
    RunReferenceMigrationRequired,
)
from futures_research.data.contracts import (
    ContractRegistry,
    ContractSpec,
    ExecutionCostSpec,
    SlippageTicks,
)
from futures_research.data.coverage import (
    ContractCoverageProjection,
    ContractCoverageSource,
    project_contract_coverage_source,
    read_contract_coverage_source,
)
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import (
    session_bounds_for_trading_date,
    trading_date_for_session_start,
)
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT
from futures_research.strategy.store import StrategyStore, default_strategy_store

AdmissionStatus = Literal["pass", "warn", "block", "unknown"]
CellIdentity = tuple[str, str]

_STRATEGY_ID = re.compile(r"^strategy-[0-9]{4,}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
_DATE_TEXT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_PASS_REASON_CODES = frozenset(
    {"coverage_complete", "warmup_sufficient", "duplicate_none"}
)


class P4SlippageTicks(BaseModel):
    """The four Owner-editable integer-tick assumptions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    breakout_entry: int = Field(strict=True, ge=0)
    stop_exit: int = Field(strict=True, ge=0)
    target_exit: int = Field(strict=True, ge=0)
    day_end_exit: int = Field(strict=True, ge=0)


class P4ExecutionAssumptions(BaseModel):
    """Strict standard-run assumptions before they become per-cell snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_capital_usd: float = Field(strict=True, gt=0)
    commission_per_side_by_symbol: dict[str, float]
    slippage_ticks: P4SlippageTicks

    @field_validator("initial_capital_usd")
    @classmethod
    def require_finite_capital(cls, value: float) -> float:
        if not isfinite(value):
            msg = "initial_capital_usd must be finite"
            raise ValueError(msg)
        return value

    @field_validator("commission_per_side_by_symbol", mode="before")
    @classmethod
    def require_exact_commission_map(cls, value: object) -> object:
        if type(value) is not dict:
            msg = "commission_per_side_by_symbol must be an object"
            raise ValueError(msg)
        checked: dict[str, float] = {}
        for key, amount in cast(dict[object, object], value).items():
            if not isinstance(key, str) or _SYMBOL.fullmatch(key) is None:
                msg = "commission symbols must be canonical uppercase root symbols"
                raise ValueError(msg)
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                msg = "commission values must be finite numbers"
                raise ValueError(msg)
            number = float(amount)
            if not isfinite(number):
                msg = "commission values must be finite numbers"
                raise ValueError(msg)
            if number < 0:
                msg = "commission values must be non-negative"
                raise ValueError(msg)
            checked[key] = number
        return checked


class DuplicateAcknowledgement(BaseModel):
    """One exact duplicate identity acknowledged by the Owner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_version: str
    symbol: str
    range_start: datetime
    range_end: datetime

    @field_validator("strategy_version")
    @classmethod
    def require_strategy_id(cls, value: str) -> str:
        if type(value) is not str or _STRATEGY_ID.fullmatch(value) is None:
            msg = "strategy_version must be a canonical strategy-NNNN id"
            raise ValueError(msg)
        return value

    @field_validator("symbol")
    @classmethod
    def require_symbol(cls, value: str) -> str:
        if type(value) is not str or _SYMBOL.fullmatch(value) is None:
            msg = "symbol must be a canonical uppercase root symbol"
            raise ValueError(msg)
        return value

    @field_validator("range_start", "range_end", mode="before")
    @classmethod
    def require_aware_timestamp(cls, value: object, info: Any) -> datetime:
        return _parse_request_timestamp(value, field=info.field_name)

    @model_validator(mode="after")
    def require_ordered_range(self) -> DuplicateAcknowledgement:
        if self.range_start >= self.range_end:
            msg = "duplicate acknowledgement range_start must be earlier than range_end"
            raise ValueError(msg)
        return self

    @property
    def key(self) -> tuple[str, str, datetime, datetime]:
        return (
            self.strategy_version,
            self.symbol,
            self.range_start,
            self.range_end,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "range_start": _utc_text(self.range_start),
            "range_end": _utc_text(self.range_end),
        }


class P4BatchRequest(BaseModel):
    """Exact P4 standard precheck/submit envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_versions: list[str] = Field(min_length=1)
    symbols: list[str] = Field(min_length=1)
    range_start: datetime
    range_end: datetime
    execution_assumptions: P4ExecutionAssumptions
    duplicate_acknowledgements: list[DuplicateAcknowledgement]

    @field_validator("strategy_versions", mode="before")
    @classmethod
    def require_strategy_versions(cls, value: object) -> object:
        if type(value) is not list:
            msg = "strategy_versions must be an array"
            raise ValueError(msg)
        entries = cast(list[object], value)
        if not entries:
            msg = "strategy_versions must be non-empty"
            raise ValueError(msg)
        if any(
            type(item) is not str
            or _STRATEGY_ID.fullmatch(item) is None
            for item in entries
        ):
            msg = "strategy_versions must contain canonical strategy-NNNN ids"
            raise ValueError(msg)
        if len(set(cast(list[str], entries))) != len(entries):
            msg = "strategy_versions must not contain duplicates"
            raise ValueError(msg)
        return value

    @field_validator("symbols", mode="before")
    @classmethod
    def require_symbols(cls, value: object) -> object:
        if type(value) is not list:
            msg = "symbols must be an array"
            raise ValueError(msg)
        entries = cast(list[object], value)
        if not entries:
            msg = "symbols must be non-empty"
            raise ValueError(msg)
        if any(
            type(item) is not str
            or _SYMBOL.fullmatch(item) is None
            for item in entries
        ):
            msg = "symbols must contain canonical uppercase root symbols"
            raise ValueError(msg)
        if len(set(cast(list[str], entries))) != len(entries):
            msg = "symbols must not contain duplicates"
            raise ValueError(msg)
        return value

    @field_validator("range_start", "range_end", mode="before")
    @classmethod
    def require_aware_timestamp(cls, value: object, info: Any) -> datetime:
        return _parse_request_timestamp(value, field=info.field_name)

    @field_validator("duplicate_acknowledgements", mode="before")
    @classmethod
    def require_acknowledgement_array(cls, value: object) -> object:
        if type(value) is not list:
            msg = "duplicate_acknowledgements must be an array"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_cross_field_identity(self) -> P4BatchRequest:
        if self.range_start >= self.range_end:
            msg = "range_start must be earlier than range_end after UTC normalization"
            raise ValueError(msg)
        commissions = set(self.execution_assumptions.commission_per_side_by_symbol)
        selected = set(self.symbols)
        if commissions != selected:
            msg = (
                "commission_per_side_by_symbol must contain exactly one entry "
                "for every selected symbol"
            )
            raise ValueError(msg)

        acknowledgement_keys = [item.key for item in self.duplicate_acknowledgements]
        if len(set(acknowledgement_keys)) != len(acknowledgement_keys):
            msg = "duplicate_acknowledgements must not contain duplicates"
            raise ValueError(msg)
        matrix = {
            (strategy_version, symbol, self.range_start, self.range_end)
            for strategy_version in self.strategy_versions
            for symbol in self.symbols
        }
        if any(key not in matrix for key in acknowledgement_keys):
            msg = "duplicate acknowledgement must identify an exact current matrix cell"
            raise ValueError(msg)
        return self

    @property
    def acknowledgement_keys(self) -> frozenset[tuple[str, str, datetime, datetime]]:
        return frozenset(item.key for item in self.duplicate_acknowledgements)

    def to_storage_dict(self) -> dict[str, object]:
        """Persist only canonical primitives; defaults can never drift queued jobs."""
        assumptions = self.execution_assumptions
        return {
            "strategy_versions": list(self.strategy_versions),
            "symbols": list(self.symbols),
            "range_start": _utc_text(self.range_start),
            "range_end": _utc_text(self.range_end),
            "execution_assumptions": {
                "initial_capital_usd": assumptions.initial_capital_usd,
                "commission_per_side_by_symbol": dict(
                    assumptions.commission_per_side_by_symbol
                ),
                "slippage_ticks": assumptions.slippage_ticks.model_dump(),
            },
            "duplicate_acknowledgements": [
                acknowledgement.to_dict()
                for acknowledgement in self.duplicate_acknowledgements
            ],
        }


@dataclass(frozen=True, slots=True)
class RunAdmissionPlan:
    """Immutable truth handed from the job-start recheck to one runner call."""

    strategy_version: str
    symbol: str
    contract: ContractSpec
    resolved_strategy: ResolvedStrategy
    range_start: datetime
    range_end: datetime
    admitted_trading_dates: tuple[date, ...]
    excluded_trading_dates: tuple[date, ...]
    calibration_excluded_trading_dates: tuple[date, ...]
    initial_capital_usd: float
    quantity: int
    costs: ExecutionCostSpec
    duplicate_acknowledged: bool


@dataclass(frozen=True, slots=True)
class AdmissionEvaluation:
    """Public facts plus private per-cell immutable plans."""

    document: dict[str, Any]
    plans: dict[CellIdentity, RunAdmissionPlan]

    def plan_for(self, strategy_version: str, symbol: str) -> RunAdmissionPlan:
        try:
            return self.plans[(strategy_version, symbol)]
        except KeyError as exc:
            msg = f"admission did not produce a runnable plan for {strategy_version} × {symbol}"
            raise LookupError(msg) from exc


class AdmissionRequestError(ValueError):
    """The semantic identity in an otherwise-shaped request is invalid."""


class AdmissionServiceError(RuntimeError):
    """Endpoint-wide authority/configuration truth could not be read safely."""


@dataclass(frozen=True, slots=True)
class _CoverageEvaluation:
    document: dict[str, Any]
    status: AdmissionStatus
    admitted_dates: tuple[date, ...]
    excluded_dates: tuple[date, ...]


@dataclass(frozen=True, slots=True)
class _WarmupEvaluation:
    document: dict[str, Any]
    status: AdmissionStatus


@dataclass(frozen=True, slots=True)
class _DuplicateEvaluation:
    document: dict[str, Any]
    status: AdmissionStatus


class BacktestAdmissionService:
    """Evaluate every P4 matrix through one coverage/warm-up/duplicate core."""

    def __init__(
        self,
        *,
        data_root: Path,
        registry: ContractRegistry,
        strategy_store: StrategyStore,
        run_reference_loader: Callable[[], RunReferenceCatalog],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._data_root = data_root
        self._registry = registry
        self._strategy_store = strategy_store
        self._run_reference_loader = run_reference_loader
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        request: P4BatchRequest,
        *,
        only_cells: Sequence[CellIdentity] | None = None,
    ) -> AdmissionEvaluation:
        """Return one complete document without performing any writer operation."""
        matrix = tuple(
            (strategy_version, symbol)
            for strategy_version in request.strategy_versions
            for symbol in request.symbols
        )
        selected_cells = matrix if only_cells is None else tuple(only_cells)
        if not selected_cells or any(cell not in matrix for cell in selected_cells):
            msg = "job-start admission cells must be a non-empty subset of the request matrix"
            raise AdmissionRequestError(msg)

        contracts: dict[str, ContractSpec] = {}
        for symbol in {symbol for _, symbol in selected_cells}:
            try:
                contracts[symbol] = self._registry.by_symbol(symbol)
            except KeyError as exc:
                msg = f"symbol must be an exact configured root symbol: {symbol!r}"
                raise AdmissionRequestError(msg) from exc

        owner_entries = self._read_owner_entries()
        entries_by_contract = {
            contract.contract_id: tuple(
                entry
                for entry in owner_entries
                if entry["contract_id"] == contract.contract_id
            )
            for contract in contracts.values()
        }

        sources = {
            symbol: read_contract_coverage_source(
                contract,
                minute_store=CanonicalStore(self._data_root / "market"),
                native_daily_store=CanonicalStore(self._data_root / "market-daily"),
            )
            for symbol, contract in contracts.items()
        }

        duplicate_catalog: RunReferenceCatalog | None
        duplicate_unavailable_reason: str | None
        try:
            duplicate_catalog = self._run_reference_loader()
        except RunReferenceMigrationRequired:
            duplicate_catalog = None
            duplicate_unavailable_reason = "duplicate_index_unavailable"
        except RunIndexIntegrityError:
            duplicate_catalog = None
            duplicate_unavailable_reason = "duplicate_catalog_integrity_error"
        except Exception as exc:
            raise AdmissionServiceError(
                "run-reference truth could not be initialized safely"
            ) from exc
        else:
            duplicate_unavailable_reason = None

        projection_cache: dict[tuple[str, str], ContractCoverageProjection] = {}
        units: list[dict[str, Any]] = []
        plans: dict[CellIdentity, RunAdmissionPlan] = {}
        for strategy_version, symbol in selected_cells:
            contract = contracts[symbol]
            resolved: ResolvedStrategy | None
            strategy_reason: str | None = None
            try:
                resolved = resolve_strategy(
                    strategy_version=strategy_version,
                    symbol=symbol,
                    validation_run=False,
                    allow_unauthorized_symbol=True,
                    store=self._strategy_store,
                )
            except StrategyResolutionError:
                resolved = None
                strategy_reason = "strategy_unavailable"

            session_name = resolved.session_name if resolved is not None else None
            if session_name is not None:
                assert resolved is not None
                projection_key = (symbol, session_name)
                projection = projection_cache.get(projection_key)
                if projection is None:
                    projection = project_contract_coverage_source(
                        contract,
                        sources[symbol],
                        owner_entries=entries_by_contract[contract.contract_id],
                        session_name=session_name,
                    )
                    projection_cache[projection_key] = projection
                requested_dates = _requested_trading_dates(
                    contract,
                    range_start=request.range_start,
                    range_end=request.range_end,
                    session_name=session_name,
                )
                coverage = _evaluate_coverage(
                    contract,
                    projection,
                    requested_dates=requested_dates,
                    owner_entries=entries_by_contract[contract.contract_id],
                )
                warmup = _evaluate_warmup(
                    contract,
                    sources[symbol],
                    resolved=resolved,
                    range_start=request.range_start,
                    admitted_dates=coverage.admitted_dates,
                    requested_dates=requested_dates,
                    owner_entries=entries_by_contract[contract.contract_id],
                )
            else:
                coverage = _unavailable_coverage()
                warmup = _unknown_warmup(required_count=None)

            acknowledged = (
                strategy_version,
                symbol,
                request.range_start,
                request.range_end,
            ) in request.acknowledgement_keys
            duplicate = _evaluate_duplicate(
                catalog=duplicate_catalog,
                unavailable_reason=duplicate_unavailable_reason,
                strategy_version=strategy_version,
                symbol=symbol,
                range_start=request.range_start,
                range_end=request.range_end,
                acknowledged=acknowledged,
            )

            component_statuses = [coverage.status, warmup.status, duplicate.status]
            unit_reasons: list[str] = []
            if strategy_reason is not None:
                component_statuses.append("block")
                unit_reasons.append(strategy_reason)
            if resolved is not None and not resolved.binding.universe_authorized:
                component_statuses.append("block")
                unit_reasons.append("strategy_symbol_not_authorized")
            unit_status = _aggregate_status(component_statuses)
            for component in (coverage.document, warmup.document, duplicate.document):
                unit_reasons.extend(
                    code
                    for code in cast(list[str], component["reason_codes"])
                    if code not in _PASS_REASON_CODES
                )
            unit_reasons = list(dict.fromkeys(unit_reasons))

            unit = {
                "strategy_version": strategy_version,
                "symbol": symbol,
                "contract_id": contract.contract_id,
                "session_name": session_name,
                "range_start": _utc_text(request.range_start),
                "range_end": _utc_text(request.range_end),
                "status": unit_status,
                "reason_codes": unit_reasons,
                "coverage": coverage.document,
                "warmup": warmup.document,
                "duplicate": duplicate.document,
            }
            units.append(unit)

            if resolved is not None and unit_status in {"pass", "warn"}:
                assumptions = request.execution_assumptions
                all_owner_excluded = _owner_dates(
                    entries_by_contract[contract.contract_id],
                    decision="exclude",
                )
                costs = ExecutionCostSpec(
                    commission_per_side=assumptions.commission_per_side_by_symbol[symbol],
                    slippage_ticks=SlippageTicks.model_validate(
                        assumptions.slippage_ticks.model_dump()
                    ),
                    target_requires_through=contract.execution_costs.target_requires_through,
                )
                plans[(strategy_version, symbol)] = RunAdmissionPlan(
                    strategy_version=strategy_version,
                    symbol=symbol,
                    contract=contract,
                    resolved_strategy=resolved,
                    range_start=request.range_start,
                    range_end=request.range_end,
                    admitted_trading_dates=coverage.admitted_dates,
                    excluded_trading_dates=coverage.excluded_dates,
                    calibration_excluded_trading_dates=tuple(
                        sorted(set(all_owner_excluded) | contract.roll_blackout_dates())
                    ),
                    initial_capital_usd=assumptions.initial_capital_usd,
                    quantity=1,
                    costs=costs,
                    duplicate_acknowledged=acknowledged,
                )

        overall_status = _aggregate_status(
            [cast(AdmissionStatus, unit["status"]) for unit in units]
        )
        document = {
            "schema": "backtest_precheck.v1",
            "checked_at": _utc_text(self._clock()),
            "overall_status": overall_status,
            "can_submit": overall_status in {"pass", "warn"},
            "unit_count": len(units),
            "units": units,
        }
        return AdmissionEvaluation(document=document, plans=plans)

    def _read_owner_entries(self) -> tuple[dict[str, str], ...]:
        path = self._data_root / "blacklists" / "owner-excluded.v1.json"
        if not path.is_file():
            return ()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AdmissionServiceError("Owner decision truth is unreadable") from exc
        if not isinstance(raw, dict) or raw.get("schema") != "owner_blacklist.v1":
            raise AdmissionServiceError("Owner decision truth has an invalid document shape")
        entries = raw.get("entries")
        if not isinstance(entries, list):
            raise AdmissionServiceError("Owner decision truth requires an entries array")
        validated: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise AdmissionServiceError("Owner decision entries must be objects")
            contract_id = entry.get("contract_id")
            raw_date = entry.get("trading_date")
            decision = entry.get("decision")
            if (
                not isinstance(contract_id, str)
                or not contract_id
                or contract_id != contract_id.strip()
                or not isinstance(raw_date, str)
                or _DATE_TEXT.fullmatch(raw_date) is None
                or decision not in {"trust", "exclude"}
            ):
                raise AdmissionServiceError("Owner decision entry identity is invalid")
            try:
                parsed_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise AdmissionServiceError(
                    "Owner decision entry has an invalid trading date"
                ) from exc
            if parsed_date.isoformat() != raw_date:
                raise AdmissionServiceError(
                    "Owner decision entry trading date must be canonical"
                )
            validated.append(
                {
                    "contract_id": contract_id,
                    "trading_date": raw_date,
                    "decision": cast(str, decision),
                }
            )
        return tuple(validated)


def default_backtest_admission_service() -> BacktestAdmissionService:
    """Build a fresh zero-cache service over the current local truth."""
    from futures_research.api.routes_run_references import default_run_reference_catalog

    return BacktestAdmissionService(
        data_root=PROJECT_ROOT / "data",
        registry=ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml"),
        strategy_store=default_strategy_store(),
        run_reference_loader=default_run_reference_catalog,
    )


def _evaluate_coverage(
    contract: ContractSpec,
    projection: ContractCoverageProjection,
    *,
    requested_dates: tuple[date, ...],
    owner_entries: Sequence[dict[str, str]],
) -> _CoverageEvaluation:
    trading = projection.snapshot.trading_day_coverage
    latest_decisions = _latest_owner_decisions(owner_entries)
    roll = set(requested_dates) & contract.roll_blackout_dates()
    owner_excluded = {
        day for day in requested_dates if latest_decisions.get(day) == "exclude"
    }
    excluded = roll | owner_excluded
    non_excluded = set(requested_dates) - excluded

    if trading.get("status") != "known":
        blocking = tuple(sorted(non_excluded))
        document = _coverage_document(
            status="unknown",
            requested_dates=requested_dates,
            complete=(),
            trusted=(),
            owner_excluded=owner_excluded,
            roll=roll,
            blocking=blocking,
            missing_native=(),
            reason_codes=("coverage_unknown",),
        )
        return _CoverageEvaluation(
            document=document,
            status="unknown",
            admitted_dates=(),
            excluded_dates=tuple(sorted(excluded)),
        )

    complete_source = _date_set(trading.get("complete_trading_dates"))
    observed = set(projection.observed_dates)
    complete = non_excluded & observed & complete_source
    incomplete_observed = non_excluded & observed - complete
    trusted = {
        day for day in incomplete_observed if latest_decisions.get(day) == "trust"
    }
    pending = incomplete_observed - trusted
    missing_minute = non_excluded - observed

    if projection.native_available_dates is None:
        blocking = tuple(sorted(non_excluded))
        document = _coverage_document(
            status="unknown",
            requested_dates=requested_dates,
            complete=(),
            trusted=(),
            owner_excluded=owner_excluded,
            roll=roll,
            blocking=blocking,
            missing_native=(),
            reason_codes=("coverage_unknown",),
        )
        return _CoverageEvaluation(
            document=document,
            status="unknown",
            admitted_dates=(),
            excluded_dates=tuple(sorted(excluded)),
        )

    native_available = set(projection.native_available_dates)
    potential_admitted = complete | trusted
    missing_native = potential_admitted - native_available
    complete -= missing_native
    trusted -= missing_native
    blocking_set = pending | missing_minute | missing_native
    admitted = complete | trusted

    reasons: list[str] = []
    if complete:
        reasons.append("coverage_complete")
    if trusted:
        reasons.append("coverage_owner_trusted")
    if owner_excluded:
        reasons.append("coverage_owner_excluded")
    if roll:
        reasons.append("coverage_roll_blackout")
    if pending:
        reasons.append("coverage_pending_problem")
    if missing_minute:
        reasons.append("coverage_minute_missing")
    if missing_native:
        reasons.append("coverage_native_daily_missing")
    if not admitted and (
        (requested_dates and excluded == set(requested_dates))
        or not blocking_set
    ):
        reasons.append("coverage_all_dates_excluded")

    if blocking_set or not admitted:
        status: AdmissionStatus = "block"
    elif trusted or excluded:
        status = "warn"
    else:
        status = "pass"
    document = _coverage_document(
        status=status,
        requested_dates=requested_dates,
        complete=complete,
        trusted=trusted,
        owner_excluded=owner_excluded,
        roll=roll,
        blocking=blocking_set,
        missing_native=missing_native,
        reason_codes=tuple(reasons),
    )
    return _CoverageEvaluation(
        document=document,
        status=status,
        admitted_dates=tuple(sorted(admitted)),
        excluded_dates=tuple(sorted(excluded)),
    )


def _coverage_document(
    *,
    status: AdmissionStatus,
    requested_dates: Sequence[date],
    complete: Iterable[date],
    trusted: Iterable[date],
    owner_excluded: Iterable[date],
    roll: Iterable[date],
    blocking: Iterable[date],
    missing_native: Iterable[date],
    reason_codes: Sequence[str],
) -> dict[str, Any]:
    complete_dates = tuple(sorted(set(complete)))
    trusted_dates = tuple(sorted(set(trusted)))
    owner_dates = tuple(sorted(set(owner_excluded)))
    roll_dates = tuple(sorted(set(roll)))
    excluded_dates = tuple(sorted(set(owner_dates) | set(roll_dates)))
    blocking_dates = tuple(sorted(set(blocking)))
    missing_native_dates = tuple(sorted(set(missing_native)))
    return {
        "status": status,
        "requested_trading_date_count": len(requested_dates),
        "admitted_trading_date_count": len(complete_dates) + len(trusted_dates),
        "complete_trading_dates": _date_texts(complete_dates),
        "owner_trusted_problem_trading_dates": _date_texts(trusted_dates),
        "owner_excluded_trading_dates": _date_texts(owner_dates),
        "roll_blackout_trading_dates": _date_texts(roll_dates),
        "excluded_trading_dates": _date_texts(excluded_dates),
        "blocking_problem_trading_dates": _date_texts(blocking_dates),
        "missing_native_daily_trading_dates": _date_texts(missing_native_dates),
        "reason_codes": list(reason_codes),
    }


def _unavailable_coverage() -> _CoverageEvaluation:
    document = _coverage_document(
        status="unknown",
        requested_dates=(),
        complete=(),
        trusted=(),
        owner_excluded=(),
        roll=(),
        blocking=(),
        missing_native=(),
        reason_codes=("coverage_unknown",),
    )
    return _CoverageEvaluation(document, "unknown", (), ())


def _evaluate_warmup(
    contract: ContractSpec,
    source: ContractCoverageSource,
    *,
    resolved: ResolvedStrategy,
    range_start: datetime,
    admitted_dates: tuple[date, ...],
    requested_dates: tuple[date, ...],
    owner_entries: Sequence[dict[str, str]],
) -> _WarmupEvaluation:
    spec = resolved.spec
    if spec is None:
        return _unknown_warmup(required_count=None)
    required_count = required_daily_regime_history_count(spec)
    if source.native_daily_bars is None or source.native_daily_error is not None:
        return _unknown_warmup(required_count=required_count)

    excluded = set(_owner_dates(owner_entries, decision="exclude"))
    excluded.update(contract.roll_blackout_dates())
    mapped: list[tuple[date, CanonicalBar]] = []
    seen_dates: set[date] = set()
    try:
        for bar in source.native_daily_bars:
            trading_date = trading_date_for_session_start(
                contract,
                bar.timestamp,
                session_name="eth",
            )
            if trading_date in seen_dates:
                return _unknown_warmup(required_count=required_count)
            seen_dates.add(trading_date)
            if trading_date not in excluded:
                mapped.append((trading_date, bar))
        series = build_native_daily_mtf_series(
            contract,
            [bar for _, bar in mapped],
            session_name=resolved.session_name,
        )
    except (KeyError, ValueError):
        return _unknown_warmup(required_count=required_count)

    prior_snapshots = tuple(
        snapshot for snapshot in series.snapshots if snapshot.bar.ts_init <= range_start
    )
    available_count = len(prior_snapshots)
    calibrated = False
    if available_count >= required_count:
        try:
            calibrate_regime_thresholds(
                series,
                historical_end=range_start,
                separation_percentile=spec.regime.separation_percentile,
                slope_percentile=spec.regime.slope_percentile,
                slope_lookback=spec.regime.daily_slope_lookback,
            )
        except ValueError:
            return _unknown_warmup(required_count=required_count)
        calibrated = True

    if calibrated:
        evaluable = len(admitted_dates)
        first = admitted_dates[0].isoformat() if admitted_dates else None
        reason_codes = (
            ["warmup_sufficient"]
            if admitted_dates
            else ["warmup_sufficient", "warmup_no_evaluable_dates"]
        )
        status: AdmissionStatus = "pass" if admitted_dates else "warn"
        return _WarmupEvaluation(
            document={
                "status": status,
                "required_prior_trading_date_count": required_count,
                "available_prior_trading_date_count": available_count,
                "evaluable_trading_date_count": evaluable,
                "first_evaluable_trading_date": first,
                "suggested_range_start": None,
                "reason_codes": reason_codes,
            },
            status=status,
        )

    suggestion = _suggested_range_start(
        contract,
        series=series,
        spec=spec,
        current_start=range_start,
        requested_dates=requested_dates,
        session_name=resolved.session_name,
        required_count=required_count,
    )
    return _WarmupEvaluation(
        document={
            "status": "warn",
            "required_prior_trading_date_count": required_count,
            "available_prior_trading_date_count": available_count,
            "evaluable_trading_date_count": 0,
            "first_evaluable_trading_date": None,
            "suggested_range_start": (
                _utc_text(suggestion) if suggestion is not None else None
            ),
            "reason_codes": ["warmup_short"],
        },
        status="warn",
    )


def _unknown_warmup(*, required_count: int | None) -> _WarmupEvaluation:
    return _WarmupEvaluation(
        document={
            "status": "unknown",
            "required_prior_trading_date_count": required_count,
            "available_prior_trading_date_count": None,
            "evaluable_trading_date_count": None,
            "first_evaluable_trading_date": None,
            "suggested_range_start": None,
            "reason_codes": ["warmup_unknown"],
        },
        status="unknown",
    )


def _suggested_range_start(
    contract: ContractSpec,
    *,
    series: Any,
    spec: Any,
    current_start: datetime,
    requested_dates: Sequence[date],
    session_name: str,
    required_count: int,
) -> datetime | None:
    for trading_date in requested_dates:
        bounds = session_bounds_for_trading_date(
            contract,
            trading_date,
            session_name=session_name,
        )
        if bounds is None:
            continue
        boundary = bounds[0]
        if boundary <= current_start:
            continue
        available = sum(
            snapshot.bar.ts_init <= boundary for snapshot in series.snapshots
        )
        if available < required_count:
            continue
        try:
            calibrate_regime_thresholds(
                series,
                historical_end=boundary,
                separation_percentile=spec.regime.separation_percentile,
                slope_percentile=spec.regime.slope_percentile,
                slope_lookback=spec.regime.daily_slope_lookback,
            )
        except ValueError:
            continue
        return boundary
    return None


def _evaluate_duplicate(
    *,
    catalog: RunReferenceCatalog | None,
    unavailable_reason: str | None,
    strategy_version: str,
    symbol: str,
    range_start: datetime,
    range_end: datetime,
    acknowledged: bool,
) -> _DuplicateEvaluation:
    if catalog is None:
        reason = unavailable_reason or "duplicate_index_unavailable"
        return _DuplicateEvaluation(
            document={
                "status": "unknown",
                "count_known": False,
                "exact_match_count": None,
                "prior_run_ids": [],
                "unindexed_candidate_count": None,
                "acknowledged": acknowledged,
                "reason_codes": [reason],
            },
            status="unknown",
        )
    try:
        references = catalog.references_for_duplicate(
            strategy_version=strategy_version,
            symbol=symbol,
            range_start=_utc_text(range_start),
            range_end=_utc_text(range_end),
        )
    except RunIndexIntegrityError:
        return _DuplicateEvaluation(
            document={
                "status": "unknown",
                "count_known": False,
                "exact_match_count": None,
                "prior_run_ids": [],
                "unindexed_candidate_count": None,
                "acknowledged": acknowledged,
                "reason_codes": ["duplicate_catalog_integrity_error"],
            },
            status="unknown",
        )

    if references.unindexed_candidates:
        return _DuplicateEvaluation(
            document={
                "status": "unknown",
                "count_known": False,
                "exact_match_count": (
                    len(references.runs) if references.runs else None
                ),
                "prior_run_ids": [run.run_id for run in references.runs],
                "unindexed_candidate_count": len(references.unindexed_candidates),
                "acknowledged": acknowledged,
                "reason_codes": ["duplicate_identity_unproven"],
            },
            status="unknown",
        )
    run_ids = [run.run_id for run in references.runs]
    if not run_ids and acknowledged:
        status: AdmissionStatus = "block"
        reasons = ["duplicate_acknowledgement_stale"]
    elif run_ids and acknowledged:
        status = "warn"
        reasons = ["duplicate_acknowledged"]
    elif run_ids:
        status = "block"
        reasons = ["duplicate_exact_match"]
    else:
        status = "pass"
        reasons = ["duplicate_none"]
    return _DuplicateEvaluation(
        document={
            "status": status,
            "count_known": True,
            "exact_match_count": len(run_ids),
            "prior_run_ids": run_ids,
            "unindexed_candidate_count": 0,
            "acknowledged": acknowledged,
            "reason_codes": reasons,
        },
        status=status,
    )


def _requested_trading_dates(
    contract: ContractSpec,
    *,
    range_start: datetime,
    range_end: datetime,
    session_name: str,
) -> tuple[date, ...]:
    """Enumerate exact exchange-local session labels overlapping a UTC range."""
    first = range_start.astimezone(UTC).date() - timedelta(days=2)
    last = range_end.astimezone(UTC).date() + timedelta(days=2)
    result: list[date] = []
    cursor = first
    while cursor <= last:
        bounds = session_bounds_for_trading_date(
            contract,
            cursor,
            session_name=session_name,
        )
        if bounds is not None and bounds[0] < range_end and bounds[1] > range_start:
            result.append(cursor)
        cursor += timedelta(days=1)
    return tuple(result)


def _latest_owner_decisions(
    entries: Sequence[dict[str, str]],
) -> dict[date, Literal["trust", "exclude"]]:
    decisions: dict[date, Literal["trust", "exclude"]] = {}
    for entry in entries:
        decisions[date.fromisoformat(entry["trading_date"])] = cast(
            Literal["trust", "exclude"],
            entry["decision"],
        )
    return decisions


def _owner_dates(
    entries: Sequence[dict[str, str]],
    *,
    decision: Literal["trust", "exclude"],
) -> tuple[date, ...]:
    latest = _latest_owner_decisions(entries)
    return tuple(sorted(day for day, value in latest.items() if value == decision))


def _aggregate_status(statuses: Sequence[AdmissionStatus]) -> AdmissionStatus:
    if "unknown" in statuses:
        return "unknown"
    if "block" in statuses:
        return "block"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _date_set(value: object) -> set[date]:
    if not isinstance(value, list):
        return set()
    return {
        date.fromisoformat(item)
        for item in value
        if isinstance(item, str) and _DATE_TEXT.fullmatch(item) is not None
    }


def _date_texts(values: Iterable[date]) -> list[str]:
    return [item.isoformat() for item in values]


def _parse_request_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or value != value.strip():
        msg = f"{field} must be an unpadded ISO-8601 timestamp with an offset"
        raise ValueError(msg)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        msg = f"{field} must be an ISO-8601 timestamp with an offset"
        raise ValueError(msg) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = f"{field} must include an explicit timezone offset"
        raise ValueError(msg)
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
