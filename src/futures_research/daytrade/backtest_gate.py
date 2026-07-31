"""Daytrade backtest gate — formal default is 1s_worst; fail-closed without 1s data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from futures_research.daytrade.models import DayBar, DaytradeDataError, SessionConfig
from futures_research.daytrade.modes import (
    BACKTEST_DEFAULT_MODE_ID,
    BACKTEST_SEMANTICS_VERSION,
    get_execution_mode,
)
from futures_research.daytrade.presets import get_trader_config
from futures_research.daytrade.session_engine import run_session


@dataclass(frozen=True, slots=True)
class BacktestEstimate:
    execution_mode_id: str
    bar_mode: str
    fill_price_policy: str
    semantics_version: str
    requested_days: int
    covered_days: int
    effective_coverage_pct: float
    missing_days: list[str]
    can_run: bool
    degrade_reason: str | None
    limitations: list[str]


def estimate_backtest(
    *,
    trader_id: str,
    start: date,
    end: date,
    execution_mode_id: str = BACKTEST_DEFAULT_MODE_ID,
    bars_by_day: dict[date, list[DayBar]] | None = None,
    config: SessionConfig | None = None,
) -> BacktestEstimate:
    mode = get_execution_mode(execution_mode_id)
    cfg = config or get_trader_config(trader_id)
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur = date.fromordinal(cur.toordinal() + 1)

    bars_by_day = bars_by_day or {}
    missing: list[str] = []
    covered = 0
    for d in days:
        day_bars = bars_by_day.get(d) or []
        usable = [
            b
            for b in day_bars
            if b.symbol == cfg.symbol and b.bar_mode == mode.bar_mode
        ]
        if usable:
            covered += 1
        else:
            missing.append(d.isoformat())

    total = len(days) or 1
    coverage = 100.0 * covered / total
    can_run = covered > 0 and len(missing) == 0
    degrade_reason = None
    if mode.bar_mode == "1s" and missing:
        # Hard rule: never silently fall back to 1m while labeling 1s_worst
        can_run = False
        degrade_reason = "missing_1s_bars_fail_closed"
    elif missing:
        can_run = False
        degrade_reason = "missing_bars_fail_closed"

    limitations = [
        "no_order_book_nbbo",
        "not_identical_to_live_1m_close",
        f"trader={trader_id}",
        f"semantics={mode.semantics_version}",
    ]
    if mode.mode_id == "1s_worst":
        limitations.append(
            "成交模式：1s_worst（1 秒 K 線 × 最差邊：買取高、賣取低，另加摩擦成本）— 現實悲觀版"
        )

    return BacktestEstimate(
        execution_mode_id=mode.mode_id,
        bar_mode=mode.bar_mode,
        fill_price_policy=mode.fill_price_policy,
        semantics_version=mode.semantics_version,
        requested_days=len(days),
        covered_days=covered,
        effective_coverage_pct=coverage,
        missing_days=missing,
        can_run=can_run,
        degrade_reason=degrade_reason,
        limitations=limitations,
    )


def run_daytrade_backtest(
    *,
    trader_id: str,
    start: date,
    end: date,
    execution_mode_id: str = BACKTEST_DEFAULT_MODE_ID,
    bars_by_day: dict[date, list[DayBar]] | None = None,
    config: SessionConfig | None = None,
) -> dict[str, Any]:
    mode = get_execution_mode(execution_mode_id)
    cfg = config or get_trader_config(trader_id)
    estimate = estimate_backtest(
        trader_id=trader_id,
        start=start,
        end=end,
        execution_mode_id=execution_mode_id,
        bars_by_day=bars_by_day,
        config=cfg,
    )
    if not estimate.can_run:
        raise DaytradeDataError(
            f"backtest fail-closed: mode={mode.mode_id} "
            f"reason={estimate.degrade_reason} missing={estimate.missing_days[:5]}"
        )
    bars_by_day = bars_by_day or {}
    day_results: list[dict[str, Any]] = []
    equity = cfg.starting_equity
    for d in _iter_days(start, end):
        result = run_session(
            cfg,
            trading_date=d,
            bars=bars_by_day[d],
            starting_equity=equity,
            complete=True,
            execution_mode_id=mode.mode_id,
        )
        equity = result.equity
        day_results.append(result.to_summary_dict())

    return {
        "trader_id": trader_id,
        "execution_mode_id": mode.mode_id,
        "bar_mode": mode.bar_mode,
        "fill_price_policy": mode.fill_price_policy,
        "semantics_version": mode.semantics_version
        if mode.mode_id != "1s_worst"
        else BACKTEST_SEMANTICS_VERSION,
        "estimate": {
            "requested_days": estimate.requested_days,
            "covered_days": estimate.covered_days,
            "effective_coverage_pct": estimate.effective_coverage_pct,
            "missing_days": estimate.missing_days,
            "degrade_reason": estimate.degrade_reason,
        },
        "days": day_results,
        "ending_equity": equity,
        "limitations": estimate.limitations,
        "banner": (
            "成交模式：1s_worst（1 秒 K 線 × 最差邊：買取高、賣取低，另加摩擦成本）— 現實悲觀版"
            if mode.mode_id == "1s_worst"
            else f"成交模式：{mode.mode_id}"
        ),
        "formula_version": "daytrade-backtest-summary-v1",
        "authority": "python_only",
    }


def _iter_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur = date.fromordinal(cur.toordinal() + 1)
    return days
