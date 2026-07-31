"""Immutable SQLite storage and regenerable ``result.v1`` artifact export.

``runs`` and ``trades`` are durable A4/A5 evidence, so this module never
updates or deletes them.  SQLite triggers enforce that policy even when an
operator bypasses this small repository API.  A6 files are deliberately
derivative: they can be atomically regenerated from the same immutable A5
facts whenever an export format evolves.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from futures_research.backtest.records import RunManifest, RunResult, strategy_event_to_dict
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.sessions import session_bounds_for_trading_date

_BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    run_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    trade_id TEXT NOT NULL,
    entry_timestamp TEXT NOT NULL,
    exit_timestamp TEXT NOT NULL,
    net_pnl REAL NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (run_id, ordinal),
    UNIQUE (run_id, trade_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS trades_by_run_exit ON trades (run_id, exit_timestamp);
"""


_RUN_INDEX_PROOF_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS run_index_proof (
    proof_id INTEGER PRIMARY KEY CHECK (proof_id = 1),
    schema_version INTEGER NOT NULL,
    run_count INTEGER NOT NULL,
    trade_count INTEGER NOT NULL,
    lookup_count INTEGER NOT NULL,
    trading_date_count INTEGER NOT NULL,
    immutable_runs_sha256 TEXT NOT NULL,
    immutable_trades_sha256 TEXT NOT NULL,
    immutable_triggers_sha256 TEXT NOT NULL,
    derived_sha256 TEXT NOT NULL,
    publication_sha256 TEXT NOT NULL
);
"""


_DERIVED_RUN_INDEX_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS run_lookup (
    run_id TEXT PRIMARY KEY,
    validation_run INTEGER NOT NULL CHECK (validation_run IN (0, 1)),
    strategy_version TEXT NOT NULL,
    strategy_content_sha256 TEXT,
    contract_id TEXT NOT NULL,
    symbol TEXT,
    session_name TEXT NOT NULL,
    range_start TEXT NOT NULL,
    range_end TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    trading_dates_status TEXT NOT NULL
        CHECK (trading_dates_status IN ('complete', 'unavailable')),
    trading_dates_sha256 TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE RESTRICT,
    CHECK (
        (trading_dates_status = 'complete' AND trading_dates_sha256 IS NOT NULL)
        OR (trading_dates_status = 'unavailable' AND trading_dates_sha256 IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS run_trading_dates (
    run_id TEXT NOT NULL,
    trading_date TEXT NOT NULL,
    PRIMARY KEY (run_id, trading_date),
    FOREIGN KEY (run_id) REFERENCES run_lookup(run_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS run_lookup_by_standard_strategy
ON run_lookup (validation_run, strategy_version, run_id);

CREATE INDEX IF NOT EXISTS run_lookup_by_standard_duplicate
ON run_lookup (validation_run, symbol, strategy_version, range_start, range_end, run_id);

CREATE INDEX IF NOT EXISTS run_lookup_by_standard_symbol_date_status
ON run_lookup (validation_run, symbol, trading_dates_status, run_id);

CREATE INDEX IF NOT EXISTS run_trading_dates_by_date
ON run_trading_dates (trading_date, run_id);
""" + _RUN_INDEX_PROOF_TABLE_SQL


_IMMUTABILITY_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS runs_are_immutable
BEFORE UPDATE ON runs
BEGIN
    SELECT RAISE(ABORT, 'runs are immutable');
END;

