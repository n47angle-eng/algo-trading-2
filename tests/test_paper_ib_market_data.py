from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from futures_research.data.contracts import ContractRegistry
from futures_research.paper.ib_market_data import (
    IbGatewaySettings,
    IbMarketDataAdapter,
    IbTransportBar,
    IbTransportProbe,
)
from futures_research.paper.market_data import (
    FormingBarUpdate,
    MarketTopic,
)
from futures_research.paper.models import ClosedMarketInput
from futures_research.paths import PROJECT_ROOT


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.bar_callback: Callable[[IbTransportBar], None] | None = None
        self.mode_callback: Callable[[str], None] | None = None

    def connect(self, host: str, port: int, client_id: int, timeout: float) -> None:
        self.calls.append(f"connect:{host}:{port}:{client_id}:{timeout}")

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def probe_contract(
        self,
        *,
        request_id: int,
        local_symbol: str,
        exchange: str,
        currency: str,
        requested_mode: str,
        timeout: float,
    ) -> IbTransportProbe:
        self.calls.append(
            f"probe:{request_id}:{local_symbol}:{exchange}:{currency}:"
            f"{requested_mode}:{timeout}"
        )
        return IbTransportProbe(
            local_symbol=local_symbol,
            exchange=exchange,
            currency=currency,
            con_id=620730920,
            provider_data_type="delayed",
            market_callback_seen=True,
        )

    def subscribe_bars(
        self,
        *,
        request_id: int,
        local_symbol: str,
        exchange: str,
        currency: str,
        requested_mode: str,
        on_bar: Callable[[IbTransportBar], None],
        on_mode: Callable[[str], None],
    ) -> None:
        self.calls.append(
            f"subscribe:{request_id}:{local_symbol}:{exchange}:{currency}:"
            f"{requested_mode}"
        )
        self.bar_callback = on_bar
        self.mode_callback = on_mode

    def unsubscribe_bars(self, request_id: int) -> None:
        self.calls.append(f"unsubscribe:{request_id}")

    def backfill_gap(
        self,
        *,
        request_id: int,
        local_symbol: str,
        exchange: str,
        currency: str,
        start_at: str,
        end_at: str,
        timeout: float,
    ) -> tuple[IbTransportBar, ...]:
        self.calls.append(f"backfill:{request_id}:{start_at}:{end_at}:{timeout}")
        return (
            IbTransportBar(
                start_at="2026-07-31T01:00:00Z",
                open_price=20_000,
                high_price=20_002,
                low_price=19_999,
                close_price=20_001,
                volume=10,
                complete=True,
            ),
        )


def registry() -> ContractRegistry:
    return ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")


def test_gateway_settings_are_exact_and_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IB_HOST", raising=False)
    monkeypatch.delenv("IB_PORT", raising=False)
    monkeypatch.delenv("IB_CLIENT_ID", raising=False)

    assert IbGatewaySettings.from_env() == IbGatewaySettings(
        host="127.0.0.1",
        port=7498,
        client_id=7,
    )

    monkeypatch.setenv("IB_PORT", "7497")
    with pytest.raises(ValueError, match="7498"):
        IbGatewaySettings.from_env()


def test_adapter_public_callable_surface_is_market_data_only() -> None:
    public = {
        name
        for name, member in inspect.getmembers(
            IbMarketDataAdapter,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }

    assert public == {
        "connect",
        "disconnect",
        "probe",
        "subscribe_bars",
        "unsubscribe_bars",
        "backfill_gap",
    }

    banned = (
        "placeOrder",
        "cancelOrder",
        "reqOpenOrders",
        "reqAllOpenOrders",
        "reqAccountUpdates",
        "reqPositions",
    )
    fake = FakeTransport()
    adapter = IbMarketDataAdapter(
        settings=IbGatewaySettings(),
        registry=registry(),
        transport=fake,
    )
    assert all(not hasattr(adapter, name) for name in banned)

    source = Path(inspect.getsourcefile(IbMarketDataAdapter) or "").read_text(
        encoding="utf-8"
    )
    assert all(name not in source for name in banned)


def test_probe_reports_delayed_truth_and_exact_contract_identity() -> None:
    fake = FakeTransport()
    adapter = IbMarketDataAdapter(
        settings=IbGatewaySettings(),
        registry=registry(),
        transport=fake,
        clock=lambda: datetime(2026, 7, 31, 1, 2, 3, tzinfo=UTC),
    )
    session = adapter.connect()

    probe = adapter.probe("NQ-202609-CME")

    assert session.host == "127.0.0.1"
    assert session.port == 7498
    assert session.client_id == 7
    assert session.read_only is True
    assert probe.provider_contract == "NQU6@CME"
    assert probe.mode == "test_delayed"
    assert probe.provider_reported_type == "delayed"
    assert probe.market_callback_seen is True
    assert adapter.transport_audit.order_requests == 0


def test_subscription_canonicalizes_forming_and_closed_callbacks() -> None:
    fake = FakeTransport()
    adapter = IbMarketDataAdapter(
        settings=IbGatewaySettings(),
        registry=registry(),
        transport=fake,
        clock=lambda: datetime(2026, 7, 31, 1, 2, 3, tzinfo=UTC),
    )
    adapter.connect()
    observed: list[FormingBarUpdate | ClosedMarketInput] = []
    subscription_id = adapter.subscribe_bars(
        MarketTopic("NQ-202609-CME", "1m", "test_delayed"),
        observed.append,
    )
    assert fake.mode_callback is not None
    assert fake.bar_callback is not None
    fake.mode_callback("delayed")
    fake.bar_callback(
        IbTransportBar(
            start_at="2026-07-31T01:00:00Z",
            open_price=20_000,
            high_price=20_002,
            low_price=19_999,
            close_price=20_001,
            volume=10,
            complete=False,
        )
    )
    fake.bar_callback(
        IbTransportBar(
            start_at="2026-07-31T01:00:00Z",
            open_price=20_000,
            high_price=20_002,
            low_price=19_999,
            close_price=20_001,
            volume=10,
            complete=True,
        )
    )

    assert isinstance(observed[0], FormingBarUpdate)
    assert isinstance(observed[1], ClosedMarketInput)
    assert observed[0].mode == "test_delayed"
    assert observed[1].mode == "test_delayed"
    assert observed[1].event_at == "2026-07-31T01:01:00Z"
    adapter.unsubscribe_bars(subscription_id)
    adapter.disconnect()
    assert adapter.transport_audit.order_requests == 0
