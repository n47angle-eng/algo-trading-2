"""SQLite v4 append-only authority for the local paper runtime."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from futures_research.api.paper_runtime import (
    LIFECYCLE_STATES,
    LifecycleState,
    PaperTraderCreateRequestV2,
    PaperTraderV2,
    StrategyTimeframeProfileWire,
    selection_fingerprint,
)
from futures_research.paper.models import (
    BaselineMember,
    ClosedMarketInput,
    RuntimeStepWrite,
    canonical_utc,
    finite_number,
)

STORE_VERSION = 4
_STORE_LOCK = threading.RLock()
_ACTIVE_LIFECYCLES = ("starting", "running", "pausing", "stopping")


class PaperStoreError(RuntimeError):
    """Base error for the v4 runtime store."""


class PaperStoreIntegrityError(PaperStoreError):
    """The store cannot prove the exact v4 schema and constraints."""


class PaperStoreRequestConflictError(PaperStoreError):
    """A request identity is already bound to a different canonical payload."""


class PaperStoreLeaseError(PaperStoreError):
    """A different live process owns the single runtime writer lease."""


class PaperStoreInputConflictError(PaperStoreError):
    """One provider/time identity has conflicting immutable content."""


class PaperStoreLifecycleConflictError(PaperStoreError):
    """A lifecycle transition used a stale version or invalid target."""


@dataclass(frozen=True, slots=True)
class StoreIntegrityReport:
    user_version: int
    quick_check: str
    schema_matches: bool
    tables: tuple[str, ...]
    append_only_trigger_count: int


@dataclass(frozen=True, slots=True)
class LeaseResult:
    instance_id: str
    acquired_at: str
    heartbeat_at: str
    stale_takeover: bool


_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE paper_store_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE paper_traders (
    trader_id TEXT PRIMARY KEY CHECK(trader_id GLOB 'trader-[0-9a-f]*' AND length(trader_id)=39),
    request_id TEXT NOT NULL UNIQUE,
    selection_json TEXT NOT NULL CHECK(json_valid(selection_json)),
    selection_fingerprint TEXT NOT NULL CHECK(length(selection_fingerprint)=64),
    strategy_id TEXT NOT NULL,
    strategy_content_sha256 TEXT NOT NULL CHECK(length(strategy_content_sha256)=64),
    contract_id TEXT NOT NULL,
    baseline_run_id TEXT NOT NULL,
    baseline_result_sha256 TEXT NOT NULL CHECK(length(baseline_result_sha256)=64),
    market_input_timeframe TEXT NOT NULL,
    execution_timeframe TEXT NOT NULL,
    chart_display_timeframe TEXT NOT NULL,
    strategy_bias_timeframe TEXT NOT NULL,
    strategy_mid_timeframe TEXT NOT NULL,
    strategy_entry_timeframe TEXT NOT NULL,
    strategy_profile_json TEXT NOT NULL CHECK(json_valid(strategy_profile_json)),
    lifecycle TEXT NOT NULL CHECK(lifecycle IN (
        'provisioned','starting','running','pausing','paused','tripped',
        'stopping','recovery_required','permanently_stopped'
    )),
    lifecycle_version INTEGER NOT NULL CHECK(lifecycle_version >= 1),
    lifecycle_reason TEXT NOT NULL,
    data_mode TEXT CHECK(data_mode IS NULL OR data_mode IN ('live','test_delayed','replay_test')),
    provider_session_id TEXT,
    activated_at TEXT,
    last_received_at TEXT,
    last_trusted_at TEXT,
    stale INTEGER NOT NULL DEFAULT 1 CHECK(stale IN (0,1)),
    blind INTEGER NOT NULL DEFAULT 1 CHECK(blind IN (0,1)),
    flatten_pending INTEGER NOT NULL DEFAULT 0 CHECK(flatten_pending IN (0,1)),
    pending_intent_count INTEGER NOT NULL DEFAULT 0 CHECK(pending_intent_count >= 0),
    open_position_count INTEGER NOT NULL DEFAULT 0 CHECK(open_position_count IN (0,1)),
    decision_count INTEGER NOT NULL DEFAULT 0 CHECK(decision_count >= 0),
    trade_count INTEGER NOT NULL DEFAULT 0 CHECK(trade_count >= 0),
    timeline_cursor INTEGER NOT NULL DEFAULT 0 CHECK(timeline_cursor >= 0),
    chart_cursor INTEGER NOT NULL DEFAULT 0 CHECK(chart_cursor >= 0),
    max_drawdown_r REAL NOT NULL CHECK(max_drawdown_r > 0),
    max_losing_streak INTEGER NOT NULL CHECK(max_losing_streak > 0),
    blind_minutes INTEGER NOT NULL CHECK(blind_minutes > 0),
    equity_high_water_r REAL NOT NULL DEFAULT 0,
    drawdown_r REAL NOT NULL DEFAULT 0,
    losing_streak INTEGER NOT NULL DEFAULT 0 CHECK(losing_streak >= 0),
    account_id TEXT NOT NULL UNIQUE,
    ledger_origin_id TEXT NOT NULL UNIQUE,
    initial_capital REAL NOT NULL CHECK(initial_capital > 0),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_accounts (
    account_id TEXT PRIMARY KEY,
    trader_id TEXT NOT NULL UNIQUE REFERENCES paper_traders(trader_id),
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    realized_r REAL NOT NULL DEFAULT 0,
    unrealized_r REAL NOT NULL DEFAULT 0,
    position_quantity INTEGER NOT NULL DEFAULT 0,
    average_entry_price REAL,
    stop_price REAL,
    target_price REAL,
    entry_fill_id TEXT,
    risk_amount REAL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_baseline_members (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    member_path TEXT NOT NULL,
    content BLOB NOT NULL,
    byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
    sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    UNIQUE(trader_id, member_path)
) STRICT;

CREATE TABLE paper_market_inputs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    input_id TEXT NOT NULL UNIQUE,
    provider_session_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('live','test_delayed','replay_test')),
    event_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL CHECK(high_price >= open_price),
    low_price REAL NOT NULL CHECK(low_price <= open_price),
    close_price REAL NOT NULL CHECK(high_price >= close_price AND low_price <= close_price),
    volume REAL NOT NULL CHECK(volume >= 0),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    source_kind TEXT NOT NULL CHECK(source_kind IN ('live','recovered')),
    UNIQUE(provider_session_id, contract_id, timeframe, mode, event_at)
) STRICT;

CREATE TABLE paper_decisions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    input_id TEXT NOT NULL REFERENCES paper_market_inputs(input_id),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    recovered_processing INTEGER NOT NULL CHECK(recovered_processing IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(trader_id, input_id)
) STRICT;

CREATE TABLE paper_intents (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    decision_id TEXT REFERENCES paper_decisions(decision_id),
    status TEXT NOT NULL CHECK(status IN ('pending','cancelled','filled')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_orders (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    input_id TEXT NOT NULL REFERENCES paper_market_inputs(input_id),
    intent_id TEXT REFERENCES paper_intents(intent_id),
    transport TEXT NOT NULL CHECK(transport='app_simulated'),
    status TEXT NOT NULL CHECK(status IN ('armed','filled','cancelled')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    created_at TEXT NOT NULL,
    UNIQUE(trader_id, input_id, order_id)
) STRICT;

CREATE TABLE paper_fills (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
    input_id TEXT NOT NULL REFERENCES paper_market_inputs(input_id),
    quantity INTEGER NOT NULL CHECK(quantity != 0),
    price REAL NOT NULL CHECK(price > 0),
    commission REAL NOT NULL CHECK(commission >= 0),
    slippage REAL NOT NULL CHECK(slippage >= 0),
    created_at TEXT NOT NULL,
    UNIQUE(trader_id, order_id, input_id)
) STRICT;

CREATE TABLE paper_trades (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    entry_fill_id TEXT NOT NULL REFERENCES paper_fills(fill_id),
    exit_fill_id TEXT NOT NULL REFERENCES paper_fills(fill_id),
    side TEXT NOT NULL CHECK(side IN ('long','short')),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    gross_pnl REAL NOT NULL,
    net_pnl REAL NOT NULL,
    net_r REAL NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_position_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    position_event_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    input_id TEXT NOT NULL REFERENCES paper_market_inputs(input_id),
    quantity INTEGER NOT NULL,
    average_entry_price REAL,
    stop_price REAL,
    target_price REAL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(trader_id, input_id)
) STRICT;

CREATE TABLE paper_equity_points (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    equity_point_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    input_id TEXT NOT NULL REFERENCES paper_market_inputs(input_id),
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    realized_r REAL NOT NULL,
    unrealized_r REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(trader_id, input_id)
) STRICT;

CREATE TABLE paper_lifecycle_commands (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    command_kind TEXT NOT NULL CHECK(command_kind IN ('start','pause','resume','permanent_stop')),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    expected_lifecycle_version INTEGER NOT NULL,
    lifecycle_before TEXT NOT NULL,
    lifecycle_after TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_lifecycle_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    lifecycle_event_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    lifecycle TEXT NOT NULL,
    lifecycle_version INTEGER NOT NULL CHECK(lifecycle_version >= 1),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_runtime_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    input_id TEXT REFERENCES paper_market_inputs(input_id),
    event_kind TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('info','warning','blocked')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_safety_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    safety_event_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    trigger_kind TEXT NOT NULL CHECK(trigger_kind IN ('drawdown','losing_streak','blind','manual')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_safety_epochs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    safety_epoch_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    epoch_number INTEGER NOT NULL CHECK(epoch_number >= 1),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(trader_id, epoch_number)
) STRICT;

CREATE TABLE paper_processing_checkpoints (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    input_id TEXT NOT NULL REFERENCES paper_market_inputs(input_id),
    input_sequence INTEGER NOT NULL CHECK(input_sequence >= 1),
    lifecycle_version INTEGER NOT NULL CHECK(lifecycle_version >= 1),
    created_at TEXT NOT NULL,
    UNIQUE(trader_id, input_id),
    UNIQUE(trader_id, input_sequence)
) STRICT;

CREATE TABLE paper_runtime_leases (
    lease_key TEXT PRIMARY KEY CHECK(lease_key='runtime-writer'),
    instance_id TEXT NOT NULL,
    pid INTEGER NOT NULL CHECK(pid > 0),
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_runtime_command_results (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    http_status INTEGER NOT NULL CHECK(http_status IN (200,202)),
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_review_v2_requests (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL REFERENCES paper_traders(trader_id),
    snapshot_id TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    cutoff_json TEXT NOT NULL CHECK(json_valid(cutoff_json)),
    opener_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('preparing','ready','failed')),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_review_v2_ready (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE REFERENCES paper_review_v2_requests(request_id),
    snapshot_id TEXT NOT NULL UNIQUE,
    display_filename TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    artifact_bytes INTEGER NOT NULL CHECK(artifact_bytes > 0),
    artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256)=64),
    opener_bytes INTEGER NOT NULL CHECK(opener_bytes >= 0),
    opener_sha256 TEXT NOT NULL CHECK(length(opener_sha256)=64),
    member_manifest_json TEXT NOT NULL CHECK(json_valid(member_manifest_json)),
    ready_at TEXT NOT NULL
) STRICT;

CREATE TABLE paper_review_v2_failures (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE REFERENCES paper_review_v2_requests(request_id),
    snapshot_id TEXT NOT NULL UNIQUE,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    failed_at TEXT NOT NULL
) STRICT;

CREATE INDEX paper_market_inputs_topic_order
ON paper_market_inputs(contract_id, timeframe, mode, sequence);
CREATE INDEX paper_runtime_events_trader_order
ON paper_runtime_events(trader_id, sequence);
CREATE INDEX paper_equity_points_trader_order
ON paper_equity_points(trader_id, sequence);
"""

