"""Golden fixtures for the closed-bar P1 Trend strategy state machines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from futures_research.backtest.evidence import build_rejection_evidence
from futures_research.backtest.mtf import (
    AggregatedBar,
    IndicatorSnapshot,
    MtfSeries,
    RegimeCalibration,
    Timeframe,
)
from futures_research.backtest.records import trading_date_for_timestamp
from futures_research.backtest.runner import _resequence_events
from futures_research.backtest.strategy import (
    Direction,
    EventPhase,
    Regime,
    RegimeDecision,
    SignalKind,
    StrategyEventType,
    StrategySpec,
    TrendStrategy,
    precompute_regime_series,
)
from futures_research.backtest.strategy_factory import create_trend_strategy
from futures_research.platform.trend_strategy_seam import rust_trend_available

# Phase E.3: golden suite runs against Python oracle and (when admitted) Rust dual-run.
_STRATEGY_BACKENDS = ["python"] + (["rust"] if rust_trend_available() else [])


@pytest.mark.parametrize("strategy_backend", _STRATEGY_BACKENDS)
def test_golden_pullback_entry_completion_emits_a_single_intent_and_event_log(
    strategy_backend: str,
) -> None:
    """Golden §12.3 terminal (i): entry consumes the first pullback opportunity."""
    strategy, daily, mid, bars = _long_setup_until_signal(
        "inside", strategy_backend=strategy_backend
    )

    update = strategy.on_entry_bar(bars[-1], daily=daily, mid=mid)

    assert len(update.entry_intents) == 1
    intent = update.entry_intents[0]
    assert intent.direction is Direction.LONG
    assert intent.signal_kind is SignalKind.INSIDE
    assert intent.entry_reference == 105.0
    assert intent.stop_reference == 98.0
    assert [(event.machine, event.event_type) for event in strategy.event_log] == [
        ("daily_regime", StrategyEventType.DAILY_REGIME_CHANGED),
        ("mid_direction", StrategyEventType.MID_DIRECTION_CHANGED),
        ("mid_direction", StrategyEventType.MID_DIRECTION_CHANGED),
        ("mid_pullback", StrategyEventType.PULLBACK_ARMED),
        ("entry_pullback", StrategyEventType.PULLBACK_ARMED),
        ("mid_pullback", StrategyEventType.PULLBACK_TOUCHED),
        ("entry_pullback", StrategyEventType.PULLBACK_TOUCHED),
        ("entry_signal", StrategyEventType.SIGNAL_CREATED),
        ("entry_signal", StrategyEventType.ENTRY_INTENT_CREATED),
        ("entry_pullback", StrategyEventType.PULLBACK_COMPLETED),
        ("mid_pullback", StrategyEventType.PULLBACK_COMPLETED),
    ]
    assert [event.phase for event in update.events] == [
        EventPhase.INTRABAR,
        EventPhase.INTRABAR,
        EventPhase.INTRABAR,
    ]
    assert strategy.pending_signal is None
    assert strategy.mid_pullback_state.value == "completed"

    with pytest.raises(TypeError):
        strategy.event_log[0].details["mutate"] = "no"  # type: ignore[index]


@pytest.mark.parametrize("strategy_backend", _STRATEGY_BACKENDS)
def test_golden_pullback_swing_reclaim_exhausts_without_an_entry(
    strategy_backend: str,
) -> None:
    """Golden §12.3 terminal (ii): reclaiming the pre-touch swing expires the setup."""
    strategy, daily, mid, bars = _long_setup_until_touch(
        strategy_backend=strategy_backend
    )
    swing_reclaim = _snapshot(
        Timeframe.M5,
        index=3,
        high=111.0,
        low=100.0,
        close=104.0,
        ema_18=101.0,
        ema_90=100.0,
    )

    update = strategy.on_entry_bar(swing_reclaim, daily=daily, mid=mid)

    assert update.entry_intents == ()
    assert strategy.pending_signal is None
    assert strategy.pullback_state.value == "exhausted"
    assert [event.event_type for event in update.events] == [StrategyEventType.PULLBACK_EXHAUSTED]
    assert dict(update.events[0].details)["reason"] == "swing_reclaimed_before_entry"
    assert bars[-1].bar.ts_init < swing_reclaim.bar.ts_init


@pytest.mark.parametrize("strategy_backend", _STRATEGY_BACKENDS)
def test_golden_pullback_cross_reversal_invalidates_then_arms_the_new_direction(
    strategy_backend: str,
) -> None:
    """Golden §12.3 terminal (iii): a closed EMA reversal cancels the old pullback."""
    strategy, daily, mid, _ = _long_setup_until_touch(
        strategy_backend=strategy_backend
    )
    reversal = _snapshot(
        Timeframe.M5,
        index=3,
        high=105.0,
        low=99.0,
        close=100.0,
        ema_18=99.0,
        ema_90=100.0,
    )

    update = strategy.on_entry_bar(reversal, daily=daily, mid=mid)

    assert update.entry_intents == ()
    assert [event.event_type for event in update.events] == [
        StrategyEventType.PULLBACK_INVALIDATED,
        StrategyEventType.PULLBACK_ARMED,
    ]
    assert strategy.pullback_state.value == "awaiting_touch"
    assert strategy.pullback_direction is Direction.SHORT


def test_magic_signal_uses_its_own_bar_levels_after_the_first_pullback() -> None:
    """The P1 Magic variant forms a pending signal and emits its own breakout intent."""
    strategy, daily, mid, bars = _long_setup_until_signal("magic")

    update = strategy.on_entry_bar(bars[-1], daily=daily, mid=mid)

    assert len(update.entry_intents) == 1
    assert update.entry_intents[0].signal_kind is SignalKind.MAGIC
    assert update.entry_intents[0].entry_reference == 104.0
    assert update.entry_intents[0].stop_reference == 97.0


def test_short_inside_flow_mirrors_the_long_entry_and_stop_references() -> None:
    """The short side uses the mother low breakout and mother high plus one tick stop."""
    strategy = create_trend_strategy(StrategySpec(), tick_size=1.0, backend="auto")
    daily = _daily_decision(Regime.TREND, index=0)
    mid_pre, mid_cross, mid_touch = _short_mid_context()
    bars = (
        _snapshot(
            Timeframe.M5,
            index=0,
            high=98.0,
            low=94.0,
            close=96.0,
            ema_18=101.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.M5,
            index=1,
            high=95.0,
            low=90.0,
            close=92.0,
            ema_18=99.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.M5,
            index=2,
            high=100.0,
            low=95.0,
            close=98.0,
            ema_18=99.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.M5,
            index=3,
            high=99.0,
            low=96.0,
            close=97.0,
            ema_18=99.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.M5,
            index=4,
            high=100.0,
            low=94.0,
            close=96.0,
            ema_18=99.0,
            ema_90=100.0,
        ),
    )

    for bar, mid in zip(bars[:-1], (mid_pre, mid_cross, mid_touch, mid_touch), strict=True):
        strategy.on_entry_bar(bar, daily=daily, mid=mid)
    update = strategy.on_entry_bar(bars[-1], daily=daily, mid=mid_touch)

    assert len(update.entry_intents) == 1
    intent = update.entry_intents[0]
    assert intent.direction is Direction.SHORT
    assert intent.signal_kind is SignalKind.INSIDE
    assert intent.entry_reference == 95.0
    assert intent.stop_reference == 101.0


@pytest.mark.parametrize("blocked_regime", [Regime.RANGE, Regime.CONGESTION])
def test_range_and_congestion_daily_contexts_reject_new_signals(blocked_regime: Regime) -> None:
    """P1's approved Daily gate never lets Range or Congestion form a pending order."""
    strategy, daily, mid, bars = _long_setup_until_touch()
    signal_bar = _inside_signal_bar(index=3)
    blocked_daily = _daily_decision(blocked_regime, index=1)

    update = strategy.on_entry_bar(signal_bar, daily=blocked_daily, mid=mid)

    assert strategy.pending_signal is None
    assert update.entry_intents == ()
    assert update.events[-1].event_type is StrategyEventType.SIGNAL_REJECTED
    assert dict(update.events[-1].details)["reason"] == f"daily_regime_{blocked_regime.value}"
    assert daily.snapshot.bar.ts_init < blocked_daily.snapshot.bar.ts_init
    assert bars[-1].bar.ts_init < signal_bar.bar.ts_init


