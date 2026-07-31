"""Golden fixtures for WO-002's immutable run record and result artifacts."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from futures_research.backtest import persistence as persistence_module
from futures_research.backtest.execution import ConservativeExecution, ExecutionTrade
from futures_research.backtest.persistence import (
    ImmutableArtifactError,
    ResultExporter,
    SqliteRunStore,
)
from futures_research.backtest.records import (
    RecordTagInputs,
    RunResult,
    build_run_result,
    build_trade_records,
    fingerprint_canonical_bars,
    prepare_run,
)
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    EventPhase,
    SignalKind,
    StrategyEvent,
    StrategyEventType,
)
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.models import CanonicalBar

_START = datetime(2026, 7, 20, 14, 30, tzinfo=UTC)


def test_prepare_run_excludes_eth_roll_blackout_and_freezes_audit_fingerprint(
    contracts_registry: ContractRegistry,
) -> None:
    """The S4 gate uses exchange trading dates rather than UTC calendar dates."""
    contract = contracts_registry.by_symbol("NQ")
    safe_bar = _bar(
        contract,
        datetime(2026, 9, 15, 22, 0, tzinfo=UTC),
        open_price=100.0,
        high=101.0,
        low=99.5,
        close=100.5,
    )
    expiry_session_bar = _bar(
        contract,
        datetime(2026, 9, 16, 22, 0, tzinfo=UTC),
        open_price=101.0,
        high=102.0,
        low=100.5,
        close=101.5,
    )

    prepared = prepare_run(
        run_id="roll-gate-001",
        strategy_version="trend-v0",
        contract=contract,
        session_name="eth",
        range_start=safe_bar.timestamp,
        range_end=expiry_session_bar.timestamp + timedelta(minutes=1),
        initial_capital=100_000.0,
        quantity=1,
        canonical_bars=(safe_bar, expiry_session_bar),
        source_partitions=("contract_id=NQ-202609-CME/year=2026/month=09",),
        quality_report_ids=("quality-nq-202609-001",),
        created_at=_START,
    )

    assert prepared.bars == (safe_bar,)
    assert prepared.manifest.excluded_trading_dates == (date(2026, 9, 17),)
    assert prepared.manifest.data_fingerprint.bar_count == 1
    assert prepared.manifest.data_fingerprint.source_partitions == (
        "contract_id=NQ-202609-CME/year=2026/month=09",
    )
    assert prepared.manifest.data_fingerprint.quality_report_ids == ("quality-nq-202609-001",)
    with pytest.raises(ValidationError, match="frozen"):
        prepared.manifest.initial_capital = 1.0
    changed_bar = safe_bar.model_copy(update={"close": 100.75})
    assert (
        fingerprint_canonical_bars((safe_bar,)).digest
        != fingerprint_canonical_bars((changed_bar,)).digest
    )


def test_trade_records_capture_full_tag_surface_and_gap_through_target(
    contracts_registry: ContractRegistry,
) -> None:
    """The record layer preserves P1 facts and explicit P2 nulls without gating trades."""
    contract = contracts_registry.by_symbol("NQ")
    trade, events, bars = _completed_gap_target_execution(contract)
    signal_event = StrategyEvent(
        sequence=99,
        timestamp=trade.signal_timestamp,
        ts_init=trade.entry_timestamp,
        phase=EventPhase.CLOSE,
        machine="entry_signal",
        event_type=StrategyEventType.SIGNAL_CREATED,
        from_state="awaiting_signal",
        to_state="pending",
        direction=Direction.LONG,
        price=100.0,
        details={"signal_kind": "inside", "inside_count": 2},
    )
    records = build_trade_records(
        run_id="tags-001",
        trades=(trade,),
        events=(signal_event, *events),
        canonical_bars=bars,
        contract=contract,
        session_name="eth",
        default_inputs=RecordTagInputs(
            daily_regime="trend",
            regime_strength="strong",
            entry_layers=3,
            has_sweep_bonus=None,
            lmr_step1_leg_atr=None,
            lmr_step2_leg_atr=None,
            atr_expansion_ratio=1.3,
            volatility_owner_view="high",
            volatility_system_daily_atr_percentile=82.5,
            volatility_system_range_ratio=1.4,
        ),
    )

    assert len(records) == 1
    record = records[0]
    assert record.trade_id == "tags-001-00001"
    assert record.tags.signal_kind is SignalKind.INSIDE
    assert record.tags.inside_count == 2
    assert record.tags.multiple_inside is True
    assert record.tags.gap_through_target is True
    assert record.tags.mfe_r == pytest.approx((106.0 - 100.25) / 5.25)
    assert record.tags.mae_r == pytest.approx(0.0)
    assert record.tags.has_sweep_bonus is None
    assert record.tags.lmr_step1_leg_atr is None
    assert record.tags.lmr_step2_leg_atr is None
    assert record.tags.volatility_owner_view == "high"
    assert record.tags.volatility_system_daily_atr_percentile == pytest.approx(82.5)
    assert record.tags.volatility_system_range_ratio == pytest.approx(1.4)
    assert record.tags.volatility_actual_daily_range == pytest.approx(6.75)
    assert set(record.tags.model_dump(mode="json")) == {
        "signal_kind",
        "daily_regime",
        "regime_strength",
        "entry_session",
        "entry_local_time",
        "entry_layers",
        "inside_count",
        "multiple_inside",
        "has_sweep_bonus",
        "lmr_step1_leg_atr",
        "lmr_step2_leg_atr",
        "atr_expansion_ratio",
        "mfe_r",
        "mae_r",
        "gap_through_target",
        "volatility_owner_view",
        "volatility_system_daily_atr_percentile",
        "volatility_system_range_ratio",
        "volatility_actual_daily_range",
    }


def test_sqlite_run_artifacts_are_append_only_and_result_sidecars_are_regenerable(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """A4/A5 cannot be changed, while A6 remains safely exportable from them."""
    contract = contracts_registry.by_symbol("NQ")
    result = _result_fixture(contract)
    database_path = tmp_path / "records" / "runs.sqlite3"
    store = SqliteRunStore(database_path)

    store.persist(
        result,
        contract=contract,
        consumed_trading_dates=(date(2026, 7, 20),),
    )
    loaded = store.load(result.run_id)

    assert store.list_run_ids() == (result.run_id,)
    assert loaded.manifest["schema"] == "run_manifest.v1"
    fingerprint = loaded.manifest["data_fingerprint"]
    assert isinstance(fingerprint, dict)
    assert fingerprint["bar_count"] == 2
    assert loaded.result["schema"] == "run_result.v1"
    tags = loaded.trades[0]["tags"]
    assert isinstance(tags, dict)
    assert tags["gap_through_target"] is True
    with sqlite3.connect(database_path) as connection:
        lookup = connection.execute(
            """
            SELECT validation_run, symbol, trading_dates_status
            FROM run_lookup WHERE run_id = ?
            """,
            (result.run_id,),
        ).fetchone()
        dates = connection.execute(
            "SELECT trading_date FROM run_trading_dates WHERE run_id = ?",
            (result.run_id,),
        ).fetchall()
    assert lookup == (0, "NQ", "complete")
    assert dates == [("2026-07-20",)]
    with pytest.raises(ImmutableArtifactError, match="already exists"):
        store.persist(
            result,
            contract=contract,
            consumed_trading_dates=(date(2026, 7, 20),),
        )

    with (
        sqlite3.connect(database_path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="runs are immutable"),
    ):
        connection.execute("UPDATE runs SET completed_at = 'changed'")

    exporter = ResultExporter(tmp_path / "results")
    exported = exporter.export(result)
    main_document = json.loads(exported.result_path.read_text(encoding="utf-8"))
    trades_document = json.loads(exported.trades_path.read_text(encoding="utf-8"))
    events_document = json.loads(exported.events_path.read_text(encoding="utf-8"))

    assert main_document["schema"] == "result.v1"
    assert main_document["trades_ref"] == f"trades/{result.run_id}.json"
    assert main_document["equity_curve_ref"] == f"equity/{result.run_id}.json"
    assert main_document["events_ref"] == f"events/{result.run_id}.json"
    assert trades_document["trades"][0]["tags"]["gap_through_target"] is True
    assert events_document["events"][-1]["details"]["fill_mode"] == "gap_open"

    regenerated = exporter.export(result)
    assert regenerated.result_path.read_text(encoding="utf-8") == exported.result_path.read_text(
        encoding="utf-8"
    )


def test_index_write_failure_rolls_back_run_trade_lookup_and_dates(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The derived date insert is inside the same commit boundary as immutable facts."""
    contract = contracts_registry.by_symbol("NQ")
    result = _result_fixture(contract)
    database_path = tmp_path / "atomic" / "runs.sqlite3"
    store = SqliteRunStore(database_path)

    def fail_second_index_write(
        connection: sqlite3.Connection,
        run_id: str,
        values: object,
    ) -> None:
        del connection, run_id, values
        raise OSError("forced run_trading_dates failure")

    monkeypatch.setattr(
        persistence_module,
        "_insert_run_trading_dates",
        fail_second_index_write,
    )
    with pytest.raises(OSError, match="forced run_trading_dates failure"):
        store.persist(
            result,
            contract=contract,
            consumed_trading_dates=(date(2026, 7, 20),),
        )

    with sqlite3.connect(database_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("runs", "trades", "run_lookup", "run_trading_dates")
        }
    assert counts == {
        "runs": 0,
        "trades": 0,
        "run_lookup": 0,
        "run_trading_dates": 0,
    }


