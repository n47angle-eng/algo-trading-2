"""Closed-bar multi-timeframe precomputation for the backtest strategy path.

Canonical data is start-labelled one-minute OHLCV.  This module turns it into
session-aligned bars whose ``ts_init`` is always the interval close, then computes
the fixed MVP indicator set once, before a run starts.  Strategy callbacks only
ask an immutable series for the latest already-closed snapshot; they do not run
indicator calculations or tabular per-bar work on the bar path.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import ceil, floor, isfinite
from types import MappingProxyType
from typing import Final
from zoneinfo import ZoneInfo

from nautilus_trader.indicators.averages import (  # type: ignore[import-not-found]
    ExponentialMovingAverage,
    MovingAverageType,
)
from nautilus_trader.indicators.volatility import (  # type: ignore[import-not-found]
    AverageTrueRange,
)

from futures_research.data.contracts import ContractSpec
from futures_research.data.models import CanonicalBar

_ONE_MINUTE: Final = timedelta(minutes=1)
_EMA_FAST_PERIOD: Final = 18
_EMA_REFERENCE_PERIOD: Final = 50
_EMA_SLOW_PERIOD: Final = 90
_ATR_PERIOD: Final = 14


class Timeframe(StrEnum):
    """Supported timeframes using the strategy/UI spellings from the file contract."""

    M1 = "1m"
    M5 = "5m"
    M30 = "30m"
    H1 = "1H"
    D1 = "D"


_INTRADAY_TIMEFRAMES: Final[tuple[tuple[Timeframe, timedelta], ...]] = (
    (Timeframe.M5, timedelta(minutes=5)),
    (Timeframe.M30, timedelta(minutes=30)),
    (Timeframe.H1, timedelta(hours=1)),
)


@dataclass(frozen=True, slots=True)
class AggregatedBar:
    """A complete derived bar with a start label and close-delivery timestamp."""

    timeframe: Timeframe
    timestamp: datetime
    ts_init: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    source_count: int

    def __post_init__(self) -> None:
        """Keep the derived-bar timestamp contract explicit at its boundary."""
        timestamp = _as_utc(self.timestamp, field_name="timestamp")
        ts_init = _as_utc(self.ts_init, field_name="ts_init")
        if ts_init <= timestamp:
            msg = "derived bar ts_init must be later than its start timestamp"
            raise ValueError(msg)
        if self.source_count <= 0:
            msg = "derived bar source_count must be positive"
            raise ValueError(msg)
        if self.volume < 0:
            msg = "derived bar volume must not be negative"
            raise ValueError(msg)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "ts_init", ts_init)


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    """The precomputed indicator values available when one derived bar closes."""

    bar: AggregatedBar
    ema_18: float
    ema_50: float
    ema_90: float
    atr_14: float
    is_ready: bool


@dataclass(frozen=True, slots=True)
class MtfSeries:
    """One immutable timeframe sequence with logarithmic closed-bar lookup."""

    timeframe: Timeframe
    bars: tuple[AggregatedBar, ...]
    snapshots: tuple[IndicatorSnapshot, ...]
    _close_timestamps: tuple[datetime, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Verify bar/snapshot pairing once rather than on strategy callbacks."""
        if len(self.bars) != len(self.snapshots):
            msg = "each derived bar must have exactly one indicator snapshot"
            raise ValueError(msg)

        close_timestamps: list[datetime] = []
        previous_close: datetime | None = None
        for bar, snapshot in zip(self.bars, self.snapshots, strict=True):
            if bar.timeframe is not self.timeframe:
                msg = "derived bar timeframe does not match its series"
                raise ValueError(msg)
            if snapshot.bar != bar:
                msg = "indicator snapshot must reference its matching derived bar"
                raise ValueError(msg)
            if previous_close is not None and bar.ts_init <= previous_close:
                msg = "derived bars must be strictly ordered by close timestamp"
                raise ValueError(msg)
            close_timestamps.append(bar.ts_init)
            previous_close = bar.ts_init

        object.__setattr__(self, "_close_timestamps", tuple(close_timestamps))

    def latest_closed(self, as_of: datetime) -> IndicatorSnapshot | None:
        """Return the last snapshot delivered no later than ``as_of``.

        A bar becomes observable at its ``ts_init`` (the exclusive end of its
        interval).  In particular, a daily bar remains unavailable throughout
        its active session and becomes visible only when that session closes.
        """
        as_of_utc = _as_utc(as_of, field_name="as_of")
        index = bisect_right(self._close_timestamps, as_of_utc) - 1
        if index < 0:
            return None
        return self.snapshots[index]


