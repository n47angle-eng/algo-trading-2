"""Batch 3 causal-evidence, atomic-publication, and legacy-read regressions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from futures_research.api.results_catalog import ResultsCatalog
from futures_research.backtest import persistence as persistence_module
from futures_research.backtest.evidence import (
    ConditionFact,
    ConditionObservation,
    EntryDecisionCapture,
    EvidenceValidationError,
    RejectionCapture,
    SignalEvaluationContext,
    build_evidence_summary,
    build_rejection_evidence,
    validate_evidence_bundle,
)
from futures_research.backtest.execution import ConservativeExecution
from futures_research.backtest.persistence import (
    ImmutableArtifactError,
    ResultExporter,
    SqliteRunStore,
)
from futures_research.backtest.records import (
    RecordedTrade,
    build_run_result,
    build_trade_records,
    prepare_run,
    trading_date_for_timestamp,
)
from futures_research.backtest.runner import BacktestRunConfig, BacktestRunner, _resequence_events
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    EventPhase,
    SignalKind,
    StrategyEvent,
    StrategyEventType,
)
from futures_research.data.contracts import ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CanonicalStore

_START = datetime(2026, 7, 20, 14, 30, tzinfo=UTC)


def test_trade_decision_evidence_preserves_all_same_bar_candidates_and_native_facts(
    contracts_registry,
    tmp_path: Path,
) -> None:
    """The four evidence segments are captured by real execution, not inferred from charts."""
    contract = contracts_registry.by_symbol("NQ")
    records, events, bars = _completed_trade_records(contract)
    assert len(records) == 1
    evidence = records[0].decision_evidence
    assert evidence is not None

    assert evidence.trade_id == records[0].trade_id
    assert evidence.ordinal == 1
    assert evidence.entry.signal_kind == "inside"
    assert evidence.entry.entry_reference == 100.0
    assert evidence.entry.fill_price == pytest.approx(100.25)
    assert evidence.entry.condition_facts[0].actual is True
    assert evidence.entry.condition_facts[0].required is True
    assert type(evidence.entry.condition_facts[0].actual) is bool
    assert evidence.entry.condition_facts[0].unit == "boolean"
    assert evidence.stop.reference_type == "mother_low"
    assert evidence.stop.reference_price == pytest.approx(95.25)
    assert evidence.stop.offset_ticks == 1
    assert evidence.stop.final_stop_price == pytest.approx(95.0)
    stop_facts = {fact.condition_id: fact for fact in evidence.stop.condition_facts}
    assert stop_facts["stop_reference_price"].actual == pytest.approx(95.25)
    assert stop_facts["stop_offset_ticks"].actual == 1
    assert stop_facts["stop_final_price"].actual == pytest.approx(95.0)
    assert evidence.exit.reason == "stop"
    assert evidence.exit.resolution_code == "same_minute_stop_first"
    assert evidence.exit.selected_candidate_id == "stop"
    assert {
        candidate.candidate_id for candidate in evidence.exit.candidates
    } == {"stop", "target"}
    assert all(candidate.triggered for candidate in evidence.exit.candidates)
    assumptions = {item.code: item for item in evidence.conservative_assumptions}
    assert assumptions["same_minute_stop_first"].applied is True
    assert assumptions["same_minute_stop_first"].effects == (
        "exit_reason",
        "exit_price",
        "event_order",
    )

    prepared = prepare_run(
        run_id="evidence-trade-001",
        strategy_version="trend-v0",
        contract=contract,
        session_name="eth",
        range_start=bars[0].timestamp,
        range_end=bars[-1].timestamp + timedelta(minutes=1),
        initial_capital=100_000.0,
        quantity=1,
        canonical_bars=bars,
        created_at=_START,
    )
    result = build_run_result(
        manifest=prepared.manifest,
        trade_records=records,
        event_log=events,
        contract=contract,
        completed_at=_START + timedelta(minutes=2),
        evidence_complete=True,
    )
    exported = ResultExporter(tmp_path / "results").export(result)
    trades_document = json.loads(exported.trades_path.read_text(encoding="utf-8"))
    events_document = json.loads(exported.events_path.read_text(encoding="utf-8"))
    main_document = json.loads(exported.result_path.read_text(encoding="utf-8"))

    assert trades_document["decision_evidence_complete"] is True
    assert trades_document["trades"][0]["decision_evidence"] == evidence.to_dict()
    serialized_fact = trades_document["trades"][0]["decision_evidence"]["entry"][
        "condition_facts"
    ][0]
    assert type(serialized_fact["actual"]) is bool
    assert type(serialized_fact["required"]) is bool
    assert events_document["evidence_complete"] is True
    assert events_document["rejection_evidence"] == []
    assert events_document["evidence_summary"] == result.evidence_summary.to_dict()
    assert main_document["decision_evidence_complete"] is True
    catalog = ResultsCatalog(tmp_path / "results")
    assert catalog.get_result("evidence-trade-001")["decision_evidence_complete"] is True
    assert catalog.get_trades("evidence-trade-001")["trades"][0]["trade_id"] == evidence.trade_id
    assert catalog.get_events("evidence-trade-001")["evidence_summary"] == (
        result.evidence_summary.to_dict()
    )
    preview_scoped_records = build_trade_records(
        run_id="preview-internal",
        trade_id_prefix="trade",
        trades=(records[0].execution,),
        events=events,
        canonical_bars=bars,
        contract=contract,
        session_name="eth",
        include_decision_evidence=True,
    )
    assert preview_scoped_records[0].trade_id == "trade-00001"


def test_rejection_builder_keeps_all_blockers_and_derives_the_only_summary_truth() -> None:
    """A full record, rather than an independent counter, is the summary's sole input."""
    event = _rejection_event(
        sequence=1,
        evidence_id="rejection_000001",
        blocking_condition_ids=("daily_regime_is_trend", "entry_unlocked"),
    )
    records = build_rejection_evidence(
        (event,),
        trading_date_for_timestamp=lambda _: date(2026, 7, 20),
    )
    assert len(records) == 1
    record = records[0]
    assert record.blocking_condition_ids == (
        "daily_regime_is_trend",
        "entry_unlocked",
    )
    facts = {fact.condition_id: fact for fact in record.condition_facts}
    assert facts["daily_regime_is_trend"].status == "failed"
    assert facts["entry_unlocked"].status == "failed"
    assert facts["mid_direction_matches_entry"].status == "not_evaluated"
    assert facts["mid_direction_matches_entry"].source_sequences == ()

    summary = build_evidence_summary(
        events=(event,),
        rejection_evidence=records,
        trade_count=0,
        complete=True,
    )
    assert summary.evaluation_count == 1
    assert summary.rejection_count == 1
    assert summary.blocking_condition_counts == {
        "daily_regime_is_trend": 1,
        "entry_unlocked": 1,
    }
    assert summary.layer_reached_counts == {"daily": 1, "entry": 1}
    assert summary.deepest_layer == "entry"
    validate_evidence_bundle(
        events=(event,),
        rejection_evidence=records,
        evidence_summary=summary,
        trade_evidence=(),
        expected_trade_count=0,
        complete=True,
    )
    with pytest.raises(EvidenceValidationError, match="derived exactly"):
        validate_evidence_bundle(
            events=(event,),
            rejection_evidence=records,
                evidence_summary=replace(
                    summary,
                    layer_reached_counts={"daily": 1, "entry": 2},
                ),
            trade_evidence=(),
            expected_trade_count=0,
            complete=True,
        )


