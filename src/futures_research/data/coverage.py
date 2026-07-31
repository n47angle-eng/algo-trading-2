"""Exact P3 trading-date and native-daily coverage projections."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any, Literal, Protocol

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from futures_research.data.contracts import ContractSpec
from futures_research.data.models import CanonicalBar, QualityReport
from futures_research.data.quality import DataQualityChecker, QualitySettings
from futures_research.data.sessions import (
    SessionDateMapper,
    expected_minute_timestamps,
    session_bounds_for_trading_date,
)
from futures_research.data.storage import CANONICAL_BAR_SCHEMA, CanonicalStore

TradingCoverageErrorCode = Literal[
    "minute_data_unreadable",
    "minute_timestamp_unmapped",
    "session_definition_unavailable",
    "coverage_computation_failed",
]
NativeDailyCoverageErrorCode = Literal[
    "minute_coverage_dependency_unavailable",
    "native_daily_unreadable",
    "native_daily_timestamp_unmapped",
    "coverage_computation_failed",
]

_BLOCKING_SEVERITIES = frozenset({"error", "warning"})


class MinuteQualityChecker(Protocol):
    """Structural interface used to test severity handling without changing production truth."""

    def check(
        self,
        contract_id: str,
        bars: Sequence[CanonicalBar],
        *,
        expected_timestamps: Iterable[datetime] | None = None,
        reference_bars: Sequence[CanonicalBar] | None = None,
        reference_interval: timedelta = timedelta(minutes=5),
        tick_size: float | None = None,
    ) -> QualityReport: ...


@dataclass(frozen=True, slots=True)
class TradingDayCoverageProjection:
    """Serialized trading-day facts plus the exact candidate set needed by daily coverage."""

    document: dict[str, Any]
    candidate_dates: tuple[date, ...]
    observed_dates: tuple[date, ...]


@dataclass(frozen=True, slots=True)
class CatalogCoverageMetadata:
    """Legacy catalog facts obtained without materializing canonical bars."""

    partition_count: int
    bar_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class ContractCoverageSnapshot:
    """One full minute scan feeding both legacy metadata and exact P3 projections."""

    partition_count: int
    bar_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    trading_day_coverage: dict[str, Any]
    native_daily_coverage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ContractCoverageSource:
    """One physical read of a contract's minute and native-Daily stores.

    P4 can project the same immutable source through more than one strategy
    session.  Keeping the physical read separate from the projection prevents a
    second Parquet scan merely because an ETH and an RTH strategy were selected
    together.
    """

    partition_count: int
    bar_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    minute_table: pa.Table | None
    minute_error: TradingCoverageErrorCode | None
    native_daily_bars: tuple[CanonicalBar, ...] | None
    native_daily_error: NativeDailyCoverageErrorCode | None


@dataclass(frozen=True, slots=True)
class ContractCoverageProjection:
    """One session projection plus the internal date facts P4 admission needs."""

    snapshot: ContractCoverageSnapshot
    candidate_dates: tuple[date, ...]
    observed_dates: tuple[date, ...]
    native_available_dates: tuple[date, ...] | None


class _SessionDefinitionUnavailable(RuntimeError):
    pass


class _MinuteDataUnreadable(RuntimeError):
    pass


class _MinuteTimestampUnmapped(RuntimeError):
    pass


class _NativeDailyTimestampUnmapped(RuntimeError):
    pass


_FULL_SCAN_COLUMNS = (
    "ts_event",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "contract_id",
    "source",
    "source_request_id",
    "ingested_at",
)
_PRICE_COLUMNS = ("open", "high", "low", "close")


def read_contract_catalog_metadata(
    store: CanonicalStore,
    contract_id: str,
) -> CatalogCoverageMetadata:
    """Read exact legacy counts/bounds from Parquet metadata, with a timestamp fallback."""
    partitions = store.partition_files(contract_id)
    bar_count = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    for partition in partitions:
        try:
            parquet_file = pq.ParquetFile(partition)  # type: ignore[no-untyped-call]
            metadata = parquet_file.metadata
            timestamp_index = parquet_file.schema_arrow.get_field_index("ts_event")
            if metadata is None or timestamp_index < 0:
                raise ValueError("canonical partition has no ts_event metadata")

            partition_first: datetime | None = None
            partition_last: datetime | None = None
            trustworthy_statistics = True
            for row_group_index in range(metadata.num_row_groups):
                row_group = metadata.row_group(row_group_index)
                if row_group.num_rows == 0:
                    continue
                statistics = row_group.column(timestamp_index).statistics
                if (
                    statistics is None
                    or not statistics.has_min_max
                    or statistics.null_count not in (None, 0)
                ):
                    trustworthy_statistics = False
                    break
                try:
                    row_group_first = _normalized_timestamp(statistics.min)
                    row_group_last = _normalized_timestamp(statistics.max)
                except (OverflowError, TypeError, ValueError):
                    trustworthy_statistics = False
                    break
                if row_group_first > row_group_last:
                    trustworthy_statistics = False
                    break
                partition_first = (
                    row_group_first
                    if partition_first is None
                    else min(partition_first, row_group_first)
                )
                partition_last = (
                    row_group_last
                    if partition_last is None
                    else max(partition_last, row_group_last)
                )

            if metadata.num_rows > 0 and (
                partition_first is None or partition_last is None
            ):
                trustworthy_statistics = False
            if trustworthy_statistics:
                partition_count = metadata.num_rows
            else:
                timestamp_table = parquet_file.read(columns=["ts_event"])  # type: ignore[no-untyped-call]
                partition_count = timestamp_table.num_rows
                partition_first, partition_last = _timestamp_bounds(timestamp_table)
        except Exception:
            # Preserve the legacy catalog behavior: one unreadable partition does not invent
            # counts or bounds for that partition, and configured identity still remains visible.
            continue

        bar_count += partition_count
        if partition_first is not None:
            first_timestamp = (
                partition_first
                if first_timestamp is None
                else min(first_timestamp, partition_first)
            )
        if partition_last is not None:
            last_timestamp = (
                partition_last
                if last_timestamp is None
                else max(last_timestamp, partition_last)
            )
    return CatalogCoverageMetadata(
        partition_count=len(partitions),
        bar_count=bar_count,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
    )


def build_contract_coverage_snapshot(
    contract: ContractSpec,
    *,
    minute_store: CanonicalStore,
    native_daily_store: CanonicalStore,
    owner_entries: Sequence[dict[str, Any]],
    session_name: str = "eth",
) -> ContractCoverageSnapshot:
    """Build full exact facts while physically scanning each minute partition once."""
    source = read_contract_coverage_source(
        contract,
        minute_store=minute_store,
        native_daily_store=native_daily_store,
    )
    return project_contract_coverage_source(
        contract,
        source,
        owner_entries=owner_entries,
        session_name=session_name,
    ).snapshot


def read_contract_coverage_source(
    contract: ContractSpec,
    *,
    minute_store: CanonicalStore,
    native_daily_store: CanonicalStore,
) -> ContractCoverageSource:
    """Read each physical source once without selecting a strategy session."""
    partitions = minute_store.partition_files(contract.contract_id)
    try:
        minute_table = _read_full_contract_table(partitions)
        first_timestamp, last_timestamp = _timestamp_bounds(minute_table)
    except Exception:
        metadata = read_contract_catalog_metadata(minute_store, contract.contract_id)
        minute_table = None
        minute_error: TradingCoverageErrorCode | None = "minute_data_unreadable"
    else:
        metadata = CatalogCoverageMetadata(
            partition_count=len(partitions),
            bar_count=minute_table.num_rows,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
        )
        minute_error = None

    try:
        loaded_native_daily_bars = tuple(native_daily_store.read(contract.contract_id))
    except Exception:
        native_daily_bars: tuple[CanonicalBar, ...] | None = None
        native_daily_error: NativeDailyCoverageErrorCode | None = "native_daily_unreadable"
    else:
        if any(
            bar.contract_id != contract.contract_id
            for bar in loaded_native_daily_bars
        ):
            native_daily_bars = None
            native_daily_error = "native_daily_unreadable"
        else:
            native_daily_bars = loaded_native_daily_bars
            native_daily_error = None

    return ContractCoverageSource(
        partition_count=metadata.partition_count,
        bar_count=metadata.bar_count,
        first_timestamp=metadata.first_timestamp,
        last_timestamp=metadata.last_timestamp,
        minute_table=minute_table,
        minute_error=minute_error,
        native_daily_bars=native_daily_bars,
        native_daily_error=native_daily_error,
    )


def project_contract_coverage_source(
    contract: ContractSpec,
    source: ContractCoverageSource,
    *,
    owner_entries: Sequence[dict[str, Any]],
    session_name: str,
) -> ContractCoverageProjection:
    """Project one already-read physical source through an exact strategy session."""
    metadata = CatalogCoverageMetadata(
        partition_count=source.partition_count,
        bar_count=source.bar_count,
        first_timestamp=source.first_timestamp,
        last_timestamp=source.last_timestamp,
    )
    if source.minute_table is None or source.minute_error is not None:
        snapshot = _unavailable_snapshot(
            metadata,
            trading_error=source.minute_error or "minute_data_unreadable",
            session_name=session_name,
        )
        return ContractCoverageProjection(
            snapshot=snapshot,
            candidate_dates=(),
            observed_dates=(),
            native_available_dates=None,
        )

    try:
        trading_projection = _compute_trading_day_coverage_from_table(
            contract,
            source.minute_table,
            owner_entries=owner_entries,
            session_name=session_name,
        )
    except _MinuteTimestampUnmapped:
        snapshot = _unavailable_snapshot(
            metadata,
            trading_error="minute_timestamp_unmapped",
            session_name=session_name,
        )
        return ContractCoverageProjection(snapshot, (), (), None)
    except _SessionDefinitionUnavailable:
        snapshot = _unavailable_snapshot(
            metadata,
            trading_error="session_definition_unavailable",
            session_name=session_name,
        )
        return ContractCoverageProjection(snapshot, (), (), None)
    except _MinuteDataUnreadable:
        snapshot = _unavailable_snapshot(
            metadata,
            trading_error="minute_data_unreadable",
            session_name=session_name,
        )
        return ContractCoverageProjection(snapshot, (), (), None)
    except Exception:
        snapshot = _unavailable_snapshot(
            metadata,
            trading_error="coverage_computation_failed",
            session_name=session_name,
        )
        return ContractCoverageProjection(snapshot, (), (), None)

    native_available_dates: tuple[date, ...] | None
    if source.native_daily_bars is None or source.native_daily_error is not None:
        native_document = unavailable_native_daily_coverage(
            source.native_daily_error or "native_daily_unreadable"
        )
        native_available_dates = None
    else:
        try:
            native_available_dates = native_daily_trading_dates(
                contract,
                source.native_daily_bars,
                source_session_name="eth",
            )
            native_document = _native_daily_coverage_document(
                native_available_dates,
                required_trading_dates=trading_projection.candidate_dates,
            )
        except _NativeDailyTimestampUnmapped:
            native_document = unavailable_native_daily_coverage(
                "native_daily_timestamp_unmapped"
            )
            native_available_dates = None
        except Exception:
            native_document = unavailable_native_daily_coverage(
                "coverage_computation_failed"
            )
            native_available_dates = None

    snapshot = ContractCoverageSnapshot(
        partition_count=source.partition_count,
        bar_count=source.bar_count,
        first_timestamp=source.first_timestamp,
        last_timestamp=source.last_timestamp,
        trading_day_coverage=trading_projection.document,
        native_daily_coverage=native_document,
    )
    return ContractCoverageProjection(
        snapshot=snapshot,
        candidate_dates=trading_projection.candidate_dates,
        observed_dates=trading_projection.observed_dates,
        native_available_dates=native_available_dates,
    )


def _read_full_contract_table(partitions: Sequence[Path]) -> pa.Table:
    if not partitions:
        schema = pa.schema(
            [CANONICAL_BAR_SCHEMA.field(name) for name in _FULL_SCAN_COLUMNS]
        )
        return pa.Table.from_batches([], schema=schema)
    try:
        return pq.read_table(  # type: ignore[no-untyped-call]
            list(partitions),
            columns=list(_FULL_SCAN_COLUMNS),
            partitioning=None,
        )
    except Exception as exc:
        raise _MinuteDataUnreadable from exc


def _compute_trading_day_coverage_from_table(
    contract: ContractSpec,
    minute_table: pa.Table,
    *,
    owner_entries: Sequence[dict[str, Any]],
    session_name: str,
) -> TradingDayCoverageProjection:
    table, timestamp_ns = _validated_sorted_minute_table(
        contract,
        minute_table,
    )
    mapper = _validated_session_mapper(contract, session_name=session_name)
    try:
        if session_name == "eth":
            mapped_dates = mapper.trading_dates_for_sorted_epoch_nanoseconds(timestamp_ns)
        else:
            # A narrower strategy projection may discard legitimate outer-ETH rows,
            # but it must not hide timestamps that fail the physical source session.
            _validated_session_mapper(
                contract,
                session_name="eth",
            ).trading_dates_for_sorted_epoch_nanoseconds(timestamp_ns)
            projected_dates = mapper.project_trading_dates_for_sorted_epoch_nanoseconds(
                timestamp_ns
            )
            selected_indices = [
                index for index, trading_date in enumerate(projected_dates)
                if trading_date is not None
            ]
            table = pc.take(  # type: ignore[no-untyped-call]
                table,
                pa.array(selected_indices, type=pa.int64()),
            )
            timestamp_ns = [timestamp_ns[index] for index in selected_indices]
            mapped_dates = tuple(
                trading_date
                for trading_date in projected_dates
                if trading_date is not None
            )
    except ValueError as exc:
        raise _MinuteTimestampUnmapped from exc

    if not mapped_dates:
        candidate_dates: tuple[date, ...] = ()
    else:
        candidate_dates = _candidate_trading_dates(
            contract,
            first=min(mapped_dates),
            last=max(mapped_dates),
            session_name=session_name,
        )

    group_ranges: dict[date, tuple[int, int]] = {}
    for index, trading_date in enumerate(mapped_dates):
        existing = group_ranges.get(trading_date)
        if existing is None:
            group_ranges[trading_date] = (index, index + 1)
        else:
            group_ranges[trading_date] = (existing[0], index + 1)

    blocking_rows = _blocking_row_mask(table, tick_size=contract.tick_size)
    highs = table.column("high").to_pylist()
    lows = table.column("low").to_pylist()
    closes = table.column("close").to_pylist()
    quality_settings = QualitySettings()
    complete_dates: list[date] = []
    for trading_date in candidate_dates:
        bounds = session_bounds_for_trading_date(
            contract,
            trading_date,
            session_name=session_name,
        )
        if bounds is None or bounds[0] >= bounds[1]:
            raise _SessionDefinitionUnavailable
        start, end = group_ranges.get(trading_date, (0, 0))
        observed = timestamp_ns[start:end]
        session_start_ns = _datetime_epoch_nanoseconds(bounds[0])
        session_end_ns = _datetime_epoch_nanoseconds(bounds[1])
        minute_ns = 60 * 1_000_000_000
        expected_count = (session_end_ns - session_start_ns) // minute_ns
        exact_sequence = len(observed) == expected_count and all(
            timestamp == session_start_ns + offset * minute_ns
            for offset, timestamp in enumerate(observed)
        )
        if not exact_sequence:
            continue
        if bool(
            pc.any(  # type: ignore[attr-defined]
                blocking_rows.slice(start, end - start)
            ).as_py()
        ):
            continue
        if _contains_atr_spike(
            highs,
            lows,
            closes,
            start=start,
            end=end,
            atr_window=quality_settings.atr_window,
            spike_atr_multiple=quality_settings.spike_atr_multiple,
        ):
            continue
        complete_dates.append(trading_date)

    return TradingDayCoverageProjection(
        document=_build_trading_day_document(
            candidate_dates,
            complete_dates=complete_dates,
            owner_entries=owner_entries,
            session_name=session_name,
        ),
        candidate_dates=candidate_dates,
        observed_dates=tuple(sorted(group_ranges)),
    )


def _validated_sorted_minute_table(
    contract: ContractSpec,
    table: pa.Table,
) -> tuple[pa.Table, list[int]]:
    if any(table.schema.get_field_index(name) < 0 for name in _FULL_SCAN_COLUMNS):
        raise _MinuteDataUnreadable

    timestamp_type = table.schema.field("ts_event").type
    ingested_type = table.schema.field("ingested_at").type
    if (
        not pa.types.is_timestamp(timestamp_type)
        or getattr(timestamp_type, "tz", None) is None
        or not pa.types.is_timestamp(ingested_type)
        or getattr(ingested_type, "tz", None) is None
    ):
        raise _MinuteDataUnreadable
    if table.column("ts_event").null_count or table.column("ingested_at").null_count:
        raise _MinuteDataUnreadable

    for column_name in _PRICE_COLUMNS:
        column = table.column(column_name)
        if (
            not pa.types.is_floating(column.type)
            or column.null_count
            or (
                table.num_rows > 0
                and pc.all(  # type: ignore[attr-defined]
                    pc.is_finite(column)  # type: ignore[attr-defined]
                ).as_py()
                is not True
            )
        ):
            raise _MinuteDataUnreadable
    volume = table.column("volume")
    if not pa.types.is_integer(volume.type) or volume.null_count:
        raise _MinuteDataUnreadable

    contract_ids = table.column("contract_id")
    sources = table.column("source")
    source_request_ids = table.column("source_request_id")
    if (
        not pa.types.is_string(contract_ids.type)
        or contract_ids.null_count
        or not pa.types.is_string(sources.type)
        or sources.null_count
        or not pa.types.is_string(source_request_ids.type)
    ):
        raise _MinuteDataUnreadable
    raw_contract_ids = pc.unique(contract_ids).to_pylist()  # type: ignore[attr-defined]
    if any(
        not isinstance(value, str) or value.strip() != contract.contract_id
        for value in raw_contract_ids
    ):
        raise _MinuteDataUnreadable
    raw_sources = pc.unique(sources).to_pylist()  # type: ignore[attr-defined]
    if any(not isinstance(value, str) or not value.strip() for value in raw_sources):
        raise _MinuteDataUnreadable

    timestamps_raw = table.column("ts_event").cast(pa.int64()).to_pylist()
    if any(type(value) is not int for value in timestamps_raw):
        raise _MinuteDataUnreadable
    timestamps = [value for value in timestamps_raw if type(value) is int]
    if any(
        previous > current
        for previous, current in zip(timestamps, timestamps[1:], strict=False)
    ):
        order = pc.sort_indices(  # type: ignore[attr-defined]
            table,
            sort_keys=[("ts_event", "ascending")],
        )
        table = pc.take(table, order)  # type: ignore[no-untyped-call]
        timestamps = [
            value
            for value in table.column("ts_event").cast(pa.int64()).to_pylist()
            if type(value) is int
        ]
    return table, timestamps


def _blocking_row_mask(
    table: pa.Table,
    *,
    tick_size: float,
) -> pa.Array | pa.ChunkedArray:
    open_prices = table.column("open")
    high_prices = table.column("high")
    low_prices = table.column("low")
    close_prices = table.column("close")
    blocking = pc.less_equal(open_prices, 0.0)  # type: ignore[attr-defined]
    for prices in (high_prices, low_prices, close_prices):
        blocking = pc.or_(  # type: ignore[attr-defined]
            blocking,
            pc.less_equal(prices, 0.0),  # type: ignore[attr-defined]
        )
    blocking = pc.or_(  # type: ignore[attr-defined]
        blocking,
        pc.less(  # type: ignore[attr-defined]
            high_prices,
            pc.max_element_wise(open_prices, close_prices),  # type: ignore[attr-defined]
        ),
    )
    blocking = pc.or_(  # type: ignore[attr-defined]
        blocking,
        pc.greater(  # type: ignore[attr-defined]
            low_prices,
            pc.min_element_wise(open_prices, close_prices),  # type: ignore[attr-defined]
        ),
    )
    blocking = pc.or_(  # type: ignore[attr-defined]
        blocking,
        pc.less(high_prices, low_prices),  # type: ignore[attr-defined]
    )
    blocking = pc.or_(  # type: ignore[attr-defined]
        blocking,
        pc.less(table.column("volume"), 0),  # type: ignore[attr-defined]
    )
    for prices in (open_prices, high_prices, low_prices, close_prices):
        blocking = pc.or_(  # type: ignore[attr-defined]
            blocking,
            _tick_misalignment_mask(prices, tick_size=tick_size),
        )
    return blocking


def _tick_misalignment_mask(
    prices: pa.ChunkedArray,
    *,
    tick_size: float,
) -> pa.Array | pa.ChunkedArray:
    misaligned_values: list[float] = []
    for raw_value in pc.unique(prices).to_pylist():  # type: ignore[attr-defined]
        if not isinstance(raw_value, (float, int)):
            continue
        value = float(raw_value)
        if not math.isfinite(value) or value <= 0:
            continue
        aligned = DataQualityChecker._is_tick_aligned(value, tick_size)
        if not aligned:
            misaligned_values.append(value)
    if not misaligned_values:
        return pc.is_null(prices)  # type: ignore[attr-defined]
    return pc.is_in(  # type: ignore[attr-defined]
        prices,
        value_set=pa.array(misaligned_values, type=prices.type),
    )


def _contains_atr_spike(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    start: int,
    end: int,
    atr_window: int,
    spike_atr_multiple: float,
) -> bool:
    if end - start <= atr_window:
        return False
    seed_ranges: list[float] = []
    previous_close: float | None = None
    atr: float | None = None
    for index in range(start, end):
        high = highs[index]
        low = lows[index]
        close = closes[index]
        true_range = (
            high - low
            if previous_close is None
            else max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
        previous_close = close
        if len(seed_ranges) < atr_window:
            seed_ranges.append(true_range)
            if len(seed_ranges) == atr_window:
                atr = fmean(seed_ranges)
            continue
        if (
            atr is not None
            and atr > 0
            and true_range > atr * spike_atr_multiple
        ):
            return True
        if atr is not None:
            atr = ((atr * (atr_window - 1)) + true_range) / atr_window
    return False


def _timestamp_bounds(table: pa.Table) -> tuple[datetime | None, datetime | None]:
    if table.num_rows == 0:
        return None, None
    result = pc.min_max(  # type: ignore[attr-defined]
        table.column("ts_event")
    ).as_py()
    if not isinstance(result, dict):
        raise ValueError("ts_event min/max statistics are unavailable")
    minimum = result.get("min")
    maximum = result.get("max")
    if minimum is None or maximum is None:
        return None, None
    return _normalized_timestamp(minimum), _normalized_timestamp(maximum)


def _normalized_timestamp(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _datetime_epoch_nanoseconds(value: datetime) -> int:
    normalized = value.astimezone(UTC)
    delta = normalized - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _unavailable_snapshot(
    metadata: CatalogCoverageMetadata,
    *,
    trading_error: TradingCoverageErrorCode,
    session_name: str,
) -> ContractCoverageSnapshot:
    return ContractCoverageSnapshot(
        partition_count=metadata.partition_count,
        bar_count=metadata.bar_count,
        first_timestamp=metadata.first_timestamp,
        last_timestamp=metadata.last_timestamp,
        trading_day_coverage=unavailable_trading_day_coverage(
            trading_error,
            session_name=session_name,
        ),
        native_daily_coverage=unavailable_native_daily_coverage(
            "minute_coverage_dependency_unavailable"
        ),
    )


def build_contract_coverage_facts(
    contract: ContractSpec,
    *,
    minute_store: CanonicalStore,
    native_daily_store: CanonicalStore,
    owner_entries: Sequence[dict[str, Any]],
    session_name: str = "eth",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read both canonical stores and return independent fail-closed nested facts."""
    try:
        minute_bars = minute_store.read(contract.contract_id)
    except Exception:
        return (
            unavailable_trading_day_coverage(
                "minute_data_unreadable",
                session_name=session_name,
            ),
            unavailable_native_daily_coverage(
                "minute_coverage_dependency_unavailable"
            ),
        )
    if any(bar.contract_id != contract.contract_id for bar in minute_bars):
        return (
            unavailable_trading_day_coverage(
                "minute_data_unreadable",
                session_name=session_name,
            ),
            unavailable_native_daily_coverage(
                "minute_coverage_dependency_unavailable"
            ),
        )

    try:
        trading_projection = compute_trading_day_coverage(
            contract,
            minute_bars,
            owner_entries=owner_entries,
            session_name=session_name,
        )
    except _SessionDefinitionUnavailable:
        return (
            unavailable_trading_day_coverage(
                "session_definition_unavailable",
                session_name=session_name,
            ),
            unavailable_native_daily_coverage(
                "minute_coverage_dependency_unavailable"
            ),
        )
    except _MinuteTimestampUnmapped:
        return (
            unavailable_trading_day_coverage(
                "minute_timestamp_unmapped",
                session_name=session_name,
            ),
            unavailable_native_daily_coverage(
                "minute_coverage_dependency_unavailable"
            ),
        )
    except Exception:
        return (
            unavailable_trading_day_coverage(
                "coverage_computation_failed",
                session_name=session_name,
            ),
            unavailable_native_daily_coverage(
                "minute_coverage_dependency_unavailable"
            ),
        )

    try:
        native_daily_bars = native_daily_store.read(contract.contract_id)
    except Exception:
        return (
            trading_projection.document,
            unavailable_native_daily_coverage("native_daily_unreadable"),
        )
    if any(bar.contract_id != contract.contract_id for bar in native_daily_bars):
        return (
            trading_projection.document,
            unavailable_native_daily_coverage("native_daily_unreadable"),
        )

    try:
        native_document = compute_native_daily_coverage(
            contract,
            native_daily_bars,
            required_trading_dates=trading_projection.candidate_dates,
            session_name=session_name,
        )
    except _NativeDailyTimestampUnmapped:
        native_document = unavailable_native_daily_coverage(
            "native_daily_timestamp_unmapped"
        )
    except Exception:
        native_document = unavailable_native_daily_coverage(
            "coverage_computation_failed"
        )
    return trading_projection.document, native_document


