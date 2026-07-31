"""Daytrade logical statistics — all derived from ledger events only.

formula_version: daytrade-stats-v2
Live scale is 1m_close; never mix with 1s_worst backtest rankings.
"""

from __future__ import annotations

from typing import Any


EXIT_KINDS = frozenset({"CLOSE", "STOP", "TARGET", "FORCE_FLAT"})
STATS_FORMULA_VERSION = "daytrade-stats-v2"
LIVE_SCALE_LABEL = "成交尺：1m_close（穩定 live paper）— 唔可比對 1s_worst 回測"


def pair_trades_from_events(
    events: list[dict[str, Any]],
    *,
    point_value: float,
) -> list[dict[str, Any]]:
    """Pair OPEN → exit events into closed trades with R multiple."""
    trades: list[dict[str, Any]] = []
    open_stack: list[dict[str, Any]] = []
    for ev in events:
        kind = ev.get("kind")
        if kind == "OPEN":
            open_stack.append(ev)
            continue
        if kind not in EXIT_KINDS or not open_stack:
            continue
        entry = open_stack.pop(0)
        entry_rp = float(entry.get("realized_pnl") or 0)
        exit_rp = float(ev.get("realized_pnl") or 0)
        pnl = exit_rp - entry_rp
        entry_price = entry.get("price")
        stop = entry.get("stop")
        qty = int(entry.get("qty") or 1)
        risk_points: float | None = None
        r_multiple: float | None = None
        one_r_dollars: float | None = None
        if (
            entry_price is not None
            and stop is not None
            and float(entry_price) != float(stop)
            and point_value > 0
        ):
            risk_points = abs(float(entry_price) - float(stop))
            one_r_dollars = risk_points * point_value * max(qty, 1)
            if one_r_dollars > 0:
                r_multiple = pnl / one_r_dollars
        trades.append(
            {
                "side": entry.get("side"),
                "qty": qty,
                "entry_price": entry_price,
                "exit_price": ev.get("price"),
                "exit_kind": kind,
                "entry_ts": entry.get("ts"),
                "exit_ts": ev.get("ts"),
                "stop": stop,
                "target": entry.get("target"),
                "pnl": pnl,
                "won": pnl > 0,
                "breakeven": pnl == 0,
                "risk_points": risk_points,
                "one_r_dollars": one_r_dollars,
                "r_multiple": r_multiple,
                "entry_ordinal": entry.get("ordinal"),
                "exit_ordinal": ev.get("ordinal"),
            }
        )
    return trades


def max_drawdown_from_equity(equities: list[float]) -> float:
    """Peak-to-trough drawdown as fraction of peak (0..1+)."""
    if not equities:
        return 0.0
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def streak_stats(trades: list[dict[str, Any]]) -> tuple[int, int]:
    """Return (max_win_streak, max_loss_streak). Breakeven breaks both."""
    max_w = max_l = 0
    cur_w = cur_l = 0
    for t in trades:
        pnl = float(t["pnl"])
        if pnl > 0:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        elif pnl < 0:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
        else:
            cur_w = cur_l = 0
    return max_w, max_l


