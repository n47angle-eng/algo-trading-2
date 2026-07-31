"""Correction-C regression coverage for complete proof publication checksums."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from futures_research.api import routes_run_references
from futures_research.api.main import app
from futures_research.backtest import persistence as persistence_module
from futures_research.backtest.persistence import (
    SqliteRunStore,
    _run_index_proof_from_row,
    _run_index_proof_publication_payload,
    _run_index_proof_publication_sha256,
)
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
_PROOF_COLUMNS = (
    "proof_id",
    "schema_version",
    "run_count",
    "trade_count",
    "lookup_count",
    "trading_date_count",
    "immutable_runs_sha256",
    "immutable_trades_sha256",
    "immutable_triggers_sha256",
    "derived_sha256",
    "publication_sha256",
)


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
    start: datetime = _RANGE_START,
) -> None:
    """Publish one complete standard run through the same transaction used in production."""
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


def _copy_default_sources(tmp_path: Path) -> tuple[Path, Path]:
    """Build an isolated default-source root so every migration stays temporary."""
    root = tmp_path / "project"
    config = root / "config"
    backtests = root / "data" / "backtests"
    config.mkdir(parents=True)
    backtests.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "config" / "contracts.yaml", config / "contracts.yaml")
    for name in ("runs.sqlite3", "runs-3b4-audit.sqlite3"):
        shutil.copy2(PROJECT_ROOT / "data" / "backtests" / name, backtests / name)
    return root, backtests / "runs.sqlite3"


def _reset_copied_run_index_state(path: Path) -> None:
    """Remove only regenerable index state from an isolated copied main database."""
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE IF EXISTS run_index_proof")
        connection.execute("DROP TABLE IF EXISTS run_trading_dates")
        connection.execute("DROP TABLE IF EXISTS run_lookup")
        connection.commit()


def _configure_default_catalog(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point the API only at the isolated source pair and clear setup-time caches."""
    routes_run_references._default_audit_runs.cache_clear()
    routes_run_references._default_registry.cache_clear()
    monkeypatch.setattr(routes_run_references, "PROJECT_ROOT", root)


def _clear_default_catalog() -> None:
    routes_run_references._default_audit_runs.cache_clear()
    routes_run_references._default_registry.cache_clear()


def _ready_main(main: Path, contract: ContractSpec) -> SqliteRunStore:
    """Replace the isolated main with a small proof-published source."""
    main.unlink()
    store = SqliteRunStore(main)
    _persist(
        store,
        contract,
        run_id="checksum-target-001",
        strategy_version="checksum-target-v1",
    )
    return store


async def _strategy_response(
    *,
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    strategy_version: str = "checksum-target-v1",
) -> httpx.Response:
    """Issue one isolated API request without retaining a cache between tests."""
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": strategy_version},
            )
    finally:
        _clear_default_catalog()


def _proof_row(main: Path) -> sqlite3.Row:
    """Read the one temporary proof row for exact payload assertions."""
    connection = sqlite3.connect(main)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            f"SELECT {', '.join(_PROOF_COLUMNS)} FROM run_index_proof"
        ).fetchone()
        assert row is not None
        return row
    finally:
        connection.close()


def _replace_with_old_proof_schema(main: Path) -> None:
    """Simulate the f4c5530 derivative proof table without touching immutable rows."""
    connection = sqlite3.connect(main)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            f"SELECT {', '.join(_PROOF_COLUMNS[:-1])} FROM run_index_proof"
        ).fetchone()
        assert row is not None
        connection.execute("DROP TABLE run_index_proof")
        connection.execute(
            """
            CREATE TABLE run_index_proof (
                proof_id INTEGER PRIMARY KEY CHECK (proof_id = 1),
                schema_version INTEGER NOT NULL,
                run_count INTEGER NOT NULL,
                trade_count INTEGER NOT NULL,
                lookup_count INTEGER NOT NULL,
                trading_date_count INTEGER NOT NULL,
                immutable_runs_sha256 TEXT NOT NULL,
                immutable_trades_sha256 TEXT NOT NULL,
                immutable_triggers_sha256 TEXT NOT NULL,
                derived_sha256 TEXT NOT NULL
            )
            """
        )
        values = [row[column] for column in _PROOF_COLUMNS[:-1]]
        values[_PROOF_COLUMNS.index("schema_version")] = 1
        connection.execute(
            f"INSERT INTO run_index_proof ({', '.join(_PROOF_COLUMNS[:-1])}) "
            f"VALUES ({', '.join('?' for _ in _PROOF_COLUMNS[:-1])})",
            values,
        )
        connection.commit()
    finally:
        connection.close()


