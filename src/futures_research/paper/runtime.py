"""Per-trader local runtime, virtual account, safety, and lifecycle controls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Protocol

from futures_research.backtest.execution import (
    ExecutionPosition,
    ExecutionTrade,
    ExecutionUpdate,
)
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    StrategyEvent,
    StrategyEventType,
    StrategyUpdate,
)
from futures_research.data.contracts import ContractSpec
from futures_research.paper.execution_runtime import ConservativeExecutionRuntime
from futures_research.paper.models import (
    ClosedMarketInput,
    CompletedTradeWrite,
    RuntimeStepWrite,
    SimulatedFillWrite,
    canonical_utc,
)
from futures_research.paper.store import PaperRuntimeStore

SafetyTrigger = Literal["drawdown", "losing_streak"]


class DecisionSource(Protocol):
    """Closed-only strategy boundary used by one trader runtime."""

    def process(self, update: ClosedMarketInput) -> StrategyUpdate: ...


@dataclass(slots=True)
class SafetyController:
    max_drawdown_r: float = 8.0
    max_losing_streak: int = 8
    equity_high_water_r: float = 0.0
    drawdown_r: float = 0.0
    losing_streak: int = 0

    def observe(
        self,
        *,
        equity_r: float,
        completed_trade_pnl: float | None,
    ) -> SafetyTrigger | None:
        self.equity_high_water_r = max(self.equity_high_water_r, equity_r)
        self.drawdown_r = equity_r - self.equity_high_water_r
        if completed_trade_pnl is not None:
            if completed_trade_pnl < 0:
                self.losing_streak += 1
            else:
                self.losing_streak = 0
        if self.drawdown_r <= -self.max_drawdown_r:
            return "drawdown"
        if self.losing_streak >= self.max_losing_streak:
            return "losing_streak"
        return None


@dataclass(frozen=True, slots=True)
class RuntimeProcessResult:
    input_id: str
    duplicate: bool
    decision_count_delta: int
    fill_count_delta: int
    trade_count_delta: int
    lifecycle: str


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    trader_id: str
    lifecycle: str
    lifecycle_version: int
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    realized_r: float
    unrealized_r: float
    position_quantity: int
    pending_intent_count: int
    flatten_pending: bool
    decision_count: int
    trade_count: int
    losing_streak: int
    drawdown_r: float


class PaperTraderRuntime:
    """One independent strategy/execution/account FSM on a shared market feed."""

    def __init__(
        self,
        *,
        store: PaperRuntimeStore,
        trader_id: str,
        contract: ContractSpec,
        decision_source: DecisionSource,
        quantity: int = 1,
    ) -> None:
        self._store = store
        self._trader_id = trader_id
        self._contract = contract
        self._decision_source = decision_source
        self._execution = ConservativeExecutionRuntime(
            contract,
            quantity=quantity,
        )
        projection = store.runtime_projection(trader_id)
        self._initial_capital = float(projection["initial_capital"])
        self._realized_pnl = float(projection["realized_pnl"])
        self._realized_r = float(projection["realized_r"])
        self._last_input_at: str | None = projection["last_trusted_at"]
        self._safety = SafetyController(
            max_drawdown_r=float(projection["max_drawdown_r"]),
            max_losing_streak=int(projection["max_losing_streak"]),
            equity_high_water_r=float(projection["equity_high_water_r"]),
            drawdown_r=float(projection["drawdown_r"]),
            losing_streak=int(projection["losing_streak"]),
        )
        self._control: Literal["pause", "permanent_stop"] | None = None

    def start(self, *, now: datetime) -> RuntimeSnapshot:
        current = self._store.get_trader(self._trader_id)
        updated = self._store.transition_lifecycle(
            trader_id=self._trader_id,
            expected_version=current.lifecycle_version,
            to_state="running",
            reason="runtime started after explicit Owner command",
            now=now,
        )
        del updated
        self._control = None
        return self.snapshot()

    def request_pause(self, *, now: datetime) -> RuntimeSnapshot:
        current = self._store.get_trader(self._trader_id)
        exposure = (
            self._execution.position is not None
            or self._execution.pending_intent is not None
        )
        if not exposure:
            self._store.transition_lifecycle(
                trader_id=self._trader_id,
                expected_version=current.lifecycle_version,
                to_state="paused",
                reason="manual pause completed while flat",
                now=now,
            )
            self._control = None
            return self.snapshot()
        self._store.transition_lifecycle(
            trader_id=self._trader_id,
            expected_version=current.lifecycle_version,
            to_state="pausing",
            reason="manual pause awaiting trusted-price flatten",
            now=now,
            flatten_pending=True,
        )
        self._control = "pause"
        return self.snapshot()

    def request_permanent_stop(self, *, now: datetime) -> RuntimeSnapshot:
        current = self._store.get_trader(self._trader_id)
        exposure = (
            self._execution.position is not None
            or self._execution.pending_intent is not None
        )
        if not exposure:
            self._store.transition_lifecycle(
                trader_id=self._trader_id,
                expected_version=current.lifecycle_version,
                to_state="permanently_stopped",
                reason="permanent stop completed while flat",
                now=now,
            )
            self._control = None
            return self.snapshot()
        self._store.transition_lifecycle(
            trader_id=self._trader_id,
            expected_version=current.lifecycle_version,
            to_state="stopping",
            reason="permanent stop awaiting trusted-price flatten",
            now=now,
            flatten_pending=True,
        )
        self._control = "permanent_stop"
        return self.snapshot()

    def process(self, market_input: ClosedMarketInput) -> RuntimeProcessResult:
        if self._store.has_processing_checkpoint(
            trader_id=self._trader_id,
            input_id=market_input.input_id,
        ):
            return RuntimeProcessResult(
                input_id=market_input.input_id,
                duplicate=True,
                decision_count_delta=0,
                fill_count_delta=0,
                trade_count_delta=0,
                lifecycle=self.snapshot().lifecycle,
            )
        if self._last_input_at is not None and market_input.event_at <= self._last_input_at:
            raise ValueError("out_of_order runtime input")
        self._store.append_market_input(market_input)
        lifecycle = self._store.get_trader(self._trader_id).lifecycle
        if lifecycle not in ("running", "pausing", "stopping", "tripped"):
            raise RuntimeError(f"runtime cannot consume input while {lifecycle}")
        strategy_update = (
            self._decision_source.process(market_input)
            if lifecycle == "running" and self._control is None
            else StrategyUpdate(events=(), entry_intents=())
        )
        if len(strategy_update.entry_intents) > 1:
            raise RuntimeError("strategy emitted more than one intent for one entry bar")
        intent: EntryIntent | None = (
            strategy_update.entry_intents[0]
            if strategy_update.entry_intents
            else None
        )
        if intent is not None:
            self._execution.arm(intent)
        before_position = self._execution.position
        execution_update = self._execution.process(market_input)
        if self._control is not None and (
            self._execution.position is not None
            or self._execution.pending_intent is not None
        ):
            flatten = self._execution.flatten_at_trusted_input(market_input)
            execution_update = ExecutionUpdate(
                events=(*execution_update.events, *flatten.events),
                trades=(*execution_update.trades, *flatten.trades),
            )
        after_position = self._execution.position
        completed_trade = (
            execution_update.trades[0] if execution_update.trades else None
        )
        if len(execution_update.trades) > 1:
            raise RuntimeError("execution emitted more than one trade for one input")
        account = self._account_after(
            market_input=market_input,
            position=after_position,
            completed_trade=completed_trade,
        )
        completed_pnl = (
            None if completed_trade is None else completed_trade.net_pnl
        )
        safety_trigger = self._safety.observe(
            equity_r=account["realized_r"] + account["unrealized_r"],
            completed_trade_pnl=completed_pnl,
        )
        lifecycle_after: Literal[
            "paused", "tripped", "permanently_stopped"
        ] | None = None
        lifecycle_reason: str | None = None
        flatten_pending = False
        if safety_trigger is not None:
            lifecycle_after = "tripped"
            lifecycle_reason = f"safety trip: {safety_trigger}"
            flatten_pending = after_position is not None
        elif self._control == "pause" and (
            after_position is None and self._execution.pending_intent is None
        ):
            lifecycle_after = "paused"
            lifecycle_reason = "manual pause flattened at trusted eligible price"
            self._control = None
        elif self._control == "permanent_stop" and (
            after_position is None and self._execution.pending_intent is None
        ):
            lifecycle_after = "permanently_stopped"
            lifecycle_reason = "permanent stop flattened at trusted eligible price"
            self._control = None
        else:
            flatten_pending = self._control is not None
        fills = _fill_writes(
            before_position=before_position,
            after_position=after_position,
            update=execution_update,
            contract=self._contract,
        )
        step = RuntimeStepWrite(
            decision_payload_json=(
                None if intent is None else _intent_json(intent)
            ),
            intent_payload_json=None if intent is None else _intent_json(intent),
            event_payloads_json=tuple(
                _event_json(event)
                for event in (*strategy_update.events, *execution_update.events)
            ),
            fills=fills,
            completed_trade=(
                None
                if completed_trade is None
                else _trade_write(completed_trade, self._contract)
            ),
            position_quantity=account["position_quantity"],
            average_entry_price=account["average_entry_price"],
            stop_price=account["stop_price"],
            target_price=account["target_price"],
            cash=account["cash"],
            equity=account["equity"],
            realized_pnl=account["realized_pnl"],
            unrealized_pnl=account["unrealized_pnl"],
            realized_r=account["realized_r"],
            unrealized_r=account["unrealized_r"],
            equity_high_water_r=self._safety.equity_high_water_r,
            drawdown_r=self._safety.drawdown_r,
            losing_streak=self._safety.losing_streak,
            pending_intent_count=int(self._execution.pending_intent is not None),
            flatten_pending=flatten_pending,
            safety_trigger=safety_trigger,
            lifecycle_after=lifecycle_after,
            lifecycle_reason=lifecycle_reason,
        )
        committed = self._store.commit_runtime_step(
            trader_id=self._trader_id,
            input_id=market_input.input_id,
            step=step,
            recovered_processing=market_input.source_kind == "recovered",
            now=_parse_utc(market_input.received_at),
        )
        if not committed:
            return RuntimeProcessResult(
                input_id=market_input.input_id,
                duplicate=True,
                decision_count_delta=0,
                fill_count_delta=0,
                trade_count_delta=0,
                lifecycle=self.snapshot().lifecycle,
            )
        self._realized_pnl = account["realized_pnl"]
        self._realized_r = account["realized_r"]
        self._last_input_at = market_input.event_at
        return RuntimeProcessResult(
            input_id=market_input.input_id,
            duplicate=False,
            decision_count_delta=int(intent is not None),
            fill_count_delta=len(fills),
            trade_count_delta=int(completed_trade is not None),
            lifecycle=self.snapshot().lifecycle,
        )

    def snapshot(self) -> RuntimeSnapshot:
        projection = self._store.runtime_projection(self._trader_id)
        return RuntimeSnapshot(
            trader_id=self._trader_id,
            lifecycle=str(projection["lifecycle"]),
            lifecycle_version=int(projection["lifecycle_version"]),
            cash=float(projection["cash"]),
            equity=float(projection["equity"]),
            realized_pnl=float(projection["realized_pnl"]),
            unrealized_pnl=float(projection["unrealized_pnl"]),
            realized_r=float(projection["realized_r"]),
            unrealized_r=float(projection["unrealized_r"]),
            position_quantity=int(projection["position_quantity"]),
            pending_intent_count=int(projection["pending_intent_count"]),
            flatten_pending=bool(projection["flatten_pending"]),
            decision_count=int(projection["decision_count"]),
            trade_count=int(projection["trade_count"]),
            losing_streak=int(projection["losing_streak"]),
            drawdown_r=float(projection["drawdown_r"]),
        )

    def _account_after(
        self,
        *,
        market_input: ClosedMarketInput,
        position: ExecutionPosition | None,
        completed_trade: ExecutionTrade | None,
    ) -> dict[str, Any]:
        realized_pnl = self._realized_pnl
        realized_r = self._realized_r
        if completed_trade is not None:
            risk = (
                abs(completed_trade.entry_price - completed_trade.stop_price)
                * self._contract.point_value
                * completed_trade.quantity
            )
            if risk <= 0:
                raise RuntimeError("completed trade has no positive risk denominator")
            realized_pnl += completed_trade.net_pnl
            realized_r += completed_trade.net_pnl / risk
        unrealized_pnl = 0.0
        unrealized_r = 0.0
        position_quantity = 0
        average_entry_price: float | None = None
        stop_price: float | None = None
        target_price: float | None = None
        if position is not None:
            sign = 1 if position.direction is Direction.LONG else -1
            position_quantity = sign * position.quantity
            points = (market_input.close_price - position.entry_price) * sign
            unrealized_pnl = (
                points * self._contract.point_value * position.quantity
                - position.entry_commission
            )
            risk = (
                abs(position.entry_price - position.stop_price)
                * self._contract.point_value
                * position.quantity
            )
            unrealized_r = unrealized_pnl / risk
            average_entry_price = position.entry_price
            stop_price = position.stop_price
            target_price = position.target_price
        cash = self._initial_capital + realized_pnl
        equity = cash + unrealized_pnl
        return {
            "cash": cash,
            "equity": equity,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "realized_r": realized_r,
            "unrealized_r": unrealized_r,
            "position_quantity": position_quantity,
            "average_entry_price": average_entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
        }


def _fill_writes(
    *,
    before_position: ExecutionPosition | None,
    after_position: ExecutionPosition | None,
    update: ExecutionUpdate,
    contract: ContractSpec,
) -> tuple[SimulatedFillWrite, ...]:
    fills: list[SimulatedFillWrite] = []
    entry_events = tuple(
        event
        for event in update.events
        if event.event_type is StrategyEventType.ENTRY_FILLED
    )
    exit_events = tuple(
        event
        for event in update.events
        if event.event_type
        in (
            StrategyEventType.POSITION_CLOSED,
            StrategyEventType.POSITION_FORCED_CLOSED,
        )
    )
    for event in entry_events:
        details = dict(event.details)
        direction = event.direction
        quantity = _integer_detail(details, "quantity", default=1)
        signed = quantity if direction is Direction.LONG else -quantity
        price = _required_event_price(event)
        ticks = _integer_detail(details, "slippage_ticks", default=0)
        fills.append(
            SimulatedFillWrite(
                role="entry",
                quantity=signed,
                price=price,
                commission=_number_detail(
                    details,
                    "commission_applied",
                    default=0,
                ),
                slippage=ticks * contract.tick_size,
                payload_json=_event_json(event),
            )
        )
    for event in exit_events:
        details = dict(event.details)
        prior = before_position
        if prior is None and update.trades:
            trade = update.trades[0]
            signed = -trade.quantity if trade.direction is Direction.LONG else trade.quantity
            commission = trade.total_commission / 2
            ticks = trade.exit_slippage_ticks
        elif prior is not None:
            signed = (
                -prior.quantity
                if prior.direction is Direction.LONG
                else prior.quantity
            )
            commission = _number_detail(
                details,
                "commission_applied",
                default=0,
            )
            ticks = _integer_detail(details, "slippage_ticks", default=0)
        else:
            raise RuntimeError("exit event has no position identity")
        fills.append(
            SimulatedFillWrite(
                role="exit",
                quantity=signed,
                price=_required_event_price(event),
                commission=commission,
                slippage=ticks * contract.tick_size,
                payload_json=_event_json(event),
            )
        )
    if after_position is not None and not entry_events and before_position is None:
        raise RuntimeError("position appeared without shared entry fill event")
    return tuple(fills)


def _trade_write(
    trade: ExecutionTrade,
    contract: ContractSpec,
) -> CompletedTradeWrite:
    risk = (
        abs(trade.entry_price - trade.stop_price)
        * contract.point_value
        * trade.quantity
    )
    if risk <= 0:
        raise RuntimeError("trade risk denominator must be positive")
    return CompletedTradeWrite(
        side="long" if trade.direction is Direction.LONG else "short",
        quantity=trade.quantity,
        gross_pnl=trade.gross_pnl,
        net_pnl=trade.net_pnl,
        net_r=trade.net_pnl / risk,
        opened_at=canonical_utc(trade.entry_timestamp),
        closed_at=canonical_utc(trade.exit_timestamp),
    )


def _intent_json(intent: EntryIntent) -> str:
    return _canonical_json(
        {
            "direction": intent.direction.value,
            "entry_reference": intent.entry_reference,
            "stop_reference": intent.stop_reference,
            "signal_kind": intent.signal_kind.value,
            "signal_timestamp": canonical_utc(intent.signal_timestamp),
            "timestamp": canonical_utc(intent.timestamp),
            "ts_init": canonical_utc(intent.ts_init),
        }
    )


def _event_json(event: StrategyEvent) -> str:
    return _canonical_json(
        {
            "sequence": event.sequence,
            "timestamp": canonical_utc(event.timestamp),
            "ts_init": canonical_utc(event.ts_init),
            "phase": event.phase.value,
            "machine": event.machine,
            "event_type": event.event_type.value,
            "from_state": event.from_state,
            "to_state": event.to_state,
            "direction": event.direction.value,
            "price": event.price,
            "details": _json_value(dict(event.details)),
        }
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return canonical_utc(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _required_event_price(event: StrategyEvent) -> float:
    if event.price is None:
        raise RuntimeError("shared fill event has no price")
    return event.price


def _number_detail(
    details: dict[str, str | float | int | bool | tuple[str, ...]],
    key: str,
    *,
    default: int,
) -> float:
    value = details.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"shared execution event {key} is not numeric")
    return float(value)


def _integer_detail(
    details: dict[str, str | float | int | bool | tuple[str, ...]],
    key: str,
    *,
    default: int,
) -> int:
    value = details.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"shared execution event {key} is not an integer")
    return value


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