def test_all_immutable_run_and_trade_triggers_still_abort(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """Derived indexes must not weaken any of the four immutable truth guards."""
    contract = contracts_registry.by_symbol("NQ")
    result = _result_fixture(contract)
    database_path = tmp_path / "guards" / "runs.sqlite3"
    store = SqliteRunStore(database_path)
    store.persist(
        result,
        contract=contract,
        consumed_trading_dates=(date(2026, 7, 20),),
    )

    with sqlite3.connect(database_path) as connection:
        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert trigger_names >= {
            "runs_are_immutable",
            "runs_cannot_be_deleted",
            "trades_are_immutable",
            "trades_cannot_be_deleted",
        }
        with pytest.raises(sqlite3.IntegrityError, match="runs are immutable"):
            connection.execute("UPDATE runs SET completed_at = 'changed'")
        with pytest.raises(sqlite3.IntegrityError, match="runs are immutable"):
            connection.execute("DELETE FROM runs")
        with pytest.raises(sqlite3.IntegrityError, match="trades are immutable"):
            connection.execute("UPDATE trades SET net_pnl = 1")
        with pytest.raises(sqlite3.IntegrityError, match="trades are immutable"):
            connection.execute("DELETE FROM trades")


def _result_fixture(contract: ContractSpec) -> RunResult:
    """Build a deterministic completed A4/A5 fixture through real execution semantics."""
    trade, events, bars = _completed_gap_target_execution(contract)
    prepared = prepare_run(
        run_id="result-001",
        strategy_version="trend-v0",
        contract=contract,
        session_name="eth",
        range_start=bars[0].timestamp,
        range_end=bars[-1].timestamp + timedelta(minutes=1),
        initial_capital=100_000.0,
        quantity=1,
        canonical_bars=bars,
        source_partitions=("contract_id=NQ-202609-CME/year=2026/month=07",),
        quality_report_ids=("quality-nq-202607-001",),
        created_at=_START,
    )
    records = build_trade_records(
        run_id=prepared.manifest.run_id,
        trades=(trade,),
        events=events,
        canonical_bars=prepared.bars,
        contract=contract,
        session_name="eth",
    )
    return build_run_result(
        manifest=prepared.manifest,
        trade_records=records,
        event_log=events,
        contract=contract,
        completed_at=_START + timedelta(minutes=2),
    )


def _completed_gap_target_execution(
    contract: ContractSpec,
) -> tuple[ExecutionTrade, tuple[StrategyEvent, ...], tuple[CanonicalBar, ...]]:
    """Use the approved §14 engine to create one target-gapping trade for artifacts."""
    execution = ConservativeExecution(contract, quantity=1)
    execution.arm(
        EntryIntent(
            direction=Direction.LONG,
            entry_reference=100.0,
            stop_reference=95.0,
            signal_kind=SignalKind.INSIDE,
            signal_timestamp=_START - timedelta(minutes=5),
            timestamp=_START,
            ts_init=_START + timedelta(minutes=5),
        )
    )
    entry_bar = _bar(
        contract,
        _START,
        open_price=99.75,
        high=100.0,
        low=99.5,
        close=100.0,
    )
    target_gap_bar = _bar(
        contract,
        _START + timedelta(minutes=1),
        open_price=106.0,
        high=106.25,
        low=105.75,
        close=106.0,
    )
    execution.process_minute(entry_bar)
    execution.process_minute(target_gap_bar)
    assert len(execution.trades) == 1
    return execution.trades[0], execution.event_log, (entry_bar, target_gap_bar)


def _bar(
    contract: ContractSpec,
    timestamp: datetime,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> CanonicalBar:
    """Create one canonical fixture minute with a contract-consistent identity."""
    return CanonicalBar(
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100,
        contract_id=contract.contract_id,
        source="fixture",
    )