def _replace_with_nullable_publication_checksum(main: Path) -> None:
    """Create a malformed-but-column-complete temporary proof row with a NULL checksum."""
    row = _proof_row(main)
    connection = sqlite3.connect(main)
    try:
        connection.execute("DROP TABLE run_index_proof")
        connection.execute(
            """
            CREATE TABLE run_index_proof (
                proof_id INTEGER PRIMARY KEY,
                schema_version INTEGER,
                run_count INTEGER,
                trade_count INTEGER,
                lookup_count INTEGER,
                trading_date_count INTEGER,
                immutable_runs_sha256 TEXT,
                immutable_trades_sha256 TEXT,
                immutable_triggers_sha256 TEXT,
                derived_sha256 TEXT,
                publication_sha256 TEXT
            )
            """
        )
        values = [row[column] for column in _PROOF_COLUMNS]
        values[-1] = None
        connection.execute(
            f"INSERT INTO run_index_proof ({', '.join(_PROOF_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _PROOF_COLUMNS)})",
            values,
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_valid_publication_checksum_binds_ordered_payload_and_all_modes(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ready proof serves all modes and binds exactly the approved ten-field payload."""
    root, main = _copy_default_sources(tmp_path)
    _ready_main(main, contracts_registry.by_symbol("NQ"))
    proof, checksum = _run_index_proof_from_row(_proof_row(main))

    assert checksum == _run_index_proof_publication_sha256(proof)
    assert _run_index_proof_publication_payload(proof) == {
        "domain": "run-index-proof.v1",
        "fields": [
            ["proof_id", 1],
            ["schema_version", 2],
            ["run_count", 1],
            ["trade_count", 0],
            ["lookup_count", 1],
            ["trading_date_count", 1],
            ["immutable_runs_sha256", proof.immutable_runs_sha256],
            ["immutable_trades_sha256", proof.immutable_trades_sha256],
            ["immutable_triggers_sha256", proof.immutable_triggers_sha256],
            ["derived_sha256", proof.derived_sha256],
        ],
    }

    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            strategy = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "checksum-target-v1"},
            )
            duplicate = await client.get(
                "/api/v1/run-references",
                params={
                    "mode": "duplicate",
                    "strategy_version": "checksum-target-v1",
                    "symbol": "NQ",
                    "range_start": "2026-07-20T22:00:00Z",
                    "range_end": "2026-07-20T22:01:00Z",
                },
            )
            trading_date = await client.get(
                "/api/v1/run-references",
                params={"mode": "trading-date", "symbol": "NQ", "trading_date": "2026-07-21"},
            )
    finally:
        _clear_default_catalog()

    assert strategy.status_code == duplicate.status_code == trading_date.status_code == 200
    assert [row["run_id"] for row in strategy.json()["runs"]] == ["checksum-target-001"]
    assert [row["run_id"] for row in duplicate.json()["runs"]] == ["checksum-target-001"]
    assert "checksum-target-001" in {row["run_id"] for row in trading_date.json()["runs"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ("immutable_runs_sha256", "immutable_trades_sha256", "publication_sha256"),
)
async def test_runtime_rejects_each_checksum_bound_proof_field(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    """Format-valid proof-hash corruption cannot pass runtime verification anymore."""
    root, main = _copy_default_sources(tmp_path)
    _ready_main(main, contracts_registry.by_symbol("NQ"))
    with sqlite3.connect(main) as connection:
        connection.execute(f"UPDATE run_index_proof SET {field} = ?", ("0" * 64,))
        connection.commit()

    response = await _strategy_response(monkeypatch=monkeypatch, root=root)

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "run reference index is inconsistent; reconcile it before querying"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("malformation", ("missing", "null", "uppercase"))
async def test_missing_null_or_nonlowercase_publication_checksum_is_503(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
    malformation: str,
) -> None:
    """Checksum shape failures are integrity 503s rather than generic API failures."""
    root, main = _copy_default_sources(tmp_path)
    _ready_main(main, contracts_registry.by_symbol("NQ"))
    if malformation == "missing":
        _replace_with_old_proof_schema(main)
    elif malformation == "null":
        _replace_with_nullable_publication_checksum(main)
    else:
        with sqlite3.connect(main) as connection:
            connection.execute("UPDATE run_index_proof SET publication_sha256 = ?", ("A" * 64,))
            connection.commit()

    response = await _strategy_response(monkeypatch=monkeypatch, root=root)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_new_run_refreshes_proof_checksum_and_same_process_get(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful append replaces its proof payload and checksum in the same transaction."""
    root, main = _copy_default_sources(tmp_path)
    store = _ready_main(main, contracts_registry.by_symbol("NQ"))
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            first = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "checksum-fresh-v1"},
            )
            assert first.status_code == 200
            _persist(
                store,
                contracts_registry.by_symbol("NQ"),
                run_id="checksum-fresh-001",
                strategy_version="checksum-fresh-v1",
            )
            second = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "checksum-fresh-v1"},
            )
    finally:
        _clear_default_catalog()

    proof, checksum = _run_index_proof_from_row(_proof_row(main))
    assert checksum == _run_index_proof_publication_sha256(proof)
    assert proof.run_count == proof.lookup_count == 2
    assert second.status_code == 200
    assert [row["run_id"] for row in second.json()["runs"]] == ["checksum-fresh-001"]


