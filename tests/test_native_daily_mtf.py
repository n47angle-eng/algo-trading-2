"""Tests for WO-003b 3b-3 native settlement Daily MtfSeries builder."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from futures_research.backtest.mtf import Timeframe, calibrate_regime_thresholds
from futures_research.backtest.native_daily_mtf import build_native_daily_mtf_series
from futures_research.data.contracts import ContractRegistry
from futures_research.data.daily_download import DAILY_SOURCE
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import session_bounds_for_trading_date


def _native_daily(
    contract_id: str,
    session_start: datetime,
    *,
    close: float,
    volume: int = 1_000,
) -> CanonicalBar:
    return CanonicalBar(
        timestamp=session_start,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=volume,
        contract_id=contract_id,
        source=DAILY_SOURCE,
    )


def test_native_daily_builder_sets_session_close_ts_init(
    contracts_registry: ContractRegistry,
) -> None:
    """ts_init must be the configured session close of the settlement trading date."""
    contract = contracts_registry.by_symbol("NQ")
    bounds = session_bounds_for_trading_date(contract, date(2026, 7, 20), session_name="eth")
    assert bounds is not None
    series = build_native_daily_mtf_series(
        contract,
        [_native_daily(contract.contract_id, bounds[0], close=20_000.0)],
        session_name="eth",
    )
    assert series.timeframe is Timeframe.D1
    assert len(series.bars) == 1
    assert series.bars[0].timestamp == bounds[0]
    assert series.bars[0].ts_init == bounds[1]


def test_native_daily_builder_rejects_non_ib_native_source(
    contracts_registry: ContractRegistry,
) -> None:
    """Production builder accepts only ib_native_daily (no fixture backdoor)."""
    contract = contracts_registry.by_symbol("NQ")
    bounds = session_bounds_for_trading_date(contract, date(2026, 7, 20), session_name="eth")
    assert bounds is not None
    bad = _native_daily(contract.contract_id, bounds[0], close=20_000.0).model_copy(
        update={"source": "fixture"}
    )
    try:
        build_native_daily_mtf_series(contract, [bad], session_name="eth")
    except ValueError as exc:
        assert "ib_native_daily" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-prod daily source")


def test_native_daily_series_calibrates_after_ema90_warmup(
    contracts_registry: ContractRegistry,
) -> None:
    """Enough native settlement dailies enable percentile calibration without 1m synthesis."""
    contract = contracts_registry.by_symbol("NQ")
    bars: list[CanonicalBar] = []
    # ~120 weekday sessions ending before mid-2026.
    cursor = date(2025, 12, 1)
    while len(bars) < 120:
        bounds = session_bounds_for_trading_date(contract, cursor, session_name="eth")
        if bounds is not None:
            bars.append(
                _native_daily(
                    contract.contract_id,
                    bounds[0],
                    close=20_000.0 + len(bars) * 0.5,
                )
            )
        cursor += timedelta(days=1)

    series = build_native_daily_mtf_series(contract, bars, session_name="eth")
    ready = sum(1 for snapshot in series.snapshots if snapshot.is_ready)
    assert ready >= 30
    cutoff = series.bars[-10].ts_init
    calibration = calibrate_regime_thresholds(
        series,
        historical_end=cutoff,
        slope_lookback=5,
    )
    assert calibration.sample_size > 0
    assert calibration.separation_threshold > 0
    assert calibration.timeframe is Timeframe.D1