CREATE TRIGGER IF NOT EXISTS runs_cannot_be_deleted
BEFORE DELETE ON runs
BEGIN
    SELECT RAISE(ABORT, 'runs are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trades_are_immutable
BEFORE UPDATE ON trades
BEGIN
    SELECT RAISE(ABORT, 'trades are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trades_cannot_be_deleted
BEFORE DELETE ON trades
BEGIN
    SELECT RAISE(ABORT, 'trades are immutable');
END;
"""


_SCHEMA_SQL = _BASE_SCHEMA_SQL + _DERIVED_RUN_INDEX_SCHEMA_SQL + _IMMUTABILITY_TRIGGER_SQL


class ImmutableArtifactError(RuntimeError):
    """Raised when a caller tries to replace an existing A4/A5 run."""


class RunIndexIntegrityError(RuntimeError):
    """Raised when a derived run index cannot be proven consistent with its source."""


class RunIndexMigrationRequired(RunIndexIntegrityError):
    """Raised when a read-only run-reference caller targets an unmigrated database."""


RunReferenceMode = Literal["strategy", "trading-date", "duplicate"]
TradingDatesStatus = Literal["complete", "unavailable"]


@dataclass(frozen=True, slots=True)
class ImmutableRunTruthSnapshot:
    """Stable evidence that a migration did not alter immutable run/trade facts."""

    run_count: int
    trade_count: int
    runs_sha256: str
    trades_sha256: str
    immutable_triggers: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class RunIndexMigrationSummary:
    """Operator-visible result of one explicit main-database index migration."""

    database: Path
    run_count: int
    trade_count: int
    runs_sha256: str
    trades_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return primitive-only CLI output without exposing mutable implementation details."""
        return {
            "database": str(self.database),
            "run_count": self.run_count,
            "trade_count": self.trade_count,
            "runs_sha256": self.runs_sha256,
            "trades_sha256": self.trades_sha256,
        }


@dataclass(frozen=True, slots=True)
class RunReference:
    """One Owner-facing standard-run reference returned by the additive lookup API."""

    run_id: str
    strategy_version: str
    contract_id: str
    symbol: str
    session_name: str
    range_start: str
    range_end: str

    def to_dict(self) -> dict[str, str]:
        """Return the fixed primitive-only response shape for one reference row."""
        return {
            "run_id": self.run_id,
            "strategy_version": self.strategy_version,
            "contract_id": self.contract_id,
            "symbol": self.symbol,
            "session_name": self.session_name,
            "range_start": self.range_start,
            "range_end": self.range_end,
        }


@dataclass(frozen=True, slots=True)
class UnindexedRunCandidate:
    """A run that prevents the API from honestly claiming an exact zero count."""

    run_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"run_id": self.run_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class RunReferenceList:
    """Complete response state for one standard-run reference lookup."""

    mode: RunReferenceMode
    runs: tuple[RunReference, ...]
    unindexed_candidates: tuple[UnindexedRunCandidate, ...]

    def to_document(self) -> dict[str, object]:
        """Encode exact/unknown count semantics without flattening the distinction."""
        count_known = not self.unindexed_candidates
        return {
            "schema": "run_reference_list.v1",
            "mode": self.mode,
            "run_scope": "standard",
            "count_known": count_known,
            "count": len(self.runs) if count_known else None,
            "known_match_count": len(self.runs),
            "runs": [row.to_dict() for row in self.runs],
            "unindexed_candidates": [row.to_dict() for row in self.unindexed_candidates],
        }


@dataclass(frozen=True, slots=True)
class _RunLookupValues:
    """Internal normalized values shared by new-write and backfill paths."""

    run_id: str
    validation_run: int
    strategy_version: str
    strategy_content_sha256: str | None
    contract_id: str
    symbol: str | None
    session_name: str
    range_start: str
    range_end: str
    manifest_sha256: str
    trading_dates_status: TradingDatesStatus
    trading_dates_sha256: str | None


@dataclass(frozen=True, slots=True)
class _RunIndexProof:
    """One atomically published binding between immutable and derived run facts."""

    proof_id: int
    schema_version: int
    run_count: int
    trade_count: int
    lookup_count: int
    trading_date_count: int
    immutable_runs_sha256: str
    immutable_trades_sha256: str
    immutable_triggers_sha256: str
    derived_sha256: str


@dataclass(frozen=True, slots=True)
class _RunIndexRuntimeState:
    """The compact read-only state that every GET can verify without reading JSON blobs."""

    run_count: int
    trade_count: int
    lookup_count: int
    trading_date_count: int
    immutable_triggers_sha256: str
    derived_sha256: str


@dataclass(frozen=True, slots=True)
class StoredRun:
    """A fresh, read-only-in-practice snapshot of one SQLite A4/A5 artifact."""

    run_id: str
    manifest: dict[str, object]
    result: dict[str, object]
    trades: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class ExportedResult:
    """Absolute locations of one main A6 JSON file and its sidecars."""

    result_path: Path
    trades_path: Path
    equity_curve_path: Path
    events_path: Path


@dataclass(slots=True)
class StagedResultExport:
    """Prepared immutable A6 files that can publish inside the SQLite commit boundary.

    New Batch 3 runs must never commit an immutable SQLite run whose evidence
    sidecars failed to publish.  The files are rendered and fsynced to private
    temporary names first; publication is called by ``SqliteRunStore.persist``
    while its transaction is still open.  A callback failure rolls the database
    back, and the caller can remove only files created by this staging object.
    """

    exported: ExportedResult
    _temporary_paths: dict[Path, Path]
    _published_paths: list[Path]

    def publish(self) -> None:
        """Atomically make all staged sidecars visible, with main result last."""
        try:
            for final_path in _export_publish_order(self.exported):
                if final_path.exists():
                    msg = f"immutable result artifact already exists: {final_path}"
                    raise ImmutableArtifactError(msg)
                temporary_path = self._temporary_paths[final_path]
                try:
                    # A hard link creates the final name atomically and fails if a
                    # concurrent publisher won the name.  ``replace`` would be an
                    # invisible overwrite on Windows, which violates immutable-run
                    # retry semantics.
                    os.link(temporary_path, final_path)
                except FileExistsError as exc:
                    msg = f"immutable result artifact already exists: {final_path}"
                    raise ImmutableArtifactError(msg) from exc
                temporary_path.unlink()
                self._published_paths.append(final_path)
        except BaseException:
            self.rollback()
            raise

    def discard_temporary(self) -> None:
        """Remove only unreleased staging files after a successful commit."""
        for temporary_path in self._temporary_paths.values():
            if temporary_path.exists():
                temporary_path.unlink()

    def rollback(self) -> None:
        """Clean only this attempt's temporary/final files after a failed publish/commit."""
        self.discard_temporary()
        for final_path in reversed(self._published_paths):
            if final_path.exists():
                final_path.unlink()
        self._published_paths.clear()


class SqliteRunStore:
    """Append immutable completed runs and their flat trade records to SQLite."""

    def __init__(self, path: Path) -> None:
        """Bind the repository to an explicit operator-controlled SQLite path."""
        self._path = path

    @property
    def path(self) -> Path:
        """Expose the configured database location for diagnostics and backups."""
        return self._path

    def persist(
        self,
        result: RunResult,
        *,
        contract: ContractSpec,
        consumed_trading_dates: Iterable[date],
        before_commit: Callable[[], None] | None = None,
    ) -> None:
        """Atomically store immutable A4/A5 facts and their complete derived indexes."""
        if contract.contract_id != result.manifest.contract_id:
            msg = "run index contract must exactly match the immutable manifest contract_id"
            raise ValueError(msg)
        dates = _normalized_trading_dates(consumed_trading_dates)
        if not dates:
            msg = "new runs must provide at least one actually consumed trading date"
            raise ValueError(msg)

        manifest_json = _json_text(result.manifest.model_dump(mode="json", by_alias=True))
        lookup = _lookup_values_from_manifest(
            result.manifest,
            manifest_json=manifest_json,
            symbol=contract.symbol,
            trading_dates_status="complete",
            trading_dates_sha256=_trading_dates_sha256(dates),
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _assert_append_can_publish_proof(connection)
            exists = connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (result.run_id,)
            ).fetchone()
            if exists is not None:
                msg = f"run_id already exists and is immutable: {result.run_id}"
                raise ImmutableArtifactError(msg)

            result_json = _json_text(result.storage_document())
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, manifest_json, result_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    manifest_json,
                    result_json,
                    _timestamp_text(result.manifest.created_at),
                    _timestamp_text(result.completed_at),
                ),
            )
            for ordinal, record in enumerate(result.trade_records, start=1):
                execution = record.execution
                connection.execute(
                    """
                    INSERT INTO trades (
                        run_id, ordinal, trade_id, entry_timestamp, exit_timestamp,
                        net_pnl, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        ordinal,
                        record.trade_id,
                        _timestamp_text(execution.entry_timestamp),
                        _timestamp_text(execution.exit_timestamp),
                        execution.net_pnl,
                        _json_text(record.to_dict()),
                    ),
                )
            _insert_run_lookup(connection, lookup)
            _insert_run_trading_dates(connection, result.run_id, dates)
            _publish_run_index_proof(connection)
            if before_commit is not None:
                before_commit()
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def backfill_run_indexes(self, *, registry: ContractRegistry) -> None:
        """Idempotently backfill manifest-derived lookup rows without inventing dates.

        Historical manifests prove their identity/range fields, but they do not prove
        the exact bars that passed every gate.  Missing historical date evidence is
        therefore represented as ``unavailable`` rather than calendar inference.
        """
        connection = self._connect()
        try:
            _backfill_run_indexes_on_connection(connection, registry=registry)
        finally:
            connection.close()

    def migrate_run_indexes(self, *, registry: ContractRegistry) -> RunIndexMigrationSummary:
        """Explicitly upgrade one existing main database with derived lookup tables.

        This method is intentionally separate from every read path.  It first captures
        the immutable A4/A5 truth and the exact four trigger definitions, then runs the
        idempotent derivative-only backfill, and finally proves those protected facts
        are byte-for-byte-equivalent at the logical SQLite row level.
        """
        if self._path.name == "runs-3b4-audit.sqlite3":
            msg = "run-index migration is forbidden for the permanent audit source"
            raise ImmutableArtifactError(msg)
        if not self._path.is_file():
            msg = f"run-index migration requires an existing SQLite database: {self._path}"
            raise FileNotFoundError(msg)
        before = _immutable_run_truth_snapshot(self._path)
        _require_four_immutable_triggers(before)
        connection = self._connect_existing_for_index_migration()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _upgrade_run_index_proof_schema(connection)
            _reconcile_and_backfill_run_indexes_on_connection(connection, registry=registry)
            after = _immutable_run_truth_snapshot_from_connection(connection)
            _require_four_immutable_triggers(after)
            if before != after:
                msg = "run-index migration changed immutable runs, trades, or trigger definitions"
                raise RunIndexIntegrityError(msg)
            _publish_run_index_proof(connection)
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        return RunIndexMigrationSummary(
            database=self._path,
            run_count=after.run_count,
            trade_count=after.trade_count,
            runs_sha256=after.runs_sha256,
            trades_sha256=after.trades_sha256,
        )

    def reconcile_run_indexes(self, *, registry: ContractRegistry) -> None:
        """Fail closed when any derived index row disagrees with immutable source facts."""
        connection = self._connect()
        try:
            _assert_reconciled(connection, registry=registry)
        finally:
            connection.close()

    def references_for_strategy(self, *, strategy_version: str) -> RunReferenceList:
        """Return standard-run references for one exact immutable strategy version."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT run_id, strategy_version, contract_id, symbol, session_name,
                       range_start, range_end
                FROM run_lookup
                WHERE validation_run = 0
                  AND strategy_version = ?
                  AND symbol IS NOT NULL
                ORDER BY run_id
                """,
                (strategy_version,),
            ).fetchall()
            candidates = [*_missing_lookup_candidates(connection)]
            candidates.extend(_strategy_symbol_unavailable_candidates(connection, strategy_version))
            return _reference_list(
                "strategy",
                rows,
                tuple(candidates),
            )
        finally:
            connection.close()

    def references_for_duplicate(
        self,
        *,
        strategy_version: str,
        symbol: str,
        range_start: str,
        range_end: str,
    ) -> RunReferenceList:
        """Return exact standard-run matches for strategy, root symbol, and UTC range."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT run_id, strategy_version, contract_id, symbol, session_name,
                       range_start, range_end
                FROM run_lookup
                WHERE validation_run = 0
                  AND strategy_version = ?
                  AND symbol = ?
                  AND range_start = ?
                  AND range_end = ?
                ORDER BY run_id
                """,
                (strategy_version, symbol, range_start, range_end),
            ).fetchall()
            return _reference_list(
                "duplicate",
                rows,
                _missing_lookup_candidates(connection),
            )
        finally:
            connection.close()

    def references_for_trading_date(
        self,
        *,
        symbol: str,
        trading_date: date,
        contract: ContractSpec,
    ) -> RunReferenceList:
        """Return proven date consumers plus candidates whose legacy dates are unavailable."""
        if contract.symbol != symbol:
            msg = "trading-date lookup contract must come from the requested registry symbol"
            raise ValueError(msg)
        date_label = trading_date.isoformat()
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT lookup.run_id, lookup.strategy_version, lookup.contract_id,
                       lookup.symbol, lookup.session_name, lookup.range_start,
                       lookup.range_end
                FROM run_lookup AS lookup
                INNER JOIN run_trading_dates AS dates ON dates.run_id = lookup.run_id
                WHERE lookup.validation_run = 0
                  AND lookup.symbol = ?
                  AND dates.trading_date = ?
                ORDER BY lookup.run_id
                """,
                (symbol, date_label),
            ).fetchall()
            candidates = [*_missing_lookup_candidates(connection)]
            unavailable_rows = connection.execute(
                """
                SELECT run_id, session_name, range_start, range_end
                FROM run_lookup
                WHERE validation_run = 0
                  AND symbol = ?
                  AND trading_dates_status = 'unavailable'
                ORDER BY run_id
                """,
                (symbol,),
            ).fetchall()
            for row in unavailable_rows:
                candidate = _unavailable_date_candidate(
                    row,
                    contract=contract,
                    trading_date=trading_date,
                )
                if candidate is not None:
                    candidates.append(candidate)
            unknown_symbol_rows = connection.execute(
                """
                SELECT run_id FROM run_lookup
                WHERE validation_run = 0 AND symbol IS NULL
                ORDER BY run_id
                """
            ).fetchall()
            candidates.extend(
                UnindexedRunCandidate(
                    run_id=cast(str, row["run_id"]),
                    reason="root_symbol_unavailable",
                )
                for row in unknown_symbol_rows
            )
            return _reference_list("trading-date", rows, tuple(candidates))
        finally:
            connection.close()

    def load(self, run_id: str) -> StoredRun:
        """Read fresh JSON copies of an immutable run and its ordered trade records."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT manifest_json, result_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                msg = f"unknown run_id: {run_id}"
                raise KeyError(msg)
            trade_rows = connection.execute(
                "SELECT record_json FROM trades WHERE run_id = ? ORDER BY ordinal", (run_id,)
            ).fetchall()
            return StoredRun(
                run_id=run_id,
                manifest=_json_object(row["manifest_json"]),
                result=_json_object(row["result_json"]),
                trades=tuple(_json_object(trade_row["record_json"]) for trade_row in trade_rows),
            )
        finally:
            connection.close()

    def list_run_ids(self) -> tuple[str, ...]:
        """Return completed runs in creation order for the future comparison UI."""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT run_id FROM runs ORDER BY created_at, run_id"
            ).fetchall()
            return tuple(cast(str, row["run_id"]) for row in rows)
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        """Open the database, enable foreign keys, and install immutable schema guards."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA_SQL)
        return connection

    def _connect_existing_for_index_migration(self) -> sqlite3.Connection:
        """Open only an existing main source and install just the derivative index schema."""
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_DERIVED_RUN_INDEX_SCHEMA_SQL)
        return connection


