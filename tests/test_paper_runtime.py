from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from futures_research.api.paper_runtime import (
    PaperTraderCreateRequestV2,
    StrategyTimeframeProfileWire,
)
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    SignalKind,
    StrategyUpdate,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.paper.models import BaselineMember, ClosedMarketInput
from futures_research.paper.runtime import (
    PaperTraderRuntime,
    SafetyController,
)
from futures_research.paper.store import (
    PaperRuntimeStore,
    PaperStoreLifecycleConflictError,
)
from futures_research.paths import PROJECT_ROOT

NOW = datetime(2026, 7, 31, 13, 30, tzinfo=UTC)
STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
BASELINE_RESULT = b'{"schema":"backtest_result.v1"}'
BASELINE_SHA = sha256(BASELINE_RESULT).hexdigest()


class ForcedOnce:
    def __init__(self, direction: Direction) -> None:
        self.direction = direction
        self.used = False

    def process(self, update: ClosedMarketInput) -> StrategyUpdate:
        if self.used:
            return StrategyUpdate(events=(), entry_intents=())
        self.used = True
        close_at = datetime.fromisoformat(
            update.event_at.removesuffix("Z") + "+00:00"
        )
        start_at = close_at - timedelta(minutes=1)
        if self.direction is Direction.LONG:
            entry, stop = update.open_price, update.open_price - 5
        else:
            entry, stop = update.open_price, update.open_price + 5
        return StrategyUpdate(
            events=(),
            entry_intents=(
                EntryIntent(
                    direction=self.direction,
                    entry_reference=entry,
                    stop_reference=stop,
                    signal_kind=SignalKind.INSIDE,
                    signal_timestamp=start_at - timedelta(minutes=5),
                    timestamp=start_at,
                    ts_init=start_at + timedelta(minutes=5),
                ),
            ),
        )


class NoSignal:
    def process(self, update: ClosedMarketInput) -> StrategyUpdate:
        del update
        return StrategyUpdate(events=(), entry_intents=())