def test_rejected_signal_captures_runtime_facts_without_re_evaluating_short_circuits(
    contracts_registry,
) -> None:
    """The real gate emits one full reject record, including truthful unknown downstream facts."""
    strategy, _, mid, _ = _long_setup_until_touch()
    blocked = strategy.on_entry_bar(
        _inside_signal_bar(index=3),
        daily=_daily_decision(Regime.RANGE, index=1),
        mid=mid,
    )
    assert blocked.events[-1].event_type is StrategyEventType.SIGNAL_REJECTED

    contract = contracts_registry.by_symbol("NQ")
    final_events = _resequence_events(strategy.event_log)
    records = build_rejection_evidence(
        final_events,
        trading_date_for_timestamp=lambda timestamp: trading_date_for_timestamp(
            timestamp,
            contract=contract,
            session_name="eth",
        ),
    )

    assert len(records) == 1
    record = records[0]
    assert record.evidence_id == "rejection_000001"
    assert record.evaluation_sequence == 1
    assert record.blocking_condition_ids == ("daily_regime_is_trend",)
    assert record.trading_date.isoformat() == "2026-07-20"
    facts = {fact.condition_id: fact for fact in record.condition_facts}
    assert facts["entry_cross_state_matches_direction"].status == "passed"
    assert facts["daily_context_available"].status == "passed"
    assert facts["daily_regime_is_trend"].status == "failed"
    assert facts["daily_regime_is_trend"].actual == "range"
    assert facts["daily_regime_is_trend"].required == "trend"
    assert facts["daily_regime_is_trend"].unit == "enum"
    assert facts["mid_direction_matches_entry"].status == "not_evaluated"
    assert facts["mid_direction_matches_entry"].actual is None
    assert facts["mid_direction_matches_entry"].required is None
    assert facts["mid_direction_matches_entry"].source_sequences == ()
    assert all(
        fact.source_sequences == (final_events[-1].sequence,)
        for fact in record.condition_facts
        if fact.status in {"passed", "failed"}
    )


