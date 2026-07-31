"""Closed-only runtime adapter around shared MTF and TrendStrategy authorities."""

from __future__ import annotations

from datetime import datetime

from futures_research.backtest.mtf import (
    MtfPrecomputation,
    MtfPrecomputer,
    Timeframe,
    calibrate_regime_thresholds,
)
from futures_research.backtest.native_daily_mtf import build_native_daily_mtf_series
from futures_research.backtest.strategy import (
    EntryIntent,
    RegimeSeries,
    StrategyEvent,
    StrategyUpdate,
    TrendStrategy,
    precompute_regime_series,
)
from futures_research.data.contracts import ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.paper.execution_runtime import canonical_bar_from_closed_input
from futures_research.paper.market_data import FormingBarUpdate
from futures_research.paper.models import ClosedMarketInput
from futures_research.paper.timeframes import (
    StrategyTimeframeProfile,
    load_locked_strategy_timeframe_profile,
)
from futures_research.strategy.parser import parse_strategy_document
from futures_research.strategy.store import default_strategy_store


class SharedTrendStrategyRuntime:
    """Incremental cursor over a shared, closed-only batch precomputation."""

    def __init__(
        self,
        *,
        core: TrendStrategy,
        precomputation: MtfPrecomputation,
        profile: StrategyTimeframeProfile,
        activation_at: datetime,
        regime_series: RegimeSeries | None,
    ) -> None:
        self._core = core
        self._precomputation = precomputation
        self._profile = profile
        self._activation_at = activation_at
        self._regime_series = regime_series
        self._entry_snapshots = tuple(
            snapshot
            for snapshot in precomputation.series[Timeframe.M5].snapshots
            if snapshot.bar.ts_init > activation_at
        )
        self._next_index = 0
        self._last_processed_entry_close: datetime | None = None

    @classmethod
    def from_locked_strategy(
        cls,
        *,
        strategy_id: str,
        content_sha256: str,
        contract: ContractSpec,
        canonical_bars: tuple[CanonicalBar, ...],
        activation_at: datetime,
        native_daily_bars: tuple[CanonicalBar, ...] = (),
    ) -> SharedTrendStrategyRuntime:
        record = default_strategy_store().get(strategy_id)
        if record.get("content_sha256") != content_sha256:
            raise ValueError("immutable strategy content identity mismatch")
        source = record.get("source_text")
        if not isinstance(source, str) or not source:
            raise ValueError("immutable strategy source is missing")
        parsed = parse_strategy_document(source)
        profile = load_locked_strategy_timeframe_profile(
            strategy_id=strategy_id,
            content_sha256=content_sha256,
        )
        precomputation = MtfPrecomputer(
            contract,
            session_name=parsed.spec.universe_session,
        ).precompute(canonical_bars)
        regime_series: RegimeSeries | None = None
        if native_daily_bars:
            daily = build_native_daily_mtf_series(
                contract,
                native_daily_bars,
                session_name=parsed.spec.universe_session,
            )
            try:
                calibration = calibrate_regime_thresholds(
                    daily,
                    historical_end=activation_at,
                    separation_percentile=(
                        parsed.spec.regime.separation_percentile
                    ),
                    slope_percentile=parsed.spec.regime.slope_percentile,
                    slope_lookback=parsed.spec.regime.daily_slope_lookback,
                )
            except ValueError:
                regime_series = None
            else:
                regime_series = precompute_regime_series(
                    daily,
                    calibration,
                    congestion_lookback=(
                        parsed.spec.regime.daily_congestion_lookback
                    ),
                    shrink_multiplier=(
                        parsed.spec.regime.congestion_shrink_multiplier
                    ),
                )
        return cls(
            core=TrendStrategy(parsed.spec, tick_size=contract.tick_size),
            precomputation=precomputation,
            profile=profile,
            activation_at=activation_at,
            regime_series=regime_series,
        )

    @property
    def core_type(self) -> type[TrendStrategy]:
        return type(self._core)

    @property
    def strategy_timeframes(self) -> tuple[str, str, str]:
        return (self._profile.bias, self._profile.mid, self._profile.entry)

    @property
    def entry_snapshot_close_times(self) -> tuple[datetime, ...]:
        return tuple(snapshot.bar.ts_init for snapshot in self._entry_snapshots)

    @property
    def last_processed_entry_close(self) -> datetime | None:
        return self._last_processed_entry_close

    def process(
        self,
        update: ClosedMarketInput | FormingBarUpdate,
    ) -> StrategyUpdate:
        if isinstance(update, FormingBarUpdate):
            raise ValueError("forming_bar_not_decidable")
        if update.timeframe != "1m":
            raise ValueError("unsupported_timeframe: strategy input requires 1m")
        canonical_bar_from_closed_input(update)
        closed_at = datetime.fromisoformat(
            update.event_at.removesuffix("Z") + "+00:00"
        )
        events: list[StrategyEvent] = []
        intents: list[EntryIntent] = []
        while self._next_index < len(self._entry_snapshots):
            snapshot = self._entry_snapshots[self._next_index]
            if snapshot.bar.ts_init > closed_at:
                break
            daily = (
                None
                if self._regime_series is None
                else self._regime_series.latest_closed(snapshot.bar.ts_init)
            )
            result = self._core.on_entry_bar(
                snapshot,
                daily=daily,
                mid=self._precomputation.latest_closed(
                    Timeframe.H1,
                    snapshot.bar.ts_init,
                ),
            )
            events.extend(result.events)
            intents.extend(result.entry_intents)
            self._last_processed_entry_close = snapshot.bar.ts_init
            self._next_index += 1
        return StrategyUpdate(
            events=tuple(events),
            entry_intents=tuple(intents),
        )
