"""Scheme-A regression coverage for the append-aware read-only run catalog."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest

from futures_research import cli
from futures_research.api import routes_run_references
from futures_research.api.main import app
from futures_research.backtest.persistence import (
    ImmutableArtifactError,
    RunIndexIntegrityError,
    SqliteRunStore,
)
from futures_research.backtest.records import (
    PreparedRun,
    RunResult,
    build_run_result,
    consumed_trading_dates_for_bars,
    prepare_run,
)
from futures_research.backtest.run_reference_catalog import RunReferenceCatalog
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.paths import PROJECT_ROOT

_COMPLETED_AT = datetime(2026, 7, 26, 12, tzinfo=UTC)
_RANGE_START = datetime(2026, 7, 20, 22, tzinfo=UTC)
_RANGE_END = _RANGE_START + timedelta(minutes=1)
_AUDIT_RUN_ID = "nq-20260724-3b4-audit"
_ACTIVATION_SMOKE_RUN_ID = "nq-20260728-standard-365adf"
_KNOWN_POST_ACTIVATION_RUN_ID = "nq-20260729-standard-e5808b"


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
) -> PreparedRun:
    """Persist one fully indexed test run so tests can remove only its derivative row."""
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
    return prepared


def _delete_lookup(path: Path, run_id: str) -> None:
    """Model a sparse legacy derivative without changing immutable run facts."""
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM run_trading_dates WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM run_lookup WHERE run_id = ?", (run_id,))
        connection.commit()


def _catalog_for(
    path: Path,
    registry: ContractRegistry,
) -> RunReferenceCatalog:
    return RunReferenceCatalog.from_sources(
        main_database=path,
        audit_database=None,
        registry=registry,
    )


def _source_state(path: Path) -> tuple[bytes, str, int, tuple[bool, bool, bool]]:
    """Capture byte, digest, mtime, and SQLite sidecar state for a protected source."""
    content = path.read_bytes()
    sidecars = tuple(
        path.with_name(f"{path.name}{suffix}").exists() for suffix in ("-journal", "-wal", "-shm")
    )
    return content, sha256(content).hexdigest(), path.stat().st_mtime_ns, sidecars


def _assert_source_unchanged(
    before: tuple[bytes, str, int, tuple[bool, bool, bool]],
    path: Path,
) -> None:
    """Require a catalog/GET operation to leave every protected source detail unchanged."""
    assert _source_state(path) == before


def _run_ids(path: Path) -> frozenset[str]:
    """Read one source's exact immutable run-ID set without opening it for writes."""
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return frozenset(
            str(row[0]) for row in connection.execute("SELECT run_id FROM runs ORDER BY run_id")
        )


