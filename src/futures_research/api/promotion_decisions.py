"""Append-only PromotionDecision records sourced from immutable result artifacts."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Literal, cast
from urllib.parse import quote
from uuid import uuid4

from futures_research.api.result_main_validation import validate_scorecard
from futures_research.api.results_catalog import (
    ResultArtifactIntegrityError,
    VerifiedResultSnapshot,
)

PromotionDecisionValue = Literal["use", "return", "abandon"]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS promotion_decisions (
    decision_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    request_payload_sha256 TEXT NOT NULL,
    run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_content_sha256 TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('use', 'return', 'abandon')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    scorecard_snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS promotion_decisions_by_run
ON promotion_decisions (run_id, created_at, decision_id);

CREATE INDEX IF NOT EXISTS promotion_decisions_by_strategy
ON promotion_decisions (strategy_id, strategy_content_sha256, created_at, decision_id);

CREATE TRIGGER IF NOT EXISTS promotion_decisions_are_immutable
BEFORE UPDATE ON promotion_decisions
BEGIN
    SELECT RAISE(ABORT, 'promotion decisions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS promotion_decisions_cannot_be_deleted
BEFORE DELETE ON promotion_decisions
BEGIN
    SELECT RAISE(ABORT, 'promotion decisions are immutable');
END;
"""

_EXPECTED_TRIGGER_SQL = {
    "promotion_decisions_are_immutable": """
        CREATE TRIGGER promotion_decisions_are_immutable
        BEFORE UPDATE ON promotion_decisions
        BEGIN
            SELECT RAISE(ABORT, 'promotion decisions are immutable');
        END
    """,
    "promotion_decisions_cannot_be_deleted": """
        CREATE TRIGGER promotion_decisions_cannot_be_deleted
        BEFORE DELETE ON promotion_decisions
        BEGIN
            SELECT RAISE(ABORT, 'promotion decisions are immutable');
        END
    """,
}

_EXPECTED_TABLE_SQL = """
    CREATE TABLE promotion_decisions (
        decision_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE,
        request_payload_sha256 TEXT NOT NULL,
        run_id TEXT NOT NULL,
        strategy_id TEXT NOT NULL,
        strategy_content_sha256 TEXT NOT NULL,
        result_sha256 TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('use', 'return', 'abandon')),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        scorecard_snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
"""

_EXPECTED_INDEX_SQL = {
    "promotion_decisions_by_run": """
        CREATE INDEX promotion_decisions_by_run
        ON promotion_decisions (run_id, created_at, decision_id)
    """,
    "promotion_decisions_by_strategy": """
        CREATE INDEX promotion_decisions_by_strategy
        ON promotion_decisions
        (strategy_id, strategy_content_sha256, created_at, decision_id)
    """,
}


class PromotionDecisionConflictError(RuntimeError):
    """A request id was reused for a different immutable intent."""


class PromotionDecisionIntegrityError(RuntimeError):
    """The append-only decision store is malformed or missing its guards."""


@dataclass(frozen=True, slots=True)
class PromotionDecisionSource:
    """Server-derived facts captured from one verified result main."""

    run_id: str
    strategy_id: str
    strategy_content_sha256: str
    result_sha256: str
    scorecard_snapshot: object


@dataclass(frozen=True, slots=True)
class PromotionDecisionRecord:
    """One immutable Owner decision."""

    decision_id: str
    request_id: str
    run_id: str
    strategy_id: str
    strategy_content_sha256: str
    result_sha256: str
    decision: PromotionDecisionValue
    reason: str
    scorecard_snapshot: object
    created_at: str

    def to_dict(self) -> dict[str, object]:
        """Return fresh nested data so response mutation cannot alter stored history."""
        return {
            "schema": "promotion_decision.v1",
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "strategy": {
                "strategy_id": self.strategy_id,
                "content_sha256": self.strategy_content_sha256,
            },
            "result_sha256": self.result_sha256,
            "decision": self.decision,
            "reason": self.reason,
            "scorecard_snapshot": deepcopy(self.scorecard_snapshot),
            "created_at": self.created_at,
        }


def source_from_result_snapshot(
    snapshot: VerifiedResultSnapshot,
    *,
    expected_run_id: str,
) -> PromotionDecisionSource:
    """Derive identity and snapshot facts only from the locked result manifest."""
    validated = snapshot.validated_main
    if validated.manifest.run_id != expected_run_id:
        msg = f"validated result does not belong to requested run {expected_run_id}"
        raise ResultArtifactIntegrityError(msg)
    scorecard_snapshot = _json_copy(validated.scorecard)

    return PromotionDecisionSource(
        run_id=expected_run_id,
        strategy_id=validated.strategy_id,
        strategy_content_sha256=validated.strategy_content_sha256,
        result_sha256=sha256(snapshot.main_bytes).hexdigest(),
        scorecard_snapshot=scorecard_snapshot,
    )