def test_rejection_builder_retains_every_record_without_a_hidden_three_record_cap() -> None:
    """Four records prove the artifact is complete retention, not a closest-three sample."""
    events = tuple(
        _rejection_event(
            sequence=sequence,
            evidence_id=f"rejection_{sequence:06d}",
        )
        for sequence in range(1, 5)
    )
    records = build_rejection_evidence(
        events,
        trading_date_for_timestamp=lambda _: date(2026, 7, 20),
    )
    assert [record.evidence_id for record in records] == [
        "rejection_000001",
        "rejection_000002",
        "rejection_000003",
        "rejection_000004",
    ]
    assert [record.evaluation_sequence for record in records] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    ("exit_kind", "expected_reason", "expected_candidates", "expected_resolution"),
    [
        ("stop", "stop", {"stop", "target"}, None),
        ("target", "target", {"stop", "target"}, None),
        ("forced", "session_close", {"session_close"}, None),
    ],
)
def test_exit_evidence_keeps_the_actual_reason_for_stop_target_and_forced_close(
    contracts_registry,
    exit_kind: str,
    expected_reason: str,
    expected_candidates: set[str],
    expected_resolution: str | None,
) -> None:
    """Each execution branch carries its real reason and candidates into the final record."""
    records, _, _ = _completed_trade_records(
        contracts_registry.by_symbol("NQ"),
        exit_kind=exit_kind,
    )
    evidence = records[0].decision_evidence
    assert evidence is not None
    assert evidence.exit.reason == expected_reason
    assert evidence.exit.resolution_code == expected_resolution
    assert {candidate.candidate_id for candidate in evidence.exit.candidates} == expected_candidates
    if exit_kind == "forced":
        assert all(candidate.triggered for candidate in evidence.exit.candidates)


