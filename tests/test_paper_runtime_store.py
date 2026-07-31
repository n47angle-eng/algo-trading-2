from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from futures_research.api.paper_runtime import (
    PaperTraderCreateRequestV2,
    StrategyTimeframeProfileWire,
)
from futures_research.paper.models import BaselineMember, ClosedMarketInput
from futures_research.paper.store import (
    PaperRuntimeStore,
    PaperStoreIntegrityError,
    PaperStoreLeaseError,
    PaperStoreRequestConflictError,
)

STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
BASELINE_RESULT = b'{"schema":"backtest_result.v1"}'
BASELINE_SHA = sha256(BASELINE_RESULT).hexdigest()
NOW = datetime(2026, 7, 31, 1, 2, 3, tzinfo=UTC)


def request(request_id: str = "4322a78f-7603-4778-bf34-a1f6369d772c") -> PaperTraderCreateRequestV2:
    return PaperTraderCreateRequestV2.model_validate(
        {
            "schema": "paper_trader_create_request.v2",
            "request_id": request_id,
            "selection": {
                "strategy_id": "strategy-0003",
                "content_sha256": STRATEGY_SHA,
                "contract_id": "NQ-202609-CME",
                "baseline_run_id": "nq-20260728-standard-365adf",
                "baseline_result_sha256": BASELINE_SHA,
                "timeframes": {
                    "market_input": "1m",
                    "execution": "1m",
                    "chart_display": "30m",
                },
            },
        }
    )


def profile() -> StrategyTimeframeProfileWire:
    return StrategyTimeframeProfileWire.model_validate(
        {
            "bias": "D",
            "mid": "1H",
            "entry": "5m",
            "source": "strategy.v1",
            "client_override": False,
        }
    )


def baseline_members() -> tuple[BaselineMember, ...]:
    run_id = "nq-20260728-standard-365adf"
    return tuple(
        BaselineMember.from_bytes(path, payload)
        for path, payload in (
            ("baseline/result.json", BASELINE_RESULT),
            (f"baseline/trades/{run_id}.json", b'{"trades":[]}'),
            (f"baseline/equity/{run_id}.json", b'{"equity":[]}'),
            (f"baseline/events/{run_id}.json", b'{"events":[]}'),
        )
    )


@pytest.fixture
def store(tmp_path: Path) -> PaperRuntimeStore:
    value = PaperRuntimeStore(tmp_path / "paper.sqlite3")
    value.initialize()
    return value


def test_v4_schema_and_append_only_guards_are_exact(store: PaperRuntimeStore) -> None:
    report = store.integrity_check()

    assert report.user_version == 4
    assert "paper_market_inputs" in report.tables
    assert "paper_runtime_leases" in report.tables
    assert "paper_review_v2_ready" in report.tables
    assert report.quick_check == "ok"
    assert report.schema_matches is True
    assert report.append_only_trigger_count >= 30


def test_unknown_older_store_fails_closed_without_migration(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 3")
    connection.execute("CREATE TABLE legacy (value TEXT)")
    connection.close()

    with pytest.raises(PaperStoreIntegrityError, match="schema version 4"):
        PaperRuntimeStore(path).initialize()

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='legacy'"
        ).fetchone() == ("legacy",)
    finally:
        connection.close()


def test_create_v2_is_atomic_idempotent_and_keeps_locked_profile(
    store: PaperRuntimeStore,
) -> None:
    first, created = store.create_trader(
        body=request(),
        strategy_profile=profile(),
        initial_capital=100_000,
        baseline_members=baseline_members(),
        now=NOW,
    )
    replay, replay_created = store.create_trader(
        body=request(),
        strategy_profile=profile(),
        initial_capital=100_000,
        baseline_members=baseline_members(),
        now=NOW + timedelta(seconds=10),
    )

    assert created is True
    assert replay_created is False
    assert replay == first
    assert first.strategy_timeframe_profile.entry == "5m"
    assert first.selection.timeframes.chart_display == "30m"
    assert first.lifecycle == "provisioned"
    assert store.table_count("paper_traders") == 1
    assert store.table_count("paper_accounts") == 1
    assert store.table_count("paper_baseline_members") == 4


