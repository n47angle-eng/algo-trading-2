"""Sequential, paced historical-download orchestration for IB market data."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from futures_research.data.contracts import ContractSpec
from futures_research.data.ingestion import DataIngestionService, IngestionResult
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import (
    expected_minute_timestamps,
    latest_completed_trading_date,
    session_bounds_for_trading_date,
)


class HistoricalDownloadRequest(BaseModel):
    """One inclusive-start, exclusive-end historical 1-minute bar request."""

    model_config = ConfigDict(frozen=True)

    contract: ContractSpec
    start: datetime
    end: datetime
    use_rth: bool = False
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    @field_validator("start", "end", mode="after")
    @classmethod
    def normalize_to_utc(cls, value: datetime) -> datetime:
        """Keep request boundaries aligned with the canonical storage policy."""
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "download boundaries must include a timezone"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def check_time_range(self) -> HistoricalDownloadRequest:
        """Reject empty or reversed historical windows before any network request."""
        if self.start >= self.end:
            msg = "download start must be earlier than end"
            raise ValueError(msg)
        return self

    def segment(self, start: datetime, end: datetime, index: int) -> HistoricalDownloadRequest:
        """Create a traceable child request for one paced historical segment."""
        return self.model_copy(
            update={"start": start, "end": end, "request_id": f"{self.request_id}-{index:04d}"}
        )


class HistoricalBarsClient(Protocol):
    """Minimum behavior required from a historical-data provider adapter."""

    async def connect(self) -> None:
        """Open the provider session."""

    async def disconnect(self) -> None:
        """Close the provider session."""

    async def fetch_bars(self, request: HistoricalDownloadRequest) -> list[CanonicalBar]:
        """Fetch one already-bounded request segment."""


class ConnectionState(StrEnum):
    """Observable state for Gateway/TWS session management."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


Sleep = Callable[[float], Awaitable[None]]


