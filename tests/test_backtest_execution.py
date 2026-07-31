"""Golden fixtures for the conservative WO-002 execution state machine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from futures_research.backtest.execution import (
    ConservativeExecution,
    ExitReason,
)
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    SignalKind,
    StrategyEventType,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.data.models import CanonicalBar

_START = datetime(2026, 7, 20, 14, 30, tzinfo=UTC)
_CONTRACT_ID = "NQ-202609-CME"


def test_golden_gap_open_entry_uses_opening_price_before_configured_slippage(
    contracts_registry: ContractRegistry,
) -> None:
    """§14.1 fills a long breakout from the opening print, not its trigger level."""
    execution = _execution(contracts_registry)
    execution.arm(_long_intent())

    update = execution.process_minute(
        _bar(0, open_price=101.0, high=102.0, low=100.75, close=101.5)
    )

    assert execution.position is not None
    assert execution.position.entry_price == pytest.approx(101.25)
    assert execution.position.target_price == pytest.approx(107.5)
    assert update.trades == ()
    event = update.events[0]
    assert event.event_type is StrategyEventType.ENTRY_FILLED
    assert event.price == pytest.approx(101.25)
    assert dict(event.details) == {
        "signal_kind": "inside",
        "fill_mode": "gap_open",
        "raw_fill_price": 101.0,
        "slippage_ticks": 1,
        "commission_per_side_per_contract": 2.5,
        "commission_applied": 2.5,
        "quantity": 1,
        "stop_price": 95.0,
        "target_price": 107.5,
    }

    with pytest.raises(TypeError):
        event.details["mutate"] = "no"  # type: ignore[index]


def test_golden_gap_open_stop_uses_opening_price_before_stop_slippage(
    contracts_registry: ContractRegistry,
) -> None:
    """§14.1 also gives an existing protective stop the next opening print."""
    execution = _execution(contracts_registry)
    execution.arm(_long_intent())
    execution.process_minute(_entry_bar(0))

    update = execution.process_minute(_bar(1, open_price=94.0, high=95.0, low=93.5, close=94.5))

    assert len(update.trades) == 1
    trade = update.trades[0]
    assert trade.exit_reason is ExitReason.STOP
    assert trade.exit_price == pytest.approx(93.5)
    event = update.events[0]
    assert dict(event.details)["raw_fill_price"] == 94.0
    assert dict(event.details)["fill_mode"] == "gap_open"
    assert dict(event.details)["slippage_ticks"] == 2


def test_golden_explicit_invalidation_precedes_same_minute_entry(
    contracts_registry: ContractRegistry,
) -> None:
    """§14.2(a) rejects a breakout when a higher-machine invalidation is present."""
    execution = _execution(contracts_registry)
    execution.arm(_long_intent())

    update = execution.process_minute(
        _bar(0, open_price=99.5, high=101.0, low=99.25, close=100.5),
        invalidation_reason="lm_step0_invalidated",
    )

    assert execution.position is None
    assert execution.pending_intent is None
    assert update.trades == ()
    assert [(event.machine, event.event_type) for event in update.events] == [
        ("execution", StrategyEventType.SIGNAL_CANCELLED)
    ]
    assert dict(update.events[0].details)["reason"] == "lm_step0_invalidated"


def test_golden_oco_invalidation_precedes_same_minute_entry(
    contracts_registry: ContractRegistry,
) -> None:
    """A pending OCO stop touch cancels before the same one-minute range can enter."""
    execution = _execution(contracts_registry)
    execution.arm(_long_intent())

    update = execution.process_minute(_bar(0, open_price=99.5, high=101.0, low=94.5, close=100.0))

    assert execution.position is None
    assert execution.pending_intent is None
    assert update.trades == ()
    assert update.events[0].event_type is StrategyEventType.SIGNAL_CANCELLED
    assert dict(update.events[0].details)["reason"] == "oco_stop_touched"


def test_golden_one_minute_order_allows_target_before_later_stop(
    contracts_registry: ContractRegistry,
) -> None:
    """Separate canonical minutes preserve a target touch that happens before a stop."""
    execution = _execution(contracts_registry)
    execution.arm(_long_intent())
    execution.process_minute(_entry_bar(0))

    target_update = execution.process_minute(
        _bar(1, open_price=101.0, high=106.0, low=100.5, close=105.5)
    )
    later_update = execution.process_minute(
        _bar(2, open_price=95.0, high=96.0, low=94.0, close=94.5)
    )

    assert len(target_update.trades) == 1
    assert target_update.trades[0].exit_reason is ExitReason.TARGET
    assert target_update.trades[0].exit_price == pytest.approx(105.5)
    assert later_update.trades == ()


def test_golden_one_minute_order_allows_stop_before_later_target(
    contracts_registry: ContractRegistry,
) -> None:
    """Separate canonical minutes preserve a stop touch that happens before a target."""
    execution = _execution(contracts_registry)
    execution.arm(_long_intent())
    execution.process_minute(_entry_bar(0))

    stop_update = execution.process_minute(
        _bar(1, open_price=99.5, high=100.0, low=94.5, close=95.0)
    )
    later_update = execution.process_minute(
        _bar(2, open_price=100.0, high=106.0, low=99.5, close=105.5)
    )

    assert len(stop_update.trades) == 1
    assert stop_update.trades[0].exit_reason is ExitReason.STOP
    assert stop_update.trades[0].exit_price == pytest.approx(94.5)
    assert later_update.trades == ()


def test_golden_same_minute_stop_target_collision_is_stop_first(
    contracts_registry: ContractRegistry,
) -> None:
    """§14.2(b) records the conservative fallback when 1m cannot order the collision."""
    execution = _execution(contracts_registry)
    execution.arm(_long_intent())
    execution.process_minute(_entry_bar(0))

    update = execution.process_minute(_bar(1, open_price=100.0, high=106.0, low=94.5, close=100.5))

    assert len(update.trades) == 1
    assert update.trades[0].exit_reason is ExitReason.STOP
    assert update.trades[0].exit_price == pytest.approx(94.5)
    assert dict(update.events[0].details)["resolution"] == "same_minute_stop_first"


def test_short_execution_mirrors_adverse_entry_and_target_semantics(
    contracts_registry: ContractRegistry,
) -> None:
    """The execution core mirrors long prices, risk, and costs for a short position."""
    execution = _execution(contracts_registry)
    execution.arm(_short_intent())

    execution.process_minute(_bar(0, open_price=100.25, high=100.5, low=100.0, close=100.0))
    update = execution.process_minute(_bar(1, open_price=96.0, high=96.5, low=94.0, close=94.5))

    assert len(update.trades) == 1
    trade = update.trades[0]
    assert trade.exit_reason is ExitReason.TARGET
    assert trade.entry_price == pytest.approx(99.75)
    assert trade.target_price == pytest.approx(94.5)
    assert trade.exit_price == pytest.approx(94.5)
    assert trade.net_pnl == pytest.approx(100.0)


def test_execution_accepts_an_explicit_per_run_cost_override(
    contracts_registry: ContractRegistry,
) -> None:
    """S4 can snapshot a cost variant without mutating the contract registry default."""
    contract = contracts_registry.by_symbol("NQ")
    override = contract.execution_costs.model_copy(update={"commission_per_side": 3.0})
    execution = ConservativeExecution(contract, quantity=2, costs=override)
    execution.arm(_long_intent())

    update = execution.process_minute(_entry_bar(0))

    assert execution.costs == override
    assert dict(update.events[0].details)["commission_per_side_per_contract"] == 3.0
    assert dict(update.events[0].details)["commission_applied"] == 6.0
    assert contract.execution_costs.commission_per_side == 2.5


def test_golden_day_end_force_flats_position_and_clears_pending(
    contracts_registry: ContractRegistry,
) -> None:
    """§14.4 closes at the final close and independently removes unfilled entries."""
    positioned = _execution(contracts_registry)
    positioned.arm(_long_intent())
    positioned.process_minute(_entry_bar(0))
    final_bar = _bar(1, open_price=101.0, high=102.0, low=100.5, close=102.0)
    positioned.process_minute(final_bar)

    flat_update = positioned.end_session(final_bar)

    assert positioned.position is None
    assert len(flat_update.trades) == 1
    forced_trade = flat_update.trades[0]
    assert forced_trade.exit_reason is ExitReason.SESSION_CLOSE
    assert forced_trade.exit_price == pytest.approx(101.75)
    assert forced_trade.total_commission == pytest.approx(5.0)
    assert forced_trade.net_pnl == pytest.approx(25.0)
    assert flat_update.events[0].event_type is StrategyEventType.POSITION_FORCED_CLOSED
    assert flat_update.events[0].timestamp == final_bar.timestamp + timedelta(minutes=1)
    assert flat_update.events[0].phase.value == "day_end"

    pending = _execution(contracts_registry)
    pending.arm(_long_intent())
    pending_final_bar = _bar(0, open_price=99.0, high=99.5, low=98.5, close=99.0)
    pending.process_minute(pending_final_bar)
    pending_update = pending.end_session(pending_final_bar)

    assert pending.pending_intent is None
    assert pending.position is None
    assert pending_update.trades == ()
    assert pending_update.events[0].event_type is StrategyEventType.SIGNAL_CANCELLED
    assert dict(pending_update.events[0].details)["reason"] == "day_end_clear"


def _execution(contracts_registry: ContractRegistry) -> ConservativeExecution:
    """Build the configured NQ execution fixture with no hidden quantity default."""
    return ConservativeExecution(contracts_registry.by_symbol("NQ"), quantity=1)


def _long_intent() -> EntryIntent:
    """Create one 5m-source long intent whose child minutes begin at ``_START``."""
    return EntryIntent(
        direction=Direction.LONG,
        entry_reference=100.0,
        stop_reference=95.0,
        signal_kind=SignalKind.INSIDE,
        signal_timestamp=_START - timedelta(minutes=5),
        timestamp=_START,
        ts_init=_START + timedelta(minutes=5),
    )


def _short_intent() -> EntryIntent:
    """Create the short mirror of the five-minute long fixture intent."""
    return EntryIntent(
        direction=Direction.SHORT,
        entry_reference=100.0,
        stop_reference=105.0,
        signal_kind=SignalKind.MAGIC,
        signal_timestamp=_START - timedelta(minutes=5),
        timestamp=_START,
        ts_init=_START + timedelta(minutes=5),
    )


def _entry_bar(index: int) -> CanonicalBar:
    """Create a normal range entry without a same-minute stop or target collision."""
    return _bar(index, open_price=99.75, high=100.0, low=99.5, close=100.0)


def _bar(
    index: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> CanonicalBar:
    """Create one tick-aligned start-labelled canonical NQ minute."""
    return CanonicalBar(
        timestamp=_START + timedelta(minutes=index),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100,
        contract_id=_CONTRACT_ID,
        source="fixture",
    )
