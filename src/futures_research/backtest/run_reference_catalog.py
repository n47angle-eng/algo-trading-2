"""Read-only, process-local catalog for the three Owner-facing run-reference modes.

The main run database supplies a migration-verified derived index.  The audit
database is intentionally never migrated: this catalog opens it in SQLite
``mode=ro`` and derives only manifest-proven identity fields in memory.  Once
bootstrapped, HTTP requests only filter these immutable in-memory entries.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, cast

from futures_research.backtest.persistence import (
    RunIndexIntegrityError,
    RunIndexMigrationRequired,
    RunReference,
    RunReferenceList,
    RunReferenceMode,
    TradingDatesStatus,
    UnindexedRunCandidate,
    _assert_runtime_run_index_proof,
    _assert_trading_dates_state,
    _lookup_values_from_stored_manifest,
    _parse_utc_timestamp,
    _RunLookupValues,
)
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.sessions import session_bounds_for_trading_date

SourceName = Literal["main", "audit"]

_MAIN_REQUIRED_TABLES = frozenset(
    {"runs", "trades", "run_lookup", "run_trading_dates", "run_index_proof"}
)
_MAIN_REQUIRED_INDEX_COLUMNS = {
    "run_lookup_by_standard_strategy": (
        "validation_run",
        "strategy_version",
        "run_id",
    ),
    "run_lookup_by_standard_duplicate": (
        "validation_run",
        "symbol",
        "strategy_version",
        "range_start",
        "range_end",
        "run_id",
    ),
    "run_lookup_by_standard_symbol_date_status": (
        "validation_run",
        "symbol",
        "trading_dates_status",
        "run_id",
    ),
    "run_trading_dates_by_date": ("trading_date", "run_id"),
}
_MAIN_REQUIRED_COLUMNS = {
    "runs": frozenset({"run_id", "manifest_json", "result_json", "created_at", "completed_at"}),
    "trades": frozenset(
        {
            "run_id",
            "ordinal",
            "trade_id",
            "entry_timestamp",
            "exit_timestamp",
            "net_pnl",
            "record_json",
        }
    ),
    "run_lookup": frozenset(
        {
            "run_id",
            "validation_run",
            "strategy_version",
            "strategy_content_sha256",
            "contract_id",
            "symbol",
            "session_name",
            "range_start",
            "range_end",
            "manifest_sha256",
            "trading_dates_status",
            "trading_dates_sha256",
        }
    ),
    "run_trading_dates": frozenset({"run_id", "trading_date"}),
    "run_index_proof": frozenset(
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
    ),
}


class RunReferenceMigrationRequired(RunIndexMigrationRequired):
    """The main source needs the explicit run-index migration before it can serve GET."""


@dataclass(frozen=True, slots=True)
class CatalogRun:
    """One run's query-safe fields, loaded once from a validated source."""

    source: SourceName
    lookup_available: bool
    run_id: str
    validation_run: bool
    strategy_version: str
    strategy_content_sha256: str | None
    contract_id: str
    symbol: str | None
    session_name: str
    range_start: str
    range_end: str
    trading_dates_status: TradingDatesStatus
    trading_dates: tuple[date, ...]

    @classmethod
    def from_lookup_values(
        cls,
        values: _RunLookupValues,
        *,
        source: SourceName,
        lookup_available: bool,
        trading_dates: tuple[date, ...],
    ) -> CatalogRun:
        """Translate persistence-normalized fields into one process-local catalog row."""
        return cls(
            source=source,
            lookup_available=lookup_available,
            run_id=values.run_id,
            validation_run=bool(values.validation_run),
            strategy_version=values.strategy_version,
            strategy_content_sha256=values.strategy_content_sha256,
            contract_id=values.contract_id,
            symbol=values.symbol,
            session_name=values.session_name,
            range_start=values.range_start,
            range_end=values.range_end,
            trading_dates_status=values.trading_dates_status,
            trading_dates=trading_dates,
        )

    @property
    def is_standard(self) -> bool:
        """Keep validation evidence out of every Owner-facing result and candidate list."""
        return not self.validation_run


