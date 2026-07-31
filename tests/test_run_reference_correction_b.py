"""Correction-B regression coverage for fresh main snapshots and runtime proof checks."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from futures_research.api import routes_run_references
from futures_research.api.main import app
from futures_research.backtest import persistence as persistence_module
from futures_research.backtest import run_reference_catalog as catalog_module
from futures_research.backtest.persistence import SqliteRunStore
from futures_research.backtest.records import (
    RunResult,
    build_run_result,
    consumed_trading_dates_for_bars,
    prepare_run,
)
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.paths import PROJECT_ROOT

_COMPLETED_AT = datetime(2026, 7, 26, 12, tzinfo=UTC)
_RANGE_START = datetime(2026, 7, 20, 22, tzinfo=UTC)
_AUDIT_RUN_ID = "nq-20260724-3b4-audit"


def _bar(contract: ContractSpec, timestamp: datetime = _RANGE_START) -> CanonicalBar:
    return CanonicalBar(
        timestamp=timestamp,
        open=20_000.0,
        high=20_001.0,
        low=19_999.0,
        close=20_000.5,
        volume=100,
        contract_id=contract.contract_id,
        source="fixture",
    )


def _persist(
    store: SqliteRunStore,
    contract: ContractSpec,
    *,
    run_id: str,
    strategy_version: str,
    validation_run: bool = False,
    start: datetime = _RANGE_START,
) -> None:
    """Create a complete, proof-published standard or validation run in one transaction."""
    bars = (_bar(contract, start),)
    prepared = prepare_run(
        run_id=run_id,
        strategy_version=strategy_version,
        contract=contract,
        session_name="eth",
        range_start=start,
        range_end=start + timedelta(minutes=1),
        initial_capital=100_000.0,
        quantity=1,
        canonical_bars=bars,
        validation_run=validation_run,
        created_at=_COMPLETED_AT,
    )
    result: RunResult = build_run_result(
        manifest=prepared.manifest,
        trade_records=(),
        event_log=(),
        contract=contract,
        completed_at=_COMPLETED_AT,
    )
    store.persist(
        result,
        contract=contract,
        consumed_trading_dates=consumed_trading_dates_for_bars(
            bars,
            contract=contract,
            session_name="eth",
        ),
    )


def _copy_default_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build an isolated project root so no test can migrate or write a real artifact."""
    source_backtests = PROJECT_ROOT / "data" / "backtests"
    root = tmp_path / "project"
    config = root / "config"
    backtests = root / "data" / "backtests"
    config.mkdir(parents=True)
    backtests.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "config" / "contracts.yaml", config / "contracts.yaml")
    main = backtests / "runs.sqlite3"
    audit = backtests / "runs-3b4-audit.sqlite3"
    shutil.copy2(source_backtests / main.name, main)
    shutil.copy2(source_backtests / audit.name, audit)
    return root, main, audit


def _reset_copied_run_index_state(path: Path) -> None:
    """Remove only regenerable index state from an isolated copied main database."""
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE IF EXISTS run_index_proof")
        connection.execute("DROP TABLE IF EXISTS run_trading_dates")
        connection.execute("DROP TABLE IF EXISTS run_lookup")
        connection.commit()


