"""Tests for WO-003b 3b-2: native daily vs 1m-complete-session synthetic consistency."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.daily_consistency import (
    SYNTHETIC_DAILY_SOURCE,
    check_native_daily_vs_1m_synthetic,
    run_daily_consistency_check,
    synthesize_complete_session_daily_bars,
)
from futures_research.data.daily_download import DAILY_SOURCE
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import session_bounds_for_trading_date
from futures_research.data.storage import CanonicalStore


def _minute_bars_for_session(
    contract: ContractSpec,
    trading_date: date,
    *,
    session_name: str = "eth",
    open_price: float = 20000.0,
    drop_minutes: set[int] | None = None,
) -> list[CanonicalBar]:
    bounds = session_bounds_for_trading_date(contract, trading_date, session_name=session_name)
    assert bounds is not None
    start, end = bounds
    drop = drop_minutes or set()
    bars: list[CanonicalBar] = []
    index = 0
    cursor = start
    while cursor < end:
        if index not in drop:
            price = open_price + index * contract.tick_size
            bars.append(
                CanonicalBar(
                    timestamp=cursor,
                    open=price,
                    high=price + contract.tick_size,
                    low=price - contract.tick_size,
                    close=price,
                    volume=10,
                    contract_id=contract.contract_id,
                    source="fixture-1m",
                )
            )
        cursor += timedelta(minutes=1)
        index += 1
    return bars


def _native_from_synthetic(synthetic: CanonicalBar, *, close_delta_ticks: int = 0) -> CanonicalBar:
    tick = 0.25
    return CanonicalBar(
        timestamp=synthetic.timestamp,
        open=synthetic.open,
        high=synthetic.high,
        low=synthetic.low,
        close=synthetic.close + close_delta_ticks * tick,
        volume=0,  # deliberately different — must not be compared
        contract_id=synthetic.contract_id,
        source=DAILY_SOURCE,
    )


def test_synthesize_drops_partial_sessions(contracts_registry: ContractRegistry) -> None:
    """Only fully complete 1m sessions become synthetic daily bars."""
    contract = contracts_registry.by_symbol("NQ")
    complete = _minute_bars_for_session(contract, date(2026, 7, 20))
    partial = _minute_bars_for_session(contract, date(2026, 7, 21), drop_minutes={0, 1, 2})

    synthetic = synthesize_complete_session_daily_bars(
        contract, complete + partial, session_name="eth"
    )

    assert len(synthetic) == 1
    bounds = session_bounds_for_trading_date(contract, date(2026, 7, 20), session_name="eth")
    assert bounds is not None
    assert synthetic[0].timestamp == bounds[0]
    assert synthetic[0].source == SYNTHETIC_DAILY_SOURCE
    assert synthetic[0].open == complete[0].open
    assert synthetic[0].close == complete[-1].close
    assert synthetic[0].high == max(bar.high for bar in complete)
    assert synthetic[0].low == min(bar.low for bar in complete)


def test_consistency_matches_ohlc_and_ignores_volume(
    contracts_registry: ContractRegistry,
) -> None:
    """Matching OHLC within one tick passes; volume divergence is ignored."""
    contract = contracts_registry.by_symbol("NQ")
    minutes = _minute_bars_for_session(contract, date(2026, 7, 20))
    synthetic = synthesize_complete_session_daily_bars(contract, minutes, session_name="eth")
    assert len(synthetic) == 1
    native = _native_from_synthetic(synthetic[0])
    # Volume differs wildly; OHLC exact.
    native = native.model_copy(update={"volume": 999_999})

    report = check_native_daily_vs_1m_synthetic(
        contract,
        native_daily_bars=[native],
        minute_bars=minutes,
        session_name="eth",
    )

    assert report.checks["matched_ohlc"] == "1"
    assert report.checks["ohlc_mismatch"] == "0"
    assert report.checks["missing_native"] == "0"
    assert report.checks["volume_compared"] == "false"
    assert not report.has_errors
    assert report.issues == [] or all(issue.severity == "info" for issue in report.issues)


def test_consistency_close_diff_is_info_not_error(
    contracts_registry: ContractRegistry,
) -> None:
    """Settlement close divergence is info; OHL within tick still counts as aligned."""
    contract = contracts_registry.by_symbol("NQ")
    minutes = _minute_bars_for_session(contract, date(2026, 7, 20))
    synthetic = synthesize_complete_session_daily_bars(contract, minutes, session_name="eth")[0]

    within = _native_from_synthetic(synthetic, close_delta_ticks=1)
    report_ok = check_native_daily_vs_1m_synthetic(
        contract,
        native_daily_bars=[within],
        minute_bars=minutes,
        session_name="eth",
    )
    assert report_ok.checks["matched_ohlc"] == "1"
    assert not report_ok.has_errors

    beyond_close = _native_from_synthetic(synthetic, close_delta_ticks=8)
    report_close = check_native_daily_vs_1m_synthetic(
        contract,
        native_daily_bars=[beyond_close],
        minute_bars=minutes,
        session_name="eth",
    )
    assert report_close.checks["matched_ohl"] == "1"
    assert not report_close.has_errors
    assert any(issue.code == "daily_close_settlement_diff" for issue in report_close.issues)
    assert all(
        issue.severity == "info"
        for issue in report_close.issues
        if issue.code == "daily_close_settlement_diff"
    )


def test_consistency_ohl_mismatch_is_warning_not_error(
    contracts_registry: ContractRegistry,
) -> None:
    """O/H/L beyond one tick is warning (not error); listed in ohl_miss_dates."""
    contract = contracts_registry.by_symbol("NQ")
    minutes = _minute_bars_for_session(contract, date(2026, 7, 20))
    synthetic = synthesize_complete_session_daily_bars(contract, minutes, session_name="eth")[0]
    bad_open = _native_from_synthetic(synthetic).model_copy(
        update={"open": synthetic.open + 5 * contract.tick_size}
    )

    report = check_native_daily_vs_1m_synthetic(
        contract,
        native_daily_bars=[bad_open],
        minute_bars=minutes,
        session_name="eth",
    )
    assert report.checks["ohlc_mismatch"] == "1"
    assert not report.has_errors
    assert any(
        issue.code == "daily_ohl_mismatch" and issue.severity == "warning"
        for issue in report.issues
    )
    assert report.checks["ohl_miss_count"] == "1"


def test_missing_native_for_complete_1m_session_is_error(
    contracts_registry: ContractRegistry,
) -> None:
    """Expected set is 1m-complete days; missing native is reportable."""
    contract = contracts_registry.by_symbol("NQ")
    minutes = _minute_bars_for_session(contract, date(2026, 7, 20))

    report = check_native_daily_vs_1m_synthetic(
        contract,
        native_daily_bars=[],
        minute_bars=minutes,
        session_name="eth",
    )

    assert report.checks["missing_native"] == "1"
    assert report.has_errors
    assert any(issue.code == "missing_native_daily" for issue in report.issues)


def test_native_outside_expected_set_is_info_only(
    contracts_registry: ContractRegistry,
) -> None:
    """Native-only days outside 1m-complete set are counted, not completeness failures."""
    contract = contracts_registry.by_symbol("NQ")
    minutes = _minute_bars_for_session(contract, date(2026, 7, 20))
    synthetic = synthesize_complete_session_daily_bars(contract, minutes, session_name="eth")[0]
    native_match = _native_from_synthetic(synthetic)
    orphan = CanonicalBar(
        timestamp=datetime(2025, 1, 2, 23, 0, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1,
        contract_id=contract.contract_id,
        source=DAILY_SOURCE,
    )

    report = check_native_daily_vs_1m_synthetic(
        contract,
        native_daily_bars=[native_match, orphan],
        minute_bars=minutes,
        session_name="eth",
    )

    assert report.checks["matched_ohlc"] == "1"
    assert report.checks["native_only_outside_expected"] == "1"
    assert any(issue.code == "native_daily_outside_1m_complete_set" for issue in report.issues)
    assert not any(issue.code == "missing_native_daily" for issue in report.issues)
    # Info-only orphan must not make the report an error by itself.
    assert all(
        issue.severity != "error" or issue.code != "native_daily_outside_1m_complete_set"
        for issue in report.issues
    )


def test_run_daily_consistency_writes_report(
    tmp_path: Path, contracts_registry: ContractRegistry
) -> None:
    """End-to-end store read + report write for the operator path."""
    contract = contracts_registry.by_symbol("NQ")
    minutes = _minute_bars_for_session(contract, date(2026, 7, 20))
    synthetic = synthesize_complete_session_daily_bars(contract, minutes, session_name="eth")[0]
    native = _native_from_synthetic(synthetic)

    CanonicalStore(tmp_path / "market").append(minutes)
    CanonicalStore(tmp_path / "market-daily").append([native])

    result = run_daily_consistency_check(
        contract,
        native_store_root=tmp_path / "market-daily",
        minute_store_root=tmp_path / "market",
        reports_root=tmp_path / "reports",
        session_name="eth",
    )

    assert result.complete_1m_session_count == 1
    assert result.matched_ohlc_count == 1
    assert result.report_path.exists()
    assert "daily-consistency-" in result.report_path.name