def test_hard_mid_entry_direction_consistency_rejects_a_completed_signal_bar() -> None:
    """A long 5m lifecycle cannot produce an order when the newly closed 1H state is short."""
    strategy, daily, mid, _ = _long_setup_until_touch()
    short_mid = _snapshot(
        Timeframe.H1,
        index=3,
        high=102.0,
        low=98.0,
        close=99.0,
        ema_18=99.0,
        ema_90=100.0,
    )

    update = strategy.on_entry_bar(_inside_signal_bar(index=6), daily=daily, mid=short_mid)

    assert strategy.pending_signal is None
    assert update.events[-1].event_type is StrategyEventType.SIGNAL_REJECTED
    assert dict(update.events[-1].details)["reason"] == "mid_entry_direction_mismatch"
    assert mid.bar.ts_init < short_mid.bar.ts_init


def test_mid_first_pullback_must_touch_before_it_can_release_a_5m_signal() -> None:
    """The 1H cross alone is insufficient; only its first 90-EMA touch releases 5m."""
    strategy = create_trend_strategy(StrategySpec(), tick_size=1.0, backend="auto")
    daily = _daily_decision(Regime.TREND, index=0)
    mid_pre, mid_cross, mid_touch = _long_mid_context()
    baseline, cross, touch = _long_entry_cross_and_touch()

    strategy.on_entry_bar(baseline, daily=daily, mid=mid_pre)
    strategy.on_entry_bar(cross, daily=daily, mid=mid_cross)
    strategy.on_entry_bar(touch, daily=daily, mid=mid_cross)
    rejected = strategy.on_entry_bar(_inside_signal_bar(index=3), daily=daily, mid=mid_cross)

    assert strategy.pending_signal is None
    assert strategy.mid_pullback_state.value == "awaiting_touch"
    assert rejected.events[-1].event_type is StrategyEventType.SIGNAL_REJECTED
    assert dict(rejected.events[-1].details)["reason"] == "mid_pullback_not_qualified"

    released = strategy.on_entry_bar(_inside_signal_bar(index=4), daily=daily, mid=mid_touch)

    assert strategy.mid_pullback_state.value == "awaiting_signal"
    assert strategy.pending_signal is not None
    assert [(event.machine, event.event_type) for event in released.events] == [
        ("mid_pullback", StrategyEventType.PULLBACK_TOUCHED),
        ("entry_signal", StrategyEventType.SIGNAL_CREATED),
    ]


