"""Immutable P6 Stage B ledger-origin persistence and evidence guards."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cmp_to_key
from hashlib import sha256
from math import isfinite
from typing import Any, cast

from futures_research.api.result_main_validation import (
    ResultMainValidationError,
    validate_result_main,
)
from futures_research.api.results_catalog import ResultExportArtifacts

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LEDGER_ORIGIN_ID_PATTERN = re.compile(r"^paper-ledger-[0-9a-f]{32}$")
_RUN_ID_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{6})?Z$"
)
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_CLOSEST_ALGORITHM = "p5_structural_closest.v1"
_OWNER_VIEW = (
    "模擬引擎尚未啟用；目前只證明初始帳戶、鎖定baseline及建立證據完整，"
    "未能判斷真實模擬盤偏離。"
)
_LAYER_DEPTH = {
    "daily": 0,
    "mid": 1,
    "entry": 2,
    "execution": 3,
}


class PaperLedgerEvidenceError(ValueError):
    """Captured baseline evidence cannot support an immutable ledger origin."""


class PaperLedgerPersistenceError(RuntimeError):
    """Persisted ledger rows differ from their immutable evidence boundary."""


@dataclass(frozen=True, slots=True)
class PaperLedgerBaselineMember:
    ordinal: int
    path: str
    byte_count: int
    sha256: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class PaperLedgerBaselineCapture:
    run_id: str
    result_sha256: str
    members: tuple[PaperLedgerBaselineMember, ...]
    rejection_count: int
    closest_algorithm: str
    closest_rejection_refs: tuple[str, ...]
    closest_rejection_refs_json: str
    interpretation_json: str


@dataclass(frozen=True, slots=True)
class PaperLedgerOriginSeed:
    ledger_origin_id: str
    trader_id: str
    account_id: str
    origin_at: str
    strategy_id: str
    strategy_name: str
    strategy_content_sha256: str
    contract_id: str
    contract_exchange: str
    contract_timezone: str
    baseline_run_id: str
    baseline_result_sha256: str
    baseline_range_start: str
    baseline_range_end: str
    currency: str
    initial_capital: float
    baseline_capture: PaperLedgerBaselineCapture


@dataclass(frozen=True, slots=True)
class PaperLedgerExpectedTrader:
    trader_id: str
    account_id: str
    created_at: str
    strategy_id: str
    strategy_content_sha256: str
    contract_id: str
    baseline_run_id: str
    baseline_result_sha256: str
    baseline_range_start: str
    baseline_range_end: str
    currency: str
    initial_capital: float


@dataclass(frozen=True, slots=True)
class _ClosestRecord:
    evidence_id: str
    depth: int
    reached_count: int
    blocker_count: int
    evaluation_sequence: int
    timestamp: datetime


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _is_safe_run_id(value: object) -> bool:
    return isinstance(value, str) and _RUN_ID_SAFE.fullmatch(value) is not None


def _required_export_paths(run_id: str) -> tuple[str, ...]:
    return (
        "result.json",
        f"trades/{run_id}.json",
        f"equity/{run_id}.json",
        f"events/{run_id}.json",
    )


def _ledger_paths(run_id: str) -> tuple[str, ...]:
    return tuple(
        "baseline/result.json" if path == "result.json" else f"baseline/{path}"
        for path in _required_export_paths(run_id)
    )


def capture_paper_ledger_baseline(
    *,
    artifacts: ResultExportArtifacts,
    expected_run_id: str,
    expected_result_sha256: str,
) -> PaperLedgerBaselineCapture:
    """Capture four verified raw members and the frozen closest-three projection."""
    if (
        not _is_safe_run_id(expected_run_id)
        or artifacts.run_id != expected_run_id
    ):
        raise PaperLedgerEvidenceError(
            "baseline export identity differs from the selected immutable run"
        )
    if _SHA256_PATTERN.fullmatch(expected_result_sha256) is None:
        raise PaperLedgerEvidenceError(
            "baseline result SHA must be canonical lowercase SHA-256"
        )
    required_paths = _required_export_paths(expected_run_id)
    if len(artifacts.members) != 4:
        raise PaperLedgerEvidenceError(
            "baseline export must contain exactly four ordered raw members"
        )
    if tuple(path for path, _payload in artifacts.members) != required_paths:
        raise PaperLedgerEvidenceError(
            "baseline export member paths or order differ from the frozen contract"
        )

    captured: list[PaperLedgerBaselineMember] = []
    for ordinal, ((source_path, payload), ledger_path) in enumerate(
        zip(artifacts.members, _ledger_paths(expected_run_id), strict=True),
        start=1,
    ):
        if (
            not isinstance(source_path, str)
            or not isinstance(payload, bytes)
            or not payload
        ):
            raise PaperLedgerEvidenceError(
                "baseline export members require non-empty immutable bytes"
            )
        member_sha256 = sha256(payload).hexdigest()
        captured.append(
            PaperLedgerBaselineMember(
                ordinal=ordinal,
                path=ledger_path,
                byte_count=len(payload),
                sha256=member_sha256,
                payload=payload,
            )
        )
    if captured[0].sha256 != expected_result_sha256:
        raise PaperLedgerEvidenceError(
            "captured baseline result bytes differ from the locked result SHA"
        )

    events_payload = captured[3].payload
    try:
        events_document = json.loads(
            events_payload.decode("utf-8"),
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperLedgerEvidenceError(
            "captured baseline events member is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(events_document, dict):
        raise PaperLedgerEvidenceError(
            "captured baseline events member must be a JSON object"
        )
    if (
        events_document.get("schema") != "events.v1"
        or events_document.get("run_id") != expected_run_id
        or events_document.get("evidence_complete") is not True
    ):
        raise PaperLedgerEvidenceError(
            "captured baseline events identity or completeness marker is invalid"
        )
    rejections = events_document.get("rejection_evidence")
    summary = events_document.get("evidence_summary")
    if not isinstance(rejections, list) or not isinstance(summary, dict):
        raise PaperLedgerEvidenceError(
            "captured baseline events requires complete rejection evidence"
        )
    rejection_count = summary.get("rejection_count")
    if (
        type(rejection_count) is not int
        or rejection_count < 0
        or rejection_count > _MAX_SAFE_INTEGER
        or rejection_count != len(rejections)
    ):
        raise PaperLedgerEvidenceError(
            "captured baseline rejection count differs from complete evidence"
        )
    closest_rejection_refs = select_structural_closest_rejection_refs(rejections)
    events_path = captured[3].path
    interpretation = {
        "evaluation_status": "not_evaluable",
        "reason": "engine_not_enabled",
        "owner_view": _OWNER_VIEW,
        "categories": ["unknown"],
        "supporting_evidence_refs": [
            {"path": events_path, "evidence_id": evidence_id}
            for evidence_id in closest_rejection_refs
        ],
    }
    return PaperLedgerBaselineCapture(
        run_id=expected_run_id,
        result_sha256=expected_result_sha256,
        members=tuple(captured),
        rejection_count=rejection_count,
        closest_algorithm=_CLOSEST_ALGORITHM,
        closest_rejection_refs=closest_rejection_refs,
        closest_rejection_refs_json=_canonical_json(list(closest_rejection_refs)),
        interpretation_json=_canonical_json(interpretation),
    )


def select_structural_closest_rejection_refs(
    rejections: Sequence[object],
) -> tuple[str, ...]:
    """Apply the frozen P5 structural comparator and return at most three refs."""
    parsed: list[_ClosestRecord] = []
    seen_ids: set[str] = set()
    for ordinal, value in enumerate(rejections, start=1):
        if not isinstance(value, dict):
            raise PaperLedgerEvidenceError(
                f"baseline rejection {ordinal} must be an object"
            )
        evidence_id = value.get("evidence_id")
        if (
            not isinstance(evidence_id, str)
            or not evidence_id
            or evidence_id != evidence_id.strip()
            or evidence_id in seen_ids
        ):
            raise PaperLedgerEvidenceError(
                "baseline rejection evidence IDs must be unique untrimmed text"
            )
        seen_ids.add(evidence_id)
        reached_layers = value.get("reached_layers")
        if (
            not isinstance(reached_layers, list)
            or not reached_layers
            or any(
                not isinstance(layer, str)
                or layer not in _LAYER_DEPTH
                for layer in reached_layers
            )
            or len(set(cast(list[str], reached_layers))) != len(reached_layers)
        ):
            raise PaperLedgerEvidenceError(
                "baseline rejection reached layer is missing, duplicate, or unknown"
            )
        blocking_ids = value.get("blocking_condition_ids")
        if (
            not isinstance(blocking_ids, list)
            or any(
                not isinstance(blocker, str) or not blocker
                for blocker in blocking_ids
            )
        ):
            raise PaperLedgerEvidenceError(
                "baseline rejection blocking conditions are malformed"
            )
        evaluation_sequence = value.get("evaluation_sequence")
        if (
            type(evaluation_sequence) is not int
            or evaluation_sequence <= 0
            or evaluation_sequence > _MAX_SAFE_INTEGER
        ):
            raise PaperLedgerEvidenceError(
                "baseline rejection evaluation sequence is invalid"
            )
        timestamp = _parse_utc_timestamp(value.get("timestamp"))
        typed_layers = cast(list[str], reached_layers)
        parsed.append(
            _ClosestRecord(
                evidence_id=evidence_id,
                depth=max(_LAYER_DEPTH[layer] for layer in typed_layers),
                reached_count=len(set(typed_layers)),
                blocker_count=len(blocking_ids),
                evaluation_sequence=evaluation_sequence,
                timestamp=timestamp,
            )
        )
    parsed.sort(key=cmp_to_key(_compare_closest_records))
    return tuple(record.evidence_id for record in parsed[:3])


def _compare_closest_records(left: _ClosestRecord, right: _ClosestRecord) -> int:
    if left.depth != right.depth:
        return -1 if left.depth > right.depth else 1
    if left.reached_count != right.reached_count:
        return -1 if left.reached_count > right.reached_count else 1
    if left.blocker_count != right.blocker_count:
        return -1 if left.blocker_count < right.blocker_count else 1
    if left.evaluation_sequence != right.evaluation_sequence:
        return -1 if left.evaluation_sequence > right.evaluation_sequence else 1
    if left.timestamp != right.timestamp:
        return -1 if left.timestamp > right.timestamp else 1
    if left.evidence_id != right.evidence_id:
        return -1 if left.evidence_id > right.evidence_id else 1
    return 0


def _parse_utc_timestamp(value: object) -> datetime:
    if (
        not isinstance(value, str)
        or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None
    ):
        raise PaperLedgerEvidenceError(
            "baseline rejection timestamp must be canonical UTC Z text"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PaperLedgerEvidenceError(
            "baseline rejection timestamp must be canonical UTC Z text"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise PaperLedgerEvidenceError(
            "baseline rejection timestamp must be canonical UTC Z text"
        )
    timespec = "microseconds" if parsed.microsecond else "seconds"
    canonical = parsed.isoformat(timespec=timespec).replace("+00:00", "Z")
    if canonical != value:
        raise PaperLedgerEvidenceError(
            "baseline rejection timestamp must be canonical UTC Z text"
        )
    return parsed


_TABLE_BODIES = {
    "paper_evidence_blobs": """
        sha256 TEXT PRIMARY KEY,
        byte_count INTEGER NOT NULL CHECK (
            byte_count > 0 AND byte_count <= 9007199254740991
        ),
        payload BLOB NOT NULL CHECK (
            typeof(payload) = 'blob' AND length(payload) = byte_count
        ),
        CHECK (
            length(sha256) = 64
            AND sha256 NOT GLOB '*[^0-9a-f]*'
        )
    """,
    "paper_ledger_origins": """
        ledger_origin_id TEXT PRIMARY KEY,
        trader_id TEXT NOT NULL UNIQUE,
        account_id TEXT NOT NULL UNIQUE,
        origin_at TEXT NOT NULL,
        strategy_id TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        strategy_content_sha256 TEXT NOT NULL,
        contract_id TEXT NOT NULL,
        contract_exchange TEXT NOT NULL,
        contract_timezone TEXT NOT NULL,
        baseline_run_id TEXT NOT NULL,
        baseline_result_sha256 TEXT NOT NULL,
        baseline_range_start TEXT NOT NULL,
        baseline_range_end TEXT NOT NULL,
        currency TEXT NOT NULL,
        initial_capital REAL NOT NULL CHECK (
            initial_capital > 0 AND initial_capital < 1.7976931348623157e308
        ),
        cash REAL NOT NULL CHECK (cash = initial_capital),
        equity REAL NOT NULL CHECK (equity = initial_capital),
        realized_pnl REAL NOT NULL CHECK (realized_pnl = 0),
        unrealized_pnl REAL NOT NULL CHECK (unrealized_pnl = 0),
        independent_account INTEGER NOT NULL CHECK (independent_account = 1),
        lifecycle_status TEXT NOT NULL CHECK (lifecycle_status = 'provisioned'),
        engine_status TEXT NOT NULL CHECK (engine_status = 'not_enabled'),
        safety_state TEXT NOT NULL CHECK (safety_state = 'not_running'),
        drawdown_r REAL NOT NULL CHECK (drawdown_r = 0),
        loss_streak INTEGER NOT NULL CHECK (loss_streak = 0),
        trades_hwm INTEGER NOT NULL CHECK (trades_hwm = 0),
        equity_hwm INTEGER NOT NULL CHECK (equity_hwm = 1),
        events_hwm INTEGER NOT NULL CHECK (events_hwm = 4),
        expected_decisions_hwm INTEGER NOT NULL CHECK (
            expected_decisions_hwm = 0
        ),
        positions_hwm INTEGER NOT NULL CHECK (positions_hwm = 0),
        orders_hwm INTEGER NOT NULL CHECK (orders_hwm = 0),
        baseline_rejection_count INTEGER NOT NULL CHECK (
            baseline_rejection_count >= 0
            AND baseline_rejection_count <= 9007199254740991
        ),
        closest_algorithm TEXT NOT NULL CHECK (
            closest_algorithm = 'p5_structural_closest.v1'
        ),
        closest_rejection_refs_json TEXT NOT NULL,
        interpretation_json TEXT NOT NULL,
        created_at TEXT NOT NULL CHECK (created_at = origin_at),
        CHECK (
            length(ledger_origin_id) = 45
            AND substr(ledger_origin_id, 1, 13) = 'paper-ledger-'
            AND substr(ledger_origin_id, 14) NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            length(strategy_content_sha256) = 64
            AND strategy_content_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            length(baseline_result_sha256) = 64
            AND baseline_result_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        FOREIGN KEY (trader_id) REFERENCES paper_traders (trader_id),
        FOREIGN KEY (account_id) REFERENCES paper_accounts (account_id)
    """,
    "paper_ledger_equity_points": """
        ledger_origin_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal = 1),
        occurred_at TEXT NOT NULL,
        currency TEXT NOT NULL,
        cash REAL NOT NULL CHECK (
            cash > 0 AND cash < 1.7976931348623157e308
        ),
        equity REAL NOT NULL CHECK (equity = cash),
        realized_pnl REAL NOT NULL CHECK (realized_pnl = 0),
        unrealized_pnl REAL NOT NULL CHECK (unrealized_pnl = 0),
        PRIMARY KEY (ledger_origin_id, ordinal),
        FOREIGN KEY (ledger_origin_id)
            REFERENCES paper_ledger_origins (ledger_origin_id)
    """,
    "paper_ledger_baseline_members": """
        ledger_origin_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 4),
        zip_path TEXT NOT NULL,
        byte_count INTEGER NOT NULL CHECK (
            byte_count > 0 AND byte_count <= 9007199254740991
        ),
        sha256 TEXT NOT NULL,
        PRIMARY KEY (ledger_origin_id, ordinal),
        UNIQUE (ledger_origin_id, zip_path),
        FOREIGN KEY (ledger_origin_id)
            REFERENCES paper_ledger_origins (ledger_origin_id),
        FOREIGN KEY (sha256) REFERENCES paper_evidence_blobs (sha256),
        CHECK (
            length(sha256) = 64
            AND sha256 NOT GLOB '*[^0-9a-f]*'
        )
    """,
    "paper_review_requests": """
        request_id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL UNIQUE,
        request_fingerprint TEXT NOT NULL,
        trader_id TEXT NOT NULL,
        ledger_origin_id TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        builder_instance_id TEXT NOT NULL,
        created_at TEXT NOT NULL CHECK (created_at = captured_at),
        FOREIGN KEY (trader_id) REFERENCES paper_traders (trader_id),
        FOREIGN KEY (ledger_origin_id)
            REFERENCES paper_ledger_origins (ledger_origin_id),
        CHECK (
            length(request_fingerprint) = 64
            AND request_fingerprint NOT GLOB '*[^0-9a-f]*'
        )
    """,
    "paper_review_status_events": """
        request_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 2),
        status TEXT NOT NULL CHECK (status IN ('preparing', 'ready', 'failed')),
        occurred_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (request_id, ordinal),
        FOREIGN KEY (request_id) REFERENCES paper_review_requests (request_id),
        CHECK (
            (ordinal = 1 AND status = 'preparing')
            OR (ordinal = 2 AND status IN ('ready', 'failed'))
        )
    """,
    "paper_review_ready_artifacts": """
        request_id TEXT PRIMARY KEY,
        artifact_relpath TEXT NOT NULL,
        display_filename TEXT NOT NULL,
        artifact_bytes INTEGER NOT NULL CHECK (
            artifact_bytes > 0 AND artifact_bytes <= 9007199254740991
        ),
        artifact_sha256 TEXT NOT NULL,
        member_manifest_json TEXT NOT NULL,
        terminal_opener_text TEXT NOT NULL,
        terminal_opener_bytes INTEGER NOT NULL CHECK (
            terminal_opener_bytes > 0
            AND terminal_opener_bytes <= 9007199254740991
        ),
        terminal_opener_sha256 TEXT NOT NULL,
        ready_at TEXT NOT NULL,
        FOREIGN KEY (request_id) REFERENCES paper_review_requests (request_id),
        CHECK (
            length(artifact_sha256) = 64
            AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            length(terminal_opener_sha256) = 64
            AND terminal_opener_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    """,
    "paper_review_failures": """
        request_id TEXT PRIMARY KEY,
        http_status INTEGER NOT NULL CHECK (http_status BETWEEN 100 AND 599),
        error_json TEXT NOT NULL,
        error_sha256 TEXT NOT NULL,
        failed_at TEXT NOT NULL,
        FOREIGN KEY (request_id) REFERENCES paper_review_requests (request_id),
        CHECK (
            length(error_sha256) = 64
            AND error_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    """,
}

PAPER_LEDGER_EXPECTED_TABLE_SQL = {
    name: f"CREATE TABLE {name} ({body})"
    for name, body in _TABLE_BODIES.items()
}

_TRIGGER_SPECS = (
    (
        "paper_evidence_blobs",
        "paper_evidence_blobs_are_immutable",
        "paper evidence blobs are immutable",
    ),
    (
        "paper_ledger_origins",
        "paper_ledger_origins_are_immutable",
        "paper ledger origins are immutable",
    ),
    (
        "paper_ledger_equity_points",
        "paper_ledger_equity_points_are_immutable",
        "paper ledger equity points are immutable",
    ),
    (
        "paper_ledger_baseline_members",
        "paper_ledger_baseline_members_are_immutable",
        "paper ledger baseline members are immutable",
    ),
    (
        "paper_review_requests",
        "paper_review_requests_are_immutable",
        "paper review requests are immutable",
    ),
    (
        "paper_review_status_events",
        "paper_review_status_events_are_append_only",
        "paper review status events are append-only",
    ),
    (
        "paper_review_ready_artifacts",
        "paper_review_ready_artifacts_are_immutable",
        "paper review ready artifacts are immutable",
    ),
    (
        "paper_review_failures",
        "paper_review_failures_are_immutable",
        "paper review failures are immutable",
    ),
)

PAPER_LEDGER_EXPECTED_TRIGGER_SQL: dict[str, str] = {}
for _table, _update_name, _message in _TRIGGER_SPECS:
    PAPER_LEDGER_EXPECTED_TRIGGER_SQL[_update_name] = f"""
        CREATE TRIGGER {_update_name}
        BEFORE UPDATE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_message}');
        END
    """
    _delete_name = f"{_table}_cannot_be_deleted"
    PAPER_LEDGER_EXPECTED_TRIGGER_SQL[_delete_name] = f"""
        CREATE TRIGGER {_delete_name}
        BEFORE DELETE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_message}');
        END
    """

PAPER_LEDGER_SCHEMA_SQL = "\n\n".join(
    [
        *(
            sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1) + ";"
            for sql in PAPER_LEDGER_EXPECTED_TABLE_SQL.values()
        ),
        *(
            sql.replace("CREATE TRIGGER", "CREATE TRIGGER IF NOT EXISTS", 1) + ";"
            for sql in PAPER_LEDGER_EXPECTED_TRIGGER_SQL.values()
        ),
    ]
)


def insert_paper_ledger_origin(
    connection: sqlite3.Connection,
    *,
    seed: PaperLedgerOriginSeed,
    step_hook: Callable[[str], None] | None = None,
) -> None:
    """Insert all immutable ledger rows inside the caller-owned transaction."""
    _validate_origin_seed(seed)
    for member in seed.baseline_capture.members:
        _insert_or_verify_blob(connection, member)
    _call_step_hook(step_hook, "paper_evidence_blobs")

    connection.execute(
        """
        INSERT INTO paper_ledger_origins (
            ledger_origin_id, trader_id, account_id, origin_at,
            strategy_id, strategy_name, strategy_content_sha256,
            contract_id, contract_exchange, contract_timezone,
            baseline_run_id, baseline_result_sha256,
            baseline_range_start, baseline_range_end,
            currency, initial_capital, cash, equity,
            realized_pnl, unrealized_pnl, independent_account,
            lifecycle_status, engine_status, safety_state,
            drawdown_r, loss_streak, trades_hwm, equity_hwm,
            events_hwm, expected_decisions_hwm, positions_hwm, orders_hwm,
            baseline_rejection_count, closest_algorithm,
            closest_rejection_refs_json, interpretation_json, created_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            seed.ledger_origin_id,
            seed.trader_id,
            seed.account_id,
            seed.origin_at,
            seed.strategy_id,
            seed.strategy_name,
            seed.strategy_content_sha256,
            seed.contract_id,
            seed.contract_exchange,
            seed.contract_timezone,
            seed.baseline_run_id,
            seed.baseline_result_sha256,
            seed.baseline_range_start,
            seed.baseline_range_end,
            seed.currency,
            seed.initial_capital,
            seed.initial_capital,
            seed.initial_capital,
            0.0,
            0.0,
            1,
            "provisioned",
            "not_enabled",
            "not_running",
            0.0,
            0,
            0,
            1,
            4,
            0,
            0,
            0,
            seed.baseline_capture.rejection_count,
            seed.baseline_capture.closest_algorithm,
            seed.baseline_capture.closest_rejection_refs_json,
            seed.baseline_capture.interpretation_json,
            seed.origin_at,
        ),
    )
    _call_step_hook(step_hook, "paper_ledger_origin")

    connection.execute(
        """
        INSERT INTO paper_ledger_equity_points (
            ledger_origin_id, ordinal, occurred_at, currency,
            cash, equity, realized_pnl, unrealized_pnl
        ) VALUES (?, 1, ?, ?, ?, ?, 0, 0)
        """,
        (
            seed.ledger_origin_id,
            seed.origin_at,
            seed.currency,
            seed.initial_capital,
            seed.initial_capital,
        ),
    )
    _call_step_hook(step_hook, "paper_ledger_equity_point")

    for member in seed.baseline_capture.members:
        connection.execute(
            """
            INSERT INTO paper_ledger_baseline_members (
                ledger_origin_id, ordinal, zip_path, byte_count, sha256
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                seed.ledger_origin_id,
                member.ordinal,
                member.path,
                member.byte_count,
                member.sha256,
            ),
        )
    _call_step_hook(step_hook, "paper_ledger_baseline_members")


def _call_step_hook(
    step_hook: Callable[[str], None] | None,
    step: str,
) -> None:
    if step_hook is not None:
        step_hook(step)


def _validate_origin_seed(seed: PaperLedgerOriginSeed) -> None:
    if _LEDGER_ORIGIN_ID_PATTERN.fullmatch(seed.ledger_origin_id) is None:
        raise ValueError("ledger origin factory returned a non-canonical identity")
    if not _is_safe_run_id(seed.baseline_run_id):
        raise PaperLedgerEvidenceError(
            "baseline run ID must be a safe immutable artifact path segment"
        )
    for label, value in (
        ("trader_id", seed.trader_id),
        ("account_id", seed.account_id),
        ("origin_at", seed.origin_at),
        ("strategy_id", seed.strategy_id),
        ("strategy_name", seed.strategy_name),
        ("contract_id", seed.contract_id),
        ("contract_exchange", seed.contract_exchange),
        ("contract_timezone", seed.contract_timezone),
        ("baseline_run_id", seed.baseline_run_id),
        ("baseline_range_start", seed.baseline_range_start),
        ("baseline_range_end", seed.baseline_range_end),
        ("currency", seed.currency),
    ):
        if not value or value != value.strip():
            raise PaperLedgerEvidenceError(f"{label} must be non-blank untrimmed text")
    if (
        _SHA256_PATTERN.fullmatch(seed.strategy_content_sha256) is None
        or _SHA256_PATTERN.fullmatch(seed.baseline_result_sha256) is None
        or seed.baseline_capture.run_id != seed.baseline_run_id
        or seed.baseline_capture.result_sha256 != seed.baseline_result_sha256
    ):
        raise PaperLedgerEvidenceError(
            "ledger origin strategy or baseline identity is inconsistent"
        )
    if (
        isinstance(seed.initial_capital, bool)
        or not isinstance(seed.initial_capital, (int, float))
        or not isfinite(seed.initial_capital)
        or seed.initial_capital <= 0
    ):
        raise PaperLedgerEvidenceError(
            "ledger origin initial capital must be positive and finite"
        )


def _insert_or_verify_blob(
    connection: sqlite3.Connection,
    member: PaperLedgerBaselineMember,
) -> None:
    if (
        member.byte_count != len(member.payload)
        or member.sha256 != sha256(member.payload).hexdigest()
    ):
        raise PaperLedgerPersistenceError(
            "captured evidence blob bytes or SHA differ before persistence"
        )
    existing = connection.execute(
        """
        SELECT byte_count, payload
        FROM paper_evidence_blobs
        WHERE sha256 = ?
        """,
        (member.sha256,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO paper_evidence_blobs (sha256, byte_count, payload)
            VALUES (?, ?, ?)
            """,
            (member.sha256, member.byte_count, member.payload),
        )
        return
    if (
        existing["byte_count"] != member.byte_count
        or bytes(existing["payload"]) != member.payload
    ):
        raise PaperLedgerPersistenceError(
            "existing evidence blob has the same SHA but different bytes or count"
        )