def _run_index_truth(
    path: Path,
) -> tuple[tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]]:
    """Snapshot every derived lookup/date field as one exact, canonically ordered value."""
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        lookup_rows = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT run_id, validation_run, strategy_version, strategy_content_sha256,
                       contract_id, symbol, session_name, range_start, range_end,
                       manifest_sha256, trading_dates_status, trading_dates_sha256
                FROM run_lookup ORDER BY run_id
                """
            )
        )
        date_rows = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT run_id, trading_date "
                "FROM run_trading_dates ORDER BY run_id, trading_date"
            )
        )
    return lookup_rows, date_rows


def _immutable_truth_by_run_id(path: Path) -> dict[str, str]:
    """Fingerprint each immutable run together with its exact ordered trade rows."""
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        runs = connection.execute(
            """
            SELECT run_id, manifest_json, result_json, created_at, completed_at
            FROM runs ORDER BY run_id
            """
        ).fetchall()
        trades = connection.execute(
            """
            SELECT run_id, ordinal, trade_id, entry_timestamp, exit_timestamp, net_pnl,
                   record_json
            FROM trades ORDER BY run_id, ordinal
            """
        ).fetchall()
    trades_by_run: dict[str, list[dict[str, object]]] = {}
    for row in trades:
        run_id = str(row[0])
        trades_by_run.setdefault(run_id, []).append(
            {
                "ordinal": row[1],
                "trade_id": row[2],
                "entry_timestamp": row[3],
                "exit_timestamp": row[4],
                "net_pnl": row[5],
                "record": json.loads(row[6]),
            }
        )
    return {
        str(row[0]): sha256(
            _canonical_json(
                {
                    "run_id": row[0],
                    "manifest": json.loads(row[1]),
                    "result": json.loads(row[2]),
                    "created_at": row[3],
                    "completed_at": row[4],
                    "trades": trades_by_run.get(str(row[0]), []),
                }
            ).encode("utf-8")
        ).hexdigest()
        for row in runs
    }


def _immutable_truth(
    path: Path,
    *,
    excluded_run_ids: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Independently fingerprint the immutable tables and exact trigger definitions."""
    with sqlite3.connect(path) as connection:
        runs = connection.execute(
            """
            SELECT run_id, manifest_json, result_json, created_at, completed_at
            FROM runs ORDER BY run_id
            """
        ).fetchall()
        trades = connection.execute(
            """
            SELECT run_id, ordinal, trade_id, entry_timestamp, exit_timestamp, net_pnl, record_json
            FROM trades ORDER BY run_id, ordinal
            """
        ).fetchall()
        triggers = connection.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'trigger'
              AND name IN (
                  'runs_are_immutable', 'runs_cannot_be_deleted',
                  'trades_are_immutable', 'trades_cannot_be_deleted'
              )
            ORDER BY name
            """
        ).fetchall()

    canonical_runs = [
        {
            "run_id": row[0],
            "manifest": json.loads(row[1]),
            "result": json.loads(row[2]),
            "created_at": row[3],
            "completed_at": row[4],
        }
        for row in runs
        if str(row[0]) not in excluded_run_ids
    ]
    canonical_trades = [
        {
            "run_id": row[0],
            "ordinal": row[1],
            "trade_id": row[2],
            "entry_timestamp": row[3],
            "exit_timestamp": row[4],
            "net_pnl": row[5],
            "record": json.loads(row[6]),
        }
        for row in trades
        if str(row[0]) not in excluded_run_ids
    ]
    return {
        "run_count": len(canonical_runs),
        "trade_count": len(canonical_trades),
        "run_ids": tuple(str(run["run_id"]) for run in canonical_runs),
        "runs_sha256": sha256(_canonical_json(canonical_runs).encode("utf-8")).hexdigest(),
        "trades_sha256": sha256(_canonical_json(canonical_trades).encode("utf-8")).hexdigest(),
        "triggers": tuple((str(name), str(sql)) for name, sql in triggers),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _copy_default_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a temporary project root with the exact, explicitly named source pair."""
    source_backtests = PROJECT_ROOT / "data" / "backtests"
    root = tmp_path / "project"
    config = root / "config"
    backtests = root / "data" / "backtests"
    config.mkdir(parents=True)
    backtests.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "config" / "contracts.yaml", config / "contracts.yaml")
    main = backtests / "runs.sqlite3"
    audit = backtests / "runs-3b4-audit.sqlite3"
    shutil.copy2(source_backtests / "runs.sqlite3", main)
    shutil.copy2(source_backtests / "runs-3b4-audit.sqlite3", audit)
    return root, main, audit


def _reset_copied_run_index_state(path: Path) -> None:
    """Remove only regenerable index state from an isolated copied main database."""
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE IF EXISTS run_index_proof")
        connection.execute("DROP TABLE IF EXISTS run_trading_dates")
        connection.execute("DROP TABLE IF EXISTS run_lookup")
        connection.commit()


