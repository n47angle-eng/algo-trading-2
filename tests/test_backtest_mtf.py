"""Deterministic coverage for closed-bar MTF aggregation and indicator snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from futures_research.backtest.mtf import (
    MtfPrecomputer,
    Timeframe,
    calibrate_regime_thresholds,
)
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import expected_minute_timestamps


def test_eth_mtf_uses_close_delivery_and_hides_the_active_daily_bar(
    contracts_registry: ContractRegistry,
) -> None:
    """Every derived interval is observable only at its close, including Daily."""
    contract = contracts_registry.by_symbol("NQ")
    first_session_start = datetime(2026, 7, 19, 22, tzinfo=UTC)
    second_session_end = datetime(2026, 7, 21, 21, tzinfo=UTC)
    bars = _canonical_bars(
        contract.contract_id,
        _session_timestamps(contract, first_session_start, second_session_end, session_name="eth"),
    )

    result = MtfPrecomputer(contract, session_name="eth").precompute(bars)

    assert [len(result.series[timeframe].bars) for timeframe in Timeframe] == [
        2760,
        552,
        92,
        46,
        2,
    ]
    first_one_minute = result.series[Timeframe.M1].bars[0]
    assert first_one_minute.timestamp == first_session_start
    assert first_one_minute.ts_init == first_session_start + timedelta(minutes=1)
    first_five_minute = result.series[Timeframe.M5].bars[0]
    assert first_five_minute.timestamp == first_session_start
    assert first_five_minute.ts_init == first_session_start + timedelta(minutes=5)

    daily = result.series[Timeframe.D1].bars
    assert daily[0].timestamp == first_session_start
    assert daily[0].ts_init == datetime(2026, 7, 20, 21, tzinfo=UTC)
    assert daily[1].timestamp == datetime(2026, 7, 20, 22, tzinfo=UTC)
    assert daily[1].ts_init == second_session_end
    assert (
        result.latest_closed(Timeframe.D1, second_session_end - timedelta(minutes=1)).bar
        == daily[0]
    )
    assert result.latest_closed(Timeframe.D1, second_session_end).bar == daily[1]


def test_incomplete_source_minutes_never_create_a_partial_aggregate(
    contracts_registry: ContractRegistry,
) -> None:
    """A missing 1m input suppresses its 5m bucket rather than inventing a close."""
    contract = contracts_registry.by_symbol("NQ")
    session_start = datetime(2026, 7, 19, 22, tzinfo=UTC)
    bars = _canonical_bars(
        contract.contract_id,
        [session_start + index * timedelta(minutes=1) for index in range(10)],
    )
    incomplete = [bar for index, bar in enumerate(bars) if index != 2]

    result = MtfPrecomputer(contract, session_name="eth").precompute(incomplete)

    assert len(result.series[Timeframe.M1].bars) == 9
    assert [bar.timestamp for bar in result.series[Timeframe.M5].bars] == [
        session_start + timedelta(minutes=5)
    ]
    assert result.series[Timeframe.M30].bars == ()
    assert result.series[Timeframe.D1].bars == ()


def test_rth_buckets_anchor_to_session_open_and_precompute_compiled_indicators(
    contracts_registry: ContractRegistry,
) -> None:
    """RTH's 08:30 CT open anchors 1H bars and its 30m tail is not fabricated."""
    contract = contracts_registry.by_symbol("NQ")
    session_start = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
    session_end = datetime(2026, 7, 20, 20, tzinfo=UTC)
    bars = _canonical_bars(
        contract.contract_id,
        _session_timestamps(contract, session_start, session_end, session_name="rth"),
    )

    result = MtfPrecomputer(contract, session_name="rth").precompute(bars)

    assert len(result.series[Timeframe.M5].bars) == 78
    assert len(result.series[Timeframe.M30].bars) == 13
    hourly = result.series[Timeframe.H1].bars
    assert [bar.timestamp for bar in hourly] == [
        session_start + index * timedelta(hours=1) for index in range(6)
    ]
    assert hourly[-1].ts_init == session_start + timedelta(hours=6)
    assert len(result.series[Timeframe.D1].bars) == 1

    minute_snapshots = result.series[Timeframe.M1].snapshots
    assert not minute_snapshots[88].is_ready
    assert minute_snapshots[89].is_ready
    assert minute_snapshots[89].ema_18 == pytest.approx(
        _ema([bar.close for bar in result.series[Timeframe.M1].bars[:90]], period=18)
    )
    assert minute_snapshots[89].atr_14 == pytest.approx(2.0)


def test_regime_calibration_uses_only_the_explicit_historical_cutoff(
    contracts_registry: ContractRegistry,
) -> None:
    """Later volatile bars cannot affect thresholds calibrated for an earlier run start."""
    contract = contracts_registry.by_symbol("NQ")
    session_start = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
    session_end = datetime(2026, 7, 20, 20, tzinfo=UTC)
    initial_bars = _canonical_bars(
        contract.contract_id,
        _session_timestamps(contract, session_start, session_end, session_name="rth"),
    )
    full_bars = [
        bar
        if index < 200
        else bar.model_copy(
            update={
                "open": 21000.0 + (index % 2) * 100.0,
                "high": 21001.0 + (index % 2) * 100.0,
                "low": 20999.0 + (index % 2) * 100.0,
                "close": 21000.0 + (index % 2) * 100.0,
            }
        )
        for index, bar in enumerate(initial_bars)
    ]
    precomputer = MtfPrecomputer(contract, session_name="rth")
    full_series = precomputer.precompute(full_bars).series[Timeframe.M1]
    historical_series = precomputer.precompute(initial_bars[:200]).series[Timeframe.M1]
    historical_end = historical_series.snapshots[-1].bar.ts_init

    with_future_present = calibrate_regime_thresholds(
        full_series,
        historical_end=historical_end,
        separation_percentile=65.0,
        slope_percentile=65.0,
        slope_lookback=10,
    )
    historical_only = calibrate_regime_thresholds(
        historical_series,
        historical_end=historical_end,
        separation_percentile=65.0,
        slope_percentile=65.0,
        slope_lookback=10,
    )

    assert with_future_present == historical_only
    assert with_future_present.sample_size == 101


def _session_timestamps(
    contract: ContractSpec,
    start: datetime,
    end: datetime,
    *,
    session_name: str,
) -> list[datetime]:
    """Return sorted canonical 1m starts for complete deterministic fixture sessions."""
    return sorted(expected_minute_timestamps(contract, start, end, session_name=session_name))


def _canonical_bars(contract_id: str, timestamps: list[datetime]) -> list[CanonicalBar]:
    """Make tick-aligned source bars with a constant true range of two points."""
    return [
        CanonicalBar(
            timestamp=timestamp,
            open=20000.0 + index * 0.25,
            high=20001.0 + index * 0.25,
            low=19999.0 + index * 0.25,
            close=20000.5 + index * 0.25,
            volume=100 + index,
            contract_id=contract_id,
            source="fixture",
        )
        for index, timestamp in enumerate(timestamps)
    ]


def _ema(values: list[float], *, period: int) -> float:
    """Independent standard EMA recurrence used to assert the compiled output."""
    value = values[0]
    alpha = 2.0 / (period + 1)
    for item in values[1:]:
        value = value + alpha * (item - value)
    return value