def _upgrade_run_index_proof_schema(connection: sqlite3.Connection) -> None:
    """Rebuild only regenerable proof metadata when an older proof schema is present."""
    if _run_index_proof_table_has_current_schema(connection):
        return
    connection.execute("DROP TABLE run_index_proof")
    connection.executescript(_RUN_INDEX_PROOF_TABLE_SQL)


def _run_index_proof_table_has_current_schema(connection: sqlite3.Connection) -> bool:
    """Recognize only the checksum-capable proof table as ready for publication."""
    columns = connection.execute("PRAGMA table_info(run_index_proof)").fetchall()
    by_name = {cast(str, row["name"]): row for row in columns}
    publication = by_name.get("publication_sha256")
    proof_id = by_name.get("proof_id")
    return (
        set(by_name) >= _RUN_INDEX_PROOF_REQUIRED_COLUMNS
        and publication is not None
        and cast(int, publication["notnull"]) == 1
        and proof_id is not None
        and cast(int, proof_id["pk"]) == 1
    )


def _backfill_run_indexes_on_connection(
    connection: sqlite3.Connection,
    *,
    registry: ContractRegistry,
) -> None:
    """Reconcile existing immutable facts into derivative-only index tables atomically."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        _reconcile_and_backfill_run_indexes_on_connection(connection, registry=registry)
        _publish_run_index_proof(connection)
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _reconcile_and_backfill_run_indexes_on_connection(
    connection: sqlite3.Connection,
    *,
    registry: ContractRegistry,
) -> None:
    """Fill only absent derived rows, then prove every immutable run matches its index."""
    _assert_no_index_orphans(connection)
    rows = connection.execute("SELECT run_id, manifest_json FROM runs ORDER BY run_id").fetchall()
    for row in rows:
        run_id = cast(str, row["run_id"])
        manifest_json = cast(str, row["manifest_json"])
        expected = _lookup_values_from_stored_manifest(
            run_id,
            manifest_json,
            registry=registry,
            trading_dates_status="unavailable",
            trading_dates_sha256=None,
        )
        existing = connection.execute(
            "SELECT * FROM run_lookup WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing is None:
            _insert_run_lookup(connection, expected)
            continue
        _assert_lookup_core_matches(run_id, existing, expected)
        _assert_trading_dates_state(connection, run_id, existing)
    _assert_reconciled(connection, registry=registry)


class ResultExporter:
    """Atomically regenerate result.v1 and UI-ready JSON sidecars from one A5."""

    def __init__(self, root: Path) -> None:
        """Use an explicit ignored runtime directory for all generated result files."""
        self._root = root

    @property
    def root(self) -> Path:
        """Expose the output root for a CLI/API caller to report to the operator."""
        return self._root

    def export(self, result: RunResult) -> ExportedResult:
        """Write sidecars first, then atomically publish the compact result.v1 main file."""
        exported, payloads = self._artifact_payloads(result)
        for path, payload in payloads:
            _write_json_atomic(path, payload)
        return exported

    def stage_new(self, result: RunResult) -> StagedResultExport:
        """Render a new immutable export before SQLite commit, without publishing it.

        This path intentionally rejects all pre-existing targets.  ``export()``
        remains available for legacy A6 regeneration, while a newly completed run
        must never silently overwrite a prior evidence artifact.
        """
        exported, payloads = self._artifact_payloads(result)
        rendered = tuple((path, _json_text(payload) + "\n") for path, payload in payloads)
        temporary_paths: dict[Path, Path] = {}
        try:
            for final_path, text in rendered:
                if final_path.exists():
                    msg = f"immutable result artifact already exists: {final_path}"
                    raise ImmutableArtifactError(msg)
                final_path.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = final_path.parent / f".{final_path.name}.{uuid4().hex}.tmp"
                temporary_paths[final_path] = temporary_path
                with temporary_path.open("x", encoding="utf-8") as output:
                    output.write(text)
                    output.flush()
                    os.fsync(output.fileno())
        except BaseException:
            for temporary_path in temporary_paths.values():
                if temporary_path.exists():
                    temporary_path.unlink()
            raise
        return StagedResultExport(
            exported=exported,
            _temporary_paths=temporary_paths,
            _published_paths=[],
        )

    def _artifact_payloads(
        self,
        result: RunResult,
    ) -> tuple[ExportedResult, tuple[tuple[Path, object], ...]]:
        """Build every result document in deterministic sidecar-before-main order."""
        run_id = result.run_id
        exported = ExportedResult(
            result_path=self._root / f"{run_id}.json",
            trades_path=self._root / "trades" / f"{run_id}.json",
            equity_curve_path=self._root / "equity" / f"{run_id}.json",
            events_path=self._root / "events" / f"{run_id}.json",
        )
        trades_ref = (Path("trades") / f"{run_id}.json").as_posix()
        equity_curve_ref = (Path("equity") / f"{run_id}.json").as_posix()
        events_ref = (Path("events") / f"{run_id}.json").as_posix()
        trades_document: dict[str, object] = {
            "schema": "trades.v1",
            "run_id": run_id,
            "trades": [record.to_dict() for record in result.trade_records],
        }
        events_document: dict[str, object] = {
            "schema": "events.v1",
            "run_id": run_id,
            "events": [strategy_event_to_dict(event) for event in result.event_log],
        }
        if result.evidence_complete:
            assert result.evidence_summary is not None
            trades_document["decision_evidence_complete"] = True
            events_document.update(
                {
                    "rejection_evidence": [
                        record.to_dict() for record in result.rejection_evidence
                    ],
                    "evidence_summary": result.evidence_summary.to_dict(),
                    "evidence_complete": True,
                }
            )
        return (
            exported,
            (
                (exported.trades_path, trades_document),
                (
                    exported.equity_curve_path,
                    {
                        "schema": "equity_curve.v1",
                        "run_id": run_id,
                        "points": [point.to_dict() for point in result.equity_curve],
                    },
                ),
                (exported.events_path, events_document),
                (
                    exported.result_path,
                    result.result_document(
                        trades_ref=trades_ref,
                        equity_curve_ref=equity_curve_ref,
                        events_ref=events_ref,
                    ),
                ),
            ),
        )


def _export_publish_order(exported: ExportedResult) -> tuple[Path, ...]:
    """Keep all sidecars visible before the main document can reference them."""
    return (
        exported.trades_path,
        exported.equity_curve_path,
        exported.events_path,
        exported.result_path,
    )


_DATE_LABEL = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _normalized_trading_dates(values: Iterable[date]) -> tuple[date, ...]:
    """Accept only real date labels from admitted bars, never timestamps or text."""
    normalized: set[date] = set()
    for value in values:
        if isinstance(value, datetime) or not isinstance(value, date):
            msg = "consumed trading dates must be date labels, not timestamps or text"
            raise TypeError(msg)
        normalized.add(value)
    return tuple(sorted(normalized))


def _trading_dates_sha256(values: Iterable[date]) -> str:
    """Fingerprint a sorted consumed-date set so reconciliation detects extra rows."""
    labels = [value.isoformat() for value in _normalized_trading_dates(values)]
    return sha256(_json_text(labels).encode("utf-8")).hexdigest()


_IMMUTABLE_TRIGGER_NAMES = (
    "runs_are_immutable",
    "runs_cannot_be_deleted",
    "trades_are_immutable",
    "trades_cannot_be_deleted",
)


def _immutable_run_truth_snapshot(path: Path) -> ImmutableRunTruthSnapshot:
    """Read immutable A4/A5 facts without executing any migration schema bootstrap."""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return _immutable_run_truth_snapshot_from_connection(connection)
    finally:
        connection.close()


def _immutable_run_truth_snapshot_from_connection(
    connection: sqlite3.Connection,
) -> ImmutableRunTruthSnapshot:
    """Capture migration-only A4/A5 truth from an already-open SQLite connection."""
    table_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('runs', 'trades')"
    ).fetchall()
    table_names = {cast(str, row["name"]) for row in table_rows}
    if table_names != {"runs", "trades"}:
        msg = "run-index migration requires existing immutable runs and trades tables"
        raise RunIndexIntegrityError(msg)
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
    trigger_rows = connection.execute(
        """
        SELECT name, sql FROM sqlite_master
        WHERE type = 'trigger' AND name IN (?, ?, ?, ?)
        ORDER BY name
        """,
        _IMMUTABLE_TRIGGER_NAMES,
    ).fetchall()

    run_payload = [
        {
            "run_id": cast(str, row["run_id"]),
            "manifest": _json_object(row["manifest_json"]),
            "result": _json_object(row["result_json"]),
            "created_at": cast(str, row["created_at"]),
            "completed_at": cast(str, row["completed_at"]),
        }
        for row in runs
    ]
    trade_payload = [
        {
            "run_id": cast(str, row["run_id"]),
            "ordinal": cast(int, row["ordinal"]),
            "trade_id": cast(str, row["trade_id"]),
            "entry_timestamp": cast(str, row["entry_timestamp"]),
            "exit_timestamp": cast(str, row["exit_timestamp"]),
            "net_pnl": row["net_pnl"],
            "record": _json_object(row["record_json"]),
        }
        for row in trades
    ]
    return ImmutableRunTruthSnapshot(
        run_count=len(run_payload),
        trade_count=len(trade_payload),
        runs_sha256=sha256(_json_text(run_payload).encode("utf-8")).hexdigest(),
        trades_sha256=sha256(_json_text(trade_payload).encode("utf-8")).hexdigest(),
        immutable_triggers=tuple(
            (cast(str, row["name"]), cast(str, row["sql"])) for row in trigger_rows
        ),
    )


_RUN_INDEX_PROOF_SCHEMA_VERSION = 2
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_RUN_INDEX_PROOF_PUBLICATION_DOMAIN = "run-index-proof.v1"
_RUN_INDEX_PROOF_REQUIRED_COLUMNS = frozenset(
    {
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
    }
)


def _assert_append_can_publish_proof(connection: sqlite3.Connection) -> None:
    """Refuse to let a normal new-run write bless unproven historical index rows."""
    if not _run_index_proof_table_has_current_schema(connection):
        msg = "run-index proof schema needs explicit migrate-run-index before appending"
        raise RunIndexIntegrityError(msg)
    proof_count = cast(
        int,
        connection.execute("SELECT COUNT(*) FROM run_index_proof").fetchone()[0],
    )
    if proof_count == 1:
        _assert_runtime_run_index_proof(connection)
        return
    immutable_count = cast(
        int,
        connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
    )
    trade_count = cast(
        int,
        connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
    )
    derived_count = cast(
        int,
        connection.execute("SELECT COUNT(*) FROM run_lookup").fetchone()[0],
    )
    date_count = cast(
        int,
        connection.execute("SELECT COUNT(*) FROM run_trading_dates").fetchone()[0],
    )
    if proof_count != 0 or immutable_count or trade_count or derived_count or date_count:
        msg = "run-index migration proof is absent; run explicit migrate-run-index first"
        raise RunIndexIntegrityError(msg)


def _publish_run_index_proof(connection: sqlite3.Connection) -> None:
    """Publish current fully reconciled state inside the caller's transaction."""
    proof = _current_run_index_proof(connection)
    publication_sha256 = _run_index_proof_publication_sha256(proof)
    connection.execute(
        """
        INSERT INTO run_index_proof (
            proof_id, schema_version, run_count, trade_count, lookup_count,
            trading_date_count, immutable_runs_sha256, immutable_trades_sha256,
            immutable_triggers_sha256, derived_sha256, publication_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(proof_id) DO UPDATE SET
            schema_version = excluded.schema_version,
            run_count = excluded.run_count,
            trade_count = excluded.trade_count,
            lookup_count = excluded.lookup_count,
            trading_date_count = excluded.trading_date_count,
            immutable_runs_sha256 = excluded.immutable_runs_sha256,
            immutable_trades_sha256 = excluded.immutable_trades_sha256,
            immutable_triggers_sha256 = excluded.immutable_triggers_sha256,
            derived_sha256 = excluded.derived_sha256,
            publication_sha256 = excluded.publication_sha256
        """,
        (
            proof.proof_id,
            proof.schema_version,
            proof.run_count,
            proof.trade_count,
            proof.lookup_count,
            proof.trading_date_count,
            proof.immutable_runs_sha256,
            proof.immutable_trades_sha256,
            proof.immutable_triggers_sha256,
            proof.derived_sha256,
            publication_sha256,
        ),
    )


