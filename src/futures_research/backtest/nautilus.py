"""Direct canonical-Parquet wiring for the NautilusTrader backtest engine.

Canonical bars retain their provider-normalized start timestamp.  The bridge delivers each bar to
Nautilus at the end of its one-minute interval, so a strategy can never inspect an unfinished bar.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from nautilus_trader.backtest.engine import BacktestEngine  # type: ignore[import-not-found]
from nautilus_trader.model.data import (  # type: ignore[import-not-found]
    Bar,
    BarSpecification,
    BarType,
)
from nautilus_trader.model.enums import (
    AccountType,
    AggregationSource,
    AssetClass,
    BarAggregation,
    OmsType,
    PriceType,
)
from nautilus_trader.model.identifiers import (  # type: ignore[import-not-found]
    InstrumentId,
    Symbol,
    Venue,
)
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import (  # type: ignore[import-not-found]
    Currency,
    Money,
    Price,
    Quantity,
)

from futures_research.data.contracts import ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CanonicalStore

_ONE_MINUTE = timedelta(minutes=1)
_NANOSECONDS_PER_SECOND = 1_000_000_000
_NAUTILUS_ASSET_CLASS_BY_CATALOG_CLASS = {
    "equity_index_futures": AssetClass.INDEX,
    "commodity_futures": AssetClass.COMMODITY,
}


@dataclass(frozen=True)
class NautilusBacktestSession:
    """A configured engine plus the exact canonical bars loaded into it.

    The caller owns the session lifetime and must call :meth:`dispose` once the strategy has run.
    Later WO-002 stages attach the strategy and immutable run artifacts to this same boundary.
    """

    engine: BacktestEngine
    instrument: FuturesContract
    bar_type: BarType
    canonical_bars: tuple[CanonicalBar, ...]
    engine_bars: tuple[Bar, ...]

    def dispose(self) -> None:
        """Release the native engine resources after a completed or failed run."""
        self.engine.dispose()


def load_backtest_session(
    store: CanonicalStore,
    contract: ContractSpec,
    *,
    start: datetime,
    end: datetime,
    initial_balance: Decimal,
) -> NautilusBacktestSession:
    """Read one canonical range and wire its one-minute bars directly into Nautilus."""
    return build_backtest_session(
        contract,
        store.read(contract.contract_id, start=start, end=end),
        initial_balance=initial_balance,
    )


def build_backtest_session(
    contract: ContractSpec,
    canonical_bars: Iterable[CanonicalBar],
    *,
    initial_balance: Decimal,
) -> NautilusBacktestSession:
    """Create a ready-to-run engine without introducing a derived data catalog.

    Bars enter the engine as external one-minute LAST bars.  Their ``ts_event`` remains the
    canonical start label, while ``ts_init`` is one minute later so engine callbacks receive only
    completed bars.
    """
    materialized = tuple(canonical_bars)
    _validate_input_bars(contract, materialized)
    _validate_initial_balance(initial_balance)

    instrument = build_futures_instrument(contract)
    bar_type = BarType(
        instrument.id,
        BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
        AggregationSource.EXTERNAL,
    )
    engine_bars = tuple(_to_nautilus_bar(bar, bar_type, contract) for bar in materialized)
    engine = BacktestEngine()
    try:
        venue = Venue(contract.ib_exchange)
        currency = Currency.from_str(contract.currency)
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(initial_balance, currency)],
            base_currency=currency,
        )
        engine.add_instrument(instrument)
        engine.add_data(list(engine_bars))
    except Exception:
        engine.dispose()
        raise

    return NautilusBacktestSession(
        engine=engine,
        instrument=instrument,
        bar_type=bar_type,
        canonical_bars=materialized,
        engine_bars=engine_bars,
    )


def build_futures_instrument(contract: ContractSpec) -> FuturesContract:
    """Construct a simulation instrument from the owner-editable contract metadata."""
    try:
        asset_class = _NAUTILUS_ASSET_CLASS_BY_CATALOG_CLASS[contract.asset_class]
    except KeyError as exc:
        msg = f"no Nautilus asset-class mapping for catalog class: {contract.asset_class}"
        raise ValueError(msg) from exc

    price_precision = _decimal_places(contract.tick_size)
    multiplier = _whole_contract_multiplier(contract.point_value)
    venue = Venue(contract.ib_exchange)
    instrument_id = InstrumentId(Symbol(contract.ib_local_symbol), venue)
    expiration = datetime(
        contract.expiry.year,
        contract.expiry.month,
        contract.expiry.day,
        tzinfo=ZoneInfo(contract.timezone),
    ) + timedelta(days=1)
    expiration_ns = _to_unix_nanoseconds(expiration)

    return FuturesContract(
        instrument_id=instrument_id,
        raw_symbol=Symbol(contract.ib_local_symbol),
        asset_class=asset_class,
        exchange=contract.ib_exchange,
        currency=Currency.from_str(contract.currency),
        price_precision=price_precision,
        price_increment=Price.from_str(_format_price(contract.tick_size, price_precision)),
        multiplier=Quantity.from_int(multiplier),
        lot_size=Quantity.from_int(1),
        underlying=contract.symbol,
        activation_ns=0,
        expiration_ns=expiration_ns,
        ts_event=0,
        ts_init=0,
        info={
            "canonical_contract_id": contract.contract_id,
            "qualified_local_symbol": contract.ib_local_symbol,
            "exchange_timezone": contract.timezone,
        },
    )


def _validate_input_bars(contract: ContractSpec, bars: tuple[CanonicalBar, ...]) -> None:
    """Reject a malformed bridge input before it reaches the engine."""
    if not bars:
        msg = "cannot run a backtest without canonical bars"
        raise ValueError(msg)

    previous_timestamp: datetime | None = None
    for bar in bars:
        if bar.contract_id != contract.contract_id:
            msg = "canonical bars must belong to the configured individual contract"
            raise ValueError(msg)
        if bar.timestamp.second or bar.timestamp.microsecond:
            msg = "canonical backtest bars must be aligned to one-minute starts"
            raise ValueError(msg)
        if bar.volume < 0:
            msg = "canonical bar volume must not be negative"
            raise ValueError(msg)
        if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
            msg = "canonical backtest bars must be strictly timestamp-ordered"
            raise ValueError(msg)
        previous_timestamp = bar.timestamp


def _validate_initial_balance(initial_balance: Decimal) -> None:
    """Keep the S4 capital input explicit and usable by Nautilus's margin account."""
    if not initial_balance.is_finite() or initial_balance <= 0:
        msg = "initial_balance must be a positive finite Decimal"
        raise ValueError(msg)


