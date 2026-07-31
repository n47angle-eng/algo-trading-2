"""Native IB daily-bar download path (WO-003b / 3b-1).

This module is deliberately separate from the 1-minute downloader so the existing
``download`` / ``backfill`` orchestration is never rewritten. Daily bars land in a
second canonical root (``data/market-daily/``) with ``source=ib_native_daily``.

Known limitations (channel [046]; info-level only — under-tag, never false-positive):
1. ``tag_thin_daily`` needs a 90-bar trailing window, so the earliest 90 daily bars
   of a series are never tagged (often the thinnest settlement period).
2. First-run year-segment backfill has no cross-segment prior history, so each
   segment's first 90 bars are systematically under-tagged until a re-download.
3. A full-series re-tag pass remains backlog (post-3b), not part of 3b-1 ingest.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from futures_research.data.contracts import ContractSpec
from futures_research.data.download import IbConnectionManager
from futures_research.data.models import CanonicalBar, QualityIssue, QualityReport
from futures_research.data.quality import DataQualityChecker
from futures_research.data.sessions import (
    latest_completed_trading_date,
    session_bounds_for_trading_date,
)
from futures_research.data.storage import CanonicalStore, WriteSummary

DAILY_SOURCE = "ib_native_daily"
THIN_DAILY_LOOKBACK_BARS = 90
THIN_DAILY_VOLUME_FRACTION = 0.05

Sleep = Callable[[float], Awaitable[None]]


class DailyHistoricalDownloadRequest(BaseModel):
    """One inclusive-start, exclusive-end native daily-bar request."""

    model_config = ConfigDict(frozen=True)

    contract: ContractSpec
    start: datetime
    end: datetime
    use_rth: bool = False
    session_name: str = "eth"
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    @field_validator("start", "end", mode="after")
    @classmethod
    def normalize_to_utc(cls, value: datetime) -> datetime:
        """Keep request boundaries aligned with the canonical UTC policy."""
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "download boundaries must include a timezone"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def check_time_range(self) -> DailyHistoricalDownloadRequest:
        """Reject empty or reversed historical windows before any network request."""
        if self.start >= self.end:
            msg = "download start must be earlier than end"
            raise ValueError(msg)
        if self.session_name not in self.contract.sessions:
            msg = f"unknown session '{self.session_name}' for {self.contract.contract_id}"
            raise ValueError(msg)
        return self

    def segment(self, start: datetime, end: datetime, index: int) -> DailyHistoricalDownloadRequest:
        """Create a traceable child request for one paced historical segment."""
        return self.model_copy(
            update={"start": start, "end": end, "request_id": f"{self.request_id}-{index:04d}"}
        )


class DailyHistoricalBarsClient(Protocol):
    """Minimum behavior required from a native daily historical-data provider adapter."""

    async def connect(self) -> None:
        """Open the provider session."""

    async def disconnect(self) -> None:
        """Close the provider session."""

    async def fetch_daily_bars(self, request: DailyHistoricalDownloadRequest) -> list[CanonicalBar]:
        """Fetch one already-bounded native daily request segment."""


@dataclass(frozen=True)
class DailyDownloadPolicy:
    """Conservative IB request sizing and pacing defaults for native daily data."""

    segment_duration: timedelta = timedelta(days=365)
    pacing_delay_seconds: float = 2.0
    max_segment_attempts: int = 3
    initial_retry_backoff_seconds: float = 2.0
    max_consecutive_empty_segments: int = 3

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
        if self.max_consecutive_empty_segments < 1:
            msg = "max_consecutive_empty_segments must be at least one"
            raise ValueError(msg)


@dataclass(frozen=True)
class DailyIngestionResult:
    """Result of one daily-bar ingestion batch (reasonableness + thin_daily only)."""

    quality_report: QualityReport
    report_path: Path
    write_summary: WriteSummary


@dataclass(frozen=True)
class DailyDownloadSummary:
    """Combined results for all segments in one native daily download request."""

    request_id: str
    segments: tuple[DailyHistoricalDownloadRequest, ...]
    ingestion_results: tuple[DailyIngestionResult, ...]

    @property
    def rows_received(self) -> int:
        """Total source rows received across all completed segments."""
        return sum(result.write_summary.rows_received for result in self.ingestion_results)

    @property
    def rows_stored(self) -> int:
        """Total new rows stored across all completed segments."""
        return sum(result.write_summary.rows_stored for result in self.ingestion_results)


@dataclass(frozen=True)
class DailyBackfillSummary:
    """Evidence from a bounded search for one contract's oldest API-available daily bars."""

    contract_id: str
    session_name: str
    as_of: datetime
    latest_completed_trading_date: date
    earliest_available_timestamp: datetime | None
    latest_available_end: datetime | None
    available_segment_count: int
    empty_segment_starts: tuple[datetime, ...]
    rows_received: int
    rows_stored: int
    duplicates_discarded: int
    unique_bar_count: int
    thin_daily_count: int
    quality_report_paths: tuple[Path, ...]
    stop_reason: str = "consecutive_empty_daily_segments"


