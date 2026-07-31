"""Conservative shared execution semantics for backtests and future simulation.

The strategy core emits :class:`~futures_research.backtest.strategy.EntryIntent`
facts.  This module consumes those facts against canonical one-minute bars and
owns only fill, OCO, stop/target, cost, and session-close state.  It deliberately
uses the same immutable event shape as the strategy so golden fixtures and the
future chart viewer have one event contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from typing import Final

from futures_research.backtest.evidence import (
    ConservativeAssumptionCapture,
    EntryDecisionCapture,
    EventOrigin,
    EventRef,
    ExitCandidateCapture,
    ExitDecisionCapture,
    TradeDecisionCapture,
)
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    EventPhase,
    SignalKind,
    StrategyEvent,
    StrategyEventType,
)
from futures_research.data.contracts import ContractSpec, ExecutionCostSpec
from futures_research.data.models import CanonicalBar

_ONE_MINUTE: Final = timedelta(minutes=1)


class FillMode(StrEnum):
    """How an executable price was selected before order-type slippage."""

    GAP_OPEN = "gap_open"
    REFERENCE = "reference"
    SESSION_CLOSE = "session_close"


class ExitReason(StrEnum):
    """The only P1 paths that can close a netted position."""

    STOP = "stop"
    TARGET = "target"
    SESSION_CLOSE = "session_close"


@dataclass(frozen=True, slots=True)
class ExecutionPosition:
    """One immutable open net position after an entry fill has occurred."""

    contract_id: str
    direction: Direction
    signal_kind: SignalKind
    quantity: int
    signal_timestamp: datetime
    entry_timestamp: datetime
    entry_ts_init: datetime
    entry_reference: float
    entry_price: float
    stop_price: float
    target_price: float
    entry_commission: float
    entry_slippage_ticks: int
    entry_capture: EntryDecisionCapture | None = None
    entry_fill_event_ref: EventRef | None = None


@dataclass(frozen=True, slots=True)
class ExecutionTrade:
    """Immutable completed-trade facts ready for the later record/result layer."""

    contract_id: str
    direction: Direction
    signal_kind: SignalKind
    quantity: int
    signal_timestamp: datetime
    entry_timestamp: datetime
    entry_ts_init: datetime
    exit_timestamp: datetime
    exit_ts_init: datetime
    entry_reference: float
    entry_price: float
    stop_price: float
    target_price: float
    exit_price: float
    exit_reason: ExitReason
    gross_points: float
    gross_pnl: float
    total_commission: float
    net_pnl: float
    entry_slippage_ticks: int
    exit_slippage_ticks: int
    decision_capture: TradeDecisionCapture | None = None


@dataclass(frozen=True, slots=True)
class ExecutionUpdate:
    """Append-only events and completed trades from one execution boundary."""

    events: tuple[StrategyEvent, ...]
    trades: tuple[ExecutionTrade, ...]


@dataclass(frozen=True, slots=True)
class _Fill:
    """Private effective fill facts, including the configured order cost."""

    raw_price: float
    price: float
    slippage_ticks: int
    commission: float
    mode: FillMode


class ConservativeExecution:
    """A single-position execution FSM implementing TRADING_SPEC section 14.

    ``arm()`` accepts exactly one valid strategy intent.  ``process_minute()``
    then consumes start-labelled canonical one-minute bars in strict time order.
    A caller must feed the one-minute children of the intent's source interval,
    allowing different minutes to establish a deterministic stop/target order.
    A collision remaining inside one minute is recorded and resolved stop-first.
    """

    def __init__(
        self,
        contract: ContractSpec,
        *,
        quantity: int,
        costs: ExecutionCostSpec | None = None,
        event_sequence_start: int = 0,
    ) -> None:
        """Bind this simulator to one configured contract and explicit quantity."""
        if quantity <= 0:
            msg = "execution quantity must be positive"
            raise ValueError(msg)
        if event_sequence_start < 0:
            msg = "event_sequence_start must not be negative"
            raise ValueError(msg)
        if not isfinite(contract.tick_size) or contract.tick_size <= 0:
            msg = "contract tick_size must be a positive finite value"
            raise ValueError(msg)
        if not isfinite(contract.point_value) or contract.point_value <= 0:
            msg = "contract point_value must be a positive finite value"
            raise ValueError(msg)
        resolved_costs = contract.execution_costs if costs is None else costs
        if not isfinite(resolved_costs.commission_per_side):
            msg = "commission_per_side must be finite"
            raise ValueError(msg)

        self._contract = contract
        self._costs = resolved_costs
        self._quantity = quantity
        self._events: list[StrategyEvent] = []
        self._trades: list[ExecutionTrade] = []
        self._event_sequence = event_sequence_start
        self._pending_intent: EntryIntent | None = None
        self._position: ExecutionPosition | None = None
        self._last_processed_timestamp: datetime | None = None

    @property
    def event_log(self) -> tuple[StrategyEvent, ...]:
        """Expose the immutable shared event schema for charting and fixtures."""
        return tuple(self._events)

    @property
    def trades(self) -> tuple[ExecutionTrade, ...]:
        """Expose completed immutable trades without mutation access."""
        return tuple(self._trades)

    @property
    def pending_intent(self) -> EntryIntent | None:
        """Expose the single pending intent, if it has not filled or invalidated."""
        return self._pending_intent

    @property
    def position(self) -> ExecutionPosition | None:
        """Expose the single net position, if one is currently open."""
        return self._position

    @property
    def costs(self) -> ExecutionCostSpec:
        """Expose the immutable per-run cost policy selected for this execution."""
        return self._costs

    def arm(self, intent: EntryIntent) -> None:
        """Accept one strategy-approved intent before its 1m source window is replayed."""
        if self._pending_intent is not None or self._position is not None:
            msg = "cannot arm a new intent while another intent or position is active"
            raise RuntimeError(msg)
        self._pending_intent = intent

    def process_minute(
        self,
        bar: CanonicalBar,
        *,
        invalidation_reason: str | None = None,
    ) -> ExecutionUpdate:
        """Apply §14 open then intrabar semantics to one closed canonical minute.

        ``invalidation_reason`` is the explicit bridge for invalidations owned by
        higher strategy machines (for example a future LMR step-0 event).  It is
        always applied before this minute can create an entry fill.
        """
        timestamp, ts_init = self._validate_minute(bar)
        normalized_reason = _normalize_reason(invalidation_reason)
        if normalized_reason is not None and self._pending_intent is None:
            msg = "an invalidation requires an active pending entry intent"
            raise ValueError(msg)

        events: list[StrategyEvent] = []
        trades: list[ExecutionTrade] = []
        if self._position is not None:
            self._resolve_position(
                bar,
                timestamp=timestamp,
                ts_init=ts_init,
                include_open=True,
                events=events,
                trades=trades,
            )
        elif self._pending_intent is not None:
            entered_at_open = self._resolve_pending(
                bar,
                timestamp=timestamp,
                ts_init=ts_init,
                invalidation_reason=normalized_reason,
                events=events,
            )
            if self._position is not None:
                self._resolve_position(
                    bar,
                    timestamp=timestamp,
                    ts_init=ts_init,
                    include_open=entered_at_open,
                    events=events,
                    trades=trades,
                )

        self._last_processed_timestamp = timestamp
        return ExecutionUpdate(events=tuple(events), trades=tuple(trades))

    def end_session(self, final_bar: CanonicalBar) -> ExecutionUpdate:
        """Force-flat after the final minute's normal processing and clear pending work."""
        final_timestamp, final_ts_init = self._validate_minute(final_bar, allow_current=True)
        if self._last_processed_timestamp != final_timestamp:
            msg = "end_session requires the final closed minute to be processed first"
            raise ValueError(msg)
        events: list[StrategyEvent] = []
        trades: list[ExecutionTrade] = []
        if self._position is not None:
            self._close_position(
                raw_price=final_bar.close,
                reason=ExitReason.SESSION_CLOSE,
                mode=FillMode.SESSION_CLOSE,
                timestamp=final_ts_init,
                ts_init=final_ts_init,
                phase=EventPhase.DAY_END,
                candidates=(
                    ExitCandidateCapture(
                        candidate_id="session_close",
                        triggered=True,
                        reference_price=final_bar.close,
                        observed_price=final_bar.close,
                    ),
                ),
                events=events,
                trades=trades,
            )
        if self._pending_intent is not None:
            self._cancel_pending(
                timestamp=final_ts_init,
                ts_init=final_ts_init,
                phase=EventPhase.DAY_END,
                reason="day_end_clear",
                price=None,
                events=events,
            )
        return ExecutionUpdate(events=tuple(events), trades=tuple(trades))

    def _resolve_pending(
        self,
        bar: CanonicalBar,
        *,
        timestamp: datetime,
        ts_init: datetime,
        invalidation_reason: str | None,
        events: list[StrategyEvent],
    ) -> bool:
        """Resolve invalidation before an opening or intrabar entry touch."""
        intent = self._require_pending()
        if timestamp < intent.timestamp:
            return False
        if timestamp >= intent.ts_init:
            msg = "pending intent was not resolved inside its source interval"
            raise ValueError(msg)
        if invalidation_reason is not None:
            self._cancel_pending(
                timestamp=timestamp,
                ts_init=ts_init,
                phase=EventPhase.INTRABAR,
                reason=invalidation_reason,
                price=None,
                events=events,
            )
            return False

        if _stop_crossed_at_open(intent, bar):
            self._cancel_pending(
                timestamp=timestamp,
                ts_init=ts_init,
                phase=EventPhase.INTRABAR,
                reason="oco_stop_gap",
                price=bar.open,
                events=events,
            )
            return False
        if _entry_crossed_at_open(intent, bar):
            self._open_position(
                intent,
                raw_price=bar.open,
                mode=FillMode.GAP_OPEN,
                timestamp=timestamp,
                ts_init=ts_init,
                events=events,
            )
            return True

        # §14.2(a): an OCO invalidation wins over a same-minute breakout.
        if _stop_touched(intent, bar):
            self._cancel_pending(
                timestamp=timestamp,
                ts_init=ts_init,
                phase=EventPhase.INTRABAR,
                reason="oco_stop_touched",
                price=intent.stop_reference,
                events=events,
            )
            return False
        if _entry_touched(intent, bar):
            self._open_position(
                intent,
                raw_price=intent.entry_reference,
                mode=FillMode.REFERENCE,
                timestamp=timestamp,
                ts_init=ts_init,
                events=events,
            )
        return False

    def _open_position(
        self,
        intent: EntryIntent,
        *,
        raw_price: float,
        mode: FillMode,
        timestamp: datetime,
        ts_init: datetime,
        events: list[StrategyEvent],
    ) -> None:
        """Fill an eligible breakout and derive its actual-fill 1R target."""
        fill = self._entry_fill(raw_price, mode)
        if intent.direction is Direction.LONG and fill.price <= intent.stop_reference:
            msg = "long entry fill must remain above its stop reference"
            raise ValueError(msg)
        if intent.direction is Direction.SHORT and fill.price >= intent.stop_reference:
            msg = "short entry fill must remain below its stop reference"
            raise ValueError(msg)
        risk = abs(fill.price - intent.stop_reference)
        if risk <= 0:
            msg = "entry fill must leave positive risk to its stop reference"
            raise ValueError(msg)
        target_price = (
            fill.price + risk if intent.direction is Direction.LONG else fill.price - risk
        )
        self._pending_intent = None
        entry_event = self._emit(
            events,
            timestamp=timestamp,
            ts_init=ts_init,
            phase=EventPhase.INTRABAR,
            machine="execution",
            event_type=StrategyEventType.ENTRY_FILLED,
            from_state="pending",
            to_state="open",
            direction=intent.direction,
            price=fill.price,
            details={
                "signal_kind": intent.signal_kind.value,
                "fill_mode": fill.mode.value,
                "raw_fill_price": fill.raw_price,
                "slippage_ticks": fill.slippage_ticks,
                "commission_per_side_per_contract": (self._costs.commission_per_side),
                "commission_applied": fill.commission,
                "quantity": self._quantity,
                "stop_price": intent.stop_reference,
                "target_price": target_price,
            },
        )
        self._position = ExecutionPosition(
            contract_id=self._contract.contract_id,
            direction=intent.direction,
            signal_kind=intent.signal_kind,
            quantity=self._quantity,
            signal_timestamp=intent.signal_timestamp,
            entry_timestamp=timestamp,
            entry_ts_init=ts_init,
            entry_reference=intent.entry_reference,
            entry_price=fill.price,
            stop_price=intent.stop_reference,
            target_price=target_price,
            entry_commission=fill.commission,
            entry_slippage_ticks=fill.slippage_ticks,
            entry_capture=intent.decision_capture,
            entry_fill_event_ref=entry_event.event_ref,
        )

    def _resolve_position(
        self,
        bar: CanonicalBar,
        *,
        timestamp: datetime,
        ts_init: datetime,
        include_open: bool,
        events: list[StrategyEvent],
        trades: list[ExecutionTrade],
    ) -> None:
        """Resolve one open position, using one-minute order before stop-first fallback."""
        position = self._require_position()
        if include_open:
            open_reason = _exit_crossed_at_open(position, bar, self._costs)
            if open_reason is not None:
                open_candidates = _open_exit_candidates(position, bar, self._costs)
                self._close_position(
                    raw_price=bar.open,
                    reason=open_reason,
                    mode=FillMode.GAP_OPEN,
                    timestamp=timestamp,
                    ts_init=ts_init,
                    phase=EventPhase.INTRABAR,
                    resolution=(
                        "gap_stop_first"
                        if all(candidate.triggered for candidate in open_candidates)
                        else None
                    ),
                    candidates=open_candidates,
                    events=events,
                    trades=trades,
                )
                return

        stop_touched = _position_stop_touched(position, bar)
        target_touched = _position_target_touched(position, bar, self._costs)
        range_candidates = _range_exit_candidates(
            position,
            bar,
            stop_touched=stop_touched,
            target_touched=target_touched,
        )
        if stop_touched and target_touched:
            self._close_position(
                raw_price=position.stop_price,
                reason=ExitReason.STOP,
                mode=FillMode.REFERENCE,
                timestamp=timestamp,
                ts_init=ts_init,
                phase=EventPhase.INTRABAR,
                resolution="same_minute_stop_first",
                candidates=range_candidates,
                events=events,
                trades=trades,
            )
            return
        if stop_touched:
            self._close_position(
                raw_price=position.stop_price,
                reason=ExitReason.STOP,
                mode=FillMode.REFERENCE,
                timestamp=timestamp,
                ts_init=ts_init,
                phase=EventPhase.INTRABAR,
                candidates=range_candidates,
                events=events,
                trades=trades,
            )
            return
        if target_touched:
            self._close_position(
                raw_price=position.target_price,
                reason=ExitReason.TARGET,
                mode=FillMode.REFERENCE,
                timestamp=timestamp,
                ts_init=ts_init,
                phase=EventPhase.INTRABAR,
                candidates=range_candidates,
                events=events,
                trades=trades,
            )

    def _close_position(
        self,
        *,
        raw_price: float,
        reason: ExitReason,
        mode: FillMode,
        timestamp: datetime,
        ts_init: datetime,
        phase: EventPhase,
        events: list[StrategyEvent],
        trades: list[ExecutionTrade],
        resolution: str | None = None,
        candidates: tuple[ExitCandidateCapture, ...] = (),
    ) -> None:
        """Close the net position, apply the order-type cost, and record one trade."""
        position = self._require_position()
        fill = self._exit_fill(raw_price, reason, mode, position.direction)
        gross_points = (
            fill.price - position.entry_price
            if position.direction is Direction.LONG
            else position.entry_price - fill.price
        )
        gross_pnl = gross_points * self._contract.point_value * position.quantity
        total_commission = position.entry_commission + fill.commission
        net_pnl = gross_pnl - total_commission
        self._position = None
        details: dict[str, str | float | int | bool | tuple[str, ...]] = {
            "exit_reason": reason.value,
            "fill_mode": fill.mode.value,
            "raw_fill_price": fill.raw_price,
            "slippage_ticks": fill.slippage_ticks,
            "commission_total": total_commission,
            "gross_points": gross_points,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
        }
        if resolution is not None:
            details["resolution"] = resolution
        exit_event = self._emit(
            events,
            timestamp=timestamp,
            ts_init=ts_init,
            phase=phase,
            machine="execution",
            event_type=(
                StrategyEventType.POSITION_FORCED_CLOSED
                if reason is ExitReason.SESSION_CLOSE
                else StrategyEventType.POSITION_CLOSED
            ),
            from_state="open",
            to_state="flat",
            direction=position.direction,
            price=fill.price,
            details=details,
        )
        capture: TradeDecisionCapture | None = None
        if position.entry_capture is not None and position.entry_fill_event_ref is not None:
            selected_candidate_id = (
                "session_close" if reason is ExitReason.SESSION_CLOSE else reason.value
            )
            if not candidates:
                msg = "captured execution exit lacks same-bar candidates"
                raise RuntimeError(msg)
            capture = TradeDecisionCapture(
                entry=position.entry_capture,
                entry_timestamp=position.entry_timestamp,
                fill_price=position.entry_price,
                entry_fill_event_ref=position.entry_fill_event_ref,
                exit=ExitDecisionCapture(
                    reason=reason.value,
                    timestamp=timestamp,
                    raw_exit_price=fill.price,
                    selected_candidate_id=selected_candidate_id,
                    candidates=candidates,
                    resolution_code=resolution,
                    event_ref=exit_event.event_ref,
                ),
                entry_slippage_ticks=position.entry_slippage_ticks,
                exit_slippage_ticks=fill.slippage_ticks,
                assumptions=(
                    ConservativeAssumptionCapture(
                        code="adverse_entry_slippage",
                        applied=position.entry_slippage_ticks > 0,
                        effects=("entry_price",),
                        source_event_refs=(position.entry_fill_event_ref,),
                    ),
                    ConservativeAssumptionCapture(
                        code="adverse_exit_slippage",
                        applied=fill.slippage_ticks > 0,
                        effects=("exit_price",),
                        source_event_refs=(exit_event.event_ref,),
                    ),
                    ConservativeAssumptionCapture(
                        code="same_minute_stop_first",
                        applied=resolution in {"same_minute_stop_first", "gap_stop_first"},
                        effects=("exit_reason", "exit_price", "event_order"),
                        source_event_refs=(exit_event.event_ref,),
                    ),
                ),
            )
        trade = ExecutionTrade(
            contract_id=position.contract_id,
            direction=position.direction,
            signal_kind=position.signal_kind,
            quantity=position.quantity,
            signal_timestamp=position.signal_timestamp,
            entry_timestamp=position.entry_timestamp,
            entry_ts_init=position.entry_ts_init,
            exit_timestamp=timestamp,
            exit_ts_init=ts_init,
            entry_reference=position.entry_reference,
            entry_price=position.entry_price,
            stop_price=position.stop_price,
            target_price=position.target_price,
            exit_price=fill.price,
            exit_reason=reason,
            gross_points=gross_points,
            gross_pnl=gross_pnl,
            total_commission=total_commission,
            net_pnl=net_pnl,
            entry_slippage_ticks=position.entry_slippage_ticks,
            exit_slippage_ticks=fill.slippage_ticks,
            decision_capture=capture,
        )
        self._trades.append(trade)
        trades.append(trade)

    def _cancel_pending(
        self,
        *,
        timestamp: datetime,
        ts_init: datetime,
        phase: EventPhase,
        reason: str,
        price: float | None,
        events: list[StrategyEvent],
    ) -> None:
        """Cancel the current intent exactly once and expose the conservative reason."""
        intent = self._require_pending()
        self._pending_intent = None
        self._emit(
            events,
            timestamp=timestamp,
            ts_init=ts_init,
            phase=phase,
            machine="execution",
            event_type=StrategyEventType.SIGNAL_CANCELLED,
            from_state="pending",
            to_state=None,
            direction=intent.direction,
            price=price,
            details={"reason": reason, "signal_kind": intent.signal_kind.value},
        )

    def _entry_fill(self, raw_price: float, mode: FillMode) -> _Fill:
        """Apply configured adverse breakout cost to an entry marketable order."""
        intent = self._require_pending()
        ticks = self._costs.slippage_ticks.breakout_entry
        return _Fill(
            raw_price=raw_price,
            price=self._with_adverse_slippage(
                raw_price,
                direction=intent.direction,
                ticks=ticks,
                is_entry=True,
            ),
            slippage_ticks=ticks,
            commission=self._commission_per_side(),
            mode=mode,
        )

    def _exit_fill(
        self,
        raw_price: float,
        reason: ExitReason,
        mode: FillMode,
        direction: Direction,
    ) -> _Fill:
        """Apply configured adverse cost to one of the three P1 exit order types."""
        ticks = self._exit_slippage_ticks(reason)
        return _Fill(
            raw_price=raw_price,
            price=self._with_adverse_slippage(
                raw_price,
                direction=direction,
                ticks=ticks,
                is_entry=False,
            ),
            slippage_ticks=ticks,
            commission=self._commission_per_side(),
            mode=mode,
        )

    def _with_adverse_slippage(
        self,
        price: float,
        *,
        direction: Direction,
        ticks: int,
        is_entry: bool,
    ) -> float:
        """Move a fill against its direction in exact configured tick increments."""
        if direction is Direction.NONE:
            msg = "an execution fill requires an executable direction"
            raise ValueError(msg)
        sign = 1 if direction is Direction.LONG else -1
        if not is_entry:
            sign *= -1
        tick_value = Decimal(str(self._contract.tick_size))
        adjusted = Decimal(str(price)) + (Decimal(ticks) * tick_value * sign)
        return float(adjusted)

    def _commission_per_side(self) -> float:
        """Return one side's configured all-in commission for this explicit quantity."""
        return self._costs.commission_per_side * self._quantity

    def _exit_slippage_ticks(self, reason: ExitReason) -> int:
        """Select the Owner-approved slippage bucket for a closed position."""
        costs = self._costs.slippage_ticks
        if reason is ExitReason.STOP:
            return costs.stop_exit
        if reason is ExitReason.TARGET:
            return costs.target_exit
        return costs.day_end_exit

    def _validate_minute(
        self,
        bar: CanonicalBar,
        *,
        allow_current: bool = False,
    ) -> tuple[datetime, datetime]:
        """Reject wrong-contract, non-minute, or non-monotonic execution input."""
        if bar.contract_id != self._contract.contract_id:
            msg = (
                f"execution contract mismatch: expected {self._contract.contract_id}, "
                f"got {bar.contract_id}"
            )
            raise ValueError(msg)
        timestamp = _as_utc(bar.timestamp, field_name="bar.timestamp")
        if timestamp.second != 0 or timestamp.microsecond != 0:
            msg = "canonical execution bars must begin on exact minute boundaries"
            raise ValueError(msg)
        if self._last_processed_timestamp is not None and (
            timestamp < self._last_processed_timestamp
            or (timestamp == self._last_processed_timestamp and not allow_current)
        ):
            msg = "canonical execution bars must be processed in strict timestamp order"
            raise ValueError(msg)
        return timestamp, timestamp + _ONE_MINUTE

    def _require_pending(self) -> EntryIntent:
        """Return the active intent or fail loudly at an invalid FSM transition."""
        if self._pending_intent is None:
            msg = "execution has no pending entry intent"
            raise RuntimeError(msg)
        return self._pending_intent

    def _require_position(self) -> ExecutionPosition:
        """Return the open position or fail loudly at an invalid FSM transition."""
        if self._position is None:
            msg = "execution has no open position"
            raise RuntimeError(msg)
        return self._position

    def _emit(
        self,
        events: list[StrategyEvent],
        *,
        timestamp: datetime,
        ts_init: datetime,
        phase: EventPhase,
        machine: str,
        event_type: StrategyEventType,
        from_state: str | None,
        to_state: str | None,
        direction: Direction,
        price: float | None,
        details: dict[str, str | float | int | bool | tuple[str, ...]],
    ) -> StrategyEvent:
        """Append one immutable event in the same shape used by the strategy FSM."""
        self._event_sequence += 1
        event = StrategyEvent(
            sequence=self._event_sequence,
            timestamp=timestamp,
            ts_init=ts_init,
            phase=phase,
            machine=machine,
            event_type=event_type,
            from_state=from_state,
            to_state=to_state,
            direction=direction,
            price=price,
            details=details,
            origin=EventOrigin.EXECUTION,
        )
        events.append(event)
        self._events.append(event)
        return event


