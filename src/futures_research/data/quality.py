"""Four auditable quality checks for canonical 1-minute market data."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import fmean

from futures_research.data.models import CanonicalBar, QualityIssue, QualityReport


@dataclass(frozen=True)
class QualitySettings:
    """Configurable thresholds for non-destructive market-data quality checks."""

    expected_interval: timedelta = timedelta(minutes=1)
    atr_window: int = 14
    spike_atr_multiple: float = 8.0
    price_tolerance: float = 1e-9


@dataclass(frozen=True)
class _AggregateBar:
    """Internal higher-timeframe aggregate used only for a consistency comparison."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class DataQualityChecker:
    """Run completeness, reasonableness, anomaly, and consistency checks without filtering data."""

    def __init__(self, settings: QualitySettings | None = None) -> None:
        self._settings = settings or QualitySettings()

    def check(
        self,
        contract_id: str,
        bars: Sequence[CanonicalBar],
        *,
        expected_timestamps: Iterable[datetime] | None = None,
        reference_bars: Sequence[CanonicalBar] | None = None,
        reference_interval: timedelta = timedelta(minutes=5),
        tick_size: float | None = None,
    ) -> QualityReport:
        """Return the four-check report for a batch of canonical one-minute bars."""
        if tick_size is not None and (not math.isfinite(tick_size) or tick_size <= 0):
            msg = "tick_size must be a positive finite value when supplied"
            raise ValueError(msg)
        ordered = sorted(bars, key=lambda bar: bar.timestamp)
        issues: list[QualityIssue] = []
        issues.extend(self._check_completeness(ordered, expected_timestamps))
        issues.extend(self._check_reasonableness(ordered, tick_size=tick_size))
        issues.extend(self._check_anomalies(ordered))
        issues.extend(self._check_consistency(ordered, reference_bars, reference_interval))

        checks = {
            "completeness": "completed",
            "reasonableness": "completed",
            "anomaly": "completed",
            "consistency": "completed"
            if reference_bars is not None
            else "completed_without_reference",
        }
        return QualityReport(
            contract_id=contract_id,
            total_bars=len(ordered),
            checks=checks,
            issues=issues,
        )

    def check_reasonableness_only(
        self,
        bars: Sequence[CanonicalBar],
        *,
        tick_size: float | None = None,
    ) -> list[QualityIssue]:
        """Return only OHLC / tick reasonableness findings (WO-003b 3b-1 daily policy).

        Completeness, anomaly, and consistency are intentionally not run here; native daily
        completeness against 1m-complete sessions is deferred to 3b-2.
        """
        if tick_size is not None and (not math.isfinite(tick_size) or tick_size <= 0):
            msg = "tick_size must be a positive finite value when supplied"
            raise ValueError(msg)
        ordered = sorted(bars, key=lambda bar: bar.timestamp)
        return self._check_reasonableness(ordered, tick_size=tick_size)

    def _check_completeness(
        self,
        bars: Sequence[CanonicalBar],
        expected_timestamps: Iterable[datetime] | None,
    ) -> list[QualityIssue]:
        issues: list[QualityIssue] = []
        if not bars:
            return [
                QualityIssue(
                    category="completeness",
                    severity="error",
                    code="empty_batch",
                    message="No bars were supplied for the requested batch.",
                )
            ]

        timestamps = [bar.timestamp for bar in bars]
        unique_timestamps = set(timestamps)
        duplicate_count = len(timestamps) - len(unique_timestamps)
        if duplicate_count:
            issues.append(
                QualityIssue(
                    category="completeness",
                    severity="error",
                    code="duplicate_timestamps",
                    message="One-minute bars contain duplicate timestamps.",
                    details={"duplicate_count": duplicate_count},
                )
            )

        if expected_timestamps is not None:
            expected = {self._normalize_timestamp(value) for value in expected_timestamps}
            missing = sorted(expected - unique_timestamps)
            for start, end, count in self._contiguous_missing_ranges(missing):
                issues.append(
                    QualityIssue(
                        category="completeness",
                        severity="error",
                        code="missing_expected_bars",
                        message="Expected one-minute bars are absent.",
                        timestamp=start,
                        details={"start": start, "end": end, "count": count},
                    )
                )
            unexpected = sorted(unique_timestamps - expected)
            if unexpected:
                issues.append(
                    QualityIssue(
                        category="completeness",
                        severity="warning",
                        code="unexpected_bars",
                        message="Bars fall outside the supplied expected-minute set.",
                        details={"count": len(unexpected)},
                    )
                )
            return issues

        for previous, current in zip(timestamps, timestamps[1:], strict=False):
            gap = current - previous
            if gap > self._settings.expected_interval:
                missing_count = int(gap / self._settings.expected_interval) - 1
                issues.append(
                    QualityIssue(
                        category="completeness",
                        severity="warning",
                        code="unscoped_timestamp_gap",
                        message=(
                            "A timestamp gap was found without a calendar-aware expected-minute "
                            "set; "
                            "verify against the exchange session and holiday schedule."
                        ),
                        timestamp=previous,
                        details={"next_timestamp": current, "missing_count": missing_count},
                    )
                )
        return issues

    def _check_reasonableness(
        self,
        bars: Sequence[CanonicalBar],
        *,
        tick_size: float | None,
    ) -> list[QualityIssue]:
        issues: list[QualityIssue] = []
        for bar in bars:
            prices = (bar.open, bar.high, bar.low, bar.close)
            if any(not math.isfinite(price) or price <= 0 for price in prices):
                issues.append(
                    QualityIssue(
                        category="reasonableness",
                        severity="error",
                        code="invalid_price",
                        message="OHLC prices must be finite and positive.",
                        timestamp=bar.timestamp,
                    )
                )
            if (
                bar.high < max(bar.open, bar.close)
                or bar.low > min(bar.open, bar.close)
                or bar.high < bar.low
            ):
                issues.append(
                    QualityIssue(
                        category="reasonableness",
                        severity="error",
                        code="invalid_ohlc_envelope",
                        message="High/low do not contain the open and close prices.",
                        timestamp=bar.timestamp,
                    )
                )
            if bar.volume < 0:
                issues.append(
                    QualityIssue(
                        category="reasonableness",
                        severity="error",
                        code="negative_volume",
                        message="Volume must not be negative.",
                        timestamp=bar.timestamp,
                    )
                )
            if tick_size is not None:
                misaligned = {
                    field_name: price
                    for field_name, price in (
                        ("open", bar.open),
                        ("high", bar.high),
                        ("low", bar.low),
                        ("close", bar.close),
                    )
                    if not self._is_tick_aligned(price, tick_size)
                }
                if misaligned:
                    issues.append(
                        QualityIssue(
                            category="reasonableness",
                            severity="warning",
                            code="price_not_tick_aligned",
                            message=(
                                "One or more OHLC prices are not aligned to the configured "
                                "contract tick size."
                            ),
                            timestamp=bar.timestamp,
                            details={"tick_size": tick_size, "fields": misaligned},
                        )
                    )
        return issues

    @staticmethod
    def _is_tick_aligned(price: float, tick_size: float) -> bool:
        """Use decimal source values so binary floating-point does not hide a bad quote."""
        if not math.isfinite(price) or price <= 0:
            return True
        try:
            return Decimal(str(price)) % Decimal(str(tick_size)) == 0
        except InvalidOperation:
            return False

    def _check_anomalies(self, bars: Sequence[CanonicalBar]) -> list[QualityIssue]:
        if len(bars) <= self._settings.atr_window:
            return []

        issues: list[QualityIssue] = []
        seed_ranges: list[float] = []
        previous_close: float | None = None
        atr: float | None = None
        for bar in bars:
            true_range = self._true_range(bar, previous_close)
            previous_close = bar.close
            if len(seed_ranges) < self._settings.atr_window:
                seed_ranges.append(true_range)
                if len(seed_ranges) == self._settings.atr_window:
                    atr = fmean(seed_ranges)
                continue

            if atr is not None and atr > 0 and true_range > atr * self._settings.spike_atr_multiple:
                issues.append(
                    QualityIssue(
                        category="anomaly",
                        severity="warning",
                        code="atr_range_spike",
                        message="True range exceeds the configured multiple of prior Wilder ATR.",
                        timestamp=bar.timestamp,
                        details={
                            "true_range": true_range,
                            "prior_atr": atr,
                            "multiple": true_range / atr,
                            "threshold": self._settings.spike_atr_multiple,
                        },
                    )
                )
            if atr is not None:
                atr = (
                    (atr * (self._settings.atr_window - 1)) + true_range
                ) / self._settings.atr_window
        return issues

    def _check_consistency(
        self,
        bars: Sequence[CanonicalBar],
        reference_bars: Sequence[CanonicalBar] | None,
        interval: timedelta,
    ) -> list[QualityIssue]:
        if reference_bars is None:
            return []
        if interval <= timedelta(0):
            msg = "reference_interval must be positive"
            raise ValueError(msg)

        issues: list[QualityIssue] = []
        actual = self._aggregate(bars, interval)
        reference = {bar.timestamp: bar for bar in reference_bars}
        for timestamp, aggregate in actual.items():
            native_bar = reference.get(timestamp)
            if native_bar is None:
                issues.append(
                    QualityIssue(
                        category="consistency",
                        severity="warning",
                        code="missing_reference_bar",
                        message=(
                            "No supplied native higher-timeframe bar matches a 1-minute aggregate."
                        ),
                        timestamp=timestamp,
                    )
                )
                continue
            for field_name, actual_value, expected_value in (
                ("open", aggregate.open, native_bar.open),
                ("high", aggregate.high, native_bar.high),
                ("low", aggregate.low, native_bar.low),
                ("close", aggregate.close, native_bar.close),
                ("volume", aggregate.volume, native_bar.volume),
            ):
                if not self._values_match(actual_value, expected_value):
                    issues.append(
                        QualityIssue(
                            category="consistency",
                            severity="warning",
                            code="aggregate_mismatch",
                            message=(
                                "A 1-minute aggregate differs from the supplied native "
                                "higher-timeframe bar."
                            ),
                            timestamp=timestamp,
                            details={
                                "field": field_name,
                                "aggregated": actual_value,
                                "reference": expected_value,
                            },
                        )
                    )
        return issues

    @staticmethod
    def _true_range(bar: CanonicalBar, previous_close: float | None) -> float:
        if previous_close is None:
            return bar.high - bar.low
        return max(
            bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close)
        )

    def _aggregate(
        self, bars: Sequence[CanonicalBar], interval: timedelta
    ) -> dict[datetime, _AggregateBar]:
        seconds = int(interval.total_seconds())
        buckets: dict[datetime, list[CanonicalBar]] = {}
        for bar in bars:
            timestamp_seconds = int(bar.timestamp.timestamp())
            bucket_timestamp = datetime.fromtimestamp(
                timestamp_seconds - (timestamp_seconds % seconds), tz=UTC
            )
            buckets.setdefault(bucket_timestamp, []).append(bar)

        return {
            timestamp: _AggregateBar(
                timestamp=timestamp,
                open=group[0].open,
                high=max(bar.high for bar in group),
                low=min(bar.low for bar in group),
                close=group[-1].close,
                volume=sum(bar.volume for bar in group),
            )
            for timestamp, group in buckets.items()
        }

    def _values_match(self, actual: float | int, expected: float | int) -> bool:
        if isinstance(actual, int) and isinstance(expected, int):
            return actual == expected
        return math.isclose(float(actual), float(expected), abs_tol=self._settings.price_tolerance)

    def _contiguous_missing_ranges(
        self, missing: Sequence[datetime]
    ) -> list[tuple[datetime, datetime, int]]:
        if not missing:
            return []
        ranges: list[tuple[datetime, datetime, int]] = []
        start = missing[0]
        previous = start
        count = 1
        for timestamp in missing[1:]:
            if timestamp - previous == self._settings.expected_interval:
                previous = timestamp
                count += 1
                continue
            ranges.append((start, previous, count))
            start = timestamp
            previous = timestamp
            count = 1
        ranges.append((start, previous, count))
        return ranges

    @staticmethod
    def _normalize_timestamp(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "expected timestamps must include a timezone"
            raise ValueError(msg)
        return value.astimezone(UTC)
