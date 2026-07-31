"""Tests for the four non-destructive data-quality check categories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from futures_research.data.models import CanonicalBar
from futures_research.data.quality import DataQualityChecker, QualitySettings


def make_bar(index: int, *, high: float | None = None, low: float | None = None) -> CanonicalBar:
    """Create a deterministic one-minute bar for quality test scenarios."""
    open_price = 100.0 + index
    return CanonicalBar(
        timestamp=datetime(2026, 7, 20, 14, 30, tzinfo=UTC) + timedelta(minutes=index),
        open=open_price,
        high=high if high is not None else open_price + 1.0,
        low=low if low is not None else open_price - 1.0,
        close=open_price + 0.5,
        volume=100,
        contract_id="NQ-202609-CME",
        source="fixture",
    )


def test_completeness_and_reasonableness_find_actionable_issues() -> None:
    """Missing bars and malformed OHLCV must remain visible to an Owner report."""
    valid = make_bar(0)
    malformed = make_bar(2, high=101.0, low=103.0).model_copy(update={"volume": -1})
    expected = [make_bar(index).timestamp for index in range(3)]

    report = DataQualityChecker().check(
        "NQ-202609-CME", [valid, malformed], expected_timestamps=expected
    )

    codes = {issue.code for issue in report.issues}
    assert "missing_expected_bars" in codes
    assert "invalid_ohlc_envelope" in codes
    assert "negative_volume" in codes
    assert report.has_errors


def test_atr_spike_and_higher_timeframe_mismatch_are_recorded() -> None:
    """Anomaly and consistency checks must flag, not discard, questionable observations."""
    bars = [make_bar(index) for index in range(14)]
    bars.append(make_bar(14, high=200.0, low=113.0))
    reference = CanonicalBar(
        timestamp=bars[0].timestamp,
        open=bars[0].open,
        high=999.0,
        low=min(bar.low for bar in bars[:5]),
        close=bars[4].close,
        volume=sum(bar.volume for bar in bars[:5]),
        contract_id="NQ-202609-CME",
        source="fixture-native-5m",
    )
    checker = DataQualityChecker(QualitySettings(spike_atr_multiple=4.0))

    report = checker.check(
        "NQ-202609-CME",
        bars,
        reference_bars=[reference],
        reference_interval=timedelta(minutes=5),
    )

    codes = {issue.code for issue in report.issues}
    assert "atr_range_spike" in codes
    assert "aggregate_mismatch" in codes


def test_tick_misalignment_is_a_non_destructive_reasonableness_warning() -> None:
    """A bad provider quote must be visible before the execution bridge rejects it."""
    misaligned = make_bar(0).model_copy(update={"open": 100.1})

    report = DataQualityChecker().check(
        "NQ-202609-CME",
        [misaligned],
        tick_size=0.25,
    )

    issue = next(issue for issue in report.issues if issue.code == "price_not_tick_aligned")
    assert issue.severity == "warning"
    assert issue.details == {"tick_size": 0.25, "fields": {"open": 100.1}}
    assert not report.has_errors