def _entry_crossed_at_open(intent: EntryIntent, bar: CanonicalBar) -> bool:
    """Return whether the opening print has already crossed a breakout level."""
    return (
        bar.open >= intent.entry_reference
        if intent.direction is Direction.LONG
        else bar.open <= intent.entry_reference
    )


def _entry_touched(intent: EntryIntent, bar: CanonicalBar) -> bool:
    """Return whether a non-opening intrabar range reaches the breakout level."""
    return (
        bar.high >= intent.entry_reference
        if intent.direction is Direction.LONG
        else bar.low <= intent.entry_reference
    )


def _stop_crossed_at_open(intent: EntryIntent, bar: CanonicalBar) -> bool:
    """Return whether a pending OCO stop is already invalid at the open."""
    return (
        bar.open <= intent.stop_reference
        if intent.direction is Direction.LONG
        else bar.open >= intent.stop_reference
    )


def _stop_touched(intent: EntryIntent, bar: CanonicalBar) -> bool:
    """Return whether a pending OCO stop occurs before same-minute entry handling."""
    return (
        bar.low <= intent.stop_reference
        if intent.direction is Direction.LONG
        else bar.high >= intent.stop_reference
    )


def _exit_crossed_at_open(
    position: ExecutionPosition,
    bar: CanonicalBar,
    costs: ExecutionCostSpec,
) -> ExitReason | None:
    """Resolve an open gap for an existing position at its actual opening price."""
    if position.direction is Direction.LONG:
        if bar.open <= position.stop_price:
            return ExitReason.STOP
        if _target_price_reached(
            bar.open,
            position.direction,
            position.target_price,
            costs,
        ):
            return ExitReason.TARGET
    else:
        if bar.open >= position.stop_price:
            return ExitReason.STOP
        if _target_price_reached(
            bar.open,
            position.direction,
            position.target_price,
            costs,
        ):
            return ExitReason.TARGET
    return None


