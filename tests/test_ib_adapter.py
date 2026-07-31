"""Compatibility tests for NautilusTrader's stable IB historical-client boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from futures_research.data.contracts import ContractRegistry
from futures_research.data.download import HistoricalDownloadRequest
from futures_research.data.ib import IbConnectionConfig, NautilusIbHistoricalClient


@pytest.mark.asyncio
async def test_stable_adapter_disconnect_fallback_uses_internal_data_client() -> None:
    """The pinned stable client has no public disconnect, so its internal close path is used."""
    calls: list[str] = []

    class StableDataClient:
        async def _disconnect(self) -> None:
            calls.append("stable")

    stable_client = SimpleNamespace(_data_client=StableDataClient())
    await NautilusIbHistoricalClient._disconnect_client(stable_client)

    assert calls == ["stable"]


@pytest.mark.asyncio
async def test_public_disconnect_path_wins_when_a_future_stable_release_supplies_it() -> None:
    """The compatibility layer must migrate to a future public API without a code rewrite."""
    calls: list[str] = []

    class FutureClient:
        async def disconnect(self) -> None:
            calls.append("public")

    await NautilusIbHistoricalClient._disconnect_client(FutureClient())

    assert calls == ["public"]


def test_adapter_preserves_nautilus_historical_start_timestamp() -> None:
    """Pinned Nautilus ``ts_event`` is bar-start; its end-labelled ``ts_init`` must be ignored."""
    raw_bar = SimpleNamespace(
        ts_event=1_784_557_800_000_000_000,
        ts_init=1_784_557_860_000_000_000,
        open="20000.00",
        high="20001.00",
        low="19999.50",
        close="20000.50",
        volume="12",
    )

    normalized = NautilusIbHistoricalClient._to_canonical_bar(
        raw_bar,
        contract_id="NQ-202609-CME",
        request_id="fixture-request",
    )

    assert normalized.timestamp == datetime(2026, 7, 20, 14, 30, tzinfo=UTC)
    assert normalized.close == 20000.5
    assert normalized.source_request_id == "fixture-request"


def test_connection_config_requires_owner_managed_environment_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paper/live port must come from an ignored owner environment file, never source defaults."""
    env_file = tmp_path / "ib.env"
    env_file.write_text("IB_HOST=127.0.0.1\nIB_PORT=7498\nIB_CLIENT_ID=7\n", encoding="utf-8")
    for name in ("IB_HOST", "IB_PORT", "IB_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)

    config = IbConnectionConfig.from_environment(env_file=env_file)

    assert (config.host, config.port, config.client_id) == ("127.0.0.1", 7498, 7)


def test_connection_config_has_no_source_code_connection_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent owner configuration must fail rather than silently target a default IB session."""
    for name in ("IB_HOST", "IB_PORT", "IB_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="missing required IB environment variables"):
        IbConnectionConfig.from_environment(env_file=tmp_path / "missing.env")


@pytest.mark.asyncio
async def test_fetch_uses_pinned_nautilus_historical_request_shape(
    contracts_registry: ContractRegistry,
) -> None:
    """The real adapter boundary must request one-minute individual-contract data correctly."""
    raw_bar = SimpleNamespace(
        ts_event=1_784_557_800_000_000_000,
        open="20000.00",
        high="20001.00",
        low="19999.50",
        close="20000.50",
        volume="12",
    )
    calls: dict[str, object] = {}

    class FakeNautilusClient:
        async def request_bars(self, **kwargs: object) -> list[SimpleNamespace]:
            calls.update(kwargs)
            return [raw_bar]

    adapter = NautilusIbHistoricalClient(
        IbConnectionConfig(host="127.0.0.1", port=7498, client_id=7, request_timeout_seconds=90)
    )
    adapter._client = FakeNautilusClient()
    adapter._connected = True
    request = HistoricalDownloadRequest(
        contract=contracts_registry.by_symbol("NQ"),
        start=datetime(2026, 7, 20, 14, 30, tzinfo=UTC),
        end=datetime(2026, 7, 20, 14, 35, tzinfo=UTC),
        request_id="fixture-request",
    )

    bars = await adapter.fetch_bars(request)

    assert calls["bar_specifications"] == ["1-MINUTE-LAST"]
    assert calls["start_date_time"] == datetime(2026, 7, 20, 9, 30)
    assert calls["end_date_time"] == datetime(2026, 7, 20, 9, 35)
    assert calls["tz_name"] == "America/Chicago"
    assert calls["use_rth"] is False
    assert calls["timeout"] == 90
    ib_contract = calls["contracts"][0]  # type: ignore[index]
    assert ib_contract.localSymbol == "NQU6"
    assert ib_contract.lastTradeDateOrContractMonth == "202609"
    assert ib_contract.includeExpired is True
    assert bars[0].contract_id == "NQ-202609-CME"


@pytest.mark.asyncio
async def test_fetch_returns_empty_only_after_explicit_ib_no_data_signal(
    contracts_registry: ContractRegistry,
) -> None:
    """A hung Nautilus 1.230 await must not hide IB's historical-depth boundary."""
    request_started = asyncio.Event()

    class FakeNautilusClient:
        async def request_bars(self, **_: object) -> list[SimpleNamespace]:
            request_started.set()
            await asyncio.Future()

    adapter = NautilusIbHistoricalClient(
        IbConnectionConfig(host="127.0.0.1", port=7498, client_id=7)
    )
    adapter._client = FakeNautilusClient()
    adapter._connected = True
    request = HistoricalDownloadRequest(
        contract=contracts_registry.by_symbol("NQ"),
        start=datetime(2026, 7, 20, 14, 30, tzinfo=UTC),
        end=datetime(2026, 7, 20, 14, 35, tzinfo=UTC),
    )

    task = asyncio.create_task(adapter.fetch_bars(request))
    await request_started.wait()
    adapter._record_no_data_error(
        error_code=162,
        error_string="HMDS query returned no data",
    )

    assert await task == []


@pytest.mark.asyncio
async def test_fetch_raises_for_ib_historical_query_cancellation(
    contracts_registry: ContractRegistry,
) -> None:
    """A transient HMDS cancellation must reach the downloader retry path, not depth discovery."""
    request_started = asyncio.Event()

    class FakeNautilusClient:
        async def request_bars(self, **_: object) -> list[SimpleNamespace]:
            request_started.set()
            await asyncio.Future()

    adapter = NautilusIbHistoricalClient(
        IbConnectionConfig(host="127.0.0.1", port=7498, client_id=7)
    )
    adapter._client = FakeNautilusClient()
    adapter._connected = True
    request = HistoricalDownloadRequest(
        contract=contracts_registry.by_symbol("NQ"),
        start=datetime(2026, 7, 20, 14, 30, tzinfo=UTC),
        end=datetime(2026, 7, 20, 14, 35, tzinfo=UTC),
    )

    task = asyncio.create_task(adapter.fetch_bars(request))
    await request_started.wait()
    adapter._record_historical_error(
        error_code=162,
        error_string="API historical data query cancelled: 10005",
    )

    with pytest.raises(ConnectionError, match="query cancelled"):
        await task
