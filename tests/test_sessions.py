"""Tests for exchange-local RTH/ETH templates used by completeness checks."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from futures_research.data.contracts import ContractRegistry
from futures_research.data.sessions import (
    SessionDateMapper,
    expected_minute_timestamps,
    latest_completed_trading_date,
    session_bounds_for_trading_date,
    trading_date_for_session_start,
    trading_date_for_session_timestamp,
)


@pytest.mark.parametrize("symbol", ("NQ", "YM"))
def test_cash_aligned_rth_template_is_converted_from_chicago_time_to_utc(
    contracts_registry: ContractRegistry,
    symbol: str,
) -> None:
    """Equity-index RTH should end with the 16:00 New York cash close."""
    contract = contracts_registry.by_symbol(symbol)

    expected = expected_minute_timestamps(
        contract,
        datetime(2026, 7, 20, 13, 0, tzinfo=UTC),
        datetime(2026, 7, 20, 21, 0, tzinfo=UTC),
        session_name="rth",
    )

    assert len(expected) == 390
    assert min(expected) == datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
    assert max(expected) == datetime(2026, 7, 20, 19, 59, tzinfo=UTC)


def test_gc_rth_template_keeps_its_commodity_day_session(
    contracts_registry: ContractRegistry,
) -> None:
    """GC's RTH is its own day session, rather than the equity cash window."""
    gc = contracts_registry.by_symbol("GC")

    assert gc.sessions["rth"].start == time(7, 20)
    assert gc.sessions["rth"].end == time(12, 30)


def test_eth_template_spans_previous_local_calendar_day(
    contracts_registry: ContractRegistry,
) -> None:
    """The overnight session should begin Sunday 17:00 CT and end Monday 16:00 CT."""
    nq = contracts_registry.by_symbol("NQ")

    expected = expected_minute_timestamps(
        nq,
        datetime(2026, 7, 19, 21, 30, tzinfo=UTC),
        datetime(2026, 7, 20, 21, 30, tzinfo=UTC),
        session_name="eth",
    )

    assert len(expected) == 1380
    assert min(expected) == datetime(2026, 7, 19, 22, 0, tzinfo=UTC)
    assert max(expected) == datetime(2026, 7, 20, 20, 59, tzinfo=UTC)


def test_eth_backfill_uses_completed_exchange_trading_dates(
    contracts_registry: ContractRegistry,
) -> None:
    """A backfill must anchor its newest complete ETH session to the local close date."""
    contract = contracts_registry.by_symbol("NQ")
    as_of = datetime(2026, 7, 24, 0, tzinfo=UTC)

    trading_date = latest_completed_trading_date(contract, as_of, session_name="eth")
    bounds = session_bounds_for_trading_date(contract, trading_date, session_name="eth")

    assert trading_date == date(2026, 7, 23)
    assert bounds == (
        datetime(2026, 7, 22, 22, tzinfo=UTC),
        datetime(2026, 7, 23, 21, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("trading_date", "expected_start", "expected_end"),
    [
        (
            date(2026, 3, 9),
            datetime(2026, 3, 8, 22, tzinfo=UTC),
            datetime(2026, 3, 9, 21, tzinfo=UTC),
        ),
        (
            date(2026, 11, 2),
            datetime(2026, 11, 1, 23, tzinfo=UTC),
            datetime(2026, 11, 2, 22, tzinfo=UTC),
        ),
    ],
)
def test_eth_mapping_keeps_exchange_label_across_us_dst_boundaries(
    contracts_registry: ContractRegistry,
    trading_date: date,
    expected_start: datetime,
    expected_end: datetime,
) -> None:
    contract = contracts_registry.by_symbol("NQ")
    bounds = session_bounds_for_trading_date(
        contract,
        trading_date,
        session_name="eth",
    )

    assert bounds == (expected_start, expected_end)
    assert (
        trading_date_for_session_timestamp(
            contract,
            expected_start,
            session_name="eth",
        )
        == trading_date
    )
    assert (
        trading_date_for_session_timestamp(
            contract,
            expected_end - timedelta(minutes=1),
            session_name="eth",
        )
        == trading_date
    )
    assert (
        trading_date_for_session_start(
            contract,
            expected_start,
            session_name="eth",
        )
        == trading_date
    )


def test_session_mapper_bulk_path_preserves_input_order_and_rejects_outside(
    contracts_registry: ContractRegistry,
) -> None:
    contract = contracts_registry.by_symbol("NQ")
    monday = session_bounds_for_trading_date(
        contract,
        date(2026, 7, 20),
        session_name="eth",
    )
    tuesday = session_bounds_for_trading_date(
        contract,
        date(2026, 7, 21),
        session_name="eth",
    )
    assert monday is not None
    assert tuesday is not None
    mapper = SessionDateMapper(contract, session_name="eth")

    mapped = mapper.trading_dates_for_timestamps(
        [
            tuesday[0],
            monday[1] - timedelta(minutes=1),
            monday[0],
        ]
    )

    assert mapped == (
        date(2026, 7, 21),
        date(2026, 7, 20),
        date(2026, 7, 20),
    )
    with pytest.raises(ValueError, match="exactly one"):
        mapper.trading_dates_for_timestamps(
            [monday[0] - timedelta(minutes=1)]
        )
    with pytest.raises(ValueError, match="session start"):
        trading_date_for_session_start(
            contract,
            monday[0] + timedelta(minutes=1),
            session_name="eth",
        )