def request_payload_sha256(
    *,
    run_id: str,
    request_id: str,
    decision: PromotionDecisionValue,
    reason: str,
) -> str:
    """Bind idempotency to the route run and exact Owner-supplied payload."""
    payload = {
        "schema": "promotion_decision_request.v1",
        "request_id": request_id,
        "run_id": run_id,
        "decision": decision,
        "reason": reason,
    }
    return sha256(_json_text(payload).encode("utf-8")).hexdigest()


class PromotionDecisionStore:
    """SQLite repository with database-enforced UPDATE/DELETE rejection."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        decision_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._path = path
        self._clock = clock or (lambda: datetime.now(UTC))
        self._decision_id_factory = decision_id_factory or (
            lambda: f"promotion-{uuid4().hex}"
        )
        self._schema_lock = Lock()

    @property
    def path(self) -> Path:
        return self._path

    def replay(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
    ) -> PromotionDecisionRecord | None:
        """Return an exact idempotent replay without touching the result artifact."""
        if not self._path.is_file():
            return None
        connection = self._connect_readonly()
        try:
            row = connection.execute(
                "SELECT * FROM promotion_decisions WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            self._assert_same_payload(row, request_payload_sha256)
            return self._record_from_row(row)
        finally:
            connection.close()

    def append(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        decision: PromotionDecisionValue,
        reason: str,
        source: PromotionDecisionSource,
    ) -> PromotionDecisionRecord:
        """Atomically append, or return the winner of an identical concurrent request."""
        decision_id = self._decision_id_factory()
        if not decision_id or decision_id != decision_id.strip():
            raise ValueError("decision_id factory must return non-blank stable text")
        created_at = _timestamp_text(self._clock())
        scorecard_json = _json_text(source.scorecard_snapshot)

        connection = self._connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM promotion_decisions WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is not None:
                self._assert_same_payload(row, request_payload_sha256)
                connection.commit()
                return self._record_from_row(row)
            connection.execute(
                """
                INSERT INTO promotion_decisions (
                    decision_id, request_id, request_payload_sha256, run_id,
                    strategy_id, strategy_content_sha256, result_sha256,
                    decision, reason, scorecard_snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    request_id,
                    request_payload_sha256,
                    source.run_id,
                    source.strategy_id,
                    source.strategy_content_sha256,
                    source.result_sha256,
                    decision,
                    reason,
                    scorecard_json,
                    created_at,
                ),
            )
            connection.commit()
            return PromotionDecisionRecord(
                decision_id=decision_id,
                request_id=request_id,
                run_id=source.run_id,
                strategy_id=source.strategy_id,
                strategy_content_sha256=source.strategy_content_sha256,
                result_sha256=source.result_sha256,
                decision=decision,
                reason=reason,
                scorecard_snapshot=_json_copy(source.scorecard_snapshot),
                created_at=created_at,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list(
        self,
        *,
        run_id: str | None = None,
        strategy_id: str | None = None,
        decision: PromotionDecisionValue | None = None,
    ) -> tuple[PromotionDecisionRecord, ...]:
        """List immutable records in deterministic creation/id order without writing."""
        if not self._path.is_file():
            return ()
        clauses: list[str] = []
        params: list[str] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if decision is not None:
            clauses.append("decision = ?")
            params.append(decision)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect_readonly()
        try:
            rows = connection.execute(
                f"SELECT * FROM promotion_decisions{where} ORDER BY created_at, decision_id",
                tuple(params),
            ).fetchall()
            return tuple(self._record_from_row(row) for row in rows)
        finally:
            connection.close()

    def eligible_strategies(self) -> tuple[dict[str, object], ...]:
        """Group every exact identity that has ever received a `use` decision.

        Historical intent only — not a paper-trading hard gate (see docs/10).
        """
        records = self.list(decision="use")
        grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
        for record in records:
            key = (record.strategy_id, record.strategy_content_sha256)
            grouped.setdefault(key, []).append(
                {
                    "decision_id": record.decision_id,
                    "run_id": record.run_id,
                }
            )
        return tuple(
            {
                "strategy_id": strategy_id,
                "content_sha256": content_sha256,
                "supporting_decisions": grouped[(strategy_id, content_sha256)],
            }
            for strategy_id, content_sha256 in sorted(grouped)
        )

    def _connect_write(self) -> sqlite3.Connection:
        with self._schema_lock:
            existed = self._path.is_file()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                if existed:
                    self._assert_schema_guards(connection)
                connection.executescript(_SCHEMA_SQL)
                self._assert_schema_guards(connection)
            except Exception:
                connection.close()
                raise
            return connection

    def _connect_readonly(self) -> sqlite3.Connection:
        with self._schema_lock:
            uri_path = quote(self._path.resolve().as_posix(), safe="/:")
            connection = sqlite3.connect(
                f"file:{uri_path}?mode=ro",
                timeout=30.0,
                isolation_level=None,
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            try:
                self._assert_schema_guards(connection)
            except Exception:
                connection.close()
                raise
            return connection

    @staticmethod
    def _assert_schema_guards(connection: sqlite3.Connection) -> None:
        table = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'promotion_decisions'
            """
        ).fetchone()
        schema_rows = connection.execute(
            """
            SELECT type, name, sql FROM sqlite_master
            WHERE type IN ('index', 'trigger') AND tbl_name = 'promotion_decisions'
            """
        ).fetchall()
        triggers = {
            cast(str, row["name"]): _normalized_sql(cast(str, row["sql"]))
            for row in schema_rows
            if row["type"] == "trigger" and row["sql"] is not None
        }
        indexes = {
            cast(str, row["name"]): _normalized_sql(cast(str, row["sql"]))
            for row in schema_rows
            if row["type"] == "index" and row["sql"] is not None
        }
        expected_triggers = {
            name: _normalized_sql(sql) for name, sql in _EXPECTED_TRIGGER_SQL.items()
        }
        expected_indexes = {
            name: _normalized_sql(sql) for name, sql in _EXPECTED_INDEX_SQL.items()
        }
        index_rows = connection.execute(
            "SELECT name, [unique], origin FROM pragma_index_list(?)",
            ("promotion_decisions",),
        ).fetchall()
        unique_request_id = any(
            row["unique"] == 1
            and row["origin"] == "u"
            and tuple(
                cast(str, field["name"])
                for field in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (row["name"],),
                ).fetchall()
            )
            == ("request_id",)
            for row in index_rows
        )
        table_sql = (
            _normalized_sql(cast(str, table["sql"]))
            if table is not None and table["sql"] is not None
            else None
        )
        if (
            table_sql != _normalized_sql(_EXPECTED_TABLE_SQL)
            or any(
                triggers.get(name) != sql for name, sql in expected_triggers.items()
            )
            or any(indexes.get(name) != sql for name, sql in expected_indexes.items())
            or not unique_request_id
        ):
            raise PromotionDecisionIntegrityError(
                "promotion decision store has an invalid table, index, unique "
                "request-id constraint, or append-only guard"
            )

    @staticmethod
    def _assert_same_payload(row: sqlite3.Row, expected_sha256: str) -> None:
        if row["request_payload_sha256"] != expected_sha256:
            request_id = cast(str, row["request_id"])
            raise PromotionDecisionConflictError(
                f"request_id {request_id} is already bound to a different payload"
            )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> PromotionDecisionRecord:
        try:
            snapshot_raw = json.loads(cast(str, row["scorecard_snapshot_json"]))
            snapshot = validate_scorecard(
                snapshot_raw,
                context="promotion decision scorecard snapshot",
            )
            decision = cast(PromotionDecisionValue, row["decision"])
            if decision not in {"use", "return", "abandon"}:
                raise ValueError("invalid decision enum")
            strategy_hash = cast(str, row["strategy_content_sha256"])
            result_hash = cast(str, row["result_sha256"])
            reason = cast(str, row["reason"])
            created_at = cast(str, row["created_at"])
            if (
                not re.fullmatch(r"[0-9a-f]{64}", strategy_hash)
                or not re.fullmatch(r"[0-9a-f]{64}", result_hash)
                or not reason.strip()
            ):
                raise ValueError("invalid immutable record fields")
            parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
                raise ValueError("created_at must include a timezone")
            return PromotionDecisionRecord(
                decision_id=cast(str, row["decision_id"]),
                request_id=cast(str, row["request_id"]),
                run_id=cast(str, row["run_id"]),
                strategy_id=cast(str, row["strategy_id"]),
                strategy_content_sha256=strategy_hash,
                result_sha256=result_hash,
                decision=decision,
                reason=reason,
                scorecard_snapshot=snapshot,
                created_at=created_at,
            )
        except (
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise PromotionDecisionIntegrityError(
                "promotion decision store contains an invalid immutable record"
            ) from exc


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_copy(value: object) -> object:
    return json.loads(_json_text(value))


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("promotion decision timestamp must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _normalized_sql(value: str) -> str:
    return " ".join(value.split()).lower()