class IbConnectionManager:
    """Retry a Gateway/TWS session connection with bounded exponential backoff."""

    def __init__(
        self,
        client: HistoricalBarsClient,
        *,
        max_attempts: int = 3,
        initial_backoff_seconds: float = 1.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be at least one"
            raise ValueError(msg)
        if initial_backoff_seconds < 0:
            msg = "initial_backoff_seconds must not be negative"
            raise ValueError(msg)
        self._client = client
        self._max_attempts = max_attempts
        self._initial_backoff_seconds = initial_backoff_seconds
        self._sleep = sleep
        self._state = ConnectionState.DISCONNECTED
        self._last_error: Exception | None = None

    @property
    def state(self) -> ConnectionState:
        """Return the latest connection lifecycle state."""
        return self._state

    @property
    def last_error(self) -> Exception | None:
        """Return the most recent failed connection exception, if any."""
        return self._last_error

    async def connect(self) -> None:
        """Connect, retrying transient failures without an unbounded loop."""
        if self._state == ConnectionState.CONNECTED:
            return
        for attempt in range(self._max_attempts):
            self._state = (
                ConnectionState.CONNECTING if attempt == 0 else ConnectionState.RECONNECTING
            )
            try:
                await self._client.connect()
            except Exception as exc:
                self._last_error = exc
                await self._safe_disconnect()
                if attempt == self._max_attempts - 1:
                    self._state = ConnectionState.FAILED
                    msg = f"IB connection failed after {self._max_attempts} attempts"
                    raise ConnectionError(msg) from exc
                await self._sleep(self._initial_backoff_seconds * (2**attempt))
            else:
                self._state = ConnectionState.CONNECTED
                self._last_error = None
                return

    async def reconnect(self) -> None:
        """Force a clean disconnect before attempting the normal bounded reconnect policy."""
        await self.disconnect()
        await self.connect()

    async def disconnect(self) -> None:
        """Disconnect safely even when the provider already dropped the session."""
        await self._safe_disconnect()
        self._state = ConnectionState.DISCONNECTED

    async def _safe_disconnect(self) -> None:
        # A failed cleanup must not hide the original connection or request exception.
        with suppress(Exception):
            await self._client.disconnect()


@dataclass(frozen=True)
class DownloadPolicy:
    """Conservative IB request sizing and pacing defaults for 1-minute data."""

    segment_duration: timedelta = timedelta(days=1)
    pacing_delay_seconds: float = 2.0
    max_segment_attempts: int = 3
    initial_retry_backoff_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.segment_duration <= timedelta(0):
            msg = "segment_duration must be positive"
            raise ValueError(msg)
        if self.pacing_delay_seconds < 0 or self.initial_retry_backoff_seconds < 0:
            msg = "pacing and retry delays must not be negative"
            raise ValueError(msg)
        if self.max_segment_attempts < 1:
            msg = "max_segment_attempts must be at least one"
            raise ValueError(msg)


@dataclass(frozen=True)
class DownloadSummary:
    """Combined results for all segments in one historical-download request."""

    request_id: str
    segments: tuple[HistoricalDownloadRequest, ...]
    ingestion_results: tuple[IngestionResult, ...]

    @property
    def rows_received(self) -> int:
        """Total source rows received across all completed segments."""
        return sum(result.write_summary.rows_received for result in self.ingestion_results)


@dataclass(frozen=True)
class HistoricalBackfillSummary:
    """Evidence from a bounded search for one contract's oldest API-available session."""

    contract_id: str
    session_name: str
    as_of: datetime
    latest_completed_trading_date: date
    earliest_available_timestamp: datetime | None
    latest_available_end: datetime | None
    available_session_count: int
    empty_session_dates: tuple[date, ...]
    rows_received: int
    rows_stored: int
    duplicates_discarded: int
    quality_report_paths: tuple[Path, ...]

    @property
    def stop_reason(self) -> str:
        """Describe the only terminal condition that silently excludes no source bars."""
        return "consecutive_empty_trading_sessions"


class SegmentedHistoricalDownloader:
    """Download historical bars sequentially, pace requests, and reconnect after failures."""

    def __init__(
        self,
        connection: IbConnectionManager,
        client: HistoricalBarsClient,
        ingestion: DataIngestionService,
        *,
        policy: DownloadPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._connection = connection
        self._client = client
        self._ingestion = ingestion
        self._policy = policy or DownloadPolicy()
        self._sleep = sleep

    def plan(self, request: HistoricalDownloadRequest) -> list[HistoricalDownloadRequest]:
        """Split a larger request into contiguous, non-overlapping day-sized segments."""
        segments: list[HistoricalDownloadRequest] = []
        current = request.start
        index = 0
        while current < request.end:
            next_boundary = min(current + self._policy.segment_duration, request.end)
            segments.append(request.segment(current, next_boundary, index))
            current = next_boundary
            index += 1
        return segments

    async def download(self, request: HistoricalDownloadRequest) -> DownloadSummary:
        """Fetch every segment, persist it, and always close the provider session."""
        segments = self.plan(request)
        results: list[IngestionResult] = []
        await self._connection.connect()
        try:
            for index, segment in enumerate(segments):
                bars = await self._fetch_with_retry(segment)
                session_name = "rth" if segment.use_rth else "eth"
                expected = expected_minute_timestamps(
                    segment.contract,
                    segment.start,
                    segment.end,
                    session_name=session_name,
                )
                results.append(
                    self._ingestion.ingest(
                        segment.contract.contract_id,
                        bars,
                        expected_timestamps=expected,
                        tick_size=segment.contract.tick_size,
                    )
                )
                if index < len(segments) - 1 and self._policy.pacing_delay_seconds:
                    await self._sleep(self._policy.pacing_delay_seconds)
        finally:
            await self._connection.disconnect()
        return DownloadSummary(
            request_id=request.request_id,
            segments=tuple(segments),
            ingestion_results=tuple(results),
        )

    async def backfill(
        self,
        contract: ContractSpec,
        *,
        as_of: datetime,
        use_rth: bool = False,
        max_consecutive_empty_sessions: int = 5,
    ) -> HistoricalBackfillSummary:
        """Walk complete exchange sessions backwards until the API history boundary.

        Empty responses are evidence of an unavailable historical interval, not malformed
        market data, so they deliberately do not create a false ``empty_batch`` quality report.
        Every non-empty response still uses the same paced ingest, quality, and storage path as
        a normal historical download.
        """
        if max_consecutive_empty_sessions < 1:
            msg = "max_consecutive_empty_sessions must be at least one"
            raise ValueError(msg)
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            msg = "backfill as_of must include a timezone"
            raise ValueError(msg)

        as_of_utc = as_of.astimezone(UTC)
        session_name = "rth" if use_rth else "eth"
        current_date = latest_completed_trading_date(
            contract,
            as_of_utc,
            session_name=session_name,
        )
        empty_dates: list[date] = []
        report_paths: list[Path] = []
        earliest: datetime | None = None
        latest_end: datetime | None = None
        available_sessions = 0
        rows_received = 0
        rows_stored = 0
        duplicates_discarded = 0
        consecutive_empty = 0

        await self._connection.connect()
        try:
            while consecutive_empty < max_consecutive_empty_sessions:
                bounds = session_bounds_for_trading_date(
                    contract,
                    current_date,
                    session_name=session_name,
                )
                if bounds is None:
                    current_date -= timedelta(days=1)
                    continue
                start, end = bounds
                segment = HistoricalDownloadRequest(
                    contract=contract,
                    start=start,
                    end=end,
                    use_rth=use_rth,
                )
                bars = await self._fetch_with_retry(segment)
                if not bars:
                    empty_dates.append(current_date)
                    consecutive_empty += 1
                else:
                    expected = expected_minute_timestamps(
                        contract,
                        start,
                        end,
                        session_name=session_name,
                    )
                    result = self._ingestion.ingest(
                        contract.contract_id,
                        bars,
                        expected_timestamps=expected,
                        tick_size=contract.tick_size,
                    )
                    available_sessions += 1
                    rows_received += result.write_summary.rows_received
                    rows_stored += result.write_summary.rows_stored
                    duplicates_discarded += result.write_summary.duplicates_discarded
                    report_paths.append(result.report_path)
                    first_timestamp = min(bar.timestamp for bar in bars)
                    last_end = max(bar.timestamp for bar in bars) + timedelta(minutes=1)
                    earliest = (
                        first_timestamp if earliest is None else min(earliest, first_timestamp)
                    )
                    latest_end = last_end if latest_end is None else max(latest_end, last_end)
                    consecutive_empty = 0
                if (
                    consecutive_empty < max_consecutive_empty_sessions
                    and self._policy.pacing_delay_seconds
                ):
                    await self._sleep(self._policy.pacing_delay_seconds)
                current_date -= timedelta(days=1)
        finally:
            await self._connection.disconnect()

        return HistoricalBackfillSummary(
            contract_id=contract.contract_id,
            session_name=session_name,
            as_of=as_of_utc,
            latest_completed_trading_date=latest_completed_trading_date(
                contract,
                as_of_utc,
                session_name=session_name,
            ),
            earliest_available_timestamp=earliest,
            latest_available_end=latest_end,
            available_session_count=available_sessions,
            empty_session_dates=tuple(empty_dates),
            rows_received=rows_received,
            rows_stored=rows_stored,
            duplicates_discarded=duplicates_discarded,
            quality_report_paths=tuple(report_paths),
        )

    async def _fetch_with_retry(self, request: HistoricalDownloadRequest) -> list[CanonicalBar]:
        for attempt in range(self._policy.max_segment_attempts):
            try:
                return await self._client.fetch_bars(request)
            except (ConnectionError, OSError, TimeoutError) as exc:
                if attempt == self._policy.max_segment_attempts - 1:
                    msg = (
                        "historical request failed after "
                        f"{self._policy.max_segment_attempts} attempts"
                    )
                    raise RuntimeError(msg) from exc
                await self._connection.reconnect()
                await self._sleep(self._policy.initial_retry_backoff_seconds * (2**attempt))
            except Exception as exc:
                msg = (
                    "historical request failed without retry because it is not a transport failure"
                )
                raise RuntimeError(msg) from exc
        msg = "unreachable retry state"
        raise RuntimeError(msg)