def test_mid_swing_reclaim_cancels_an_older_5m_pending_signal() -> None:
    """A 1H first-pullback expiry invalidates its dependent 5m signal at close."""
    strategy, daily, _, _ = _long_setup_until_signal("inside", include_breakout=False)
    mid_reclaim = _snapshot(
        Timeframe.H1,
        index=3,
        high=111.0,
        low=100.0,
        close=104.0,
        ema_18=101.0,
        ema_90=100.0,
    )
    quiet_5m = _snapshot(
        Timeframe.M5,
        index=6,
        high=104.5,
        low=99.0,
        close=101.5,
        ema_18=101.0,
        ema_90=100.0,
    )

    update = strategy.on_entry_bar(quiet_5m, daily=daily, mid=mid_reclaim)

    assert update.entry_intents == ()
    assert strategy.pending_signal is None
    assert strategy.mid_pullback_state.value == "exhausted"
    assert [(event.machine, event.event_type) for event in update.events] == [
        ("mid_pullback", StrategyEventType.PULLBACK_EXHAUSTED),
        ("entry_signal", StrategyEventType.SIGNAL_CANCELLED),
    ]
    assert dict(update.events[-1].details)["reason"] == "mid_pullback_invalidated"


def test_mid_cross_reversal_cancels_an_older_5m_pending_signal() -> None:
    """A closed 1H reversal invalidates the old gate before a new 5m order can exist."""
    strategy, daily, _, _ = _long_setup_until_signal("inside", include_breakout=False)
    mid_reversal = _snapshot(
        Timeframe.H1,
        index=3,
        high=105.0,
        low=95.0,
        close=98.0,
        ema_18=99.0,
        ema_90=100.0,
    )
    quiet_5m = _snapshot(
        Timeframe.M5,
        index=6,
        high=104.5,
        low=99.0,
        close=101.5,
        ema_18=101.0,
        ema_90=100.0,
    )

    update = strategy.on_entry_bar(quiet_5m, daily=daily, mid=mid_reversal)

    assert update.entry_intents == ()
    assert strategy.pending_signal is None
    assert strategy.mid_pullback_state.value == "awaiting_touch"
    assert strategy.mid_pullback_direction is Direction.SHORT
    assert [(event.machine, event.event_type) for event in update.events] == [
        ("mid_direction", StrategyEventType.MID_DIRECTION_CHANGED),
        ("mid_pullback", StrategyEventType.PULLBACK_INVALIDATED),
        ("mid_pullback", StrategyEventType.PULLBACK_ARMED),
        ("entry_signal", StrategyEventType.SIGNAL_CANCELLED),
    ]


def test_same_bar_inside_magic_rejection_records_all_candidates() -> None:
    """The approved conservative conflict event retains both candidate identities."""
    strategy = create_trend_strategy(StrategySpec(), tick_size=1.0, backend="auto")
    range_daily = _daily_decision(Regime.RANGE, index=0)
    trend_daily = _daily_decision(Regime.TREND, index=1)
    mid_pre, mid_cross, mid_touch = _long_mid_context()
    baseline, cross, touch = _long_entry_cross_and_touch()
    first_inside = _inside_signal_bar(index=3)
    dual_candidate = _snapshot(
        Timeframe.M5,
        index=4,
        high=103.0,
        low=99.5,
        close=102.0,
        ema_18=101.0,
        ema_90=100.0,
    )

    strategy.on_entry_bar(baseline, daily=range_daily, mid=mid_pre)
    strategy.on_entry_bar(cross, daily=range_daily, mid=mid_cross)
    strategy.on_entry_bar(touch, daily=range_daily, mid=mid_touch)
    first_rejection = strategy.on_entry_bar(first_inside, daily=range_daily, mid=mid_touch)
    conflict = strategy.on_entry_bar(dual_candidate, daily=trend_daily, mid=mid_touch)

    assert dict(first_rejection.events[-1].details)["reason"] == "daily_regime_range"
    assert strategy.pending_signal is None
    assert conflict.events[-1].event_type is StrategyEventType.SIGNAL_REJECTED
    assert dict(conflict.events[-1].details) == {
        "reason": "multiple_signal_bars_same_bar",
        "candidates": ("inside", "magic"),
    }