def _assert_runtime_run_index_proof(connection: sqlite3.Connection) -> None:
    """Verify one current snapshot without reparsing every immutable manifest JSON blob."""
    rows = connection.execute(
        """
        SELECT proof_id, schema_version, run_count, trade_count, lookup_count,
               trading_date_count, immutable_runs_sha256, immutable_trades_sha256,
               immutable_triggers_sha256, derived_sha256, publication_sha256
        FROM run_index_proof
        ORDER BY proof_id
        """
    ).fetchall()
    if len(rows) != 1:
        msg = "run-index migration proof must contain exactly one publication row"
        raise RunIndexIntegrityError(msg)
    actual, publication_sha256 = _run_index_proof_from_row(rows[0])
    if publication_sha256 != _run_index_proof_publication_sha256(actual):
        msg = "run-index migration proof publication checksum does not match its payload"
        raise RunIndexIntegrityError(msg)
    current = _current_run_index_runtime_state(connection)
    if (
        actual.run_count != current.run_count
        or actual.trade_count != current.trade_count
        or actual.lookup_count != current.lookup_count
        or actual.trading_date_count != current.trading_date_count
        or actual.immutable_triggers_sha256 != current.immutable_triggers_sha256
        or actual.derived_sha256 != current.derived_sha256
    ):
        msg = "run-index migration proof does not match the current immutable and derived state"
        raise RunIndexIntegrityError(msg)
    if current.run_count != current.lookup_count:
        msg = "run-index migration proof is missing a lookup row for an immutable run"
        raise RunIndexIntegrityError(msg)


