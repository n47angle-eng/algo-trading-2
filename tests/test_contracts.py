"""Tests for individual-contract metadata and conservative roll windows."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from futures_research.data.contracts import ContractRegistry


def test_initial_contracts_are_individual_contracts(contracts_registry: ContractRegistry) -> None:
    """NQ, YM, and GC must remain named individual contracts, never continuous series."""
    assert set(contracts_registry.contracts) == {"NQ", "YM", "GC"}
    assert contracts_registry.by_symbol("NQ").contract_id == "NQ-202609-CME"
    assert contracts_registry.by_symbol("NQ").ib_local_symbol == "NQU6"
    assert contracts_registry.by_symbol("YM").contract_id == "YM-202609-CBOT"
    assert contracts_registry.by_symbol("GC").contract_id == "GC-202608-COMEX"
    assert contracts_registry.by_symbol("NQ").display_name == "E-mini Nasdaq-100"
    assert contracts_registry.by_symbol("YM").display_name == "E-mini Dow"
    assert contracts_registry.by_symbol("GC").display_name == "Gold"
    assert contracts_registry.by_symbol("NQ").asset_class == "equity_index_futures"
    assert contracts_registry.by_symbol("YM").asset_class == "equity_index_futures"
    assert contracts_registry.by_symbol("GC").asset_class == "commodity_futures"


def test_roll_blackout_is_an_inclusive_three_calendar_day_window(
    contracts_registry: ContractRegistry,
) -> None:
    """The Owner-approved safety window covers three total calendar dates around expiry."""
    nq = contracts_registry.by_symbol("NQ")

    assert nq.expiry == date(2026, 9, 18)
    assert nq.roll_blackout_dates() == {
        date(2026, 9, 17),
        date(2026, 9, 18),
        date(2026, 9, 19),
    }
    assert nq.is_roll_blackout(date(2026, 9, 17))
    assert not nq.is_roll_blackout(date(2026, 9, 20))


def test_owner_approved_execution_costs_are_explicit_per_contract(
    contracts_registry: ContractRegistry,
) -> None:
    """The conservative P1 cost model must not depend on hidden code defaults."""
    nq = contracts_registry.by_symbol("NQ").execution_costs
    ym = contracts_registry.by_symbol("YM").execution_costs
    gc = contracts_registry.by_symbol("GC").execution_costs

    assert (nq.commission_per_side, ym.commission_per_side, gc.commission_per_side) == (
        2.50,
        2.50,
        2.80,
    )
    assert nq.slippage_ticks.breakout_entry == 1
    assert nq.slippage_ticks.stop_exit == 2
    assert nq.slippage_ticks.target_exit == 0
    assert nq.slippage_ticks.day_end_exit == 1
    assert not nq.target_requires_through


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("asset_class", "unapproved_futures"),
        ("asset_class", ""),
        ("display_name", ""),
        ("display_name", "   "),
    ],
)
def test_contract_catalog_identity_fields_fail_closed(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    """The config itself is the one controlled catalog, so no partial registry is usable."""
    source = Path(__file__).resolve().parents[1] / "config" / "contracts.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    raw["contracts"]["NQ"][field] = value
    path = tmp_path / "contracts.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError):
        ContractRegistry.from_yaml(path)