def _open_exit_candidates(
    position: ExecutionPosition,
    bar: CanonicalBar,
    costs: ExecutionCostSpec,
) -> tuple[ExitCandidateCapture, ...]:
    """Capture both opening-gap candidates before stop-first selection."""
    if position.direction is Direction.LONG:
        stop_triggered = bar.open <= position.stop_price
        target_triggered = _target_price_reached(
            bar.open,
            position.direction,
            position.target_price,
            costs,
        )
    else:
        stop_triggered = bar.open >= position.stop_price
        target_triggered = _target_price_reached(
            bar.open,
            position.direction,
            position.target_price,
            costs,
        )
    return (
        ExitCandidateCapture(
            candidate_id="stop",
            triggered=stop_triggered,
            reference_price=position.stop_price,
            observed_price=bar.open,
        ),
        ExitCandidateCapture(
            candidate_id="target",
            triggered=target_triggered,
            reference_price=position.target_price,
            observed_price=bar.open,
        ),
    )


def _range_exit_candidates(
    position: ExecutionPosition,
    bar: CanonicalBar,
    *,
    stop_touched: bool,
    target_touched: bool,
) -> tuple[ExitCandidateCapture, ...]:
    """Capture all same-minute stop/target competitors without ranking them later."""
    target_observed = bar.high if position.direction is Direction.LONG else bar.low
    stop_observed = bar.low if position.direction is Direction.LONG else bar.high
    return (
        ExitCandidateCapture(
            candidate_id="stop",
            triggered=stop_touched,
            reference_price=position.stop_price,
            observed_price=stop_observed,
        ),
        ExitCandidateCapture(
            candidate_id="target",
            triggered=target_touched,
            reference_price=position.target_price,
            observed_price=target_observed,
        ),
    )