def test_same_request_with_different_payload_conflicts_without_write(
    store: PaperRuntimeStore,
) -> None:
    store.create_trader(
        body=request(),
        strategy_profile=profile(),
        initial_capital=100_000,
        baseline_members=baseline_members(),
        now=NOW,
    )
    changed = request().model_copy(
        update={
            "selection": request().selection.model_copy(
                update={
                    "timeframes": request().selection.timeframes.model_copy(
                        update={"chart_display": "1m"}
                    )
                }
            )
        }
    )

    with pytest.raises(PaperStoreRequestConflictError, match="request_id_conflict"):
        store.create_trader(
            body=changed,
            strategy_profile=profile(),
            initial_capital=100_000,
            baseline_members=baseline_members(),
            now=NOW,
        )

    assert store.table_count("paper_traders") == 1


def test_market_input_duplicate_is_exact_once_and_rows_are_immutable(
    store: PaperRuntimeStore,
) -> None:
    market_input = ClosedMarketInput.create(
        provider_session_id="ib-session-01",
        contract_id="NQ-202609-CME",
        timeframe="1m",
        mode="replay_test",
        event_at="2026-07-31T01:03:00Z",
        received_at="2026-07-31T01:03:01Z",
        open_price=20_000,
        high_price=20_005,
        low_price=19_998,
        close_price=20_004,
        volume=123,
        source_kind="live",
    )

    first, inserted = store.append_market_input(market_input)
    replay, replay_inserted = store.append_market_input(market_input)

    assert inserted is True
    assert replay_inserted is False
    assert replay == first
    assert store.table_count("paper_market_inputs") == 1

    connection = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE paper_market_inputs SET close_price = 1 WHERE input_id = ?",
                (market_input.input_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM paper_market_inputs WHERE input_id = ?",
                (market_input.input_id,),
            )
    finally:
        connection.close()


def test_single_writer_lease_denies_second_owner_and_stale_takeover_requires_recovery(
    store: PaperRuntimeStore,
) -> None:
    trader, _ = store.create_trader(
        body=request(),
        strategy_profile=profile(),
        initial_capital=100_000,
        baseline_members=baseline_members(),
        now=NOW,
    )
    store.transition_lifecycle(
        trader_id=trader.trader_id,
        expected_version=1,
        to_state="running",
        reason="runtime accepted after successful preflight",
        now=NOW,
    )
    store.acquire_lease(instance_id="instance-a", pid=101, now=NOW)

    with pytest.raises(PaperStoreLeaseError, match="single_writer_lease_unavailable"):
        store.acquire_lease(
            instance_id="instance-b",
            pid=202,
            now=NOW + timedelta(seconds=5),
        )

    takeover = store.acquire_lease(
        instance_id="instance-b",
        pid=202,
        now=NOW + timedelta(seconds=31),
        stale_after_seconds=30,
    )

    assert takeover.stale_takeover is True
    assert store.get_trader(trader.trader_id).lifecycle == "recovery_required"


def test_atomic_processing_rolls_back_all_evidence_and_projection_on_failure(
    store: PaperRuntimeStore,
) -> None:
    trader, _ = store.create_trader(
        body=request(),
        strategy_profile=profile(),
        initial_capital=100_000,
        baseline_members=baseline_members(),
        now=NOW,
    )
    market_input = ClosedMarketInput.create(
        provider_session_id="ib-session-01",
        contract_id="NQ-202609-CME",
        timeframe="1m",
        mode="replay_test",
        event_at="2026-07-31T01:03:00Z",
        received_at="2026-07-31T01:03:01Z",
        open_price=20_000,
        high_price=20_005,
        low_price=19_998,
        close_price=20_004,
        volume=123,
        source_kind="live",
    )
    store.append_market_input(market_input)
    connection = sqlite3.connect(store.path)
    try:
        connection.execute(
            """
            CREATE TRIGGER inject_checkpoint_failure
            BEFORE INSERT ON paper_processing_checkpoints
            BEGIN
                SELECT RAISE(ABORT, 'injected checkpoint failure');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError):
        store.append_processing_evidence(
            trader_id=trader.trader_id,
            input_id=market_input.input_id,
            decision_payload=json.dumps({"side": "long"}, separators=(",", ":")),
            order_payload=json.dumps({"side": "long"}, separators=(",", ":")),
            fill_price=20_004,
            position_quantity=1,
            cash=99_000,
            equity=100_000,
            now=NOW,
        )

    assert store.table_count("paper_decisions") == 0
    assert store.table_count("paper_orders") == 0
    assert store.table_count("paper_fills") == 0
    assert store.table_count("paper_position_events") == 0
    assert store.table_count("paper_processing_checkpoints") == 0
    account = store.account_projection(trader.trader_id)
    assert account["cash"] == 100_000
    assert account["position_quantity"] == 0