def _to_nautilus_bar(bar: CanonicalBar, bar_type: BarType, contract: ContractSpec) -> Bar:
    """Convert a start-labelled canonical minute into a close-delivered Nautilus bar."""
    price_precision = _decimal_places(contract.tick_size)
    return Bar(
        bar_type=bar_type,
        open=_price_for_contract(bar.open, contract, price_precision),
        high=_price_for_contract(bar.high, contract, price_precision),
        low=_price_for_contract(bar.low, contract, price_precision),
        close=_price_for_contract(bar.close, contract, price_precision),
        volume=Quantity.from_int(bar.volume),
        ts_event=_to_unix_nanoseconds(bar.timestamp),
        ts_init=_to_unix_nanoseconds(bar.timestamp + _ONE_MINUTE),
    )


def _price_for_contract(value: float, contract: ContractSpec, precision: int) -> Price:
    """Format only tick-aligned canonical prices for Nautilus's fixed-point model."""
    decimal_value = Decimal(str(value))
    tick_size = Decimal(str(contract.tick_size))
    if decimal_value % tick_size:
        msg = f"canonical price {value} is not aligned to {contract.symbol}'s tick size"
        raise ValueError(msg)
    return Price.from_str(_format_price(value, precision))


def _decimal_places(value: float) -> int:
    """Return the fixed-point precision implied by a positive tick size."""
    decimal_value = Decimal(str(value))
    if not decimal_value.is_finite() or decimal_value <= 0:
        msg = "tick size must be positive"
        raise ValueError(msg)
    exponent = decimal_value.normalize().as_tuple().exponent
    if not isinstance(exponent, int):
        msg = "tick size must have a finite decimal precision"
        raise ValueError(msg)
    return max(0, -exponent)


def _whole_contract_multiplier(point_value: float) -> int:
    """Translate the configured whole-dollar point value into a futures multiplier."""
    decimal_value = Decimal(str(point_value))
    if decimal_value <= 0 or decimal_value != decimal_value.to_integral_value():
        msg = "configured point value must be a positive whole futures multiplier"
        raise ValueError(msg)
    return int(decimal_value)


def _format_price(value: float, precision: int) -> str:
    """Create the exact decimal spelling expected by Nautilus's fixed-point Price."""
    return f"{Decimal(str(value)):.{precision}f}"


def _to_unix_nanoseconds(value: datetime) -> int:
    """Convert an aware timestamp to UTC nanoseconds without float rounding."""
    utc_value = value.astimezone(UTC)
    seconds = calendar.timegm(utc_value.utctimetuple())
    return seconds * _NANOSECONDS_PER_SECOND + utc_value.microsecond * 1_000