def _position_stop_touched(position: ExecutionPosition, bar: CanonicalBar) -> bool:
    """Return whether the one-minute range reaches the protective stop."""
    return (
        bar.low <= position.stop_price
        if position.direction is Direction.LONG
        else bar.high >= position.stop_price
    )


def _position_target_touched(
    position: ExecutionPosition,
    bar: CanonicalBar,
    costs: ExecutionCostSpec,
) -> bool:
    """Return whether the one-minute range reaches the 1R target under P1 rules."""
    candidate = bar.high if position.direction is Direction.LONG else bar.low
    return _target_price_reached(candidate, position.direction, position.target_price, costs)


def _target_price_reached(
    candidate: float,
    direction: Direction,
    target_price: float,
    costs: ExecutionCostSpec,
) -> bool:
    """Apply normal P1 target touch semantics, leaving the P2 pass-through switch ready."""
    requires_through = costs.target_requires_through
    if direction is Direction.LONG:
        return candidate > target_price if requires_through else candidate >= target_price
    return candidate < target_price if requires_through else candidate <= target_price


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    """Require aware timestamps and normalize them to the canonical UTC policy."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name} must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _normalize_reason(value: str | None) -> str | None:
    """Normalize an optional caller-owned invalidation without silently accepting blanks."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        msg = "invalidation_reason must not be empty when supplied"
        raise ValueError(msg)
    return normalized
