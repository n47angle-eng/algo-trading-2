from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from futures_research.backtest.execution import ConservativeExecution
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    SignalKind,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.data.models import CanonicalBar
from futures_research.paper.execution_runtime import ConservativeExecutionRuntime
from futures_research.paper.market_data import FormingBarUpdate
from futures_research.paper.models import ClosedMarketInput
from futures_research.paths import PROJECT_ROOT

START = datetime(2026, 7, 31, 13, 30, tzinfo=UTC)


def contract():
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    return registry.contracts["NQ"]


def intent() -> EntryIntent:
    return EntryIntent(
        direction=Direction.LONG,
        entry_reference=100,
        stop_reference=95,
        signal_kind=SignalKind.INSIDE,
        signal_timestamp=START - timedelta(minutes=5),
        timestamp=START,
        ts_init=START + timedelta(minutes=5),
    )


def canonical(
    minute: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> CanonicalBar:
    timestamp = START + timedelta(minutes=minute)
    return CanonicalBar(
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=10,
        contract_id="NQ-202609-CME",
        ingested_at=timestamp + timedelta(minutes=1),
    )


def closed(bar: CanonicalBar) -> ClosedMarketInput:
    return ClosedMarketInput.create(
        provider_session_id="replay-session",
        contract_id=bar.contract_id,
        timeframe="1m",
        mode="replay_test",
        event_at=(bar.timestamp + timedelta(minutes=1))
        .isoformat()
        .replace("+00:00", "Z"),
        received_at=bar.ingested_at.isoformat().replace("+00:00", "Z"),
        open_price=bar.open,
        high_price=bar.high,
        low_price=bar.low,
        close_price=bar.close,
        volume=bar.volume,
        source_kind="live",
    )


def test_runtime_adapter_is_byte_for_behavior_equal_to_shared_execution_core() -> None:
    direct = ConservativeExecution(contract(), quantity=1)
    runtime = ConservativeExecutionRuntime(contract(), quantity=1)
    direct.arm(intent())
    runtime.arm(intent())
    bars = (
        canonical(0, open_price=99, high=101, low=98, close=100.5),
        canonical(1, open_price=101, high=106, low=100.5, close=105.5),
    )

    direct_updates = tuple(direct.process_minute(bar) for bar in bars)
    runtime_updates = tuple(runtime.process(closed(bar)) for bar in bars)

    assert runtime.core_type is ConservativeExecution
    assert runtime.position is direct.position
    assert runtime.trades == direct.trades
    assert [
        (event.event_type, event.price)
        for update in runtime_updates
        for event in update.events
    ] == [
        (event.event_type, event.price)
        for update in direct_updates
        for event in update.events
    ]


def test_forming_update_can_never_reach_shared_execution() -> None:
    runtime = ConservativeExecutionRuntime(contract(), quantity=1)
    forming = FormingBarUpdate(
        provider_session_id="session",
        contract_id="NQ-202609-CME",
        timeframe="1m",
        mode="replay_test",
        event_at="2026-07-31T13:31:00Z",
        received_at="2026-07-31T13:30:30Z",
        open_price=100,
        high_price=101,
        low_price=99,
        close_price=100,
        volume=5,
    )

    with pytest.raises(ValueError, match="forming_bar_not_executable"):
        runtime.process(forming)

    assert runtime.event_log == ()
    assert runtime.trades == ()