def _run_index_proof_from_row(row: sqlite3.Row) -> tuple[_RunIndexProof, str]:
    """Decode one proof row strictly so malformed SQLite values never look ready."""
    integer_fields = (
        "proof_id",
        "schema_version",
        "run_count",
        "trade_count",
        "lookup_count",
        "trading_date_count",
    )
    values: dict[str, int] = {}
    for field in integer_fields:
        value = row[field]
        if type(value) is not int or value < 0:
            msg = f"run-index migration proof has invalid {field}"
            raise RunIndexIntegrityError(msg)
        values[field] = value
    if values["proof_id"] != 1:
        msg = "run-index migration proof has an invalid publication identity"
        raise RunIndexIntegrityError(msg)
    if values["schema_version"] != _RUN_INDEX_PROOF_SCHEMA_VERSION:
        msg = "run-index migration proof has an unsupported schema version"
        raise RunIndexIntegrityError(msg)
    hash_fields = (
        "immutable_runs_sha256",
        "immutable_trades_sha256",
        "immutable_triggers_sha256",
        "derived_sha256",
        "publication_sha256",
    )
    hashes: dict[str, str] = {}
    for field in hash_fields:
        value = row[field]
        if not isinstance(value, str) or _SHA256_HEX.fullmatch(value) is None:
            msg = f"run-index migration proof has invalid {field}"
            raise RunIndexIntegrityError(msg)
        hashes[field] = value
    proof = _RunIndexProof(
        proof_id=values["proof_id"],
        schema_version=values["schema_version"],
        run_count=values["run_count"],
        trade_count=values["trade_count"],
        lookup_count=values["lookup_count"],
        trading_date_count=values["trading_date_count"],
        immutable_runs_sha256=hashes["immutable_runs_sha256"],
        immutable_trades_sha256=hashes["immutable_trades_sha256"],
        immutable_triggers_sha256=hashes["immutable_triggers_sha256"],
        derived_sha256=hashes["derived_sha256"],
    )
    return proof, hashes["publication_sha256"]


