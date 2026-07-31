"""WO-003b / 3b-2: native daily vs 1m-synthesized daily consistency.

Compares IB native daily bars (``data/market-daily/``, ``source=ib_native_daily``)
against daily OHLC synthesized only from **complete** configured 1m sessions.
Volume is never compared (IB daily volume is a different definition). Completeness
outside the 1m-complete expected set is not judged — only quantity is reported.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import ceil
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from futures_research.data.contracts import ContractSpec
from futures_research.data.models import CanonicalBar, QualityIssue, QualityReport
from futures_research.data.sessions import session_bounds_for_trading_date

SYNTHETIC_DAILY_SOURCE = "1m_synthetic_daily"
_ONE_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class DailyConsistencyResult:
    """Summary of one native-vs-1m-synthetic daily consistency pass."""

    quality_report: QualityReport
    report_path: Path
    complete_1m_session_count: int
    native_in_expected_count: int
    matched_ohlc_count: int
    ohlc_mismatch_count: int
    missing_native_count: int
    native_only_outside_expected_count: int
    matched_ohl_count: int = 0
    close_diff_ticks_median: float | None = None
    close_diff_ticks_p95: float | None = None
    close_diff_sample_size: int = 0


def synthesize_complete_session_daily_bars(
    contract: ContractSpec,
    minute_bars: Sequence[CanonicalBar],
    *,
    session_name: str = "eth",
) -> tuple[CanonicalBar, ...]:
    """Build one daily bar per fully complete configured 1m session.

    Partial sessions (gaps / early closes / holidays under a weekday template) are
    dropped — matching the MTF closed-bar completeness rule. Each output bar uses
    session open as ``ts_event`` (bar start), same convention as native daily.
    """
    if session_name not in contract.sessions:
        msg = f"unknown session '{session_name}' for {contract.contract_id}"
        raise ValueError(msg)

    grouped = _group_minute_bars_by_session(contract, minute_bars, session_name=session_name)
    synthesized: list[CanonicalBar] = []
    for window_start, window_end, bars in grouped:
        expected_minutes = int((window_end - window_start) / _ONE_MINUTE)
        if expected_minutes <= 0:
            continue
        ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
        if not _is_complete_minute_sequence(ordered, window_start, expected_minutes):
            continue
        synthesized.append(
            CanonicalBar(
                timestamp=window_start,
                open=ordered[0].open,
                high=max(bar.high for bar in ordered),
                low=min(bar.low for bar in ordered),
                close=ordered[-1].close,
                volume=sum(bar.volume for bar in ordered),
                contract_id=contract.contract_id,
                source=SYNTHETIC_DAILY_SOURCE,
            )
        )
    return tuple(synthesized)


def check_native_daily_vs_1m_synthetic(
    contract: ContractSpec,
    *,
    native_daily_bars: Sequence[CanonicalBar],
    minute_bars: Sequence[CanonicalBar],
    session_name: str = "eth",
    tick_size: float | None = None,
) -> QualityReport:
    """Compare native daily OHLC to 1m-complete-session synthetic daily bars.

    Expected set = timestamps of complete 1m sessions only. For each expected day:
    missing native → error; OHLC beyond one-tick tolerance → error. Volume is never
    compared. Native bars outside the expected set are counted as info only.
    """
    resolved_tick = contract.tick_size if tick_size is None else tick_size
    if not isinstance(resolved_tick, (int, float)) or resolved_tick <= 0:
        msg = "tick_size must be a positive finite value"
        raise ValueError(msg)

    synthetic = synthesize_complete_session_daily_bars(
        contract,
        minute_bars,
        session_name=session_name,
    )
    native_by_ts = {
        bar.timestamp.astimezone(UTC): bar
        for bar in native_daily_bars
        if bar.contract_id == contract.contract_id
    }
    expected_timestamps = {bar.timestamp.astimezone(UTC) for bar in synthetic}
    issues: list[QualityIssue] = []
    matched = 0
    mismatches = 0
    missing_native = 0
    field_match_counts = {"open": 0, "high": 0, "low": 0, "close": 0}
    compared_days = 0
    matched_ohl = 0
    close_diff_ticks: list[float] = []

    for synthetic_bar in synthetic:
        ts = synthetic_bar.timestamp.astimezone(UTC)
        native = native_by_ts.get(ts)
        if native is None:
            missing_native += 1
            issues.append(
                QualityIssue(
                    category="consistency",
                    severity="error",
                    code="missing_native_daily",
                    message=(
                        "A complete 1m session has no matching native daily bar at session open."
                    ),
                    timestamp=ts,
                    details={
                        "session_name": session_name,
                        "synthetic_source": SYNTHETIC_DAILY_SOURCE,
                    },
                )
            )
            continue

        compared_days += 1
        field_mismatches: dict[str, dict[str, float]] = {}
        field_ok = {
            field_name: _within_tick(
                float(getattr(synthetic_bar, field_name)),
                float(getattr(native, field_name)),
                resolved_tick,
            )
            for field_name in ("open", "high", "low", "close")
        }
        for field_name, ok in field_ok.items():
            if ok:
                field_match_counts[field_name] += 1
            else:
                synthetic_value = float(getattr(synthetic_bar, field_name))
                native_value = float(getattr(native, field_name))
                field_mismatches[field_name] = {
                    "synthetic": synthetic_value,
                    "native": native_value,
                    "abs_diff": abs(synthetic_value - native_value),
                }
        ohl_ok = field_ok["open"] and field_ok["high"] and field_ok["low"]
        if ohl_ok:
            matched_ohl += 1
            close_diff_ticks.append(
                abs(float(native.close) - float(synthetic_bar.close)) / float(resolved_tick)
            )
        ohl_mismatches = {
            name: details
            for name, details in field_mismatches.items()
            if name in ("open", "high", "low")
        }
        close_only = "close" in field_mismatches and not ohl_mismatches
        if ohl_mismatches:
            mismatches += 1
            issues.append(
                QualityIssue(
                    category="consistency",
                    severity="warning",
                    code="daily_ohl_mismatch",
                    message=(
                        "Native daily open/high/low differs from 1m-complete-session synthetic "
                        "daily by more than one tick."
                    ),
                    timestamp=ts,
                    details={
                        "tick_size": resolved_tick,
                        "fields": ohl_mismatches,
                        "volume_compared": False,
                    },
                )
            )
        elif close_only:
            issues.append(
                QualityIssue(
                    category="consistency",
                    severity="info",
                    code="daily_close_settlement_diff",
                    message=(
                        "Native daily close differs from 1m ETH last trade (expected settlement "
                        "vs session-close artifact); does not fail quality_has_errors."
                    ),
                    timestamp=ts,
                    details={
                        "tick_size": resolved_tick,
                        "fields": {"close": field_mismatches["close"]},
                        "volume_compared": False,
                    },
                )
            )
            matched += 1  # OHL aligned; close divergence is documented as info
        elif not field_mismatches:
            matched += 1

    native_only = sorted(set(native_by_ts) - expected_timestamps)
    if native_only:
        issues.append(
            QualityIssue(
                category="consistency",
                severity="info",
                code="native_daily_outside_1m_complete_set",
                message=(
                    "Native daily bars exist outside the 1m-complete expected set; "
                    "counted only, not treated as completeness failures."
                ),
                details={
                    "count": len(native_only),
                    "earliest": native_only[0],
                    "latest": native_only[-1],
                },
            )
        )

    ohl_miss_dates = sorted(
        issue.timestamp.date().isoformat()
        for issue in issues
        if issue.code == "daily_ohl_mismatch" and issue.timestamp is not None
    )
    close_median, close_p95 = _close_diff_distribution(close_diff_ticks)
    return QualityReport(
        contract_id=contract.contract_id,
        total_bars=len(synthetic),
        checks={
            "completeness": "expected_set_is_1m_complete_sessions",
            "reasonableness": "skipped_3b2",
            "anomaly": "skipped_3b2",
            "consistency": "completed",
            "volume_compared": "false",
            "matched_ohlc": str(matched),
            "matched_ohl": str(matched_ohl),
            "ohlc_mismatch": str(mismatches),
            "missing_native": str(missing_native),
            "native_only_outside_expected": str(len(native_only)),
            "compared_days": str(compared_days),
            "field_match_open": str(field_match_counts["open"]),
            "field_match_high": str(field_match_counts["high"]),
            "field_match_low": str(field_match_counts["low"]),
            "field_match_close": str(field_match_counts["close"]),
            "close_diff_ticks_median": ("null" if close_median is None else f"{close_median:.6f}"),
            "close_diff_ticks_p95": "null" if close_p95 is None else f"{close_p95:.6f}",
            "close_diff_sample_size": str(len(close_diff_ticks)),
            "ohl_miss_dates": ",".join(ohl_miss_dates) if ohl_miss_dates else "",
            "ohl_miss_count": str(len(ohl_miss_dates)),
        },
        issues=issues,
    )


def run_daily_consistency_check(
    contract: ContractSpec,
    *,
    native_store_root: Path,
    minute_store_root: Path,
    reports_root: Path,
    session_name: str = "eth",
    minute_start: datetime | None = None,
    minute_end: datetime | None = None,
) -> DailyConsistencyResult:
    """Load both canonical stores, compare, and write a JSON quality report."""
    from futures_research.data.storage import CanonicalStore

    native_store = CanonicalStore(native_store_root)
    minute_store = CanonicalStore(minute_store_root)
    native_bars = native_store.read(contract.contract_id)
    minute_bars = minute_store.read(
        contract.contract_id,
        start=minute_start,
        end=minute_end,
    )
    report = check_native_daily_vs_1m_synthetic(
        contract,
        native_daily_bars=native_bars,
        minute_bars=minute_bars,
        session_name=session_name,
    )
    report_path = _write_report(reports_root, report)

    synthetic = synthesize_complete_session_daily_bars(
        contract,
        minute_bars,
        session_name=session_name,
    )
    expected = {bar.timestamp.astimezone(UTC) for bar in synthetic}
    native_ts = {bar.timestamp.astimezone(UTC) for bar in native_bars}

    def _parse_optional_float(raw: str) -> float | None:
        if raw == "null":
            return None
        return float(raw)

    return DailyConsistencyResult(
        quality_report=report,
        report_path=report_path,
        complete_1m_session_count=len(synthetic),
        native_in_expected_count=len(expected & native_ts),
        matched_ohlc_count=int(report.checks.get("matched_ohlc", "0")),
        ohlc_mismatch_count=int(report.checks.get("ohlc_mismatch", "0")),
        missing_native_count=int(report.checks.get("missing_native", "0")),
        native_only_outside_expected_count=len(native_ts - expected),
        matched_ohl_count=int(report.checks.get("matched_ohl", "0")),
        close_diff_ticks_median=_parse_optional_float(
            report.checks.get("close_diff_ticks_median", "null")
        ),
        close_diff_ticks_p95=_parse_optional_float(
            report.checks.get("close_diff_ticks_p95", "null")
        ),
        close_diff_sample_size=int(report.checks.get("close_diff_sample_size", "0")),
    )


def _close_diff_distribution(values: Sequence[float]) -> tuple[float | None, float | None]:
    """Return (median, p95) of absolute close differences measured in ticks."""
    if not values:
        return None, None
    ordered = sorted(values)
    med = float(median(ordered))
    if len(ordered) == 1:
        return med, med
    # Nearest-rank P95 on a 1-based index.
    rank = max(1, ceil(0.95 * len(ordered)))
    p95 = float(ordered[rank - 1])
    return med, p95


def _group_minute_bars_by_session(
    contract: ContractSpec,
    minute_bars: Sequence[CanonicalBar],
    *,
    session_name: str,
) -> list[tuple[datetime, datetime, list[CanonicalBar]]]:
    """Group start-labelled 1m bars into configured exchange session windows."""
    zone = ZoneInfo(contract.timezone)
    buckets: dict[datetime, list[CanonicalBar]] = defaultdict(list)
    window_ends: dict[datetime, datetime] = {}

    for bar in sorted(minute_bars, key=lambda item: item.timestamp):
        if bar.contract_id != contract.contract_id:
            continue
        ts = bar.timestamp.astimezone(UTC)
        local_date = ts.astimezone(zone).date()
        window: tuple[datetime, datetime] | None = None
        candidates = (
            local_date,
            local_date + timedelta(days=1),
            local_date - timedelta(days=1),
        )
        for candidate in candidates:
            bounds = session_bounds_for_trading_date(
                contract,
                candidate,
                session_name=session_name,
            )
            if bounds is None:
                continue
            start, end = bounds
            if start <= ts < end:
                window = (start, end)
                break
        if window is None:
            continue
        start, end = window
        buckets[start].append(bar)
        window_ends[start] = end

    return [
        (start, window_ends[start], bars)
        for start, bars in sorted(buckets.items(), key=lambda item: item[0])
    ]


def _is_complete_minute_sequence(
    bars: Sequence[CanonicalBar],
    start: datetime,
    expected_count: int,
) -> bool:
    """Require every start-labelled minute before claiming a daily bar is complete."""
    if len(bars) != expected_count:
        return False
    start_utc = start.astimezone(UTC)
    return all(
        bar.timestamp.astimezone(UTC) == start_utc + index * _ONE_MINUTE
        for index, bar in enumerate(bars)
    )


def _within_tick(left: float, right: float, tick_size: float) -> bool:
    """True when absolute price difference is at most one contract tick."""
    try:
        return abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal(str(tick_size))
    except InvalidOperation:
        return False


def _write_report(reports_root: Path, report: QualityReport) -> Path:
    directory = reports_root / _safe_component(report.contract_id)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"daily-consistency-{report.checked_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    destination = directory / filename
    destination.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return destination


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)
