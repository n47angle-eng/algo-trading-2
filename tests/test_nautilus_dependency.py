"""Pin-level smoke test for the official NautilusTrader IB historical adapter."""


def test_nautilus_ib_historical_adapter_is_installed() -> None:
    """WO-001 must fail loudly if the pinned package omits the required IB extra."""
    from nautilus_trader.adapters.interactive_brokers.historical.client import (
        HistoricInteractiveBrokersClient,
    )

    assert HistoricInteractiveBrokersClient.__name__ == "HistoricInteractiveBrokersClient"
