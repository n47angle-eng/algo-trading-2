"""HTTP-facing helpers for the local paper runtime (v4 store)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from futures_research.api.paper_runtime import (
    LIFECYCLE_STATES,
    PaperRuntimeCapabilities,
    build_runtime_capabilities,
    selection_fingerprint,
)
from futures_research.paper.models import canonical_utc
from futures_research.paper.runtime import PaperTraderRuntime
from futures_research.paper.store import (
    PaperRuntimeStore,
    PaperStoreLifecycleConflictError,
)
from futures_research.paper.timeframes import DEFAULT_TIMEFRAME_CAPABILITIES

PublicMarketMode = Literal["live", "test_delayed"]


class PaperRuntimeServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def runtime_capabilities(*, as_of: datetime | None = None) -> dict[str, Any]:
    caps: PaperRuntimeCapabilities = build_runtime_capabilities(
        DEFAULT_TIMEFRAME_CAPABILITIES,
        as_of=as_of or datetime.now(UTC),
    )
    return caps.model_dump(mode="json", by_alias=True)


def runtime_snapshot(store: PaperRuntimeStore, trader_id: str) -> dict[str, Any]:
    try:
        projection = store.runtime_projection(trader_id)
    except LookupError as exc:
        raise PaperRuntimeServiceError(
            "trader_not_found", f"paper trader not found: {trader_id}"
        ) from exc
    selection = json.loads(projection["selection_json"])
    profile = json.loads(projection["strategy_profile_json"])
    return {
        "schema": "paper_runtime_snapshot.v1",
        "trader_id": trader_id,
        "selection_fingerprint": projection["selection_fingerprint"],
        "lifecycle": projection["lifecycle"],
        "lifecycle_version": int(projection["lifecycle_version"]),
        "lifecycle_reason": projection["lifecycle_reason"],
        "strategy_timeframe_profile": profile,
        "timeframes": selection.get("timeframes"),
        "market_mode": None,
        "last_trusted_at": projection.get("last_trusted_at"),
        "stale": False,
        "blind": False,
        "cash": float(projection["cash"]),
        "equity": float(projection["equity"]),
        "realized_pnl": float(projection["realized_pnl"]),
        "unrealized_pnl": float(projection["unrealized_pnl"]),
        "realized_r": float(projection["realized_r"]),
        "unrealized_r": float(projection["unrealized_r"]),
        "position_quantity": int(projection["position_quantity"]),
        "pending_intent_count": int(projection["pending_intent_count"]),
        "decision_count": int(projection["decision_count"]),
        "trade_count": int(projection["trade_count"]),
        "safety": {
            "max_drawdown_r": int(projection["max_drawdown_r"]),
            "max_losing_streak": int(projection["max_losing_streak"]),
            "blind_minutes": int(projection["blind_minutes"]),
            "drawdown_r": float(projection["drawdown_r"]),
            "losing_streak": int(projection["losing_streak"]),
            "equity_high_water_r": float(projection["equity_high_water_r"]),
        },
        "as_of": canonical_utc(datetime.now(UTC)),
    }


def runtime_timeline(
    store: PaperRuntimeStore,
    trader_id: str,
    *,
    after_cursor: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Lifecycle control stream only (running/paused/…).

    Trade notifications must use ``runtime_activity`` — lifecycle rows never
    carry buy/entry/exit kinds.
    """
    if after_cursor < 0 or limit < 1 or limit > 500:
        raise PaperRuntimeServiceError(
            "invalid_cursor", "timeline cursor/limit out of bounds"
        )
    try:
        store.get_trader(trader_id)
    except LookupError as exc:
        raise PaperRuntimeServiceError(
            "trader_not_found", f"paper trader not found: {trader_id}"
        ) from exc
    with store._connect() as connection:  # noqa: SLF001
        rows = connection.execute(
            """
            SELECT sequence, lifecycle_event_id, lifecycle, lifecycle_version,
                   reason, created_at
            FROM paper_lifecycle_events
            WHERE trader_id=? AND sequence > ?
            ORDER BY sequence
            LIMIT ?
            """,
            (trader_id, after_cursor, limit),
        ).fetchall()
    items = [
        {
            "cursor": int(row["sequence"]),
            "created_at": row["created_at"],
            "payload": {
                "lifecycle_event_id": row["lifecycle_event_id"],
                "lifecycle": row["lifecycle"],
                "lifecycle_version": int(row["lifecycle_version"]),
                "reason": row["reason"],
            },
        }
        for row in rows
    ]
    next_cursor = items[-1]["cursor"] if items else after_cursor
    return {
        "schema": "paper_runtime_timeline.v1",
        "trader_id": trader_id,
        "after_cursor": after_cursor,
        "next_cursor": next_cursor,
        "count": len(items),
        "items": items,
    }