def _configure_default_catalog(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point the default API catalog at a temporary project and clear all cached state."""
    routes_run_references._default_audit_runs.cache_clear()
    routes_run_references._default_registry.cache_clear()
    monkeypatch.setattr(routes_run_references, "PROJECT_ROOT", root)


def _clear_default_catalog() -> None:
    routes_run_references._default_audit_runs.cache_clear()
    routes_run_references._default_registry.cache_clear()


def test_migrated_missing_lookup_is_a_runtime_integrity_failure(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """Completed migration never downgrades a deleted lookup row into an unknown candidate."""
    contract = contracts_registry.by_symbol("NQ")
    store = SqliteRunStore(tmp_path / "runs.sqlite3")
    _persist(store, contract, run_id="target", strategy_version="strategy-target")
    _persist(
        store,
        contract,
        run_id="validation-missing",
        strategy_version="strategy-target",
        validation_run=True,
    )
    _persist(
        store,
        contract,
        run_id="unrelated-missing",
        strategy_version="strategy-other",
    )
    _delete_lookup(store.path, "validation-missing")

    with pytest.raises(RunIndexIntegrityError, match="proof"):
        _catalog_for(store.path, contracts_registry)


def test_duplicate_null_symbol_is_unknown_but_irrelevant_and_validation_rows_are_excluded(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """An exact strategy/range with unproven symbol prevents only that duplicate zero claim."""
    contract = contracts_registry.by_symbol("NQ")
    unknown_contract = contract.model_copy(
        update={"contract_id": "NQ-UNKNOWN-CME", "symbol": "NQ-UNKNOWN"}
    )
    store = SqliteRunStore(tmp_path / "runs.sqlite3")
    _persist(store, contract, run_id="known-match", strategy_version="strategy-target")
    _persist(
        store,
        unknown_contract,
        run_id="unknown-symbol",
        strategy_version="strategy-target",
    )
    _persist(
        store,
        unknown_contract,
        run_id="validation-unknown-symbol",
        strategy_version="strategy-target",
        validation_run=True,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE run_trading_dates")
        connection.execute("DROP TABLE run_lookup")
        connection.execute("DROP TABLE run_index_proof")
        connection.commit()
    store.migrate_run_indexes(registry=contracts_registry)
    catalog = _catalog_for(store.path, contracts_registry)

    exact = catalog.references_for_duplicate(
        strategy_version="strategy-target",
        symbol="NQ",
        range_start="2026-07-20T22:00:00Z",
        range_end="2026-07-20T22:01:00Z",
    ).to_document()
    assert exact["count_known"] is False
    assert exact["count"] is None
    assert exact["known_match_count"] == 1
    assert [row["run_id"] for row in exact["runs"]] == ["known-match"]
    assert exact["unindexed_candidates"] == [
        {"run_id": "unknown-symbol", "reason": "root_symbol_unavailable"}
    ]

    different_strategy = catalog.references_for_duplicate(
        strategy_version="strategy-other",
        symbol="NQ",
        range_start="2026-07-20T22:00:00Z",
        range_end="2026-07-20T22:01:00Z",
    ).to_document()
    assert different_strategy["count_known"] is True
    assert different_strategy["unindexed_candidates"] == []
    different_range = catalog.references_for_duplicate(
        strategy_version="strategy-target",
        symbol="NQ",
        range_start="2026-07-20T22:01:00Z",
        range_end="2026-07-20T22:02:00Z",
    ).to_document()
    assert different_range["count_known"] is True
    assert different_range["unindexed_candidates"] == []


def test_catalog_rejects_tampered_immutable_manifest_and_missing_lookup(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """Runtime proof rejects a source whose immutable facts or derived rows were tampered."""
    contract = contracts_registry.by_symbol("NQ")
    store = SqliteRunStore(tmp_path / "runs.sqlite3")
    _persist(store, contract, run_id="tampered-index", strategy_version="strategy-target")
    with sqlite3.connect(store.path) as connection:
        manifest = json.loads(
            connection.execute(
                "SELECT manifest_json FROM runs WHERE run_id = ?", ("tampered-index",)
            ).fetchone()[0]
        )
        manifest["run_id"] = "other-run-id"
        connection.execute("DELETE FROM run_trading_dates WHERE run_id = ?", ("tampered-index",))
        connection.execute("DELETE FROM run_lookup WHERE run_id = ?", ("tampered-index",))
        connection.execute("DROP TRIGGER runs_are_immutable")
        connection.execute(
            "UPDATE runs SET manifest_json = ? WHERE run_id = ?",
            (_canonical_json(manifest), "tampered-index"),
        )
        connection.commit()

    with pytest.raises(RunIndexIntegrityError, match="proof"):
        _catalog_for(store.path, contracts_registry)


def test_explicit_migration_is_idempotent_and_preserves_immutable_truth(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """Only an explicit migration writes a main copy, and it preserves all protected facts."""
    source_root = PROJECT_ROOT / "data" / "backtests"
    source = source_root / "runs.sqlite3"
    backup = source_root / "runs.sqlite3.pre-migration-backup-20260728"
    source_before = _source_state(source)
    backup_before = _source_state(backup)
    opening_main_run_ids = _run_ids(source)
    pre_activation_run_ids = _run_ids(backup)
    post_activation_run_ids = opening_main_run_ids - pre_activation_run_ids
    appended_run_ids = post_activation_run_ids - {_ACTIVATION_SMOKE_RUN_ID}
    opening_index_truth = _run_index_truth(source)
    main_copy = tmp_path / "runs.sqlite3"
    shutil.copy2(source, main_copy)
    before = _immutable_truth(main_copy)
    pre_activation = _immutable_truth(backup)
    opening_truth_by_id = _immutable_truth_by_run_id(main_copy)
    pre_activation_truth_by_id = _immutable_truth_by_run_id(backup)

    store = SqliteRunStore(main_copy)
    first = store.migrate_run_indexes(registry=contracts_registry)
    second = store.migrate_run_indexes(registry=contracts_registry)
    migrated_lookup_rows, migrated_date_rows = _run_index_truth(main_copy)
    migrated_truth_by_id = _immutable_truth_by_run_id(main_copy)
    migrated_status_by_id = {
        str(row[0]): str(row[10])
        for row in migrated_lookup_rows
    }

    assert first == second
    assert pre_activation["run_count"] == 15
    assert frozenset(pre_activation["run_ids"]) == pre_activation_run_ids
    assert pre_activation_run_ids < opening_main_run_ids
    assert _ACTIVATION_SMOKE_RUN_ID in post_activation_run_ids
    assert _KNOWN_POST_ACTIVATION_RUN_ID in appended_run_ids
    assert {
        run_id: opening_truth_by_id[run_id]
        for run_id in pre_activation_run_ids
    } == pre_activation_truth_by_id
    assert set(before["run_ids"]) == opening_main_run_ids
    assert first.run_count == before["run_count"] == len(opening_main_run_ids)
    assert first.trade_count == before["trade_count"]
    assert first.runs_sha256 == before["runs_sha256"]
    assert first.trades_sha256 == before["trades_sha256"]
    assert _immutable_truth(main_copy) == before
    assert migrated_truth_by_id == opening_truth_by_id
    assert migrated_truth_by_id[_ACTIVATION_SMOKE_RUN_ID] == opening_truth_by_id[
        _ACTIVATION_SMOKE_RUN_ID
    ]
    assert {
        run_id: migrated_truth_by_id[run_id]
        for run_id in appended_run_ids
    } == {
        run_id: opening_truth_by_id[run_id]
        for run_id in appended_run_ids
    }
    assert (migrated_lookup_rows, migrated_date_rows) == opening_index_truth
    assert set(migrated_status_by_id) == opening_main_run_ids
    assert {
        run_id
        for run_id, status in migrated_status_by_id.items()
        if status == "unavailable"
    } == pre_activation_run_ids
    assert {
        run_id
        for run_id, status in migrated_status_by_id.items()
        if status == "complete"
    } == post_activation_run_ids
    assert {str(row[0]) for row in migrated_date_rows} == post_activation_run_ids
    with sqlite3.connect(main_copy) as connection:
        lookup_run_ids = {
            str(row[0])
            for row in connection.execute("SELECT run_id FROM run_lookup").fetchall()
        }
        assert lookup_run_ids == opening_main_run_ids
        assert connection.execute("SELECT COUNT(*) FROM run_index_proof").fetchone()[0] == 1
    _assert_source_unchanged(source_before, source)
    _assert_source_unchanged(backup_before, backup)


def test_migration_requires_all_four_existing_immutable_triggers(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """A migration refuses to recreate a missing original trigger as though nothing happened."""
    main_copy = tmp_path / "runs.sqlite3"
    shutil.copy2(PROJECT_ROOT / "data" / "backtests" / "runs.sqlite3", main_copy)
    with sqlite3.connect(main_copy) as connection:
        connection.execute("DROP TRIGGER runs_cannot_be_deleted")
        connection.commit()

    with pytest.raises(RunIndexIntegrityError, match="four existing immutable"):
        SqliteRunStore(main_copy).migrate_run_indexes(registry=contracts_registry)


def test_migration_refuses_an_audit_named_source_without_changing_it(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """The explicit main migration cannot be accidentally aimed at the protected audit file."""
    audit_copy = tmp_path / "runs-3b4-audit.sqlite3"
    shutil.copy2(PROJECT_ROOT / "data" / "backtests" / audit_copy.name, audit_copy)
    before = _source_state(audit_copy)

    with pytest.raises(ImmutableArtifactError, match="forbidden"):
        SqliteRunStore(audit_copy).migrate_run_indexes(registry=contracts_registry)

    _assert_source_unchanged(before, audit_copy)


def test_cli_migration_command_exercises_only_a_temporary_main_copy(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The documented operator command is an explicit action, never a GET side effect."""
    source = PROJECT_ROOT / "data" / "backtests" / "runs.sqlite3"
    source_before = _source_state(source)
    opening_main_run_ids = _run_ids(source)
    opening_truth = _immutable_truth(source)
    main_copy = tmp_path / "runs.sqlite3"
    shutil.copy2(source, main_copy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "futures-research",
            "--contracts-config",
            str(PROJECT_ROOT / "config" / "contracts.yaml"),
            "--runs-db",
            str(main_copy),
            "migrate-run-index",
        ],
    )

    cli.main()

    document = json.loads(capsys.readouterr().out)
    assert document["database"] == str(main_copy)
    assert document["run_count"] == len(opening_main_run_ids)
    assert document["trade_count"] == opening_truth["trade_count"]
    assert document["runs_sha256"] == opening_truth["runs_sha256"]
    assert document["trades_sha256"] == opening_truth["trades_sha256"]
    assert _run_ids(main_copy) == opening_main_run_ids
    migrated_lookup_rows, _ = _run_index_truth(main_copy)
    assert {str(row[0]) for row in migrated_lookup_rows} == opening_main_run_ids
    assert _immutable_truth(main_copy) == opening_truth
    assert contracts_registry.by_symbol("NQ").symbol == "NQ"
    _assert_source_unchanged(source_before, source)


