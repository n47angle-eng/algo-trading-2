"""WO-004 scorecard calculator — pure read-only consumer of run artifacts.

Does not call strategy, execution, or MTF engines.  Inputs are completed trades,
events, and compact run facts; outputs are scorecard dimensions and the
opportunity funnel for ``result.v1``.

4-1: core dims 1/2/3/7 + evaluation funnel.
4-2: dims 2b/3b/4/6 + P2 placeholders + funnel unit-semantics fix ([062]).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from typing import Any, Literal

from futures_research.backtest.strategy import StrategySpec

ScoreStatus = Literal["pass", "warn", "insufficient_sample", "not_available_p2"]

# Baseline §5 / matrix §6 thresholds.
_MIN_TRADES_WARN = 100
_MIN_TRADES_PER_PARAM_WARN = 30.0
_TOP_N_LEVELS = (5, 10)
_COST_MULTIPLIERS = (1.0, 1.5, 2.0)
_PSR_BASELINE_SHARPE = 0.0  # matrix §6: test true Sharpe > 0
_VAR_CONFIDENCE = 0.95
_MIN_TRADES_FOR_DISTRIBUTION = 3
_MIN_TRADES_FOR_TOP5 = 5
_MIN_TRADES_FOR_TOP10 = 10
_MIN_TRADES_FOR_PSR = 3


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    """Minimal trade facts needed for P1 scorecard metrics (R, PnL, costs)."""

    net_pnl: float
    net_r: float
    exit_timestamp: datetime
    gross_pnl: float | None = None
    total_commission: float = 0.0
    slippage_dollars: float = 0.0
    risk_dollars: float | None = None


@dataclass(frozen=True, slots=True)
class CoreMetrics:
    """A-class metrics for dimensions 1, 2, 3, and 7."""

    trade_count: int
    param_count: int
    trades_per_param: float | None
    win_rate: float | None
    expectancy_r: float | None
    payoff_ratio: float | None
    profit_factor: float | None
    max_losing_streak: int
    max_drawdown_pnl: float
    max_drawdown_r: float | None
    dd_duration_trades: int
    calmar_r: float | None
    rule_count: int
    net_r: float
    net_pnl: float


@dataclass(frozen=True, slots=True)
class ExtendedMetrics:
    """4-2 metrics: luck (2b), tail (3b), cost (4), period cuts (6)."""

    top5_removed_net_r: float | None
    top10_removed_net_r: float | None
    psr: float | None
    sharpe_per_trade: float | None
    skew: float | None
    kurtosis: float | None
    tail_ratio: float | None
    var95_r: float | None
    cvar95_r: float | None
    cost_scenarios: Mapping[str, Mapping[str, float | None]]
    period_cuts_year: Mapping[str, Mapping[str, float | int | None]]
    period_cuts_month: Mapping[str, Mapping[str, float | int | None]]


@dataclass(frozen=True, slots=True)
class OpportunityFunnel:
    """Layered opportunity counts with explicit day-level vs evaluation-level units.

    funnel.v1 dual units ([062]):
    - Day-level: ``daily_trend_days`` from ``daily_regime_changed`` records only.
    - Evaluation-level: ``evaluations_passing_*`` from signal evaluation events.
    """

    schema: Literal["funnel.v1"] = "funnel.v1"
    status: Literal["ok", "insufficient_sample"] = "ok"
    daily_trend_days: int = 0
    evaluations_passing_daily_gate: int = 0
    evaluations_passing_mid_gate: int = 0
    signals_created: int = 0
    fills: int = 0
    reject_reasons: Mapping[str, int] | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        """JSON shape for ``result.v1`` ``funnel`` with unit documentation."""
        return {
            "schema": self.schema,
            "status": self.status,
            "daily_trend_days": self.daily_trend_days,
            "evaluations_passing_daily_gate": self.evaluations_passing_daily_gate,
            "evaluations_passing_mid_gate": self.evaluations_passing_mid_gate,
            "signals_created": self.signals_created,
            "fills": self.fills,
            "reject_reasons": dict(self.reject_reasons or {}),
            "notes": self.notes,
            "units": {
                "daily_trend_days": (
                    "day-level: trading days with active daily regime == trend; "
                    "source = daily_regime_changed step function over event dates "
                    "(never inferred from evaluation counts)"
                ),
                "evaluations_passing_daily_gate": (
                    "evaluation-level: signal evaluations not rejected for daily_regime_*"
                ),
                "evaluations_passing_mid_gate": (
                    "evaluation-level: signal evaluations not rejected for daily_regime_* or mid_*"
                ),
                "signals_created": "evaluation-level: signal_created event count",
                "fills": "completed trades (trades sidecar count)",
            },
        }


@dataclass(frozen=True, slots=True)
class ScorecardItem:
    """One dimension row in ``result.v1`` ``scorecard``."""

    dim: str
    status: ScoreStatus
    detail: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        """JSON shape matching docs/05 scorecard sample."""
        return {"dim": self.dim, "status": self.status, "detail": dict(self.detail)}


@dataclass(frozen=True, slots=True)
class ScorecardReport:
    """Full scorecard plus funnel for one completed run artifact."""

    metrics: CoreMetrics
    extended: ExtendedMetrics
    scorecard: tuple[ScorecardItem, ...]
    funnel: OpportunityFunnel

    def apply_to_result_document(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """Return a new result.v1 dict with scorecard + funnel (engine untouched)."""
        enriched = dict(document)
        metrics = dict(enriched.get("metrics") or {})
        metrics.update(
            {
                "trade_count": self.metrics.trade_count,
                "net_pnl": self.metrics.net_pnl,
                "net_r": self.metrics.net_r,
                "win_rate": self.metrics.win_rate,
                "profit_factor": self.metrics.profit_factor,
                "expectancy_r": self.metrics.expectancy_r,
                "max_drawdown_pnl": self.metrics.max_drawdown_pnl,
                "max_drawdown_r": self.metrics.max_drawdown_r,
                "payoff_ratio": self.metrics.payoff_ratio,
                "max_losing_streak": self.metrics.max_losing_streak,
                "dd_duration_trades": self.metrics.dd_duration_trades,
                "calmar_r": self.metrics.calmar_r,
                "param_count": self.metrics.param_count,
                "trades_per_param": self.metrics.trades_per_param,
                "rule_count": self.metrics.rule_count,
                "skew": self.extended.skew,
                "kurtosis": self.extended.kurtosis,
                "tail_ratio": self.extended.tail_ratio,
                "var95_r": self.extended.var95_r,
                "cvar95_r": self.extended.cvar95_r,
                "psr": self.extended.psr,
                "sharpe_per_trade": self.extended.sharpe_per_trade,
                "profit_concentration": {
                    "top5_removed_net_r": self.extended.top5_removed_net_r,
                    "top10_removed_net_r": self.extended.top10_removed_net_r,
                },
                "cost_scenarios": dict(self.extended.cost_scenarios),
                "period_cuts": {
                    "by_year": dict(self.extended.period_cuts_year),
                    "by_month": dict(self.extended.period_cuts_month),
                },
            }
        )
        enriched["metrics"] = metrics
        enriched["scorecard"] = [item.to_dict() for item in self.scorecard]
        enriched["funnel"] = self.funnel.to_dict()
        return enriched


def count_strategy_parameters(spec: StrategySpec | None = None) -> int:
    """Count tunable StrategySpec parameters for dimension 1 / 7.

    Counts leaf numeric/bool/enum settings on the P1 StrategySpec tree (not nested
    container wrappers).  Default P1 shape is fixed and deterministic.
    """
    resolved = spec or StrategySpec()
    count = 0
    for section_name in ("timeframes", "regime", "direction", "entry", "risk"):
        section = getattr(resolved, section_name)
        for field_name in type(section).model_fields:
            value = getattr(section, field_name)
            if isinstance(value, (int, float, bool, str)):
                count += 1
            elif isinstance(value, tuple):
                count += 1  # one parameter: the enabled set
    count += 1  # universe_session
    return count


def count_strategy_rules(spec: StrategySpec | None = None) -> int:
    """Count hard P1 gate/rules for dimension 7 (simplicity).

    Fixed structural rules of the Trend MVP (not free parameters).
    """
    del spec  # P1 rule surface is fixed; reserved for future strategy.v1 counting.
    # daily require_trend, congestion_no_trade, layer_consistency hard,
    # first-pullback-only, closed-bar, day-end clear, stop/target OCO.
    return 7


def compute_core_metrics(
    trades: Sequence[TradeOutcome],
    *,
    param_count: int,
    rule_count: int | None = None,
) -> CoreMetrics:
    """Compute dimension 1/2/3/7 metrics from completed trade outcomes only."""
    if param_count <= 0:
        msg = "param_count must be positive"
        raise ValueError(msg)
    rules = count_strategy_rules() if rule_count is None else rule_count
    if rules <= 0:
        msg = "rule_count must be positive"
        raise ValueError(msg)

    r_values = tuple(trade.net_r for trade in trades)
    pnl_values = tuple(trade.net_pnl for trade in trades)
    wins_r = tuple(value for value in r_values if value > 0)
    losses_r = tuple(value for value in r_values if value < 0)
    wins_pnl = tuple(value for value in pnl_values if value > 0)
    losses_pnl = tuple(value for value in pnl_values if value < 0)

    max_dd_pnl, dd_duration = _max_drawdown_and_duration(pnl_values)
    net_r = sum(r_values)
    max_dd_r = _max_drawdown_r(r_values)
    calmar = None
    if max_dd_r is not None and max_dd_r > 0 and isfinite(net_r):
        calmar = net_r / max_dd_r

    payoff = None
    if wins_r and losses_r:
        avg_win = sum(wins_r) / len(wins_r)
        avg_loss = abs(sum(losses_r) / len(losses_r))
        if avg_loss > 0:
            payoff = avg_win / avg_loss

    profit_factor = None
    if losses_pnl:
        profit_factor = sum(wins_pnl) / abs(sum(losses_pnl))
    elif wins_pnl and not losses_pnl:
        profit_factor = None  # undefined infinite PF — leave null, scorecard notes wins-only

    return CoreMetrics(
        trade_count=len(trades),
        param_count=param_count,
        trades_per_param=(len(trades) / param_count) if trades else None,
        win_rate=(len(wins_r) / len(trades)) if trades else None,
        expectancy_r=(sum(r_values) / len(r_values)) if r_values else None,
        payoff_ratio=payoff,
        profit_factor=profit_factor,
        max_losing_streak=_max_losing_streak(pnl_values),
        max_drawdown_pnl=max_dd_pnl,
        max_drawdown_r=max_dd_r,
        dd_duration_trades=dd_duration,
        calmar_r=calmar,
        rule_count=rules,
        net_r=net_r,
        net_pnl=sum(pnl_values),
    )


def compute_extended_metrics(trades: Sequence[TradeOutcome]) -> ExtendedMetrics:
    """Compute 4-2 metrics: Top-N / PSR / distribution tails / cost / period cuts."""
    r_values = tuple(trade.net_r for trade in trades)
    top5 = _top_n_removed_net_r(r_values, 5)
    top10 = _top_n_removed_net_r(r_values, 10)
    skew = _sample_skewness(r_values) if len(r_values) >= _MIN_TRADES_FOR_DISTRIBUTION else None
    kurtosis = (
        _sample_kurtosis_pearson(r_values)
        if len(r_values) >= _MIN_TRADES_FOR_DISTRIBUTION
        else None
    )
    sharpe = _sharpe_per_trade(r_values)
    psr = None
    if (
        len(r_values) >= _MIN_TRADES_FOR_PSR
        and sharpe is not None
        and skew is not None
        and kurtosis is not None
    ):
        psr = probabilistic_sharpe_ratio(
            sharpe_observed=sharpe,
            n=len(r_values),
            skewness=skew,
            kurtosis=kurtosis,
            sharpe_benchmark=_PSR_BASELINE_SHARPE,
        )
    var95, cvar95 = _var_cvar_95(r_values)
    tail = _tail_ratio(r_values)
    return ExtendedMetrics(
        top5_removed_net_r=top5,
        top10_removed_net_r=top10,
        psr=psr,
        sharpe_per_trade=sharpe,
        skew=skew,
        kurtosis=kurtosis,
        tail_ratio=tail,
        var95_r=var95,
        cvar95_r=cvar95,
        cost_scenarios=compute_cost_scenarios(trades),
        period_cuts_year=_period_cuts(trades, grain="year"),
        period_cuts_month=_period_cuts(trades, grain="month"),
    )


def probabilistic_sharpe_ratio(
    *,
    sharpe_observed: float,
    n: int,
    skewness: float,
    kurtosis: float,
    sharpe_benchmark: float = 0.0,
) -> float | None:
    """Closed-form PSR (Bailey & López de Prado): P(true SR > benchmark).

    Uses observed per-trade Sharpe, sample size, skewness, and Pearson kurtosis
    (normal = 3).  Returns None when the variance term is non-positive / non-finite.
    """
    if n < 2 or not all(
        isfinite(value) for value in (sharpe_observed, skewness, kurtosis, sharpe_benchmark)
    ):
        return None
    # σ̂(SR)² = (1 − γ3·SR + (γ4−1)/4 · SR²) / (n−1)
    variance_term = (
        1.0 - skewness * sharpe_observed + ((kurtosis - 1.0) / 4.0) * (sharpe_observed**2)
    ) / (n - 1)
    if variance_term <= 0 or not isfinite(variance_term):
        return None
    se = math.sqrt(variance_term)
    if se <= 0:
        return None
    z_stat = (sharpe_observed - sharpe_benchmark) / se
    return _standard_normal_cdf(z_stat)


def compute_cost_scenarios(
    trades: Sequence[TradeOutcome],
    multipliers: Sequence[float] = _COST_MULTIPLIERS,
) -> dict[str, dict[str, float | None]]:
    """Reprice net PnL/R at cost multipliers without re-running the engine.

    Friction per trade = commission + estimated slippage dollars.
    ``net_at_m = net_pnl - (m - 1) * friction`` so m=1 reproduces baseline.
    """
    scenarios: dict[str, dict[str, float | None]] = {}
    for multiplier in multipliers:
        key = f"x{multiplier:g}"
        if not trades:
            scenarios[key] = {
                "multiplier": multiplier,
                "net_pnl": 0.0,
                "net_r": 0.0,
                "expectancy_r": None,
                "trade_count": 0,
            }
            continue
        net_pnls: list[float] = []
        net_rs: list[float] = []
        for trade in trades:
            friction = trade.total_commission + trade.slippage_dollars
            net_pnl_m = trade.net_pnl - (multiplier - 1.0) * friction
            net_pnls.append(net_pnl_m)
            if trade.risk_dollars is not None and trade.risk_dollars > 0:
                net_rs.append(net_pnl_m / trade.risk_dollars)
            elif trade.net_pnl != 0:
                # Fallback: scale R by PnL ratio when risk is unknown.
                net_rs.append(trade.net_r * (net_pnl_m / trade.net_pnl))
            else:
                net_rs.append(trade.net_r)
        scenarios[key] = {
            "multiplier": multiplier,
            "net_pnl": sum(net_pnls),
            "net_r": sum(net_rs),
            "expectancy_r": (sum(net_rs) / len(net_rs)) if net_rs else None,
            "trade_count": len(trades),
        }
    return scenarios


def build_core_scorecard(metrics: CoreMetrics) -> tuple[ScorecardItem, ...]:
    """Map core metrics to dimensions 1, 2, 3, 7 with pass/warn/insufficient_sample."""
    if metrics.trade_count == 0:
        empty_detail = {"trade_count": 0, "reason": "no_completed_trades"}
        return (
            ScorecardItem("1_樣本量", "insufficient_sample", empty_detail),
            ScorecardItem("2_期望值結構", "insufficient_sample", empty_detail),
            ScorecardItem("3_風險形狀", "insufficient_sample", empty_detail),
            ScorecardItem(
                "7_簡潔度",
                "pass",
                {
                    "param_count": metrics.param_count,
                    "rule_count": metrics.rule_count,
                    "note": "simplicity is independent of trade sample size",
                },
            ),
        )

    dim1_status: ScoreStatus = "pass"
    if (
        metrics.trade_count < _MIN_TRADES_WARN
        or (metrics.trades_per_param or 0.0) < _MIN_TRADES_PER_PARAM_WARN
    ):
        dim1_status = "warn"

    dim2_status: ScoreStatus = "pass"
    if metrics.expectancy_r is not None and metrics.expectancy_r <= 0:
        dim2_status = "warn"
    if metrics.profit_factor is not None and metrics.profit_factor < 1.0:
        dim2_status = "warn"

    dim3_status: ScoreStatus = "pass"
    if metrics.calmar_r is not None and metrics.calmar_r < 0:
        dim3_status = "warn"

    return (
        ScorecardItem(
            "1_樣本量",
            dim1_status,
            {
                "trades": metrics.trade_count,
                "param_count": metrics.param_count,
                "per_param_ratio": metrics.trades_per_param,
                "warn_trades_below": _MIN_TRADES_WARN,
                "warn_ratio_below": _MIN_TRADES_PER_PARAM_WARN,
            },
        ),
        ScorecardItem(
            "2_期望值結構",
            dim2_status,
            {
                "expectancy_r": metrics.expectancy_r,
                "win_rate": metrics.win_rate,
                "payoff_ratio": metrics.payoff_ratio,
                "profit_factor": metrics.profit_factor,
                "max_losing_streak": metrics.max_losing_streak,
                "net_r": metrics.net_r,
            },
        ),
        ScorecardItem(
            "3_風險形狀",
            dim3_status,
            {
                "max_drawdown_pnl": metrics.max_drawdown_pnl,
                "max_drawdown_r": metrics.max_drawdown_r,
                "dd_duration_trades": metrics.dd_duration_trades,
                "calmar_r": metrics.calmar_r,
            },
        ),
        ScorecardItem(
            "7_簡潔度",
            "pass",
            {
                "param_count": metrics.param_count,
                "rule_count": metrics.rule_count,
            },
        ),
    )


def build_full_scorecard(
    metrics: CoreMetrics,
    extended: ExtendedMetrics,
) -> tuple[ScorecardItem, ...]:
    """Fill all P1 scorecard rows + P2 placeholders (matrix §6 thresholds)."""
    empty_detail: dict[str, object] = {"trade_count": 0, "reason": "no_completed_trades"}
    zero = metrics.trade_count == 0

    # --- dim 1 ---
    if zero:
        dim1 = ScorecardItem("1_樣本量", "insufficient_sample", empty_detail)
    else:
        dim1_status: ScoreStatus = "pass"
        if (
            metrics.trade_count < _MIN_TRADES_WARN
            or (metrics.trades_per_param or 0.0) < _MIN_TRADES_PER_PARAM_WARN
        ):
            dim1_status = "warn"
        dim1 = ScorecardItem(
            "1_樣本量",
            dim1_status,
            {
                "trades": metrics.trade_count,
                "param_count": metrics.param_count,
                "per_param_ratio": metrics.trades_per_param,
                "warn_trades_below": _MIN_TRADES_WARN,
                "warn_ratio_below": _MIN_TRADES_PER_PARAM_WARN,
            },
        )

    # --- dim 2 ---
    if zero:
        dim2 = ScorecardItem("2_期望值結構", "insufficient_sample", empty_detail)
    else:
        dim2_status: ScoreStatus = "pass"
        if metrics.expectancy_r is not None and metrics.expectancy_r <= 0:
            dim2_status = "warn"
        if metrics.profit_factor is not None and metrics.profit_factor < 1.0:
            dim2_status = "warn"
        dim2 = ScorecardItem(
            "2_期望值結構",
            dim2_status,
            {
                "expectancy_r": metrics.expectancy_r,
                "win_rate": metrics.win_rate,
                "payoff_ratio": metrics.payoff_ratio,
                "profit_factor": metrics.profit_factor,
                "max_losing_streak": metrics.max_losing_streak,
                "net_r": metrics.net_r,
            },
        )

    # --- dim 2b luck ---
    if zero or metrics.trade_count < _MIN_TRADES_FOR_TOP5:
        dim2b = ScorecardItem(
            "2b_好運依賴",
            "insufficient_sample",
            {
                "trade_count": metrics.trade_count,
                "min_trades_for_top5": _MIN_TRADES_FOR_TOP5,
                "reason": "need_at_least_top5_trades",
            },
        )
    else:
        dim2b_status: ScoreStatus = "pass"
        # Warn if removing top winners flips or zeros net edge; or PSR says SR>0 unlikely.
        if extended.top5_removed_net_r is not None and extended.top5_removed_net_r <= 0:
            dim2b_status = "warn"
        if (
            metrics.trade_count >= _MIN_TRADES_FOR_TOP10
            and extended.top10_removed_net_r is not None
            and extended.top10_removed_net_r <= 0
        ):
            dim2b_status = "warn"
        if extended.psr is not None and extended.psr < 0.5:
            dim2b_status = "warn"
        dim2b = ScorecardItem(
            "2b_好運依賴",
            dim2b_status,
            {
                "top5_removed_net_r": extended.top5_removed_net_r,
                "top10_removed_net_r": extended.top10_removed_net_r,
                "psr": extended.psr,
                "psr_benchmark_sharpe": _PSR_BASELINE_SHARPE,
                "sharpe_per_trade": extended.sharpe_per_trade,
                "top_n": list(_TOP_N_LEVELS),
            },
        )

    # --- dim 3 ---
    if zero:
        dim3 = ScorecardItem("3_風險形狀", "insufficient_sample", empty_detail)
    else:
        dim3_status: ScoreStatus = "pass"
        if metrics.calmar_r is not None and metrics.calmar_r < 0:
            dim3_status = "warn"
        dim3 = ScorecardItem(
            "3_風險形狀",
            dim3_status,
            {
                "max_drawdown_pnl": metrics.max_drawdown_pnl,
                "max_drawdown_r": metrics.max_drawdown_r,
                "dd_duration_trades": metrics.dd_duration_trades,
                "calmar_r": metrics.calmar_r,
            },
        )

    # --- dim 3b tail ---
    if zero or metrics.trade_count < _MIN_TRADES_FOR_DISTRIBUTION:
        dim3b = ScorecardItem(
            "3b_長尾",
            "insufficient_sample",
            {
                "trade_count": metrics.trade_count,
                "min_trades": _MIN_TRADES_FOR_DISTRIBUTION,
            },
        )
    else:
        dim3b_status: ScoreStatus = "pass"
        # Extreme left tail relative to right: tail_ratio < 1 means left heavier.
        if extended.tail_ratio is not None and extended.tail_ratio < 0.5:
            dim3b_status = "warn"
        if extended.cvar95_r is not None and extended.cvar95_r <= -3.0:
            dim3b_status = "warn"
        dim3b = ScorecardItem(
            "3b_長尾",
            dim3b_status,
            {
                "skew": extended.skew,
                "kurtosis": extended.kurtosis,
                "tail_ratio": extended.tail_ratio,
                "var95_r": extended.var95_r,
                "cvar95_r": extended.cvar95_r,
                "confidence": _VAR_CONFIDENCE,
            },
        )

    # --- dim 4 cost ---
    if zero:
        dim4 = ScorecardItem("4_成本敏感度", "insufficient_sample", empty_detail)
    else:
        scenarios = extended.cost_scenarios
        base = scenarios.get("x1") or scenarios.get("x1.0") or {}
        high = scenarios.get("x2") or scenarios.get("x2.0") or {}
        base_r = base.get("net_r")
        high_r = high.get("net_r")
        dim4_status: ScoreStatus = "pass"
        if (
            isinstance(base_r, (int, float))
            and isinstance(high_r, (int, float))
            and base_r > 0
            and high_r <= 0
        ):
            dim4_status = "warn"
        if isinstance(high_r, (int, float)) and high_r < 0 and metrics.net_r <= 0:
            dim4_status = "warn"
        dim4 = ScorecardItem(
            "4_成本敏感度",
            dim4_status,
            {
                "scenarios": dict(scenarios),
                "multipliers": list(_COST_MULTIPLIERS),
                "method": "net_pnl - (m-1)*(commission+slippage_dollars)",
            },
        )

    # --- dim 5 / 5b P2 ---
    dim5 = ScorecardItem(
        "5_參數平原",
        "not_available_p2",
        {"reason": "walk_forward_mc_param_scan_are_p2", "phase": "P2"},
    )
    dim5b = ScorecardItem(
        "5b_機率評估",
        "not_available_p2",
        {"reason": "dsr_mc_bootstrap_are_p2", "phase": "P2"},
    )

    # --- dim 6 period cuts ---
    if zero:
        dim6 = ScorecardItem("6_跨時期", "insufficient_sample", empty_detail)
    else:
        years = extended.period_cuts_year
        year_nets: list[float] = []
        for block in years.values():
            net_r_value = block.get("net_r")
            if isinstance(net_r_value, (int, float)):
                year_nets.append(float(net_r_value))
        dim6_status: ScoreStatus = "pass"
        if len(years) < 2:
            # Single period still reports month cuts; warn that cross-year not testable.
            dim6_status = "warn"
        if year_nets and any(value < 0 for value in year_nets) and metrics.net_r > 0:
            dim6_status = "warn"
        dim6 = ScorecardItem(
            "6_跨時期",
            dim6_status,
            {
                "by_year": dict(years),
                "by_month": dict(extended.period_cuts_month),
                "cut_method": "year_primary_month_secondary",
                "multi_market_note": (
                    "single_run period cuts only; multi-market batch compare is separate"
                ),
            },
        )

    # --- dim 7 ---
    dim7 = ScorecardItem(
        "7_簡潔度",
        "pass",
        {
            "param_count": metrics.param_count,
            "rule_count": metrics.rule_count,
            **({"note": "simplicity is independent of trade sample size"} if zero else {}),
        },
    )

    # --- dim 8 human ---
    dim8 = ScorecardItem(
        "8_經濟原理",
        "insufficient_sample",
        {
            "reason": "owner_manual_review_required",
            "note": "rationale text + owner promotion self-check; not auto-scored in P1",
        },
    )

    return (dim1, dim2, dim2b, dim3, dim3b, dim4, dim5, dim5b, dim6, dim7, dim8)


def compute_opportunity_funnel(
    events: Sequence[Mapping[str, Any]],
    *,
    fills: int,
) -> OpportunityFunnel:
    """Build day-level + evaluation-level funnel counts from an events sidecar.

    Definitions (deterministic, event-only; [062] dual units):
    - ``daily_trend_days``: distinct trading dates (from event timestamps) whose
      active daily regime is ``trend``, stepped from ``daily_regime_changed`` only.
    - Each ``signal_rejected`` / ``signal_created`` is one signal evaluation.
    - ``evaluations_passing_daily_gate`` = evaluations not rejected for daily_regime_*.
    - ``evaluations_passing_mid_gate`` = not rejected for daily_regime_* or mid_*.
    - Signals created = ``signal_created`` count.
    - Fills = completed trade count supplied by the caller (trades sidecar).
    """
    if fills < 0:
        msg = "fills must not be negative"
        raise ValueError(msg)

    reject_reasons: Counter[str] = Counter()
    signal_created = 0
    signal_rejected = 0
    daily_blocked = 0
    mid_blocked = 0

    for event in events:
        event_type = str(event.get("event_type") or "")
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        reason = str(details.get("reason") or "") if details else ""

        if event_type == "signal_created":
            signal_created += 1
            continue
        if event_type != "signal_rejected":
            continue
        signal_rejected += 1
        if reason:
            reject_reasons[reason] += 1
        if reason.startswith("daily_regime"):
            daily_blocked += 1
        elif reason.startswith("mid_"):
            mid_blocked += 1

    evaluations = signal_created + signal_rejected
    evaluations_daily = evaluations - daily_blocked
    evaluations_mid = evaluations - daily_blocked - mid_blocked
    daily_trend_days, trend_notes = _count_daily_trend_days(events)

    if evaluations == 0 and fills == 0 and daily_trend_days == 0:
        return OpportunityFunnel(
            status="insufficient_sample",
            daily_trend_days=0,
            evaluations_passing_daily_gate=0,
            evaluations_passing_mid_gate=0,
            signals_created=0,
            fills=0,
            reject_reasons={},
            notes="no regime changes, no signal evaluations, and no fills",
        )

    notes = (
        "funnel.v1 dual units: daily_trend_days is day-level from daily_regime_changed; "
        "evaluations_passing_* are evaluation-level signal counts. "
        f"{trend_notes}"
    )
    return OpportunityFunnel(
        status="ok",
        daily_trend_days=daily_trend_days,
        evaluations_passing_daily_gate=evaluations_daily,
        evaluations_passing_mid_gate=evaluations_mid,
        signals_created=signal_created,
        fills=fills,
        reject_reasons=dict(sorted(reject_reasons.items())),
        notes=notes,
    )


def compute_scorecard_report(
    *,
    trades: Sequence[TradeOutcome],
    events: Sequence[Mapping[str, Any]],
    param_count: int | None = None,
    rule_count: int | None = None,
) -> ScorecardReport:
    """Compute full scorecard + funnel for one run (read-only)."""
    params = count_strategy_parameters() if param_count is None else param_count
    metrics = compute_core_metrics(trades, param_count=params, rule_count=rule_count)
    extended = compute_extended_metrics(trades)
    funnel = compute_opportunity_funnel(events, fills=metrics.trade_count)
    return ScorecardReport(
        metrics=metrics,
        extended=extended,
        scorecard=build_full_scorecard(metrics, extended),
        funnel=funnel,
    )


def load_events_from_sidecar(path: Path) -> tuple[dict[str, Any], ...]:
    """Load events list from events.v1 sidecar."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events")
    if not isinstance(events, list):
        msg = f"events sidecar missing events list: {path}"
        raise ValueError(msg)
    return tuple(event for event in events if isinstance(event, dict))


