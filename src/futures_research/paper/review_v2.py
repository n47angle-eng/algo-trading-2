"""Immutable paper-review.v2 package assembly from v4 runtime evidence."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from futures_research.paper.models import canonical_utc
from futures_research.paper.store import PaperRuntimeStore

# Ordered member paths — fail closed if any required member cannot be built.
REVIEW_V2_MEMBER_ORDER: tuple[str, ...] = (
    "paper-review.json",
    "market/provenance.json",
    "runtime/account.json",
    "runtime/positions.json",
    "runtime/decisions.jsonl",
    "runtime/fills.jsonl",
    "runtime/trades.jsonl",
    "runtime/equity.jsonl",
    "runtime/events.jsonl",
    "baseline/result.json",
    "INSTRUCTIONS.md",
)


class PaperReviewV2Error(RuntimeError):
    """Fail-closed packaging or missing member error."""


class PaperReviewV2MissingMemberError(PaperReviewV2Error):
    """A required package member is missing or empty when it must exist."""


@dataclass(frozen=True, slots=True)
class PaperReviewV2Package:
    snapshot_id: str
    request_id: str
    trader_id: str
    cutoff_at: str
    zip_bytes: bytes
    member_sha256: dict[str, str]
    package_sha256: str


def build_paper_review_v2(
    store: PaperRuntimeStore,
    *,
    trader_id: str,
    request_id: str,
    cutoff_at: datetime | None = None,
) -> PaperReviewV2Package:
    """Freeze one runtime snapshot into an immutable paper-review.v2 zip.

    Fail closed when trader is unknown, baseline members are incomplete, or
    required identity fields cannot be proven from store projections.
    """
    try:
        projection = store.runtime_projection(trader_id)
    except LookupError as exc:
        raise PaperReviewV2Error(f"trader not found: {trader_id}") from exc

    cutoff = cutoff_at or datetime.now(UTC)
    cutoff_text = canonical_utc(cutoff)
    snapshot_id = (
        "paper-review-v2-"
        + sha256(f"{trader_id}:{request_id}:{cutoff_text}".encode()).hexdigest()[:24]
    )

    selection = json.loads(projection["selection_json"])
    profile = json.loads(projection["strategy_profile_json"])
    main = {
        "schema": "paper-review.v2",
        "snapshot_id": snapshot_id,
        "request_id": request_id,
        "trader_id": trader_id,
        "cutoff_at": cutoff_text,
        "selection": selection,
        "selection_fingerprint": projection["selection_fingerprint"],
        "strategy_timeframe_profile": profile,
        "lifecycle": {
            "state": projection["lifecycle"],
            "version": int(projection["lifecycle_version"]),
            "reason": projection["lifecycle_reason"],
        },
        "safety": {
            "max_drawdown_r": int(projection["max_drawdown_r"]),
            "max_losing_streak": int(projection["max_losing_streak"]),
            "blind_minutes": int(projection["blind_minutes"]),
            "drawdown_r": float(projection["drawdown_r"]),
            "losing_streak": int(projection["losing_streak"]),
            "equity_high_water_r": float(projection["equity_high_water_r"]),
        },
        "account": {
            "cash": float(projection["cash"]),
            "equity": float(projection["equity"]),
            "realized_pnl": float(projection["realized_pnl"]),
            "unrealized_pnl": float(projection["unrealized_pnl"]),
            "realized_r": float(projection["realized_r"]),
            "unrealized_r": float(projection["unrealized_r"]),
        },
        "counts": {
            "decision_count": int(projection["decision_count"]),
            "trade_count": int(projection["trade_count"]),
            "pending_intent_count": int(projection["pending_intent_count"]),
        },
        "open_position": {
            "quantity": int(projection["position_quantity"]),
            "average_entry_price": projection["average_entry_price"],
            "stop_price": projection["stop_price"],
            "target_price": projection["target_price"],
        },
        "member_order": list(REVIEW_V2_MEMBER_ORDER),
    }

    with store._connect() as connection:  # noqa: SLF001 — same-package authority read
        baseline_rows = connection.execute(
            """
            SELECT member_path, content, byte_length, sha256
            FROM paper_baseline_members
            WHERE trader_id=?
            ORDER BY member_path
            """,
            (trader_id,),
        ).fetchall()
        decisions = connection.execute(
            """
            SELECT decision_id, input_id, payload_json, created_at
            FROM paper_decisions
            WHERE trader_id=? ORDER BY sequence
            """,
            (trader_id,),
        ).fetchall()
        fills = connection.execute(
            """
            SELECT fill_id, order_id, input_id, quantity, price,
                   commission, slippage, created_at
            FROM paper_fills
            WHERE trader_id=? ORDER BY sequence
            """,
            (trader_id,),
        ).fetchall()
        trades = connection.execute(
            """
            SELECT trade_id, entry_fill_id, exit_fill_id, side, quantity,
                   gross_pnl, net_pnl, net_r, opened_at, closed_at
            FROM paper_trades
            WHERE trader_id=? ORDER BY sequence
            """,
            (trader_id,),
        ).fetchall()
        equity = connection.execute(
            """
            SELECT equity_point_id, input_id, cash, equity, realized_pnl,
                   unrealized_pnl, realized_r, unrealized_r, created_at
            FROM paper_equity_points
            WHERE trader_id=? ORDER BY sequence
            """,
            (trader_id,),
        ).fetchall()
        events = connection.execute(
            """
            SELECT lifecycle_event_id, lifecycle, lifecycle_version, reason, created_at
            FROM paper_lifecycle_events
            WHERE trader_id=? ORDER BY sequence
            """,
            (trader_id,),
        ).fetchall()
        market_inputs = connection.execute(
            """
            SELECT
                provider_session_id, contract_id, timeframe, mode,
                event_at, source_kind, payload_sha256
            FROM paper_market_inputs
            ORDER BY sequence
            """,
        ).fetchall()

    if not baseline_rows:
        raise PaperReviewV2MissingMemberError(
            "baseline members missing; refuse paper-review.v2 package"
        )

    baseline_result = None
    for row in baseline_rows:
        if row["member_path"] == "baseline/result.json":
            baseline_result = bytes(row["content"])
            break
    if baseline_result is None or not baseline_result:
        raise PaperReviewV2MissingMemberError(
            "baseline/result.json missing; refuse paper-review.v2 package"
        )

    provenance = {
        "schema": "paper_review_market_provenance.v1",
        "trader_id": trader_id,
        "inputs": [
            {
                "provider_session_id": r["provider_session_id"],
                "contract_id": r["contract_id"],
                "timeframe": r["timeframe"],
                "mode": r["mode"],
                "event_at": r["event_at"],
                "source_kind": r["source_kind"],
                "payload_sha256": r["payload_sha256"],
            }
            for r in market_inputs
        ],
    }

    members: dict[str, bytes] = {
        "paper-review.json": _json_bytes(main),
        "market/provenance.json": _json_bytes(provenance),
        "runtime/account.json": _json_bytes(main["account"]),
        "runtime/positions.json": _json_bytes(main["open_position"]),
        "runtime/decisions.jsonl": _rows_jsonl(decisions),
        "runtime/fills.jsonl": _rows_jsonl(fills),
        "runtime/trades.jsonl": _rows_jsonl(trades),
        "runtime/equity.jsonl": _rows_jsonl(equity),
        "runtime/events.jsonl": _rows_jsonl(events),
        "baseline/result.json": baseline_result,
        "INSTRUCTIONS.md": _instructions_bytes(main),
    }

    for path in REVIEW_V2_MEMBER_ORDER:
        if path not in members:
            raise PaperReviewV2MissingMemberError(f"missing member: {path}")
        if path.endswith(".json") and not members[path].strip():
            raise PaperReviewV2MissingMemberError(f"empty member: {path}")
        if path == "INSTRUCTIONS.md" and not members[path].strip():
            raise PaperReviewV2MissingMemberError(f"empty member: {path}")

    buffer = io.BytesIO()
    member_sha: dict[str, str] = {}
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in REVIEW_V2_MEMBER_ORDER:
            payload = members[path]
            member_sha[path] = sha256(payload).hexdigest()
            zf.writestr(path, payload)

    zip_bytes = buffer.getvalue()
    return PaperReviewV2Package(
        snapshot_id=snapshot_id,
        request_id=request_id,
        trader_id=trader_id,
        cutoff_at=cutoff_text,
        zip_bytes=zip_bytes,
        member_sha256=member_sha,
        package_sha256=sha256(zip_bytes).hexdigest(),
    )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _rows_jsonl(rows: list[Any]) -> bytes:
    lines: list[str] = []
    for row in rows:
        payload = {key: row[key] for key in row.keys()}
        # Nested payload_json already is JSON text — keep as parsed object when present.
        if "payload_json" in payload and isinstance(payload["payload_json"], str):
            payload["payload"] = json.loads(payload.pop("payload_json"))
        lines.append(
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _instructions_bytes(main: dict[str, Any]) -> bytes:
    body = (
        "# paper-review.v2\n\n"
        f"- schema：paper-review.v2\n"
        f"- snapshot_id：{main['snapshot_id']}\n"
        f"- trader_id：{main['trader_id']}\n"
        f"- strategy：{main['selection']['strategy_id']}\n"
        f"- baseline_run_id：{main['selection']['baseline_run_id']}\n"
        f"- cutoff_at：{main['cutoff_at']}\n\n"
        "呢個係 app 自家模擬盤不可變快照；唔代表 IB paper account 成交。\n"
        "請用鎖定 strategy／baseline 身份衍生新 strategy.v1 後重新走完整流程。\n"
    )
    return body.encode("utf-8")