def test_oco_stop_cancellation_wins_over_same_bar_breakout() -> None:
    """A signal whose stop and entry both touch in one bar is cancelled conservatively first."""
    strategy, daily, mid, _ = _long_setup_until_signal("inside", include_breakout=False)
    same_bar_conflict = _snapshot(
        Timeframe.M5,
        index=4,
        high=106.0,
        low=98.0,
        close=102.0,
        ema_18=101.0,
        ema_90=100.0,
    )

    update = strategy.on_entry_bar(same_bar_conflict, daily=daily, mid=mid)

    assert update.entry_intents == ()
    assert strategy.pending_signal is None
    assert [event.event_type for event in update.events] == [StrategyEventType.SIGNAL_CANCELLED]
    assert dict(update.events[0].details)["reason"] == "oco_stop_touched"


def test_same_bar_swing_reclaim_cancels_pending_breakout_conservatively() -> None:
    """An unknown swing-reclaim/breakout order resolves to the §12.3 expiry first."""
    strategy, daily, mid, _ = _long_setup_until_signal("inside", include_breakout=False)
    reclaim_and_breakout = _snapshot(
        Timeframe.M5,
        index=4,
        high=111.0,
        low=100.0,
        close=104.0,
        ema_18=101.0,
        ema_90=100.0,
    )

    update = strategy.on_entry_bar(reclaim_and_breakout, daily=daily, mid=mid)

    assert update.entry_intents == ()
    assert strategy.pending_signal is None
    assert strategy.pullback_state.value == "exhausted"
    assert [event.event_type for event in update.events] == [
        StrategyEventType.PULLBACK_EXHAUSTED,
        StrategyEventType.SIGNAL_CANCELLED,
    ]
    assert dict(update.events[0].details)["reason"] == "swing_reclaimed_before_entry"
    assert dict(update.events[1].details)["reason"] == "pullback_exhausted"


def test_entry_cross_state_must_still_match_the_active_lifecycle() -> None:
    """A flat 5m EMA state cannot turn an otherwise valid signal bar into an order."""
    strategy, daily, mid, _ = _long_setup_until_touch()
    flat_inside = _snapshot(
        Timeframe.M5,
        index=3,
        high=104.0,
        low=100.0,
        close=101.0,
        ema_18=100.0,
        ema_90=100.0,
    )

    update = strategy.on_entry_bar(flat_inside, daily=daily, mid=mid)

    assert strategy.pending_signal is None
    assert update.events[-1].event_type is StrategyEventType.SIGNAL_REJECTED
    assert dict(update.events[-1].details)["reason"] == "entry_cross_state_mismatch"


def test_future_higher_timeframe_context_is_rejected_before_it_can_leak() -> None:
    """The public strategy boundary refuses a Daily value that has not closed yet."""
    strategy = create_trend_strategy(StrategySpec(), tick_size=1.0, backend="auto")
    entry = _snapshot(
        Timeframe.M5,
        index=0,
        high=106.0,
        low=102.0,
        close=104.0,
        ema_18=99.0,
        ema_90=100.0,
    )
    future_daily = _daily_decision(Regime.TREND, index=6)

    with pytest.raises(ValueError, match="daily context must be closed"):
        strategy.on_entry_bar(entry, daily=future_daily, mid=None)


def test_end_day_clears_pending_signal_and_logs_the_transition() -> None:
    """Closed signal bars do not carry a pending order into the next trading day."""
    strategy, _, _, _ = _long_setup_until_signal("inside", include_breakout=False)

    update = strategy.end_day(datetime(2026, 7, 21, 20, tzinfo=UTC))

    assert strategy.pending_signal is None
    assert [event.event_type for event in update.events] == [StrategyEventType.SIGNAL_CANCELLED]
    assert update.events[0].phase is EventPhase.DAY_END
    assert dict(update.events[0].details)["reason"] == "day_end_clear"