def summarize_trades(
    trades: list[dict[str, Any]],
    *,
    equities: list[float] | None = None,
    starting_equity: float | None = None,
    ending_equity: float | None = None,
) -> dict[str, Any]:
    wins = [t for t in trades if float(t["pnl"]) > 0]
    losses = [t for t in trades if float(t["pnl"]) < 0]
    be = [t for t in trades if float(t["pnl"]) == 0]
    n_decided = len(wins) + len(losses)
    sum_win = sum(float(t["pnl"]) for t in wins)
    sum_loss = sum(float(t["pnl"]) for t in losses)
    total_pnl = sum(float(t["pnl"]) for t in trades)

    profit_factor: float | None
    if sum_loss < 0:
        profit_factor = sum_win / abs(sum_loss) if abs(sum_loss) > 0 else None
    elif sum_win > 0 and not losses:
        profit_factor = None  # undefined / infinite — surface as null + flag
    else:
        profit_factor = None

    r_vals = [
        float(t["r_multiple"])
        for t in trades
        if t.get("r_multiple") is not None
    ]
    max_w, max_l = streak_stats(trades)

    exit_mix: dict[str, int] = {}
    for t in trades:
        k = str(t.get("exit_kind") or "UNKNOWN")
        exit_mix[k] = exit_mix.get(k, 0) + 1

    eq_list = equities or []
    peak = max(eq_list) if eq_list else ending_equity
    max_dd = max_drawdown_from_equity(eq_list) if eq_list else 0.0

    total_return: float | None = None
    if (
        starting_equity is not None
        and ending_equity is not None
        and starting_equity != 0
    ):
        total_return = (ending_equity - starting_equity) / starting_equity

    return {
        "closed_trade_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(be),
        "win_rate": (len(wins) / n_decided) if n_decided else None,
        "sample_sufficient": n_decided >= 20,
        "total_closed_pnl": total_pnl,
        "avg_win": (sum_win / len(wins)) if wins else None,
        "avg_loss": (sum_loss / len(losses)) if losses else None,
        "expectancy": (total_pnl / len(trades)) if trades else None,
        "expectancy_r": (sum(r_vals) / len(r_vals)) if r_vals else None,
        "profit_factor": profit_factor,
        "profit_factor_infinite": bool(sum_win > 0 and not losses and trades),
        "max_win_streak": max_w,
        "max_loss_streak": max_l,
        "exit_mix": exit_mix,
        "peak_equity": peak,
        "max_drawdown": max_dd,
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "total_return": total_return,
        "trades": trades,
        "formula_version": STATS_FORMULA_VERSION,
        "live_scale_label": LIVE_SCALE_LABEL,
    }


def market_reference_from_bars(
    bars: list[dict[str, Any]],
    *,
    or_minutes: int,
    point_value: float,
    open_position: dict[str, Any] | None,
) -> dict[str, Any]:
    """Important market reference numbers for the chart panel."""
    if not bars:
        return {
            "last_price": None,
            "session_open": None,
            "session_high": None,
            "session_low": None,
            "session_range": None,
            "session_range_dollars": None,
            "or_high": None,
            "or_low": None,
            "or_width": None,
            "or_width_dollars": None,
            "bar_count": 0,
            "one_r_dollars": None,
            "unrealized_hint": None,
        }
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    session_open = float(bars[0]["open"])
    session_high = max(highs)
    session_low = min(lows)
    last_price = closes[-1]
    session_range = session_high - session_low

    # Opening range = first N completed minutes (bars sorted ascending)
    or_slice = bars[: max(or_minutes, 1)]
    or_high = max(float(b["high"]) for b in or_slice)
    or_low = min(float(b["low"]) for b in or_slice)
    or_width = or_high - or_low

    one_r = None
    unrealized = None
    if open_position:
        entry = float(open_position.get("entry_price") or 0)
        stop = float(open_position.get("stop") or 0)
        qty = int(open_position.get("qty") or 1)
        side = open_position.get("side")
        if entry and stop and entry != stop:
            one_r = abs(entry - stop) * point_value * qty
        if side == "long":
            unrealized = (last_price - entry) * point_value * qty
        elif side == "short":
            unrealized = (entry - last_price) * point_value * qty

    return {
        "last_price": last_price,
        "session_open": session_open,
        "session_high": session_high,
        "session_low": session_low,
        "session_range": session_range,
        "session_range_dollars": session_range * point_value,
        "or_high": or_high,
        "or_low": or_low,
        "or_width": or_width,
        "or_width_dollars": or_width * point_value,
        "bar_count": len(bars),
        "one_r_dollars": one_r,
        "unrealized_hint": unrealized,
    }
