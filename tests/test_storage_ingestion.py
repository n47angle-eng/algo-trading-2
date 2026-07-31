"""Fixture-based tests for canonical Parquet persistence and audit reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from futures_research.data import storage as storage_module
from futures_research.data.ingestion import DataIngestionService
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CANONICAL_BAR_SCHEMA, CanonicalStore


def load_fixture_bars() -> list[CanonicalBar]:
    """Load a deterministic replay fixture instead of needing an IB session."""
    fixture_path = Path(__file__).parent / "fixtures" / "nq_1m.json"
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    return [
        CanonicalBar(
            **item,
            contract_id="NQ-202609-CME",
            source="fixture",
            source_request_id="fixture-nq-001",
        )
        for item in raw
    ]


def test_fixture_ingestion_persists_schema_and_quality_report(tmp_path: Path) -> None:
    """A replay fixture must prove storage, report output, and Parquet metadata without IB."""
    bars = load_fixture_bars()
    store = CanonicalStore(tmp_path / "market")
    service = DataIngestionService(store, tmp_path / "reports")
    reference_bar = CanonicalBar(
        timestamp=datetime(2026, 7, 20, 14, 30, tzinfo=UTC),
        open=20000.0,
        high=20005.0,
        low=19999.5,
        close=20004.0,
        volume=600,
        contract_id="NQ-202609-CME",
        source="fixture-native-5m",
    )

    result = service.ingest(
        "NQ-202609-CME",
        bars,
        expected_timestamps=[bar.timestamp for bar in bars],
        reference_bars=[reference_bar],
    )

    assert result.write_summary.rows_received == 5
    assert result.write_summary.rows_stored == 5
    assert result.write_summary.duplicates_discarded == 0
    assert result.quality_report.issues == []
    assert result.report_path.exists()
    report_json = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report_json["contract_id"] == "NQ-202609-CME"

    partition = result.write_summary.partitions_written[0]
    metadata = pq.ParquetFile(partition).schema_arrow.metadata
    assert metadata == CANONICAL_BAR_SCHEMA.metadata
    assert [bar.close for bar in store.read("NQ-202609-CME")] == [
        20000.5,
        20001.5,
        20002.0,
        20003.5,
        20004.0,
    ]


def test_existing_bar_wins_when_a_fixture_is_replayed(tmp_path: Path) -> None:
    """A rerun must not silently rewrite a previously persisted minute."""
    bars = load_fixture_bars()
    store = CanonicalStore(tmp_path / "market")
    service = DataIngestionService(store, tmp_path / "reports")
    service.ingest("NQ-202609-CME", bars)
    altered_duplicate = bars[0].model_copy(update={"close": 29999.0, "source_request_id": "replay"})

    result = service.ingest("NQ-202609-CME", [altered_duplicate])

    assert result.write_summary.rows_stored == 0
    assert result.write_summary.duplicates_discarded == 1
    assert store.read("NQ-202609-CME")[0].close == 20000.5


def test_rows_stored_counts_actual_incoming_appends_after_existing_deduplication(
    tmp_path: Path,
) -> None:
    """Repairing a malformed existing partition must not inflate the incoming-row count."""
    bars = load_fixture_bars()
    store = CanonicalStore(tmp_path / "market")
    store.append(bars)
    partition = store.partition_files("NQ-202609-CME")[0]
    existing_records = CanonicalStore._read_records(partition)
    CanonicalStore._write_records(partition, [existing_records[0], *existing_records])
    new_bar = bars[-1].model_copy(update={"timestamp": datetime(2026, 7, 20, 14, 35, tzinfo=UTC)})

    result = store.append([new_bar])

    assert result.rows_stored == 1
    assert result.duplicates_discarded == 0
    assert len(store.read("NQ-202609-CME")) == 6


def test_read_prunes_to_intersecting_month_partitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bounded read must not open the adjacent monthly partitions."""
    store = CanonicalStore(tmp_path / "market")
    contract_id = "NQ-202609-CME"
    bars = [
        _bar_at(datetime(2026, 1, 31, 23, 59, tzinfo=UTC), contract_id),
        _bar_at(datetime(2026, 2, 1, 0, 0, tzinfo=UTC), contract_id),
        _bar_at(datetime(2026, 2, 15, 12, 0, tzinfo=UTC), contract_id),
        _bar_at(datetime(2026, 3, 1, 0, 0, tzinfo=UTC), contract_id),
    ]
    store.append(bars)
    start = datetime(2026, 2, 1, tzinfo=UTC)
    end = datetime(2026, 3, 1, tzinfo=UTC)
    assert store._partitions_for_read(contract_id, start, None) == [
        store._partition_path(contract_id, 2026, 2),
        store._partition_path(contract_id, 2026, 3),
    ]
    assert store._partitions_for_read(contract_id, None, start) == [
        store._partition_path(contract_id, 2026, 1)
    ]
    opened_paths: list[Path] = []
    read_records = CanonicalStore._read_records

    def record_opened_partition(
        path: Path,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, object]]:
        opened_paths.append(path)
        return read_records(path, start=start, end=end)

    monkeypatch.setattr(store, "_read_records", record_opened_partition)

    result = store.read(contract_id, start=start, end=end)

    assert [bar.timestamp for bar in result] == [bar.timestamp for bar in bars[1:3]]
    assert opened_paths == [store._partition_path(contract_id, 2026, 2)]


def test_read_passes_half_open_utc_bounds_to_arrow_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Time filtering belongs in Parquet/Arrow before records become Python objects."""
    store = CanonicalStore(tmp_path / "market")
    contract_id = "NQ-202609-CME"
    bars = [
        _bar_at(datetime(2026, 2, 1, 0, minute, tzinfo=UTC), contract_id) for minute in range(3)
    ]
    store.append(bars)
    captured_filters: list[object] = []
    captured_partitioning: list[object] = []
    read_table = storage_module.pq.read_table

    def capture_arrow_read(*args: object, **kwargs: object) -> object:
        captured_filters.append(kwargs["filters"])
        captured_partitioning.append(kwargs["partitioning"])
        return read_table(*args, **kwargs)

    monkeypatch.setattr(storage_module.pq, "read_table", capture_arrow_read)
    start = datetime(2026, 2, 1, 0, 1, tzinfo=UTC)
    end = datetime(2026, 2, 1, 0, 2, tzinfo=UTC)

    result = store.read(contract_id, start=start, end=end)

    assert [bar.timestamp for bar in result] == [start]
    assert captured_filters == [[("ts_event", ">=", start), ("ts_event", "<", end)]]
    assert captured_partitioning == [None]


def _bar_at(timestamp: datetime, contract_id: str) -> CanonicalBar:
    """Create a structurally valid bar for partition-read tests."""
    return CanonicalBar(
        timestamp=timestamp,
        open=20000.0,
        high=20001.0,
        low=19999.0,
        close=20000.5,
        volume=10,
        contract_id=contract_id,
        source="fixture",
    )