def test_precomputed_regime_series_distinguishes_trend_congestion_and_range() -> None:
    """The Daily gate uses only current-or-earlier true ranges and closed indicator snapshots."""
    snapshots = (
        _snapshot(
            Timeframe.D1,
            index=0,
            high=101.0,
            low=100.0,
            close=100.0,
            ema_18=100.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.D1,
            index=1,
            high=101.0,
            low=100.0,
            close=100.0,
            ema_18=120.0,
            ema_90=110.0,
        ),
        _snapshot(
            Timeframe.D1,
            index=2,
            high=101.0,
            low=100.0,
            close=100.0,
            ema_18=111.0,
            ema_90=110.0,
        ),
        _snapshot(
            Timeframe.D1,
            index=3,
            high=120.0,
            low=100.0,
            close=100.0,
            ema_18=111.0,
            ema_90=110.0,
        ),
    )
    series = MtfSeries(
        timeframe=Timeframe.D1,
        bars=tuple(snapshot.bar for snapshot in snapshots),
        snapshots=snapshots,
    )
    calibration = RegimeCalibration(
        timeframe=Timeframe.D1,
        historical_end=snapshots[0].bar.ts_init,
        sample_size=1,
        separation_percentile=65.0,
        slope_percentile=65.0,
        separation_threshold=1.0,
        slope_threshold=1.0,
        slope_lookback=1,
    )

    regimes = precompute_regime_series(
        series,
        calibration,
        congestion_lookback=2,
        shrink_multiplier=0.6,
    )

    assert [decision.regime for decision in regimes.decisions] == [
        Regime.UNAVAILABLE,
        Regime.TREND,
        Regime.CONGESTION,
        Regime.RANGE,
    ]
    assert regimes.latest_closed(snapshots[3].bar.ts_init).regime is Regime.RANGE


def _long_setup_until_touch(
    *,
    strategy_backend: str = "python",
) -> tuple[
    TrendStrategy,
    RegimeDecision,
    IndicatorSnapshot,
    tuple[IndicatorSnapshot, ...],
]:
    """Replay the deterministic cross then first-touch prefix shared by golden fixtures."""
    strategy = create_trend_strategy(
        StrategySpec(), tick_size=1.0, backend=strategy_backend  # type: ignore[arg-type]
    )
    daily = _daily_decision(Regime.TREND, index=0)
    mid_pre, mid_cross, mid_touch = _long_mid_context()
    baseline, cross, touch = _long_entry_cross_and_touch()
    strategy.on_entry_bar(baseline, daily=daily, mid=mid_pre)
    strategy.on_entry_bar(cross, daily=daily, mid=mid_cross)
    strategy.on_entry_bar(touch, daily=daily, mid=mid_touch)
    return strategy, daily, mid_touch, (baseline, cross, touch)


def _long_setup_until_signal(
    signal_kind: str,
    *,
    include_breakout: bool = True,
    strategy_backend: str = "python",
) -> tuple[TrendStrategy, RegimeDecision, IndicatorSnapshot, tuple[IndicatorSnapshot, ...]]:
    """Replay a complete long setup with either an Inside or Magic signal bar."""
    strategy, daily, mid, prefix = _long_setup_until_touch(
        strategy_backend=strategy_backend
    )
    signal_bar = (
        _inside_signal_bar(index=3) if signal_kind == "inside" else _magic_signal_bar(index=3)
    )
    strategy.on_entry_bar(signal_bar, daily=daily, mid=mid)
    if not include_breakout:
        return strategy, daily, mid, (*prefix, signal_bar)
    breakout = _snapshot(
        Timeframe.M5,
        index=4,
        high=106.0 if signal_kind == "inside" else 105.0,
        low=100.0 if signal_kind == "inside" else 98.0,
        close=104.0,
        ema_18=101.0,
        ema_90=100.0,
    )
    return strategy, daily, mid, (*prefix, signal_bar, breakout)


def _inside_signal_bar(*, index: int) -> IndicatorSnapshot:
    """Return a strict inside bar of the touch bar: mother high=105 and low=99."""
    return _snapshot(
        Timeframe.M5,
        index=index,
        high=104.0,
        low=100.0,
        close=101.0,
        ema_18=101.0,
        ema_90=100.0,
    )