def compute_trading_day_coverage(
    contract: ContractSpec,
    minute_bars: Sequence[CanonicalBar],
    *,
    owner_entries: Sequence[dict[str, Any]],
    session_name: str = "eth",
    quality_checker: MinuteQualityChecker | None = None,
) -> TradingDayCoverageProjection:
    """Recompute literal complete/problem dates from current canonical one-minute bars."""
    mapper = _validated_session_mapper(contract, session_name=session_name)
    checker = quality_checker or DataQualityChecker()
    grouped: dict[date, list[CanonicalBar]] = defaultdict(list)
    if session_name == "eth":
        try:
            mapped_dates = mapper.trading_dates_for_timestamps(
                [bar.timestamp for bar in minute_bars]
            )
        except ValueError as exc:
            raise _MinuteTimestampUnmapped from exc
        for bar, trading_date in zip(minute_bars, mapped_dates, strict=True):
            grouped[trading_date].append(bar)
    else:
        source_mapper = _validated_session_mapper(contract, session_name="eth")
        for bar in minute_bars:
            try:
                source_mapper.trading_date_for_timestamp(bar.timestamp)
            except ValueError as exc:
                raise _MinuteTimestampUnmapped from exc
            try:
                trading_date = mapper.trading_date_for_timestamp(bar.timestamp)
            except ValueError:
                continue
            grouped[trading_date].append(bar)

    if not grouped:
        candidate_dates: tuple[date, ...] = ()
    else:
        candidate_dates = _candidate_trading_dates(
            contract,
            first=min(grouped),
            last=max(grouped),
            session_name=session_name,
        )

    complete_dates: list[date] = []
    for trading_date in candidate_dates:
        bounds = session_bounds_for_trading_date(
            contract,
            trading_date,
            session_name=session_name,
        )
        if bounds is None or bounds[0] >= bounds[1]:
            raise _SessionDefinitionUnavailable
        expected = expected_minute_timestamps(
            contract,
            bounds[0],
            bounds[1],
            session_name=session_name,
        )
        bars = sorted(grouped.get(trading_date, []), key=lambda item: item.timestamp)
        observed = [bar.timestamp for bar in bars]
        exact_sequence = len(observed) == len(expected) and set(observed) == expected
        report = checker.check(
            contract.contract_id,
            bars,
            expected_timestamps=expected,
            tick_size=contract.tick_size,
        )
        has_blocking_finding = any(
            issue.severity in _BLOCKING_SEVERITIES for issue in report.issues
        )
        if exact_sequence and not has_blocking_finding:
            complete_dates.append(trading_date)

    return TradingDayCoverageProjection(
        document=_build_trading_day_document(
            candidate_dates,
            complete_dates=complete_dates,
            owner_entries=owner_entries,
            session_name=session_name,
        ),
        candidate_dates=candidate_dates,
        observed_dates=tuple(sorted(grouped)),
    )