def _run_index_proof_publication_payload(proof: _RunIndexProof) -> dict[str, object]:
    """Return the one ordered, typed payload bound by every proof publication checksum."""
    return {
        "domain": _RUN_INDEX_PROOF_PUBLICATION_DOMAIN,
        "fields": [
            ["proof_id", proof.proof_id],
            ["schema_version", proof.schema_version],
            ["run_count", proof.run_count],
            ["trade_count", proof.trade_count],
            ["lookup_count", proof.lookup_count],
            ["trading_date_count", proof.trading_date_count],
            ["immutable_runs_sha256", proof.immutable_runs_sha256],
            ["immutable_trades_sha256", proof.immutable_trades_sha256],
            ["immutable_triggers_sha256", proof.immutable_triggers_sha256],
            ["derived_sha256", proof.derived_sha256],
        ],
    }


def _run_index_proof_publication_sha256(proof: _RunIndexProof) -> str:
    """Hash the canonical publication payload used identically for publish and GET verify."""
    payload = _json_text(_run_index_proof_publication_payload(proof)).encode("utf-8")
    return sha256(payload).hexdigest()


def _current_run_index_proof(connection: sqlite3.Connection) -> _RunIndexProof:
    """Build a publication proof while a migration or append transaction owns the source."""
    current = _current_run_index_runtime_state(connection)
    try:
        run_rows = connection.execute(
            """
            SELECT run_id, manifest_json, result_json, created_at, completed_at
            FROM runs ORDER BY run_id
            """
        ).fetchall()
        trade_rows = connection.execute(
            """
            SELECT run_id, ordinal, trade_id, entry_timestamp, exit_timestamp, net_pnl, record_json
            FROM trades ORDER BY run_id, ordinal
            """
        ).fetchall()
        immutable_runs = [
            {
                "run_id": row["run_id"],
                "manifest_json": row["manifest_json"],
                "result_json": row["result_json"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }
            for row in run_rows
        ]
        immutable_trades = [
            {
                "run_id": row["run_id"],
                "ordinal": row["ordinal"],
                "trade_id": row["trade_id"],
                "entry_timestamp": row["entry_timestamp"],
                "exit_timestamp": row["exit_timestamp"],
                "net_pnl": row["net_pnl"],
                "record_json": row["record_json"],
            }
            for row in trade_rows
        ]
        return _RunIndexProof(
            proof_id=1,
            schema_version=_RUN_INDEX_PROOF_SCHEMA_VERSION,
            run_count=current.run_count,
            trade_count=current.trade_count,
            lookup_count=current.lookup_count,
            trading_date_count=current.trading_date_count,
            immutable_runs_sha256=sha256(_json_text(immutable_runs).encode("utf-8")).hexdigest(),
            immutable_trades_sha256=sha256(
                _json_text(immutable_trades).encode("utf-8")
            ).hexdigest(),
            immutable_triggers_sha256=current.immutable_triggers_sha256,
            derived_sha256=current.derived_sha256,
        )
    except (TypeError, ValueError) as exc:
        msg = f"run-index migration proof cannot fingerprint malformed SQLite values: {exc}"
        raise RunIndexIntegrityError(msg) from exc


def _current_run_index_runtime_state(
    connection: sqlite3.Connection,
) -> _RunIndexRuntimeState:
    """Read only compact counts, triggers, and derived rows for one GET snapshot.

    This intentionally never selects ``runs.manifest_json``, ``runs.result_json``,
    or ``trades.record_json``.  Those immutable blobs are reconciled when the proof
    is published; GET verifies that proof against the current derived state instead.
    """
    _assert_no_index_orphans(connection)
    try:
        run_count = cast(
            int,
            connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
        )
        trade_count = cast(
            int,
            connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
        )
        lookup_rows = connection.execute(
            """
            SELECT run_id, validation_run, strategy_version, strategy_content_sha256,
                   contract_id, symbol, session_name, range_start, range_end,
                   manifest_sha256, trading_dates_status, trading_dates_sha256
            FROM run_lookup ORDER BY run_id
            """
        ).fetchall()
        date_rows = connection.execute(
            "SELECT run_id, trading_date FROM run_trading_dates ORDER BY run_id, trading_date"
        ).fetchall()
        derived = {
            "lookup": [
                {
                    "run_id": row["run_id"],
                    "validation_run": row["validation_run"],
                    "strategy_version": row["strategy_version"],
                    "strategy_content_sha256": row["strategy_content_sha256"],
                    "contract_id": row["contract_id"],
                    "symbol": row["symbol"],
                    "session_name": row["session_name"],
                    "range_start": row["range_start"],
                    "range_end": row["range_end"],
                    "manifest_sha256": row["manifest_sha256"],
                    "trading_dates_status": row["trading_dates_status"],
                    "trading_dates_sha256": row["trading_dates_sha256"],
                }
                for row in lookup_rows
            ],
            "trading_dates": [
                {"run_id": row["run_id"], "trading_date": row["trading_date"]} for row in date_rows
            ],
        }
        trigger_rows = connection.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'trigger' AND name IN (?, ?, ?, ?)
            ORDER BY name
            """,
            _IMMUTABLE_TRIGGER_NAMES,
        ).fetchall()
        trigger_values = [(row["name"], row["sql"]) for row in trigger_rows]
        if tuple(name for name, _ in trigger_values) != _IMMUTABLE_TRIGGER_NAMES:
            msg = "run-index migration proof requires all four immutable triggers"
            raise RunIndexIntegrityError(msg)
        return _RunIndexRuntimeState(
            run_count=run_count,
            trade_count=trade_count,
            lookup_count=len(lookup_rows),
            trading_date_count=len(date_rows),
            immutable_triggers_sha256=sha256(
                _json_text(trigger_values).encode("utf-8")
            ).hexdigest(),
            derived_sha256=sha256(_json_text(derived).encode("utf-8")).hexdigest(),
        )
    except (TypeError, ValueError) as exc:
        msg = f"run-index runtime proof cannot fingerprint malformed SQLite values: {exc}"
        raise RunIndexIntegrityError(msg) from exc


def _require_four_immutable_triggers(snapshot: ImmutableRunTruthSnapshot) -> None:
    """Require the exact existing trigger set before and after an explicit migration."""
    names = tuple(name for name, _ in snapshot.immutable_triggers)
    if names != _IMMUTABLE_TRIGGER_NAMES:
        msg = "run-index migration requires all four existing immutable run/trade triggers"
        raise RunIndexIntegrityError(msg)


def _lookup_values_from_manifest(
    manifest: RunManifest,
    *,
    manifest_json: str,
    symbol: str | None,
    trading_dates_status: TradingDatesStatus,
    trading_dates_sha256: str | None,
) -> _RunLookupValues:
    """Extract only immutable-manifest facts into one normalized derived lookup row."""
    binding = manifest.strategy_binding
    return _RunLookupValues(
        run_id=manifest.run_id,
        validation_run=int(manifest.validation_run),
        strategy_version=manifest.strategy_version,
        strategy_content_sha256=None if binding is None else binding.content_sha256,
        contract_id=manifest.contract_id,
        symbol=symbol,
        session_name=manifest.session_name,
        range_start=_timestamp_text(manifest.range_start),
        range_end=_timestamp_text(manifest.range_end),
        manifest_sha256=sha256(manifest_json.encode("utf-8")).hexdigest(),
        trading_dates_status=trading_dates_status,
        trading_dates_sha256=trading_dates_sha256,
    )


def _lookup_values_from_stored_manifest(
    run_id: str,
    manifest_json: str,
    *,
    registry: ContractRegistry,
    trading_dates_status: TradingDatesStatus,
    trading_dates_sha256: str | None,
) -> _RunLookupValues:
    """Parse one immutable manifest fail-closed before backfill or reconciliation."""
    try:
        manifest = RunManifest.model_validate(_json_object(manifest_json))
    except (TypeError, ValueError) as exc:
        msg = f"run {run_id} has an invalid immutable manifest: {exc}"
        raise RunIndexIntegrityError(msg) from exc
    if manifest.run_id != run_id:
        msg = f"run {run_id} manifest run_id does not match its immutable SQLite key"
        raise RunIndexIntegrityError(msg)
    return _lookup_values_from_manifest(
        manifest,
        manifest_json=manifest_json,
        symbol=_registry_symbol_for_contract_id(registry, manifest.contract_id),
        trading_dates_status=trading_dates_status,
        trading_dates_sha256=trading_dates_sha256,
    )


def _registry_symbol_for_contract_id(
    registry: ContractRegistry,
    contract_id: str,
) -> str | None:
    """Return a root symbol only when registry evidence identifies exactly one match."""
    matches = [
        contract.symbol
        for contract in registry.contracts.values()
        if contract.contract_id == contract_id
    ]
    return matches[0] if len(matches) == 1 else None


def _insert_run_lookup(connection: sqlite3.Connection, values: _RunLookupValues) -> None:
    """Insert exactly one derived lookup row after immutable run/trade writes succeed."""
    connection.execute(
        """
        INSERT INTO run_lookup (
            run_id, validation_run, strategy_version, strategy_content_sha256,
            contract_id, symbol, session_name, range_start, range_end,
            manifest_sha256, trading_dates_status, trading_dates_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            values.run_id,
            values.validation_run,
            values.strategy_version,
            values.strategy_content_sha256,
            values.contract_id,
            values.symbol,
            values.session_name,
            values.range_start,
            values.range_end,
            values.manifest_sha256,
            values.trading_dates_status,
            values.trading_dates_sha256,
        ),
    )