def runtime_activity(
    store: PaperRuntimeStore,
    trader_id: str,
    *,
    after_cursor: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Trade-relevant activity stream for notifications (intents / fills / trades).

    Cursor is a dense integer over a merge of:
    - paper_intents → kind ``buy_opportunity``
    - paper_fills (entry via order.intent_id) → kind ``entry``
    - paper_trades → kind ``exit`` with pnl/R
    """
    if after_cursor < 0 or limit < 1 or limit > 500:
        raise PaperRuntimeServiceError(
            "invalid_cursor", "activity cursor/limit out of bounds"
        )
    try:
        store.get_trader(trader_id)
    except LookupError as exc:
        raise PaperRuntimeServiceError(
            "trader_not_found", f"paper trader not found: {trader_id}"
        ) from exc

    raw_events: list[tuple[str, int, str, dict[str, Any]]] = []
    with store._connect() as connection:  # noqa: SLF001
        for row in connection.execute(
            """
            SELECT sequence, intent_id, status, payload_json, created_at
            FROM paper_intents
            WHERE trader_id=?
            ORDER BY sequence
            """,
            (trader_id,),
        ).fetchall():
            payload_obj: dict[str, Any] = {}
            try:
                parsed = json.loads(row["payload_json"])
                if isinstance(parsed, dict):
                    payload_obj = parsed
            except (TypeError, json.JSONDecodeError):
                payload_obj = {}
            side = payload_obj.get("side")
            price = payload_obj.get("entry_reference") or payload_obj.get("price")
            raw_events.append(
                (
                    row["created_at"],
                    int(row["sequence"]),
                    f"intent:{row['intent_id']}",
                    {
                        "kind": "buy_opportunity",
                        "source": "intent",
                        "intent_id": row["intent_id"],
                        "status": row["status"],
                        "side": side,
                        "price": price,
                        "reason": "策略產生入市意圖",
                    },
                )
            )

        for row in connection.execute(
            """
            SELECT f.sequence AS sequence, f.fill_id AS fill_id, f.quantity,
                   f.price, f.created_at, o.intent_id
            FROM paper_fills f
            JOIN paper_orders o ON o.order_id = f.order_id
            WHERE f.trader_id=?
            ORDER BY f.sequence
            """,
            (trader_id,),
        ).fetchall():
            # Entry fills reference an intent; exit fills do not.
            is_entry = row["intent_id"] is not None
            qty = int(row["quantity"])
            side = "long" if qty > 0 else "short"
            fill_id = str(row["fill_id"])
            raw_events.append(
                (
                    row["created_at"],
                    int(row["sequence"]),
                    f"fill:{fill_id}",
                    {
                        "kind": "entry" if is_entry else "exit",
                        "source": "fill",
                        "fill_id": fill_id,
                        "quantity": abs(qty),
                        "price": float(row["price"]),
                        "side": side,
                        "role": "entry" if is_entry else "exit",
                    },
                )
            )

        for row in connection.execute(
            """
            SELECT sequence, trade_id, side, quantity, gross_pnl, net_pnl,
                   net_r, opened_at, closed_at
            FROM paper_trades
            WHERE trader_id=?
            ORDER BY sequence
            """,
            (trader_id,),
        ).fetchall():
            net_pnl = float(row["net_pnl"])
            net_r = float(row["net_r"])
            raw_events.append(
                (
                    row["closed_at"] or row["opened_at"],
                    int(row["sequence"]),
                    f"trade:{row['trade_id']}",
                    {
                        "kind": "exit",
                        "source": "trade",
                        "trade_id": row["trade_id"],
                        "side": row["side"],
                        "quantity": int(row["quantity"]),
                        "pnl": net_pnl,
                        "realized_r": net_r,
                        "profitable": net_pnl >= 0 or net_r >= 0,
                        "reason": "模擬交易平倉",
                    },
                )
            )

    # Stable order: created_at, then source key, then sequence
    raw_events.sort(key=lambda item: (item[0], item[2], item[1]))
    items: list[dict[str, Any]] = []
    for index, (created_at, _seq, _key, payload) in enumerate(raw_events, start=1):
        if index <= after_cursor:
            continue
        items.append(
            {
                "cursor": index,
                "created_at": created_at,
                "payload": payload,
            }
        )
        if len(items) >= limit:
            break
    next_cursor = items[-1]["cursor"] if items else after_cursor
    return {
        "schema": "paper_runtime_activity.v1",
        "trader_id": trader_id,
        "after_cursor": after_cursor,
        "next_cursor": next_cursor,
        "count": len(items),
        "items": items,
    }


def apply_lifecycle_command(
    store: PaperRuntimeStore,
    *,
    trader_id: str,
    command: Literal["start", "pause", "resume", "permanent_stop"],
    request_id: str,
    expected_lifecycle_version: int,
    selection_fingerprint: str,
    contract: Any,
    decision_source: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply one Owner lifecycle command through PaperTraderRuntime."""
    _require_uuid4(request_id)
    now = now or datetime.now(UTC)
    try:
        trader = store.get_trader(trader_id)
    except LookupError as exc:
        raise PaperRuntimeServiceError(
            "trader_not_found", f"paper trader not found: {trader_id}"
        ) from exc
    if trader.lifecycle_version != expected_lifecycle_version:
        raise PaperRuntimeServiceError(
            "stale_lifecycle_version",
            "lifecycle version does not match; zero write performed",
        )
    if trader.selection_fingerprint != selection_fingerprint:
        raise PaperRuntimeServiceError(
            "selection_fingerprint_mismatch",
            "selection fingerprint does not match locked trader",
        )
    runtime = PaperTraderRuntime(
        store=store,
        trader_id=trader_id,
        contract=contract,
        decision_source=decision_source,
    )
    try:
        if command == "start":
            snap = runtime.start(now=now)
        elif command == "pause":
            snap = runtime.request_pause(now=now)
        elif command == "resume":
            # Resume from paused only.
            current = store.get_trader(trader_id)
            if current.lifecycle != "paused":
                raise PaperRuntimeServiceError(
                    "invalid_lifecycle_transition",
                    f"cannot resume from {current.lifecycle}",
                )
            snap = runtime.start(now=now)
        elif command == "permanent_stop":
            snap = runtime.request_permanent_stop(now=now)
        else:
            raise PaperRuntimeServiceError("unknown_command", command)
    except PaperStoreLifecycleConflictError as exc:
        raise PaperRuntimeServiceError(
            "lifecycle_conflict", str(exc)
        ) from exc
    return {
        "schema": "paper_runtime_command.v1",
        "request_id": request_id,
        "trader_id": trader_id,
        "command": command,
        "lifecycle": snap.lifecycle,
        "lifecycle_version": snap.lifecycle_version,
        "accepted_at": canonical_utc(now),
    }


def preflight_runtime(
    store: PaperRuntimeStore,
    *,
    trader_id: str,
    request_id: str,
    expected_lifecycle_version: int,
    selection_fingerprint: str,
    requested_market_mode: PublicMarketMode,
    gateway_reachable: bool | None = None,
) -> dict[str, Any]:
    """Read-only preflight — zero state-changing writes."""
    _require_uuid4(request_id)
    try:
        trader = store.get_trader(trader_id)
        integrity = store.integrity_check()
    except LookupError as exc:
        raise PaperRuntimeServiceError(
            "trader_not_found", f"paper trader not found: {trader_id}"
        ) from exc
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "immutable_selection",
            trader.selection_fingerprint == selection_fingerprint
            and trader.lifecycle_version == expected_lifecycle_version,
            "selection fingerprint and lifecycle version match",
            "selection or lifecycle version mismatch",
        )
    )
    checks.append(
        _check(
            "store_integrity",
            integrity.schema_matches and integrity.quick_check == "ok",
            "v4 store integrity ok",
            "v4 store integrity failed",
        )
    )
    checks.append(
        _check(
            "lifecycle_eligible",
            trader.lifecycle
            in ("provisioned", "paused", "recovery_required"),
            f"lifecycle {trader.lifecycle} may start/resume after Owner command",
            f"lifecycle {trader.lifecycle} cannot start",
        )
    )
    # Gateway: honest unknown when not probed; fail closed for live if unreachable.
    if gateway_reachable is None:
        checks.append(
            {
                "code": "gateway_handshake",
                "status": "unknown",
                "detail": "IB Gateway session not probed in this process",
            }
        )
        gateway_status = "unknown"
    elif gateway_reachable:
        checks.append(
            {
                "code": "gateway_handshake",
                "status": "pass",
                "detail": "port reachable (session not claimed as verified)",
            }
        )
        gateway_status = "pass"
    else:
        checks.append(
            {
                "code": "gateway_handshake",
                "status": "block",
                "detail": "IB Gateway port not reachable",
            }
        )
        gateway_status = "block"

    if requested_market_mode == "live" and gateway_status == "block":
        overall = "blocked"
    elif any(c["status"] == "block" for c in checks):
        overall = "blocked"
    elif any(c["status"] == "unknown" for c in checks):
        overall = "unknown"
    else:
        overall = "ready"

    now = datetime.now(UTC)
    return {
        "schema": "paper_runtime_preflight.v1",
        "preflight_id": request_id,
        "trader_id": trader_id,
        "expected_lifecycle_version": expected_lifecycle_version,
        "selection_fingerprint": selection_fingerprint,
        "requested_market_mode": requested_market_mode,
        "overall": overall,
        "checks": checks,
        "lifecycle_states": list(LIFECYCLE_STATES),
        "checked_at": canonical_utc(now),
        "expires_at": canonical_utc(now),
    }


def _check(code: str, ok: bool, pass_detail: str, block_detail: str) -> dict[str, str]:
    return {
        "code": code,
        "status": "pass" if ok else "block",
        "detail": pass_detail if ok else block_detail,
    }


def _require_uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise PaperRuntimeServiceError(
            "invalid_request_id", "request_id must be canonical UUID4"
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise PaperRuntimeServiceError(
            "invalid_request_id", "request_id must be canonical UUID4"
        )
    return value


# Re-export for route layer fingerprint helpers
__all__ = [
    "PaperRuntimeServiceError",
    "apply_lifecycle_command",
    "preflight_runtime",
    "runtime_capabilities",
    "runtime_snapshot",
    "runtime_timeline",
    "selection_fingerprint",
]