def _build_trading_day_document(
    candidate_dates: Sequence[date],
    *,
    complete_dates: Sequence[date],
    owner_entries: Sequence[dict[str, Any]],
    session_name: str,
) -> dict[str, Any]:
    candidate_set = set(candidate_dates)
    complete_set = set(complete_dates)
    problem_dates = sorted(candidate_set - complete_set)
    latest_decisions = _latest_owner_decisions(owner_entries)
    trusted_problem_dates = sorted(
        day for day in problem_dates if latest_decisions.get(day) == "trust"
    )
    excluded_dates = sorted(
        day
        for day in candidate_dates
        if latest_decisions.get(day) == "exclude"
    )
    pending_problem_dates = sorted(
        set(problem_dates) - set(trusted_problem_dates) - set(excluded_dates)
    )
    longest_segment = _longest_complete_segment(
        candidate_dates,
        usable_dates=complete_set - set(excluded_dates),
    )

    return {
        "schema": "trading_day_coverage.v1",
        "status": "known",
        "session_name": session_name,
        "first_trading_date": (
            candidate_dates[0].isoformat() if candidate_dates else None
        ),
        "last_trading_date": (
            candidate_dates[-1].isoformat() if candidate_dates else None
        ),
        "trading_date_count": len(candidate_dates),
        "complete_trading_date_count": len(complete_dates),
        "problem_trading_date_count": len(problem_dates),
        "pending_problem_trading_date_count": len(pending_problem_dates),
        "owner_trusted_problem_trading_date_count": len(trusted_problem_dates),
        "owner_excluded_trading_date_count": len(excluded_dates),
        "complete_trading_dates": _iso_dates(complete_dates),
        "problem_trading_dates": _iso_dates(problem_dates),
        "pending_problem_trading_dates": _iso_dates(pending_problem_dates),
        "owner_trusted_problem_trading_dates": _iso_dates(
            trusted_problem_dates
        ),
        "owner_excluded_trading_dates": _iso_dates(excluded_dates),
        "longest_complete_segment": longest_segment,
    }