def _insert_run_trading_dates(
    connection: sqlite3.Connection,
    run_id: str,
    values: Iterable[date],
) -> None:
    """Insert only the exact sorted unique dates that admitted bars actually consumed."""
    connection.executemany(
        "INSERT INTO run_trading_dates (run_id, trading_date) VALUES (?, ?)",
        [(run_id, value.isoformat()) for value in _normalized_trading_dates(values)],
    )


def _assert_no_index_orphans(connection: sqlite3.Connection) -> None:
    """Reject any derived row that no longer has the immutable parent it claims."""
    lookup_orphan = connection.execute(
        """
        SELECT lookup.run_id FROM run_lookup AS lookup
        LEFT JOIN runs AS runs ON runs.run_id = lookup.run_id
        WHERE runs.run_id IS NULL
        ORDER BY lookup.run_id
        LIMIT 1
        """
    ).fetchone()
    if lookup_orphan is not None:
        msg = f"orphan run_lookup row: {lookup_orphan['run_id']}"
        raise RunIndexIntegrityError(msg)
    date_orphan = connection.execute(
        """
        SELECT dates.run_id FROM run_trading_dates AS dates
        LEFT JOIN run_lookup AS lookup ON lookup.run_id = dates.run_id
        WHERE lookup.run_id IS NULL
        ORDER BY dates.run_id
        LIMIT 1
        """
    ).fetchone()
    if date_orphan is not None:
        msg = f"orphan run_trading_dates row: {date_orphan['run_id']}"
        raise RunIndexIntegrityError(msg)


def _assert_lookup_core_matches(
    run_id: str,
    row: sqlite3.Row,
    expected: _RunLookupValues,
) -> None:
    """Ensure a backfill never silently overwrites a disagreeing derived identity row."""
    for field, expected_value in (
        ("validation_run", expected.validation_run),
        ("strategy_version", expected.strategy_version),
        ("strategy_content_sha256", expected.strategy_content_sha256),
        ("contract_id", expected.contract_id),
        ("symbol", expected.symbol),
        ("session_name", expected.session_name),
        ("range_start", expected.range_start),
        ("range_end", expected.range_end),
        ("manifest_sha256", expected.manifest_sha256),
    ):
        if row[field] != expected_value:
            msg = f"run_lookup mismatch for {run_id}: {field} disagrees with manifest"
            raise RunIndexIntegrityError(msg)