class DailyDataIngestionService:
    """Persist native daily bars with 3b-1 quality policy (reasonableness + thin_daily only)."""

    def __init__(
        self,
        store: CanonicalStore,
        reports_root: Path,
        quality_checker: DataQualityChecker | None = None,
    ) -> None:
        self._store = store
        self._reports_root = reports_root
        self._quality_checker = quality_checker or DataQualityChecker()

    def ingest(
        self,
        contract_id: str,
        bars: Sequence[CanonicalBar],
        *,
        tick_size: float | None = None,
        prior_bars_for_thin: Sequence[CanonicalBar] = (),
    ) -> DailyIngestionResult:
        """Ingest one native-daily batch without completeness or anomaly gates."""
        wrong_contracts = {bar.contract_id for bar in bars if bar.contract_id != contract_id}
        if wrong_contracts:
            msg = f"batch contains bars for another contract: {sorted(wrong_contracts)}"
            raise ValueError(msg)
        ordered = sorted(bars, key=lambda bar: bar.timestamp)
        issues = list(
            self._quality_checker.check_reasonableness_only(
                ordered,
                tick_size=tick_size,
            )
        )
        history = sorted(
            [*prior_bars_for_thin, *ordered],
            key=lambda bar: bar.timestamp,
        )
        issues.extend(tag_thin_daily(history, only_timestamps={bar.timestamp for bar in ordered}))
        report = QualityReport(
            contract_id=contract_id,
            total_bars=len(ordered),
            checks={
                "completeness": "skipped_3b1",
                "reasonableness": "completed",
                "anomaly": "skipped_3b1",
                "consistency": "skipped_3b1",
                "thin_daily": "completed",
            },
            issues=issues,
        )
        write_summary = self._store.append(ordered)
        report_path = self._write_report(report)
        return DailyIngestionResult(
            quality_report=report,
            report_path=report_path,
            write_summary=write_summary,
        )

    def _write_report(self, report: QualityReport) -> Path:
        directory = self._reports_root / _safe_component(report.contract_id)
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{report.checked_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        destination = directory / filename
        destination.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return destination


class NativeDailyDownloader:
    """Download native IB daily bars sequentially, pace requests, and reconnect after failures."""

    def __init__(
        self,
        connection: IbConnectionManager,
        client: DailyHistoricalBarsClient,
        ingestion: DailyDataIngestionService,
        store: CanonicalStore,
        *,
        policy: DailyDownloadPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._connection = connection
        self._client = client
        self._ingestion = ingestion
        self._store = store
        self._policy = policy or DailyDownloadPolicy()
        self._sleep = sleep

    def plan(self, request: DailyHistoricalDownloadRequest) -> list[DailyHistoricalDownloadRequest]:
        """Split a larger request into contiguous, non-overlapping year-sized segments."""
        segments: list[DailyHistoricalDownloadRequest] = []
        current = request.start
        index = 0
        while current < request.end:
            next_boundary = min(current + self._policy.segment_duration, request.end)
            segments.append(request.segment(current, next_boundary, index))
            current = next_boundary
            index += 1
        return segments

    async def download(self, request: DailyHistoricalDownloadRequest) -> DailyDownloadSummary:
        """Fetch every segment, persist it, and always close the provider session."""
        segments = self.plan(request)
        results: list[DailyIngestionResult] = []
        await self._connection.connect()
        try:
            for index, segment in enumerate(segments):
                bars = await self._fetch_with_retry(segment)
                prior = self._store.read(
                    segment.contract.contract_id,
                    start=segment.start - timedelta(days=THIN_DAILY_LOOKBACK_BARS * 2),
                    end=segment.start,
                )
                results.append(
                    self._ingestion.ingest(
                        segment.contract.contract_id,
                        bars,
                        tick_size=segment.contract.tick_size,
                        prior_bars_for_thin=prior,
                    )
                )
                if index < len(segments) - 1 and self._policy.pacing_delay_seconds:
                    await self._sleep(self._policy.pacing_delay_seconds)
        finally:
            await self._connection.disconnect()
        return DailyDownloadSummary(
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
        session_name: str | None = None,
        max_consecutive_empty_segments: int | None = None,
    ) -> DailyBackfillSummary:
        """Walk year-sized windows backwards until the API history boundary.

        Empty responses are evidence of an unavailable historical interval, not malformed
        market data, so they deliberately do not create a false ``empty_batch`` quality report.
        """
        empty_limit = max_consecutive_empty_segments or self._policy.max_consecutive_empty_segments
        if empty_limit < 1:
            msg = "max_consecutive_empty_segments must be at least one"
            raise ValueError(msg)
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            msg = "backfill as_of must include a timezone"
            raise ValueError(msg)

        as_of_utc = as_of.astimezone(UTC)
        resolved_session = session_name or ("rth" if use_rth else "eth")
        latest_date = latest_completed_trading_date(
            contract,
            as_of_utc,
            session_name=resolved_session,
        )
        latest_bounds = session_bounds_for_trading_date(
            contract,
            latest_date,
            session_name=resolved_session,
        )
        if latest_bounds is None:
            msg = f"no completed session for {contract.contract_id} at {as_of_utc.isoformat()}"
            raise RuntimeError(msg)
        current_end = latest_bounds[1]
        empty_starts: list[datetime] = []
        report_paths: list[Path] = []
        earliest: datetime | None = None
        latest_end: datetime | None = None
        available_segments = 0
        rows_received = 0
        rows_stored = 0
        duplicates_discarded = 0
        thin_daily_count = 0
        consecutive_empty = 0

        await self._connection.connect()
        try:
            while consecutive_empty < empty_limit:
                current_start = current_end - self._policy.segment_duration
                segment = DailyHistoricalDownloadRequest(
                    contract=contract,
                    start=current_start,
                    end=current_end,
                    use_rth=use_rth,
                    session_name=resolved_session,
                )
                bars = await self._fetch_with_retry(segment)
                if not bars:
                    empty_starts.append(current_start)
                    consecutive_empty += 1
                else:
                    prior = self._store.read(
                        contract.contract_id,
                        start=current_start - timedelta(days=THIN_DAILY_LOOKBACK_BARS * 2),
                        end=current_start,
                    )
                    result = self._ingestion.ingest(
                        contract.contract_id,
                        bars,
                        tick_size=contract.tick_size,
                        prior_bars_for_thin=prior,
                    )
                    available_segments += 1
                    rows_received += result.write_summary.rows_received
                    rows_stored += result.write_summary.rows_stored
                    duplicates_discarded += result.write_summary.duplicates_discarded
                    thin_daily_count += sum(
                        1 for issue in result.quality_report.issues if issue.code == "thin_daily"
                    )
                    report_paths.append(result.report_path)
                    first_timestamp = min(bar.timestamp for bar in bars)
                    last_end = max(bar.timestamp for bar in bars) + timedelta(days=1)
                    earliest = (
                        first_timestamp if earliest is None else min(earliest, first_timestamp)
                    )
                    latest_end = last_end if latest_end is None else max(latest_end, last_end)
                    consecutive_empty = 0
                if consecutive_empty < empty_limit and self._policy.pacing_delay_seconds:
                    await self._sleep(self._policy.pacing_delay_seconds)
                current_end = current_start
        finally:
            await self._connection.disconnect()

        stored = self._store.read(contract.contract_id)
        return DailyBackfillSummary(
            contract_id=contract.contract_id,
            session_name=resolved_session,
            as_of=as_of_utc,
            latest_completed_trading_date=latest_date,
            earliest_available_timestamp=earliest,
            latest_available_end=latest_end,
            available_segment_count=available_segments,
            empty_segment_starts=tuple(empty_starts),
            rows_received=rows_received,
            rows_stored=rows_stored,
            duplicates_discarded=duplicates_discarded,
            unique_bar_count=len(stored),
            thin_daily_count=thin_daily_count,
            quality_report_paths=tuple(report_paths),
        )

    async def _fetch_with_retry(
        self, request: DailyHistoricalDownloadRequest
    ) -> list[CanonicalBar]:
        for attempt in range(self._policy.max_segment_attempts):
            try:
                return await self._client.fetch_daily_bars(request)
            except (ConnectionError, OSError, TimeoutError) as exc:
                if attempt == self._policy.max_segment_attempts - 1:
                    msg = (
                        "daily historical request failed after "
                        f"{self._policy.max_segment_attempts} attempts"
                    )
                    raise RuntimeError(msg) from exc
                await self._connection.reconnect()
                await self._sleep(self._policy.initial_retry_backoff_seconds * (2**attempt))
            except Exception as exc:
                msg = (
                    "daily historical request failed without retry because it is not a "
                    "transport failure"
                )
                raise RuntimeError(msg) from exc
        msg = "unreachable retry state"
        raise RuntimeError(msg)


def tag_thin_daily(
    bars: Sequence[CanonicalBar],
    *,
    only_timestamps: set[datetime] | None = None,
    lookback: int = THIN_DAILY_LOOKBACK_BARS,
    fraction: float = THIN_DAILY_VOLUME_FRACTION,
) -> list[QualityIssue]:
    """Tag low-liquidity settlement days without blocking ingestion.

    A day is ``thin_daily`` when its volume is below ``fraction`` of the trailing
    ``lookback``-bar volume median (initial values from channel [044]: 90 bars, 5%).
    Tags are severity ``info`` and never reject storage.
    """
    if lookback < 1:
        msg = "lookback must be at least one"
        raise ValueError(msg)
    if not math.isfinite(fraction) or fraction <= 0:
        msg = "fraction must be a positive finite value"
        raise ValueError(msg)

    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    issues: list[QualityIssue] = []
    for index, bar in enumerate(ordered):
        if only_timestamps is not None and bar.timestamp not in only_timestamps:
            continue
        if index < lookback:
            continue
        window = ordered[index - lookback : index]
        window_median = median(item.volume for item in window)
        # Zero-volume settlement days are thin even when the trailing median is also zero
        # (common early in an individual-contract series before it is liquid).
        is_thin = bar.volume <= 0 or (window_median > 0 and bar.volume < window_median * fraction)
        if not is_thin:
            continue
        threshold = window_median * fraction if window_median > 0 else 0.0
        issues.append(
            QualityIssue(
                category="reasonableness",
                severity="info",
                code="thin_daily",
                message=(
                    "Daily volume is zero or below the configured fraction of the trailing "
                    "90-bar volume median (low-liquidity settlement day)."
                ),
                timestamp=bar.timestamp,
                details={
                    "volume": bar.volume,
                    "median_volume": window_median,
                    "threshold": threshold,
                    "lookback_bars": lookback,
                    "fraction": fraction,
                },
            )
        )
    return issues


def map_daily_event_to_session_start(
    contract: ContractSpec,
    event: datetime,
    *,
    session_name: str = "eth",
) -> datetime:
    """Map a Nautilus 1-DAY bar ``ts_event`` onto the configured session open (bar start).

    Channel [044] Q2: canonical daily ``ts_event`` is session open in UTC.

    Vendor evidence (NautilusTrader 1.230 ``market_data.py``):
    - IB day bars arrive as ``bar.date`` in ``YYYYMMDD`` (aggregation 14).
    - ``_convert_ib_bar_date_to_unix_nanos`` parses that as **midnight UTC** of the label.
    - ``_ib_bar_to_ts_event`` uses that midnight for non-week/month bars (bar-period start in
      Nautilus' calendar sense, **not** the exchange session open).
    - ``_ib_bar_to_ts_init`` for day bars is ``midnight + 1 day - 1ns``.

    The IB ``YYYYMMDD`` for US futures daily bars is the **exchange trading date** (session
    end date). It must **not** be re-derived via America/Chicago local date of that UTC
    midnight (which is the prior evening in CT and caused a systematic −1 trading-day shift
    on weekdays — channel [048] forensic). Correct path: ``trading_date = event.date()`` in
    UTC for midnight-labelled day bars → ``session_bounds_for_trading_date(...).start``.
    """
    if event.tzinfo is None or event.utcoffset() is None:
        msg = "daily event timestamps must include a timezone"
        raise ValueError(msg)
    event_utc = event.astimezone(UTC)
    trading_date = ib_daily_label_to_trading_date(event_utc)
    return trading_date_to_session_start(
        contract,
        trading_date,
        session_name=session_name,
    )


def ib_daily_label_to_trading_date(event: datetime) -> date:
    """Extract the IB ``YYYYMMDD`` trading date from a Nautilus day-bar ``ts_event``.

    Day bars are midnight UTC of the IB label. Non-midnight inputs (tests / defensive) still
    use the UTC calendar date of the instant, which matches the YYYYMMDD label for true day bars.
    """
    if event.tzinfo is None or event.utcoffset() is None:
        msg = "daily event timestamps must include a timezone"
        raise ValueError(msg)
    return event.astimezone(UTC).date()


def trading_date_to_session_start(
    contract: ContractSpec,
    trading_date: date,
    *,
    session_name: str = "eth",
) -> datetime:
    """Map an exchange trading date to the configured session open (canonical bar start)."""
    if session_name not in contract.sessions:
        msg = f"unknown session '{session_name}' for {contract.contract_id}"
        raise ValueError(msg)
    bounds = session_bounds_for_trading_date(
        contract,
        trading_date,
        session_name=session_name,
    )
    if bounds is not None:
        return bounds[0]

    # Rare weekend/holiday label: walk forward to the next configured weekday session.
    cursor = trading_date
    for _ in range(7):
        bounds = session_bounds_for_trading_date(
            contract,
            cursor,
            session_name=session_name,
        )
        if bounds is not None:
            return bounds[0]
        cursor += timedelta(days=1)

    msg = (
        f"unable to map trading date {trading_date.isoformat()} to a session start for "
        f"{contract.contract_id}/{session_name}"
    )
    raise ValueError(msg)


def describe_daily_timestamp_chain(
    contract: ContractSpec,
    *,
    nautilus_ts_event: datetime,
    session_name: str = "eth",
    ib_date_label: str | None = None,
) -> dict[str, str]:
    """Build the forensic chain IB label → Nautilus ts_event → mapped session start.

    When ``ib_date_label`` is omitted it is recovered from Nautilus midnight-UTC day-bar
    convention (``YYYYMMDD``), matching ``_convert_ib_bar_date_to_unix_nanos``.
    """
    event_utc = nautilus_ts_event.astimezone(UTC)
    label = ib_date_label or event_utc.strftime("%Y%m%d")
    trading_date = ib_daily_label_to_trading_date(event_utc)
    session_start = trading_date_to_session_start(
        contract,
        trading_date,
        session_name=session_name,
    )
    return {
        "ib_date_label": label,
        "nautilus_ts_event": event_utc.isoformat(),
        "trading_date": trading_date.isoformat(),
        "mapped_session_start": session_start.isoformat(),
        "session_name": session_name,
    }


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)
