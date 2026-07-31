"""Independent Daily MtfSeries builder from IB native settlement daily bars (WO-003b 3b-3).

Does **not** modify ``mtf.py``. Produces the same ``MtfSeries`` / ``AggregatedBar`` shapes
consumed by ``calibrate_regime_thresholds`` and the runner Daily gate, with:

- ``timestamp`` = session open (canonical daily ``ts_event``)
- ``ts_init`` = session close for that settlement trading date (closed-bar delivery)

Indicators are compiled once with Nautilus EMA/ATR, matching the MTF precompute path.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nautilus_trader.indicators.averages import (  # type: ignore[import-not-found]
    ExponentialMovingAverage,
    MovingAverageType,
)
from nautilus_trader.indicators.volatility import (  # type: ignore[import-not-found]
    AverageTrueRange,
)

from futures_research.backtest.mtf import (
    AggregatedBar,
    IndicatorSnapshot,
    MtfSeries,
    Timeframe,
)
from futures_research.data.contracts import ContractSpec
from futures_research.data.daily_download import DAILY_SOURCE
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import (
    session_bounds_for_trading_date,
    trading_date_for_session_start,
)

if TYPE_CHECKING:
    from futures_research.backtest.strategy import StrategySpec

_EMA_FAST_PERIOD = 18
_EMA_REFERENCE_PERIOD = 50
DAILY_SLOW_EMA_PERIOD = 90
_ATR_PERIOD = 14


def build_native_daily_mtf_series(
    contract: ContractSpec,
    daily_bars: Sequence[CanonicalBar],
    *,
    session_name: str = "eth",
) -> MtfSeries:
    """Build a Daily ``MtfSeries`` from start-labelled native settlement daily bars.

    Each bar's trading date is recovered from its session-open ``timestamp`` via the
    configured session window; ``ts_init`` is that session's exclusive close instant.
    """
    if session_name not in contract.sessions:
        msg = f"unknown session '{session_name}' for {contract.contract_id}"
        raise ValueError(msg)

    ordered = sorted(daily_bars, key=lambda bar: bar.timestamp)
    aggregated: list[AggregatedBar] = []
    previous_ts_init: datetime | None = None
    for bar in ordered:
        if bar.contract_id != contract.contract_id:
            msg = "native daily bars must belong to the configured individual contract"
            raise ValueError(msg)
        if bar.source != DAILY_SOURCE:
            msg = f"unexpected daily bar source '{bar.source}' (expected {DAILY_SOURCE})"
            raise ValueError(msg)
        session_start = bar.timestamp.astimezone(UTC)
        session_end = _session_end_for_open(contract, session_start, session_name=session_name)
        if previous_ts_init is not None and session_end <= previous_ts_init:
            msg = "native daily bars must produce strictly increasing session close times"
            raise ValueError(msg)
        aggregated.append(
            AggregatedBar(
                timeframe=Timeframe.D1,
                timestamp=session_start,
                ts_init=session_end,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                source_count=1,
            )
        )
        previous_ts_init = session_end

    return _with_compiled_daily_indicators(tuple(aggregated))


def _session_end_for_open(
    contract: ContractSpec,
    session_start: datetime,
    *,
    session_name: str,
) -> datetime:
    """Map a canonical ETH-labelled Daily bar to the selected session's close.

    ``market-daily`` has one physical settlement sequence whose timestamp is the
    configured ETH session start. RTH strategies consume that same sequence;
    only the closed-bar delivery boundary changes to the selected strategy
    session.
    """
    start_utc = session_start.astimezone(UTC)
    trading_date = trading_date_for_session_start(
        contract,
        start_utc,
        session_name="eth",
    )
    bounds = session_bounds_for_trading_date(
        contract,
        trading_date,
        session_name=session_name,
    )
    if bounds is None:
        msg = (
            f"no configured {session_name} session for trading date "
            f"{trading_date.isoformat()} on {contract.contract_id}"
        )
        raise ValueError(msg)
    return bounds[1]


def required_daily_regime_history_count(strategy_spec: StrategySpec) -> int:
    """Return the exact pre-run Daily count required by the engine graph.

    EMA90 must be initialized and the calibrated slope requires the configured
    ready snapshot five Daily bars earlier. The first valid calibration sample
    therefore appears after ``90 + slope_lookback`` source bars. Entry EMA18
    versus EMA90 does not change this shared Daily regime dependency.
    """
    return DAILY_SLOW_EMA_PERIOD + strategy_spec.regime.daily_slope_lookback


def _with_compiled_daily_indicators(bars: tuple[AggregatedBar, ...]) -> MtfSeries:
    """Compile EMA/ATR for Daily bars without calling into mtf.py private helpers."""
    ema_18 = ExponentialMovingAverage(_EMA_FAST_PERIOD)
    ema_50 = ExponentialMovingAverage(_EMA_REFERENCE_PERIOD)
    ema_90 = ExponentialMovingAverage(DAILY_SLOW_EMA_PERIOD)
    atr_14 = AverageTrueRange(_ATR_PERIOD, ma_type=MovingAverageType.WILDER)

    snapshots: list[IndicatorSnapshot] = []
    for bar in bars:
        ema_18.update_raw(bar.close)
        ema_50.update_raw(bar.close)
        ema_90.update_raw(bar.close)
        atr_14.update_raw(bar.high, bar.low, bar.close)
        snapshots.append(
            IndicatorSnapshot(
                bar=bar,
                ema_18=float(ema_18.value),
                ema_50=float(ema_50.value),
                ema_90=float(ema_90.value),
                atr_14=float(atr_14.value),
                is_ready=bool(
                    ema_18.initialized
                    and ema_50.initialized
                    and ema_90.initialized
                    and atr_14.initialized
                ),
            )
        )
    return MtfSeries(timeframe=Timeframe.D1, bars=bars, snapshots=tuple(snapshots))
