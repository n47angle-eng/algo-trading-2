"""Batch-2 proof for derived, fail-closed run-reference indexes."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from futures_research.backtest.persistence import RunIndexIntegrityError, SqliteRunStore
from futures_research.backtest.records import (
    PreparedRun,
    RunResult,
    StrategyBinding,
    build_run_result,
    consumed_trading_dates_for_bars,
    prepare_run,
    trading_date_for_bar,
)
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.paths import PROJECT_ROOT

_COMPLETED_AT = datetime(2026, 7, 26, 12, tzinfo=UTC)
_ACTIVATION_SMOKE_RUN_ID = "nq-20260728-standard-365adf"
_KNOWN_POST_ACTIVATION_RUN_ID = "nq-20260729-standard-e5808b"


def _source_state(path: Path) -> tuple[bytes, str, int, tuple[bool, bool, bool]]:
    """Capture exact protected-source bytes, digest, mtime, and SQLite sidecars."""
    content = path.read_bytes()
    sidecars = tuple(
        path.with_name(f"{path.name}{suffix}").exists() for suffix in ("-journal", "-wal", "-shm")
    )
    return content, sha256(content).hexdigest(), path.stat().st_mtime_ns, sidecars


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


def _bar(contract: ContractSpec, timestamp: datetime) -> CanonicalBar:
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


def _prepared(
    contract: ContractSpec,
    *,
    run_id: str,
    strategy_version: str = "strategy-0003",
    validation_run: bool = False,
    bars: tuple[CanonicalBar, ...] | None = None,
    strategy_binding: StrategyBinding | None = None,
) -> PreparedRun:
    input_bars = bars or (
        _bar(contract, datetime(2026, 7, 20, 22, tzinfo=UTC)),
        _bar(contract, datetime(2026, 7, 20, 22, 1, tzinfo=UTC)),
    )
    return prepare_run(
        run_id=run_id,
        strategy_version=strategy_version,
        contract=contract,
        session_name="eth",
        range_start=input_bars[0].timestamp,
        range_end=input_bars[-1].timestamp + timedelta(minutes=1),
        initial_capital=100_000.0,
        quantity=1,
        canonical_bars=input_bars,
        validation_run=validation_run,
        strategy_binding=strategy_binding,
        created_at=_COMPLETED_AT,
    )


def _result(contract: ContractSpec, prepared: PreparedRun) -> RunResult:
    return build_run_result(
        manifest=prepared.manifest,
        trade_records=(),
        event_log=(),
        contract=contract,
        completed_at=_COMPLETED_AT,
    )


def _persist(
    store: SqliteRunStore,
    contract: ContractSpec,
    *,
    run_id: str,
    strategy_version: str = "strategy-0003",
    validation_run: bool = False,
    bars: tuple[CanonicalBar, ...] | None = None,
    strategy_binding: StrategyBinding | None = None,
) -> PreparedRun:
    prepared = _prepared(
        contract,
        run_id=run_id,
        strategy_version=strategy_version,
        validation_run=validation_run,
        bars=bars,
        strategy_binding=strategy_binding,
    )
    store.persist(
        _result(contract, prepared),
        contract=contract,
        consumed_trading_dates=consumed_trading_dates_for_bars(
            prepared.bars,
            contract=contract,
            session_name=prepared.manifest.session_name,
        ),
    )
    return prepared


def test_new_lookup_copies_exact_manifest_identity_and_strategy_digest(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """A new index row records immutable facts without parsing identifiers heuristically."""
    contract = contracts_registry.by_symbol("NQ")
    strategy_hash = "a" * 64
    binding = StrategyBinding(
        source="strategy_file",
        strategy_id="strategy-0003",
        strategy_name="Fixture strategy",
        content_sha256=strategy_hash,
        universe_contracts=(contract.contract_id,),
    )
    store = SqliteRunStore(tmp_path / "identity.sqlite3")
    prepared = _persist(
        store,
        contract,
        run_id="identity-001",
        strategy_binding=binding,
    )

    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            """
            SELECT validation_run, strategy_version, strategy_content_sha256,
                   contract_id, symbol, session_name, range_start, range_end,
                   manifest_sha256, trading_dates_status, trading_dates_sha256
            FROM run_lookup WHERE run_id = ?
            """,
            (prepared.manifest.run_id,),
        ).fetchone()
    assert row is not None
    assert row[:8] == (
        0,
        "strategy-0003",
        strategy_hash,
        contract.contract_id,
        "NQ",
        "eth",
        "2026-07-20T22:00:00Z",
        "2026-07-20T22:02:00Z",
    )
    assert isinstance(row[8], str) and len(row[8]) == 64
    assert row[9] == "complete"
    assert isinstance(row[10], str) and len(row[10]) == 64


def test_actual_consumed_dates_exclude_range_gaps_weekends_and_blackout(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """Only admitted bars contribute labels; no range calendar is ever synthesized."""
    contract = contracts_registry.by_symbol("NQ")
    pre_holiday_bar = _bar(contract, datetime(2026, 7, 2, 20, tzinfo=UTC))
    monday_bar = _bar(contract, datetime(2026, 7, 5, 22, tzinfo=UTC))
    blackout_bar = _bar(contract, datetime(2026, 9, 16, 22, tzinfo=UTC))
    prepared = _prepared(
        contract,
        run_id="actual-dates-001",
        bars=(pre_holiday_bar, monday_bar, blackout_bar),
    )

    actual_dates = consumed_trading_dates_for_bars(
        prepared.bars,
        contract=contract,
        session_name="eth",
    )
    expected_dates = tuple(
        sorted(
            {
                trading_date_for_bar(
                    pre_holiday_bar,
                    contract=contract,
                    session_name="eth",
                ),
                trading_date_for_bar(monday_bar, contract=contract, session_name="eth"),
            }
        )
    )
    excluded_date = trading_date_for_bar(blackout_bar, contract=contract, session_name="eth")
    assert actual_dates == expected_dates
    assert excluded_date in prepared.manifest.excluded_trading_dates
    assert excluded_date not in actual_dates
    # 2026-07-03 is the observed US Independence Day holiday; the long range also
    # spans the July 4/5 weekend.  Neither can appear without an admitted bar.
    assert date(2026, 7, 3) not in actual_dates
    assert date(2026, 7, 4) not in actual_dates
    assert date(2026, 7, 5) not in actual_dates

    store = SqliteRunStore(tmp_path / "runs.sqlite3")
    store.persist(
        _result(contract, prepared),
        contract=contract,
        consumed_trading_dates=actual_dates,
    )
    with sqlite3.connect(store.path) as connection:
        stored_dates = connection.execute(
            "SELECT trading_date FROM run_trading_dates ORDER BY trading_date"
        ).fetchall()
        index_names = {
            row[1] for row in connection.execute("PRAGMA index_list('run_lookup')")
        }
        date_index_names = {
            row[1] for row in connection.execute("PRAGMA index_list('run_trading_dates')")
        }
    assert stored_dates == [(value.isoformat(),) for value in actual_dates]
    assert {
        "run_lookup_by_standard_strategy",
        "run_lookup_by_standard_duplicate",
        "run_lookup_by_standard_symbol_date_status",
    } <= index_names
    assert "run_trading_dates_by_date" in date_index_names


@pytest.mark.parametrize("timezone_name", ["Asia/Shanghai", "America/New_York"])
def test_consumed_trading_date_labels_do_not_depend_on_process_timezone(
    contracts_registry: ContractRegistry,
    monkeypatch: pytest.MonkeyPatch,
    timezone_name: str,
) -> None:
    """Exchange labels are dates, not local-midnight instants subject to TZ conversion."""
    contract = contracts_registry.by_symbol("NQ")
    bar = _bar(contract, datetime(2026, 7, 19, 22, tzinfo=UTC))
    monkeypatch.setenv("TZ", timezone_name)

    labels = consumed_trading_dates_for_bars(
        (bar,),
        contract=contract,
        session_name="eth",
    )

    assert labels == (date(2026, 7, 20),)


def test_backfill_legacy_manifest_rows_is_idempotent_and_honestly_unavailable(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """Missing consumed-bar evidence becomes unavailable, never a fake zero/date range."""
    contract = contracts_registry.by_symbol("NQ")
    store = SqliteRunStore(tmp_path / "legacy-copy.sqlite3")
    prepared = _persist(store, contract, run_id="legacy-copy-001")
    with sqlite3.connect(store.path) as connection:
        connection.execute("DELETE FROM run_trading_dates")
        connection.execute("DELETE FROM run_lookup")
        connection.commit()

    store.backfill_run_indexes(registry=contracts_registry)
    store.backfill_run_indexes(registry=contracts_registry)
    store.reconcile_run_indexes(registry=contracts_registry)

    with sqlite3.connect(store.path) as connection:
        lookup_rows = connection.execute(
            """
            SELECT run_id, validation_run, strategy_version, symbol,
                   trading_dates_status, trading_dates_sha256
            FROM run_lookup
            """
        ).fetchall()
        date_count = connection.execute("SELECT COUNT(*) FROM run_trading_dates").fetchone()[0]
    assert lookup_rows == [
        (
            prepared.manifest.run_id,
            0,
            prepared.manifest.strategy_version,
            "NQ",
            "unavailable",
            None,
        )
    ]
    assert date_count == 0


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("missing_lookup", "missing run_lookup"),
        ("orphan", "orphan run_lookup"),
        ("manifest_mismatch", "strategy_version"),
        ("extra_date", "fingerprint mismatch"),
    ],
)
def test_reconciliation_detects_missing_orphan_manifest_mismatch_and_extra_date(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
    corruption: str,
    message: str,
) -> None:
    """Every reconciliation failure stops instead of silently repairing derived evidence."""
    contract = contracts_registry.by_symbol("NQ")
    store = SqliteRunStore(tmp_path / f"{corruption}.sqlite3")
    prepared = _persist(store, contract, run_id="reconcile-001")
    with sqlite3.connect(store.path) as connection:
        if corruption == "missing_lookup":
            connection.execute("DELETE FROM run_trading_dates")
            connection.execute("DELETE FROM run_lookup")
        elif corruption == "orphan":
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                """
                INSERT INTO run_lookup (
                    run_id, validation_run, strategy_version, strategy_content_sha256,
                    contract_id, symbol, session_name, range_start, range_end,
                    manifest_sha256, trading_dates_status, trading_dates_sha256
                ) VALUES (?, 0, 'strategy-0003', NULL, ?, 'NQ', 'eth',
                          '2026-07-20T22:00:00Z', '2026-07-20T22:01:00Z',
                          ?, 'unavailable', NULL)
                """,
                ("orphan-001", contract.contract_id, "0" * 64),
            )
        elif corruption == "manifest_mismatch":
            connection.execute(
                "UPDATE run_lookup SET strategy_version = 'tampered' WHERE run_id = ?",
                (prepared.manifest.run_id,),
            )
        else:
            connection.execute(
                "INSERT INTO run_trading_dates (run_id, trading_date) VALUES (?, ?)",
                (prepared.manifest.run_id, "2026-07-22"),
            )
        connection.commit()

    with pytest.raises(RunIndexIntegrityError, match=message):
        store.reconcile_run_indexes(registry=contracts_registry)


def test_real_historical_main_copy_migrates_and_audit_source_stays_read_only(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """Only a main copy receives derived rows; the audit source contributes one read-only run."""
    source_root = PROJECT_ROOT / "data" / "backtests"
    source = source_root / "runs.sqlite3"
    backup = source_root / "runs.sqlite3.pre-migration-backup-20260728"
    audit = source_root / "runs-3b4-audit.sqlite3"
    protected_before = {
        path: _source_state(path)
        for path in (source, backup, audit)
    }
    opening_main_run_ids = _run_ids(source)
    pre_activation_run_ids = _run_ids(backup)
    audit_run_ids = _run_ids(audit)
    opening_lookup_rows, opening_date_rows = _run_index_truth(source)
    result_root = source_root / "results"
    result_id = "nq-20260725-val-02a5b3"
    source_hashes = {
        path.relative_to(source_root): sha256(path.read_bytes()).hexdigest()
        for path in source_root.rglob("*")
        if path.is_file()
    }
    assert json.loads(
        (result_root / f"{result_id}.json").read_text(encoding="utf-8")
    )["schema"] == "result.v1"
    assert json.loads(
        (result_root / "trades" / f"{result_id}.json").read_text(encoding="utf-8")
    )["schema"] == "trades.v1"
    assert json.loads(
        (result_root / "equity" / f"{result_id}.json").read_text(encoding="utf-8")
    )["schema"] == "equity_curve.v1"
    assert json.loads(
        (result_root / "events" / f"{result_id}.json").read_text(encoding="utf-8")
    )["schema"] == "events.v1"
    main_copy = tmp_path / "runs.sqlite3"
    shutil.copy2(source, main_copy)
    store = SqliteRunStore(main_copy)
    store.migrate_run_indexes(registry=contracts_registry)
    store.reconcile_run_indexes(registry=contracts_registry)
    with sqlite3.connect(main_copy) as connection:
        main_run_ids = {
            row[0] for row in connection.execute("SELECT run_id FROM runs").fetchall()
        }
        lookup_run_ids = {
            row[0] for row in connection.execute("SELECT run_id FROM run_lookup").fetchall()
        }
        unavailable_run_ids = {
            row[0]
            for row in connection.execute(
                "SELECT run_id FROM run_lookup "
                "WHERE trading_dates_status = 'unavailable'"
            ).fetchall()
        }
        complete_run_ids = {
            row[0]
            for row in connection.execute(
                "SELECT run_id FROM run_lookup WHERE trading_dates_status = 'complete'"
            ).fetchall()
        }
    migrated_lookup_rows, migrated_date_rows = _run_index_truth(main_copy)
    post_activation_run_ids = opening_main_run_ids - pre_activation_run_ids
    appended_run_ids = post_activation_run_ids - {_ACTIVATION_SMOKE_RUN_ID}

    assert len(pre_activation_run_ids) == 15
    assert pre_activation_run_ids < opening_main_run_ids
    assert _ACTIVATION_SMOKE_RUN_ID in post_activation_run_ids
    assert _KNOWN_POST_ACTIVATION_RUN_ID in appended_run_ids
    assert main_run_ids == opening_main_run_ids
    assert lookup_run_ids == opening_main_run_ids
    assert migrated_lookup_rows == opening_lookup_rows
    assert migrated_date_rows == opening_date_rows
    assert unavailable_run_ids == pre_activation_run_ids
    assert complete_run_ids == post_activation_run_ids
    assert {str(row[0]) for row in migrated_date_rows} == post_activation_run_ids
    assert len(audit_run_ids) == 1
    assert main_run_ids | audit_run_ids == opening_main_run_ids | audit_run_ids
    assert len(main_run_ids | audit_run_ids) == len(opening_main_run_ids) + len(audit_run_ids)
    assert {
        path: _source_state(path)
        for path in (source, backup, audit)
    } == protected_before
    assert {
        path.relative_to(source_root): sha256(path.read_bytes()).hexdigest()
        for path in source_root.rglob("*")
        if path.is_file()
    } == source_hashes
