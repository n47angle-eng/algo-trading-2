"""Atomic, vendor-neutral Parquet storage for canonical one-minute bars."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from futures_research.data.models import CanonicalBar

SCHEMA_VERSION = 1
CANONICAL_BAR_SCHEMA = pa.schema(
    [
        pa.field("ts_event", pa.timestamp("ns", tz="UTC"), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
        pa.field("contract_id", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("source_request_id", pa.string(), nullable=True),
        pa.field("ingested_at", pa.timestamp("ns", tz="UTC"), nullable=False),
    ],
    metadata={
        b"canonical_schema_version": str(SCHEMA_VERSION).encode(),
        b"canonical_timezone": b"UTC",
        b"canonical_interval": b"1m",
        b"canonical_owner": b"futures_research.data",
    },
)


@dataclass(frozen=True)
class WriteSummary:
    """Storage result suitable for a user-facing quality or run report."""

    rows_received: int
    rows_stored: int
    duplicates_discarded: int
    partitions_written: tuple[Path, ...]


class CanonicalStore:
    """Persist individual-contract bars as atomically replaced monthly Parquet partitions."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """Return the root directory containing canonical market-data partitions."""
        return self._root

    def append(self, bars: Iterable[CanonicalBar]) -> WriteSummary:
        """Persist bars while preserving an existing bar for any duplicate minute key."""
        materialized = list(bars)
        by_partition: dict[tuple[str, int, int], list[CanonicalBar]] = defaultdict(list)
        for bar in materialized:
            timestamp = bar.timestamp.astimezone(UTC)
            by_partition[(bar.contract_id, timestamp.year, timestamp.month)].append(bar)

        stored_rows = 0
        discarded_duplicates = 0
        written_paths: list[Path] = []
        for (contract_id, year, month), partition_bars in by_partition.items():
            destination = self._partition_path(contract_id, year, month)
            existing = self._read_records(destination) if destination.exists() else []
            merged, duplicates, appended = self._merge_existing_first(existing, partition_bars)
            self._write_records(destination, merged)
            stored_rows += appended
            discarded_duplicates += duplicates
            written_paths.append(destination)

        return WriteSummary(
            rows_received=len(materialized),
            rows_stored=stored_rows,
            duplicates_discarded=discarded_duplicates,
            partitions_written=tuple(written_paths),
        )

    def read(
        self,
        contract_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CanonicalBar]:
        """Read bars in an inclusive-start, exclusive-end UTC time range."""
        normalized_start = self._normalize_bound(start)
        normalized_end = self._normalize_bound(end)
        if (
            normalized_start is not None
            and normalized_end is not None
            and normalized_start >= normalized_end
        ):
            msg = "start must be earlier than end"
            raise ValueError(msg)

        rows: list[dict[str, object]] = []
        for path in self._partitions_for_read(contract_id, normalized_start, normalized_end):
            rows.extend(self._read_records(path, start=normalized_start, end=normalized_end))

        bars = [self._record_to_bar(record) for record in rows]
        return sorted(bars, key=lambda bar: bar.timestamp)

    def partition_files(self, contract_id: str) -> list[Path]:
        """Return all monthly partitions for the supplied individual contract."""
        contract_directory = self._root / f"contract_id={self._safe_component(contract_id)}"
        return sorted(contract_directory.glob("year=*/month=*/bars.parquet"))

    def _partitions_for_read(
        self,
        contract_id: str,
        start: datetime | None,
        end: datetime | None,
    ) -> list[Path]:
        """Return only partitions whose UTC calendar months intersect the requested range."""
        if start is not None and end is not None:
            paths: list[Path] = []
            for year, month in self._months_intersecting_range(start, end):
                path = self._partition_path(contract_id, year, month)
                if path.exists():
                    paths.append(path)
            return paths

        return [
            path
            for path in self.partition_files(contract_id)
            if self._partition_intersects_range(path, start, end)
        ]

    @staticmethod
    def _months_intersecting_range(start: datetime, end: datetime) -> Iterable[tuple[int, int]]:
        """Yield every UTC month with at least one instant in the half-open range."""
        cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
        while cursor < end:
            yield cursor.year, cursor.month
            cursor = CanonicalStore._next_month(cursor)

    @staticmethod
    def _partition_intersects_range(
        path: Path,
        start: datetime | None,
        end: datetime | None,
    ) -> bool:
        """Determine whether a canonical monthly partition intersects ``[start, end)``."""
        month_directory = path.parent.name
        year_directory = path.parent.parent.name
        year = int(year_directory.removeprefix("year="))
        month = int(month_directory.removeprefix("month="))
        month_start = datetime(year, month, 1, tzinfo=UTC)
        month_end = CanonicalStore._next_month(month_start)
        return (end is None or month_start < end) and (start is None or month_end > start)

    @staticmethod
    def _next_month(value: datetime) -> datetime:
        """Return the UTC first instant of the month after ``value``."""
        if value.month == 12:
            return datetime(value.year + 1, 1, 1, tzinfo=UTC)
        return datetime(value.year, value.month + 1, 1, tzinfo=UTC)

    def _partition_path(self, contract_id: str, year: int, month: int) -> Path:
        return (
            self._root
            / f"contract_id={self._safe_component(contract_id)}"
            / f"year={year:04d}"
            / f"month={month:02d}"
            / "bars.parquet"
        )

    @staticmethod
    def _safe_component(value: str) -> str:
        """Keep a contract ID from escaping its partition directory."""
        return re.sub(r"[^A-Za-z0-9._-]", "_", value)

    @staticmethod
    def _normalize_bound(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "read bounds must include a timezone"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @staticmethod
    def _merge_existing_first(
        existing_records: list[dict[str, object]], incoming_bars: list[CanonicalBar]
    ) -> tuple[list[dict[str, object]], int, int]:
        seen_timestamps: set[datetime] = set()
        merged: list[dict[str, object]] = []
        duplicates = 0
        for record in existing_records:
            timestamp = CanonicalStore._record_timestamp(record)
            if timestamp not in seen_timestamps:
                seen_timestamps.add(timestamp)
                merged.append(record)
        for bar in incoming_bars:
            if bar.timestamp in seen_timestamps:
                duplicates += 1
                continue
            seen_timestamps.add(bar.timestamp)
            merged.append(bar.to_record())
        return (
            sorted(merged, key=CanonicalStore._record_timestamp),
            duplicates,
            len(incoming_bars) - duplicates,
        )

    @staticmethod
    def _record_timestamp(record: dict[str, object]) -> datetime:
        value = record["ts_event"]
        if not isinstance(value, datetime):
            msg = "canonical Parquet row contains a non-datetime ts_event"
            raise TypeError(msg)
        return value.astimezone(UTC)

    @staticmethod
    def _record_to_bar(record: dict[str, object]) -> CanonicalBar:
        return CanonicalBar.model_validate(
            {
                "timestamp": record["ts_event"],
                "open": record["open"],
                "high": record["high"],
                "low": record["low"],
                "close": record["close"],
                "volume": record["volume"],
                "contract_id": record["contract_id"],
                "source": record["source"],
                "source_request_id": record["source_request_id"],
                "ingested_at": record["ingested_at"],
            }
        )

    @staticmethod
    def _read_records(
        path: Path,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, object]]:
        """Read a partition with a native Arrow timestamp predicate before materialization."""
        filters: list[tuple[str, str, datetime]] = []
        if start is not None:
            filters.append(("ts_event", ">=", start))
        if end is not None:
            filters.append(("ts_event", "<", end))
        # ``partitioning=None`` prevents Hive directory names from being merged with the canonical
        # ``contract_id`` column. The filter stays inside Arrow/Parquet rather than Python rows.
        table = pq.read_table(  # type: ignore[no-untyped-call]
            path,
            partitioning=None,
            filters=filters or None,
        )
        return [dict(record) for record in table.to_pylist()]

    @staticmethod
    def _write_records(destination: Path, records: list[dict[str, object]]) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(records, schema=CANONICAL_BAR_SCHEMA)
        temporary_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            # PyArrow's Parquet writer has no inline type signature as of the pinned release.
            pq.write_table(table, temporary_path, compression="zstd", version="2.6")  # type: ignore[no-untyped-call]
            temporary_path.replace(destination)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