def _assert_trading_dates_state(
    connection: sqlite3.Connection,
    run_id: str,
    row: sqlite3.Row,
) -> None:
    """Check date-index status and fingerprint without inferring any missing sessions."""
    status = row["trading_dates_status"]
    digest = row["trading_dates_sha256"]
    date_rows = connection.execute(
        "SELECT trading_date FROM run_trading_dates WHERE run_id = ? ORDER BY trading_date",
        (run_id,),
    ).fetchall()
    if status == "unavailable":
        if digest is not None or date_rows:
            msg = f"run_lookup unavailable date state is not empty for {run_id}"
            raise RunIndexIntegrityError(msg)
        return
    if status != "complete" or not isinstance(digest, str):
        msg = f"run_lookup has an invalid trading-date status for {run_id}"
        raise RunIndexIntegrityError(msg)
    if not date_rows:
        msg = f"run_lookup complete date state has no consumed dates for {run_id}"
        raise RunIndexIntegrityError(msg)
    values = tuple(
        _parse_trading_date_label(cast(str, date_row["trading_date"])) for date_row in date_rows
    )
    if _trading_dates_sha256(values) != digest:
        msg = f"run_trading_dates fingerprint mismatch for {run_id}"
        raise RunIndexIntegrityError(msg)


def _assert_reconciled(connection: sqlite3.Connection, *, registry: ContractRegistry) -> None:
    """Prove every immutable run has one matching lookup and coherent date state."""
    _assert_no_index_orphans(connection)
    rows = connection.execute("SELECT run_id, manifest_json FROM runs ORDER BY run_id").fetchall()
    for run_row in rows:
        run_id = cast(str, run_row["run_id"])
        manifest_json = cast(str, run_row["manifest_json"])
        lookup = connection.execute(
            "SELECT * FROM run_lookup WHERE run_id = ?", (run_id,)
        ).fetchone()
        if lookup is None:
            msg = f"missing run_lookup row for immutable run {run_id}"
            raise RunIndexIntegrityError(msg)
        expected = _lookup_values_from_stored_manifest(
            run_id,
            manifest_json,
            registry=registry,
            trading_dates_status="unavailable",
            trading_dates_sha256=None,
        )
        _assert_lookup_core_matches(run_id, lookup, expected)
        _assert_trading_dates_state(connection, run_id, lookup)


def _parse_trading_date_label(value: str) -> date:
    """Parse one stored trading-date label as an ASCII date, never a local timestamp."""
    if _DATE_LABEL.fullmatch(value) is None:
        msg = f"invalid stored trading_date label: {value!r}"
        raise RunIndexIntegrityError(msg)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        msg = f"invalid stored trading_date calendar value: {value!r}"
        raise RunIndexIntegrityError(msg) from exc


def _missing_lookup_candidates(connection: sqlite3.Connection) -> tuple[UnindexedRunCandidate, ...]:
    """Conservatively expose every immutable run lacking a lookup row as unknown."""
    rows = connection.execute(
        """
        SELECT runs.run_id FROM runs
        LEFT JOIN run_lookup AS lookup ON lookup.run_id = runs.run_id
        WHERE lookup.run_id IS NULL
        ORDER BY runs.run_id
        """
    ).fetchall()
    return tuple(
        UnindexedRunCandidate(
            run_id=cast(str, row["run_id"]),
            reason="lookup_unavailable",
        )
        for row in rows
    )


def _strategy_symbol_unavailable_candidates(
    connection: sqlite3.Connection,
    strategy_version: str,
) -> tuple[UnindexedRunCandidate, ...]:
    """Do not pretend a root symbol where registry evidence cannot prove one."""
    rows = connection.execute(
        """
        SELECT run_id FROM run_lookup
        WHERE validation_run = 0
          AND strategy_version = ?
          AND symbol IS NULL
        ORDER BY run_id
        """,
        (strategy_version,),
    ).fetchall()
    return tuple(
        UnindexedRunCandidate(
            run_id=cast(str, row["run_id"]),
            reason="root_symbol_unavailable",
        )
        for row in rows
    )


def _unavailable_date_candidate(
    row: sqlite3.Row,
    *,
    contract: ContractSpec,
    trading_date: date,
) -> UnindexedRunCandidate | None:
    """Return a candidate only when exact configured session bounds overlap its run."""
    run_id = cast(str, row["run_id"])
    session_name = cast(str, row["session_name"])
    try:
        bounds = session_bounds_for_trading_date(
            contract,
            trading_date,
            session_name=session_name,
        )
    except KeyError:
        return UnindexedRunCandidate(run_id=run_id, reason="session_bounds_unavailable")
    if bounds is None:
        return None
    session_start, session_end = bounds
    run_start = _parse_utc_timestamp(cast(str, row["range_start"]))
    run_end = _parse_utc_timestamp(cast(str, row["range_end"]))
    if run_start < session_end and session_start < run_end:
        return UnindexedRunCandidate(run_id=run_id, reason="trading_dates_unavailable")
    return None


def _parse_utc_timestamp(value: str) -> datetime:
    """Parse one canonical stored UTC instant for interval overlap checks."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        msg = f"invalid stored UTC timestamp: {value!r}"
        raise RunIndexIntegrityError(msg) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = f"stored timestamp has no timezone: {value!r}"
        raise RunIndexIntegrityError(msg)
    return parsed.astimezone(UTC)


def _reference_list(
    mode: RunReferenceMode,
    rows: Iterable[sqlite3.Row],
    candidates: Iterable[UnindexedRunCandidate],
) -> RunReferenceList:
    """Normalize deterministic SQL rows into the fixed additive response document."""
    references = tuple(
        RunReference(
            run_id=cast(str, row["run_id"]),
            strategy_version=cast(str, row["strategy_version"]),
            contract_id=cast(str, row["contract_id"]),
            symbol=cast(str, row["symbol"]),
            session_name=cast(str, row["session_name"]),
            range_start=cast(str, row["range_start"]),
            range_end=cast(str, row["range_end"]),
        )
        for row in rows
    )
    ordered_candidates = tuple(sorted(candidates, key=lambda candidate: candidate.run_id))
    return RunReferenceList(
        mode=mode,
        runs=references,
        unindexed_candidates=ordered_candidates,
    )


def _json_text(value: object) -> str:
    """Encode one persisted payload canonically and reject non-finite JSON values."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_object(value: object) -> dict[str, object]:
    """Decode a stored JSON object while defending the repository schema boundary."""
    if not isinstance(value, str):
        msg = "SQLite JSON column must contain text"
        raise TypeError(msg)
    decoded: object = json.loads(value)
    if not isinstance(decoded, dict):
        msg = "SQLite JSON column must contain an object"
        raise ValueError(msg)
    return cast(dict[str, object], decoded)


def _timestamp_text(value: datetime) -> str:
    """Serialize all SQLite/sidecar timestamps as canonical UTC ISO-8601 strings."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "persisted timestamps must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: object) -> None:
    """Replace one derived A6 file atomically without leaving a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary_path.open("x", encoding="utf-8") as output:
            output.write(_json_text(payload))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