def test_evidence_validation_fails_closed_for_invalid_native_values_and_references() -> None:
    """Bad evidence cannot become a plausible-but-false immutable artifact."""
    with pytest.raises(EvidenceValidationError, match="finite"):
        _condition_fact(actual=float("nan"))
    with pytest.raises(EvidenceValidationError, match="unit"):
        _condition_fact(unit="")
    with pytest.raises(EvidenceValidationError, match="source sequence"):
        _condition_fact(source_sequences=())
    with pytest.raises(EvidenceValidationError, match="not_evaluated"):
        ConditionFact(
            condition_id="downstream_gate",
            layer_id="mid",
            observed_at=_START,
            status="not_evaluated",
            actual=True,
            operator="eq",
            required=True,
            unit="boolean",
            source_sequences=(),
        )

    event = _rejection_event(sequence=1, evidence_id="rejection_000001")
    record = build_rejection_evidence(
        (event,),
        trading_date_for_timestamp=lambda _: date(2026, 7, 20),
    )[0]
    with pytest.raises(EvidenceValidationError, match="trading_date"):
        replace(record, trading_date=datetime(2026, 7, 20, tzinfo=UTC))

    duplicate = _rejection_event(sequence=2, evidence_id="rejection_000001")
    with pytest.raises(EvidenceValidationError, match="ids must be unique"):
        build_rejection_evidence(
            (event, duplicate),
            trading_date_for_timestamp=lambda _: date(2026, 7, 20),
        )


def test_rejection_trading_date_is_the_same_exchange_label_from_any_process_timezone(
    contracts_registry,
) -> None:
    """The evidence label comes from exchange session rules, not the process-local calendar."""
    contract = contracts_registry.by_symbol("NQ")
    utc_instant = datetime(2026, 7, 20, 22, 30, tzinfo=UTC)
    another_representation = utc_instant.astimezone(timezone(timedelta(hours=8)))
    exchange_date = trading_date_for_timestamp(
        utc_instant,
        contract=contract,
        session_name="eth",
    )
    assert exchange_date == date(2026, 7, 21)
    assert exchange_date == trading_date_for_timestamp(
        another_representation,
        contract=contract,
        session_name="eth",
    )