_APPEND_ONLY_TABLES = (
    "paper_baseline_members",
    "paper_market_inputs",
    "paper_decisions",
    "paper_intents",
    "paper_orders",
    "paper_fills",
    "paper_trades",
    "paper_position_events",
    "paper_equity_points",
    "paper_lifecycle_commands",
    "paper_lifecycle_events",
    "paper_runtime_events",
    "paper_safety_events",
    "paper_safety_epochs",
    "paper_processing_checkpoints",
    "paper_runtime_command_results",
    "paper_review_v2_ready",
    "paper_review_v2_failures",
)


class PaperRuntimeStore:
    """Explicitly initialized v4 writer/read model."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        """Create a brand-new v4 store or validate an existing exact v4 store."""
        with _STORE_LOCK:
            existed = self.path.exists()
            if existed:
                with self._connect() as connection:
                    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version != STORE_VERSION:
                    raise PaperStoreIntegrityError(
                        "paper runtime requires exact schema version 4; "
                        f"found {version}"
                    )
                self._require_integrity()
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            try:
                _apply_schema(connection)
                connection.execute(
                    "INSERT INTO paper_store_meta(key, value) VALUES (?, ?)",
                    ("store_profile", "paper-runtime.v4"),
                )
                connection.execute(f"PRAGMA user_version = {STORE_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                connection.close()
                raise
            connection.close()
            self._require_integrity()

    def integrity_check(self) -> StoreIntegrityReport:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            objects = _schema_objects(connection)
            tables = tuple(
                row[1]
                for row in objects
                if row[0] == "table" and not row[1].startswith("sqlite_")
            )
            trigger_count = sum(
                1
                for kind, name, _sql in objects
                if kind == "trigger" and name.startswith("guard_append_only_")
            )
            return StoreIntegrityReport(
                user_version=version,
                quick_check=quick,
                schema_matches=_schema_digest(objects) == _EXPECTED_SCHEMA_DIGEST,
                tables=tables,
                append_only_trigger_count=trigger_count,
            )

    def create_trader(
        self,
        *,
        body: PaperTraderCreateRequestV2,
        strategy_profile: StrategyTimeframeProfileWire,
        initial_capital: int | float,
        baseline_members: tuple[BaselineMember, ...],
        now: datetime,
        forced_trader_id: str | None = None,
        forced_account_id: str | None = None,
        forced_ledger_id: str | None = None,
    ) -> tuple[PaperTraderV2, bool]:
        capital = finite_number(initial_capital, field_name="initial_capital")
        if capital <= 0:
            raise ValueError("initial_capital must be positive")
        _validate_baseline_members(body, baseline_members)
        fingerprint = selection_fingerprint(body.selection)
        timestamp = canonical_utc(now)
        # Default derivation aligns Stage A + runtime. Mirror may force Stage A
        # identities when tests inject custom Stage A factories.
        trader_id = forced_trader_id or _derived_id("trader", body.request_id)
        account_id = forced_account_id or _derived_id(
            "paper-account", body.request_id
        )
        ledger_id = forced_ledger_id or _derived_id(
            "paper-ledger", body.request_id
        )
        if not trader_id.startswith("trader-") or len(trader_id) != 39:
            raise ValueError("forced trader_id must be canonical trader-* identity")
        if not account_id.startswith("paper-account-") or len(account_id) != 46:
            raise ValueError("forced account_id must be canonical paper-account-*")
        if not ledger_id.startswith("paper-ledger-") or len(ledger_id) != 45:
            raise ValueError("forced ledger_id must be canonical paper-ledger-*")
        selection_json = _canonical_json(body.selection.model_dump(mode="json"))
        profile_json = _canonical_json(strategy_profile.model_dump(mode="json"))
        with _STORE_LOCK, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM paper_traders WHERE request_id=?",
                    (body.request_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["selection_fingerprint"] != fingerprint
                        or existing["selection_json"] != selection_json
                    ):
                        raise PaperStoreRequestConflictError(
                            "request_id_conflict: create request payload differs"
                        )
                    connection.rollback()
                    return self._trader_from_row(existing), False
                connection.execute(
                    """
                    INSERT INTO paper_traders(
                        trader_id, request_id, selection_json, selection_fingerprint,
                        strategy_id, strategy_content_sha256, contract_id,
                        baseline_run_id, baseline_result_sha256,
                        market_input_timeframe, execution_timeframe,
                        chart_display_timeframe, strategy_bias_timeframe,
                        strategy_mid_timeframe, strategy_entry_timeframe,
                        strategy_profile_json, lifecycle, lifecycle_version,
                        lifecycle_reason, max_drawdown_r, max_losing_streak,
                        blind_minutes, account_id, ledger_origin_id,
                        initial_capital, created_at
                    ) VALUES (
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        'provisioned',1,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        trader_id,
                        body.request_id,
                        selection_json,
                        fingerprint,
                        body.selection.strategy_id,
                        body.selection.content_sha256,
                        body.selection.contract_id,
                        body.selection.baseline_run_id,
                        body.selection.baseline_result_sha256,
                        body.selection.timeframes.market_input,
                        body.selection.timeframes.execution,
                        body.selection.timeframes.chart_display,
                        strategy_profile.bias,
                        strategy_profile.mid,
                        strategy_profile.entry,
                        profile_json,
                        "trader created; runtime has not started",
                        8.0,
                        8,
                        5,
                        account_id,
                        ledger_id,
                        capital,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO paper_accounts(
                        account_id,trader_id,cash,equity,updated_at
                    ) VALUES (?,?,?,?,?)
                    """,
                    (account_id, trader_id, capital, capital, timestamp),
                )
                for member in baseline_members:
                    connection.execute(
                        """
                        INSERT INTO paper_baseline_members(
                            trader_id,member_path,content,byte_length,sha256
                        ) VALUES (?,?,?,?,?)
                        """,
                        (
                            trader_id,
                            member.path,
                            member.content,
                            member.byte_length,
                            member.sha256,
                        ),
                    )
                _append_lifecycle_event(
                    connection,
                    trader_id=trader_id,
                    lifecycle="provisioned",
                    lifecycle_version=1,
                    reason="trader created; runtime has not started",
                    created_at=timestamp,
                )
                row = connection.execute(
                    "SELECT * FROM paper_traders WHERE trader_id=?",
                    (trader_id,),
                ).fetchone()
                assert row is not None
                result = self._trader_from_row(row)
                connection.commit()
                return result, True
            except Exception:
                connection.rollback()
                raise

    def get_trader(self, trader_id: str) -> PaperTraderV2:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_traders WHERE trader_id=?",
                (trader_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"paper trader not found: {trader_id}")
        return self._trader_from_row(row)

    def list_traders(self) -> tuple[PaperTraderV2, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_traders ORDER BY created_at, trader_id"
            ).fetchall()
        return tuple(self._trader_from_row(row) for row in rows)

    def append_market_input(
        self,
        market_input: ClosedMarketInput,
    ) -> tuple[int, bool]:
        with _STORE_LOCK, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO paper_market_inputs(
                        input_id,provider_session_id,contract_id,timeframe,mode,
                        event_at,received_at,open_price,high_price,low_price,
                        close_price,volume,payload_sha256,source_kind
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(input_id) DO NOTHING
                    """,
                    (
                        market_input.input_id,
                        market_input.provider_session_id,
                        market_input.contract_id,
                        market_input.timeframe,
                        market_input.mode,
                        market_input.event_at,
                        market_input.received_at,
                        market_input.open_price,
                        market_input.high_price,
                        market_input.low_price,
                        market_input.close_price,
                        market_input.volume,
                        market_input.payload_sha256,
                        market_input.source_kind,
                    ),
                )
                if cursor.rowcount == 1:
                    if cursor.lastrowid is None:
                        raise PaperStoreIntegrityError(
                            "market input insert returned no sequence"
                        )
                    sequence = cursor.lastrowid
                    connection.commit()
                    return sequence, True
                row = connection.execute(
                    "SELECT sequence,payload_sha256 FROM paper_market_inputs "
                    "WHERE input_id=?",
                    (market_input.input_id,),
                ).fetchone()
                if row is None or row["payload_sha256"] != market_input.payload_sha256:
                    raise PaperStoreInputConflictError(
                        "market_input_identity_conflict"
                    )
                connection.rollback()
                return int(row["sequence"]), False
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                row = connection.execute(
                    """
                    SELECT input_id,payload_sha256 FROM paper_market_inputs
                    WHERE provider_session_id=? AND contract_id=? AND timeframe=?
                      AND mode=? AND event_at=?
                    """,
                    (
                        market_input.provider_session_id,
                        market_input.contract_id,
                        market_input.timeframe,
                        market_input.mode,
                        market_input.event_at,
                    ),
                ).fetchone()
                if row is not None and (
                    row["input_id"] != market_input.input_id
                    or row["payload_sha256"] != market_input.payload_sha256
                ):
                    raise PaperStoreInputConflictError(
                        "market_input_identity_conflict"
                    ) from exc
                raise

    def append_market_inputs_atomic(
        self,
        market_inputs: tuple[ClosedMarketInput, ...],
    ) -> tuple[tuple[int, bool], ...]:
        """Append a prevalidated recovery batch wholly or not at all."""
        if not market_inputs:
            raise ValueError("market input batch must not be empty")
        results: list[tuple[int, bool]] = []
        with _STORE_LOCK, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for market_input in market_inputs:
                    try:
                        cursor = connection.execute(
                            """
                            INSERT INTO paper_market_inputs(
                                input_id,provider_session_id,contract_id,timeframe,mode,
                                event_at,received_at,open_price,high_price,low_price,
                                close_price,volume,payload_sha256,source_kind
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(input_id) DO NOTHING
                            """,
                            (
                                market_input.input_id,
                                market_input.provider_session_id,
                                market_input.contract_id,
                                market_input.timeframe,
                                market_input.mode,
                                market_input.event_at,
                                market_input.received_at,
                                market_input.open_price,
                                market_input.high_price,
                                market_input.low_price,
                                market_input.close_price,
                                market_input.volume,
                                market_input.payload_sha256,
                                market_input.source_kind,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise PaperStoreInputConflictError(
                            "market_input_identity_conflict"
                        ) from exc
                    if cursor.rowcount == 1:
                        if cursor.lastrowid is None:
                            raise PaperStoreIntegrityError(
                                "market input insert returned no sequence"
                            )
                        results.append((cursor.lastrowid, True))
                        continue
                    row = connection.execute(
                        """
                        SELECT sequence,payload_sha256 FROM paper_market_inputs
                        WHERE input_id=?
                        """,
                        (market_input.input_id,),
                    ).fetchone()
                    if (
                        row is None
                        or row["payload_sha256"] != market_input.payload_sha256
                    ):
                        raise PaperStoreInputConflictError(
                            "market_input_identity_conflict"
                        )
                    results.append((int(row["sequence"]), False))
                connection.commit()
                return tuple(results)
            except Exception:
                connection.rollback()
                raise

    def transition_lifecycle(
        self,
        *,
        trader_id: str,
        expected_version: int,
        to_state: LifecycleState,
        reason: str,
        now: datetime,
        flatten_pending: bool = False,
    ) -> PaperTraderV2:
        if to_state not in LIFECYCLE_STATES:
            raise PaperStoreLifecycleConflictError("unknown lifecycle target")
        timestamp = canonical_utc(now)
        with _STORE_LOCK, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM paper_traders WHERE trader_id=?",
                    (trader_id,),
                ).fetchone()
                if row is None:
                    raise LookupError(f"paper trader not found: {trader_id}")
                if int(row["lifecycle_version"]) != expected_version:
                    raise PaperStoreLifecycleConflictError(
                        "stale_lifecycle_version"
                    )
                if row["lifecycle"] == "permanently_stopped":
                    raise PaperStoreLifecycleConflictError(
                        "permanently_stopped is irreversible"
                    )
                version = expected_version + 1
                connection.execute(
                    """
                    UPDATE paper_traders
                    SET lifecycle=?, lifecycle_version=?, lifecycle_reason=?,
                        flatten_pending=?
                    WHERE trader_id=?
                    """,
                    (to_state, version, reason, int(flatten_pending), trader_id),
                )
                _append_lifecycle_event(
                    connection,
                    trader_id=trader_id,
                    lifecycle=to_state,
                    lifecycle_version=version,
                    reason=reason,
                    created_at=timestamp,
                )
                updated = connection.execute(
                    "SELECT * FROM paper_traders WHERE trader_id=?",
                    (trader_id,),
                ).fetchone()
                assert updated is not None
                result = self._trader_from_row(updated)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def acquire_lease(
        self,
        *,
        instance_id: str,
        pid: int,
        now: datetime,
        stale_after_seconds: int = 30,
    ) -> LeaseResult:
        if not instance_id or instance_id != instance_id.strip() or pid <= 0:
            raise ValueError("lease identity and pid must be valid")
        timestamp = canonical_utc(now)
        with _STORE_LOCK, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM paper_runtime_leases WHERE lease_key='runtime-writer'"
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO paper_runtime_leases(
                            lease_key,instance_id,pid,acquired_at,heartbeat_at
                        ) VALUES ('runtime-writer',?,?,?,?)
                        """,
                        (instance_id, pid, timestamp, timestamp),
                    )
                    connection.commit()
                    return LeaseResult(instance_id, timestamp, timestamp, False)
                if row["instance_id"] == instance_id and int(row["pid"]) == pid:
                    connection.execute(
                        """
                        UPDATE paper_runtime_leases SET heartbeat_at=?
                        WHERE lease_key='runtime-writer'
                        """,
                        (timestamp,),
                    )
                    connection.commit()
                    return LeaseResult(
                        instance_id,
                        str(row["acquired_at"]),
                        timestamp,
                        False,
                    )
                heartbeat = datetime.fromisoformat(
                    str(row["heartbeat_at"]).removesuffix("Z") + "+00:00"
                )
                age = (now.astimezone(UTC) - heartbeat).total_seconds()
                if age <= stale_after_seconds:
                    raise PaperStoreLeaseError(
                        "single_writer_lease_unavailable"
                    )
                active_rows = connection.execute(
                    """
                    SELECT trader_id,lifecycle_version FROM paper_traders
                    WHERE lifecycle IN ('starting','running','pausing','stopping')
                    ORDER BY trader_id
                    """
                ).fetchall()
                for active in active_rows:
                    version = int(active["lifecycle_version"]) + 1
                    reason = "runtime writer lease expired; Owner recovery required"
                    connection.execute(
                        """
                        UPDATE paper_traders
                        SET lifecycle='recovery_required',
                            lifecycle_version=?, lifecycle_reason=?
                        WHERE trader_id=?
                        """,
                        (version, reason, active["trader_id"]),
                    )
                    _append_lifecycle_event(
                        connection,
                        trader_id=str(active["trader_id"]),
                        lifecycle="recovery_required",
                        lifecycle_version=version,
                        reason=reason,
                        created_at=timestamp,
                    )
                connection.execute(
                    """
                    UPDATE paper_runtime_leases
                    SET instance_id=?,pid=?,acquired_at=?,heartbeat_at=?
                    WHERE lease_key='runtime-writer'
                    """,
                    (instance_id, pid, timestamp, timestamp),
                )
                connection.commit()
                return LeaseResult(instance_id, timestamp, timestamp, True)
            except Exception:
                connection.rollback()
                raise

    def heartbeat_lease(self, *, instance_id: str, pid: int, now: datetime) -> None:
        timestamp = canonical_utc(now)
        with _STORE_LOCK, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE paper_runtime_leases SET heartbeat_at=?
                WHERE lease_key='runtime-writer' AND instance_id=? AND pid=?
                """,
                (timestamp, instance_id, pid),
            )
            if cursor.rowcount != 1:
                raise PaperStoreLeaseError("single_writer_lease_lost")
            connection.commit()

    def release_lease(self, *, instance_id: str, pid: int) -> None:
        with _STORE_LOCK, self._connect() as connection:
            connection.execute(
                """
                DELETE FROM paper_runtime_leases
                WHERE lease_key='runtime-writer' AND instance_id=? AND pid=?
                """,
                (instance_id, pid),
            )
            connection.commit()

    def has_processing_checkpoint(self, *, trader_id: str, input_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM paper_processing_checkpoints
                WHERE trader_id=? AND input_id=?
                """,
                (trader_id, input_id),
            ).fetchone()
        return row is not None

    def commit_runtime_step(
        self,
        *,
        trader_id: str,
        input_id: str,
        step: RuntimeStepWrite,
        recovered_processing: bool,
        now: datetime,
    ) -> bool:
        """Commit all side effects and the checkpoint for one closed input."""
        timestamp = canonical_utc(now)
        for value, field_name in (
            (step.cash, "cash"),
            (step.equity, "equity"),
            (step.realized_pnl, "realized_pnl"),
            (step.unrealized_pnl, "unrealized_pnl"),
            (step.realized_r, "realized_r"),
            (step.unrealized_r, "unrealized_r"),
            (step.equity_high_water_r, "equity_high_water_r"),
            (step.drawdown_r, "drawdown_r"),
        ):
            finite_number(value, field_name=field_name)
        if step.losing_streak < 0 or step.pending_intent_count < 0:
            raise ValueError("runtime counters must be non-negative")
        with _STORE_LOCK, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    """
                    SELECT 1 FROM paper_processing_checkpoints
                    WHERE trader_id=? AND input_id=?
                    """,
                    (trader_id, input_id),
                ).fetchone() is not None:
                    connection.rollback()
                    return False
                input_row = connection.execute(
                    "SELECT sequence,event_at FROM paper_market_inputs WHERE input_id=?",
                    (input_id,),
                ).fetchone()
                trader = connection.execute(
                    "SELECT * FROM paper_traders WHERE trader_id=?",
                    (trader_id,),
                ).fetchone()
                account = connection.execute(
                    "SELECT * FROM paper_accounts WHERE trader_id=?",
                    (trader_id,),
                ).fetchone()
                if input_row is None or trader is None or account is None:
                    raise LookupError("runtime processing identity is missing")
                latest = connection.execute(
                    """
                    SELECT MAX(input_sequence) FROM paper_processing_checkpoints
                    WHERE trader_id=?
                    """,
                    (trader_id,),
                ).fetchone()[0]
                input_sequence = int(input_row["sequence"])
                if latest is not None and input_sequence <= int(latest):
                    raise PaperStoreInputConflictError(
                        "out_of_order trader input cursor"
                    )
                identity = sha256(f"{trader_id}\0{input_id}".encode()).hexdigest()
                decision_id: str | None = None
                if step.decision_payload_json is not None:
                    decision_id = f"paper-decision-{identity}"
                    connection.execute(
                        """
                        INSERT INTO paper_decisions(
                            decision_id,trader_id,input_id,payload_json,
                            recovered_processing,created_at
                        ) VALUES (?,?,?,?,?,?)
                        """,
                        (
                            decision_id,
                            trader_id,
                            input_id,
                            step.decision_payload_json,
                            int(recovered_processing),
                            timestamp,
                        ),
                    )
                logical_intent_id: str | None = None
                if step.intent_payload_json is not None:
                    logical_intent_id = f"paper-intent-{identity}"
                    status = "filled" if any(
                        fill.role == "entry" for fill in step.fills
                    ) else "pending"
                    connection.execute(
                        """
                        INSERT INTO paper_intents(
                            intent_id,trader_id,decision_id,status,payload_json,
                            created_at
                        ) VALUES (?,?,?,?,?,?)
                        """,
                        (
                            logical_intent_id,
                            trader_id,
                            decision_id,
                            status,
                            step.intent_payload_json,
                            timestamp,
                        ),
                    )
                elif any(fill.role == "entry" for fill in step.fills):
                    pending = connection.execute(
                        """
                        SELECT intent_id FROM paper_intents
                        WHERE trader_id=? AND status='pending'
                        ORDER BY sequence DESC LIMIT 1
                        """,
                        (trader_id,),
                    ).fetchone()
                    if pending is not None:
                        logical_intent_id = str(pending["intent_id"])
                entry_fill_id = (
                    None
                    if account["entry_fill_id"] is None
                    else str(account["entry_fill_id"])
                )
                exit_fill_id: str | None = None
                for index, fill in enumerate(step.fills):
                    fill_price = finite_number(fill.price, field_name="fill.price")
                    commission = finite_number(
                        fill.commission,
                        field_name="fill.commission",
                    )
                    slippage = finite_number(
                        fill.slippage,
                        field_name="fill.slippage",
                    )
                    if fill.quantity == 0 or fill_price <= 0:
                        raise ValueError("fill must have non-zero quantity and price")
                    order_id = f"paper-order-{identity}-{index}"
                    fill_id = f"paper-fill-{identity}-{index}"
                    connection.execute(
                        """
                        INSERT INTO paper_orders(
                            order_id,trader_id,input_id,intent_id,transport,status,
                            payload_json,created_at
                        ) VALUES (?,?,?,?,'app_simulated','filled',?,?)
                        """,
                        (
                            order_id,
                            trader_id,
                            input_id,
                            logical_intent_id if fill.role == "entry" else None,
                            fill.payload_json,
                            timestamp,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO paper_fills(
                            fill_id,trader_id,order_id,input_id,quantity,price,
                            commission,slippage,created_at
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            fill_id,
                            trader_id,
                            order_id,
                            input_id,
                            fill.quantity,
                            fill_price,
                            commission,
                            slippage,
                            timestamp,
                        ),
                    )
                    if fill.role == "entry":
                        entry_fill_id = fill_id
                    else:
                        exit_fill_id = fill_id
                if step.completed_trade is not None:
                    trade = step.completed_trade
                    if entry_fill_id is None or exit_fill_id is None:
                        raise PaperStoreIntegrityError(
                            "completed trade must reference entry and exit fills"
                        )
                    connection.execute(
                        """
                        INSERT INTO paper_trades(
                            trade_id,trader_id,entry_fill_id,exit_fill_id,side,
                            quantity,gross_pnl,net_pnl,net_r,opened_at,closed_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            f"paper-trade-{identity}",
                            trader_id,
                            entry_fill_id,
                            exit_fill_id,
                            trade.side,
                            trade.quantity,
                            trade.gross_pnl,
                            trade.net_pnl,
                            trade.net_r,
                            trade.opened_at,
                            trade.closed_at,
                        ),
                    )
                    entry_fill_id = None
                if (
                    int(account["position_quantity"]) != step.position_quantity
                    or step.fills
                ):
                    connection.execute(
                        """
                        INSERT INTO paper_position_events(
                            position_event_id,trader_id,input_id,quantity,
                            average_entry_price,stop_price,target_price,reason,
                            created_at
                        ) VALUES (?,?,?,?,?,?,?,'shared execution update',?)
                        """,
                        (
                            f"paper-position-{identity}",
                            trader_id,
                            input_id,
                            step.position_quantity,
                            step.average_entry_price,
                            step.stop_price,
                            step.target_price,
                            timestamp,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO paper_equity_points(
                        equity_point_id,trader_id,input_id,cash,equity,
                        realized_pnl,unrealized_pnl,realized_r,unrealized_r,
                        created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"paper-equity-{identity}",
                        trader_id,
                        input_id,
                        step.cash,
                        step.equity,
                        step.realized_pnl,
                        step.unrealized_pnl,
                        step.realized_r,
                        step.unrealized_r,
                        timestamp,
                    ),
                )
                for index, payload in enumerate(step.event_payloads_json):
                    connection.execute(
                        """
                        INSERT INTO paper_runtime_events(
                            event_id,trader_id,input_id,event_kind,severity,
                            payload_json,created_at
                        ) VALUES (?,?,?,'execution','info',?,?)
                        """,
                        (
                            f"paper-event-{identity}-{index}",
                            trader_id,
                            input_id,
                            payload,
                            timestamp,
                        ),
                    )
                lifecycle = str(trader["lifecycle"])
                lifecycle_version = int(trader["lifecycle_version"])
                lifecycle_reason = str(trader["lifecycle_reason"])
                if step.safety_trigger is not None:
                    connection.execute(
                        """
                        INSERT INTO paper_safety_events(
                            safety_event_id,trader_id,trigger_kind,payload_json,
                            created_at
                        ) VALUES (?,?,?,?,?)
                        """,
                        (
                            f"paper-safety-{identity}",
                            trader_id,
                            step.safety_trigger,
                            _canonical_json(
                                {
                                    "drawdown_r": step.drawdown_r,
                                    "losing_streak": step.losing_streak,
                                }
                            ),
                            timestamp,
                        ),
                    )
                if step.lifecycle_after is not None:
                    lifecycle = step.lifecycle_after
                    lifecycle_version += 1
                    lifecycle_reason = step.lifecycle_reason or "runtime state changed"
                    _append_lifecycle_event(
                        connection,
                        trader_id=trader_id,
                        lifecycle=lifecycle,
                        lifecycle_version=lifecycle_version,
                        reason=lifecycle_reason,
                        created_at=timestamp,
                    )
                position_count = int(step.position_quantity != 0)
                risk_amount = None
                if (
                    position_count
                    and step.average_entry_price is not None
                    and step.stop_price is not None
                ):
                    risk_amount = abs(
                        step.average_entry_price - step.stop_price
                    ) * abs(step.position_quantity)
                connection.execute(
                    """
                    UPDATE paper_accounts SET
                        cash=?,equity=?,realized_pnl=?,unrealized_pnl=?,
                        realized_r=?,unrealized_r=?,position_quantity=?,
                        average_entry_price=?,stop_price=?,target_price=?,
                        entry_fill_id=?,risk_amount=?,updated_at=?
                    WHERE trader_id=?
                    """,
                    (
                        step.cash,
                        step.equity,
                        step.realized_pnl,
                        step.unrealized_pnl,
                        step.realized_r,
                        step.unrealized_r,
                        step.position_quantity,
                        step.average_entry_price,
                        step.stop_price,
                        step.target_price,
                        entry_fill_id,
                        risk_amount,
                        timestamp,
                        trader_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE paper_traders SET
                        lifecycle=?,lifecycle_version=?,lifecycle_reason=?,
                        provider_session_id=(
                            SELECT provider_session_id FROM paper_market_inputs
                            WHERE input_id=?
                        ),
                        data_mode=(
                            SELECT mode FROM paper_market_inputs WHERE input_id=?
                        ),
                        last_received_at=(
                            SELECT received_at FROM paper_market_inputs WHERE input_id=?
                        ),
                        last_trusted_at=(
                            SELECT event_at FROM paper_market_inputs WHERE input_id=?
                        ),
                        stale=0,blind=0,
                        flatten_pending=?,
                        pending_intent_count=?,
                        open_position_count=?,
                        decision_count=decision_count+?,
                        trade_count=trade_count+?,
                        timeline_cursor=timeline_cursor+?,
                        chart_cursor=?,
                        equity_high_water_r=?,drawdown_r=?,losing_streak=?
                    WHERE trader_id=?
                    """,
                    (
                        lifecycle,
                        lifecycle_version,
                        lifecycle_reason,
                        input_id,
                        input_id,
                        input_id,
                        input_id,
                        int(step.flatten_pending),
                        step.pending_intent_count,
                        position_count,
                        int(step.decision_payload_json is not None),
                        int(step.completed_trade is not None),
                        len(step.event_payloads_json)
                        + int(step.decision_payload_json is not None)
                        + len(step.fills)
                        + int(step.completed_trade is not None),
                        input_sequence,
                        step.equity_high_water_r,
                        step.drawdown_r,
                        step.losing_streak,
                        trader_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO paper_processing_checkpoints(
                        checkpoint_id,trader_id,input_id,input_sequence,
                        lifecycle_version,created_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        f"paper-checkpoint-{identity}",
                        trader_id,
                        input_id,
                        input_sequence,
                        lifecycle_version,
                        timestamp,
                    ),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def append_processing_evidence(
        self,
        *,
        trader_id: str,
        input_id: str,
        decision_payload: str,
        order_payload: str,
        fill_price: int | float,
        position_quantity: int,
        cash: int | float,
        equity: int | float,
        now: datetime,
    ) -> None:
        """Atomically append a minimal decision/fill/projection checkpoint batch."""
        timestamp = canonical_utc(now)
        price = finite_number(fill_price, field_name="fill_price")
        cash_value = finite_number(cash, field_name="cash")
        equity_value = finite_number(equity, field_name="equity")
        identity = sha256(f"{trader_id}\0{input_id}".encode()).hexdigest()
        with _STORE_LOCK, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                input_row = connection.execute(
                    "SELECT sequence FROM paper_market_inputs WHERE input_id=?",
                    (input_id,),
                ).fetchone()
                trader = connection.execute(
                    "SELECT lifecycle_version FROM paper_traders WHERE trader_id=?",
                    (trader_id,),
                ).fetchone()
                if input_row is None or trader is None:
                    raise LookupError("processing identity is missing")
                decision_id = f"paper-decision-{identity}"
                order_id = f"paper-order-{identity}"
                fill_id = f"paper-fill-{identity}"
                connection.execute(
                    """
                    INSERT INTO paper_decisions(
                        decision_id,trader_id,input_id,payload_json,
                        recovered_processing,created_at
                    ) VALUES (?,?,?,?,0,?)
                    """,
                    (
                        decision_id,
                        trader_id,
                        input_id,
                        decision_payload,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO paper_orders(
                        order_id,trader_id,input_id,transport,status,
                        payload_json,created_at
                    ) VALUES (?,?,?,'app_simulated','filled',?,?)
                    """,
                    (order_id, trader_id, input_id, order_payload, timestamp),
                )
                connection.execute(
                    """
                    INSERT INTO paper_fills(
                        fill_id,trader_id,order_id,input_id,quantity,price,
                        commission,slippage,created_at
                    ) VALUES (?,?,?,?,?,?,0,0,?)
                    """,
                    (
                        fill_id,
                        trader_id,
                        order_id,
                        input_id,
                        position_quantity,
                        price,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO paper_position_events(
                        position_event_id,trader_id,input_id,quantity,
                        average_entry_price,reason,created_at
                    ) VALUES (?,?,?,?,?,'simulated fill',?)
                    """,
                    (
                        f"paper-position-{identity}",
                        trader_id,
                        input_id,
                        position_quantity,
                        price,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    UPDATE paper_accounts
                    SET cash=?,equity=?,position_quantity=?,
                        average_entry_price=?,updated_at=?
                    WHERE trader_id=?
                    """,
                    (
                        cash_value,
                        equity_value,
                        position_quantity,
                        price,
                        timestamp,
                        trader_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO paper_processing_checkpoints(
                        checkpoint_id,trader_id,input_id,input_sequence,
                        lifecycle_version,created_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        f"paper-checkpoint-{identity}",
                        trader_id,
                        input_id,
                        int(input_row["sequence"]),
                        int(trader["lifecycle_version"]),
                        timestamp,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def account_projection(self, trader_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_accounts WHERE trader_id=?",
                (trader_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"paper account not found for trader: {trader_id}")
        return dict(row)

    def runtime_projection(self, trader_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    t.*, a.cash, a.equity, a.realized_pnl, a.unrealized_pnl,
                    a.realized_r, a.unrealized_r, a.position_quantity,
                    a.average_entry_price, a.stop_price, a.target_price,
                    a.entry_fill_id, a.risk_amount
                FROM paper_traders AS t
                JOIN paper_accounts AS a ON a.trader_id=t.trader_id
                WHERE t.trader_id=?
                """,
                (trader_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"paper runtime not found: {trader_id}")
        return dict(row)

    def table_count(self, table: str) -> int:
        if table not in _EXPECTED_TABLES:
            raise ValueError("table is not part of the exact v4 schema")
        with self._connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _trader_from_row(self, row: sqlite3.Row) -> PaperTraderV2:
        return PaperTraderV2.model_validate(
            {
                "schema": "paper_trader.v2",
                "trader_id": row["trader_id"],
                "request_id": row["request_id"],
                "selection": json.loads(row["selection_json"]),
                "selection_fingerprint": row["selection_fingerprint"],
                "strategy_timeframe_profile": json.loads(
                    row["strategy_profile_json"]
                ),
                "lifecycle": row["lifecycle"],
                "lifecycle_version": row["lifecycle_version"],
                "lifecycle_reason": row["lifecycle_reason"],
                "account_id": row["account_id"],
                "ledger_origin_id": row["ledger_origin_id"],
                "safety": {
                    "max_drawdown_r": int(row["max_drawdown_r"]),
                    "max_losing_streak": row["max_losing_streak"],
                    "blind_minutes": row["blind_minutes"],
                },
                "created_at": row["created_at"],
            }
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _require_integrity(self) -> None:
        report = self.integrity_check()
        if (
            report.user_version != STORE_VERSION
            or report.quick_check != "ok"
            or not report.schema_matches
        ):
            raise PaperStoreIntegrityError(
                "paper runtime store failed exact v4 schema/integrity guard"
            )


def _validate_baseline_members(
    body: PaperTraderCreateRequestV2,
    members: tuple[BaselineMember, ...],
) -> None:
    run_id = body.selection.baseline_run_id
    expected = (
        "baseline/result.json",
        f"baseline/trades/{run_id}.json",
        f"baseline/equity/{run_id}.json",
        f"baseline/events/{run_id}.json",
    )
    actual = tuple(member.path for member in members)
    if actual != expected:
        raise PaperStoreIntegrityError(
            "baseline source members do not match exact locked four-member profile"
        )
    for member in members:
        if (
            member.byte_length != len(member.content)
            or member.sha256 != sha256(member.content).hexdigest()
        ):
            raise PaperStoreIntegrityError("baseline source member bytes drifted")
    if members[0].sha256 != body.selection.baseline_result_sha256:
        raise PaperStoreIntegrityError(
            "baseline result member SHA does not match selection"
        )


def _derived_id(prefix: str, request_id: str) -> str:
    digest = sha256(f"{prefix}\0{request_id}".encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _append_lifecycle_event(
    connection: sqlite3.Connection,
    *,
    trader_id: str,
    lifecycle: str,
    lifecycle_version: int,
    reason: str,
    created_at: str,
) -> None:
    identity = sha256(
        f"{trader_id}\0{lifecycle_version}\0{lifecycle}".encode()
    ).hexdigest()
    connection.execute(
        """
        INSERT INTO paper_lifecycle_events(
            lifecycle_event_id,trader_id,lifecycle,lifecycle_version,
            reason,created_at
        ) VALUES (?,?,?,?,?,?)
        """,
        (
            f"paper-lifecycle-{identity}",
            trader_id,
            lifecycle,
            lifecycle_version,
            reason,
            created_at,
        ),
    )


def _apply_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA_SQL)
    for table in _APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            trigger_name = f"guard_append_only_{table}_{operation.lower()}"
            connection.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE {operation} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'append-only table: {table}');
                END
                """
            )


def _schema_objects(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str], ...]:
    rows = connection.execute(
        """
        SELECT type,name,sql FROM sqlite_master
        WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
        ORDER BY type,name
        """
    ).fetchall()
    return tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)


def _schema_digest(objects: tuple[tuple[str, str, str], ...]) -> str:
    canonical = "\n".join(
        f"{kind}\0{name}\0{' '.join(sql.split())}" for kind, name, sql in objects
    )
    return sha256(canonical.encode()).hexdigest()


def _expected_schema() -> tuple[tuple[str, str, str], ...]:
    connection = sqlite3.connect(":memory:")
    try:
        _apply_schema(connection)
        return _schema_objects(connection)
    finally:
        connection.close()


_EXPECTED_SCHEMA_OBJECTS = _expected_schema()
_EXPECTED_SCHEMA_DIGEST = _schema_digest(_EXPECTED_SCHEMA_OBJECTS)
_EXPECTED_TABLES = frozenset(
    name for kind, name, _sql in _EXPECTED_SCHEMA_OBJECTS if kind == "table"
)