def _magic_signal_bar(*, index: int) -> IndicatorSnapshot:
    """Return a long Magic bar with low below its predecessor but no swing reclaim."""
    return _snapshot(
        Timeframe.M5,
        index=index,
        high=104.0,
        low=98.0,
        close=103.0,
        ema_18=101.0,
        ema_90=100.0,
    )


def _long_entry_cross_and_touch() -> tuple[IndicatorSnapshot, IndicatorSnapshot, IndicatorSnapshot]:
    """Return the shared 5m predecessor, bullish cross, and first 90-EMA touch sequence."""
    return (
        _snapshot(
            Timeframe.M5,
            index=0,
            high=106.0,
            low=102.0,
            close=104.0,
            ema_18=99.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.M5,
            index=1,
            high=110.0,
            low=105.0,
            close=108.0,
            ema_18=101.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.M5,
            index=2,
            high=105.0,
            low=99.0,
            close=102.0,
            ema_18=101.0,
            ema_90=100.0,
        ),
    )


def _long_mid_context() -> tuple[IndicatorSnapshot, IndicatorSnapshot, IndicatorSnapshot]:
    """Return the 1H predecessor, bullish cross, and first 90-EMA touch sequence."""
    return (
        _snapshot(
            Timeframe.H1,
            index=0,
            high=106.0,
            low=102.0,
            close=104.0,
            ema_18=99.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.H1,
            index=1,
            high=110.0,
            low=105.0,
            close=108.0,
            ema_18=101.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.H1,
            index=2,
            high=105.0,
            low=99.0,
            close=102.0,
            ema_18=101.0,
            ema_90=100.0,
        ),
    )


def _short_mid_context() -> tuple[IndicatorSnapshot, IndicatorSnapshot, IndicatorSnapshot]:
    """Return the 1H predecessor, bearish cross, and first 90-EMA touch sequence."""
    return (
        _snapshot(
            Timeframe.H1,
            index=0,
            high=98.0,
            low=94.0,
            close=96.0,
            ema_18=101.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.H1,
            index=1,
            high=95.0,
            low=90.0,
            close=92.0,
            ema_18=99.0,
            ema_90=100.0,
        ),
        _snapshot(
            Timeframe.H1,
            index=2,
            high=100.0,
            low=95.0,
            close=98.0,
            ema_18=99.0,
            ema_90=100.0,
        ),
    )


def _daily_decision(regime: Regime, *, index: int) -> RegimeDecision:
    """Provide a ready Daily context with a monotonically increasing close timestamp."""
    snapshot = _snapshot(
        Timeframe.D1,
        index=index,
        high=110.0,
        low=100.0,
        close=106.0,
        ema_18=105.0,
        ema_90=100.0,
    )
    return RegimeDecision(
        snapshot=snapshot,
        regime=regime,
        direction=Direction.LONG,
        normalized_separation=1.0,
        normalized_slope=1.0,
        recent_average_true_range=5.0,
    )


def _snapshot(
    timeframe: Timeframe,
    *,
    index: int,
    high: float,
    low: float,
    close: float,
    ema_18: float,
    ema_90: float,
) -> IndicatorSnapshot:
    """Build one ready, timestamped immutable test snapshot at a valid timeframe cadence."""
    start = {
        Timeframe.M5: datetime(2026, 7, 20, 13, 30, tzinfo=UTC),
        Timeframe.H1: datetime(2026, 7, 20, 10, tzinfo=UTC),
        Timeframe.D1: datetime(2026, 7, 15, 0, tzinfo=UTC),
    }[timeframe]
    interval = {
        Timeframe.M5: timedelta(minutes=5),
        Timeframe.H1: timedelta(hours=1),
        Timeframe.D1: timedelta(days=1),
    }[timeframe]
    timestamp = start + index * interval
    bar = AggregatedBar(
        timeframe=timeframe,
        timestamp=timestamp,
        ts_init=timestamp + interval,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=100,
        source_count=1,
    )
    return IndicatorSnapshot(
        bar=bar,
        ema_18=ema_18,
        ema_50=(ema_18 + ema_90) / 2.0,
        ema_90=ema_90,
        atr_14=10.0,
        is_ready=True,
    )