def closed(
    minute: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> ClosedMarketInput:
    event_at = NOW + timedelta(minutes=minute + 1)
    return ClosedMarketInput.create(
        provider_session_id="saved-ib-session",
        contract_id="NQ-202609-CME",
        timeframe="1m",
        mode="replay_test",
        event_at=event_at.isoformat().replace("+00:00", "Z"),
        received_at=(event_at + timedelta(seconds=1))
        .isoformat()
        .replace("+00:00", "Z"),
        open_price=open_price,
        high_price=high,
        low_price=low,
        close_price=close,
        volume=100,
        source_kind="live",
    )


def create_trader(
    store: PaperRuntimeStore,
    *,
    request_id: str,
) -> str:
    body = PaperTraderCreateRequestV2.model_validate(
        {
            "schema": "paper_trader_create_request.v2",
            "request_id": request_id,
            "selection": {
                "strategy_id": "strategy-0003",
                "content_sha256": STRATEGY_SHA,
                "contract_id": "NQ-202609-CME",
                "baseline_run_id": "baseline-test",
                "baseline_result_sha256": BASELINE_SHA,
                "timeframes": {
                    "market_input": "1m",
                    "execution": "1m",
                    "chart_display": "30m",
                },
            },
        }
    )
    run_id = body.selection.baseline_run_id
    members = tuple(
        BaselineMember.from_bytes(path, content)
        for path, content in (
            ("baseline/result.json", BASELINE_RESULT),
            (f"baseline/trades/{run_id}.json", b'{"trades":[]}'),
            (f"baseline/equity/{run_id}.json", b'{"equity":[]}'),
            (f"baseline/events/{run_id}.json", b'{"events":[]}'),
        )
    )
    trader, _ = store.create_trader(
        body=body,
        strategy_profile=StrategyTimeframeProfileWire.model_validate(
            {
                "bias": "D",
                "mid": "1H",
                "entry": "5m",
                "source": "strategy.v1",
                "client_override": False,
            }
        ),
        initial_capital=100_000,
        baseline_members=members,
        now=NOW,
    )
    return trader.trader_id


@pytest.fixture
def store(tmp_path: Path) -> PaperRuntimeStore:
    value = PaperRuntimeStore(tmp_path / "paper.sqlite3")
    value.initialize()
    return value


def nq_contract():
    return ContractRegistry.from_yaml(
        PROJECT_ROOT / "config" / "contracts.yaml"
    ).contracts["NQ"]


def test_runtime_duplicate_input_cannot_double_fill_or_trade(
    store: PaperRuntimeStore,
) -> None:
    trader_id = create_trader(
        store,
        request_id="4322a78f-7603-4778-bf34-a1f6369d772c",
    )
    runtime = PaperTraderRuntime(
        store=store,
        trader_id=trader_id,
        contract=nq_contract(),
        decision_source=ForcedOnce(Direction.LONG),
    )
    runtime.start(now=NOW)
    entry = closed(0, open_price=100, high=101, low=98, close=100.5)

    first = runtime.process(entry)
    duplicate = runtime.process(entry)

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert store.table_count("paper_fills") == 1
    assert store.table_count("paper_processing_checkpoints") == 1

    runtime.process(
        closed(1, open_price=101, high=106, low=100.5, close=105.5)
    )
    assert store.table_count("paper_trades") == 1
    assert runtime.snapshot().position_quantity == 0
    assert runtime.snapshot().realized_pnl > 0


def test_multi_trader_same_feed_keeps_account_and_cursor_independent(
    store: PaperRuntimeStore,
) -> None:
    first_id = create_trader(
        store,
        request_id="4322a78f-7603-4778-bf34-a1f6369d772c",
    )
    second_id = create_trader(
        store,
        request_id="7b14045e-07fb-413c-a718-ef4c0d98520b",
    )
    first = PaperTraderRuntime(
        store=store,
        trader_id=first_id,
        contract=nq_contract(),
        decision_source=ForcedOnce(Direction.LONG),
    )
    second = PaperTraderRuntime(
        store=store,
        trader_id=second_id,
        contract=nq_contract(),
        decision_source=NoSignal(),
    )
    first.start(now=NOW)
    second.start(now=NOW)
    shared = closed(0, open_price=100, high=101, low=98, close=100.5)

    first.process(shared)
    second.process(shared)

    assert store.table_count("paper_market_inputs") == 1
    assert store.table_count("paper_processing_checkpoints") == 2
    assert first.snapshot().position_quantity == 1
    assert second.snapshot().position_quantity == 0
    assert second.snapshot().decision_count == 0


def test_pause_without_new_trusted_price_never_fabricates_exit(
    store: PaperRuntimeStore,
) -> None:
    trader_id = create_trader(
        store,
        request_id="4322a78f-7603-4778-bf34-a1f6369d772c",
    )
    runtime = PaperTraderRuntime(
        store=store,
        trader_id=trader_id,
        contract=nq_contract(),
        decision_source=ForcedOnce(Direction.LONG),
    )
    runtime.start(now=NOW)
    runtime.process(closed(0, open_price=100, high=101, low=98, close=100.5))

    runtime.request_pause(now=NOW + timedelta(seconds=30))

    assert runtime.snapshot().lifecycle == "pausing"
    assert runtime.snapshot().position_quantity == 1
    assert store.table_count("paper_fills") == 1

    runtime.process(
        closed(1, open_price=100.5, high=104, low=96, close=100)
    )
    assert runtime.snapshot().lifecycle == "paused"
    assert runtime.snapshot().position_quantity == 0
    assert store.table_count("paper_fills") == 2


def test_safety_controller_trips_at_exact_8r_and_eight_losses() -> None:
    drawdown = SafetyController()
    assert drawdown.observe(equity_r=-7.999, completed_trade_pnl=None) is None
    assert drawdown.observe(equity_r=-8, completed_trade_pnl=None) == "drawdown"

    streak = SafetyController()
    for _ in range(7):
        assert streak.observe(equity_r=0, completed_trade_pnl=-1) is None
    assert streak.observe(equity_r=0, completed_trade_pnl=-1) == "losing_streak"
    assert streak.losing_streak == 8

    reset = SafetyController()
    reset.observe(equity_r=0, completed_trade_pnl=-1)
    reset.observe(equity_r=0, completed_trade_pnl=0)
    assert reset.losing_streak == 0


def test_permanent_stop_is_irreversible_when_flat(store: PaperRuntimeStore) -> None:
    trader_id = create_trader(
        store,
        request_id="4322a78f-7603-4778-bf34-a1f6369d772c",
    )
    runtime = PaperTraderRuntime(
        store=store,
        trader_id=trader_id,
        contract=nq_contract(),
        decision_source=NoSignal(),
    )
    runtime.start(now=NOW)
    runtime.request_permanent_stop(now=NOW + timedelta(seconds=1))

    assert runtime.snapshot().lifecycle == "permanently_stopped"
    with pytest.raises(
        PaperStoreLifecycleConflictError,
        match="irreversible",
    ):
        runtime.start(now=NOW + timedelta(seconds=2))
