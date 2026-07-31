"""P3 coverage two-speed contract, differential truth, and read-only guards."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from futures_research.api import data_catalog
from futures_research.api.data_catalog import DataPaths, list_coverage
from futures_research.api.main import app
from futures_research.backtest.persistence import ResultExporter, SqliteRunStore
from futures_research.data import coverage as coverage_mod
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.coverage import build_contract_coverage_facts
from futures_research.data.daily_download import DailyDataIngestionService
from futures_research.data.download import IbConnectionManager, SegmentedHistoricalDownloader
from futures_research.data.ingestion import DataIngestionService
from futures_research.data.models import CanonicalBar
from futures_research.data.quality import DataQualityChecker
from futures_research.data.sessions import session_bounds_for_trading_date
from futures_research.data.storage import CANONICAL_BAR_SCHEMA, CanonicalStore
from futures_research.paths import PROJECT_ROOT

CATALOG_ROW_KEYS = {
    "symbol",
    "contract_id",
    "display_name",
    "asset_class",
    "currency",
    "sessions_available",
    "partition_count",
    "bar_count",
    "first_timestamp",
    "last_timestamp",
    "roll_blackout_dates",
    "owner_excluded_dates",
    "quality",
}
FULL_ONLY_ROW_KEYS = {"trading_day_coverage", "native_daily_coverage"}


def _write_contract_config(
    project_root: Path,
    *,
    symbols: tuple[str, ...] = ("NQ",),
) -> tuple[Path, ContractRegistry]:
    raw = yaml.safe_load(
        (PROJECT_ROOT / "config" / "contracts.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(raw, dict)
    contracts = raw["contracts"]
    for symbol in symbols:
        contracts[symbol]["sessions"]["eth"] = {
            "start": "09:00",
            "end": "09:20",
        }
    raw["contracts"] = {symbol: contracts[symbol] for symbol in symbols}
    path = project_root / "config" / "contracts.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path, ContractRegistry.from_yaml(path)


def _minute_bars(
    contract: ContractSpec,
    trading_date: date,
    *,
    omit: frozenset[int] = frozenset(),
) -> list[CanonicalBar]:
    bounds = session_bounds_for_trading_date(
        contract,
        trading_date,
        session_name="eth",
    )
    assert bounds is not None
    count = int((bounds[1] - bounds[0]) / timedelta(minutes=1))
    return [
        CanonicalBar(
            timestamp=bounds[0] + timedelta(minutes=index),
            open=100.0,
            high=100.25,
            low=99.75,
            close=100.0,
            volume=100,
            contract_id=contract.contract_id,
            source="fixture",
        )
        for index in range(count)
        if index not in omit
    ]


def _native_bar(
    contract: ContractSpec,
    trading_date: date,
) -> CanonicalBar:
    bounds = session_bounds_for_trading_date(
        contract,
        trading_date,
        session_name="eth",
    )
    assert bounds is not None
    return CanonicalBar(
        timestamp=bounds[0],
        open=100.0,
        high=100.25,
        low=99.75,
        close=100.0,
        volume=1_000,
        contract_id=contract.contract_id,
        source="ib_native_daily",
    )


def _write_raw_partition(
    store: CanonicalStore,
    bars: list[CanonicalBar],
    *,
    write_statistics: bool = True,
    path_contract_id: str | None = None,
) -> Path:
    assert bars
    first = bars[0]
    destination = (
        store.root
        / f"contract_id={path_contract_id or first.contract_id}"
        / f"year={first.timestamp.year:04d}"
        / f"month={first.timestamp.month:02d}"
        / "bars.parquet"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(
        [bar.to_record() for bar in bars],
        schema=CANONICAL_BAR_SCHEMA,
    )
    pq.write_table(  # type: ignore[no-untyped-call]
        table,
        destination,
        write_statistics=write_statistics,
    )
    return destination


def _write_corrupt_partition(root: Path, contract_id: str) -> Path:
    path = (
        root
        / f"contract_id={contract_id}"
        / "year=2026"
        / "month=07"
        / "bars.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not parquet")
    return path


def _write_owner_entries(
    paths: DataPaths,
    contract_id: str,
    entries: list[dict[str, str]],
) -> None:
    paths.blacklist_path.parent.mkdir(parents=True, exist_ok=True)
    paths.blacklist_path.write_text(
        json.dumps(
            {
                "schema": "owner_blacklist.v1",
                "updated_at": "2026-07-27T00:00:00Z",
                "entries": [
                    {"contract_id": contract_id, **entry}
                    for entry in entries
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _tree_inventory(root: Path) -> dict[str, tuple[int, str]]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _slow_reference_payload(
    paths: DataPaths,
    *,
    contracts_config: Path,
) -> dict[str, Any]:
    """Literal pre-correction list_coverage path retained as the truth oracle."""
    registry = ContractRegistry.from_yaml(contracts_config)
    minute_store = CanonicalStore(paths.market_root)
    native_daily_store = CanonicalStore(paths.daily_market_root)
    rows: list[dict[str, Any]] = []
    for symbol, contract in sorted(registry.contracts.items()):
        partitions = minute_store.partition_files(contract.contract_id)
        first_timestamp: datetime | None = None
        last_timestamp: datetime | None = None
        bar_count = 0
        for partition in partitions:
            try:
                parquet_file = pq.ParquetFile(partition)  # type: ignore[no-untyped-call]
                table = parquet_file.read(columns=["ts_event"])  # type: ignore[no-untyped-call]
                bar_count += table.num_rows
                times = [
                    value
                    if isinstance(value, datetime)
                    else datetime.fromisoformat(str(value))
                    for value in table.column("ts_event").to_pylist()
                    if value is not None
                ]
                if not times:
                    continue
                low = min(times)
                high = max(times)
                if low.tzinfo is None:
                    low = low.replace(tzinfo=UTC)
                if high.tzinfo is None:
                    high = high.replace(tzinfo=UTC)
                first_timestamp = (
                    low
                    if first_timestamp is None
                    else min(first_timestamp, low)
                )
                last_timestamp = (
                    high
                    if last_timestamp is None
                    else max(last_timestamp, high)
                )
            except (OSError, TypeError, ValueError):
                continue
        owner = data_catalog._owner_entries_for_contract(  # noqa: SLF001
            paths,
            contract.contract_id,
        )
        trading, native = build_contract_coverage_facts(
            contract,
            minute_store=minute_store,
            native_daily_store=native_daily_store,
            owner_entries=owner,
        )
        rows.append(
            {
                "symbol": symbol,
                "contract_id": contract.contract_id,
                "display_name": contract.display_name,
                "asset_class": contract.asset_class,
                "currency": contract.currency,
                "sessions_available": sorted(contract.sessions),
                "partition_count": len(partitions),
                "bar_count": bar_count,
                "first_timestamp": (
                    first_timestamp.isoformat().replace("+00:00", "Z")
                    if first_timestamp is not None
                    else None
                ),
                "last_timestamp": (
                    last_timestamp.isoformat().replace("+00:00", "Z")
                    if last_timestamp is not None
                    else None
                ),
                "roll_blackout_dates": [
                    value.isoformat()
                    for value in sorted(contract.roll_blackout_dates())
                ],
                "owner_excluded_dates": [
                    entry["trading_date"]
                    for entry in owner
                    if entry.get("decision") == "exclude"
                ],
                "quality": data_catalog._latest_quality_summary(  # noqa: SLF001
                    paths,
                    contract.contract_id,
                ),
                "trading_day_coverage": trading,
                "native_daily_coverage": native,
            }
        )
    return {
        "schema": "data_coverage.v1",
        "count": len(rows),
        "contracts": rows,
    }


@pytest.mark.asyncio
async def test_query_contract_default_full_catalog_and_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    _, registry = _write_contract_config(project)
    contract = registry.by_symbol("NQ")
    paths = DataPaths(data_root=project / "data")
    CanonicalStore(paths.market_root).append(
        _minute_bars(contract, date(2026, 7, 20))
    )
    CanonicalStore(paths.daily_market_root).append(
        [_native_bar(contract, date(2026, 7, 20))]
    )
    monkeypatch.setattr(data_catalog, "PROJECT_ROOT", project)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        default_response = await client.get("/api/v1/data/coverage")
        full_response = await client.get("/api/v1/data/coverage?view=full")
        catalog_response = await client.get("/api/v1/data/coverage?view=catalog")
        invalid_response = await client.get("/api/v1/data/coverage?view=summary")

    assert default_response.status_code == full_response.status_code == 200
    assert default_response.json() == full_response.json()
    catalog = catalog_response.json()
    assert catalog_response.status_code == 200
    assert set(catalog) == {"schema", "count", "contracts"}
    assert set(catalog["contracts"][0]) == CATALOG_ROW_KEYS
    assert not (FULL_ONLY_ROW_KEYS & set(catalog["contracts"][0]))
    assert set(default_response.json()["contracts"][0]) == (
        CATALOG_ROW_KEYS | FULL_ONLY_ROW_KEYS
    )
    assert invalid_response.status_code == 422


def test_catalog_includes_configured_empty_and_uses_footer_fallback(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    config, registry = _write_contract_config(project, symbols=("NQ", "YM"))
    nq = registry.by_symbol("NQ")
    paths = DataPaths(data_root=project / "data")
    bars = _minute_bars(nq, date(2026, 7, 20))
    _write_raw_partition(
        CanonicalStore(paths.market_root),
        bars,
        write_statistics=False,
    )

    payload = list_coverage(paths, contracts_config=config, view="catalog")

    assert [row["symbol"] for row in payload["contracts"]] == ["NQ", "YM"]
    nq_row, ym_row = payload["contracts"]
    assert nq_row["bar_count"] == len(bars)
    assert nq_row["first_timestamp"] == bars[0].timestamp.isoformat().replace(
        "+00:00",
        "Z",
    )
    assert nq_row["last_timestamp"] == bars[-1].timestamp.isoformat().replace(
        "+00:00",
        "Z",
    )
    assert ym_row["partition_count"] == ym_row["bar_count"] == 0
    assert ym_row["first_timestamp"] is ym_row["last_timestamp"] is None


def test_catalog_calls_no_full_minute_daily_or_quality_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    config, registry = _write_contract_config(project)
    contract = registry.by_symbol("NQ")
    paths = DataPaths(data_root=project / "data")
    CanonicalStore(paths.market_root).append(
        _minute_bars(contract, date(2026, 7, 20))
    )
    _write_corrupt_partition(paths.daily_market_root, contract.contract_id)
    calls = {
        "full_snapshot": 0,
        "slow_helper": 0,
        "minute_or_daily_read": 0,
        "quality": 0,
    }

    def forbidden_full(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls["full_snapshot"] += 1
        raise AssertionError("catalog must not build full coverage")

    def forbidden_slow(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls["slow_helper"] += 1
        raise AssertionError("catalog must not call the slow helper")

    def forbidden_read(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls["minute_or_daily_read"] += 1
        raise AssertionError("catalog must not materialize canonical bars")

    def forbidden_quality(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls["quality"] += 1
        raise AssertionError("catalog must not recompute quality")

    monkeypatch.setattr(data_catalog, "build_contract_coverage_snapshot", forbidden_full)
    monkeypatch.setattr(coverage_mod, "build_contract_coverage_facts", forbidden_slow)
    monkeypatch.setattr(CanonicalStore, "read", forbidden_read)
    monkeypatch.setattr(DataQualityChecker, "check", forbidden_quality)

    payload = list_coverage(paths, contracts_config=config, view="catalog")

    assert payload["count"] == 1
    assert set(payload["contracts"][0]) == CATALOG_ROW_KEYS
    assert calls == {
        "full_snapshot": 0,
        "slow_helper": 0,
        "minute_or_daily_read": 0,
        "quality": 0,
    }


def test_full_uses_one_minute_scan_and_never_materializes_minute_bars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    config, registry = _write_contract_config(project)
    contract = registry.by_symbol("NQ")
    paths = DataPaths(data_root=project / "data")
    CanonicalStore(paths.market_root).append(
        _minute_bars(contract, date(2026, 7, 20))
    )
    CanonicalStore(paths.daily_market_root).append(
        [_native_bar(contract, date(2026, 7, 20))]
    )
    calls = {"minute_scan": 0, "minute_read": 0, "daily_read": 0}
    original_scan = coverage_mod._read_full_contract_table  # noqa: SLF001
    original_read = CanonicalStore.read

    def counted_scan(partitions: list[Path]) -> pa.Table:
        calls["minute_scan"] += 1
        return original_scan(partitions)

    def counted_read(
        self: CanonicalStore,
        contract_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CanonicalBar]:
        key = "daily_read" if self.root == paths.daily_market_root else "minute_read"
        calls[key] += 1
        return original_read(self, contract_id, start=start, end=end)

    monkeypatch.setattr(coverage_mod, "_read_full_contract_table", counted_scan)
    monkeypatch.setattr(CanonicalStore, "read", counted_read)

    payload = list_coverage(paths, contracts_config=config, view="full")

    assert payload["contracts"][0]["trading_day_coverage"]["status"] == "known"
    assert calls == {"minute_scan": 1, "minute_read": 0, "daily_read": 1}


def test_full_document_matches_slow_reference_across_differential_fixtures(
    tmp_path: Path,
) -> None:
    scenario_names = (
        "missing_minute",
        "zero_bar_weekday",
        "duplicate_timestamp",
        "invalid_price",
        "ohlc_envelope",
        "negative_volume",
        "tick_misalignment_warning",
        "atr_spike_warning",
        "owner_trust_exclude",
        "dst_boundary",
        "unmapped_minute",
        "configured_empty",
    )
    for scenario_name in scenario_names:
        project = tmp_path / scenario_name
        config, registry = _write_contract_config(project)
        contract = registry.by_symbol("NQ")
        paths = DataPaths(data_root=project / "data")
        minute_store = CanonicalStore(paths.market_root)
        native_store = CanonicalStore(paths.daily_market_root)
        monday = date(2026, 7, 20)
        bars = _minute_bars(contract, monday)
        native_bars = [_native_bar(contract, monday)]
        raw_write = False

        if scenario_name == "missing_minute":
            bars = _minute_bars(contract, monday, omit=frozenset({1}))
        elif scenario_name == "zero_bar_weekday":
            wednesday = date(2026, 7, 22)
            bars = [
                *_minute_bars(contract, monday),
                *_minute_bars(contract, wednesday),
            ]
            native_bars.append(_native_bar(contract, wednesday))
        elif scenario_name == "duplicate_timestamp":
            bars = [*bars, bars[0]]
            raw_write = True
        elif scenario_name == "invalid_price":
            bars[0] = bars[0].model_copy(update={"open": 0.0})
        elif scenario_name == "ohlc_envelope":
            bars[0] = bars[0].model_copy(update={"high": 99.5})
        elif scenario_name == "negative_volume":
            bars[0] = bars[0].model_copy(update={"volume": -1})
        elif scenario_name == "tick_misalignment_warning":
            bars[0] = bars[0].model_copy(update={"open": 100.1})
        elif scenario_name == "atr_spike_warning":
            bars[14] = bars[14].model_copy(
                update={"high": 110.0, "low": 99.75}
            )
        elif scenario_name == "owner_trust_exclude":
            tuesday = date(2026, 7, 21)
            wednesday = date(2026, 7, 22)
            bars = [
                *_minute_bars(contract, monday),
                *_minute_bars(contract, tuesday, omit=frozenset({1})),
                *_minute_bars(contract, wednesday),
            ]
            native_bars.extend(
                [
                    _native_bar(contract, tuesday),
                    _native_bar(contract, wednesday),
                ]
            )
            _write_owner_entries(
                paths,
                contract.contract_id,
                [
                    {"trading_date": "2026-07-21", "decision": "trust"},
                    {"trading_date": "2026-07-22", "decision": "exclude"},
                ],
            )
        elif scenario_name == "dst_boundary":
            friday = date(2026, 3, 6)
            dst_monday = date(2026, 3, 9)
            bars = [
                *_minute_bars(contract, friday),
                *_minute_bars(contract, dst_monday),
            ]
            native_bars = [
                _native_bar(contract, friday),
                _native_bar(contract, dst_monday),
            ]
        elif scenario_name == "unmapped_minute":
            bounds = session_bounds_for_trading_date(
                contract,
                monday,
                session_name="eth",
            )
            assert bounds is not None
            bars = [
                bars[0].model_copy(
                    update={"timestamp": bounds[0] - timedelta(minutes=1)}
                )
            ]
        elif scenario_name == "configured_empty":
            bars = []
            native_bars = []

        if bars:
            if raw_write:
                _write_raw_partition(minute_store, bars)
            else:
                minute_store.append(bars)
        if native_bars:
            native_store.append(native_bars)

        optimized = list_coverage(
            paths,
            contracts_config=config,
            view="full",
        )
        reference = _slow_reference_payload(
            paths,
            contracts_config=config,
        )
        assert optimized == reference, scenario_name


def test_full_document_matches_reference_for_native_and_corrupt_dependencies(
    tmp_path: Path,
) -> None:
    for scenario_name in (
        "unmapped_native",
        "corrupt_native",
        "corrupt_minute",
    ):
        project = tmp_path / scenario_name
        config, registry = _write_contract_config(project)
        contract = registry.by_symbol("NQ")
        paths = DataPaths(data_root=project / "data")
        day = date(2026, 7, 20)
        if scenario_name == "corrupt_minute":
            _write_corrupt_partition(paths.market_root, contract.contract_id)
        else:
            CanonicalStore(paths.market_root).append(_minute_bars(contract, day))
        if scenario_name == "corrupt_native":
            _write_corrupt_partition(paths.daily_market_root, contract.contract_id)
        elif scenario_name == "unmapped_native":
            native = _native_bar(contract, day)
            CanonicalStore(paths.daily_market_root).append(
                [
                    native.model_copy(
                        update={"timestamp": native.timestamp + timedelta(minutes=1)}
                    )
                ]
            )
        else:
            CanonicalStore(paths.daily_market_root).append(
                [_native_bar(contract, day)]
            )

        optimized = list_coverage(
            paths,
            contracts_config=config,
            view="full",
        )
        reference = _slow_reference_payload(
            paths,
            contracts_config=config,
        )
        assert optimized == reference, scenario_name
        row = optimized["contracts"][0]
        if scenario_name == "corrupt_native":
            assert row["trading_day_coverage"]["status"] == "known"
            assert row["native_daily_coverage"]["error_code"] == "native_daily_unreadable"
        if scenario_name == "corrupt_minute":
            assert row["trading_day_coverage"]["error_code"] == "minute_data_unreadable"
            assert (
                row["native_daily_coverage"]["error_code"]
                == "minute_coverage_dependency_unavailable"
            )


@pytest.mark.parametrize("mutation", ["wrong_contract", "blank_source"])
def test_full_fails_closed_for_invalid_contract_identity_or_source(
    tmp_path: Path,
    mutation: str,
) -> None:
    project = tmp_path / mutation
    config, registry = _write_contract_config(project)
    contract = registry.by_symbol("NQ")
    paths = DataPaths(data_root=project / "data")
    bars = _minute_bars(contract, date(2026, 7, 20))
    if mutation == "wrong_contract":
        bars[0] = bars[0].model_copy(update={"contract_id": "WRONG-CONTRACT"})
    else:
        bars[0] = bars[0].model_copy(update={"source": " "})
    _write_raw_partition(
        CanonicalStore(paths.market_root),
        bars,
        path_contract_id=contract.contract_id,
    )

    optimized = list_coverage(
        paths,
        contracts_config=config,
        view="full",
    )
    reference = _slow_reference_payload(
        paths,
        contracts_config=config,
    )

    assert optimized == reference
    row = optimized["contracts"][0]
    assert row["trading_day_coverage"]["error_code"] == "minute_data_unreadable"
    assert (
        row["native_daily_coverage"]["error_code"]
        == "minute_coverage_dependency_unavailable"
    )


@pytest.mark.asyncio
async def test_catalog_and_full_get_are_byte_read_only_and_call_no_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    _, registry = _write_contract_config(project)
    contract = registry.by_symbol("NQ")
    paths = DataPaths(data_root=project / "data")
    CanonicalStore(paths.market_root).append(
        _minute_bars(contract, date(2026, 7, 20))
    )
    CanonicalStore(paths.daily_market_root).append(
        [_native_bar(contract, date(2026, 7, 20))]
    )
    paths.quality_root.joinpath(contract.contract_id).mkdir(parents=True)
    paths.quality_root.joinpath(contract.contract_id, "report.json").write_text(
        json.dumps(
            {
                "contract_id": contract.contract_id,
                "checked_at": "2026-07-27T00:00:00Z",
                "total_bars": 20,
                "issues": [],
            }
        ),
        encoding="utf-8",
    )
    _write_owner_entries(paths, contract.contract_id, [])
    for relative_path in (
        "data/strategies/strategy-fixture/strategy.yaml",
        "data/backtests/runs.sqlite3",
        "data/backtests/results/run-fixture/result.v1.json",
    ):
        marker = project / relative_path
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"immutable-fixture")
    before = _tree_inventory(project)
    calls = {
        "append": 0,
        "ingest": 0,
        "daily_ingest": 0,
        "download": 0,
        "ib": 0,
        "migration": 0,
        "db": 0,
        "artifact": 0,
        "report": 0,
    }

    def forbidden(key: str) -> Any:
        def fail(*args: object, **kwargs: object) -> None:
            del args, kwargs
            calls[key] += 1
            raise AssertionError(f"coverage GET called forbidden {key} path")

        return fail

    monkeypatch.setattr(data_catalog, "PROJECT_ROOT", project)
    monkeypatch.setattr(CanonicalStore, "append", forbidden("append"))
    monkeypatch.setattr(DataIngestionService, "ingest", forbidden("ingest"))
    monkeypatch.setattr(
        DailyDataIngestionService,
        "ingest",
        forbidden("daily_ingest"),
    )
    monkeypatch.setattr(
        SegmentedHistoricalDownloader,
        "download",
        forbidden("download"),
    )
    monkeypatch.setattr(IbConnectionManager, "connect", forbidden("ib"))
    monkeypatch.setattr(
        SqliteRunStore,
        "migrate_run_indexes",
        forbidden("migration"),
    )
    monkeypatch.setattr(SqliteRunStore, "persist", forbidden("db"))
    monkeypatch.setattr(ResultExporter, "export", forbidden("artifact"))
    monkeypatch.setattr(data_catalog, "_write_json_atomic", forbidden("report"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        catalog_response = await client.get("/api/v1/data/coverage?view=catalog")
        full_response = await client.get("/api/v1/data/coverage?view=full")

    assert catalog_response.status_code == full_response.status_code == 200
    assert _tree_inventory(project) == before
    assert calls == {
        "append": 0,
        "ingest": 0,
        "daily_ingest": 0,
        "download": 0,
        "ib": 0,
        "migration": 0,
        "db": 0,
        "artifact": 0,
        "report": 0,
    }


def test_full_path_keeps_complete_days_and_includes_zero_bar_weekday(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    config, registry = _write_contract_config(project)
    contract = registry.by_symbol("NQ")
    paths = DataPaths(data_root=project / "data")
    monday = date(2026, 7, 20)
    wednesday = date(2026, 7, 22)
    CanonicalStore(paths.market_root).append(
        [
            *_minute_bars(contract, monday),
            *_minute_bars(contract, wednesday),
        ]
    )

    row = list_coverage(
        paths,
        contracts_config=config,
        view="full",
    )["contracts"][0]

    assert row["trading_day_coverage"]["complete_trading_dates"] == [
        "2026-07-20",
        "2026-07-22",
    ]
    assert row["trading_day_coverage"]["problem_trading_dates"] == [
        "2026-07-21"
    ]
