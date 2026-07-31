"""Pure closed-bar Trend strategy state machines for the WO-002 backtest path.

The module deliberately separates strategy decisions from fills.  It consumes
already-precomputed MTF snapshots, emits deterministic entry intents, and records
every state transition for both golden tests and the future chart viewer.  The
execution adapter in the next WO-002 segment owns gap prices, fills, stops, and
targets.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from futures_research.backtest.evidence import (
    ConditionObservation,
    EntryDecisionCapture,
    EventOrigin,
    EventRef,
    RejectionCapture,
    SignalEvaluationContext,
)
from futures_research.backtest.mtf import (
    AggregatedBar,
    IndicatorSnapshot,
    MtfSeries,
    RegimeCalibration,
    Timeframe,
)

_DEFAULT_PERCENTILE: Final = 65.0
_DEFAULT_CONGESTION_SHRINK: Final = 0.6
_DEFAULT_DAILY_CONGESTION_LOOKBACK: Final = 3
_DEFAULT_DAILY_SLOPE_LOOKBACK: Final = 5
_DEFAULT_INTRADAY_SLOPE_LOOKBACK: Final = 10


class Direction(StrEnum):
    """The strategy's two executable directions plus the unavailable state."""

    NONE = "none"
    LONG = "long"
    SHORT = "short"


class Regime(StrEnum):
    """The closed-bar regime labels used by the P1 Daily gate."""

    UNAVAILABLE = "unavailable"
    TREND = "trend"
    RANGE = "range"
    CONGESTION = "congestion"


class PullbackState(StrEnum):
    """The first-pullback lifecycle states from TRADING_SPEC section 12."""

    IDLE = "idle"
    AWAITING_TOUCH = "awaiting_touch"
    AWAITING_SIGNAL = "awaiting_signal"
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"


class SignalKind(StrEnum):
    """P1 signal bars; LMR remains explicitly outside this strategy version."""

    INSIDE = "inside"
    MAGIC = "magic"


class EventPhase(StrEnum):
    """Where a state change belongs in the conservative per-bar event sequence."""

    INTRABAR = "intrabar"
    CLOSE = "close"
    DAY_END = "day_end"


class StrategyEventType(StrEnum):
    """Stable core/event vocabulary shared by fixtures and the future chart API."""

    DAILY_REGIME_CHANGED = "daily_regime_changed"
    MID_DIRECTION_CHANGED = "mid_direction_changed"
    PULLBACK_ARMED = "pullback_armed"
    PULLBACK_TOUCHED = "pullback_touched"
    PULLBACK_EXHAUSTED = "pullback_exhausted"
    PULLBACK_INVALIDATED = "pullback_invalidated"
    PULLBACK_COMPLETED = "pullback_completed"
    SIGNAL_CREATED = "signal_created"
    SIGNAL_CANCELLED = "signal_cancelled"
    SIGNAL_REJECTED = "signal_rejected"
    ENTRY_INTENT_CREATED = "entry_intent_created"
    ENTRY_FILLED = "entry_filled"
    POSITION_CLOSED = "position_closed"
    POSITION_FORCED_CLOSED = "position_forced_closed"
    DAY_RESET = "day_reset"


class TimeframeTrio(BaseModel):
    """The fixed P1 D/1H/5m trio, named to match ``strategy.v1``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bias: Literal[Timeframe.D1] = Timeframe.D1
    mid: Literal[Timeframe.H1] = Timeframe.H1
    entry: Literal[Timeframe.M5] = Timeframe.M5


class RegimeSettings(BaseModel):
    """P1 values for the Daily normalized EMA/ATR regime gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    require_trend: Literal[True] = True
    congestion_no_trade: Literal[True] = True
    # Default remains 65 (spec band). Floor 50 allows explicit validation runs (channel [056]).
    separation_percentile: float = Field(default=_DEFAULT_PERCENTILE, ge=50.0, le=70.0)
    slope_percentile: float = Field(default=_DEFAULT_PERCENTILE, ge=50.0, le=70.0)
    daily_slope_lookback: Literal[5] = _DEFAULT_DAILY_SLOPE_LOOKBACK
    intraday_slope_lookback: Literal[10] = _DEFAULT_INTRADAY_SLOPE_LOOKBACK
    congestion_shrink_multiplier: float = Field(
        default=_DEFAULT_CONGESTION_SHRINK,
        ge=0.5,
        le=0.7,
    )
    daily_congestion_lookback: int = Field(
        default=_DEFAULT_DAILY_CONGESTION_LOOKBACK,
        ge=2,
        le=3,
    )


class DirectionSettings(BaseModel):
    """The P1 Trend-only direction constraints from the strategy file contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["trend_following"] = "trend_following"
    layer_consistency: Literal["hard"] = "hard"


class EntrySettings(BaseModel):
    """P1 three-layer pullback/signal parameters, aligned with ``strategy.v1``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_layers: Literal[3] = 3
    signal_bars: tuple[SignalKind, ...] = (SignalKind.INSIDE, SignalKind.MAGIC)
    # Spec §12.2: touch 90 (default) / touch 18 / value zone. P1 supports 90 and 18.
    pullback_ema_period: Literal[18, 90] = 90
    inside_entry_ref: Literal["mother_high"] = "mother_high"
    inside_stop_ref: Literal["mother_low"] = "mother_low"

    @field_validator("signal_bars")
    @classmethod
    def require_unique_enabled_signal_bars(
        cls,
        value: tuple[SignalKind, ...],
    ) -> tuple[SignalKind, ...]:
        """Keep the enabled P1 signal set explicit and deterministic."""
        if not value:
            msg = "at least one P1 signal bar must be enabled"
            raise ValueError(msg)
        if len(set(value)) != len(value):
            msg = "signal_bars must not contain duplicates"
            raise ValueError(msg)
        return value


class RiskSettings(BaseModel):
    """The signal-side risk references; fill mechanics arrive in the next segment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stop_offset_ticks: Literal[1] = 1
    target_r_multiple: float = Field(default=1.0, gt=0.0)


class StrategySpec(BaseModel):
    """The internal P1 strategy shape that a later ``strategy.v1`` parser maps to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    universe_session: Literal["rth", "eth"] = "rth"
    timeframes: TimeframeTrio = Field(default_factory=TimeframeTrio)
    regime: RegimeSettings = Field(default_factory=RegimeSettings)
    direction: DirectionSettings = Field(default_factory=DirectionSettings)
    entry: EntrySettings = Field(default_factory=EntrySettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)


@dataclass(frozen=True, slots=True)
class RegimeDecision:
    """One precomputed regime decision associated with an already-closed bar."""

    snapshot: IndicatorSnapshot
    regime: Regime
    direction: Direction
    normalized_separation: float | None
    normalized_slope: float | None
    recent_average_true_range: float | None