@dataclass(frozen=True, slots=True)
class RunReferenceCatalog:
    """Validated union of one migrated main source and one read-only audit source."""

    _runs: tuple[CatalogRun, ...]
    main_database: Path
    audit_database: Path | None

    @classmethod
    def audit_runs_from_source(
        cls,
        *,
        audit_database: Path,
        registry: ContractRegistry,
    ) -> tuple[CatalogRun, ...]:
        """Bootstrap the permanent audit entries once without touching the mutable main source."""
        return _read_audit_source(audit_database, registry=registry)

    @classmethod
    def from_sources(
        cls,
        *,
        main_database: Path,
        audit_database: Path | None,
        registry: ContractRegistry,
    ) -> RunReferenceCatalog:
        """Bootstrap once without ever writing to either source database.

        ``audit_database=None`` exists only for isolated API fixtures.  The live
        default always passes the explicit main+audit pair.
        """
        audit_runs = (
            _read_audit_source(audit_database, registry=registry)
            if audit_database is not None
            else ()
        )
        return cls.from_main_with_audit_runs(
            main_database=main_database,
            audit_database=audit_database,
            registry=registry,
            audit_runs=audit_runs,
        )

    @classmethod
    def from_main_with_audit_runs(
        cls,
        *,
        main_database: Path,
        audit_database: Path | None,
        registry: ContractRegistry,
        audit_runs: tuple[CatalogRun, ...],
    ) -> RunReferenceCatalog:
        """Refresh only the mutable main index while retaining audited immutable rows."""
        main_runs = _read_migrated_main_source(main_database, registry=registry)
        runs = tuple(sorted((*main_runs, *audit_runs), key=lambda item: item.run_id))
        _assert_distinct_run_ids(runs)
        return cls(
            _runs=runs,
            main_database=main_database,
            audit_database=audit_database,
        )

    @property
    def run_ids(self) -> tuple[str, ...]:
        """Expose the frozen source union for diagnostics and direct acceptance tests."""
        return tuple(item.run_id for item in self._runs)

    def references_for_strategy(self, *, strategy_version: str) -> RunReferenceList:
        """Return only standard runs for one exact strategy, with relevant unknowns only."""
        references: list[RunReference] = []
        candidates: list[UnindexedRunCandidate] = []
        for run in self._runs:
            if not run.is_standard or run.strategy_version != strategy_version:
                continue
            _append_identity_result(run, references=references, candidates=candidates)
        return _reference_list("strategy", references, candidates)

    def references_for_duplicate(
        self,
        *,
        strategy_version: str,
        symbol: str,
        range_start: str,
        range_end: str,
    ) -> RunReferenceList:
        """Return exact strategy/range matches and symbol-unknown possibilities fail-closed."""
        references: list[RunReference] = []
        candidates: list[UnindexedRunCandidate] = []
        for run in self._runs:
            if (
                not run.is_standard
                or run.strategy_version != strategy_version
                or run.range_start != range_start
                or run.range_end != range_end
            ):
                continue
            if run.symbol is None:
                candidates.append(
                    UnindexedRunCandidate(
                        run_id=run.run_id,
                        reason="root_symbol_unavailable",
                    )
                )
            elif run.symbol == symbol:
                _append_identity_result(run, references=references, candidates=candidates)
        return _reference_list("duplicate", references, candidates)

    def references_for_trading_date(
        self,
        *,
        symbol: str,
        trading_date: date,
        contract: ContractSpec,
    ) -> RunReferenceList:
        """Return proven consumers plus only date-overlapping, standard unknown candidates."""
        if contract.symbol != symbol:
            msg = "trading-date lookup contract must come from the requested registry symbol"
            raise ValueError(msg)
        references: list[RunReference] = []
        candidates: list[UnindexedRunCandidate] = []
        for run in self._runs:
            if not run.is_standard:
                continue
            candidate = _date_candidate_for_run(
                run,
                requested_symbol=symbol,
                trading_date=trading_date,
                contract=contract,
            )
            if candidate is not None:
                candidates.append(candidate)
                continue
            if (
                run.symbol == symbol
                and run.lookup_available
                and run.trading_dates_status == "complete"
                and trading_date in run.trading_dates
            ):
                references.append(_reference_from_run(run))
        return _reference_list("trading-date", references, candidates)


