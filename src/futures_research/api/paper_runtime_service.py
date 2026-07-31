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


def runtime_trade_lesson(
    store: PaperRuntimeStore,
    trader_id: str,
    *,
    chart_bars: list[dict[str, Any]] | None = None,
    bar_limit: int = 500,
) -> dict[str, Any]:
    """Owner-facing trade lesson: position, chart markers, fills, conditions.

    One read-only payload so the UI can open a position inspector with:
    - current open position (entry / stop / target / last price)
    - OHLC bars for replay scrubbing
    - entry/exit markers and price levels
    - ordered condition timeline explaining why fills happened
    - short teaching tips (Cantonese, no technical jargon)
    """
    if bar_limit < 1 or bar_limit > 2000:
        raise PaperRuntimeServiceError(
            "invalid_limit", "trade-lesson bar_limit out of bounds"
        )
    try:
        projection = store.runtime_projection(trader_id)
    except LookupError as exc:
        raise PaperRuntimeServiceError(
            "trader_not_found", f"paper trader not found: {trader_id}"
        ) from exc

    selection = json.loads(projection["selection_json"])
    contract_id = str(selection.get("contract_id") or "")
    strategy_id = str(selection.get("strategy_id") or "")
    position_qty = int(projection["position_quantity"] or 0)
    avg_entry = projection["average_entry_price"]
    stop_price = projection["stop_price"]
    target_price = projection["target_price"]
    risk_amount = projection.get("risk_amount")

    with store._connect() as connection:  # noqa: SLF001
        bar_rows = connection.execute(
            """
            SELECT m.sequence, m.event_at, m.open_price, m.high_price,
                   m.low_price, m.close_price, m.volume, m.mode
            FROM paper_market_inputs AS m
            WHERE m.input_id IN (
                SELECT input_id FROM paper_processing_checkpoints
                WHERE trader_id=?
            )
            ORDER BY m.sequence
            LIMIT ?
            """,
            (trader_id, bar_limit),
        ).fetchall()
        if not bar_rows:
            # Fallback: any inputs on the locked contract (demo / single-trader).
            bar_rows = connection.execute(
                """
                SELECT sequence, event_at, open_price, high_price,
                       low_price, close_price, volume, mode
                FROM paper_market_inputs
                WHERE contract_id=?
                ORDER BY sequence
                LIMIT ?
                """,
                (contract_id, bar_limit),
            ).fetchall()

        fill_rows = connection.execute(
            """
            SELECT f.fill_id, f.quantity, f.price, f.commission, f.slippage,
                   f.created_at, f.input_id, o.intent_id, o.payload_json AS order_payload,
                   m.event_at AS bar_at
            FROM paper_fills AS f
            JOIN paper_orders AS o ON o.order_id = f.order_id
            LEFT JOIN paper_market_inputs AS m ON m.input_id = f.input_id
            WHERE f.trader_id=?
            ORDER BY f.sequence
            """,
            (trader_id,),
        ).fetchall()

        trade_rows = connection.execute(
            """
            SELECT t.trade_id, t.side, t.quantity, t.gross_pnl, t.net_pnl, t.net_r,
                   t.opened_at, t.closed_at,
                   ef.price AS entry_price, xf.price AS exit_price,
                   ef.created_at AS entry_at, xf.created_at AS exit_at,
                   me.event_at AS entry_bar_at, mx.event_at AS exit_bar_at
            FROM paper_trades AS t
            JOIN paper_fills AS ef ON ef.fill_id = t.entry_fill_id
            JOIN paper_fills AS xf ON xf.fill_id = t.exit_fill_id
            LEFT JOIN paper_market_inputs AS me ON me.input_id = ef.input_id
            LEFT JOIN paper_market_inputs AS mx ON mx.input_id = xf.input_id
            WHERE t.trader_id=?
            ORDER BY t.sequence
            """,
            (trader_id,),
        ).fetchall()

        intent_rows = connection.execute(
            """
            SELECT intent_id, decision_id, status, payload_json, created_at
            FROM paper_intents
            WHERE trader_id=?
            ORDER BY sequence
            """,
            (trader_id,),
        ).fetchall()

        decision_rows = connection.execute(
            """
            SELECT d.decision_id, d.payload_json, d.created_at, d.input_id,
                   m.event_at AS bar_at, m.close_price AS bar_close
            FROM paper_decisions AS d
            LEFT JOIN paper_market_inputs AS m ON m.input_id = d.input_id
            WHERE d.trader_id=?
            ORDER BY d.sequence
            """,
            (trader_id,),
        ).fetchall()

        position_event_rows = connection.execute(
            """
            SELECT quantity, average_entry_price, stop_price, target_price,
                   reason, created_at, input_id
            FROM paper_position_events
            WHERE trader_id=?
            ORDER BY sequence
            """,
            (trader_id,),
        ).fetchall()

    bars: list[dict[str, Any]] = [
        {
            "cursor": int(row["sequence"]),
            "event_at": row["event_at"],
            "open": float(row["open_price"]),
            "high": float(row["high_price"]),
            "low": float(row["low_price"]),
            "close": float(row["close_price"]),
            "volume": float(row["volume"]),
            "mode": row["mode"],
        }
        for row in bar_rows
    ]
    # Process-local chart points (live Gateway path may not yet be in DB join).
    if not bars and chart_bars:
        bars = [
            {
                "cursor": index + 1,
                "event_at": str(point.get("event_at") or ""),
                "open": float(point["open"]),
                "high": float(point["high"]),
                "low": float(point["low"]),
                "close": float(point["close"]),
                "volume": float(point.get("volume") or 0),
                "mode": "live",
            }
            for index, point in enumerate(chart_bars[:bar_limit])
            if point.get("open") is not None
        ]

    last_price: float | None = bars[-1]["close"] if bars else None

    fills: list[dict[str, Any]] = []
    markers: list[dict[str, Any]] = []
    for row in fill_rows:
        qty = int(row["quantity"])
        is_entry = row["intent_id"] is not None
        role = "entry" if is_entry else "exit"
        side = "long" if qty > 0 else "short"
        if not is_entry:
            # Exit quantity is often opposite of position direction.
            side = "long" if qty < 0 else "short"
        event_at = row["bar_at"] or row["created_at"]
        price = float(row["price"])
        fill_item = {
            "fill_id": row["fill_id"],
            "role": role,
            "side": side,
            "quantity": abs(qty),
            "price": price,
            "commission": float(row["commission"]),
            "slippage": float(row["slippage"]),
            "event_at": event_at,
            "created_at": row["created_at"],
            "label": "買入開倉" if role == "entry" else "平倉離場",
            "teach": (
                "策略產生入市意圖後，以當時可信收市價模擬成交。App 永遠唔會向 IB 發單。"
                if role == "entry"
                else "達到止蝕／止賺或手動／安全網觸發時，以可信價模擬平倉。"
            ),
        }
        fills.append(fill_item)
        markers.append(
            {
                "kind": "ENTRY" if role == "entry" else "EXIT",
                "ts": event_at,
                "price": price,
                "side": side,
                "quantity": abs(qty),
                "label": fill_item["label"],
            }
        )

    trades: list[dict[str, Any]] = []
    for row in trade_rows:
        entry_price = float(row["entry_price"])
        exit_price = float(row["exit_price"])
        net_pnl = float(row["net_pnl"])
        net_r = float(row["net_r"])
        side = row["side"]
        trades.append(
            {
                "trade_id": row["trade_id"],
                "side": side,
                "quantity": int(row["quantity"]),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "entry_at": row["entry_bar_at"] or row["opened_at"],
                "exit_at": row["exit_bar_at"] or row["closed_at"],
                "gross_pnl": float(row["gross_pnl"]),
                "net_pnl": net_pnl,
                "net_r": net_r,
                "profitable": net_pnl >= 0 or net_r >= 0,
                "label": "好倉" if side == "long" else "淡倉",
                "teach": (
                    f"{'好倉' if side == 'long' else '淡倉'} "
                    f"由 {entry_price:.2f} 到 {exit_price:.2f}，"
                    f"淨盈虧 {net_pnl:+.2f}（{net_r:+.2f}R）。"
                    "R 係以入市時風險距離做單位：+1R 等於賺返當初願意輸嘅幅度。"
                ),
            }
        )
        markers.append(
            {
                "kind": "TARGET" if net_pnl >= 0 else "STOP",
                "ts": row["exit_bar_at"] or row["closed_at"],
                "price": exit_price,
                "side": side,
                "quantity": int(row["quantity"]),
                "label": "止賺離場" if net_pnl >= 0 else "止蝕離場",
            }
        )

    conditions: list[dict[str, Any]] = []
    for row in decision_rows:
        payload: dict[str, Any] = {}
        try:
            parsed = json.loads(row["payload_json"])
            if isinstance(parsed, dict):
                payload = parsed
        except (TypeError, json.JSONDecodeError):
            payload = {}
        summary = _decision_summary(payload)
        conditions.append(
            {
                "kind": "decision",
                "at": row["bar_at"] or row["created_at"],
                "title": "策略睇盤決定",
                "status": "recorded",
                "summary": summary,
                "detail": (
                    f"當時收市價 {row['bar_close']}"
                    if row["bar_close"] is not None
                    else "已記錄一次策略決定（可能冇產生入市意圖）。"
                ),
                "teach": "每一根可信 K 線都會問一次策略：而家係咪有入市／離場理由。冇理由就靜止，有理由先產生意圖。",
                "price": float(row["bar_close"]) if row["bar_close"] is not None else None,
            }
        )

    for row in intent_rows:
        payload = {}
        try:
            parsed = json.loads(row["payload_json"])
            if isinstance(parsed, dict):
                payload = parsed
        except (TypeError, json.JSONDecodeError):
            payload = {}
        status = str(row["status"])
        side_raw = payload.get("side") or payload.get("direction") or "long"
        entry_ref = payload.get("entry_reference") or payload.get("price")
        stop_ref = payload.get("stop_reference") or payload.get("stop")
        status_label = {
            "pending": "等待成交",
            "filled": "已成交",
            "cancelled": "已取消",
        }.get(status, status)
        conditions.append(
            {
                "kind": "intent",
                "at": row["created_at"],
                "title": f"入市意圖 · {status_label}",
                "status": status,
                "summary": (
                    f"{'好倉' if str(side_raw).lower() in ('long', 'buy', '1') else '淡倉'}"
                    + (f" · 參考價 {entry_ref}" if entry_ref is not None else "")
                    + (f" · 止蝕 {stop_ref}" if stop_ref is not None else "")
                ),
                "detail": "策略認為條件成立，發出模擬入市意圖。只有可信價先會成交。",
                "teach": (
                    "入市意圖 ≠ 已成交。要等下一根（或當下）可信價確認，"
                    "模擬引擎先會用 app 自家模擬成交——IB 只供應行情。"
                ),
                "price": float(entry_ref) if entry_ref is not None else None,
            }
        )

    for fill in fills:
        conditions.append(
            {
                "kind": fill["role"],
                "at": fill["event_at"],
                "title": fill["label"],
                "status": "filled",
                "summary": (
                    f"{'好倉' if fill['side'] == 'long' else '淡倉'} "
                    f"{fill['quantity']} 張 @ {fill['price']:.2f}"
                ),
                "detail": (
                    f"手續費 {fill['commission']:.2f} · 滑價 {fill['slippage']:.2f}"
                ),
                "teach": fill["teach"],
                "price": fill["price"],
            }
        )

    for row in position_event_rows:
        qty = int(row["quantity"])
        if qty == 0:
            title = "倉位清零"
            summary = "而家空手"
        else:
            title = "倉位更新"
            summary = (
                f"{'好倉' if qty > 0 else '淡倉'} {abs(qty)} 張"
                + (
                    f" · 均價 {float(row['average_entry_price']):.2f}"
                    if row["average_entry_price"] is not None
                    else ""
                )
            )
        conditions.append(
            {
                "kind": "position",
                "at": row["created_at"],
                "title": title,
                "status": "open" if qty != 0 else "flat",
                "summary": summary,
                "detail": row["reason"] or "倉位事件",
                "teach": (
                    "持倉時圖上會標買入價、止蝕、止賺同現價。"
                    "止蝕係你願意輸嘅界線；止賺係目標獲利離場位。"
                ),
                "price": (
                    float(row["average_entry_price"])
                    if row["average_entry_price"] is not None
                    else None
                ),
                "stop_price": (
                    float(row["stop_price"]) if row["stop_price"] is not None else None
                ),
                "target_price": (
                    float(row["target_price"])
                    if row["target_price"] is not None
                    else None
                ),
            }
        )

    conditions.sort(key=lambda item: (str(item.get("at") or ""), item["kind"]))

    levels: dict[str, float | None] = {
        "entry": float(avg_entry) if avg_entry is not None else None,
        "stop": float(stop_price) if stop_price is not None else None,
        "target": float(target_price) if target_price is not None else None,
        "last": last_price,
    }

    open_position: dict[str, Any] | None
    if position_qty != 0:
        side = "long" if position_qty > 0 else "short"
        open_position = {
            "side": side,
            "quantity": abs(position_qty),
            "average_entry_price": levels["entry"],
            "stop_price": levels["stop"],
            "target_price": levels["target"],
            "last_price": last_price,
            "unrealized_pnl": float(projection["unrealized_pnl"]),
            "unrealized_r": float(projection["unrealized_r"]),
            "risk_amount": float(risk_amount) if risk_amount is not None else None,
            "label": f"{'好倉' if side == 'long' else '淡倉'} {abs(position_qty)} 張",
            "instrument_label": contract_id or "未標合約",
            "teach": (
                f"而家揸住 {contract_id or '呢個合約'}。"
                "買入價係開倉均價；止蝕／止賺係策略鎖定嘅保護同目標。"
                "現價來自最近一根可信 K 線收市。"
            ),
        }
        if levels["entry"] is not None:
            markers.append(
                {
                    "kind": "OPEN",
                    "ts": bars[-1]["event_at"] if bars else projection.get("updated_at"),
                    "price": levels["entry"],
                    "side": side,
                    "quantity": abs(position_qty),
                    "label": "持倉均價",
                }
            )
    else:
        open_position = None

    teaching = _build_trade_teaching(
        has_bars=bool(bars),
        has_position=open_position is not None,
        fill_count=len(fills),
        trade_count=len(trades),
        intent_count=len(intent_rows),
        contract_id=contract_id,
        strategy_id=strategy_id,
    )

    return {
        "schema": "paper_trade_lesson.v1",
        "trader_id": trader_id,
        "as_of": canonical_utc(datetime.now(UTC)),
        "selection": {
            "strategy_id": strategy_id,
            "contract_id": contract_id,
            "baseline_run_id": selection.get("baseline_run_id"),
        },
        "lifecycle": {
            "state": projection["lifecycle"],
            "version": int(projection["lifecycle_version"]),
            "reason": projection["lifecycle_reason"],
        },
        "account": {
            "cash": float(projection["cash"]),
            "equity": float(projection["equity"]),
            "realized_pnl": float(projection["realized_pnl"]),
            "unrealized_pnl": float(projection["unrealized_pnl"]),
            "realized_r": float(projection["realized_r"]),
            "unrealized_r": float(projection["unrealized_r"]),
        },
        "open_position": open_position,
        "levels": levels,
        "bars": bars,
        "markers": markers,
        "fills": fills,
        "trades": trades,
        "conditions": conditions,
        "counts": {
            "bar_count": len(bars),
            "fill_count": len(fills),
            "trade_count": len(trades),
            "condition_count": len(conditions),
            "decision_count": int(projection["decision_count"] or 0),
            "pending_intent_count": int(projection["pending_intent_count"] or 0),
        },
        "teaching": teaching,
        "replay": {
            "supported": True,
            "bar_count": len(bars),
            "hint": (
                "用下方進度掣由第一根 K 線播到而家，睇買入、止蝕、止賺同現價點樣出現。"
                if bars
                else "暫時未有 K 線；開始模擬並餵示範行情或接 IB 行情後就可以重播。"
            ),
        },
    }