def test_catalog_unites_migrated_main_with_audit_without_mutating_audit(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """An audit-format source adds one standard run through a strictly read-only catalog."""
    source_root = PROJECT_ROOT / "data" / "backtests"
    source_main = source_root / "runs.sqlite3"
    source_audit = source_root / "runs-3b4-audit.sqlite3"
    source_main_before = _source_state(source_main)
    source_audit_before = _source_state(source_audit)
    opening_main_run_ids = _run_ids(source_main)
    audit_run_ids = _run_ids(source_audit)
    expected_catalog_run_ids = tuple(sorted(opening_main_run_ids | audit_run_ids))
    main_copy = tmp_path / "runs.sqlite3"
    audit = tmp_path / "runs-3b4-audit.sqlite3"
    shutil.copy2(source_main, main_copy)
    shutil.copy2(source_audit, audit)
    SqliteRunStore(main_copy).migrate_run_indexes(registry=contracts_registry)
    before_audit = _source_state(audit)

    catalog = RunReferenceCatalog.from_sources(
        main_database=main_copy,
        audit_database=audit,
        registry=contracts_registry,
    )
    strategy = catalog.references_for_strategy(strategy_version="trend-v0").to_document()
    duplicate = catalog.references_for_duplicate(
        strategy_version="trend-v0",
        symbol="NQ",
        range_start="2026-04-06T22:00:00Z",
        range_end="2026-07-23T21:00:00Z",
    ).to_document()
    overlap = catalog.references_for_trading_date(
        symbol="NQ",
        trading_date=date(2026, 7, 23),
        contract=contracts_registry.by_symbol("NQ"),
    ).to_document()
    non_overlap = catalog.references_for_trading_date(
        symbol="NQ",
        trading_date=date(2026, 1, 5),
        contract=contracts_registry.by_symbol("NQ"),
    ).to_document()

    assert catalog.run_ids == expected_catalog_run_ids
    assert len(catalog.run_ids) == len(set(catalog.run_ids))
    assert _AUDIT_RUN_ID in catalog.run_ids
    assert _ACTIVATION_SMOKE_RUN_ID in catalog.run_ids
    assert _KNOWN_POST_ACTIVATION_RUN_ID in catalog.run_ids
    assert [row["run_id"] for row in strategy["runs"]] == [
        "gc-20260724-3b4-001",
        "nq-20260723-eth-smoke-001",
        "nq-20260724-3b4-001",
        _AUDIT_RUN_ID,
    ]
    assert [row["run_id"] for row in duplicate["runs"]] == [
        "nq-20260724-3b4-001",
        _AUDIT_RUN_ID,
    ]
    assert {row["run_id"] for row in overlap["unindexed_candidates"]} >= {_AUDIT_RUN_ID}
    assert {row["run_id"] for row in non_overlap["unindexed_candidates"]}.isdisjoint(
        {_AUDIT_RUN_ID}
    )
    _assert_source_unchanged(before_audit, audit)
    _assert_source_unchanged(source_main_before, source_main)
    _assert_source_unchanged(source_audit_before, source_audit)


@pytest.mark.asyncio
async def test_default_get_unmigrated_main_returns_503_without_writing_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal endpoint never creates schema, a directory, or SQLite sidecars on GET."""
    root, main, audit = _copy_default_sources(tmp_path)
    _reset_copied_run_index_state(main)
    before_main = _source_state(main)
    before_audit = _source_state(audit)
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "trend-v0"},
            )
    finally:
        _clear_default_catalog()

    assert response.status_code == 503
    assert "migrate-run-index" in response.json()["detail"]
    _assert_source_unchanged(before_main, main)
    _assert_source_unchanged(before_audit, audit)


@pytest.mark.asyncio
async def test_default_catalog_gets_read_both_sources_without_writing_after_explicit_migration(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All default GET modes use the append-aware source union and leave both sources alone."""
    root, main, audit = _copy_default_sources(tmp_path)
    SqliteRunStore(main).migrate_run_indexes(registry=contracts_registry)
    before_main = _source_state(main)
    before_audit = _source_state(audit)
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            strategy = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "trend-v0"},
            )
            duplicate = await client.get(
                "/api/v1/run-references",
                params={
                    "mode": "duplicate",
                    "strategy_version": "trend-v0",
                    "symbol": "NQ",
                    "range_start": "2026-04-06T22:00:00Z",
                    "range_end": "2026-07-23T21:00:00Z",
                },
            )
            trading_date = await client.get(
                "/api/v1/run-references",
                params={"mode": "trading-date", "symbol": "NQ", "trading_date": "2026-07-23"},
            )
    finally:
        _clear_default_catalog()

    assert strategy.status_code == duplicate.status_code == trading_date.status_code == 200
    assert _AUDIT_RUN_ID in [row["run_id"] for row in strategy.json()["runs"]]
    assert _AUDIT_RUN_ID in [row["run_id"] for row in duplicate.json()["runs"]]
    assert _AUDIT_RUN_ID in {row["run_id"] for row in trading_date.json()["unindexed_candidates"]}
    _assert_source_unchanged(before_main, main)
    _assert_source_unchanged(before_audit, audit)


@pytest.mark.asyncio
async def test_default_catalog_rejects_duplicate_run_ids_across_explicit_sources(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No payload comparison can silently deduplicate an identity collision between source files."""
    root, main, audit = _copy_default_sources(tmp_path)
    SqliteRunStore(main).migrate_run_indexes(registry=contracts_registry)
    shutil.copy2(main, audit)
    _configure_default_catalog(monkeypatch, root)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/v1/run-references",
                params={"mode": "strategy", "strategy_version": "trend-v0"},
            )
    finally:
        _clear_default_catalog()

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "run reference index is inconsistent; reconcile it before querying"
    )