def _read_migrated_main_source(
    path: Path,
    *,
    registry: ContractRegistry,
) -> tuple[CatalogRun, ...]:
    """Read one current proof-verified main snapshot without schema bootstrap or writes."""
    connection = _open_readonly(path, source="main")
    try:
        connection.execute("BEGIN")
        _assert_migrated_main_schema(connection, path)
        _assert_runtime_run_index_proof(connection)
        lookup_rows = connection.execute("SELECT * FROM run_lookup ORDER BY run_id").fetchall()
        date_rows = connection.execute(
            "SELECT run_id, trading_date FROM run_trading_dates ORDER BY run_id, trading_date"
        ).fetchall()
        dates_by_id: dict[str, list[date]] = {}
        for row in date_rows:
            run_id = cast(str, row["run_id"])
            date_rows_for_run = dates_by_id.setdefault(run_id, [])
            date_rows_for_run.append(_parse_catalog_date(cast(str, row["trading_date"])))

        entries: list[CatalogRun] = []
        for row in lookup_rows:
            run_id = cast(str, row["run_id"])
            _assert_lookup_row_shape(row)
            _assert_trading_dates_state(connection, run_id, row)
            entries.append(
                _catalog_run_from_lookup_row(
                    row,
                    source="main",
                    lookup_available=True,
                    trading_dates=tuple(dates_by_id.get(run_id, ())),
                )
            )
        return tuple(entries)
    except sqlite3.Error as exc:
        msg = f"main run-reference source could not be read safely: {exc}"
        raise RunIndexIntegrityError(msg) from exc
    finally:
        connection.close()