def test_legacy_evidence_is_unavailable_but_new_known_empty_is_not(tmp_path: Path) -> None:
    """The read layer must never reinterpret legacy absence as a known empty decision set."""
    root = tmp_path / "results"
    (root / "events").mkdir(parents=True)
    (root / "trades").mkdir()
    (root / "legacy-001.json").write_text(
        json.dumps(
            {
                "schema": "result.v1",
                "run": {"run_id": "legacy-001", "manifest": {}},
                "trades_ref": "trades/legacy-001.json",
                "events_ref": "events/legacy-001.json",
            }
        ),
        encoding="utf-8",
    )
    (root / "trades" / "legacy-001.json").write_text(
        json.dumps({"schema": "trades.v1", "run_id": "legacy-001", "trades": []}),
        encoding="utf-8",
    )
    events_path = root / "events" / "legacy-001.json"
    events_path.write_text(
        json.dumps({"schema": "events.v1", "run_id": "legacy-001", "events": []}),
        encoding="utf-8",
    )

    catalog = ResultsCatalog(results_root=root)
    legacy_events = catalog.get_events("legacy-001")
    legacy_trades = catalog.get_trades("legacy-001")
    assert legacy_events["evidence_complete"] is False
    assert legacy_events["evidence_availability"] == "unavailable"
    assert "rejection_evidence" not in legacy_events
    assert legacy_trades["decision_evidence_complete"] is False
    assert legacy_trades["decision_evidence_availability"] == "unavailable"

    events_path.write_text(
        json.dumps(
            {
                "schema": "events.v1",
                "run_id": "legacy-001",
                "events": [],
                "rejection_evidence": [],
                "evidence_summary": {
                    "availability": "available",
                    "complete": True,
                    "evaluation_count": 0,
                    "rejection_count": 0,
                    "layer_reached_counts": {},
                    "blocking_condition_counts": {},
                    "deepest_layer": None,
                    "trade_count": 0,
                },
                "evidence_complete": True,
            }
        ),
        encoding="utf-8",
    )
    known_empty = catalog.get_events("legacy-001")
    assert known_empty["evidence_complete"] is True
    assert known_empty["rejection_evidence"] == []
    assert "evidence_availability" not in known_empty

    events_path.write_text(
        json.dumps(
            {
                "schema": "events.v1",
                "run_id": "legacy-001",
                "events": [],
                "evidence_complete": False,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="complete or absent for legacy"):
        catalog.get_events("legacy-001")


def test_evidence_serialization_failure_publishes_no_db_row_or_result_sidecar(
    tmp_path: Path,
    contracts_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D6: rendering the evidence sidecar fails before an immutable run becomes visible."""
    contract = contracts_registry.by_symbol("NQ")
    runner, bars, database_path, results_root = _tiny_runner(tmp_path, contract)
    original_json_text = persistence_module._json_text

    def fail_events_document(value: object) -> str:
        if isinstance(value, dict) and value.get("schema") == "events.v1":
            raise EvidenceValidationError("forced evidence serialization failure")
        return original_json_text(value)

    monkeypatch.setattr(persistence_module, "_json_text", fail_events_document)
    with pytest.raises(EvidenceValidationError, match="forced evidence serialization"):
        runner.run(contract=contract, config=_tiny_config("evidence-atomic-001", bars))

    assert not database_path.exists()
    assert _json_or_temp_inventory(results_root) == {}


def test_database_failure_removes_only_this_attempts_staged_evidence_files(
    tmp_path: Path,
    contracts_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure before commit rolls back both immutable rows and staged sidecars."""
    contract = contracts_registry.by_symbol("NQ")
    runner, bars, database_path, results_root = _tiny_runner(tmp_path, contract)

    def fail_date_index(
        connection: sqlite3.Connection,
        run_id: str,
        values: object,
    ) -> None:
        del connection, run_id, values
        raise OSError("forced index failure")

    monkeypatch.setattr(persistence_module, "_insert_run_trading_dates", fail_date_index)
    with pytest.raises(OSError, match="forced index failure"):
        runner.run(contract=contract, config=_tiny_config("evidence-atomic-002", bars))

    with sqlite3.connect(database_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("runs", "trades", "run_lookup", "run_trading_dates")
        }
    assert counts == {"runs": 0, "trades": 0, "run_lookup": 0, "run_trading_dates": 0}
    assert _json_or_temp_inventory(results_root) == {}


def test_retry_cannot_overwrite_a_completed_evidence_artifact(
    tmp_path: Path,
    contracts_registry,
) -> None:
    """New immutable runs reject a duplicate ID before touching any existing sidecar bytes."""
    contract = contracts_registry.by_symbol("NQ")
    runner, bars, _, results_root = _tiny_runner(tmp_path, contract)
    config = _tiny_config("evidence-retry-001", bars)
    runner.run(contract=contract, config=config)
    before = _all_file_hashes(results_root)

    with pytest.raises(ImmutableArtifactError, match="already exists"):
        runner.run(contract=contract, config=config)

    assert _all_file_hashes(results_root) == before


def _completed_trade_records(
    contract: ContractSpec,
    *,
    exit_kind: str = "collision",
) -> tuple[tuple[RecordedTrade, ...], tuple[StrategyEvent, ...], tuple[CanonicalBar, ...]]:
    """Run one fully captured same-minute collision through the real execution state machine."""
    signal_event = StrategyEvent(
        sequence=1,
        timestamp=_START - timedelta(minutes=5),
        ts_init=_START,
        phase=EventPhase.CLOSE,
        machine="entry_signal",
        event_type=StrategyEventType.SIGNAL_CREATED,
        from_state=None,
        to_state="pending",
        direction=Direction.LONG,
        price=100.0,
        details={"signal_kind": "inside"},
    )
    intent_event = StrategyEvent(
        sequence=2,
        timestamp=_START,
        ts_init=_START + timedelta(minutes=5),
        phase=EventPhase.INTRABAR,
        machine="entry_signal",
        event_type=StrategyEventType.ENTRY_INTENT_CREATED,
        from_state="pending",
        to_state="armed",
        direction=Direction.LONG,
        price=100.0,
        details={"signal_kind": "inside"},
    )
    entry_capture = EntryDecisionCapture(
        signal_kind="inside",
        signal_timestamp=signal_event.timestamp,
        entry_reference=100.0,
        stop_reference_type="mother_low",
        stop_reference_price=95.25,
        stop_offset_ticks=1,
        final_stop_price=95.0,
        condition_observations=(
            ConditionObservation(
                condition_id="entry_cross_state_matches_direction",
                layer_id="entry",
                observed_at=signal_event.ts_init,
                status="passed",
                actual=True,
                operator="eq",
                required=True,
                unit="boolean",
            ),
            ConditionObservation(
                condition_id="daily_regime_is_trend",
                layer_id="daily",
                observed_at=signal_event.ts_init,
                status="passed",
                actual="trend",
                operator="eq",
                required="trend",
                unit="enum",
            ),
        ),
        signal_event_ref=signal_event.event_ref,
        intent_event_ref=intent_event.event_ref,
    )
    intent = EntryIntent(
        direction=Direction.LONG,
        entry_reference=100.0,
        stop_reference=95.0,
        signal_kind=SignalKind.INSIDE,
        signal_timestamp=signal_event.timestamp,
        timestamp=_START,
        ts_init=_START + timedelta(minutes=5),
        decision_capture=entry_capture,
    )
    execution = ConservativeExecution(contract, quantity=1)
    execution.arm(intent)
    entry_bar = _bar(contract, _START, open_price=99.75, high=100.0, low=99.5, close=100.0)
    execution.process_minute(entry_bar)
    if exit_kind == "collision":
        exit_bar = _bar(
            contract,
            _START + timedelta(minutes=1),
            open_price=100.0,
            high=106.0,
            low=94.5,
            close=100.0,
        )
        execution.process_minute(exit_bar)
        bars = (entry_bar, exit_bar)
    elif exit_kind == "stop":
        exit_bar = _bar(
            contract,
            _START + timedelta(minutes=1),
            open_price=100.0,
            high=100.0,
            low=94.5,
            close=95.0,
        )
        execution.process_minute(exit_bar)
        bars = (entry_bar, exit_bar)
    elif exit_kind == "target":
        exit_bar = _bar(
            contract,
            _START + timedelta(minutes=1),
            open_price=100.0,
            high=106.0,
            low=99.5,
            close=105.5,
        )
        execution.process_minute(exit_bar)
        bars = (entry_bar, exit_bar)
    elif exit_kind == "forced":
        execution.end_session(entry_bar)
        bars = (entry_bar,)
    else:
        msg = f"unknown fixture exit_kind: {exit_kind}"
        raise AssertionError(msg)
    assert len(execution.trades) == 1
    events = _resequence_events((signal_event, intent_event, *execution.event_log))
    records = build_trade_records(
        run_id="evidence-trade-001",
        trades=execution.trades,
        events=events,
        canonical_bars=bars,
        contract=contract,
        session_name="eth",
        include_decision_evidence=True,
    )
    return records, events, bars


def _rejection_event(
    *,
    sequence: int,
    evidence_id: str,
    blocking_condition_ids: tuple[str, ...] = ("daily_regime_is_trend",),
) -> StrategyEvent:
    """Build a true same-evaluation multi-blocker capture without re-running a gate."""
    observations = (
        ConditionObservation(
            condition_id="daily_regime_is_trend",
            layer_id="daily",
            observed_at=_START,
            status="failed",
            actual="range",
            operator="eq",
            required="trend",
            unit="enum",
        ),
        ConditionObservation(
            condition_id="entry_unlocked",
            layer_id="entry",
            observed_at=_START,
            status="failed",
            actual=False,
            operator="eq",
            required=True,
            unit="boolean",
        ),
        ConditionObservation(
            condition_id="mid_direction_matches_entry",
            layer_id="mid",
            observed_at=_START,
            status="not_evaluated",
            actual=None,
            operator="eq",
            required=None,
            unit="enum",
        ),
    )
    return StrategyEvent(
        sequence=sequence,
        timestamp=_START,
        ts_init=_START + timedelta(minutes=5),
        phase=EventPhase.CLOSE,
        machine="entry_signal",
        event_type=StrategyEventType.SIGNAL_REJECTED,
        from_state="awaiting_signal",
        to_state="awaiting_signal",
        direction=Direction.LONG,
        details={"reason": "fixture"},
        rejection_capture=RejectionCapture(
            evidence_id=evidence_id,
            evaluation_sequence=sequence,
            reached_layers=("daily", "entry"),
            condition_observations=observations,
            blocking_condition_ids=blocking_condition_ids,
            context=SignalEvaluationContext(
                candidate_signal_kinds=("inside",),
                inside_count=1,
                entry_pullback_state="awaiting_signal",
                mid_pullback_state="awaiting_touch",
                daily_regime="range",
            ),
        ),
    )


def _condition_fact(
    *,
    actual: object = True,
    unit: str = "boolean",
    source_sequences: tuple[int, ...] = (1,),
) -> ConditionFact:
    """One compact valid fact with targeted mutation knobs."""
    return ConditionFact(
        condition_id="fixture_condition",
        layer_id="entry",
        observed_at=_START,
        status="passed",
        actual=actual,  # type: ignore[arg-type]
        operator="eq",
        required=True,
        unit=unit,
        source_sequences=source_sequences,
    )


def _tiny_runner(
    tmp_path: Path,
    contract: ContractSpec,
) -> tuple[BacktestRunner, tuple[CanonicalBar, ...], Path, Path]:
    """A complete ten-minute ETH slice is enough to exercise the shared replay path quickly."""
    start = datetime(2026, 7, 22, 22, tzinfo=UTC)
    bars = tuple(
        _bar(
            contract,
            start + timedelta(minutes=index),
            open_price=20_000.0 + index * 0.25,
            high=20_001.0 + index * 0.25,
            low=19_999.5 + index * 0.25,
            close=20_000.5 + index * 0.25,
        )
        for index in range(10)
    )
    market_root = tmp_path / "market"
    CanonicalStore(market_root).append(bars)
    database_path = tmp_path / "runs.sqlite3"
    results_root = tmp_path / "results"
    runner = BacktestRunner(
        canonical_store=CanonicalStore(market_root),
        daily_canonical_store=CanonicalStore(tmp_path / "market-daily"),
        run_store=SqliteRunStore(database_path),
        result_exporter=ResultExporter(results_root),
        quality_reports_root=None,
    )
    return runner, bars, database_path, results_root


def _tiny_config(run_id: str, bars: tuple[CanonicalBar, ...]) -> BacktestRunConfig:
    return BacktestRunConfig(
        run_id=run_id,
        strategy_version="trend-v0",
        session_name="eth",
        range_start=bars[0].timestamp,
        range_end=bars[-1].timestamp + timedelta(minutes=1),
        initial_capital=100_000.0,
        quantity=1,
        verify_nautilus_replay=False,
    )


def _bar(
    contract: ContractSpec,
    timestamp: datetime,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> CanonicalBar:
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


def _json_or_temp_inventory(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and (path.suffix == ".json" or path.name.endswith(".tmp"))
    }


def _all_file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