def enrich_result_file(
    result_path: Path,
    *,
    param_count: int | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Read result.v1 + sidecars, compute scorecard/funnel, optionally rewrite main file."""
    # Route every new complete artifact through the same main-marker integrity
    # gate as the HTTP/catalog consumers.  Without this, a missing or partial
    # evidence sidecar could be silently consumed by the derivative scorecard
    # exporter even though ResultsCatalog correctly rejects it.
    from futures_research.api.results_catalog import ResultsCatalog

    parent = result_path.parent
    document = ResultsCatalog(results_root=parent).get_result(result_path.stem)
    trades_ref = str(document.get("trades_ref") or f"trades/{result_path.stem}.json")
    events_ref = str(document.get("events_ref") or f"events/{result_path.stem}.json")
    trades_path = parent / trades_ref
    events_path = parent / events_ref

    trades = _trade_outcomes_for_result(document, trades_path)
    events = load_events_from_sidecar(events_path)
    report = compute_scorecard_report(
        trades=trades,
        events=events,
        param_count=param_count,
    )
    enriched = report.apply_to_result_document(document)
    if write:
        _atomic_write_json(result_path, enriched)
    return enriched


def _trade_outcomes_for_result(
    document: Mapping[str, Any],
    trades_path: Path,
) -> tuple[TradeOutcome, ...]:
    """Build trade outcomes using sidecar prices and contract specs when needed."""
    payload = json.loads(trades_path.read_text(encoding="utf-8"))
    trades_raw = payload.get("trades")
    if not isinstance(trades_raw, list):
        msg = f"trades sidecar missing trades list: {trades_path}"
        raise ValueError(msg)
    if not trades_raw:
        return ()

    point_value, tick_size = _contract_economics_from_result(document)
    outcomes: list[TradeOutcome] = []
    for item in trades_raw:
        if not isinstance(item, dict):
            continue
        net_pnl = float(item["net_pnl"])
        gross_pnl = float(item["gross_pnl"]) if item.get("gross_pnl") is not None else None
        total_commission = float(item.get("total_commission") or 0.0)
        entry = float(item["entry_price"])
        stop = float(item["stop_price"])
        quantity = int(item.get("quantity") or 1)
        entry_slip = int(item.get("entry_slippage_ticks") or 0)
        exit_slip = int(item.get("exit_slippage_ticks") or 0)
        risk_dollars = abs(entry - stop) * point_value * quantity
        if risk_dollars <= 0:
            msg = f"trade {item.get('trade_id')} has non-positive initial risk"
            raise ValueError(msg)
        net_r = net_pnl / risk_dollars
        exit_ts = datetime.fromisoformat(str(item["exit_timestamp"]).replace("Z", "+00:00"))
        slippage_dollars = (entry_slip + exit_slip) * tick_size * point_value * quantity
        outcomes.append(
            TradeOutcome(
                net_pnl=net_pnl,
                net_r=net_r,
                exit_timestamp=exit_ts,
                gross_pnl=gross_pnl,
                total_commission=total_commission,
                slippage_dollars=slippage_dollars,
                risk_dollars=risk_dollars,
            )
        )
    return tuple(outcomes)


def _contract_economics_from_result(document: Mapping[str, Any]) -> tuple[float, float]:
    """Resolve (point_value, tick_size) from result manifest contract_id."""
    run_obj = document.get("run")
    run: dict[str, Any] = run_obj if isinstance(run_obj, dict) else {}
    manifest_obj = run.get("manifest")
    manifest: dict[str, Any] = manifest_obj if isinstance(manifest_obj, dict) else {}
    contract_id = str(manifest.get("contract_id") or "")
    if not contract_id:
        msg = "result.v1 missing run.manifest.contract_id for scorecard R conversion"
        raise ValueError(msg)
    from futures_research.data.contracts import ContractRegistry
    from futures_research.paths import PROJECT_ROOT

    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    for contract in registry.contracts.values():
        if contract.contract_id == contract_id:
            return float(contract.point_value), float(contract.tick_size)
    msg = f"unknown contract_id in result manifest: {contract_id}"
    raise ValueError(msg)


def _point_value_from_result(document: Mapping[str, Any]) -> float:
    """Backward-compatible helper: point_value only."""
    point_value, _tick = _contract_economics_from_result(document)
    return point_value


def _count_daily_trend_days(
    events: Sequence[Mapping[str, Any]],
) -> tuple[int, str]:
    """Count trading days with active regime == trend from regime change records only."""
    changes: list[tuple[date, str]] = []
    event_dates: set[date] = set()

    for event in events:
        event_type = str(event.get("event_type") or "")
        for key in ("timestamp", "ts_init"):
            raw = event.get(key)
            if raw:
                parsed = _parse_date(str(raw))
                if parsed is not None:
                    event_dates.add(parsed)
        if event_type != "daily_regime_changed":
            continue
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        source = details.get("source_close") if details else None
        effective_raw = str(source or event.get("timestamp") or event.get("ts_init") or "")
        effective = _parse_date(effective_raw)
        to_state = str(event.get("to_state") or "")
        if effective is not None and to_state:
            changes.append((effective, to_state))
            event_dates.add(effective)

    if not changes:
        return 0, "regime_change_events=0"

    changes.sort(key=lambda item: item[0])
    trend_days = 0
    by_state: Counter[str] = Counter()
    for day in sorted(event_dates):
        state: str | None = None
        for effective, to_state in changes:
            if effective <= day:
                state = to_state
            else:
                break
        if state is None:
            continue
        by_state[state] += 1
        if state == "trend":
            trend_days += 1

    state_summary = ",".join(f"{name}={count}" for name, count in sorted(by_state.items()))
    return trend_days, (
        f"regime_change_events={len(changes)}; "
        f"event_dates_scored={sum(by_state.values())}; "
        f"regime_day_counts={{{state_summary}}}"
    )


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _top_n_removed_net_r(r_values: Sequence[float], n: int) -> float | None:
    """Net R after removing the top-N winning trades (by R). None if too few trades."""
    if len(r_values) < n:
        return None
    # Remove the n largest R values (winners first; zeros/losses only if not enough winners).
    ordered = sorted(r_values, reverse=True)
    remaining = ordered[n:]
    return float(sum(remaining))


def _sharpe_per_trade(r_values: Sequence[float]) -> float | None:
    """Non-annualized Sharpe = mean(R) / sample_std(R)."""
    if len(r_values) < 2:
        return None
    mean = sum(r_values) / len(r_values)
    variance = sum((value - mean) ** 2 for value in r_values) / (len(r_values) - 1)
    if variance <= 0 or not isfinite(variance):
        return None
    std = math.sqrt(variance)
    if std <= 0:
        return None
    return mean / std


def _sample_skewness(values: Sequence[float]) -> float | None:
    """Biased (moment) sample skewness γ3 = m3 / s^3 with s = sqrt(m2)."""
    n = len(values)
    if n < 3:
        return None
    mean = sum(values) / n
    m2 = sum((value - mean) ** 2 for value in values) / n
    m3 = sum((value - mean) ** 3 for value in values) / n
    if m2 <= 0 or not isfinite(m2):
        return None
    return float(m3 / (m2**1.5))


def _sample_kurtosis_pearson(values: Sequence[float]) -> float | None:
    """Pearson kurtosis γ4 = m4 / m2^2 (normal distribution → 3)."""
    n = len(values)
    if n < 3:
        return None
    mean = sum(values) / n
    m2 = sum((value - mean) ** 2 for value in values) / n
    m4 = sum((value - mean) ** 4 for value in values) / n
    if m2 <= 0 or not isfinite(m2):
        return None
    return float(m4 / (m2**2))


def _empirical_quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation empirical quantile for sorted ascending values."""
    if not sorted_values:
        msg = "quantile requires values"
        raise ValueError(msg)
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _var_cvar_95(r_values: Sequence[float]) -> tuple[float | None, float | None]:
    """VaR95 and CVaR95 on trade R (left tail). VaR = q05; CVaR = mean of R <= VaR."""
    if len(r_values) < _MIN_TRADES_FOR_DISTRIBUTION:
        return None, None
    ordered = sorted(r_values)
    var95 = _empirical_quantile(ordered, 1.0 - _VAR_CONFIDENCE)
    tail = [value for value in ordered if value <= var95]
    if not tail:
        tail = [ordered[0]]
    cvar95 = sum(tail) / len(tail)
    return var95, cvar95


def _tail_ratio(r_values: Sequence[float]) -> float | None:
    """Right-tail / left-tail magnitude: |q95| / |q05| on trade R."""
    if len(r_values) < _MIN_TRADES_FOR_DISTRIBUTION:
        return None
    ordered = sorted(r_values)
    q05 = _empirical_quantile(ordered, 0.05)
    q95 = _empirical_quantile(ordered, 0.95)
    left = abs(q05)
    if left <= 0 or not isfinite(left):
        return None
    return abs(q95) / left


def _period_cuts(
    trades: Sequence[TradeOutcome],
    *,
    grain: Literal["year", "month"],
) -> dict[str, dict[str, float | int | None]]:
    """Summarize net R / trade count by calendar year or YYYY-MM."""
    buckets: dict[str, list[TradeOutcome]] = {}
    for trade in trades:
        ts = trade.exit_timestamp
        key = f"{ts.year:04d}" if grain == "year" else f"{ts.year:04d}-{ts.month:02d}"
        buckets.setdefault(key, []).append(trade)
    summary: dict[str, dict[str, float | int | None]] = {}
    for key in sorted(buckets):
        group = buckets[key]
        r_values = [trade.net_r for trade in group]
        summary[key] = {
            "trade_count": len(group),
            "net_r": sum(r_values),
            "net_pnl": sum(trade.net_pnl for trade in group),
            "expectancy_r": (sum(r_values) / len(r_values)) if r_values else None,
        }
    return summary


def _max_losing_streak(pnl_values: Sequence[float]) -> int:
    streak = 0
    best = 0
    for value in pnl_values:
        if value < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _max_drawdown_and_duration(pnl_values: Sequence[float]) -> tuple[float, int]:
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    peak_index = -1
    max_duration = 0
    for index, value in enumerate(pnl_values):
        cumulative += value
        if cumulative >= peak:
            peak = cumulative
            peak_index = index
        drawdown = peak - cumulative
        if drawdown > max_dd:
            max_dd = drawdown
            max_duration = index - peak_index
    return max_dd, max_duration


def _max_drawdown_r(r_values: Sequence[float]) -> float | None:
    if not r_values:
        return None
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in r_values:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd


def _standard_normal_cdf(x: float) -> float:
    """Φ(x) via error function (stdlib only)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    import os
    from uuid import uuid4

    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