def _read_audit_source(path: Path, *, registry: ContractRegistry) -> tuple[CatalogRun, ...]:
    """Derive manifest-proven audit rows in memory while its SQLite bytes remain read-only."""
    connection = _open_readonly(path, source="audit")
    try:
        table_names = {
            cast(str, row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not {"runs", "trades"} <= table_names:
            msg = f"audit run-reference source lacks immutable tables: {path}"
            raise RunIndexIntegrityError(msg)
        rows = connection.execute(
            "SELECT run_id, manifest_json FROM runs ORDER BY run_id"
        ).fetchall()
        return tuple(
            CatalogRun.from_lookup_values(
                _lookup_values_from_stored_manifest(
                    cast(str, row["run_id"]),
                    cast(str, row["manifest_json"]),
                    registry=registry,
                    trading_dates_status="unavailable",
                    trading_dates_sha256=None,
                ),
                source="audit",
                lookup_available=True,
                trading_dates=(),
            )
            for row in rows
        )
    except sqlite3.Error as exc:
        msg = f"audit run-reference source could not be read safely: {exc}"
        raise RunIndexIntegrityError(msg) from exc
    finally:
        connection.close()


def _open_readonly(path: Path, *, source: SourceName) -> sqlite3.Connection:
    """Open an existing source with SQLite's no-write mode and no directory creation."""
    if not path.is_file():
        msg = f"{source} run-reference source does not exist: {path}"
        raise RunIndexIntegrityError(msg)
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error as exc:
        msg = f"{source} run-reference source could not be opened read-only: {exc}"
        raise RunIndexIntegrityError(msg) from exc


def _assert_migrated_main_schema(connection: sqlite3.Connection, path: Path) -> None:
    """Require an explicit migration before the main source can answer GET requests."""
    object_rows = connection.execute(
        "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    table_names = {cast(str, row["name"]) for row in object_rows if row["type"] == "table"}
    index_names = {cast(str, row["name"]) for row in object_rows if row["type"] == "index"}
    if not table_names >= _MAIN_REQUIRED_TABLES or not index_names >= set(
        _MAIN_REQUIRED_INDEX_COLUMNS
    ):
        msg = (
            "run-reference index is not migrated; run: "
            f"futures-research --runs-db {path} migrate-run-index"
        )
        raise RunReferenceMigrationRequired(msg)
    for table, required_columns in _MAIN_REQUIRED_COLUMNS.items():
        column_rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        column_names = {cast(str, row["name"]) for row in column_rows}
        if not required_columns <= column_names:
            msg = f"run-reference index has malformed required table columns: {table}"
            raise RunIndexIntegrityError(msg)
    proof_columns = connection.execute("PRAGMA table_info(run_index_proof)").fetchall()
    proof_id = next((row for row in proof_columns if row["name"] == "proof_id"), None)
    if proof_id is None or proof_id["pk"] != 1:
        msg = "run-reference index has malformed proof publication identity"
        raise RunIndexIntegrityError(msg)
    for index, expected_columns in _MAIN_REQUIRED_INDEX_COLUMNS.items():
        columns = tuple(
            cast(str, row["name"])
            for row in connection.execute(f"PRAGMA index_info({index})").fetchall()
        )
        if columns != expected_columns:
            msg = f"run-reference index has malformed required index: {index}"
            raise RunIndexIntegrityError(msg)


def _assert_lookup_row_shape(row: sqlite3.Row) -> None:
    """Fail closed for malformed indexed identities without rereading their manifest blob."""
    validation = row["validation_run"]
    if validation not in (0, 1):
        msg = f"run_lookup has invalid validation_run for {row['run_id']}"
        raise RunIndexIntegrityError(msg)
    for field in (
        "run_id",
        "strategy_version",
        "contract_id",
        "session_name",
        "range_start",
        "range_end",
        "manifest_sha256",
    ):
        if not isinstance(row[field], str) or not row[field]:
            msg = f"run_lookup has invalid {field} for {row['run_id']}"
            raise RunIndexIntegrityError(msg)
    symbol = row["symbol"]
    if symbol is not None and (not isinstance(symbol, str) or not symbol):
        msg = f"run_lookup has invalid symbol for {row['run_id']}"
        raise RunIndexIntegrityError(msg)


def _catalog_run_from_lookup_row(
    row: sqlite3.Row,
    *,
    source: SourceName,
    lookup_available: bool,
    trading_dates: tuple[date, ...],
) -> CatalogRun:
    """Build a catalog row from an already schema-validated main lookup row."""
    status = row["trading_dates_status"]
    if status not in ("complete", "unavailable"):
        msg = f"run_lookup has invalid trading_dates_status for {row['run_id']}"
        raise RunIndexIntegrityError(msg)
    values = _RunLookupValues(
        run_id=cast(str, row["run_id"]),
        validation_run=cast(int, row["validation_run"]),
        strategy_version=cast(str, row["strategy_version"]),
        strategy_content_sha256=cast(str | None, row["strategy_content_sha256"]),
        contract_id=cast(str, row["contract_id"]),
        symbol=cast(str | None, row["symbol"]),
        session_name=cast(str, row["session_name"]),
        range_start=cast(str, row["range_start"]),
        range_end=cast(str, row["range_end"]),
        manifest_sha256=cast(str, row["manifest_sha256"]),
        trading_dates_status=cast(TradingDatesStatus, status),
        trading_dates_sha256=cast(str | None, row["trading_dates_sha256"]),
    )
    return CatalogRun.from_lookup_values(
        values,
        source=source,
        lookup_available=lookup_available,
        trading_dates=trading_dates,
    )


def _parse_catalog_date(value: str) -> date:
    """Use persistence's date-state validator before materializing a catalog date label."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        msg = f"invalid stored trading_date label: {value!r}"
        raise RunIndexIntegrityError(msg) from exc


def _assert_distinct_run_ids(runs: tuple[CatalogRun, ...]) -> None:
    """Never silently deduplicate a collision across explicitly named sources."""
    seen: set[str] = set()
    for run in runs:
        if run.run_id in seen:
            msg = f"duplicate run_id across run-reference sources: {run.run_id}"
            raise RunIndexIntegrityError(msg)
        seen.add(run.run_id)


def _append_identity_result(
    run: CatalogRun,
    *,
    references: list[RunReference],
    candidates: list[UnindexedRunCandidate],
) -> None:
    """Turn a standard identity into a proven reference or a stable unknown reason."""
    if run.symbol is None:
        candidates.append(
            UnindexedRunCandidate(run_id=run.run_id, reason="root_symbol_unavailable")
        )
    elif not run.lookup_available:
        candidates.append(UnindexedRunCandidate(run_id=run.run_id, reason="lookup_unavailable"))
    else:
        references.append(_reference_from_run(run))


def _date_candidate_for_run(
    run: CatalogRun,
    *,
    requested_symbol: str,
    trading_date: date,
    contract: ContractSpec,
) -> UnindexedRunCandidate | None:
    """Return one candidate only when its unproven source could cover the requested session."""
    if run.symbol is not None and run.symbol != requested_symbol:
        return None
    if run.lookup_available and run.trading_dates_status == "complete":
        return None
    try:
        bounds = session_bounds_for_trading_date(
            contract,
            trading_date,
            session_name=run.session_name,
        )
    except KeyError:
        return UnindexedRunCandidate(run_id=run.run_id, reason="session_bounds_unavailable")
    if bounds is None:
        return None
    session_start, session_end = bounds
    run_start = _parse_utc_timestamp(run.range_start)
    run_end = _parse_utc_timestamp(run.range_end)
    if run_start >= session_end or session_start >= run_end:
        return None
    if run.symbol is None:
        return UnindexedRunCandidate(run_id=run.run_id, reason="root_symbol_unavailable")
    if not run.lookup_available:
        return UnindexedRunCandidate(run_id=run.run_id, reason="lookup_unavailable")
    return UnindexedRunCandidate(run_id=run.run_id, reason="trading_dates_unavailable")


def _reference_from_run(run: CatalogRun) -> RunReference:
    """Build one response row only when a root symbol has been proven."""
    if run.symbol is None:
        msg = f"cannot serialize an unproven root symbol for {run.run_id}"
        raise RunIndexIntegrityError(msg)
    return RunReference(
        run_id=run.run_id,
        strategy_version=run.strategy_version,
        contract_id=run.contract_id,
        symbol=run.symbol,
        session_name=run.session_name,
        range_start=run.range_start,
        range_end=run.range_end,
    )


def _reference_list(
    mode: RunReferenceMode,
    references: list[RunReference],
    candidates: list[UnindexedRunCandidate],
) -> RunReferenceList:
    """Guarantee globally deterministic, non-overlapping response lists."""
    ordered_references = tuple(sorted(references, key=lambda item: item.run_id))
    seen_candidate_ids: set[str] = set()
    ordered_candidates: list[UnindexedRunCandidate] = []
    reference_ids = {item.run_id for item in ordered_references}
    for candidate in sorted(candidates, key=lambda item: item.run_id):
        if candidate.run_id in reference_ids:
            msg = f"run reference appears as both proven and unknown: {candidate.run_id}"
            raise RunIndexIntegrityError(msg)
        if candidate.run_id not in seen_candidate_ids:
            ordered_candidates.append(candidate)
            seen_candidate_ids.add(candidate.run_id)
    return RunReferenceList(
        mode=mode,
        runs=ordered_references,
        unindexed_candidates=tuple(ordered_candidates),
    )