def compute_native_daily_coverage(
    contract: ContractSpec,
    native_daily_bars: Sequence[CanonicalBar],
    *,
    required_trading_dates: Sequence[date],
    session_name: str = "eth",
    source_session_name: str | None = None,
) -> dict[str, Any]:
    """Map exact session-start daily bars and intersect them with the minute candidate range."""
    available_dates = native_daily_trading_dates(
        contract,
        native_daily_bars,
        source_session_name=source_session_name or session_name,
    )
    return _native_daily_coverage_document(
        available_dates,
        required_trading_dates=required_trading_dates,
    )


def native_daily_trading_dates(
    contract: ContractSpec,
    native_daily_bars: Sequence[CanonicalBar],
    *,
    source_session_name: str = "eth",
) -> tuple[date, ...]:
    """Recover canonical trading-date labels from exact source-session starts."""
    mapper = _validated_session_mapper(contract, session_name=source_session_name)
    available_dates: set[date] = set()
    for bar in native_daily_bars:
        try:
            trading_date = mapper.trading_date_for_session_start(bar.timestamp)
        except ValueError as exc:
            raise _NativeDailyTimestampUnmapped from exc
        available_dates.add(trading_date)
    return tuple(sorted(available_dates))


def _native_daily_coverage_document(
    available_dates: Sequence[date],
    *,
    required_trading_dates: Sequence[date],
) -> dict[str, Any]:
    ordered_available = tuple(sorted(set(available_dates)))
    required = set(required_trading_dates)
    available_set = set(ordered_available)
    within = required & available_set
    missing = sorted(required - available_set)
    return {
        "schema": "native_daily_coverage.v1",
        "status": "known",
        "available_first_trading_date": (
            ordered_available[0].isoformat() if ordered_available else None
        ),
        "available_last_trading_date": (
            ordered_available[-1].isoformat() if ordered_available else None
        ),
        "available_trading_date_count": len(ordered_available),
        "within_minute_range_trading_date_count": len(within),
        "required_minute_range_trading_date_count": len(required),
        "missing_within_minute_range_trading_dates": _iso_dates(missing),
    }


