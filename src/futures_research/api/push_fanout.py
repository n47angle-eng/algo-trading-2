"""Best-effort Web Push fan-out for paper/backtest domain events.

When VAPID + pywebpush are configured and subscriptions exist, notifications
reach installed home-screen PWAs even if the app is backgrounded/closed.
When not configured, calls no-op safely (client-side polling still covers the
open-PWA path).
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def fanout_push(
    *,
    title: str,
    body: str,
    tag: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send one notification payload to all stored push subscriptions."""
    try:
        from futures_research.api.routes_push import _send_web_push
    except Exception:  # noqa: BLE001
        return {"sent": 0, "failed": 0, "skipped": "import"}

    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "tag": tag,
        "data": data or {},
        "icon": "/icons/icon-192.png",
        "badge": "/icons/icon-192.png",
    }
    try:
        return _send_web_push(payload)
    except Exception as exc:  # noqa: BLE001 — never break trading path
        _LOGGER.info("push fan-out skipped: %s", exc)
        return {"sent": 0, "failed": 0, "skipped": str(exc)}


def fanout_paper_activity(
    *,
    trader_id: str,
    kind: str,
    symbol: str | None = None,
    price: float | None = None,
    quantity: int | None = None,
    side: str | None = None,
    pnl: float | None = None,
    realized_r: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Map paper activity kinds to push title/body and fan out."""
    symbol_part = symbol or "合約"
    if kind == "buy_opportunity":
        title = "買入機會"
        price_part = f" @ {price:.2f}" if price is not None else ""
        body = f"模擬交易員 {trader_id} · {symbol_part}{price_part}"
        if reason:
            body = f"{body} — {reason}"
        notif_type = "buy_opportunity"
    elif kind == "entry":
        title = "入市開倉"
        side_label = "多" if side == "long" else "空" if side == "short" else "開倉"
        qty_part = f" ×{quantity}" if quantity is not None else ""
        price_part = f" @ {price:.2f}" if price is not None else ""
        body = f"模擬交易員 {trader_id} · {side_label}{qty_part} {symbol_part}{price_part}"
        notif_type = "entry"
    elif kind == "exit":
        profitable = True
        if pnl is not None:
            profitable = pnl >= 0
        elif realized_r is not None:
            profitable = realized_r >= 0
        title = "獲利離場" if profitable else "止損 / 離場"
        pnl_parts: list[str] = []
        if pnl is not None:
            sign = "+" if pnl >= 0 else ""
            pnl_parts.append(f"{sign}{pnl:.2f}")
        if realized_r is not None:
            sign = "+" if realized_r >= 0 else ""
            pnl_parts.append(f"{sign}{realized_r:.2f}R")
        pnl_text = " · ".join(pnl_parts)
        body = f"模擬交易員 {trader_id} · {symbol_part}"
        if pnl_text:
            body = f"{body} · {pnl_text}"
        notif_type = "exit_profit" if profitable else "exit_loss"
    else:
        return {"sent": 0, "failed": 0, "skipped": f"unknown_kind:{kind}"}

    return fanout_push(
        title=title,
        body=body,
        tag=f"paper-{kind}-{trader_id}",
        data={
            "type": notif_type,
            "traderId": trader_id,
            "kind": kind,
            "url": "/paper",
        },
    )


def fanout_backtest_complete(
    *,
    batch_id: str,
    status: str,
    completed: int | None = None,
    failed: int | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    labels = {
        "completed": "已完成",
        "failed": "失敗",
        "cancelled": "已取消",
        "partial": "部分完成",
    }
    status_text = labels.get(status, status)
    counts = ""
    if total is not None:
        counts = f" · {completed or 0}/{total} 成功"
        if failed:
            counts += f" · {failed} 失敗"
    return fanout_push(
        title=f"回測{status_text}",
        body=f"批次 {batch_id}{counts}",
        tag=f"backtest-{batch_id}-{status}",
        data={
            "type": "backtest_complete",
            "batchId": batch_id,
            "status": status,
            "url": "/backtest",
        },
    )


def fanout_from_process_deltas(
    *,
    trader_id: str,
    decision_count_delta: int,
    fill_count_delta: int,
    trade_count_delta: int,
    position_quantity: int | None = None,
    realized_pnl: float | None = None,
    realized_r: float | None = None,
) -> list[dict[str, Any]]:
    """Emit push from runtime process() deltas (server-side live path)."""
    results: list[dict[str, Any]] = []
    if decision_count_delta > 0 and (position_quantity or 0) == 0 and trade_count_delta == 0:
        results.append(
            fanout_paper_activity(
                trader_id=trader_id,
                kind="buy_opportunity",
                reason="策略產生新決策",
            )
        )
    if fill_count_delta > 0 and trade_count_delta == 0:
        # Fill without completed trade ≈ entry (or partial)
        side = None
        if position_quantity is not None and position_quantity != 0:
            side = "long" if position_quantity > 0 else "short"
        results.append(
            fanout_paper_activity(
                trader_id=trader_id,
                kind="entry",
                side=side,
                quantity=abs(position_quantity) if position_quantity else None,
            )
        )
    if trade_count_delta > 0:
        results.append(
            fanout_paper_activity(
                trader_id=trader_id,
                kind="exit",
                pnl=realized_pnl,
                realized_r=realized_r,
            )
        )
    return results