@dataclass(frozen=True, slots=True)
class MtfPrecomputation:
    """All session-filtered, immutable timeframe series for one contract run."""

    contract_id: str
    session_name: str
    series: Mapping[Timeframe, MtfSeries]

    def __post_init__(self) -> None:
        """Freeze the mapping and require the complete MVP timeframe set."""
        expected = set(Timeframe)
        if set(self.series) != expected:
            msg = "MTF precomputation must contain every supported timeframe"
            raise ValueError(msg)
        for timeframe, item in self.series.items():
            if item.timeframe is not timeframe:
                msg = "MTF series mapping key does not match its timeframe"
                raise ValueError(msg)
        object.__setattr__(self, "series", MappingProxyType(dict(self.series)))

    def latest_closed(self, timeframe: Timeframe, as_of: datetime) -> IndicatorSnapshot | None:
        """Look up one timeframe without recalculating indicators in a callback."""
        return self.series[timeframe].latest_closed(as_of)


@dataclass(frozen=True, slots=True)
class RegimeCalibration:
    """Historical-only thresholds for the normalized EMA trend gate."""

    timeframe: Timeframe
    historical_end: datetime
    sample_size: int
    separation_percentile: float
    slope_percentile: float
    separation_threshold: float
    slope_threshold: float
    slope_lookback: int


@dataclass(frozen=True, slots=True)
class _SessionWindow:
    """One configured exchange-local session represented as UTC instants."""

    start: datetime
    end: datetime


class MtfPrecomputer:
    """Build closed-only MTF snapshots from canonical one-minute bars.

    ``session_name`` is deliberately required.  A future ``strategy.v1``
    ``universe.session`` value selects it explicitly, avoiding a hidden RTH/ETH
    default that could change a strategy's data universe.
    """

    def __init__(self, contract: ContractSpec, *, session_name: str) -> None:
        """Bind the precomputation to one configured individual contract/session."""
        try:
            self._hours = contract.sessions[session_name]
        except KeyError as exc:
            msg = f"unknown session '{session_name}' for {contract.contract_id}"
            raise KeyError(msg) from exc
        if self._hours.start == self._hours.end:
            msg = "a configured session must have distinct start and end times"
            raise ValueError(msg)
        self._contract = contract
        self._session_name = session_name
        self._zone = ZoneInfo(contract.timezone)

    def precompute(self, canonical_bars: Iterable[CanonicalBar]) -> MtfPrecomputation:
        """Aggregate complete bars and run the compiled indicator pass once.

        A partially present intraday bucket is omitted instead of being labelled
        as a complete 5m/30m/1H bar.  Daily bars similarly require all expected
        one-minute source bars for their configured session.
        """
        materialized = tuple(canonical_bars)
        _validate_canonical_bars(self._contract, materialized)
        session_bars = self._group_by_session(materialized)
        if not session_bars:
            msg = "no canonical bars fall inside the configured session"
            raise ValueError(msg)

        one_minute = tuple(
            _aggregate(
                Timeframe.M1,
                (bar,),
                timestamp=bar.timestamp,
                ts_init=bar.timestamp + _ONE_MINUTE,
            )
            for _, bars in session_bars
            for bar in bars
        )
        bars_by_timeframe: dict[Timeframe, tuple[AggregatedBar, ...]] = {
            Timeframe.M1: one_minute,
            Timeframe.D1: _aggregate_daily(session_bars),
        }
        for timeframe, duration in _INTRADAY_TIMEFRAMES:
            bars_by_timeframe[timeframe] = _aggregate_intraday(
                timeframe,
                duration,
                session_bars,
            )

        return MtfPrecomputation(
            contract_id=self._contract.contract_id,
            session_name=self._session_name,
            series={
                timeframe: _with_compiled_indicators(timeframe, bars_by_timeframe[timeframe])
                for timeframe in Timeframe
            },
        )

    def _group_by_session(
        self,
        bars: tuple[CanonicalBar, ...],
    ) -> tuple[tuple[_SessionWindow, tuple[CanonicalBar, ...]], ...]:
        """Select source bars and group them by the session they belong to."""
        windows: dict[datetime, _SessionWindow] = {}
        grouped: dict[datetime, list[CanonicalBar]] = {}
        for bar in bars:
            window = self._session_window_for(bar.timestamp)
            if window is None:
                continue
            windows[window.start] = window
            grouped.setdefault(window.start, []).append(bar)
        return tuple((windows[start], tuple(grouped[start])) for start in sorted(grouped))

    def _session_window_for(self, timestamp: datetime) -> _SessionWindow | None:
        """Map one UTC minute to its configured exchange-local session window."""
        local_timestamp = timestamp.astimezone(self._zone)
        local_time = local_timestamp.time()
        if self._hours.start < self._hours.end:
            if not self._hours.start <= local_time < self._hours.end:
                return None
            end_date = local_timestamp.date()
            start_date = end_date
        else:
            if local_time >= self._hours.start:
                end_date = local_timestamp.date() + timedelta(days=1)
            elif local_time < self._hours.end:
                end_date = local_timestamp.date()
            else:
                return None
            start_date = end_date - timedelta(days=1)

        if end_date.weekday() > 4:
            return None
        local_start = datetime.combine(start_date, self._hours.start, tzinfo=self._zone)
        local_end = datetime.combine(end_date, self._hours.end, tzinfo=self._zone)
        return _SessionWindow(
            start=local_start.astimezone(UTC),
            end=local_end.astimezone(UTC),
        )


