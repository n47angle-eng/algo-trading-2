"""Shared live/backtest session engine: full-day causal replay + resume prefix.

Live paper = completed bars → run_session → append only new events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from futures_research.daytrade.fill_policy import BarOHLC, action_from_position, resolve_fill
from futures_research.daytrade.models import (
    DayBar,
    EventKind,
    OpenPosition,
    PositionSide,
    PrefixMismatchError,
    SessionConfig,
    SessionEvent,
    SessionResult,
)
from futures_research.daytrade.modes import (
    FillPricePolicy,
    get_execution_mode,
)


def _parse_hhmm(value: str) -> time:
    hour_s, minute_s = value.strip().split(":", 1)
    return time(hour=int(hour_s), minute=int(minute_s))


def _local_time(ts: datetime, tz_name: str) -> time:
    if ts.tzinfo is None:
        raise ValueError("bar timestamps must be timezone-aware")
    return ts.astimezone(ZoneInfo(tz_name)).time()


def _local_dt(ts: datetime, tz_name: str) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("bar timestamps must be timezone-aware")
    return ts.astimezone(ZoneInfo(tz_name))


@dataclass(slots=True)
class _EngineState:
    cash: float
    equity: float
    realized_pnl: float
    position: OpenPosition | None
    or_high: float | None
    or_low: float | None
    or_ready: bool
    no_new_entry: bool
    force_flat_done: bool
    daily_halt: bool
    overnight_breach: bool
    paused: bool
    peak_equity: float
    events: list[SessionEvent]
    ordinal: int


class SessionEngine:
    """Opening-range breakout day session (MVP strategy_id=opening_range_breakout_v1)."""

    def __init__(
        self,
        config: SessionConfig,
        *,
        fill_price_policy: FillPricePolicy,
        execution_mode_id: str,
    ) -> None:
        self.config = config
        self.fill_price_policy = fill_price_policy
        self.execution_mode_id = execution_mode_id
        mode = get_execution_mode(execution_mode_id)
        self.semantics_version = mode.semantics_version
        self.bar_mode = mode.bar_mode

    def run(
        self,
        *,
        trading_date: date,
        bars: list[DayBar],
        starting_equity: float,
        complete: bool,
        committed_events: list[SessionEvent] | None = None,
        force_flat_command: bool = False,
        paused: bool = False,
    ) -> SessionResult:
        cfg = self.config
        if cfg.strategy_id != "opening_range_breakout_v1":
            raise ValueError(f"unsupported strategy_id={cfg.strategy_id!r}")

        state = _EngineState(
            cash=float(starting_equity),
            equity=float(starting_equity),
            realized_pnl=0.0,
            position=None,
            or_high=None,
            or_low=None,
            or_ready=False,
            no_new_entry=False,
            force_flat_done=False,
            daily_halt=False,
            overnight_breach=False,
            paused=paused,
            peak_equity=float(starting_equity),
            events=[],
            ordinal=0,
        )

        rth_start = _parse_hhmm(cfg.rth_start)
        rth_end = _parse_hhmm(cfg.rth_end)
        no_new = _parse_hhmm(cfg.no_new_entry_after)
        force_flat = _parse_hhmm(cfg.force_flat_time)
        or_end_delta = timedelta(minutes=cfg.or_minutes)

        rth_bars = [
            b
            for b in bars
            if b.symbol == cfg.symbol
            and rth_start <= _local_time(b.ts, cfg.timezone) <= rth_end
        ]
        rth_bars.sort(key=lambda b: b.ts)

        if rth_bars:
            self._emit(
                state,
                kind="SESSION_OPEN",
                ts=rth_bars[0].ts,
                note=f"RTH session {trading_date.isoformat()}",
            )

        session_open_dt: datetime | None = None
        if rth_bars:
            first_local = _local_dt(rth_bars[0].ts, cfg.timezone)
            session_open_dt = first_local.replace(
                hour=rth_start.hour,
                minute=rth_start.minute,
                second=0,
                microsecond=0,
            )

        for bar in rth_bars:
            local_t = _local_time(bar.ts, cfg.timezone)
            local_dt = _local_dt(bar.ts, cfg.timezone)

            if not state.no_new_entry and local_t >= no_new:
                state.no_new_entry = True
                self._emit(state, kind="NO_NEW_ENTRY", ts=bar.ts, note="no_new_entry_after")

            # Opening range accumulation
            if session_open_dt is not None and not state.or_ready:
                if local_dt < session_open_dt + or_end_delta:
                    state.or_high = (
                        bar.high if state.or_high is None else max(state.or_high, bar.high)
                    )
                    state.or_low = (
                        bar.low if state.or_low is None else min(state.or_low, bar.low)
                    )
                else:
                    if state.or_high is not None and state.or_low is not None:
                        state.or_ready = True
                        self._emit(
                            state,
                            kind="OR_READY",
                            ts=bar.ts,
                            note=f"OR high={state.or_high} low={state.or_low}",
                            reference=state.or_high,
                        )

            # Manage open position first (stop > target same bar)
            if state.position is not None:
                self._manage_position(state, bar)

            # Force flat window
            if (local_t >= force_flat or force_flat_command) and not state.force_flat_done:
                if state.position is not None:
                    self._close_position(
                        state,
                        bar,
                        kind="FORCE_FLAT",
                        reference=bar.close,
                        note="force_flat",
                    )
                state.force_flat_done = True

            # Entries
            can_enter = (
                state.or_ready
                and state.position is None
                and not state.no_new_entry
                and not state.force_flat_done
                and not state.daily_halt
                and not state.overnight_breach
                and not state.paused
            )
            if can_enter and state.or_high is not None and state.or_low is not None:
                self._try_entry(state, bar, state.or_high, state.or_low)

            # MTM + daily loss halt (currency vs max_daily_loss_r × 1R estimate)
            self._mark_equity(state, bar)
            one_r = cfg.tick_size * cfg.point_value * 4
            if state.position is not None:
                one_r = abs(state.position.entry_price - state.position.stop) * cfg.point_value
            if (
                one_r > 0
                and state.realized_pnl <= -cfg.max_daily_loss_r * one_r
                and not state.daily_halt
            ):
                state.daily_halt = True
                self._emit(
                    state,
                    kind="DAILY_LOSS_HALT",
                    ts=bar.ts,
                    note=f"realized_pnl={state.realized_pnl:.2f}",
                )

        # End of session completeness check
        risk_flags: list[str] = []
        if state.daily_halt:
            risk_flags.append("DAILY_LOSS_HALT")
        if state.force_flat_done:
            risk_flags.append("FORCE_FLAT_DONE")
        if complete and state.position is not None:
            state.overnight_breach = True
            risk_flags.append("OVERNIGHT_BREACH")
            last_ts = rth_bars[-1].ts if rth_bars else datetime.now(tz=ZoneInfo(cfg.timezone))
            self._emit(
                state,
                kind="OVERNIGHT_BREACH",
                ts=last_ts,
                note="position still open at session complete",
            )

        # Prefix check
        committed = committed_events or []
        prefix_matched = True
        new_start = 1
        if committed:
            if len(state.events) < len(committed):
                raise PrefixMismatchError(
                    f"replay shorter than committed: {len(state.events)} < {len(committed)}"
                )
            for idx, committed_ev in enumerate(committed):
                replay_ev = state.events[idx]
                if replay_ev.fingerprint() != committed_ev.fingerprint():
                    raise PrefixMismatchError(
                        f"prefix mismatch at ordinal={committed_ev.ordinal}"
                    )
            new_start = len(committed) + 1

        day_return = (
            (state.equity - starting_equity) / starting_equity if starting_equity else 0.0
        )
        limitations = (
            "live_scale_is_1m_close_not_comparable_to_1s_worst",
            "no_order_book_nbbo",
            "mvp_or_breakout_only",
        )

        return SessionResult(
            trader_id=cfg.trader_id,
            trading_date=trading_date,
            events=tuple(state.events),
            open_position=state.position,
            cash=state.cash,
            equity=state.equity,
            starting_equity=starting_equity,
            day_return=day_return,
            realized_pnl=state.realized_pnl,
            complete=complete,
            bar_mode=self.bar_mode,
            fill_price_policy=self.fill_price_policy,
            execution_mode_id=self.execution_mode_id,
            semantics_version=self.semantics_version,
            config_generation=cfg.config_generation,
            risk_flags=tuple(risk_flags),
            prefix_matched=prefix_matched,
            new_event_start_ordinal=new_start,
            limitations=limitations,
        )

    # --- internals ---------------------------------------------------------

    def _next_ordinal(self, state: _EngineState) -> int:
        state.ordinal += 1
        return state.ordinal

    def _emit(
        self,
        state: _EngineState,
        *,
        kind: EventKind,
        ts: datetime,
        note: str,
        side: PositionSide | None = None,
        qty: int = 0,
        price: float | None = None,
        reference: float | None = None,
        stop: float | None = None,
        target: float | None = None,
        fill_policy: FillPricePolicy | None = None,
        bar_side_used: str | None = None,
    ) -> None:
        state.events.append(
            SessionEvent(
                ordinal=self._next_ordinal(state),
                kind=kind,
                ts=ts,
                symbol=self.config.symbol,
                side=side,
                qty=qty,
                price=price,
                reference=reference,
                stop=stop,
                target=target,
                realized_pnl=state.realized_pnl,
                cash=state.cash,
                equity=state.equity,
                note=note,
                fill_policy=fill_policy,
                bar_side_used=bar_side_used,
            )
        )

    def _bar_ohlc(self, bar: DayBar) -> BarOHLC:
        return BarOHLC(
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )

    def _try_entry(
        self,
        state: _EngineState,
        bar: DayBar,
        or_high: float,
        or_low: float,
    ) -> None:
        cfg = self.config
        # Long breakout
        if bar.high >= or_high and bar.low <= or_low:
            # both sides touched — fail-closed skip ambiguous entry
            return
        if bar.high >= or_high:
            ref = or_high
            quote = resolve_fill(
                policy=self.fill_price_policy,
                action=action_from_position(position_side="long", is_open=True),
                reference=ref,
                bar=self._bar_ohlc(bar),
            )
            stop = or_low
            risk = quote.price - stop
            if risk <= 0:
                return
            target = quote.price + risk
            commission = cfg.commission_per_side
            state.cash -= commission
            state.position = OpenPosition(
                side="long",
                qty=cfg.quantity,
                entry_price=quote.price,
                entry_ts=bar.ts,
                stop=stop,
                target=target,
                entry_reference=ref,
            )
            self._emit(
                state,
                kind="OPEN",
                ts=bar.ts,
                side="long",
                qty=cfg.quantity,
                price=quote.price,
                reference=ref,
                stop=stop,
                target=target,
                note="or_breakout_long",
                fill_policy=quote.policy,
                bar_side_used=quote.bar_side_used,
            )
            return

        if bar.low <= or_low:
            ref = or_low
            quote = resolve_fill(
                policy=self.fill_price_policy,
                action=action_from_position(position_side="short", is_open=True),
                reference=ref,
                bar=self._bar_ohlc(bar),
            )
            stop = or_high
            risk = stop - quote.price
            if risk <= 0:
                return
            target = quote.price - risk
            commission = cfg.commission_per_side
            state.cash -= commission
            state.position = OpenPosition(
                side="short",
                qty=cfg.quantity,
                entry_price=quote.price,
                entry_ts=bar.ts,
                stop=stop,
                target=target,
                entry_reference=ref,
            )
            self._emit(
                state,
                kind="OPEN",
                ts=bar.ts,
                side="short",
                qty=cfg.quantity,
                price=quote.price,
                reference=ref,
                stop=stop,
                target=target,
                note="or_breakout_short",
                fill_policy=quote.policy,
                bar_side_used=quote.bar_side_used,
            )

    def _manage_position(self, state: _EngineState, bar: DayBar) -> None:
        pos = state.position
        if pos is None:
            return
        # Stop priority over target (same bar)
        if pos.side == "long":
            hit_stop = bar.low <= pos.stop
            hit_target = bar.high >= pos.target
            if hit_stop:
                self._close_position(
                    state, bar, kind="STOP", reference=pos.stop, note="stop"
                )
                return
            if hit_target:
                self._close_position(
                    state, bar, kind="TARGET", reference=pos.target, note="target"
                )
                return
        else:
            hit_stop = bar.high >= pos.stop
            hit_target = bar.low <= pos.target
            if hit_stop:
                self._close_position(
                    state, bar, kind="STOP", reference=pos.stop, note="stop"
                )
                return
            if hit_target:
                self._close_position(
                    state, bar, kind="TARGET", reference=pos.target, note="target"
                )

    def _close_position(
        self,
        state: _EngineState,
        bar: DayBar,
        *,
        kind: EventKind,
        reference: float,
        note: str,
    ) -> None:
        pos = state.position
        if pos is None:
            return
        cfg = self.config
        quote = resolve_fill(
            policy=self.fill_price_policy,
            action=action_from_position(position_side=pos.side, is_open=False),
            reference=reference,
            bar=self._bar_ohlc(bar),
        )
        if pos.side == "long":
            gross = (quote.price - pos.entry_price) * pos.qty * cfg.point_value
        else:
            gross = (pos.entry_price - quote.price) * pos.qty * cfg.point_value
        commission = cfg.commission_per_side
        net = gross - commission
        state.cash += net
        state.realized_pnl += net
        state.position = None
        state.equity = state.cash
        self._emit(
            state,
            kind=kind,
            ts=bar.ts,
            side=pos.side,
            qty=pos.qty,
            price=quote.price,
            reference=reference,
            stop=pos.stop,
            target=pos.target,
            note=note,
            fill_policy=quote.policy,
            bar_side_used=quote.bar_side_used,
        )

    def _mark_equity(self, state: _EngineState, bar: DayBar) -> None:
        cfg = self.config
        unrealized = 0.0
        if state.position is not None:
            pos = state.position
            if pos.side == "long":
                unrealized = (bar.close - pos.entry_price) * pos.qty * cfg.point_value
            else:
                unrealized = (pos.entry_price - bar.close) * pos.qty * cfg.point_value
        state.equity = state.cash + unrealized
        if state.equity > state.peak_equity:
            state.peak_equity = state.equity


def run_session(
    config: SessionConfig,
    *,
    trading_date: date,
    bars: list[DayBar],
    starting_equity: float,
    complete: bool,
    execution_mode_id: str,
    committed_events: list[SessionEvent] | None = None,
    force_flat_command: bool = False,
    paused: bool = False,
) -> SessionResult:
    mode = get_execution_mode(execution_mode_id)
    engine = SessionEngine(
        config,
        fill_price_policy=mode.fill_price_policy,
        execution_mode_id=mode.mode_id,
    )
    return engine.run(
        trading_date=trading_date,
        bars=bars,
        starting_equity=starting_equity,
        complete=complete,
        committed_events=committed_events,
        force_flat_command=force_flat_command,
        paused=paused,
    )
