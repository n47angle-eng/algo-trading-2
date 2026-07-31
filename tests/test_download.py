"""Tests for paced segmentation and bounded Gateway/TWS reconnect behavior."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from futures_research.data.contracts import ContractRegistry
from futures_research.data.download import (
    ConnectionState,
    DownloadPolicy,
    HistoricalDownloadRequest,
    IbConnectionManager,
    SegmentedHistoricalDownloader,
)
from futures_research.data.ingestion import DataIngestionService
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CanonicalStore


class FakeHistoricalClient:
    """Replay-only client that exercises connection and request orchestration deterministically."""

    def __init__(self, *, failed_connections: int = 0, failed_fetches: int = 0) -> None:
        self.failed_connections = failed_connections
        self.failed_fetches = failed_fetches
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.fetch_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self.failed_connections:
            raise OSError("simulated gateway unavailable")

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def fetch_bars(self, request: HistoricalDownloadRequest) -> list[CanonicalBar]:
        self.fetch_calls += 1
        if self.fetch_calls <= self.failed_fetches:
            raise TimeoutError("simulated IB pacing timeout")
        return [
            CanonicalBar(
                timestamp=request.start,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=10,
                contract_id=request.contract.contract_id,
                source="fixture",
                source_request_id=request.request_id,
            )
        ]


class PermanentFailureClient(FakeHistoricalClient):
    """A deterministic contract-definition failure that must not trigger a reconnect storm."""

    async def fetch_bars(self, request: HistoricalDownloadRequest) -> list[CanonicalBar]:
        self.fetch_calls += 1
        raise ValueError("simulated malformed contract")


class BoundaryHistoricalClient(FakeHistoricalClient):
    """Return source data only for explicitly configured available session starts."""

    def __init__(self, available_starts: set[datetime]) -> None:
        super().__init__()
        self._available_starts = available_starts

    async def fetch_bars(self, request: HistoricalDownloadRequest) -> list[CanonicalBar]:
        self.fetch_calls += 1
        if request.start not in self._available_starts:
            return []
        return [
            CanonicalBar(
                timestamp=request.start,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=10,
                contract_id=request.contract.contract_id,
                source="fixture",
                source_request_id=request.request_id,
            )
        ]


async def no_sleep(_: float) -> None:
    """Keep retry tests deterministic and immediate."""


@pytest.mark.asyncio
async def test_connection_manager_retries_then_connects() -> None:
    """A transient Gateway failure must use bounded backoff and recover."""
    client = FakeHistoricalClient(failed_connections=2)
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    manager = IbConnectionManager(
        client,
        max_attempts=3,
        initial_backoff_seconds=0.5,
        sleep=record_sleep,
    )
    await manager.connect()

    assert manager.state == ConnectionState.CONNECTED
    assert client.connect_calls == 3
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_downloader_segments_paces_and_reconnects(
    tmp_path: Path, contracts_registry: ContractRegistry
) -> None:
    """The downloader should be sequential, persist fixture data, and retry a failed segment."""
    client = FakeHistoricalClient(failed_fetches=1)
    connection = IbConnectionManager(client, sleep=no_sleep)
    ingestion = DataIngestionService(CanonicalStore(tmp_path / "market"), tmp_path / "reports")
    downloader = SegmentedHistoricalDownloader(
        connection,
        client,
        ingestion,
        policy=DownloadPolicy(
            segment_duration=timedelta(days=1),
            pacing_delay_seconds=0,
            max_segment_attempts=2,
            initial_retry_backoff_seconds=0,
        ),
        sleep=no_sleep,
    )
    request = HistoricalDownloadRequest(
        contract=contracts_registry.by_symbol("NQ"),
        start=datetime(2026, 7, 20, tzinfo=UTC),
        end=datetime(2026, 7, 22, tzinfo=UTC),
        request_id="fixture-download",
    )

    summary = await downloader.download(request)

    assert len(summary.segments) == 2
    assert summary.rows_received == 2
    assert client.fetch_calls == 3
    assert client.connect_calls == 2  # original connect plus reconnect after the failed segment
    assert connection.state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_downloader_does_not_retry_non_transport_contract_errors(
    tmp_path: Path, contracts_registry: ContractRegistry
) -> None:
    """Permanent configuration errors must surface once instead of creating a new IB client."""
    client = PermanentFailureClient()
    connection = IbConnectionManager(client, sleep=no_sleep)
    ingestion = DataIngestionService(CanonicalStore(tmp_path / "market"), tmp_path / "reports")
    downloader = SegmentedHistoricalDownloader(
        connection,
        client,
        ingestion,
        policy=DownloadPolicy(pacing_delay_seconds=0),
        sleep=no_sleep,
    )
    request = HistoricalDownloadRequest(
        contract=contracts_registry.by_symbol("NQ"),
        start=datetime(2026, 7, 20, tzinfo=UTC),
        end=datetime(2026, 7, 20, 0, 1, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="without retry"):
        await downloader.download(request)

    assert client.fetch_calls == 1
    assert client.connect_calls == 1
    assert client.disconnect_calls == 1


@pytest.mark.asyncio
async def test_backfill_stops_at_consecutive_empty_exchange_sessions_without_error_reports(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """Unavailable provider history is a boundary, not an ``empty_batch`` data-quality error."""
    contract = contracts_registry.by_symbol("NQ")
    available_start = datetime(2026, 7, 22, 22, tzinfo=UTC)
    client = BoundaryHistoricalClient({available_start})
    connection = IbConnectionManager(client, sleep=no_sleep)
    reports_root = tmp_path / "reports"
    downloader = SegmentedHistoricalDownloader(
        connection,
        client,
        DataIngestionService(CanonicalStore(tmp_path / "market"), reports_root),
        policy=DownloadPolicy(pacing_delay_seconds=0),
        sleep=no_sleep,
    )

    summary = await downloader.backfill(
        contract,
        as_of=datetime(2026, 7, 24, tzinfo=UTC),
        max_consecutive_empty_sessions=2,
    )

    assert summary.available_session_count == 1
    assert summary.rows_received == 1
    assert summary.rows_stored == 1
    assert summary.earliest_available_timestamp == available_start
    assert summary.empty_session_dates == (date(2026, 7, 22), date(2026, 7, 21))
    assert summary.stop_reason == "consecutive_empty_trading_sessions"
    assert len(summary.quality_report_paths) == 1
    assert len(list((reports_root / contract.contract_id).glob("*.json"))) == 1
    assert connection.state == ConnectionState.DISCONNECTED