def unavailable_trading_day_coverage(
    error_code: TradingCoverageErrorCode,
    *,
    session_name: str,
) -> dict[str, Any]:
    """Build the exact fail-closed trading-day object without plausible zero facts."""
    return {
        "schema": "trading_day_coverage.v1",
        "status": "unavailable",
        "error_code": error_code,
        "session_name": session_name,
        "first_trading_date": None,
        "last_trading_date": None,
        "trading_date_count": None,
        "complete_trading_date_count": None,
        "problem_trading_date_count": None,
        "pending_problem_trading_date_count": None,
        "owner_trusted_problem_trading_date_count": None,
        "owner_excluded_trading_date_count": None,
        "complete_trading_dates": None,
        "problem_trading_dates": None,
        "pending_problem_trading_dates": None,
        "owner_trusted_problem_trading_dates": None,
        "owner_excluded_trading_dates": None,
        "longest_complete_segment": None,
    }


def unavailable_native_daily_coverage(
    error_code: NativeDailyCoverageErrorCode,
) -> dict[str, Any]:
    """Build the exact fail-closed daily object without zero/empty fallbacks."""
    return {
        "schema": "native_daily_coverage.v1",
        "status": "unavailable",
        "error_code": error_code,
        "available_first_trading_date": None,
        "available_last_trading_date": None,
        "available_trading_date_count": None,
        "within_minute_range_trading_date_count": None,
        "required_minute_range_trading_date_count": None,
        "missing_within_minute_range_trading_dates": None,
    }


