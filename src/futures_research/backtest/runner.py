"""Closed-bar orchestration from canonical data through immutable run artifacts.

The strategy and execution state machines deliberately remain independent.  This
module is their thin, deterministic composition root: it performs all MTF work
before the replay loop, drives only closed five-minute snapshots, resolves fills
against the underlying one-minute data, then persists the completed A4/A5/A6
chain.  It never adds a second event schema.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Any, Final, Literal

from futures_research.backtest.evidence import build_rejection_evidence
from futures_research.backtest.execution import ConservativeExecution
from futures_research.backtest.mtf import (
    IndicatorSnapshot,
    MtfPrecomputation,
    MtfPrecomputer,
    MtfSeries,
    RegimeCalibration,
    Timeframe,
    calibrate_regime_thresholds,
)
from futures_research.backtest.native_daily_mtf import build_native_daily_mtf_series
from futures_research.backtest.nautilus import build_backtest_session
from futures_research.backtest.persistence import ExportedResult, ResultExporter, SqliteRunStore
from futures_research.backtest.records import (
    CalibrationProvenance,
    DataFingerprint,
    PreparedRun,
    RecordTagInputs,
    RunManifest,
    RunResult,
    StrategyBinding,
    build_run_result,
    build_trade_records,
    calculate_realized_trade_metrics,
    consumed_trading_dates_for_bars,
    fingerprint_canonical_bars,
    prepare_run,
    trading_date_for_bar,
    trading_date_for_timestamp,
)
from futures_research.backtest.strategy import (
    Direction,
    EntrySettings,
    EventPhase,
    Regime,
    RegimeDecision,
    RegimeSeries,
    RegimeSettings,
    StrategyEvent,
    StrategyEventType,
    StrategySpec,
    TrendStrategy,
    precompute_regime_series,
)
from futures_research.data.contracts import ContractSpec, ExecutionCostSpec
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import trading_date_for_session_start
from futures_research.data.storage import CanonicalStore

_ONE_MINUTE: Final = timedelta(minutes=1)
_LOGGER: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DayProgressSnapshot:
    """One operational snapshot after an admitted trading day is fully closed."""

    current_trading_date: date
    processed_trading_date_count: int
    total_trading_date_count: int
    trade_count: int
    realized_net_pnl_usd: float
    realized_net_r: float
    reported_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= self.processed_trading_date_count <= self.total_trading_date_count:
            msg = "processed trading-date count must be within the admitted total"
            raise ValueError(msg)
        if self.trade_count < 0:
            msg = "progress trade_count must not be negative"
            raise ValueError(msg)
        if not isfinite(self.realized_net_pnl_usd) or not isfinite(self.realized_net_r):
            msg = "progress PnL and R must be finite"
            raise ValueError(msg)
        object.__setattr__(
            self,
            "reported_at",
            _as_utc(self.reported_at, field_name="progress reported_at"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "current_trading_date": self.current_trading_date.isoformat(),
            "processed_trading_date_count": self.processed_trading_date_count,
            "total_trading_date_count": self.total_trading_date_count,
            "trade_count": self.trade_count,
            "realized_net_pnl_usd": self.realized_net_pnl_usd,
            "realized_net_r": self.realized_net_r,
            "reported_at": self.reported_at.isoformat().replace("+00:00", "Z"),
        }


DayProgressObserver = Callable[[DayProgressSnapshot], None]
ProgressClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class BacktestRunConfig:
    """Explicit operator inputs for one immutable S4/S5 backtest run.

    When ``strategy_spec`` is provided (WO-005), it is the sole strategy truth for
    the replay; ``regime_*_percentile`` and ``pullback_ema_period`` knobs are
    ignored for strategy construction (still validated for operator convenience).
    ``strategy_spec.universe_session`` must equal ``session_name``.
    """

    run_id: str
    strategy_version: str
    session_name: Literal["eth", "rth"]
    range_start: datetime
    range_end: datetime
    initial_capital: float
    quantity: int
    costs: ExecutionCostSpec | None = None
    admitted_trading_dates: tuple[date, ...] | None = None
    excluded_trading_dates: tuple[date, ...] = ()
    calibration_excluded_trading_dates: tuple[date, ...] = ()
    verify_nautilus_replay: bool = True
    validation_run: bool = False
    regime_separation_percentile: float = 65.0
    regime_slope_percentile: float = 65.0
    pullback_ema_period: Literal[18, 90] = 90
    strategy_spec: StrategySpec | None = None
    strategy_binding: StrategyBinding | None = None

    def __post_init__(self) -> None:
        """Normalize public time bounds and reject an ambiguous run definition."""
        start = _as_utc(self.range_start, field_name="range_start")
        end = _as_utc(self.range_end, field_name="range_end")
        if start >= end:
            msg = "range_start must be earlier than range_end"
            raise ValueError(msg)
        if not self.strategy_version.strip():
            msg = "strategy_version must not be blank"
            raise ValueError(msg)
        if not isfinite(self.initial_capital) or self.initial_capital <= 0:
            msg = "initial_capital must be a positive finite value"
            raise ValueError(msg)
        if self.quantity <= 0:
            msg = "quantity must be positive"
            raise ValueError(msg)
        if (
            self.admitted_trading_dates is not None
            and tuple(sorted(set(self.admitted_trading_dates)))
            != self.admitted_trading_dates
        ):
            msg = "admitted_trading_dates must be sorted and unique"
            raise ValueError(msg)
        if tuple(sorted(set(self.excluded_trading_dates))) != self.excluded_trading_dates:
            msg = "excluded_trading_dates must be sorted and unique"
            raise ValueError(msg)
        if (
            tuple(sorted(set(self.calibration_excluded_trading_dates)))
            != self.calibration_excluded_trading_dates
        ):
            msg = "calibration_excluded_trading_dates must be sorted and unique"
            raise ValueError(msg)
        if self.admitted_trading_dates is not None and set(
            self.admitted_trading_dates
        ) & set(self.excluded_trading_dates):
            msg = "admitted and excluded trading dates must not overlap"
            raise ValueError(msg)
        for name, value in (
            ("regime_separation_percentile", self.regime_separation_percentile),
            ("regime_slope_percentile", self.regime_slope_percentile),
        ):
            if not isfinite(value) or not 50.0 <= value <= 70.0:
                msg = f"{name} must be a finite value from 50 through 70"
                raise ValueError(msg)
        if self.pullback_ema_period not in (18, 90):
            msg = "pullback_ema_period must be 18 or 90 (TRADING_SPEC §12.2)"
            raise ValueError(msg)
        if (
            self.strategy_spec is not None
            and self.strategy_spec.universe_session != self.session_name
        ):
            msg = (
                "strategy_spec.universe_session must match session_name "
                f"({self.strategy_spec.universe_session!r} != {self.session_name!r})"
            )
            raise ValueError(msg)
        object.__setattr__(self, "range_start", start)
        object.__setattr__(self, "range_end", end)


@dataclass(frozen=True, slots=True)
class BacktestRunArtifacts:
    """Locations and immutable facts emitted by one completed full-run replay."""

    manifest: RunManifest
    result: RunResult
    exported: ExportedResult
    raw_bar_count: int
    admitted_bar_count: int
    warmup_bar_count: int
    entry_bar_count: int
    nautilus_replay_iterations: int | None
    #: ``None`` when chart sidecars were written; otherwise the reason they were
    #: not (WO-006 / 6-5 advisory 1 — a soft failure must still leave a trace).
    chart_materialization_error: str | None = None


@dataclass(frozen=True, slots=True)
class BacktestPreviewArtifacts:
    """A non-persistent replay result returned only to the explicit P2 preview route."""

    result: RunResult
    raw_bar_count: int
    admitted_bar_count: int
    warmup_bar_count: int
    entry_bar_count: int
    nautilus_replay_iterations: int | None
    charts: dict[str, dict[str, object]]


@dataclass(frozen=True, slots=True)
class _ReplayOutcome:
    """Pure replay facts shared by durable runs and zero-write preview."""

    prepared: PreparedRun
    result: RunResult
    raw_bar_count: int
    admitted_bar_count: int
    warmup_bar_count: int
    entry_bar_count: int
    nautilus_replay_iterations: int | None


class BacktestRunner:
    """Compose canonical storage, closed-bar strategy, execution, and A4/A5/A6 output."""

    def __init__(
        self,
        *,
        canonical_store: CanonicalStore,
        run_store: SqliteRunStore,
        result_exporter: ResultExporter,
        quality_reports_root: Path | None = None,
        daily_canonical_store: CanonicalStore | None = None,
    ) -> None:
        """Bind a runner to explicit data, persistence, export, and provenance roots.

        ``daily_canonical_store`` supplies IB native settlement daily bars for Daily
        regime calibration and gate (WO-003b 3b-3). When omitted, an empty daily series
        is used and the Daily gate stays under-warm / unavailable.
        """
        self._canonical_store = canonical_store
        self._daily_canonical_store = daily_canonical_store
        self._run_store = run_store
        self._result_exporter = result_exporter
        self._quality_reports_root = quality_reports_root

    def run(
        self,
        *,
        contract: ContractSpec,
        config: BacktestRunConfig,
        day_observer: DayProgressObserver | None = None,
        progress_clock: ProgressClock | None = None,
    ) -> BacktestRunArtifacts:
        """Produce one durable full-run artifact chain from the configured canonical range."""
        replay = self._replay(
            contract=contract,
            config=config,
            day_observer=day_observer,
            progress_clock=progress_clock,
        )
        staged_export = self._result_exporter.stage_new(replay.result)
        try:
            self._run_store.persist(
                replay.result,
                contract=contract,
                consumed_trading_dates=consumed_trading_dates_for_bars(
                    replay.prepared.bars,
                    contract=contract,
                    session_name=config.session_name,
                ),
                before_commit=staged_export.publish,
            )
        except BaseException:
            staged_export.rollback()
            raise
        else:
            staged_export.discard_temporary()
        chart_materialization_error = self._materialize_chart_sidecars(
            replay.prepared.manifest.run_id
        )
        return BacktestRunArtifacts(
            manifest=replay.prepared.manifest,
            result=replay.result,
            exported=staged_export.exported,
            chart_materialization_error=chart_materialization_error,
            raw_bar_count=replay.raw_bar_count,
            admitted_bar_count=replay.admitted_bar_count,
            warmup_bar_count=replay.warmup_bar_count,
            entry_bar_count=replay.entry_bar_count,
            nautilus_replay_iterations=replay.nautilus_replay_iterations,
        )

    def preview(
        self,
        *,
        contract: ContractSpec,
        config: BacktestRunConfig,
    ) -> BacktestPreviewArtifacts:
        """Replay the standard engine path without touching any repository or artifact writer."""
        replay = self._replay(
            contract=contract,
            config=config,
            trade_id_prefix="trade",
        )
        from futures_research.api.chart_series import build_preview_chart_series
        from futures_research.backtest.records import strategy_event_to_dict

        events = [strategy_event_to_dict(event) for event in replay.result.event_log]
        trades = [record.to_dict() for record in replay.result.trade_records]
        preview_timeframes: tuple[Literal["D", "1H", "30m", "5m"], ...] = (
            "D",
            "1H",
            "30m",
            "5m",
        )
        charts: dict[str, dict[str, object]] = {
            timeframe: build_preview_chart_series(
                contract=contract,
                session_name=config.session_name,
                range_start=config.range_start,
                range_end=config.range_end,
                data_fingerprint=replay.result.manifest.data_fingerprint.digest,
                events=events,
                trades=trades,
                market_store=self._canonical_store,
                daily_store=self._daily_canonical_store,
                timeframe=timeframe,
            )
            for timeframe in preview_timeframes
        }
        return BacktestPreviewArtifacts(
            result=replay.result,
            raw_bar_count=replay.raw_bar_count,
            admitted_bar_count=replay.admitted_bar_count,
            warmup_bar_count=replay.warmup_bar_count,
            entry_bar_count=replay.entry_bar_count,
            nautilus_replay_iterations=replay.nautilus_replay_iterations,
            charts=charts,
        )

    def _replay(
        self,
        *,
        contract: ContractSpec,
        config: BacktestRunConfig,
        trade_id_prefix: str | None = None,
        day_observer: DayProgressObserver | None = None,
        progress_clock: ProgressClock | None = None,
    ) -> _ReplayOutcome:
        """Execute the shared deterministic engine path before any publication decision."""
        warmup_bars = tuple(
            self._canonical_store.read(
                contract.contract_id,
                end=config.range_start,
            )
        )
        raw_bars = tuple(
            self._canonical_store.read(
                contract.contract_id,
                start=config.range_start,
                end=config.range_end,
            )
        )
        if not raw_bars:
            msg = "requested run range has no canonical bars"
            raise ValueError(msg)
        warmup_bars = _exclude_named_trading_dates(
            warmup_bars,
            contract=contract,
            session_name=config.session_name,
            excluded_dates=config.calibration_excluded_trading_dates,
        )
        planned_raw_bars = _apply_admission_date_plan(
            raw_bars,
            contract=contract,
            session_name=config.session_name,
            admitted_dates=config.admitted_trading_dates,
            excluded_dates=config.excluded_trading_dates,
        )
        quality_gate_mode: Literal["enforce", "disabled"] = (
            "disabled" if self._quality_reports_root is None else "enforce"
        )
        quality_report_ids = (
            () if quality_gate_mode == "disabled" else self._quality_report_ids(contract)
        )
        strategy_spec = _resolve_strategy_spec(config)
        precomputation = MtfPrecomputer(
            contract,
            session_name=config.session_name,
        ).precompute((*warmup_bars, *planned_raw_bars))
        native_daily_bars = self._load_native_daily_bars(contract, config)
        native_daily_series = build_native_daily_mtf_series(
            contract,
            native_daily_bars,
            session_name=config.session_name,
        )
        # Calibration fingerprint covers native daily history closed at/before range_start.
        calibration_daily_bars = tuple(
            bar
            for bar in native_daily_bars
            if _session_close_for_native_bar(contract, bar, config.session_name)
            <= config.range_start
        )
        regime_series, setup_warnings, calibration = _build_daily_regime_series(
            native_daily_series,
            strategy_spec=strategy_spec,
            calibration_cutoff=config.range_start,
            daily_fingerprint=fingerprint_canonical_bars(
                calibration_daily_bars,
                source_partitions=_partition_references(calibration_daily_bars),
                quality_report_ids=(),
            ),
        )
        prepared = prepare_run(
            run_id=config.run_id,
            strategy_version=config.strategy_version,
            contract=contract,
            session_name=config.session_name,
            range_start=config.range_start,
            range_end=config.range_end,
            initial_capital=config.initial_capital,
            quantity=config.quantity,
            canonical_bars=planned_raw_bars,
            costs=config.costs,
            source_partitions=_partition_references(planned_raw_bars),
            quality_report_ids=quality_report_ids,
            calibration=calibration,
            strategy_binding=config.strategy_binding,
            quality_gate_mode=quality_gate_mode,
            validation_run=config.validation_run,
            additional_excluded_trading_dates=config.excluded_trading_dates,
        )
        if not prepared.bars:
            msg = "all requested canonical bars were excluded by the roll blackout gate"
            raise ValueError(msg)

        nautilus_iterations = (
            self._verify_nautilus_replay(contract, prepared)
            if config.verify_nautilus_replay
            else None
        )
        entry_snapshots = _entry_snapshots_for_run(precomputation, prepared)

        # Phase E.2: prefer Rust dual-EMA loop as the product trade seal.
        # On any failure, full-job fall back to Python TrendStrategy + execution.
        # Data load / admission / SQLite / result.v1 writers remain Python.
        rust_draft = self._try_rust_backtest_loop(
            prepared_bars=prepared.bars,
            contract=contract,
            config=config,
            run_id=prepared.manifest.run_id,
        )
        if rust_draft is not None:
            try:
                from futures_research.backtest.kernel_seal import (
                    seal_kernel_draft_to_run_result,
                )

                sealed = seal_kernel_draft_to_run_result(
                    draft=rust_draft,
                    manifest=prepared.manifest,
                    contract=contract,
                    session_name=config.session_name,
                    canonical_bars=prepared.bars,
                    trade_id_prefix=trade_id_prefix,
                    additional_warnings=(
                        *setup_warnings,
                        "compute:product_seal=rust_kernel_v1",
                    ),
                )
                return _ReplayOutcome(
                    prepared=prepared,
                    result=sealed,
                    raw_bar_count=len(raw_bars),
                    admitted_bar_count=len(prepared.bars),
                    warmup_bar_count=len(warmup_bars),
                    entry_bar_count=len(entry_snapshots),
                    nautilus_replay_iterations=nautilus_iterations,
                )
            except Exception as exc:  # noqa: BLE001 — never abort without Python fallback
                _LOGGER.warning(
                    "rust kernel seal failed for run %s (%s); falling back to TrendStrategy",
                    prepared.manifest.run_id,
                    exc,
                )
                try:
                    from futures_research.platform.compute_errors import (
                        record_compute_error,
                    )

                    record_compute_error(
                        feature="backtest_loop_v1_seal",
                        message="Rust 回測 seal 失敗，已 fallback Python TrendStrategy",
                        detail=f"{type(exc).__name__}: {exc}",
                        tip="檢查 evidence 適配／dylib；或 FR_COMPUTE_BACKEND=stable。",
                        severity="warn",
                        effective_backend="python",
                        requested_backend="auto",
                        fallback_used=True,
                        context={"run_id": prepared.manifest.run_id},
                    )
                except Exception:  # noqa: BLE001
                    pass
                setup_warnings = (
                    *setup_warnings,
                    f"compute:rust_kernel_seal_failed:{type(exc).__name__}",
                )

        from futures_research.backtest.strategy_factory import create_trend_strategy

        strategy = create_trend_strategy(
            strategy_spec, tick_size=contract.tick_size
        )
        execution = ConservativeExecution(
            contract,
            quantity=config.quantity,
            costs=prepared.manifest.costs,
        )
        self._replay_closed_bars(
            prepared=prepared,
            precomputation=precomputation,
            regime_series=regime_series,
            native_daily_series=native_daily_series,
            strategy=strategy,
            execution=execution,
            entry_snapshots=entry_snapshots,
            contract=contract,
            session_name=config.session_name,
            day_observer=day_observer,
            progress_clock=progress_clock,
        )
        event_log = _resequence_events((*strategy.event_log, *execution.event_log))
        rejection_evidence = build_rejection_evidence(
            event_log,
            trading_date_for_timestamp=lambda timestamp: trading_date_for_timestamp(
                timestamp,
                contract=contract,
                session_name=config.session_name,
            ),
        )
        trade_records = build_trade_records(
            run_id=prepared.manifest.run_id,
            trades=execution.trades,
            events=event_log,
            canonical_bars=prepared.bars,
            contract=contract,
            session_name=config.session_name,
            inputs_by_trading_date=_tag_inputs_by_trading_date(
                prepared.bars,
                contract=contract,
                session_name=config.session_name,
                regime_series=regime_series,
                strategy_spec=strategy_spec,
            ),
            include_decision_evidence=True,
            trade_id_prefix=trade_id_prefix,
        )
        result = build_run_result(
            manifest=prepared.manifest,
            trade_records=trade_records,
            event_log=event_log,
            contract=contract,
            additional_warnings=(
                *setup_warnings,
                "compute:product_seal=python_trend_strategy",
            ),
            rejection_evidence=rejection_evidence,
            evidence_complete=True,
        )
        return _ReplayOutcome(
            prepared=prepared,
            result=result,
            raw_bar_count=len(raw_bars),
            admitted_bar_count=len(prepared.bars),
            warmup_bar_count=len(warmup_bars),
            entry_bar_count=len(entry_snapshots),
            nautilus_replay_iterations=nautilus_iterations,
        )

    def _try_rust_backtest_loop(
        self,
        *,
        prepared_bars: Sequence[CanonicalBar],
        contract: ContractSpec,
        config: BacktestRunConfig,
        run_id: str,
    ) -> Any:
        """Phase E: run pure dual-EMA loop via Rust seam when policy allows.

        Returns draft on success, ``None`` when skipped/failed (Python path continues).
        """
        try:
            from futures_research.contracts.backtest_loop import BacktestLoopRequest
            from futures_research.platform.backtest_loop_seam import (
                backtest_loop_v1,
                product_backtest_should_try_rust,
            )
        except ImportError:
            return None
        if not product_backtest_should_try_rust():
            return None
        costs = config.costs if config.costs is not None else contract.execution_costs
        commission = float(costs.commission_per_side)
        bars_payload = [
            {
                "t": bar.timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "o": float(bar.open),
                "h": float(bar.high),
                "l": float(bar.low),
                "c": float(bar.close),
                "v": float(bar.volume),
            }
            for bar in prepared_bars
        ]
        if not bars_payload:
            return None
        try:
            req = BacktestLoopRequest.model_validate(
                {
                    "schema": "backtest_loop_request.v1",
                    "timeframe_minutes": 5,
                    "ema_fast": 18,
                    "ema_slow": 50,
                    "quantity": int(config.quantity),
                    "point_value": float(contract.point_value),
                    "commission_per_side": commission,
                    "bars": bars_payload,
                    "requested_backend": "auto",
                }
            )
            draft = backtest_loop_v1(
                req,
                force_backend="accelerated",
                context={"run_id": run_id, "contract_id": contract.contract_id},
            )
            if draft.provenance.get("effective_backend") != "rust":
                return None
            return draft
        except Exception as exc:  # noqa: BLE001 — never abort product Python path
            _LOGGER.warning(
                "rust backtest_loop_v1 failed for run %s (%s); using Python strategy loop",
                run_id,
                exc,
            )
            return None

    def _materialize_chart_sidecars(self, run_id: str) -> str | None:
        """Pre-write chart sidecars so the viewer is a pure file read (6-3b).

        Soft failure by design — a chart cache miss must never invalidate a
        completed run.  But it is never silent: the reason is logged and returned
        so the caller (batch queue / CLI) can surface it (6-5 advisory 1).
        """
        try:
            from futures_research.api.chart_series import materialize_chart_sidecars
            from futures_research.api.results_catalog import ResultsCatalog

            materialize_chart_sidecars(
                ResultsCatalog(results_root=self._result_exporter.root),
                run_id,
            )
        except (OSError, ValueError, LookupError, RuntimeError, KeyError, TypeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            _LOGGER.warning(
                "chart sidecar materialization failed for run %s (%s); "
                "the viewer will fall back to a slower lazy compute on first open",
                run_id,
                reason,
            )
            return reason
        return None

    def _quality_report_ids(self, contract: ContractSpec) -> tuple[str, ...]:
        """Snapshot successful quality-report provenance without inventing report IDs."""
        if self._quality_reports_root is None:
            return ()
        contract_directory = self._quality_reports_root / contract.contract_id
        if not contract_directory.exists():
            return ()
        report_ids: list[str] = []
        for path in sorted(contract_directory.glob("*.json")):
            payload = _read_quality_report(path)
            if payload.get("contract_id") != contract.contract_id:
                msg = f"quality report contract mismatch: {path}"
                raise ValueError(msg)
            issues = payload.get("issues")
            if not isinstance(issues, list):
                msg = f"quality report issues must be a list: {path}"
                raise ValueError(msg)
            if any(
                isinstance(issue, dict) and issue.get("severity") == "error" for issue in issues
            ):
                msg = f"quality report contains errors: {path}"
                raise ValueError(msg)
            report_ids.append(path.relative_to(self._quality_reports_root).as_posix())
        return tuple(report_ids)

    @staticmethod
    def _verify_nautilus_replay(
        contract: ContractSpec,
        prepared: PreparedRun,
    ) -> int:
        """Replay admitted canonical bars through Nautilus before artifact publication."""
        session = build_backtest_session(
            contract,
            prepared.bars,
            initial_balance=Decimal(str(prepared.manifest.initial_capital)),
        )
        try:
            session.engine.run()
            iterations = int(session.engine.get_result().iterations)
        finally:
            session.dispose()
        if iterations != len(prepared.bars):
            msg = "Nautilus replay did not consume every admitted canonical bar"
            raise RuntimeError(msg)
        return iterations

    def _load_native_daily_bars(
        self,
        contract: ContractSpec,
        config: BacktestRunConfig,
    ) -> tuple[CanonicalBar, ...]:
        """Load IB native settlement daily bars through the run end (inclusive history)."""
        if self._daily_canonical_store is None:
            return ()
        excluded = set(config.calibration_excluded_trading_dates)
        return tuple(
            bar
            for bar in self._daily_canonical_store.read(
                contract.contract_id,
                end=config.range_end,
            )
            if trading_date_for_session_start(
                contract,
                bar.timestamp,
                session_name="eth",
            )
            not in excluded
        )

    @staticmethod
    def _replay_closed_bars(
        *,
        prepared: PreparedRun,
        precomputation: MtfPrecomputation,
        regime_series: RegimeSeries,
        native_daily_series: MtfSeries,
        strategy: TrendStrategy,
        execution: ConservativeExecution,
        entry_snapshots: Sequence[IndicatorSnapshot],
        contract: ContractSpec,
        session_name: str,
        day_observer: DayProgressObserver | None,
        progress_clock: ProgressClock | None,
    ) -> None:
        """Drive five-minute closed snapshots and their one-minute execution children."""
        children_by_timestamp = {bar.timestamp: bar for bar in prepared.bars}
        # Day-end uses native settlement Daily ts_init (session close of that trading date).
        daily_close_times = {bar.ts_init for bar in native_daily_series.bars}
        admitted_dates = consumed_trading_dates_for_bars(
            prepared.bars,
            contract=contract,
            session_name=session_name,
        )
        processed_date_count = 0
        if not entry_snapshots:
            msg = "run has no complete five-minute entry bars"
            raise ValueError(msg)
        for snapshot in entry_snapshots:
            entry_bar = snapshot.bar
            strategy_update = strategy.on_entry_bar(
                snapshot,
                daily=regime_series.latest_closed(entry_bar.ts_init),
                mid=precomputation.latest_closed(Timeframe.H1, entry_bar.ts_init),
            )
            if len(strategy_update.entry_intents) > 1:
                msg = "P1 strategy must not emit more than one intent per entry bar"
                raise RuntimeError(msg)
            if strategy_update.entry_intents:
                execution.arm(strategy_update.entry_intents[0])
            children = _minute_children(
                entry_bar.timestamp,
                entry_bar.ts_init,
                children_by_timestamp,
            )
            for child in children:
                execution.process_minute(child)
            if entry_bar.ts_init in daily_close_times:
                strategy.end_day(entry_bar.ts_init)
                execution.end_session(children[-1])
                if day_observer is not None:
                    current_date = trading_date_for_bar(
                        children[-1],
                        contract=contract,
                        session_name=session_name,
                    )
                    if (
                        processed_date_count >= len(admitted_dates)
                        or current_date != admitted_dates[processed_date_count]
                    ):
                        msg = "day observer date order diverged from admitted replay truth"
                        raise RuntimeError(msg)
                    processed_date_count += 1
                    realized = calculate_realized_trade_metrics(
                        execution.trades,
                        contract=contract,
                    )
                    day_observer(
                        DayProgressSnapshot(
                            current_trading_date=current_date,
                            processed_trading_date_count=processed_date_count,
                            total_trading_date_count=len(admitted_dates),
                            trade_count=realized.trade_count,
                            realized_net_pnl_usd=realized.net_pnl,
                            realized_net_r=realized.net_r,
                            reported_at=(
                                datetime.now(UTC)
                                if progress_clock is None
                                else progress_clock()
                            ),
                        )
                    )
        if day_observer is not None and processed_date_count != len(admitted_dates):
            msg = "day observer did not report every admitted trading date"
            raise RuntimeError(msg)


def _resolve_strategy_spec(config: BacktestRunConfig) -> StrategySpec:
    """Prefer an injected StrategySpec (WO-005); otherwise build from operator knobs."""
    if config.strategy_spec is not None:
        return config.strategy_spec
    return StrategySpec(
        universe_session=config.session_name,
        regime=RegimeSettings(
            separation_percentile=config.regime_separation_percentile,
            slope_percentile=config.regime_slope_percentile,
        ),
        entry=EntrySettings(pullback_ema_period=config.pullback_ema_period),
    )


def _exclude_named_trading_dates(
    bars: Sequence[CanonicalBar],
    *,
    contract: ContractSpec,
    session_name: str,
    excluded_dates: Sequence[date],
) -> tuple[CanonicalBar, ...]:
    """Remove excluded historical minute inputs before any MTF precomputation."""
    excluded = set(excluded_dates)
    if not excluded:
        return tuple(bars)
    return tuple(
        bar
        for bar in bars
        if trading_date_for_bar(
            bar,
            contract=contract,
            session_name=session_name,
        )
        not in excluded
    )


def _apply_admission_date_plan(
    bars: Sequence[CanonicalBar],
    *,
    contract: ContractSpec,
    session_name: str,
    admitted_dates: tuple[date, ...] | None,
    excluded_dates: Sequence[date],
) -> tuple[CanonicalBar, ...]:
    """Fail closed if a post-precheck read no longer matches the admitted partition."""
    if admitted_dates is None:
        return tuple(bars)
    admitted = set(admitted_dates)
    excluded = set(excluded_dates)
    selected: list[CanonicalBar] = []
    observed_admitted: set[date] = set()
    for bar in bars:
        trading_date = trading_date_for_bar(
            bar,
            contract=contract,
            session_name=session_name,
        )
        if trading_date in excluded:
            continue
        if trading_date not in admitted:
            msg = (
                "canonical data changed after admission: an unplanned trading date "
                f"appeared ({trading_date.isoformat()})"
            )
            raise RuntimeError(msg)
        observed_admitted.add(trading_date)
        selected.append(bar)
    if observed_admitted != admitted:
        missing = sorted(admitted - observed_admitted)
        msg = (
            "canonical data changed after admission: admitted trading dates disappeared "
            f"({','.join(day.isoformat() for day in missing)})"
        )
        raise RuntimeError(msg)
    return tuple(selected)


def _build_daily_regime_series(
    daily_series: MtfSeries,
    *,
    strategy_spec: StrategySpec,
    calibration_cutoff: datetime,
    daily_fingerprint: DataFingerprint,
) -> tuple[RegimeSeries, tuple[str, ...], CalibrationProvenance]:
    """Calibrate Daily regime from native settlement series, or mark under-warm unavailable."""
    historical_daily = tuple(
        snapshot
        for snapshot in daily_series.snapshots
        if snapshot.bar.ts_init <= calibration_cutoff
    )
    daily_count = len(historical_daily)
    ready_daily_count = sum(snapshot.is_ready for snapshot in historical_daily)
    try:
        calibration: RegimeCalibration = calibrate_regime_thresholds(
            daily_series,
            historical_end=calibration_cutoff,
            separation_percentile=strategy_spec.regime.separation_percentile,
            slope_percentile=strategy_spec.regime.slope_percentile,
            slope_lookback=strategy_spec.regime.daily_slope_lookback,
        )
    except ValueError:
        unavailable = RegimeSeries(
            timeframe=Timeframe.D1,
            decisions=tuple(
                RegimeDecision(
                    snapshot=snapshot,
                    regime=Regime.UNAVAILABLE,
                    direction=Direction.NONE,
                    normalized_separation=None,
                    normalized_slope=None,
                    recent_average_true_range=None,
                )
                for snapshot in daily_series.snapshots
            ),
        )
        return (
            unavailable,
            (
                "daily_regime_calibration_unavailable: no closed historical daily warm-up "
                "before range_start; no entries were eligible",
            ),
            CalibrationProvenance(
                window_start=daily_fingerprint.first_timestamp,
                window_end=calibration_cutoff,
                data_fingerprint=daily_fingerprint,
                daily_bar_count=daily_count,
                ready_daily_bar_count=ready_daily_count,
                sample_size=0,
                status="unavailable",
                daily_source="ib_native_daily",
            ),
        )
    return (
        precompute_regime_series(
            daily_series,
            calibration,
            congestion_lookback=strategy_spec.regime.daily_congestion_lookback,
            shrink_multiplier=strategy_spec.regime.congestion_shrink_multiplier,
        ),
        (),
        CalibrationProvenance(
            window_start=daily_fingerprint.first_timestamp,
            window_end=calibration_cutoff,
            data_fingerprint=daily_fingerprint,
            daily_bar_count=daily_count,
            ready_daily_bar_count=ready_daily_count,
            sample_size=calibration.sample_size,
            status="calibrated",
            separation_threshold=calibration.separation_threshold,
            slope_threshold=calibration.slope_threshold,
            daily_source="ib_native_daily",
        ),
    )


def _session_close_for_native_bar(
    contract: ContractSpec,
    bar: CanonicalBar,
    session_name: str,
) -> datetime:
    """Return session close for one start-labelled native daily bar."""
    from futures_research.backtest.native_daily_mtf import _session_end_for_open

    return _session_end_for_open(contract, bar.timestamp, session_name=session_name)


def _entry_snapshots_for_run(
    precomputation: MtfPrecomputation,
    prepared: PreparedRun,
) -> tuple[IndicatorSnapshot, ...]:
    """Return only complete, non-blacklisted entry bars inside the manifest range."""
    admitted_timestamps = {bar.timestamp for bar in prepared.bars}
    snapshots: list[IndicatorSnapshot] = []
    for snapshot in precomputation.series[Timeframe.M5].snapshots:
        bar = snapshot.bar
        if (
            bar.timestamp < prepared.manifest.range_start
            or bar.ts_init > prepared.manifest.range_end
        ):
            continue
        cursor = bar.timestamp
        complete_and_admitted = True
        while cursor < bar.ts_init:
            if cursor not in admitted_timestamps:
                complete_and_admitted = False
                break
            cursor += _ONE_MINUTE
        if complete_and_admitted:
            snapshots.append(snapshot)
    return tuple(snapshots)


def _tag_inputs_by_trading_date(
    bars: Sequence[CanonicalBar],
    *,
    contract: ContractSpec,
    session_name: str,
    regime_series: RegimeSeries,
    strategy_spec: StrategySpec,
) -> dict[date, RecordTagInputs]:
    """Map each exchange trading date to the already-closed Daily context it saw."""
    first_bar_by_date: dict[date, CanonicalBar] = {}
    for bar in bars:
        trading_date = trading_date_for_bar(
            bar,
            contract=contract,
            session_name=session_name,
        )
        first_bar_by_date.setdefault(trading_date, bar)
    return {
        trading_date: _record_inputs_for_daily_context(
            regime_series.latest_closed(first_bar.timestamp),
            entry_layers=strategy_spec.entry.entry_layers,
        )
        for trading_date, first_bar in first_bar_by_date.items()
    }


def _record_inputs_for_daily_context(
    decision: RegimeDecision | None,
    *,
    entry_layers: int,
) -> RecordTagInputs:
    """Create explicit record-only daily tags without promoting P2 sources to gates."""
    if decision is None:
        daily_regime: Literal["trend", "range", "congestion", "unavailable"] = "unavailable"
    elif decision.regime is Regime.TREND:
        daily_regime = "trend"
    elif decision.regime is Regime.RANGE:
        daily_regime = "range"
    elif decision.regime is Regime.CONGESTION:
        daily_regime = "congestion"
    else:
        daily_regime = "unavailable"
    return RecordTagInputs(
        daily_regime=daily_regime,
        entry_layers=entry_layers,
    )


def _minute_children(
    start: datetime,
    end: datetime,
    bars_by_timestamp: dict[datetime, CanonicalBar],
) -> tuple[CanonicalBar, ...]:
    """Recover every one-minute child of one complete five-minute entry interval."""
    children: list[CanonicalBar] = []
    cursor = start
    while cursor < end:
        try:
            children.append(bars_by_timestamp[cursor])
        except KeyError as exc:
            msg = f"complete entry bar lacks canonical child minute: {cursor.isoformat()}"
            raise RuntimeError(msg) from exc
        cursor += _ONE_MINUTE
    return tuple(children)


def _resequence_events(events: Sequence[StrategyEvent]) -> tuple[StrategyEvent, ...]:
    """Publish one globally ordered StrategyEvent log without changing its vocabulary."""
    indexed = enumerate(events)
    ordered = sorted(indexed, key=lambda item: (_event_sort_key(item[1]), item[0]))
    return tuple(
        StrategyEvent(
            sequence=index + 1,
            timestamp=event.timestamp,
            ts_init=event.ts_init,
            phase=event.phase,
            machine=event.machine,
            event_type=event.event_type,
            from_state=event.from_state,
            to_state=event.to_state,
            direction=event.direction,
            price=event.price,
            details=event.details,
            origin=event.origin,
            origin_sequence=event.origin_sequence,
            rejection_capture=event.rejection_capture,
        )
        for index, (_, event) in enumerate(ordered)
    )


def _event_sort_key(event: StrategyEvent) -> tuple[datetime, int, int, datetime, str, int]:
    """Order events by causal phase while retaining their chart timestamps unchanged."""
    if event.phase is EventPhase.CLOSE:
        return (
            event.ts_init,
            2,
            0,
            event.timestamp,
            event.machine,
            event.sequence,
        )
    if event.phase is EventPhase.DAY_END:
        return (
            event.timestamp,
            3,
            0,
            event.ts_init,
            event.machine,
            event.sequence,
        )
    intrabar_priority = (
        0
        if event.event_type is StrategyEventType.ENTRY_INTENT_CREATED
        else 2
        if event.machine == "execution"
        else 1
    )
    return (
        event.timestamp,
        0,
        intrabar_priority,
        event.ts_init,
        event.machine,
        event.sequence,
    )


def _partition_references(bars: Sequence[CanonicalBar]) -> tuple[str, ...]:
    """Derive the exact canonical partitions represented in an admitted source range."""
    return tuple(
        sorted(
            {
                (
                    f"contract_id={bar.contract_id}/year={bar.timestamp.year:04d}/"
                    f"month={bar.timestamp.month:02d}"
                )
                for bar in bars
            }
        )
    )


def _read_quality_report(path: Path) -> dict[str, object]:
    """Read one JSON quality report as a validated object-shaped provenance record."""
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        msg = f"quality report must be a JSON object: {path}"
        raise ValueError(msg)
    return parsed


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    """Normalize aware public run bounds to the project's canonical UTC convention."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name} must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)
