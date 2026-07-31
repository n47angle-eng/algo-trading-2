"""Tests for WO-003b native daily download (3b-1): additive path only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from futures_research.data.contracts import ContractRegistry
from futures_research.data.daily_download import (
    DAILY_SOURCE,
    DailyDataIngestionService,
    DailyDownloadPolicy,
    DailyHistoricalDownloadRequest,
    NativeDailyDownloader,
    map_daily_event_to_session_start,
    tag_thin_daily,
)
from futures_research.data.download import IbConnectionManager
from futures_research.data.ib import IbConnectionConfig, NautilusIbHistoricalClient
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import session_bounds_for_trading_date
from futures_research.data.storage import CanonicalStore


def _daily_bar(
    contract_id: str,
    timestamp: datetime,
    *,
    volume: int = 10_000,
    close: float = 20000.0,
    source: str = DAILY_SOURCE,
) -> CanonicalBar:
    return CanonicalBar(
        timestamp=timestamp,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=volume,
        contract_id=contract_id,
        source=source,
        source_request_id="fixture",
    )


class FakeDailyClient:
    """Replay-only client for native daily orchestration tests."""

    def __init__(
        self,
        bars_by_start: dict[datetime, list[CanonicalBar]] | None = None,
        *,
        failed_fetches: int = 0,
    ) -> None:
        self.bars_by_start = bars_by_start or {}
        self.failed_fetches = failed_fetches
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.fetch_calls = 0
        self.requests: list[DailyHistoricalDownloadRequest] = []

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def fetch_daily_bars(self, request: DailyHistoricalDownloadRequest) -> list[CanonicalBar]:
        self.fetch_calls += 1
        self.requests.append(request)
        if self.fetch_calls <= self.failed_fetches:
            raise TimeoutError("simulated IB pacing timeout")
        return list(self.bars_by_start.get(request.start, []))


async def no_sleep(_: float) -> None:
    """Keep retry tests deterministic and immediate."""


def test_map_daily_event_to_session_start_uses_ib_yyyymmdd_as_trading_date(
    contracts_registry: ContractRegistry,
) -> None:
    """Nautilus day-bar ts_event is midnight UTC of IB YYYYMMDD trading date.

    Vendor: market_data.py::_convert_ib_bar_date_to_unix_nanos for aggregation 14.
    Must not use America/Chicago local date of that midnight (prior evening → −1 day).
    """
    contract = contracts_registry.by_symbol("NQ")
    # IB label 20260720 → Nautilus ts_event = 2026-07-20 00:00:00Z
    event = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    session_start = map_daily_event_to_session_start(contract, event, session_name="eth")
    expected = session_bounds_for_trading_date(
        contract, datetime(2026, 7, 20).date(), session_name="eth"
    )
    assert expected is not None
    assert session_start == expected[0]
    assert session_start.astimezone(ZoneInfo("America/Chicago")).hour == 17
    # CT local of midnight UTC is the prior evening — must not become trading date.
    assert event.astimezone(ZoneInfo("America/Chicago")).date() == datetime(2026, 7, 19).date()


def test_map_daily_rejects_ct_local_date_shift_regression(
    contracts_registry: ContractRegistry,
) -> None:
    """Channel [048]: weekday mapping must not land one trading session early."""
    from futures_research.data.daily_download import (
        describe_daily_timestamp_chain,
        ib_daily_label_to_trading_date,
    )

    contract = contracts_registry.by_symbol("NQ")
    # C forensic sample shape: label day D must map to session open for trading date D.
    nautilus_ts = datetime(2026, 7, 8, 0, 0, tzinfo=UTC)
    chain = describe_daily_timestamp_chain(
        contract,
        nautilus_ts_event=nautilus_ts,
        session_name="eth",
    )
    assert chain["ib_date_label"] == "20260708"
    assert ib_daily_label_to_trading_date(nautilus_ts).isoformat() == "2026-07-08"
    expected = session_bounds_for_trading_date(
        contract, datetime(2026, 7, 8).date(), session_name="eth"
    )
    assert expected is not None
    assert chain["mapped_session_start"] == expected[0].isoformat()
    # Wrong path (CT local of midnight) would pick trading date 2026-07-07.
    wrong = session_bounds_for_trading_date(
        contract, datetime(2026, 7, 7).date(), session_name="eth"
    )
    assert wrong is not None
    assert chain["mapped_session_start"] != wrong[0].isoformat()


def test_tag_thin_daily_uses_trailing_median_threshold() -> None:
    """Volume below 5% of trailing 90-bar median is tagged info-only."""
    base = datetime(2025, 1, 2, 23, 0, tzinfo=UTC)
    bars = [
        _daily_bar("NQ-202609-CME", base + timedelta(days=index), volume=10_000)
        for index in range(90)
    ]
    thin_ts = base + timedelta(days=90)
    bars.append(_daily_bar("NQ-202609-CME", thin_ts, volume=10))  # 0.1% of median

    issues = tag_thin_daily(bars)

    assert len(issues) == 1
    assert issues[0].code == "thin_daily"
    assert issues[0].severity == "info"
    assert issues[0].timestamp == thin_ts


def test_tag_thin_daily_flags_zero_volume_after_lookback() -> None:
    """Zero-volume daily bars are thin even when the trailing median is also zero."""
    base = datetime(2025, 1, 2, 23, 0, tzinfo=UTC)
    bars = [
        _daily_bar("NQ-202609-CME", base + timedelta(days=index), volume=0) for index in range(91)
    ]

    issues = tag_thin_daily(bars)

    assert len(issues) == 1
    assert issues[0].code == "thin_daily"
    assert issues[0].details["volume"] == 0


def test_daily_ingestion_skips_completeness_and_stores(
    tmp_path: Path, contracts_registry: ContractRegistry
) -> None:
    """3b-1 quality policy: reasonableness only; completeness left for 3b-2."""
    contract = contracts_registry.by_symbol("NQ")
    store = CanonicalStore(tmp_path / "market-daily")
    service = DailyDataIngestionService(store, tmp_path / "reports")
    bounds = session_bounds_for_trading_date(
        contract, datetime(2026, 7, 20).date(), session_name="eth"
    )
    assert bounds is not None
    bars = [_daily_bar(contract.contract_id, bounds[0], volume=1_000)]

    result = service.ingest(contract.contract_id, bars, tick_size=contract.tick_size)

    assert result.write_summary.rows_stored == 1
    assert result.quality_report.checks["completeness"] == "skipped_3b1"
    assert result.quality_report.checks["reasonableness"] == "completed"
    assert result.quality_report.checks["anomaly"] == "skipped_3b1"
    stored = store.read(contract.contract_id)
    assert len(stored) == 1
    assert stored[0].source == DAILY_SOURCE


@pytest.mark.asyncio
async def test_native_daily_downloader_segments_and_persists(
    tmp_path: Path, contracts_registry: ContractRegistry
) -> None:
    """Year-sized segments land in market-daily with ib_native_daily source."""
    contract = contracts_registry.by_symbol("NQ")
    start = datetime(2024, 7, 1, tzinfo=UTC)
    mid = datetime(2025, 7, 1, tzinfo=UTC)
    end = datetime(2026, 7, 1, tzinfo=UTC)
    bar_a = _daily_bar(contract.contract_id, start + timedelta(days=10))
    bar_b = _daily_bar(contract.contract_id, mid + timedelta(days=10))
    client = FakeDailyClient({start: [bar_a], mid: [bar_b]})
    store = CanonicalStore(tmp_path / "market-daily")
    ingestion = DailyDataIngestionService(store, tmp_path / "reports")
    downloader = NativeDailyDownloader(
        IbConnectionManager(client, sleep=no_sleep),  # type: ignore[arg-type]
        client,
        ingestion,
        store,
        policy=DailyDownloadPolicy(
            segment_duration=timedelta(days=365),
            pacing_delay_seconds=0,
            max_segment_attempts=2,
            initial_retry_backoff_seconds=0,
        ),
        sleep=no_sleep,
    )
    request = DailyHistoricalDownloadRequest(
        contract=contract,
        start=start,
        end=end,
        request_id="daily-fixture",
    )

    summary = await downloader.download(request)

    assert len(summary.segments) == 2
    assert summary.rows_received == 2
    assert summary.rows_stored == 2
    assert client.disconnect_calls == 1
    stored = store.read(contract.contract_id)
    assert {bar.timestamp for bar in stored} == {bar_a.timestamp, bar_b.timestamp}
    assert all(bar.source == DAILY_SOURCE for bar in stored)


@pytest.mark.asyncio
async def test_native_daily_backfill_stops_after_consecutive_empty_segments(
    tmp_path: Path, contracts_registry: ContractRegistry
) -> None:
    """Max-depth discovery stops after consecutive empty year segments, not on first empty."""
    contract = contracts_registry.by_symbol("NQ")
    # Latest completed ETH day relative to as_of 2026-07-24.
    as_of = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    available_start = datetime(2025, 7, 23, 21, 0, tzinfo=UTC)  # approx one year window start
    bar = _daily_bar(
        contract.contract_id,
        datetime(2026, 1, 5, 23, 0, tzinfo=UTC),
        volume=5_000,
    )

    class WindowClient(FakeDailyClient):
        async def fetch_daily_bars(
            self, request: DailyHistoricalDownloadRequest
        ) -> list[CanonicalBar]:
            self.fetch_calls += 1
            self.requests.append(request)
            # Only the first (most recent) year-sized window returns data.
            if (
                request.end > datetime(2026, 1, 1, tzinfo=UTC)
                and request.start < as_of
                and request.start >= datetime(2025, 6, 1, tzinfo=UTC)
            ):
                return [bar]
            return []

    client = WindowClient()
    store = CanonicalStore(tmp_path / "market-daily")
    ingestion = DailyDataIngestionService(store, tmp_path / "reports")
    downloader = NativeDailyDownloader(
        IbConnectionManager(client, sleep=no_sleep),  # type: ignore[arg-type]
        client,
        ingestion,
        store,
        policy=DailyDownloadPolicy(
            segment_duration=timedelta(days=365),
            pacing_delay_seconds=0,
            max_segment_attempts=1,
            initial_retry_backoff_seconds=0,
            max_consecutive_empty_segments=3,
        ),
        sleep=no_sleep,
    )

    summary = await downloader.backfill(
        contract,
        as_of=as_of,
        max_consecutive_empty_segments=3,
    )

    assert summary.rows_stored == 1
    assert summary.unique_bar_count == 1
    assert summary.available_segment_count == 1
    assert len(summary.empty_segment_starts) == 3
    assert summary.stop_reason == "consecutive_empty_daily_segments"
    assert client.disconnect_calls == 1
    # Available window should be roughly within the last ~1.5y of as_of for this fixture.
    assert summary.earliest_available_timestamp == bar.timestamp
    del available_start  # fixture documentation only


@pytest.mark.asyncio
async def test_fetch_daily_bars_requests_one_day_last_and_maps_session_start(
    contracts_registry: ContractRegistry,
) -> None:
    """Adapter boundary must request 1-DAY-LAST and emit session-open ts_event."""
    contract = contracts_registry.by_symbol("NQ")
    # Nautilus day-bar ts_event = midnight UTC of IB YYYYMMDD (label 20260720).
    label_ns = int(datetime(2026, 7, 20, 0, 0, tzinfo=UTC).timestamp() * 1_000_000_000)
    raw_bar = SimpleNamespace(
        ts_event=label_ns,
        open="20000.00",
        high="20010.00",
        low="19990.00",
        close="20005.00",
        volume="12345",
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
    request = DailyHistoricalDownloadRequest(
        contract=contract,
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 7, 24, tzinfo=UTC),
        request_id="daily-adapter-fixture",
    )

    bars = await adapter.fetch_daily_bars(request)

    assert calls["bar_specifications"] == ["1-DAY-LAST"]
    assert calls["use_rth"] is False
    assert len(bars) == 1
    expected = session_bounds_for_trading_date(
        contract, datetime(2026, 7, 20).date(), session_name="eth"
    )
    assert expected is not None
    assert bars[0].timestamp == expected[0]
    assert bars[0].source == DAILY_SOURCE
    assert bars[0].volume == 12345