def assert_paper_ledger_for_trader(
    connection: sqlite3.Connection,
    *,
    expected: PaperLedgerExpectedTrader,
) -> None:
    """Fail closed unless one trader has one exact immutable ledger foundation."""
    if not _is_safe_run_id(expected.baseline_run_id):
        raise PaperLedgerPersistenceError(
            "persisted baseline run ID is not a safe artifact path segment"
        )
    origins = connection.execute(
        "SELECT * FROM paper_ledger_origins WHERE trader_id = ?",
        (expected.trader_id,),
    ).fetchall()
    if len(origins) != 1:
        raise PaperLedgerPersistenceError(
            "paper trader requires exactly one immutable ledger origin"
        )
    origin = origins[0]
    expected_origin_values = {
        "trader_id": expected.trader_id,
        "account_id": expected.account_id,
        "origin_at": expected.created_at,
        "strategy_id": expected.strategy_id,
        "strategy_content_sha256": expected.strategy_content_sha256,
        "contract_id": expected.contract_id,
        "baseline_run_id": expected.baseline_run_id,
        "baseline_result_sha256": expected.baseline_result_sha256,
        "baseline_range_start": expected.baseline_range_start,
        "baseline_range_end": expected.baseline_range_end,
        "currency": expected.currency,
        "initial_capital": expected.initial_capital,
        "cash": expected.initial_capital,
        "equity": expected.initial_capital,
        "realized_pnl": 0,
        "unrealized_pnl": 0,
        "independent_account": 1,
        "lifecycle_status": "provisioned",
        "engine_status": "not_enabled",
        "safety_state": "not_running",
        "drawdown_r": 0,
        "loss_streak": 0,
        "trades_hwm": 0,
        "equity_hwm": 1,
        "events_hwm": 4,
        "expected_decisions_hwm": 0,
        "positions_hwm": 0,
        "orders_hwm": 0,
        "closest_algorithm": _CLOSEST_ALGORITHM,
        "created_at": expected.created_at,
    }
    if (
        _LEDGER_ORIGIN_ID_PATTERN.fullmatch(
            cast(str, origin["ledger_origin_id"])
        )
        is None
        or any(origin[key] != value for key, value in expected_origin_values.items())
        or not _valid_untrimmed_text(origin["strategy_name"])
        or not _valid_untrimmed_text(origin["contract_exchange"])
        or not _valid_untrimmed_text(origin["contract_timezone"])
    ):
        raise PaperLedgerPersistenceError(
            "paper ledger origin differs from immutable trader/account truth"
        )

    ledger_origin_id = cast(str, origin["ledger_origin_id"])
    equity_rows = connection.execute(
        """
        SELECT *
        FROM paper_ledger_equity_points
        WHERE ledger_origin_id = ?
        ORDER BY ordinal
        """,
        (ledger_origin_id,),
    ).fetchall()
    if len(equity_rows) != 1:
        raise PaperLedgerPersistenceError(
            "paper ledger origin requires exactly one initial equity point"
        )
    equity = equity_rows[0]
    if (
        equity["ordinal"] != 1
        or equity["occurred_at"] != expected.created_at
        or equity["currency"] != expected.currency
        or equity["cash"] != expected.initial_capital
        or equity["equity"] != expected.initial_capital
        or equity["realized_pnl"] != 0
        or equity["unrealized_pnl"] != 0
    ):
        raise PaperLedgerPersistenceError(
            "paper ledger initial equity point differs from persisted origin truth"
        )

    member_rows = connection.execute(
        """
        SELECT member.ordinal, member.zip_path, member.byte_count, member.sha256,
               blob.byte_count AS blob_byte_count, blob.payload
        FROM paper_ledger_baseline_members AS member
        JOIN paper_evidence_blobs AS blob ON blob.sha256 = member.sha256
        WHERE member.ledger_origin_id = ?
        ORDER BY member.ordinal
        """,
        (ledger_origin_id,),
    ).fetchall()
    if len(member_rows) != 4:
        raise PaperLedgerPersistenceError(
            "paper ledger origin requires exactly four baseline raw members"
        )
    export_members: list[tuple[str, bytes]] = []
    required_ledger_paths = _ledger_paths(expected.baseline_run_id)
    for ordinal, (row, ledger_path, source_path) in enumerate(
        zip(
            member_rows,
            required_ledger_paths,
            _required_export_paths(expected.baseline_run_id),
            strict=True,
        ),
        start=1,
    ):
        payload = bytes(row["payload"])
        actual_sha = sha256(payload).hexdigest()
        if (
            row["ordinal"] != ordinal
            or row["zip_path"] != ledger_path
            or row["byte_count"] != len(payload)
            or row["blob_byte_count"] != len(payload)
            or row["sha256"] != actual_sha
        ):
            raise PaperLedgerPersistenceError(
                "paper ledger baseline member path, bytes, or SHA is invalid"
            )
        export_members.append((source_path, payload))

    capture = capture_paper_ledger_baseline(
        artifacts=ResultExportArtifacts(
            run_id=expected.baseline_run_id,
            members=tuple(export_members),
        ),
        expected_run_id=expected.baseline_run_id,
        expected_result_sha256=expected.baseline_result_sha256,
    )
    if (
        origin["baseline_rejection_count"] != capture.rejection_count
        or origin["closest_rejection_refs_json"]
        != capture.closest_rejection_refs_json
        or origin["interpretation_json"] != capture.interpretation_json
    ):
        raise PaperLedgerPersistenceError(
            "paper ledger closest evidence or interpretation is not canonical"
        )

    try:
        main_document = json.loads(
            capture.members[0].payload.decode("utf-8"),
            parse_constant=_reject_nonstandard_json_constant,
        )
        if not isinstance(main_document, dict):
            raise ValueError("result main must be an object")
        validated = validate_result_main(
            cast(dict[str, Any], main_document),
            expected_run_id=expected.baseline_run_id,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ResultMainValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise PaperLedgerPersistenceError(
            "paper ledger captured result main no longer validates"
        ) from exc
    binding = validated.manifest.strategy_binding
    if (
        binding is None
        or binding.strategy_name != origin["strategy_name"]
        or validated.strategy_id != expected.strategy_id
        or validated.strategy_content_sha256 != expected.strategy_content_sha256
        or validated.manifest.contract_id != expected.contract_id
        or validated.manifest.initial_capital != expected.initial_capital
    ):
        raise PaperLedgerPersistenceError(
            "paper ledger strategy, contract, or capital differs from captured result"
        )


def _valid_untrimmed_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and "\x00" not in value
    )