@dataclass(frozen=True, slots=True)
class RegimeSeries:
    """Immutable regime lookup matching the closed-bar semantics of ``MtfSeries``."""

    timeframe: Timeframe
    decisions: tuple[RegimeDecision, ...]
    _close_timestamps: tuple[datetime, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate decision ordering once and cache timestamps for callback lookup."""
        close_timestamps: list[datetime] = []
        previous_close: datetime | None = None
        for decision in self.decisions:
            bar = decision.snapshot.bar
            if bar.timeframe is not self.timeframe:
                msg = "regime decision timeframe does not match its series"
                raise ValueError(msg)
            if previous_close is not None and bar.ts_init <= previous_close:
                msg = "regime decisions must be strictly ordered by close timestamp"
                raise ValueError(msg)
            close_timestamps.append(bar.ts_init)
            previous_close = bar.ts_init
        object.__setattr__(self, "_close_timestamps", tuple(close_timestamps))

    def latest_closed(self, as_of: datetime) -> RegimeDecision | None:
        """Return only a decision whose source bar has already completed."""
        as_of_utc = _as_utc(as_of, field_name="as_of")
        index = bisect_right(self._close_timestamps, as_of_utc) - 1
        if index < 0:
            return None
        return self.decisions[index]


@dataclass(frozen=True, slots=True)
class PendingSignal:
    """A closed signal bar awaiting a future intrabar breakout or OCO cancellation."""

    kind: SignalKind
    direction: Direction
    entry_price: float
    stop_price: float
    source_timestamp: datetime
    source_ts_init: datetime
    inside_count: int | None = None
    stop_reference_type: str | None = None
    stop_reference_price: float | None = None
    stop_offset_ticks: int = 0
    entry_capture: EntryDecisionCapture | None = None

    def __post_init__(self) -> None:
        """Keep the trigger/stop relation and timeline valid before execution sees it."""
        source_timestamp = _as_utc(self.source_timestamp, field_name="source_timestamp")
        source_ts_init = _as_utc(self.source_ts_init, field_name="source_ts_init")
        if source_ts_init <= source_timestamp:
            msg = "signal source ts_init must be later than its timestamp"
            raise ValueError(msg)
        if not isfinite(self.entry_price) or not isfinite(self.stop_price):
            msg = "signal prices must be finite"
            raise ValueError(msg)
        if self.direction is Direction.LONG and self.stop_price >= self.entry_price:
            msg = "a long signal stop must be below its entry reference"
            raise ValueError(msg)
        if self.direction is Direction.SHORT and self.stop_price <= self.entry_price:
            msg = "a short signal stop must be above its entry reference"
            raise ValueError(msg)
        if self.direction is Direction.NONE:
            msg = "a pending signal must have an executable direction"
            raise ValueError(msg)
        if self.stop_reference_price is not None and not isfinite(self.stop_reference_price):
            msg = "signal stop reference price must be finite when supplied"
            raise ValueError(msg)
        if self.stop_offset_ticks < 0:
            msg = "signal stop offset ticks must not be negative"
            raise ValueError(msg)
        object.__setattr__(self, "source_timestamp", source_timestamp)
        object.__setattr__(self, "source_ts_init", source_ts_init)


@dataclass(frozen=True, slots=True)
class EntryIntent:
    """A strategy-side breakout fact, not yet a simulated or live fill."""

    direction: Direction
    entry_reference: float
    stop_reference: float
    signal_kind: SignalKind
    signal_timestamp: datetime
    timestamp: datetime
    ts_init: datetime
    decision_capture: EntryDecisionCapture | None = None

    def __post_init__(self) -> None:
        """Preserve source-bar timing so the execution layer cannot invent a fill time."""
        signal_timestamp = _as_utc(self.signal_timestamp, field_name="signal_timestamp")
        timestamp = _as_utc(self.timestamp, field_name="timestamp")
        ts_init = _as_utc(self.ts_init, field_name="ts_init")
        if ts_init <= timestamp:
            msg = "entry intent ts_init must be later than its trigger bar timestamp"
            raise ValueError(msg)
        object.__setattr__(self, "signal_timestamp", signal_timestamp)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "ts_init", ts_init)


EventDetail = str | float | int | bool | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyEvent:
    """One append-only strategy state transition for charting and golden assertions."""

    sequence: int
    timestamp: datetime
    ts_init: datetime
    phase: EventPhase
    machine: str
    event_type: StrategyEventType
    from_state: str | None
    to_state: str | None
    direction: Direction
    price: float | None = None
    details: Mapping[str, EventDetail] = field(default_factory=dict)
    origin: EventOrigin = EventOrigin.STRATEGY
    origin_sequence: int | None = None
    rejection_capture: RejectionCapture | None = None

    def __post_init__(self) -> None:
        """Freeze event metadata and retain both chart and causal timestamps."""
        timestamp = _as_utc(self.timestamp, field_name="timestamp")
        ts_init = _as_utc(self.ts_init, field_name="ts_init")
        if ts_init < timestamp:
            msg = "event ts_init must not be earlier than its chart timestamp"
            raise ValueError(msg)
        if self.sequence <= 0:
            msg = "event sequence must be positive"
            raise ValueError(msg)
        if self.price is not None and not isfinite(self.price):
            msg = "event price must be finite when supplied"
            raise ValueError(msg)
        origin_sequence = self.sequence if self.origin_sequence is None else self.origin_sequence
        if origin_sequence <= 0:
            msg = "event origin_sequence must be positive"
            raise ValueError(msg)
        frozen_details: Mapping[str, EventDetail] = MappingProxyType(dict(self.details))
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "ts_init", ts_init)
        object.__setattr__(self, "details", frozen_details)
        object.__setattr__(self, "origin_sequence", origin_sequence)

    @property
    def event_ref(self) -> EventRef:
        """Expose the pre-resequence identity used only by structured evidence."""
        assert self.origin_sequence is not None
        return EventRef(origin=self.origin, origin_sequence=self.origin_sequence)


@dataclass(frozen=True, slots=True)
class StrategyUpdate:
    """The deterministic results emitted while processing one closed entry bar."""

    events: tuple[StrategyEvent, ...]
    entry_intents: tuple[EntryIntent, ...]


@dataclass(frozen=True, slots=True)
class _LifecycleTransition:
    """An internal transition that the strategy translates to an immutable event."""

    event_type: StrategyEventType
    from_state: PullbackState
    to_state: PullbackState
    direction: Direction
    reason: str
    price: float | None


@dataclass(frozen=True, slots=True)
class _GateEvaluation:
    """One no-side-effect gate evaluation, including explicit short-circuit facts."""

    allowed: bool
    observations: tuple[ConditionObservation, ...]
    blocking_condition_ids: tuple[str, ...]
    reached_layers: tuple[str, ...]


class PullbackLifecycle:
    """Named FSM for the first-pullback rule in TRADING_SPEC section 12.3."""

    def __init__(self, *, pullback_ema_period: Literal[18, 90] = 90) -> None:
        """Start with no cross and no eligible first-pullback opportunity."""
        if pullback_ema_period not in (18, 90):
            msg = "pullback_ema_period must be 18 or 90 (TRADING_SPEC §12.2)"
            raise ValueError(msg)
        self._pullback_ema_period = pullback_ema_period
        self._state = PullbackState.IDLE
        self._direction = Direction.NONE
        self._swing_reference: float | None = None

    @property
    def state(self) -> PullbackState:
        """Return the current lifecycle state without exposing mutable internals."""
        return self._state

    @property
    def direction(self) -> Direction:
        """Return the direction associated with the current cross opportunity."""
        return self._direction

    @property
    def swing_reference(self) -> float | None:
        """Return the pre-touch swing used to expire a late pullback opportunity."""
        return self._swing_reference

    @property
    def is_first_pullback_qualified(self) -> bool:
        """Return whether the first configured-EMA touch remains eligible for a lower layer."""
        return self._state is PullbackState.AWAITING_SIGNAL

    def arm_from_cross(
        self,
        direction: Direction,
        bar: AggregatedBar,
    ) -> tuple[_LifecycleTransition, ...]:
        """Cancel a prior pre-entry lifecycle, then arm exactly one new first pullback."""
        if direction is Direction.NONE:
            msg = "a pullback lifecycle can only be armed from a directional cross"
            raise ValueError(msg)

        transitions: list[_LifecycleTransition] = []
        if self._state in {PullbackState.AWAITING_TOUCH, PullbackState.AWAITING_SIGNAL}:
            transitions.append(self._cancel_for_cross_reversal())

        previous_state = self._state
        self._state = PullbackState.AWAITING_TOUCH
        self._direction = direction
        self._swing_reference = bar.high if direction is Direction.LONG else bar.low
        transitions.append(
            _LifecycleTransition(
                event_type=StrategyEventType.PULLBACK_ARMED,
                from_state=previous_state,
                to_state=self._state,
                direction=direction,
                reason="ema_cross",
                price=self._swing_reference,
            )
        )
        return tuple(transitions)

    def observe_closed_bar(self, snapshot: IndicatorSnapshot) -> tuple[_LifecycleTransition, ...]:
        """Advance a non-cross bar through touch or conservative swing expiry."""
        if self._state not in {PullbackState.AWAITING_TOUCH, PullbackState.AWAITING_SIGNAL}:
            return ()
        if self._swing_reference is None:
            msg = "active pullback lifecycle must have a swing reference"
            raise RuntimeError(msg)

        bar = snapshot.bar
        touch_ema = self._touch_ema(snapshot)
        if self._state is PullbackState.AWAITING_TOUCH:
            if self._direction is Direction.LONG:
                if bar.low <= touch_ema:
                    if bar.high > self._swing_reference:
                        return (self._exhaust("touch_and_swing_reclaim_same_bar", bar.high),)
                    return (self._touch(bar.low),)
                self._swing_reference = max(self._swing_reference, bar.high)
                return ()
            if self._direction is Direction.SHORT:
                if bar.high >= touch_ema:
                    if bar.low < self._swing_reference:
                        return (self._exhaust("touch_and_swing_reclaim_same_bar", bar.low),)
                    return (self._touch(bar.high),)
                self._swing_reference = min(self._swing_reference, bar.low)
                return ()
            msg = "an active pullback lifecycle must have a direction"
            raise RuntimeError(msg)

        if self._direction is Direction.LONG and bar.high > self._swing_reference:
            return (self._exhaust("swing_reclaimed_before_entry", bar.high),)
        if self._direction is Direction.SHORT and bar.low < self._swing_reference:
            return (self._exhaust("swing_reclaimed_before_entry", bar.low),)
        return ()

    def observe_intrabar_invalidation(
        self,
        bar: AggregatedBar,
    ) -> _LifecycleTransition | None:
        """Prefer a same-bar swing reclaim over a pending breakout conservatively.

        Once a signal is pending, its breakout and the pre-touch swing reclaim can
        both appear in the same OHLC bar.  Their intrabar order is unknowable at
        this layer, so the section 12.3 expiry wins before an entry intent is
        emitted, consistent with the section 14 conservative conflict policy.
        """
        if self._state is not PullbackState.AWAITING_SIGNAL:
            return None
        if self._swing_reference is None:
            msg = "a signal-ready pullback lifecycle must have a swing reference"
            raise RuntimeError(msg)
        if self._direction is Direction.LONG and bar.high > self._swing_reference:
            return self._exhaust("swing_reclaimed_before_entry", bar.high)
        if self._direction is Direction.SHORT and bar.low < self._swing_reference:
            return self._exhaust("swing_reclaimed_before_entry", bar.low)
        return None

    def complete_entry(self) -> _LifecycleTransition:
        """Consume the one permitted first-pullback opportunity after its breakout."""
        if self._state is not PullbackState.AWAITING_SIGNAL:
            msg = "only a signal-ready pullback can complete an entry"
            raise RuntimeError(msg)
        previous_state = self._state
        self._state = PullbackState.COMPLETED
        return _LifecycleTransition(
            event_type=StrategyEventType.PULLBACK_COMPLETED,
            from_state=previous_state,
            to_state=self._state,
            direction=self._direction,
            reason="entry_intent_created",
            price=self._swing_reference,
        )

    def _touch(self, price: float) -> _LifecycleTransition:
        previous_state = self._state
        self._state = PullbackState.AWAITING_SIGNAL
        return _LifecycleTransition(
            event_type=StrategyEventType.PULLBACK_TOUCHED,
            from_state=previous_state,
            to_state=self._state,
            direction=self._direction,
            reason=f"ema_{self._pullback_ema_period}_touched",
            price=price,
        )

    def _touch_ema(self, snapshot: IndicatorSnapshot) -> float:
        """Select the configured pullback touch line (EMA 90 default, or EMA 18)."""
        if self._pullback_ema_period == 18:
            return snapshot.ema_18
        return snapshot.ema_90

    def _exhaust(self, reason: str, price: float) -> _LifecycleTransition:
        previous_state = self._state
        self._state = PullbackState.EXHAUSTED
        return _LifecycleTransition(
            event_type=StrategyEventType.PULLBACK_EXHAUSTED,
            from_state=previous_state,
            to_state=self._state,
            direction=self._direction,
            reason=reason,
            price=price,
        )

    def _cancel_for_cross_reversal(self) -> _LifecycleTransition:
        previous_state = self._state
        previous_direction = self._direction
        self._state = PullbackState.IDLE
        self._direction = Direction.NONE
        self._swing_reference = None
        return _LifecycleTransition(
            event_type=StrategyEventType.PULLBACK_INVALIDATED,
            from_state=previous_state,
            to_state=self._state,
            direction=previous_direction,
            reason="ema_cross_reversal",
            price=None,
        )


@dataclass(frozen=True, slots=True)
class _InsideRun:
    """The retained mother bar and latest count for multi-inside-bar detection."""

    mother: AggregatedBar
    count: int


def precompute_regime_series(
    series: MtfSeries,
    calibration: RegimeCalibration,
    *,
    congestion_lookback: int,
    shrink_multiplier: float,
) -> RegimeSeries:
    """Classify a series without future bars or work in a strategy callback.

    The trend criterion implements the approved normalized EMA separation and
    slope thresholds.  If a closed bar is not trend, the most recent complete
    true-range window differentiates Congestion from Range.  Warm-up records are
    ``UNAVAILABLE`` and never pass the P1 Daily gate.
    """
    if series.timeframe is not calibration.timeframe:
        msg = "regime calibration timeframe must match the series timeframe"
        raise ValueError(msg)
    if congestion_lookback <= 0:
        msg = "congestion_lookback must be positive"
        raise ValueError(msg)
    if not isfinite(shrink_multiplier) or shrink_multiplier <= 0:
        msg = "shrink_multiplier must be a positive finite value"
        raise ValueError(msg)

    true_ranges: list[float] = []
    decisions: list[RegimeDecision] = []
    previous_close: float | None = None
    for index, snapshot in enumerate(series.snapshots):
        bar = snapshot.bar
        true_range = _true_range(bar, previous_close)
        true_ranges.append(true_range)
        previous_close = bar.close

        normalized_separation: float | None = None
        normalized_slope: float | None = None
        recent_average_true_range: float | None = None
        regime = Regime.UNAVAILABLE
        direction = _direction_from_snapshot(snapshot)
        if _has_regime_inputs(snapshot, index, series, calibration.slope_lookback):
            older = series.snapshots[index - calibration.slope_lookback]
            normalized_separation = abs(snapshot.ema_18 - snapshot.ema_90) / snapshot.atr_14
            normalized_slope = abs(snapshot.ema_90 - older.ema_90) / (
                calibration.slope_lookback * snapshot.atr_14
            )
            if len(true_ranges) >= congestion_lookback:
                recent_average_true_range = sum(true_ranges[-congestion_lookback:]) / (
                    congestion_lookback
                )
                if (
                    normalized_separation >= calibration.separation_threshold
                    and normalized_slope >= calibration.slope_threshold
                ):
                    regime = Regime.TREND
                elif recent_average_true_range < snapshot.atr_14 * shrink_multiplier:
                    regime = Regime.CONGESTION
                else:
                    regime = Regime.RANGE
        decisions.append(
            RegimeDecision(
                snapshot=snapshot,
                regime=regime,
                direction=direction,
                normalized_separation=normalized_separation,
                normalized_slope=normalized_slope,
                recent_average_true_range=recent_average_true_range,
            )
        )
    return RegimeSeries(timeframe=series.timeframe, decisions=tuple(decisions))


class TrendStrategy:
    """Stateful P1 Trend flow consuming one already-closed entry-timeframe bar at a time."""

    def __init__(self, spec: StrategySpec, *, tick_size: float) -> None:
        """Create a pure strategy core with an explicit contract tick for signal stops."""
        if not isfinite(tick_size) or tick_size <= 0:
            msg = "tick_size must be a positive finite value"
            raise ValueError(msg)
        self._spec = spec
        self._tick_size = tick_size
        period = spec.entry.pullback_ema_period
        self._mid_lifecycle = PullbackLifecycle(pullback_ema_period=period)
        self._entry_lifecycle = PullbackLifecycle(pullback_ema_period=period)
        self._events: list[StrategyEvent] = []
        self._event_sequence = 0
        self._previous_entry_snapshot: IndicatorSnapshot | None = None
        self._previous_mid_snapshot: IndicatorSnapshot | None = None
        self._inside_run: _InsideRun | None = None
        self._pending_signal: PendingSignal | None = None
        self._daily_context: RegimeDecision | None = None
        self._mid_direction = Direction.NONE
        self._mid_context_close: datetime | None = None
        self._entry_locked = False
        self._signal_evaluation_sequence = 0

    @property
    def event_log(self) -> tuple[StrategyEvent, ...]:
        """Expose the append-only immutable event stream for UI and test consumers."""
        return tuple(self._events)

    @property
    def pending_signal(self) -> PendingSignal | None:
        """Expose an immutable pending signal without allowing mutation of strategy state."""
        return self._pending_signal

    @property
    def pullback_state(self) -> PullbackState:
        """Expose the entry-layer pullback state for deterministic golden fixtures."""
        return self._entry_lifecycle.state

    @property
    def pullback_direction(self) -> Direction:
        """Expose the entry-layer direction associated with the active lifecycle."""
        return self._entry_lifecycle.direction

    @property
    def mid_pullback_state(self) -> PullbackState:
        """Expose the 1H first-pullback state required by the three-layer Trend flow."""
        return self._mid_lifecycle.state

    @property
    def mid_pullback_direction(self) -> Direction:
        """Expose the direction associated with the active 1H pullback lifecycle."""
        return self._mid_lifecycle.direction

    def on_entry_bar(
        self,
        snapshot: IndicatorSnapshot,
        *,
        daily: RegimeDecision | None,
        mid: IndicatorSnapshot | None,
    ) -> StrategyUpdate:
        """Process one complete entry bar in conservative intrabar-then-close order.

        Existing signals are tested against the bar before close-state updates.
        Therefore a Daily or 1H bar that closes simultaneously cannot influence
        an intrabar breakout that happened earlier in this entry bar.
        """
        self._validate_entry_snapshot(snapshot)
        self._validate_daily_context(daily)
        self._validate_mid_context(mid)
        self._validate_causal_context(snapshot, daily=daily, mid=mid)
        self._validate_entry_order(snapshot)
        emitted_events: list[StrategyEvent] = []
        entry_intents: list[EntryIntent] = []

        self._process_pending_intrabar(snapshot, emitted_events, entry_intents)
        self._observe_daily_at_close(daily, snapshot.bar, emitted_events)
        self._observe_mid_at_close(mid, snapshot.bar, emitted_events)
        self._clear_pending_if_mid_pullback_invalidated(snapshot.bar, emitted_events)
        if not self._entry_locked:
            self._process_entry_close(snapshot, emitted_events)
        self._previous_entry_snapshot = snapshot
        return StrategyUpdate(events=tuple(emitted_events), entry_intents=tuple(entry_intents))

    def end_day(self, at: datetime) -> StrategyUpdate:
        """Clear only signal-level pending work, as required by the strategy specification."""
        timestamp = _as_utc(at, field_name="at")
        emitted_events: list[StrategyEvent] = []
        self._clear_pending_signal(
            timestamp=timestamp,
            ts_init=timestamp,
            phase=EventPhase.DAY_END,
            reason="day_end_clear",
            events=emitted_events,
        )
        if self._entry_locked:
            self._entry_locked = False
            self._emit(
                emitted_events,
                timestamp=timestamp,
                ts_init=timestamp,
                phase=EventPhase.DAY_END,
                machine="entry_lock",
                event_type=StrategyEventType.DAY_RESET,
                from_state="locked",
                to_state="ready",
                direction=Direction.NONE,
                details={"reason": "day_end_forced_flat_boundary"},
            )
        return StrategyUpdate(events=tuple(emitted_events), entry_intents=())

    def _process_pending_intrabar(
        self,
        snapshot: IndicatorSnapshot,
        events: list[StrategyEvent],
        entry_intents: list[EntryIntent],
    ) -> None:
        """Apply OCO cancellation before breakout detection to an older pending signal."""
        pending = self._pending_signal
        if pending is None or self._entry_locked:
            return
        bar = snapshot.bar
        lifecycle_invalidation = self._entry_lifecycle.observe_intrabar_invalidation(bar)
        if lifecycle_invalidation is not None:
            self._emit_lifecycle_transitions(
                lifecycle_invalidation,
                bar,
                EventPhase.INTRABAR,
                events,
            )
            self._clear_pending_signal(
                timestamp=bar.timestamp,
                ts_init=bar.ts_init,
                phase=EventPhase.INTRABAR,
                reason="pullback_exhausted",
                events=events,
            )
            return
        if not self._gate_allows(pending.direction):
            self._clear_pending_signal(
                timestamp=bar.timestamp,
                ts_init=bar.ts_init,
                phase=EventPhase.INTRABAR,
                reason="daily_or_mid_gate_changed",
                events=events,
            )
            return
        if _touches_stop(pending, bar):
            self._clear_pending_signal(
                timestamp=bar.timestamp,
                ts_init=bar.ts_init,
                phase=EventPhase.INTRABAR,
                reason="oco_stop_touched",
                events=events,
            )
            return
        if not _touches_entry(pending, bar):
            return

        intent_event = self._emit(
            events,
            timestamp=bar.timestamp,
            ts_init=bar.ts_init,
            phase=EventPhase.INTRABAR,
            machine="entry_signal",
            event_type=StrategyEventType.ENTRY_INTENT_CREATED,
            from_state="pending",
            to_state="triggered",
            direction=pending.direction,
            price=pending.entry_price,
            details={
                "signal_kind": pending.kind.value,
                "stop_reference": pending.stop_price,
            },
        )
        decision_capture = (
            pending.entry_capture.with_intent_event(intent_event.event_ref)
            if pending.entry_capture is not None
            else None
        )
        intent = EntryIntent(
            direction=pending.direction,
            entry_reference=pending.entry_price,
            stop_reference=pending.stop_price,
            signal_kind=pending.kind,
            signal_timestamp=pending.source_timestamp,
            timestamp=bar.timestamp,
            ts_init=bar.ts_init,
            decision_capture=decision_capture,
        )
        entry_intents.append(intent)
        self._pending_signal = None
        self._entry_locked = True
        self._emit_lifecycle_transitions(
            self._entry_lifecycle.complete_entry(),
            bar,
            EventPhase.INTRABAR,
            events,
        )
        self._emit_lifecycle_transitions(
            self._mid_lifecycle.complete_entry(),
            bar,
            EventPhase.INTRABAR,
            events,
            machine="mid_pullback",
        )

    def _observe_daily_at_close(
        self,
        daily: RegimeDecision | None,
        entry_bar: AggregatedBar,
        events: list[StrategyEvent],
    ) -> None:
        """Cache a newly closed Daily regime for subsequent entry-bar intrabar work."""
        if daily is None:
            return
        if (
            self._daily_context is not None
            and daily.snapshot.bar.ts_init <= self._daily_context.snapshot.bar.ts_init
        ):
            return
        previous = self._daily_context
        self._daily_context = daily
        previous_regime = previous.regime.value if previous is not None else None
        if previous is None or previous.regime is not daily.regime:
            self._emit(
                events,
                timestamp=entry_bar.timestamp,
                ts_init=entry_bar.ts_init,
                phase=EventPhase.CLOSE,
                machine="daily_regime",
                event_type=StrategyEventType.DAILY_REGIME_CHANGED,
                from_state=previous_regime,
                to_state=daily.regime.value,
                direction=daily.direction,
                details={"source_close": daily.snapshot.bar.ts_init.isoformat()},
            )

    def _observe_mid_at_close(
        self,
        mid: IndicatorSnapshot | None,
        entry_bar: AggregatedBar,
        events: list[StrategyEvent],
    ) -> None:
        """Advance the closed 1H cross/first-pullback gate for the 5m lifecycle."""
        if mid is None:
            return
        if self._mid_context_close is not None and mid.bar.ts_init <= self._mid_context_close:
            return
        previous_direction = self._mid_direction
        self._mid_context_close = mid.bar.ts_init
        self._mid_direction = _direction_from_snapshot(mid)
        if previous_direction is not self._mid_direction:
            self._emit(
                events,
                timestamp=entry_bar.timestamp,
                ts_init=entry_bar.ts_init,
                phase=EventPhase.CLOSE,
                machine="mid_direction",
                event_type=StrategyEventType.MID_DIRECTION_CHANGED,
                from_state=previous_direction.value,
                to_state=self._mid_direction.value,
                direction=self._mid_direction,
                details={"source_close": mid.bar.ts_init.isoformat()},
            )
        cross_direction = _cross_direction(self._previous_mid_snapshot, mid)
        if cross_direction is not Direction.NONE:
            transitions = self._mid_lifecycle.arm_from_cross(cross_direction, mid.bar)
        else:
            transitions = self._mid_lifecycle.observe_closed_bar(mid)
        self._emit_lifecycle_transitions(
            transitions,
            mid.bar,
            EventPhase.CLOSE,
            events,
            machine="mid_pullback",
        )
        self._previous_mid_snapshot = mid

    def _process_entry_close(
        self,
        snapshot: IndicatorSnapshot,
        events: list[StrategyEvent],
    ) -> None:
        """Update the entry-layer cross/pullback/signal FSM after the bar is closed."""
        bar = snapshot.bar
        inside_run = self._advance_inside_run(snapshot)
        cross_direction = _cross_direction(self._previous_entry_snapshot, snapshot)
        if cross_direction is not Direction.NONE:
            self._clear_pending_signal(
                timestamp=bar.timestamp,
                ts_init=bar.ts_init,
                phase=EventPhase.CLOSE,
                reason="ema_cross_reversal",
                events=events,
            )
            self._emit_lifecycle_transitions(
                self._entry_lifecycle.arm_from_cross(cross_direction, bar),
                bar,
                EventPhase.CLOSE,
                events,
            )
            return

        was_signal_ready = self._entry_lifecycle.state is PullbackState.AWAITING_SIGNAL
        transitions = self._entry_lifecycle.observe_closed_bar(snapshot)
        self._emit_lifecycle_transitions(transitions, bar, EventPhase.CLOSE, events)
        if any(
            transition.event_type is StrategyEventType.PULLBACK_EXHAUSTED
            for transition in transitions
        ):
            self._clear_pending_signal(
                timestamp=bar.timestamp,
                ts_init=bar.ts_init,
                phase=EventPhase.CLOSE,
                reason="pullback_exhausted",
                events=events,
            )
            return
        if was_signal_ready and self._entry_lifecycle.state is PullbackState.AWAITING_SIGNAL:
            self._try_create_signal(snapshot, inside_run, events)

    def _advance_inside_run(self, snapshot: IndicatorSnapshot) -> _InsideRun | None:
        """Track mother-bar continuity independently of whether a signal gate is currently open."""
        previous = self._previous_entry_snapshot
        if previous is None:
            self._inside_run = None
            return None
        current_bar = snapshot.bar
        if self._inside_run is not None and _is_inside(current_bar, self._inside_run.mother):
            self._inside_run = _InsideRun(
                mother=self._inside_run.mother,
                count=self._inside_run.count + 1,
            )
            return self._inside_run
        if _is_inside(current_bar, previous.bar):
            self._inside_run = _InsideRun(mother=previous.bar, count=1)
            return self._inside_run
        self._inside_run = None
        return None

    def _try_create_signal(
        self,
        snapshot: IndicatorSnapshot,
        inside_run: _InsideRun | None,
        events: list[StrategyEvent],
    ) -> None:
        """Create at most one post-pullback signal or conservatively reject a conflict."""
        direction = self._entry_lifecycle.direction
        bar = snapshot.bar
        candidates = self._signal_candidates(snapshot, direction, inside_run)
        if not candidates:
            return
        self._signal_evaluation_sequence += 1
        evaluation_sequence = self._signal_evaluation_sequence
        context = SignalEvaluationContext(
            candidate_signal_kinds=tuple(candidate.kind.value for candidate in candidates),
            inside_count=next(
                (
                    candidate.inside_count
                    for candidate in candidates
                    if candidate.inside_count is not None
                ),
                None,
            ),
            entry_pullback_state=self._entry_lifecycle.state.value,
            mid_pullback_state=self._mid_lifecycle.state.value,
            daily_regime=(
                self._daily_context.regime.value
                if self._daily_context is not None
                else None
            ),
        )
        observed_at = bar.ts_init
        cross_matches = _direction_from_snapshot(snapshot) is direction
        cross_observation = ConditionObservation(
            condition_id="entry_cross_state_matches_direction",
            layer_id="entry",
            observed_at=observed_at,
            status="passed" if cross_matches else "failed",
            actual=_direction_from_snapshot(snapshot).value,
            operator="eq",
            required=direction.value,
            unit="enum",
        )
        if _direction_from_snapshot(snapshot) is not direction:
            self._emit(
                events,
                timestamp=bar.timestamp,
                ts_init=bar.ts_init,
                phase=EventPhase.CLOSE,
                machine="entry_signal",
                event_type=StrategyEventType.SIGNAL_REJECTED,
                from_state="awaiting_signal",
                to_state="awaiting_signal",
                direction=direction,
                details={"reason": "entry_cross_state_mismatch"},
                rejection_capture=self._rejection_capture(
                    evaluation_sequence=evaluation_sequence,
                    context=context,
                    observations=(
                        cross_observation,
                        *self._not_evaluated_gate_observations(observed_at),
                        self._not_evaluated_candidate_observation(observed_at),
                    ),
                    blocking_condition_ids=("entry_cross_state_matches_direction",),
                    reached_layers=("entry",),
                ),
            )
            return
        gate = self._evaluate_gate(direction, observed_at=observed_at)
        if not gate.allowed:
            self._emit(
                events,
                timestamp=bar.timestamp,
                ts_init=bar.ts_init,
                phase=EventPhase.CLOSE,
                machine="entry_signal",
                event_type=StrategyEventType.SIGNAL_REJECTED,
                from_state="awaiting_signal",
                to_state="awaiting_signal",
                direction=direction,
                details={"reason": self._gate_rejection_reason(direction)},
                rejection_capture=self._rejection_capture(
                    evaluation_sequence=evaluation_sequence,
                    context=context,
                    observations=(
                        cross_observation,
                        *gate.observations,
                        self._not_evaluated_candidate_observation(observed_at),
                    ),
                    blocking_condition_ids=gate.blocking_condition_ids,
                    reached_layers=("entry", *gate.reached_layers),
                ),
            )
            return
        candidate_count_observation = ConditionObservation(
            condition_id="signal_candidate_count_is_one",
            layer_id="entry",
            observed_at=observed_at,
            status="passed" if len(candidates) == 1 else "failed",
            actual=len(candidates),
            operator="eq",
            required=1,
            unit="count",
        )
        if len(candidates) > 1:
            self._emit(
                events,
                timestamp=bar.timestamp,
                ts_init=bar.ts_init,
                phase=EventPhase.CLOSE,
                machine="entry_signal",
                event_type=StrategyEventType.SIGNAL_REJECTED,
                from_state="awaiting_signal",
                to_state="awaiting_signal",
                direction=direction,
                details={
                    "reason": "multiple_signal_bars_same_bar",
                    "candidates": tuple(candidate.kind.value for candidate in candidates),
                },
                rejection_capture=self._rejection_capture(
                    evaluation_sequence=evaluation_sequence,
                    context=context,
                    observations=(
                        cross_observation,
                        *gate.observations,
                        candidate_count_observation,
                    ),
                    blocking_condition_ids=("signal_candidate_count_is_one",),
                    reached_layers=("entry", *gate.reached_layers),
                ),
            )
            return

        signal = candidates[0]
        signal_event = self._emit(
            events,
            timestamp=bar.timestamp,
            ts_init=bar.ts_init,
            phase=EventPhase.CLOSE,
            machine="entry_signal",
            event_type=StrategyEventType.SIGNAL_CREATED,
            from_state=None,
            to_state="pending",
            direction=signal.direction,
            price=signal.entry_price,
            details={
                "signal_kind": signal.kind.value,
                "stop_reference": signal.stop_price,
                "inside_count": signal.inside_count or 0,
            },
        )
        if (
            signal.stop_reference_type is None
            or signal.stop_reference_price is None
        ):
            msg = "strategy-created signal lacks typed stop evidence"
            raise RuntimeError(msg)
        self._pending_signal = replace(
            signal,
            entry_capture=EntryDecisionCapture(
                signal_kind=signal.kind.value,
                signal_timestamp=signal.source_timestamp,
                entry_reference=signal.entry_price,
                stop_reference_type=signal.stop_reference_type,
                stop_reference_price=signal.stop_reference_price,
                stop_offset_ticks=signal.stop_offset_ticks,
                final_stop_price=signal.stop_price,
                condition_observations=(
                    cross_observation,
                    *gate.observations,
                    candidate_count_observation,
                ),
                signal_event_ref=signal_event.event_ref,
            ),
        )

    def _signal_candidates(
        self,
        snapshot: IndicatorSnapshot,
        direction: Direction,
        inside_run: _InsideRun | None,
    ) -> tuple[PendingSignal, ...]:
        """Evaluate the P1 Inside and Magic definitions against a signal-ready lifecycle."""
        candidates: list[PendingSignal] = []
        bar = snapshot.bar
        if SignalKind.INSIDE in self._spec.entry.signal_bars and inside_run is not None:
            mother = inside_run.mother
            stop_anchor = mother.low if direction is Direction.LONG else mother.high
            candidates.append(
                PendingSignal(
                    kind=SignalKind.INSIDE,
                    direction=direction,
                    entry_price=mother.high if direction is Direction.LONG else mother.low,
                    stop_price=(
                        stop_anchor - self._tick_size
                        if direction is Direction.LONG
                        else stop_anchor + self._tick_size
                    ),
                    source_timestamp=bar.timestamp,
                    source_ts_init=bar.ts_init,
                    inside_count=inside_run.count,
                    stop_reference_type=(
                        "mother_low" if direction is Direction.LONG else "mother_high"
                    ),
                    stop_reference_price=stop_anchor,
                    stop_offset_ticks=self._spec.risk.stop_offset_ticks,
                )
            )

        previous = self._previous_entry_snapshot
        if (
            SignalKind.MAGIC in self._spec.entry.signal_bars
            and previous is not None
            and _is_magic(snapshot.bar, previous.bar, direction)
        ):
            stop_anchor = bar.low if direction is Direction.LONG else bar.high
            candidates.append(
                PendingSignal(
                    kind=SignalKind.MAGIC,
                    direction=direction,
                    entry_price=bar.high if direction is Direction.LONG else bar.low,
                    stop_price=(
                        stop_anchor - self._tick_size
                        if direction is Direction.LONG
                        else stop_anchor + self._tick_size
                    ),
                    source_timestamp=bar.timestamp,
                    source_ts_init=bar.ts_init,
                    stop_reference_type=(
                        "signal_low" if direction is Direction.LONG else "signal_high"
                    ),
                    stop_reference_price=stop_anchor,
                    stop_offset_ticks=self._spec.risk.stop_offset_ticks,
                )
            )
        return tuple(candidates)

    def _gate_allows(self, direction: Direction) -> bool:
        """Apply the P1 Daily Trend-only gate and hard 1H/5m direction consistency."""
        return (
            not self._entry_locked
            and self._daily_context is not None
            and self._daily_context.regime is Regime.TREND
            and self._mid_direction is direction
            and self._mid_lifecycle.is_first_pullback_qualified
            and direction is not Direction.NONE
        )

    def _evaluate_gate(self, direction: Direction, *, observed_at: datetime) -> _GateEvaluation:
        """Evaluate the existing gate order with explicit short-circuit observations.

        This is observational only: each return path mirrors the old ``and`` chain
        exactly, while recording downstream conditions as ``not_evaluated`` rather
        than re-running a hypothetical decision after the blocker.
        """
        entry_unlocked = ConditionObservation(
            condition_id="entry_unlocked",
            layer_id="entry",
            observed_at=observed_at,
            status="passed" if not self._entry_locked else "failed",
            actual=not self._entry_locked,
            operator="eq",
            required=True,
            unit="boolean",
        )
        if self._entry_locked:
            return _GateEvaluation(
                allowed=False,
                observations=(
                    entry_unlocked,
                    *self._not_evaluated_gate_observations(observed_at)[1:],
                ),
                blocking_condition_ids=("entry_unlocked",),
                reached_layers=("entry",),
            )
        daily_available = ConditionObservation(
            condition_id="daily_context_available",
            layer_id="daily",
            observed_at=observed_at,
            status="passed" if self._daily_context is not None else "failed",
            actual=self._daily_context is not None,
            operator="eq",
            required=True,
            unit="boolean",
        )
        if self._daily_context is None:
            return _GateEvaluation(
                allowed=False,
                observations=(
                    entry_unlocked,
                    daily_available,
                    *self._not_evaluated_gate_observations(observed_at)[2:],
                ),
                blocking_condition_ids=("daily_context_available",),
                reached_layers=("entry", "daily"),
            )
        daily_trend = ConditionObservation(
            condition_id="daily_regime_is_trend",
            layer_id="daily",
            observed_at=observed_at,
            status="passed" if self._daily_context.regime is Regime.TREND else "failed",
            actual=self._daily_context.regime.value,
            operator="eq",
            required=Regime.TREND.value,
            unit="enum",
        )
        if self._daily_context.regime is not Regime.TREND:
            return _GateEvaluation(
                allowed=False,
                observations=(
                    entry_unlocked,
                    daily_available,
                    daily_trend,
                    *self._not_evaluated_gate_observations(observed_at)[3:],
                ),
                blocking_condition_ids=("daily_regime_is_trend",),
                reached_layers=("entry", "daily"),
            )
        mid_direction = ConditionObservation(
            condition_id="mid_direction_matches_entry",
            layer_id="mid",
            observed_at=observed_at,
            status="passed" if self._mid_direction is direction else "failed",
            actual=self._mid_direction.value,
            operator="eq",
            required=direction.value,
            unit="enum",
        )
        if self._mid_direction is not direction:
            return _GateEvaluation(
                allowed=False,
                observations=(
                    entry_unlocked,
                    daily_available,
                    daily_trend,
                    mid_direction,
                    self._not_evaluated_gate_observations(observed_at)[4],
                ),
                blocking_condition_ids=("mid_direction_matches_entry",),
                reached_layers=("entry", "daily", "mid"),
            )
        mid_pullback = ConditionObservation(
            condition_id="mid_pullback_is_qualified",
            layer_id="mid",
            observed_at=observed_at,
            status=(
                "passed" if self._mid_lifecycle.is_first_pullback_qualified else "failed"
            ),
            actual=self._mid_lifecycle.is_first_pullback_qualified,
            operator="eq",
            required=True,
            unit="boolean",
        )
        if not self._mid_lifecycle.is_first_pullback_qualified:
            return _GateEvaluation(
                allowed=False,
                observations=(
                    entry_unlocked,
                    daily_available,
                    daily_trend,
                    mid_direction,
                    mid_pullback,
                ),
                blocking_condition_ids=("mid_pullback_is_qualified",),
                reached_layers=("entry", "daily", "mid"),
            )
        if direction is Direction.NONE:
            # The old chain rejects this only after the preceding gates.  Preserve
            # that ordering so evidence never invents an earlier blocker.
            direction_available = ConditionObservation(
                condition_id="entry_direction_executable",
                layer_id="entry",
                observed_at=observed_at,
                status="failed",
                actual=direction.value,
                operator="neq",
                required=Direction.NONE.value,
                unit="enum",
            )
            return _GateEvaluation(
                allowed=False,
                observations=(
                    entry_unlocked,
                    daily_available,
                    daily_trend,
                    mid_direction,
                    mid_pullback,
                    direction_available,
                ),
                blocking_condition_ids=("entry_direction_executable",),
                reached_layers=("entry", "daily", "mid"),
            )
        return _GateEvaluation(
            allowed=True,
            observations=(
                entry_unlocked,
                daily_available,
                daily_trend,
                mid_direction,
                mid_pullback,
            ),
            blocking_condition_ids=(),
            reached_layers=("entry", "daily", "mid"),
        )

    @staticmethod
    def _not_evaluated_gate_observations(observed_at: datetime) -> tuple[ConditionObservation, ...]:
        """Return the stable gate schema used after a genuine runtime short-circuit."""
        return (
            ConditionObservation(
                condition_id="entry_unlocked",
                layer_id="entry",
                observed_at=observed_at,
                status="not_evaluated",
                actual=None,
                operator="eq",
                required=None,
                unit="boolean",
            ),
            ConditionObservation(
                condition_id="daily_context_available",
                layer_id="daily",
                observed_at=observed_at,
                status="not_evaluated",
                actual=None,
                operator="eq",
                required=None,
                unit="boolean",
            ),
            ConditionObservation(
                condition_id="daily_regime_is_trend",
                layer_id="daily",
                observed_at=observed_at,
                status="not_evaluated",
                actual=None,
                operator="eq",
                required=None,
                unit="enum",
            ),
            ConditionObservation(
                condition_id="mid_direction_matches_entry",
                layer_id="mid",
                observed_at=observed_at,
                status="not_evaluated",
                actual=None,
                operator="eq",
                required=None,
                unit="enum",
            ),
            ConditionObservation(
                condition_id="mid_pullback_is_qualified",
                layer_id="mid",
                observed_at=observed_at,
                status="not_evaluated",
                actual=None,
                operator="eq",
                required=None,
                unit="boolean",
            ),
        )

    @staticmethod
    def _not_evaluated_candidate_observation(observed_at: datetime) -> ConditionObservation:
        return ConditionObservation(
            condition_id="signal_candidate_count_is_one",
            layer_id="entry",
            observed_at=observed_at,
            status="not_evaluated",
            actual=None,
            operator="eq",
            required=None,
            unit="count",
        )

    @staticmethod
    def _rejection_capture(
        *,
        evaluation_sequence: int,
        context: SignalEvaluationContext,
        observations: tuple[ConditionObservation, ...],
        blocking_condition_ids: tuple[str, ...],
        reached_layers: tuple[str, ...],
    ) -> RejectionCapture:
        """Create one typed record per rejected evaluation, never a sampled summary."""
        return RejectionCapture(
            evidence_id=f"rejection_{evaluation_sequence:06d}",
            evaluation_sequence=evaluation_sequence,
            reached_layers=tuple(dict.fromkeys(reached_layers)),
            condition_observations=observations,
            blocking_condition_ids=blocking_condition_ids,
            context=context,
        )

    def _gate_rejection_reason(self, direction: Direction) -> str:
        """Return an auditable reason whenever a fully formed signal cannot become pending."""
        if self._daily_context is None:
            return "daily_regime_unavailable"
        if self._daily_context.regime is not Regime.TREND:
            return f"daily_regime_{self._daily_context.regime.value}"
        if self._mid_direction is not direction:
            return "mid_entry_direction_mismatch"
        if not self._mid_lifecycle.is_first_pullback_qualified:
            return "mid_pullback_not_qualified"
        return "entry_locked"

    def _clear_pending_if_mid_pullback_invalidated(
        self,
        bar: AggregatedBar,
        events: list[StrategyEvent],
    ) -> None:
        """Cancel an older 5m signal when a just-closed 1H lifecycle stops qualifying."""
        if self._pending_signal is None or self._mid_lifecycle.is_first_pullback_qualified:
            return
        self._clear_pending_signal(
            timestamp=bar.timestamp,
            ts_init=bar.ts_init,
            phase=EventPhase.CLOSE,
            reason="mid_pullback_invalidated",
            events=events,
        )

    def _clear_pending_signal(
        self,
        *,
        timestamp: datetime,
        ts_init: datetime,
        phase: EventPhase,
        reason: str,
        events: list[StrategyEvent],
    ) -> None:
        """Cancel a signal exactly once and make its transition visible to consumers."""
        pending = self._pending_signal
        if pending is None:
            return
        self._pending_signal = None
        self._emit(
            events,
            timestamp=timestamp,
            ts_init=ts_init,
            phase=phase,
            machine="entry_signal",
            event_type=StrategyEventType.SIGNAL_CANCELLED,
            from_state="pending",
            to_state=None,
            direction=pending.direction,
            price=pending.stop_price if reason == "oco_stop_touched" else None,
            details={"reason": reason, "signal_kind": pending.kind.value},
        )

    def _emit_lifecycle_transitions(
        self,
        transitions: _LifecycleTransition | tuple[_LifecycleTransition, ...],
        bar: AggregatedBar,
        phase: EventPhase,
        events: list[StrategyEvent],
        *,
        machine: str = "entry_pullback",
    ) -> None:
        """Translate internal FSM transitions into the single append-only event format."""
        materialized = (
            (transitions,) if isinstance(transitions, _LifecycleTransition) else transitions
        )
        for transition in materialized:
            self._emit(
                events,
                timestamp=bar.timestamp,
                ts_init=bar.ts_init,
                phase=phase,
                machine=machine,
                event_type=transition.event_type,
                from_state=transition.from_state.value,
                to_state=transition.to_state.value,
                direction=transition.direction,
                price=transition.price,
                details={"reason": transition.reason},
            )

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
        price: float | None = None,
        details: Mapping[str, EventDetail] | None = None,
        rejection_capture: RejectionCapture | None = None,
    ) -> StrategyEvent:
        """Append one event to both the current update and the durable in-memory event log."""
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
            details={} if details is None else details,
            origin=EventOrigin.STRATEGY,
            rejection_capture=rejection_capture,
        )
        self._events.append(event)
        events.append(event)
        return event

    def _validate_entry_snapshot(self, snapshot: IndicatorSnapshot) -> None:
        if snapshot.bar.timeframe is not self._spec.timeframes.entry:
            msg = "entry snapshot timeframe does not match StrategySpec.timeframes.entry"
            raise ValueError(msg)

    def _validate_daily_context(self, daily: RegimeDecision | None) -> None:
        if daily is not None and daily.snapshot.bar.timeframe is not self._spec.timeframes.bias:
            msg = "daily regime timeframe does not match StrategySpec.timeframes.bias"
            raise ValueError(msg)

    def _validate_mid_context(self, mid: IndicatorSnapshot | None) -> None:
        if mid is not None and mid.bar.timeframe is not self._spec.timeframes.mid:
            msg = "mid snapshot timeframe does not match StrategySpec.timeframes.mid"
            raise ValueError(msg)

    def _validate_causal_context(
        self,
        entry: IndicatorSnapshot,
        *,
        daily: RegimeDecision | None,
        mid: IndicatorSnapshot | None,
    ) -> None:
        """Reject a caller that tries to inject a higher-timeframe future close."""
        entry_close = entry.bar.ts_init
        if daily is not None and daily.snapshot.bar.ts_init > entry_close:
            msg = "daily context must be closed no later than the entry bar close"
            raise ValueError(msg)
        if mid is not None and mid.bar.ts_init > entry_close:
            msg = "mid context must be closed no later than the entry bar close"
            raise ValueError(msg)

    def _validate_entry_order(self, snapshot: IndicatorSnapshot) -> None:
        """Keep one strategy instance on a strictly increasing closed-bar timeline."""
        previous = self._previous_entry_snapshot
        if previous is not None and snapshot.bar.ts_init <= previous.bar.ts_init:
            msg = "entry snapshots must be strictly increasing by close timestamp"
            raise ValueError(msg)


def _has_regime_inputs(
    snapshot: IndicatorSnapshot,
    index: int,
    series: MtfSeries,
    slope_lookback: int,
) -> bool:
    """Require warm-up, a prior slope point, and positive ATR before classification."""
    if index < slope_lookback or not snapshot.is_ready or snapshot.atr_14 <= 0:
        return False
    return series.snapshots[index - slope_lookback].is_ready


def _direction_from_snapshot(snapshot: IndicatorSnapshot) -> Direction:
    """Return the latest EMA18/EMA90 state only after both indicators are ready."""
    if not snapshot.is_ready:
        return Direction.NONE
    if snapshot.ema_18 > snapshot.ema_90:
        return Direction.LONG
    if snapshot.ema_18 < snapshot.ema_90:
        return Direction.SHORT
    return Direction.NONE


def _cross_direction(
    previous: IndicatorSnapshot | None,
    current: IndicatorSnapshot,
) -> Direction:
    """Detect a closed-bar EMA crossover rather than treating persistent separation as new."""
    if previous is None or not previous.is_ready or not current.is_ready:
        return Direction.NONE
    if current.ema_18 > current.ema_90 and previous.ema_18 <= previous.ema_90:
        return Direction.LONG
    if current.ema_18 < current.ema_90 and previous.ema_18 >= previous.ema_90:
        return Direction.SHORT
    return Direction.NONE


def _true_range(bar: AggregatedBar, previous_close: float | None) -> float:
    """Calculate a closed-bar true range for the precomputed congestion comparison."""
    if previous_close is None:
        return bar.high - bar.low
    return max(
        bar.high - bar.low,
        abs(bar.high - previous_close),
        abs(bar.low - previous_close),
    )


def _is_inside(current: AggregatedBar, mother: AggregatedBar) -> bool:
    """Apply the strict Inside Bar definition without treating equal edges as inside."""
    return current.high < mother.high and current.low > mother.low


def _is_magic(current: AggregatedBar, previous: AggregatedBar, direction: Direction) -> bool:
    """Apply the strict closed Magic Bar definition in the active lifecycle direction."""
    midpoint = (current.high + current.low) / 2.0
    if direction is Direction.LONG:
        return current.close > midpoint and current.low < previous.low
    if direction is Direction.SHORT:
        return current.close < midpoint and current.high > previous.high
    return False


def _touches_stop(signal: PendingSignal, bar: AggregatedBar) -> bool:
    """Return whether the OCO stop reference was touched before a pending breakout."""
    if signal.direction is Direction.LONG:
        return bar.low <= signal.stop_price
    return bar.high >= signal.stop_price


def _touches_entry(signal: PendingSignal, bar: AggregatedBar) -> bool:
    """Return whether a pending breakout reference was intrabar-touched."""
    if signal.direction is Direction.LONG:
        return bar.high >= signal.entry_price
    return bar.low <= signal.entry_price


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    """Normalize public timestamps to the strategy's UTC-only convention."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name} must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)