def _configure_default_catalog(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Reset only test setup caches before redirecting the default API to an isolated root."""
    routes_run_references._default_audit_runs.cache_clear()
    routes_run_references._default_registry.cache_clear()
    monkeypatch.setattr(routes_run_references, "PROJECT_ROOT", root)


def _clear_default_catalog() -> None:
    routes_run_references._default_audit_runs.cache_clear()
    routes_run_references._default_registry.cache_clear()


def _source_state(path: Path) -> tuple[str, int, tuple[bool, bool, bool]]:
    """Capture every source-write signal that a read-only endpoint must preserve."""
    sidecars = tuple(
        path.with_name(f"{path.name}{suffix}").exists() for suffix in ("-journal", "-wal", "-shm")
    )
    return sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns, sidecars


def _ready_empty_main(main: Path, contract: ContractSpec) -> SqliteRunStore:
    """Replace an isolated copied main with a small ready source for tamper tests."""
    main.unlink()
    store = SqliteRunStore(main)
    _persist(
        store,
        contract,
        run_id="proof-target-001",
        strategy_version="proof-target-v1",
    )
    return store


@pytest.mark.asyncio
async def test_same_app_refreshes_main_for_strategy_duplicate_and_trading_date(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second GET sees a newly committed run without restart, cache clearing, or migration."""
    root, main, audit = _copy_default_sources(tmp_path)
    store = SqliteRunStore(main)
    store.migrate_run_indexes(registry=contracts_registry)
    contract = contracts_registry.by_symbol("NQ")
    before_main = _source_state(main)
    before_audit = _source_state(audit)
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            first = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "freshness-v1"},
            )
            assert first.status_code == 200
            assert first.json()["count"] == 0
            assert _source_state(main) == before_main
            assert _source_state(audit) == before_audit

            _persist(
                store,
                contract,
                run_id="freshness-standard-001",
                strategy_version="freshness-v1",
            )

            strategy = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "freshness-v1"},
            )
            duplicate = await client.get(
                "/api/v1/run-references",
                params={
                    "mode": "duplicate",
                    "strategy_version": "freshness-v1",
                    "symbol": "NQ",
                    "range_start": "2026-07-20T22:00:00Z",
                    "range_end": "2026-07-20T22:01:00Z",
                },
            )
            trading_date = await client.get(
                "/api/v1/run-references",
                params={
                    "mode": "trading-date",
                    "symbol": "NQ",
                    "trading_date": "2026-07-21",
                },
            )
    finally:
        _clear_default_catalog()

    assert strategy.status_code == duplicate.status_code == trading_date.status_code == 200
    assert [row["run_id"] for row in strategy.json()["runs"]] == ["freshness-standard-001"]
    assert [row["run_id"] for row in duplicate.json()["runs"]] == ["freshness-standard-001"]
    assert "freshness-standard-001" in {row["run_id"] for row in trading_date.json()["runs"]}
    assert _source_state(audit) == before_audit


@pytest.mark.asyncio
async def test_fresh_main_refresh_detects_cross_source_collision_after_first_get(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cached audit entry cannot hide a newly introduced duplicate main run identity."""
    root, main, _ = _copy_default_sources(tmp_path)
    store = SqliteRunStore(main)
    store.migrate_run_indexes(registry=contracts_registry)
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            first = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "trend-v0"},
            )
            assert first.status_code == 200
            _persist(
                store,
                contracts_registry.by_symbol("NQ"),
                run_id=_AUDIT_RUN_ID,
                strategy_version="collision-v1",
            )
            second = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "collision-v1"},
            )
    finally:
        _clear_default_catalog()

    assert second.status_code == 503
    assert second.json()["detail"] == (
        "run reference index is inconsistent; reconcile it before querying"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("validation_run", 1),
        ("strategy_version", "tampered-strategy"),
        ("strategy_content_sha256", "a" * 64),
        ("contract_id", "tampered-contract"),
        ("symbol", "GC"),
        ("session_name", "rth"),
        ("range_start", "2026-01-01T00:00:00Z"),
        ("manifest_sha256", "f" * 64),
    ],
)
async def test_runtime_proof_rejects_each_tampered_lookup_identity(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: int | str,
) -> None:
    """All identity fields are bound to the published immutable/derived proof at GET time."""
    root, main, _ = _copy_default_sources(tmp_path)
    _ready_empty_main(main, contracts_registry.by_symbol("NQ"))
    with sqlite3.connect(main) as connection:
        connection.execute(f"UPDATE run_lookup SET {field} = ?", (value,))
        connection.commit()
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "proof-target-v1"},
            )
    finally:
        _clear_default_catalog()

    assert response.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing_lookup", "orphan_lookup", "extra_date"])
async def test_runtime_proof_rejects_missing_or_extra_derived_rows(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    """A completed migration never treats a missing or extra derived row as an honest unknown."""
    root, main, _ = _copy_default_sources(tmp_path)
    _ready_empty_main(main, contracts_registry.by_symbol("NQ"))
    with sqlite3.connect(main) as connection:
        if corruption == "missing_lookup":
            connection.execute("DELETE FROM run_trading_dates WHERE run_id = 'proof-target-001'")
            connection.execute("DELETE FROM run_lookup WHERE run_id = 'proof-target-001'")
        elif corruption == "orphan_lookup":
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                """
                INSERT INTO run_lookup (
                    run_id, validation_run, strategy_version, strategy_content_sha256,
                    contract_id, symbol, session_name, range_start, range_end,
                    manifest_sha256, trading_dates_status, trading_dates_sha256
                ) VALUES (
                    'orphan-proof-001', 0, 'proof-target-v1', NULL,
                    'NQ-202609-CME', 'NQ', 'eth',
                    '2026-07-20T22:00:00Z', '2026-07-20T22:01:00Z', ?,
                    'unavailable', NULL
                )
                """,
                ("0" * 64,),
            )
        else:
            connection.execute(
                """
                INSERT INTO run_trading_dates (run_id, trading_date)
                VALUES ('proof-target-001', '2026-07-22')
                """
            )
        connection.commit()
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "proof-target-v1"},
            )
    finally:
        _clear_default_catalog()

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_partial_migration_without_publication_is_503_then_retry_succeeds(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema names alone never make a failed migration ready, and a retry remains safe."""
    root, main, _ = _copy_default_sources(tmp_path)
    _reset_copied_run_index_state(main)
    store = SqliteRunStore(main)
    original_reconcile = persistence_module._reconcile_and_backfill_run_indexes_on_connection

    def stop_after_schema(*args: object, **kwargs: object) -> None:
        raise RuntimeError("intentional partial migration interruption")

    monkeypatch.setattr(
        persistence_module,
        "_reconcile_and_backfill_run_indexes_on_connection",
        stop_after_schema,
    )
    with pytest.raises(RuntimeError, match="intentional partial"):
        store.migrate_run_indexes(registry=contracts_registry)
    with sqlite3.connect(main) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_index_proof'"
        ).fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM run_index_proof").fetchone() == (0,)

    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            incomplete = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "trend-v0"},
            )
            monkeypatch.setattr(
                persistence_module,
                "_reconcile_and_backfill_run_indexes_on_connection",
                original_reconcile,
            )
            store.migrate_run_indexes(registry=contracts_registry)
            retried = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "trend-v0"},
            )
    finally:
        _clear_default_catalog()

    assert incomplete.status_code == 503
    assert retried.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("malformation", ["proof_columns", "index_columns"])