def calibrate_regime_thresholds(
    series: MtfSeries,
    *,
    historical_end: datetime,
    separation_percentile: float = 65.0,
    slope_percentile: float = 65.0,
    slope_lookback: int,
) -> RegimeCalibration:
    """Calibrate spec section 11.2 thresholds from a bounded historical-only pass.

    ``historical_end`` is mandatory so a caller must name the training cutoff.
    A run must pass a cutoff no later than its start; snapshots after it are
    excluded even if they are present in ``series``.  This keeps percentile
    calibration out of the per-bar callback and prevents future information
    from selecting the regime thresholds.
    """
    history_end_utc = _as_utc(historical_end, field_name="historical_end")
    _validate_percentile(separation_percentile, field_name="separation_percentile")
    _validate_percentile(slope_percentile, field_name="slope_percentile")
    if slope_lookback <= 0:
        msg = "slope_lookback must be positive"
        raise ValueError(msg)

    separations: list[float] = []
    slopes: list[float] = []
    for index, snapshot in enumerate(series.snapshots):
        if snapshot.bar.ts_init > history_end_utc:
            break
        if index < slope_lookback:
            continue
        previous = series.snapshots[index - slope_lookback]
        if not snapshot.is_ready or not previous.is_ready or snapshot.atr_14 <= 0:
            continue
        separation = abs(snapshot.ema_18 - snapshot.ema_90) / snapshot.atr_14
        slope = abs(snapshot.ema_90 - previous.ema_90) / (slope_lookback * snapshot.atr_14)
        if not isfinite(separation) or not isfinite(slope):
            msg = "precomputed indicator values must be finite for calibration"
            raise ValueError(msg)
        separations.append(separation)
        slopes.append(slope)

    if not separations:
        msg = "historical calibration needs ready indicators and slope history"
        raise ValueError(msg)
    return RegimeCalibration(
        timeframe=series.timeframe,
        historical_end=history_end_utc,
        sample_size=len(separations),
        separation_percentile=separation_percentile,
        slope_percentile=slope_percentile,
        separation_threshold=_percentile(separations, separation_percentile),
        slope_threshold=_percentile(slopes, slope_percentile),
        slope_lookback=slope_lookback,
    )


def _validate_canonical_bars(contract: ContractSpec, bars: tuple[CanonicalBar, ...]) -> None:
    """Reject malformed source input before it reaches derived strategy state."""
    if not bars:
        msg = "cannot precompute MTF data without canonical bars"
        raise ValueError(msg)

    previous_timestamp: datetime | None = None
    for bar in bars:
        if bar.contract_id != contract.contract_id:
            msg = "canonical bars must belong to the configured individual contract"
            raise ValueError(msg)
        if bar.timestamp.second or bar.timestamp.microsecond:
            msg = "canonical MTF bars must be aligned to one-minute starts"
            raise ValueError(msg)
        if bar.volume < 0:
            msg = "canonical bar volume must not be negative"
            raise ValueError(msg)
        if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
            msg = "canonical MTF bars must be strictly timestamp-ordered"
            raise ValueError(msg)
        previous_timestamp = bar.timestamp