def _validated_session_mapper(
    contract: ContractSpec,
    *,
    session_name: str,
) -> SessionDateMapper:
    try:
        mapper = SessionDateMapper(contract, session_name=session_name)
        bounds = mapper.bounds_for_trading_date(date(2026, 1, 5))
    except (KeyError, ValueError) as exc:
        raise _SessionDefinitionUnavailable from exc
    if bounds is None or bounds[0] >= bounds[1]:
        raise _SessionDefinitionUnavailable
    return mapper


def _candidate_trading_dates(
    contract: ContractSpec,
    *,
    first: date,
    last: date,
    session_name: str,
) -> tuple[date, ...]:
    dates: list[date] = []
    cursor = first
    while cursor <= last:
        bounds = session_bounds_for_trading_date(
            contract,
            cursor,
            session_name=session_name,
        )
        if bounds is not None:
            dates.append(cursor)
        cursor += timedelta(days=1)
    return tuple(dates)


def _latest_owner_decisions(
    entries: Sequence[dict[str, Any]],
) -> dict[date, Literal["exclude", "trust"]]:
    decisions: dict[date, Literal["exclude", "trust"]] = {}
    for entry in entries:
        decision = entry.get("decision")
        if decision not in {"exclude", "trust"}:
            continue
        raw_date = entry.get("trading_date")
        if not isinstance(raw_date, str):
            msg = "owner decision trading_date must be an ISO date string"
            raise ValueError(msg)
        parsed = date.fromisoformat(raw_date)
        if raw_date != parsed.isoformat():
            msg = "owner decision trading_date must be canonical YYYY-MM-DD"
            raise ValueError(msg)
        decisions[parsed] = decision
    return decisions


def _longest_complete_segment(
    candidate_dates: Sequence[date],
    *,
    usable_dates: set[date],
) -> dict[str, Any] | None:
    best_start: date | None = None
    best_end: date | None = None
    best_count = 0
    current_start: date | None = None
    current_end: date | None = None
    current_count = 0

    for trading_date in candidate_dates:
        if trading_date in usable_dates:
            if current_start is None:
                current_start = trading_date
                current_count = 0
            current_end = trading_date
            current_count += 1
            if current_count > best_count:
                best_start = current_start
                best_end = current_end
                best_count = current_count
            continue
        current_start = None
        current_end = None
        current_count = 0

    if best_start is None or best_end is None:
        return None
    return {
        "start_trading_date": best_start.isoformat(),
        "end_trading_date": best_end.isoformat(),
        "trading_date_count": best_count,
    }


def _iso_dates(values: Iterable[date]) -> list[str]:
    return [value.isoformat() for value in values]