def test_publish_failure_rolls_back_run_lookup_dates_and_proof(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure before checksum publication cannot leave a partial append behind."""
    store = SqliteRunStore(tmp_path / "runs.sqlite3")

    def fail_publish(*args: object, **kwargs: object) -> None:
        raise RuntimeError("intentional proof publication failure")

    monkeypatch.setattr(persistence_module, "_publish_run_index_proof", fail_publish)
    with pytest.raises(RuntimeError, match="intentional proof publication"):
        _persist(
            store,
            contracts_registry.by_symbol("NQ"),
            run_id="rollback-checksum-001",
            strategy_version="rollback-checksum-v1",
        )

    with sqlite3.connect(store.path) as connection:
        for table in ("runs", "trades", "run_lookup", "run_trading_dates", "run_index_proof"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)


@pytest.mark.asyncio
async def test_partial_migration_without_checksum_is_503_then_retry_succeeds(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A migration interrupted before checksum publication is never a ready source."""
    root, main = _copy_default_sources(tmp_path)
    _reset_copied_run_index_state(main)
    store = SqliteRunStore(main)
    original_publish = persistence_module._publish_run_index_proof

    def fail_publish(*args: object, **kwargs: object) -> None:
        raise RuntimeError("intentional migration publication failure")

    monkeypatch.setattr(persistence_module, "_publish_run_index_proof", fail_publish)
    with pytest.raises(RuntimeError, match="intentional migration publication"):
        store.migrate_run_indexes(registry=contracts_registry)
    with sqlite3.connect(main) as connection:
        assert connection.execute("SELECT COUNT(*) FROM run_index_proof").fetchone() == (0,)

    incomplete = await _strategy_response(
        monkeypatch=monkeypatch,
        root=root,
        strategy_version="trend-v0",
    )
    monkeypatch.setattr(persistence_module, "_publish_run_index_proof", original_publish)
    store.migrate_run_indexes(registry=contracts_registry)
    retried = await _strategy_response(
        monkeypatch=monkeypatch,
        root=root,
        strategy_version="trend-v0",
    )

    assert incomplete.status_code == 503
    assert retried.status_code == 200


@pytest.mark.asyncio
async def test_old_proof_schema_is_503_until_explicit_derivative_upgrade(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only explicit migration upgrades the old proof table; immutable truth stays unchanged."""
    root, main = _copy_default_sources(tmp_path)
    store = SqliteRunStore(main)
    store.migrate_run_indexes(registry=contracts_registry)
    before = persistence_module._immutable_run_truth_snapshot(main)
    _replace_with_old_proof_schema(main)

    before_upgrade = await _strategy_response(
        monkeypatch=monkeypatch,
        root=root,
        strategy_version="trend-v0",
    )
    store.migrate_run_indexes(registry=contracts_registry)
    after = persistence_module._immutable_run_truth_snapshot(main)
    after_upgrade = await _strategy_response(
        monkeypatch=monkeypatch,
        root=root,
        strategy_version="trend-v0",
    )
    with sqlite3.connect(main) as connection:
        names = {row[1] for row in connection.execute("PRAGMA table_info(run_index_proof)")}
        publication = connection.execute(
            "SELECT publication_sha256 FROM run_index_proof"
        ).fetchone()

    assert before_upgrade.status_code == 503
    assert after_upgrade.status_code == 200
    assert before == after
    assert "publication_sha256" in names
    assert publication is not None and len(publication[0]) == 64