def _aggregate_intraday(
    timeframe: Timeframe,
    duration: timedelta,
    session_bars: tuple[tuple[_SessionWindow, tuple[CanonicalBar, ...]], ...],
) -> tuple[AggregatedBar, ...]:
    """Build full session-anchored intraday buckets and drop partial tail buckets."""
    bucket_minutes = duration // _ONE_MINUTE
    if not isinstance(bucket_minutes, int) or bucket_minutes <= 0:
        msg = "intraday timeframe duration must be a positive whole number of minutes"
        raise ValueError(msg)

    result: list[AggregatedBar] = []
    for window, bars in session_bars:
        buckets: dict[datetime, list[CanonicalBar]] = {}
        for bar in bars:
            offset_minutes = (bar.timestamp - window.start) // _ONE_MINUTE
            if not isinstance(offset_minutes, int):
                msg = "canonical timestamp offset must be whole minutes"
                raise ValueError(msg)
            bucket_start = window.start + (offset_minutes // bucket_minutes) * duration
            buckets.setdefault(bucket_start, []).append(bar)

        for bucket_start in sorted(buckets):
            bucket = buckets[bucket_start]
            bucket_end = bucket_start + duration
            if bucket_end > window.end:
                continue
            if not _is_complete_minute_sequence(bucket, bucket_start, bucket_minutes):
                continue
            result.append(
                _aggregate(
                    timeframe,
                    tuple(bucket),
                    timestamp=bucket_start,
                    ts_init=bucket_end,
                )
            )
    return tuple(result)


def _aggregate_daily(
    session_bars: tuple[tuple[_SessionWindow, tuple[CanonicalBar, ...]], ...],
) -> tuple[AggregatedBar, ...]:
    """Build one daily bar per complete configured session, delivered at session close."""
    result: list[AggregatedBar] = []
    for window, bars in session_bars:
        expected_minutes = (window.end - window.start) // _ONE_MINUTE
        if not isinstance(expected_minutes, int) or expected_minutes <= 0:
            msg = "configured session duration must be a positive whole number of minutes"
            raise ValueError(msg)
        if not _is_complete_minute_sequence(bars, window.start, expected_minutes):
            continue
        result.append(
            _aggregate(
                Timeframe.D1,
                bars,
                timestamp=window.start,
                ts_init=window.end,
            )
        )
    return tuple(result)


def _is_complete_minute_sequence(
    bars: tuple[CanonicalBar, ...] | list[CanonicalBar],
    start: datetime,
    expected_count: int,
) -> bool:
    """Require every start-labelled minute before claiming a derived bar is closed."""
    if len(bars) != expected_count:
        return False
    return all(bar.timestamp == start + index * _ONE_MINUTE for index, bar in enumerate(bars))


def _aggregate(
    timeframe: Timeframe,
    bars: tuple[CanonicalBar, ...],
    *,
    timestamp: datetime,
    ts_init: datetime,
) -> AggregatedBar:
    """Combine a verified contiguous source sequence into one OHLCV interval."""
    if not bars:
        msg = "cannot aggregate an empty source bar sequence"
        raise ValueError(msg)
    return AggregatedBar(
        timeframe=timeframe,
        timestamp=timestamp,
        ts_init=ts_init,
        open=bars[0].open,
        high=max(bar.high for bar in bars),
        low=min(bar.low for bar in bars),
        close=bars[-1].close,
        volume=sum(bar.volume for bar in bars),
        source_count=len(bars),
    )


def _with_compiled_indicators(
    timeframe: Timeframe,
    bars: tuple[AggregatedBar, ...],
) -> MtfSeries:
    """Run Nautilus's compiled EMA/ATR indicators during precomputation only."""
    ema_18 = ExponentialMovingAverage(_EMA_FAST_PERIOD)
    ema_50 = ExponentialMovingAverage(_EMA_REFERENCE_PERIOD)
    ema_90 = ExponentialMovingAverage(_EMA_SLOW_PERIOD)
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
    return MtfSeries(timeframe=timeframe, bars=bars, snapshots=tuple(snapshots))


def _validate_percentile(value: float, *, field_name: str) -> None:
    """Use the inclusive conventional percentile range for explicit calibration."""
    if not isfinite(value) or not 0.0 <= value <= 100.0:
        msg = f"{field_name} must be a finite value from 0 through 100"
        raise ValueError(msg)


def _percentile(values: list[float], percentile: float) -> float:
    """Return a deterministic linearly interpolated percentile without extra dependencies."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower_index = floor(position)
    upper_index = ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]
    return lower_value + (upper_value - lower_value) * (position - lower_index)


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    """Normalize an aware timestamp to the UTC-only internal convention."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name} must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)