async def test_malformed_required_schema_returns_503_not_generic_500(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
    malformation: str,
) -> None:
    """Malformed proof/index shape is an integrity response, never a server crash."""
    root, main, _ = _copy_default_sources(tmp_path)
    _ready_empty_main(main, contracts_registry.by_symbol("NQ"))
    with sqlite3.connect(main) as connection:
        if malformation == "proof_columns":
            connection.execute("DROP TABLE run_index_proof")
            connection.execute("CREATE TABLE run_index_proof (proof_id INTEGER PRIMARY KEY)")
        else:
            connection.execute("DROP INDEX run_lookup_by_standard_strategy")
            connection.execute(
                "CREATE INDEX run_lookup_by_standard_strategy ON run_lookup (run_id)"
            )
        connection.commit()
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "proof-target-v1"},
            )
    finally:
        _clear_default_catalog()

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_fresh_main_path_skips_manifest_parse_and_audit_bootstraps_once(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Main refresh reads derived rows only, while the immutable audit is parsed only once."""
    root, main, audit = _copy_default_sources(tmp_path)
    store = SqliteRunStore(main)
    store.migrate_run_indexes(registry=contracts_registry)
    before_audit = _source_state(audit)
    audit_reads = 0
    original_audit_read = catalog_module._read_audit_source

    def count_audit_reads(*args: object, **kwargs: object) -> object:
        nonlocal audit_reads
        audit_reads += 1
        return original_audit_read(*args, **kwargs)

    _configure_default_catalog(monkeypatch, root)
    monkeypatch.setattr(catalog_module, "_read_audit_source", count_audit_reads)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            first = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "manifest-free-v1"},
            )
            assert first.status_code == 200
            _persist(
                store,
                contracts_registry.by_symbol("NQ"),
                run_id="manifest-free-001",
                strategy_version="manifest-free-v1",
            )

            def forbid_manifest_parse(*args: object, **kwargs: object) -> object:
                raise AssertionError("fresh main GET must not parse runs.manifest_json")

            monkeypatch.setattr(
                catalog_module,
                "_lookup_values_from_stored_manifest",
                forbid_manifest_parse,
            )
            monkeypatch.setattr(
                persistence_module,
                "_current_run_index_proof",
                forbid_manifest_parse,
            )
            second = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "manifest-free-v1"},
            )
            third = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "manifest-free-v1"},
            )
    finally:
        _clear_default_catalog()

    assert second.status_code == third.status_code == 200
    assert [row["run_id"] for row in second.json()["runs"]] == ["manifest-free-001"]
    assert audit_reads == 1
    assert _source_state(audit) == before_audit