def _decision_summary(payload: dict[str, Any]) -> str:
    if not payload:
        return "策略完成一次睇盤（無額外說明）"
    for key in ("summary", "reason", "message", "kind", "action"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if payload.get("entry_intents") or payload.get("has_intent"):
        return "策略認為有入市機會"
    return "策略完成一次睇盤"


def _build_trade_teaching(
    *,
    has_bars: bool,
    has_position: bool,
    fill_count: int,
    trade_count: int,
    intent_count: int,
    contract_id: str,
    strategy_id: str,
) -> dict[str, Any]:
    tips: list[dict[str, str]] = [
        {
            "id": "what-is-sim",
            "title": "呢度係咩？",
            "body": (
                "模擬交易員用策略同真實／示範行情，喺 app 入面自己做買賣紀錄。"
                "IB 只供應行情，永遠唔會代你向交易所落真單。"
            ),
        },
        {
            "id": "chart-marks",
            "title": "圖上標記點睇",
            "body": (
                "↑ 或 「買入」= 開倉成交價；"
                "止蝕線 = 你願意認輸嘅界線；"
                "止賺線 = 目標獲利離場；"
                "現價線 = 最近一根 K 線收市。"
            ),
        },
        {
            "id": "r-multiple",
            "title": "R 係咩意思？",
            "body": (
                "R 係風險單位。開倉時止蝕距離定義 1R。"
                "賺 +2R 即係賺咗兩倍當初願意輸嘅幅度；蝕 −1R 即係打中止蝕。"
            ),
        },
        {
            "id": "conditions",
            "title": "成交條件列表",
            "body": (
                "下面「達成條件」由時間排：策略睇盤 → 入市意圖 → 模擬成交 → 倉位更新。"
                "每一行解釋電腦點解喺嗰一刻做／唔做。"
            ),
        },
        {
            "id": "replay",
            "title": "點樣用重播",
            "body": (
                "撳播放會由第一根 K 線慢慢行到最新。"
                "進度條可以拖，方便對住某一刻嘅買入同止賺位複盤。"
            ),
        },
    ]
    if not has_bars:
        tips.insert(
            0,
            {
                "id": "no-bars",
                "title": "而家未有走勢",
                "body": (
                    "交易員建立後要「開始模擬」，再用「餵示範行情」或開 IB Gateway 收真 bar，"
                    "先有圖同成交。"
                ),
            },
        )
    if has_position:
        tips.append(
            {
                "id": "open-now",
                "title": "而家有持倉",
                "body": (
                    f"你而家揸住 {contract_id or '合約'}。"
                    "留意止蝕有冇被打穿、現價同止賺距離，以及未實現盈虧。"
                ),
            }
        )
    elif fill_count == 0 and intent_count == 0:
        tips.append(
            {
                "id": "waiting",
                "title": "仲未有成交",
                "body": (
                    f"策略「{strategy_id or '未標'}」可能仲等條件，"
                    "或者行情未餵。空倉唔等於壞——好多時係正確地唔做。"
                ),
            }
        )
    if trade_count > 0:
        tips.append(
            {
                "id": "closed-trades",
                "title": "已完成交易",
                "body": (
                    f"已經有 {trade_count} 筆完整開平倉。可以對住圖上標記同條件列表，"
                    "複盤每一筆點入、點出。"
                ),
            }
        )
    return {
        "headline": "交易教學 · 倉位與重播",
        "intro": (
            "撳持倉或打開本面板，可以睇呢個交易員買咗咩、點解成交、"
            "止蝕／止賺喺邊，同埋用重播由頭複盤。"
        ),
        "tips": tips,
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
    "runtime_activity",
    "runtime_capabilities",
    "runtime_snapshot",
    "runtime_timeline",
    "runtime_trade_lesson",
    "selection_fingerprint",
]
