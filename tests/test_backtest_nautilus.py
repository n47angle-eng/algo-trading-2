"""Tests for the canonical-data to Nautilus backtest-engine bridge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.model.enums import AssetClass

from futures_research.backtest.nautilus import (
    build_futures_instrument,
    load_backtest_session,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CanonicalStore


def test_contract_metadata_builds_the_expected_nautilus_future(
    contracts_registry: ContractRegistry,
) -> None:
    """Configured local symbols and contract economics define the simulation instrument."""
    nq = build_futures_instrument(contracts_registry.by_symbol("NQ"))
    ym = build_futures_instrument(contracts_registry.by_symbol("YM"))
    gc = build_futures_instrument(contracts_registry.by_symbol("GC"))

    assert str(nq.id) == "NQU6.CME"
    assert str(nq.price_increment) == "0.25"
    assert str(nq.multiplier) == "20"
    assert nq.asset_class == AssetClass.INDEX
    assert ym.asset_class == AssetClass.INDEX
    assert gc.asset_class == AssetClass.COMMODITY
    assert gc.info["canonical_contract_id"] == "GC-202608-COMEX"


def test_nautilus_asset_class_comes_from_catalog_class_not_symbol(
    contracts_registry: ContractRegistry,
) -> None:
    """A hard-coded NQ/YM/GC symbol table would incorrectly accept this malformed catalog row."""
    malformed = contracts_registry.by_symbol("NQ").model_copy(
        update={"asset_class": "unsupported_catalog_class"}
    )

    with pytest.raises(ValueError, match="catalog class"):
        build_futures_instrument(malformed)


@pytest.mark.filterwarnings("ignore:Timestamp.utcnow is deprecated.*:pandas.errors.Pandas4Warning")
def test_canonical_parquet_range_runs_through_nautilus_after_each_minute_closes(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """Direct wiring preserves labels while delaying engine delivery by one minute."""
    contract = contracts_registry.by_symbol("NQ")
    bars = _canonical_bars(contract.contract_id)
    store = CanonicalStore(tmp_path / "market")
    store.append(bars)
    session = load_backtest_session(
        store,
        contract,
        start=bars[0].timestamp,
        end=bars[-1].timestamp + timedelta(minutes=1),
        initial_balance=Decimal("100000"),
    )

    try:
        assert [bar.ts_event for bar in session.engine_bars] == [
            _nanoseconds(bar.timestamp) for bar in bars
        ]
        assert [bar.ts_init for bar in session.engine_bars] == [
            _nanoseconds(bar.timestamp + timedelta(minutes=1)) for bar in bars
        ]

        session.engine.run()
        result = session.engine.get_result()
    finally:
        session.dispose()

    assert result.iterations == len(bars)
    assert result.total_orders == 0


def _canonical_bars(contract_id: str) -> list[CanonicalBar]:
    """Create a deterministic five-minute canonical range for bridge integration coverage."""
    start = datetime(2026, 7, 20, 14, 30, tzinfo=UTC)
    return [
        CanonicalBar(
            timestamp=start + timedelta(minutes=index),
            open=20000.0 + index * 0.25,
            high=20001.0 + index * 0.25,
            low=19999.5 + index * 0.25,
            close=20000.5 + index * 0.25,
            volume=100 + index,
            contract_id=contract_id,
            source="fixture",
        )
        for index in range(5)
    ]


def _nanoseconds(value: datetime) -> int:
    """Return an exact integer timestamp used by the canonical-to-Nautilus assertions."""
    return int(value.timestamp()) * 1_000_000_000
