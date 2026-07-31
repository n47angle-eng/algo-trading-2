"""P4-B day progress, operational persistence, cancel, and v1-read proofs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_p4_admission import (
    RUN_DATE,
    _build_fixture,
    _request,
    _service,
)

from futures_research.api import batch_queue as batch_queue_mod
from futures_research.api import chart_series as chart_series_mod
from futures_research.api.batch_queue import (
    BatchOperationalStateError,
    BatchPersistenceError,
    BatchQueue,
    BatchRecord,
    JobProgress,
    RunJob,
)
from futures_research.api.main import app
from futures_research.backtest import records as records_mod
from futures_research.backtest.evidence import (
    ConditionObservation,
    EntryDecisionCapture,
)
from futures_research.backtest.execution import (
    ConservativeExecution,
    EntryIntent,
    ExecutionTrade,
    ExitReason,
)
from futures_research.backtest.persistence import ResultExporter, SqliteRunStore
from futures_research.backtest.records import calculate_realized_trade_metrics
from futures_research.backtest.runner import (
    BacktestRunConfig,
    BacktestRunner,
    DayProgressSnapshot,
)
from futures_research.backtest.strategy import (
    Direction,
    EventPhase,
    SignalKind,
    StrategyEventType,
    StrategyUpdate,
    TrendStrategy,
)
from futures_research.data.storage import CanonicalStore

FIXED_NOW = datetime(2026, 7, 27, 8, 5, tzinfo=UTC)
CREATED_AT = "2026-07-27T08:00:00Z"
UPDATED_AT = "2026-07-27T08:05:00Z"
INTERNAL_ASSUMPTIONS = {
    "initial_capital_usd": 100_000.0,
    "commission_per_side": 2.5,
    "slippage_ticks": {
        "breakout_entry": 1,
        "stop_exit": 2,
        "target_exit": 0,
        "day_end_exit": 1,
    },
    "target_requires_through": False,
    "fill_model": "conservative",
    "simulation_precision": "one_minute",
    "quantity": 1,
}
PUBLIC_ASSUMPTIONS = {
    "initial_capital_usd": 100_000.0,
    "commission_per_side": 2.5,
    "slippage_ticks": {
        "breakout_entry": 1,
        "stop_exit": 2,
        "target_exit": 0,
        "day_end_exit": 1,
    },
    "target_requires_through": False,
    "fill_model": "conservative",
    "bar_precision": "1m",
}


class PausedBatchQueue(BatchQueue):
    """Keep deterministic tests on the same lock without starting a thread."""

    def _ensure_worker_unlocked(self) -> None:
        return


def _snapshot(
    *,
    trading_date: date = RUN_DATE,
    processed: int = 1,
    total: int = 2,
    trade_count: int = 0,
    net_pnl: float = 0.0,
    net_r: float = 0.0,
) -> DayProgressSnapshot:
    return DayProgressSnapshot(
        current_trading_date=trading_date,
        processed_trading_date_count=processed,
        total_trading_date_count=total,
        trade_count=trade_count,
        realized_net_pnl_usd=net_pnl,
        realized_net_r=net_r,
        reported_at=FIXED_NOW,
    )


def _queued_record(
    *,
    batch_id: str = "batch-progress-fixture",
    job_count: int = 2,
    total: int = 2,
    assumptions: bool = False,
) -> BatchRecord:
    jobs = [
        RunJob(
            job_id=f"job-{index + 1}",
            run_id=f"run-{index + 1}",
            symbol="NQ",
            strategy_version=f"strategy-{index + 1:04d}",
            execution_assumptions=(
                deepcopy(INTERNAL_ASSUMPTIONS) if assumptions else None
            ),
            planned_trading_date_count=total,
        )
        for index in range(job_count)
    ]
    return BatchRecord(
        batch_id=batch_id,
        status="queued",
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        request={},
        jobs=jobs,
    )


def _install_queue(
    monkeypatch: pytest.MonkeyPatch,
    queue: BatchQueue,
) -> None:
    monkeypatch.setattr(batch_queue_mod, "_GLOBAL_QUEUE", queue)


def _claim(queue: BatchQueue) -> tuple[BatchRecord, RunJob]:
    with queue._lock:
        claimed = queue._claim_next_job_unlocked()
    assert claimed is not None
    return claimed


def _config_for_plan(plan: Any, *, run_id: str) -> BacktestRunConfig:
    return BacktestRunConfig(
        run_id=run_id,
        strategy_version=plan.strategy_version,
        session_name=plan.resolved_strategy.session_name,
        range_start=plan.range_start,
        range_end=plan.range_end,
        initial_capital=plan.initial_capital_usd,
        quantity=plan.quantity,
        costs=plan.costs,
        admitted_trading_dates=plan.admitted_trading_dates,
        excluded_trading_dates=plan.excluded_trading_dates,
        calibration_excluded_trading_dates=(
            plan.calibration_excluded_trading_dates
        ),
        verify_nautilus_replay=False,
        validation_run=False,
        strategy_spec=plan.resolved_strategy.spec,
        strategy_binding=plan.resolved_strategy.binding,
    )


def _runner_for_fixture(fixture: Any, tmp_path: Path) -> BacktestRunner:
    return BacktestRunner(
        canonical_store=CanonicalStore(fixture.data_root / "market"),
        daily_canonical_store=CanonicalStore(fixture.data_root / "market-daily"),
        run_store=SqliteRunStore(tmp_path / "runs.sqlite3"),
        result_exporter=ResultExporter(tmp_path / "results"),
        quality_reports_root=None,
    )


def _sidecar_inventory(root: Path) -> dict[str, tuple[bytes, str]]:
    inventory: dict[str, tuple[bytes, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        content = path.read_bytes()
        inventory[path.relative_to(root).as_posix()] = (
            content,
            hashlib.sha256(content).hexdigest(),
        )
    return inventory


def test_day_observer_runs_after_both_day_close_hooks_and_matches_final_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dates = (RUN_DATE, date(2026, 7, 21))
    fixture = _build_fixture(tmp_path, run_dates=run_dates)
    plan = _service(fixture).evaluate(_request(fixture)).plan_for(
        fixture.strategy_id,
        "NQ",
    )
    runner = _runner_for_fixture(fixture, tmp_path)
    events: list[tuple[str, date]] = []
    snapshots: list[DayProgressSnapshot] = []
    original_end_day = TrendStrategy.end_day
    original_end_session = ConservativeExecution.end_session

    def end_day(self: TrendStrategy, at: datetime) -> Any:
        update = original_end_day(self, at)
        events.append(("strategy.end_day", at.date()))
        return update

    def end_session(self: ConservativeExecution, final_bar: Any) -> Any:
        update = original_end_session(self, final_bar)
        events.append(("execution.end_session", final_bar.timestamp.date()))
        return update

    def observe(snapshot: DayProgressSnapshot) -> None:
        assert [event[0] for event in events[-2:]] == [
            "strategy.end_day",
            "execution.end_session",
        ]
        snapshots.append(snapshot)
        events.append(("observer", snapshot.current_trading_date))

    monkeypatch.setattr(TrendStrategy, "end_day", end_day)
    monkeypatch.setattr(ConservativeExecution, "end_session", end_session)
    replay = runner._replay(
        contract=plan.contract,
        config=_config_for_plan(plan, run_id="p4-progress-order"),
        day_observer=observe,
        progress_clock=lambda: FIXED_NOW,
    )

    assert [snapshot.current_trading_date for snapshot in snapshots] == list(
        run_dates
    )
    assert [snapshot.processed_trading_date_count for snapshot in snapshots] == [
        1,
        2,
    ]
    assert {snapshot.total_trading_date_count for snapshot in snapshots} == {2}
    assert all(snapshot.reported_at == FIXED_NOW for snapshot in snapshots)
    assert all(snapshot.trade_count == 0 for snapshot in snapshots)
    final = snapshots[-1]
    assert final.trade_count == replay.result.metrics.trade_count
    assert final.realized_net_pnl_usd == replay.result.metrics.net_pnl
    assert final.realized_net_r == replay.result.metrics.net_r


def test_true_runner_closed_trade_snapshot_matches_nonzero_final_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    plan = _service(fixture).evaluate(_request(fixture)).plan_for(
        fixture.strategy_id,
        "NQ",
    )

    def controlled_entry(
        self: TrendStrategy,
        snapshot: Any,
        *,
        daily: Any,
        mid: Any,
    ) -> StrategyUpdate:
        del daily, mid
        if getattr(self, "_p4_nonzero_fixture_emitted", False):
            return StrategyUpdate(events=(), entry_intents=())
        self._p4_nonzero_fixture_emitted = True
        events = []
        bar = snapshot.bar
        signal_event = self._emit(
            events,
            timestamp=bar.timestamp,
            ts_init=bar.ts_init,
            phase=EventPhase.INTRABAR,
            machine="entry_signal",
            event_type=StrategyEventType.SIGNAL_CREATED,
            from_state="candidate",
            to_state="pending",
            direction=Direction.LONG,
            price=100.0,
            details={"signal_kind": SignalKind.INSIDE.value},
        )
        intent_event = self._emit(
            events,
            timestamp=bar.timestamp,
            ts_init=bar.ts_init,
            phase=EventPhase.INTRABAR,
            machine="entry_signal",
            event_type=StrategyEventType.ENTRY_INTENT_CREATED,
            from_state="pending",
            to_state="triggered",
            direction=Direction.LONG,
            price=100.0,
            details={
                "signal_kind": SignalKind.INSIDE.value,
                "stop_reference": 99.0,
            },
        )
        capture = EntryDecisionCapture(
            signal_kind=SignalKind.INSIDE.value,
            signal_timestamp=bar.timestamp,
            entry_reference=100.0,
            stop_reference_type="signal_low",
            stop_reference_price=99.0,
            stop_offset_ticks=0,
            final_stop_price=99.0,
            condition_observations=(
                ConditionObservation(
                    condition_id="fixture_entry_gate",
                    layer_id="entry",
                    observed_at=bar.timestamp,
                    status="passed",
                    actual=True,
                    operator="eq",
                    required=True,
                    unit="bool",
                ),
            ),
            signal_event_ref=signal_event.event_ref,
            intent_event_ref=intent_event.event_ref,
        )
        intent = EntryIntent(
            direction=Direction.LONG,
            entry_reference=100.0,
            stop_reference=99.0,
            signal_kind=SignalKind.INSIDE,
            signal_timestamp=bar.timestamp,
            timestamp=bar.timestamp,
            ts_init=bar.ts_init,
            decision_capture=capture,
        )
        return StrategyUpdate(events=tuple(events), entry_intents=(intent,))

    monkeypatch.setattr(TrendStrategy, "on_entry_bar", controlled_entry)
    snapshots: list[DayProgressSnapshot] = []
    replay = _runner_for_fixture(fixture, tmp_path)._replay(
        contract=plan.contract,
        config=_config_for_plan(plan, run_id="p4-progress-nonzero-trade"),
        day_observer=snapshots.append,
        progress_clock=lambda: FIXED_NOW,
    )

    assert len(replay.result.trade_records) == 1
    assert replay.result.trade_records[0].decision_evidence is not None
    assert len(snapshots) == 1
    final = snapshots[-1]
    assert final.trade_count == replay.result.metrics.trade_count == 1
    assert final.realized_net_pnl_usd == replay.result.metrics.net_pnl == -15.0
    assert final.realized_net_r == replay.result.metrics.net_r == -0.6


def test_none_and_noop_observer_publish_identical_immutable_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    plan = _service(fixture).evaluate(_request(fixture)).plan_for(
        fixture.strategy_id,
        "NQ",
    )
    config = _config_for_plan(plan, run_id="p4-observer-parity")
    none_root = tmp_path / "observer-none"
    noop_root = tmp_path / "observer-noop"

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            if tz is None:
                return FIXED_NOW.replace(tzinfo=None)
            return FIXED_NOW.astimezone(tz)

    monkeypatch.setattr(records_mod, "datetime", FrozenDateTime)
    original_materialize = chart_series_mod.materialize_chart_sidecars

    def materialize_fixture_sidecars(
        catalog: Any,
        run_id: str,
        **kwargs: Any,
    ) -> list[Path]:
        assert kwargs == {}
        return original_materialize(
            catalog,
            run_id,
            market_root=fixture.data_root / "market",
            daily_root=fixture.data_root / "market-daily",
        )

    monkeypatch.setattr(
        chart_series_mod,
        "resolve_contract",
        lambda _contract_id: fixture.contract,
    )
    monkeypatch.setattr(
        chart_series_mod,
        "materialize_chart_sidecars",
        materialize_fixture_sidecars,
    )
    chart_series_mod.clear_chart_memory_cache()
    none = _runner_for_fixture(fixture, none_root).run(
        contract=plan.contract,
        config=config,
    )
    observed: list[DayProgressSnapshot] = []
    noop = _runner_for_fixture(fixture, noop_root).run(
        contract=plan.contract,
        config=config,
        day_observer=observed.append,
        progress_clock=lambda: FIXED_NOW,
    )

    assert len(observed) == 1
    assert none.chart_materialization_error is None
    assert noop.chart_materialization_error is None
    assert none.result == noop.result
    assert none.manifest == noop.manifest
    none_paths = (
        none.exported.result_path,
        none.exported.trades_path,
        none.exported.equity_curve_path,
        none.exported.events_path,
    )
    noop_paths = (
        noop.exported.result_path,
        noop.exported.trades_path,
        noop.exported.equity_curve_path,
        noop.exported.events_path,
    )
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in none_paths
    ] == [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in noop_paths
    ]
    database_rows: list[tuple[Any, ...]] = []
    for database in (none_root / "runs.sqlite3", noop_root / "runs.sqlite3"):
        with sqlite3.connect(database) as connection:
            run = connection.execute(
                """
                SELECT manifest_json, result_json, created_at, completed_at
                FROM runs WHERE run_id = ?
                """,
                (config.run_id,),
            ).fetchone()
            trades = connection.execute(
                """
                SELECT ordinal, trade_id, record_json
                FROM trades WHERE run_id = ? ORDER BY ordinal
                """,
                (config.run_id,),
            ).fetchall()
        assert run is not None
        database_rows.append((*run, tuple(trades)))
    assert database_rows[0] == database_rows[1]
    none_sidecars = _sidecar_inventory(none_root / "results" / "chart")
    noop_sidecars = _sidecar_inventory(noop_root / "results" / "chart")
    assert set(none_sidecars) == {
        f"{config.run_id}-5m-lb10.json",
        f"{config.run_id}-1H-lb30.json",
        f"{config.run_id}-D-lb200.json",
    }
    assert none_sidecars == noop_sidecars
    assert all(content and digest for content, digest in none_sidecars.values())
    chart_series_mod.clear_chart_memory_cache()


def test_excluded_day_is_not_reported_by_day_observer(tmp_path: Path) -> None:
    excluded = date(2026, 7, 21)
    fixture = _build_fixture(
        tmp_path,
        run_dates=(RUN_DATE, excluded),
        owner_decisions={excluded: "exclude"},
    )
    plan = _service(fixture).evaluate(_request(fixture)).plan_for(
        fixture.strategy_id,
        "NQ",
    )
    snapshots: list[DayProgressSnapshot] = []

    _runner_for_fixture(fixture, tmp_path)._replay(
        contract=plan.contract,
        config=_config_for_plan(plan, run_id="p4-progress-excluded"),
        day_observer=snapshots.append,
        progress_clock=lambda: FIXED_NOW,
    )

    assert plan.admitted_trading_dates == (RUN_DATE,)
    assert plan.excluded_trading_dates == (excluded,)
    assert [snapshot.current_trading_date for snapshot in snapshots] == [
        RUN_DATE
    ]
    assert sum(
        snapshot.current_trading_date == excluded for snapshot in snapshots
    ) == 0
    assert snapshots[0].total_trading_date_count == 1


def test_roll_blackout_day_is_not_reported_by_day_observer(
    tmp_path: Path,
) -> None:
    admitted = date(2026, 9, 17)
    blackout = date(2026, 9, 18)
    fixture = _build_fixture(
        tmp_path,
        run_dates=(admitted, blackout),
    )
    evaluation = _service(fixture).evaluate(_request(fixture))
    coverage = evaluation.document["units"][0]["coverage"]
    plan = evaluation.plan_for(fixture.strategy_id, "NQ")
    snapshots: list[DayProgressSnapshot] = []

    _runner_for_fixture(fixture, tmp_path)._replay(
        contract=plan.contract,
        config=_config_for_plan(plan, run_id="p4-progress-roll-blackout"),
        day_observer=snapshots.append,
        progress_clock=lambda: FIXED_NOW,
    )

    assert coverage["roll_blackout_trading_dates"] == [blackout.isoformat()]
    assert plan.admitted_trading_dates == (admitted,)
    assert plan.excluded_trading_dates == (blackout,)
    assert [snapshot.current_trading_date for snapshot in snapshots] == [
        admitted
    ]
    assert all(snapshot.current_trading_date != blackout for snapshot in snapshots)


def test_observer_failure_precedes_every_immutable_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    plan = _service(fixture).evaluate(_request(fixture)).plan_for(
        fixture.strategy_id,
        "NQ",
    )
    calls: list[str] = []

    def forbidden(name: str) -> Any:
        def fail(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            calls.append(name)
            raise AssertionError(f"{name} must not run after observer failure")

        return fail

    monkeypatch.setattr(SqliteRunStore, "persist", forbidden("run_store"))
    monkeypatch.setattr(ResultExporter, "stage_new", forbidden("result_exporter"))
    monkeypatch.setattr(
        BacktestRunner,
        "_materialize_chart_sidecars",
        forbidden("sidecars"),
    )

    def fail_observer(snapshot: DayProgressSnapshot) -> None:
        del snapshot
        raise BatchPersistenceError("fixture progress replace failed")

    with pytest.raises(BatchPersistenceError, match="progress replace failed"):
        _runner_for_fixture(fixture, tmp_path).run(
            contract=plan.contract,
            config=_config_for_plan(plan, run_id="p4-progress-persist-fail"),
            day_observer=fail_observer,
            progress_clock=lambda: FIXED_NOW,
        )

    assert calls == []
    assert not (tmp_path / "runs.sqlite3").exists()
    assert not (tmp_path / "results").exists()


def test_realized_progress_r_uses_net_pnl_not_gross(
    contracts_registry: Any,
) -> None:
    contract = contracts_registry.by_symbol("NQ")
    at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    trade = ExecutionTrade(
        contract_id=contract.contract_id,
        direction=Direction.LONG,
        signal_kind=SignalKind.INSIDE,
        quantity=1,
        signal_timestamp=at,
        entry_timestamp=at,
        entry_ts_init=at,
        exit_timestamp=at,
        exit_ts_init=at,
        entry_reference=100.0,
        entry_price=100.0,
        stop_price=98.0,
        target_price=104.0,
        exit_price=150.0,
        exit_reason=ExitReason.TARGET,
        gross_points=50.0,
        gross_pnl=1_000.0,
        total_commission=100.0,
        net_pnl=900.0,
        entry_slippage_ticks=0,
        exit_slippage_ticks=0,
    )

    metrics = calculate_realized_trade_metrics((trade,), contract=contract)

    assert metrics.trade_count == 1
    assert metrics.net_pnl == 900.0
    assert metrics.net_r == 900.0 / (
        abs(trade.entry_price - trade.stop_price)
        * contract.point_value
        * trade.quantity
    )
    assert metrics.net_r != trade.gross_pnl / (
        abs(trade.entry_price - trade.stop_price)
        * contract.point_value
        * trade.quantity
    )


def test_progress_baseline_each_day_and_completed_reload_are_exact(
    tmp_path: Path,
) -> None:
    queue = PausedBatchQueue(data_root=tmp_path, clock=lambda: FIXED_NOW)
    record = _queued_record(job_count=1, total=2)
    queue._persist_unlocked(record)
    queue._batches[record.batch_id] = record

    claimed, job = _claim(queue)
    assert claimed.status == "running"
    assert job.progress == JobProgress.baseline(
        total=2,
        reported_at="2026-07-27T08:05:00Z",
    )

    queue._record_day_progress(
        record.batch_id,
        job.job_id,
        _snapshot(processed=1, total=2),
    )
    first = queue.get(record.batch_id).jobs[0].progress
    assert first is not None
    assert first.current_trading_date == RUN_DATE.isoformat()
    assert first.processed_trading_date_count == 1

    queue._record_day_progress(
        record.batch_id,
        job.job_id,
        _snapshot(
            trading_date=date(2026, 7, 21),
            processed=2,
            total=2,
            trade_count=3,
            net_pnl=420.5,
            net_r=1.37,
        ),
    )
    queue._finalize_job(
        record.batch_id,
        job.job_id,
        status="completed",
        message="ok",
    )

    reloaded = PausedBatchQueue(data_root=tmp_path).get(record.batch_id)
    final = reloaded.jobs[0]
    assert reloaded.status == "completed"
    assert final.status == "completed"
    assert final.progress is not None
    assert final.progress.to_dict() == {
        "current_trading_date": "2026-07-21",
        "processed_trading_date_count": 2,
        "total_trading_date_count": 2,
        "trade_count": 3,
        "realized_net_pnl_usd": 420.5,
        "realized_net_r": 1.37,
        "reported_at": "2026-07-27T08:05:00Z",
    }


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("trade_count", 9),
        ("realized_net_pnl_usd", 1234.5),
        ("realized_net_pnl_usd", -1234.5),
        ("realized_net_r", 7.25),
        ("realized_net_r", -7.25),
    ],
)
def test_first_day_baseline_rejects_every_nonzero_cumulative_fact(
    field_name: str,
    invalid_value: int | float,
) -> None:
    payload: dict[str, Any] = {
        "current_trading_date": None,
        "processed_trading_date_count": 0,
        "total_trading_date_count": 2,
        "trade_count": 0,
        "realized_net_pnl_usd": 0.0,
        "realized_net_r": 0.0,
        "reported_at": UPDATED_AT,
    }
    payload[field_name] = invalid_value

    with pytest.raises(ValueError, match="exact zero trade, PnL, and R"):
        JobProgress(**payload)
    with pytest.raises(ValueError, match="exact zero trade, PnL, and R"):
        JobProgress.from_dict(payload)


def test_first_day_baseline_accepts_only_the_exact_zero_contract() -> None:
    expected = {
        "current_trading_date": None,
        "processed_trading_date_count": 0,
        "total_trading_date_count": 2,
        "trade_count": 0,
        "realized_net_pnl_usd": 0.0,
        "realized_net_r": 0.0,
        "reported_at": UPDATED_AT,
    }

    assert JobProgress.baseline(total=2, reported_at=UPDATED_AT).to_dict() == expected
    assert JobProgress.from_dict(expected).to_dict() == expected


def test_progress_rejects_non_monotonic_or_total_drift(tmp_path: Path) -> None:
    queue = PausedBatchQueue(data_root=tmp_path, clock=lambda: FIXED_NOW)
    record = _queued_record(job_count=1, total=2)
    queue._persist_unlocked(record)
    queue._batches[record.batch_id] = record
    _, job = _claim(queue)

    for bad in (
        _snapshot(processed=2, total=2),
        _snapshot(processed=1, total=3),
    ):
        with pytest.raises(
            BatchOperationalStateError,
            match="monotonic|processed count one",
        ):
            queue._record_day_progress(record.batch_id, job.job_id, bad)

    current = queue.get(record.batch_id).jobs[0].progress
    assert current is not None
    assert current.processed_trading_date_count == 0
    assert current.total_trading_date_count == 2


def test_public_assumptions_are_exact_and_capital_is_not_split() -> None:
    record = _queued_record(job_count=2, assumptions=True)
    second = record.jobs[1].execution_assumptions
    assert second is not None
    second["commission_per_side"] = 3.75

    document = record.to_dict()

    for index, job in enumerate(document["jobs"]):
        expected = deepcopy(PUBLIC_ASSUMPTIONS)
        expected["commission_per_side"] = (2.5, 3.75)[index]
        assert job["assumptions"] == expected
        assert set(job["assumptions"]) == {
            "initial_capital_usd",
            "commission_per_side",
            "slippage_ticks",
            "target_requires_through",
            "fill_model",
            "bar_precision",
        }
        assert job["assumptions"]["initial_capital_usd"] == 100_000.0
        assert "simulation_precision" not in job["assumptions"]
        assert "quantity" not in job["assumptions"]
    assert [
        job["assumptions"]["commission_per_side"]
        for job in document["jobs"]
    ] == [2.5, 3.75]


def test_five_count_summary_and_terminal_mix_reducer() -> None:
    finished = JobProgress.from_day_snapshot(
        _snapshot(processed=2, total=2)
    )
    completed = RunJob(
        "job-completed",
        "run-completed",
        "NQ",
        "strategy-0001",
        status="completed",
        message="ok",
        started_at=CREATED_AT,
        finished_at=UPDATED_AT,
        progress=finished,
    )
    cancelled = RunJob(
        "job-cancelled",
        "run-cancelled",
        "NQ",
        "strategy-0002",
        status="cancelled",
        finished_at=UPDATED_AT,
        cancelled_at=UPDATED_AT,
    )
    record = BatchRecord(
        "batch-terminal-mix",
        "partial",
        CREATED_AT,
        UPDATED_AT,
        {},
        [completed, cancelled],
    )

    document = record.to_dict()

    assert document["status"] == "partial"
    assert document["summary"] == {
        "total": 2,
        "queued": 0,
        "running": 0,
        "completed": 1,
        "failed": 0,
        "cancelled": 1,
    }


def test_cancel_first_prevents_claim_and_is_idempotent(tmp_path: Path) -> None:
    queue = PausedBatchQueue(data_root=tmp_path, clock=lambda: FIXED_NOW)
    record = _queued_record()
    queue._persist_unlocked(record)
    queue._batches[record.batch_id] = record

    cancelled = queue.cancel_queued(record.batch_id)
    path = tmp_path / "jobs" / "batches" / f"{record.batch_id}.json"
    first_bytes = path.read_bytes()
    second = queue.cancel_queued(record.batch_id)
    second_bytes = path.read_bytes()
    with queue._lock:
        claimed = queue._claim_next_job_unlocked()

    assert cancelled.status == "cancelled"
    assert cancelled.to_dict()["summary"]["cancelled"] == 2
    assert claimed is None
    assert second_bytes == first_bytes
    assert second.updated_at == cancelled.updated_at
    for job in second.jobs:
        assert job.status == "cancelled"
        assert job.started_at is None
        assert job.finished_at == job.cancelled_at == "2026-07-27T08:05:00Z"
        assert job.progress is None


def test_worker_claim_first_preserves_running_and_cancels_only_rest(
    tmp_path: Path,
) -> None:
    queue = PausedBatchQueue(data_root=tmp_path, clock=lambda: FIXED_NOW)
    record = _queued_record()
    queue._persist_unlocked(record)
    queue._batches[record.batch_id] = record

    _, running = _claim(queue)
    latest = queue.cancel_queued(record.batch_id)

    assert [job.status for job in latest.jobs] == ["running", "cancelled"]
    assert latest.jobs[0].job_id == running.job_id
    assert latest.jobs[0].started_at == "2026-07-27T08:05:00Z"
    assert latest.jobs[0].cancelled_at is None
    assert latest.jobs[1].started_at is None
    assert latest.status == "running"
    assert latest.to_dict()["summary"] == {
        "total": 2,
        "queued": 0,
        "running": 1,
        "completed": 0,
        "failed": 0,
        "cancelled": 1,
    }


def test_cancel_preserves_completed_failed_cancelled_and_their_progress(
    tmp_path: Path,
) -> None:
    final_progress = JobProgress.from_day_snapshot(
        _snapshot(processed=2, total=2)
    )
    first_progress = JobProgress.from_day_snapshot(
        _snapshot(processed=1, total=2)
    )
    jobs = [
        RunJob(
            "job-queued",
            "run-queued",
            "NQ",
            "strategy-0001",
            planned_trading_date_count=2,
        ),
        RunJob(
            "job-completed",
            "run-completed",
            "NQ",
            "strategy-0002",
            status="completed",
            message="ok",
            started_at=CREATED_AT,
            finished_at=UPDATED_AT,
            progress=final_progress,
        ),
        RunJob(
            "job-failed",
            "run-failed",
            "NQ",
            "strategy-0003",
            status="failed",
            message="worker_failed:internal_error",
            started_at=CREATED_AT,
            finished_at=UPDATED_AT,
            progress=first_progress,
            error_summary="worker_failed:internal_error",
            error_full="RuntimeError: sanitized",
        ),
        RunJob(
            "job-cancelled",
            "run-cancelled",
            "NQ",
            "strategy-0004",
            status="cancelled",
            finished_at=UPDATED_AT,
            cancelled_at=UPDATED_AT,
        ),
    ]
    record = BatchRecord(
        "batch-cancel-terminal-fixture",
        "running",
        CREATED_AT,
        UPDATED_AT,
        {},
        jobs,
    )
    queue = PausedBatchQueue(data_root=tmp_path, clock=lambda: FIXED_NOW)
    queue._persist_unlocked(record)
    queue._batches[record.batch_id] = record
    terminal_before = [job.to_dict() for job in jobs[1:]]

    latest = queue.cancel_queued(record.batch_id)

    assert latest.jobs[0].status == "cancelled"
    assert [job.to_dict() for job in latest.jobs[1:]] == terminal_before
    assert latest.status == "partial"
    assert latest.to_dict()["summary"] == {
        "total": 4,
        "queued": 0,
        "running": 0,
        "completed": 1,
        "failed": 1,
        "cancelled": 2,
    }


def test_summary_conservation_holds_after_each_operational_transition(
    tmp_path: Path,
) -> None:
    queue = PausedBatchQueue(data_root=tmp_path, clock=lambda: FIXED_NOW)
    record = _queued_record(job_count=2, total=1)
    queue._persist_unlocked(record)
    queue._batches[record.batch_id] = record

    def assert_conserved() -> None:
        summary = queue.get(record.batch_id).to_dict()["summary"]
        assert (
            summary["queued"]
            + summary["running"]
            + summary["completed"]
            + summary["failed"]
            + summary["cancelled"]
            == summary["total"]
        )

    assert_conserved()
    _, running = _claim(queue)
    assert_conserved()
    queue._record_day_progress(
        record.batch_id,
        running.job_id,
        _snapshot(processed=1, total=1),
    )
    assert_conserved()
    queue._finalize_job(
        record.batch_id,
        running.job_id,
        status="completed",
        message="ok",
    )
    assert_conserved()
    queue.cancel_queued(record.batch_id)
    assert_conserved()
    assert queue.get(record.batch_id).status == "partial"


@pytest.mark.parametrize("transition", ["claim", "progress", "cancel"])
def test_transition_replace_failure_rolls_back_memory_disk_and_tmp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    queue = PausedBatchQueue(data_root=tmp_path, clock=lambda: FIXED_NOW)
    record = _queued_record(job_count=1, total=2)
    queue._persist_unlocked(record)
    queue._batches[record.batch_id] = record
    if transition == "progress":
        _, job = _claim(queue)
    else:
        job = record.jobs[0]
    path = tmp_path / "jobs" / "batches" / f"{record.batch_id}.json"
    before_bytes = path.read_bytes()
    before_public = queue.get(record.batch_id).to_dict()
    original_replace = Path.replace

    def fail_replace(self: Path, target: Path) -> Path:
        if self.parent == path.parent and self.suffix == ".tmp":
            raise OSError("fixture replace failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(BatchPersistenceError):
        if transition == "claim":
            with queue._lock:
                queue._claim_next_job_unlocked()
        elif transition == "progress":
            queue._record_day_progress(
                record.batch_id,
                job.job_id,
                _snapshot(processed=1, total=2),
            )
        else:
            queue.cancel_queued(record.batch_id)

    assert queue.get(record.batch_id).to_dict() == before_public
    assert path.read_bytes() == before_bytes
    assert list(path.parent.glob("*.tmp")) == []


def test_reload_retains_cancel_error_and_last_progress(tmp_path: Path) -> None:
    first_progress = JobProgress.from_day_snapshot(
        _snapshot(processed=1, total=2)
    )
    final_progress = JobProgress.from_day_snapshot(
        _snapshot(
            trading_date=date(2026, 7, 21),
            processed=2,
            total=2,
        )
    )
    jobs = [
        RunJob(
            "job-failed",
            "run-failed",
            "NQ",
            "strategy-0001",
            status="failed",
            message="worker_failed:internal_error",
            started_at=CREATED_AT,
            finished_at=UPDATED_AT,
            progress=first_progress,
            error_summary="worker_failed:internal_error",
            error_full="RuntimeError: sanitized\nCaused by: OSError: hidden",
        ),
        RunJob(
            "job-completed",
            "run-completed",
            "NQ",
            "strategy-0002",
            status="completed",
            message="ok",
            started_at=CREATED_AT,
            finished_at=UPDATED_AT,
            progress=final_progress,
        ),
        RunJob(
            "job-cancelled",
            "run-cancelled",
            "NQ",
            "strategy-0003",
            status="cancelled",
            finished_at=UPDATED_AT,
            cancelled_at=UPDATED_AT,
        ),
    ]
    record = BatchRecord(
        "batch-reload-fixture",
        "partial",
        CREATED_AT,
        UPDATED_AT,
        {},
        jobs,
    )
    writer = PausedBatchQueue(data_root=tmp_path)
    writer._persist_unlocked(record)

    reloaded = PausedBatchQueue(data_root=tmp_path).get(record.batch_id)

    assert reloaded.status == "partial"
    assert reloaded.jobs[0].progress == first_progress
    assert reloaded.jobs[0].error_full == (
        "RuntimeError: sanitized\nCaused by: OSError: hidden"
    )
    assert reloaded.jobs[1].progress == final_progress
    assert reloaded.jobs[2].status == "cancelled"
    assert reloaded.jobs[2].cancelled_at == UPDATED_AT


def _legacy_document(
    *,
    batch_id: str,
    status: str = "completed",
    job_status: str = "completed",
) -> dict[str, Any]:
    started_at = CREATED_AT if job_status != "queued" else None
    finished_at = UPDATED_AT if job_status in {"completed", "failed"} else None
    counts = {
        "queued": int(job_status == "queued"),
        "running": int(job_status == "running"),
        "completed": int(job_status == "completed"),
        "failed": int(job_status == "failed"),
    }
    return {
        "schema": "batch_job.v1",
        "batch_id": batch_id,
        "status": status,
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
        "request": {},
        "jobs": [
            {
                "job_id": "legacy-job-1",
                "run_id": "legacy-run-1",
                "symbol": "NQ",
                "strategy_version": "strategy-0001",
                "status": job_status,
                "message": "ok" if job_status == "completed" else "",
                "started_at": started_at,
                "finished_at": finished_at,
                "result_path": (
                    "C:\\legacy\\result.json"
                    if job_status == "completed"
                    else None
                ),
                "strategy_source": "strategy_file",
                "warnings": [],
                "execution_assumptions": deepcopy(INTERNAL_ASSUMPTIONS),
            }
        ],
        "summary": {"total": 1, **counts},
    }


def test_v1_read_normalizes_to_v2_without_changing_bytes_hash_or_mtime(
    tmp_path: Path,
) -> None:
    batch_id = "batch-legacy-fixture"
    path = tmp_path / "jobs" / "batches" / f"{batch_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(_legacy_document(batch_id=batch_id), indent=2) + "\n",
        encoding="utf-8",
    )
    before = path.read_bytes()
    before_hash = hashlib.sha256(before).hexdigest()
    before_mtime = path.stat().st_mtime_ns

    normalized = PausedBatchQueue(data_root=tmp_path).get(batch_id).to_dict()

    assert normalized["schema"] == "batch_job.v2"
    assert normalized["summary"]["cancelled"] == 0
    assert normalized["jobs"][0]["progress"] is None
    assert normalized["jobs"][0]["error_summary"] is None
    assert normalized["jobs"][0]["error_full"] is None
    assert normalized["jobs"][0]["cancelled_at"] is None
    assert normalized["jobs"][0]["assumptions"] == PUBLIC_ASSUMPTIONS
    assert path.read_bytes() == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_hash
    assert path.stat().st_mtime_ns == before_mtime


def test_v1_unprovable_assumptions_normalize_to_honest_null(
    tmp_path: Path,
) -> None:
    batch_id = "batch-legacy-unproven-assumptions"
    document = _legacy_document(batch_id=batch_id)
    document["jobs"][0]["execution_assumptions"] = "legacy-opaque-value"
    path = tmp_path / "jobs" / "batches" / f"{batch_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()

    normalized = PausedBatchQueue(data_root=tmp_path).get(batch_id).to_dict()

    assert normalized["jobs"][0]["assumptions"] is None
    assert "execution_assumptions" not in normalized["jobs"][0]
    assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "unknown_status",
        "summary_drift",
        "unknown_schema",
        "duplicate_identity",
        "v2_summary_drift",
        "nonzero_baseline",
    ],
)
async def test_corrupt_operational_document_fails_closed_503_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    batch_id = "batch-corrupt-fixture"
    document = _legacy_document(batch_id=batch_id)
    if corruption == "unknown_status":
        document["jobs"][0]["status"] = "mystery"
        document["jobs"][0]["message"] = ""
        document["jobs"][0]["started_at"] = None
        document["jobs"][0]["finished_at"] = None
        document["jobs"][0]["result_path"] = None
        document["status"] = "queued"
        document["summary"]["queued"] = 1
        document["summary"]["completed"] = 0
    elif corruption == "summary_drift":
        document["summary"]["completed"] = 0
        document["summary"]["queued"] = 1
    elif corruption == "unknown_schema":
        document["schema"] = "batch_job.v0"
    elif corruption == "v2_summary_drift":
        document = _queued_record(batch_id=batch_id, job_count=1).to_dict()
        document["summary"]["queued"] = 0
    elif corruption == "nonzero_baseline":
        document = _queued_record(batch_id=batch_id, job_count=1).to_dict()
        document["status"] = "running"
        document["updated_at"] = UPDATED_AT
        document["summary"]["queued"] = 0
        document["summary"]["running"] = 1
        document["jobs"][0]["status"] = "running"
        document["jobs"][0]["started_at"] = CREATED_AT
        document["jobs"][0]["progress"] = {
            **JobProgress.baseline(total=2, reported_at=UPDATED_AT).to_dict(),
            "trade_count": 9,
            "realized_net_pnl_usd": 1234.5,
            "realized_net_r": 7.25,
        }
    else:
        duplicate = deepcopy(document["jobs"][0])
        document["jobs"].append(duplicate)
        document["summary"]["total"] = 2
        document["summary"]["completed"] = 2
    path = tmp_path / "jobs" / "batches" / f"{batch_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    queue = PausedBatchQueue(data_root=tmp_path)
    _install_queue(monkeypatch, queue)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        detail = await client.get(f"/api/v1/batches/jobs/{batch_id}")
        listing = await client.get("/api/v1/batches/jobs")

    assert detail.status_code == 503
    assert listing.status_code == 503
    assert detail.json() == {
        "detail": "batch operational state is unavailable"
    }
    assert listing.json() == {
        "detail": "batch operational state is unavailable"
    }
    assert queue._batches == {}
    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_list_detail_and_cancel_routes_are_exact_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = PausedBatchQueue(data_root=tmp_path, clock=lambda: FIXED_NOW)
    record = _queued_record(job_count=1, assumptions=True)
    queue._persist_unlocked(record)
    queue._batches[record.batch_id] = record
    _install_queue(monkeypatch, queue)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        listing = await client.get("/api/v1/batches/jobs")
        detail = await client.get(f"/api/v1/batches/jobs/{record.batch_id}")
        cancelled = await client.post(
            f"/api/v1/batches/jobs/{record.batch_id}/cancel-queued"
        )

    assert listing.status_code == detail.status_code == cancelled.status_code == 200
    assert listing.json()["schema"] == "batch_job_list.v2"
    assert listing.json()["batches"][0]["schema"] == "batch_job.v2"
    assert detail.json()["schema"] == "batch_job.v2"
    assert cancelled.json()["schema"] == "batch_job.v2"
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["summary"]["cancelled"] == 1
    assert set(cancelled.json()["jobs"][0]) == {
        "job_id",
        "run_id",
        "symbol",
        "strategy_version",
        "status",
        "message",
        "started_at",
        "finished_at",
        "result_path",
        "strategy_source",
        "warnings",
        "progress",
        "error_summary",
        "error_full",
        "cancelled_at",
        "assumptions",
        "execution_assumptions",
    }


@pytest.mark.asyncio
async def test_cancel_unknown_is_404_and_replace_failure_is_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = PausedBatchQueue(data_root=tmp_path, clock=lambda: FIXED_NOW)
    record = _queued_record(job_count=1)
    queue._persist_unlocked(record)
    queue._batches[record.batch_id] = record
    _install_queue(monkeypatch, queue)
    path = tmp_path / "jobs" / "batches" / f"{record.batch_id}.json"
    original_replace = Path.replace

    def fail_replace(self: Path, target: Path) -> Path:
        if self.parent == path.parent and self.suffix == ".tmp":
            raise OSError("fixture replace failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        unknown = await client.post(
            "/api/v1/batches/jobs/missing/cancel-queued"
        )
        failed = await client.post(
            f"/api/v1/batches/jobs/{record.batch_id}/cancel-queued"
        )

    assert unknown.status_code == 404
    assert failed.status_code == 503
    assert failed.json() == {
        "detail": "batch operational state is unavailable"
    }
    assert queue.get(record.batch_id).status == "queued"
    assert list(path.parent.glob("*.tmp")) == []


def test_error_chain_keeps_types_and_causes_but_removes_ui_html_paths_and_lines() -> None:
    inner_path = r"C:\Users\Carlos\Secret\market.arrow"
    outer_path = "/srv/private/worker.py:417"
    single_component_path = "/secret.txt"
    try:
        try:
            raise FileNotFoundError(
                f"<b>UI:</b> {inner_path} {single_component_path} line 91"
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"frontend: {outer_path}") from exc
    except RuntimeError as error:
        public = batch_queue_mod._sanitized_exception_chain(error)

    assert "RuntimeError" in public
    assert "Caused by: FileNotFoundError" in public
    for forbidden in (
        inner_path,
        outer_path,
        single_component_path,
        "<b>",
        "</b>",
        "UI:",
        "frontend:",
        "line 91",
        ":417",
        "Traceback",
    ):
        assert forbidden not in public
