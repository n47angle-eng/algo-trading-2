"""Golden-number tests for WO-004 scorecard (4-1 core + 4-2 extended)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from futures_research.backtest.scorecard import (
    TradeOutcome,
    build_core_scorecard,
    build_full_scorecard,
    compute_core_metrics,
    compute_cost_scenarios,
    compute_extended_metrics,
    compute_opportunity_funnel,
    compute_scorecard_report,
    count_strategy_parameters,
    probabilistic_sharpe_ratio,
)


def _trade(
    net_r: float,
    *,
    net_pnl: float | None = None,
    day: int = 1,
    month: int = 5,
    year: int = 2026,
    total_commission: float = 0.0,
    slippage_dollars: float = 0.0,
    risk_dollars: float | None = None,
) -> TradeOutcome:
    pnl = net_r if net_pnl is None else net_pnl
    risk = risk_dollars if risk_dollars is not None else (1.0 if net_r == 0 else abs(pnl / net_r))
    return TradeOutcome(
        net_pnl=pnl,
        net_r=net_r,
        exit_timestamp=datetime(year, month, day, 21, 0, tzinfo=UTC),
        total_commission=total_commission,
        slippage_dollars=slippage_dollars,
        risk_dollars=risk,
    )


def test_core_metrics_golden_five_trade_book() -> None:
    """3 wins of +1R and 2 losses of -1R → fixed expectancy / PF / streaks."""
    trades = (
        _trade(1.0, day=1),
        _trade(-1.0, day=2),
        _trade(1.0, day=3),
        _trade(1.0, day=4),
        _trade(-1.0, day=5),
    )
    metrics = compute_core_metrics(trades, param_count=10)

    assert metrics.trade_count == 5
    assert metrics.net_r == 1.0
    assert metrics.win_rate == 0.6
    assert metrics.expectancy_r == 0.2
    assert metrics.payoff_ratio == 1.0
    assert metrics.profit_factor == 1.5
    assert metrics.max_losing_streak == 1
    assert metrics.max_drawdown_r == 1.0
    assert metrics.trades_per_param == 0.5
    assert metrics.calmar_r == 1.0  # net_r / max_dd_r

    scorecard = {item.dim: item for item in build_core_scorecard(metrics)}
    assert scorecard["1_樣本量"].status == "warn"  # <100 trades and <30:1
    assert scorecard["2_期望值結構"].status == "pass"
    assert scorecard["3_風險形狀"].status == "pass"
    assert scorecard["7_簡潔度"].status == "pass"


def test_zero_trades_scorecard_is_insufficient_sample() -> None:
    """Empty books must not raise; dims 1–3 mark insufficient_sample."""
    metrics = compute_core_metrics((), param_count=12)
    scorecard = build_core_scorecard(metrics)
    statuses = {item.dim: item.status for item in scorecard}
    assert statuses["1_樣本量"] == "insufficient_sample"
    assert statuses["2_期望值結構"] == "insufficient_sample"
    assert statuses["3_風險形狀"] == "insufficient_sample"
    assert statuses["7_簡潔度"] == "pass"
    assert metrics.win_rate is None
    assert metrics.expectancy_r is None
    assert metrics.profit_factor is None


def test_max_losing_streak_and_drawdown_duration_golden() -> None:
    """LLL then W: streak 3; drawdown recovery length tracked in trade units."""
    trades = (
        _trade(-1.0, day=1),
        _trade(-1.0, day=2),
        _trade(-1.0, day=3),
        _trade(2.0, day=4),
    )
    metrics = compute_core_metrics(trades, param_count=5)
    assert metrics.max_losing_streak == 3
    assert metrics.max_drawdown_r == 3.0
    assert metrics.dd_duration_trades == 3


def test_opportunity_funnel_evaluation_units_renamed() -> None:
    """Funnel evaluation layers use unit-explicit field names ([062])."""
    events = [
        {"event_type": "signal_rejected", "details": {"reason": "daily_regime_range"}},
        {"event_type": "signal_rejected", "details": {"reason": "daily_regime_range"}},
        {"event_type": "signal_rejected", "details": {"reason": "mid_pullback_not_qualified"}},
        {"event_type": "signal_rejected", "details": {"reason": "mid_entry_direction_mismatch"}},
        {"event_type": "signal_created", "details": {"signal_kind": "inside"}},
        {"event_type": "pullback_touched", "details": {"reason": "ema_18_touched"}},
    ]
    funnel = compute_opportunity_funnel(events, fills=0)
    assert funnel.status == "ok"
    assert funnel.evaluations_passing_daily_gate == 3  # 5 - 2
    assert funnel.evaluations_passing_mid_gate == 1  # 5 - 2 - 2
    assert funnel.signals_created == 1
    assert funnel.fills == 0
    assert funnel.daily_trend_days == 0  # no regime change events
    assert funnel.reject_reasons["daily_regime_range"] == 2
    assert funnel.reject_reasons["mid_pullback_not_qualified"] == 1
    payload = funnel.to_dict()
    assert "daily_pass" not in payload
    assert "mid_pass" not in payload
    assert "units" in payload
    assert "day-level" in str(payload["units"]["daily_trend_days"])


def test_daily_trend_days_from_regime_change_events_not_evaluations() -> None:
    """daily_trend_days steps from daily_regime_changed only (never from eval counts)."""
    events = [
        {
            "event_type": "daily_regime_changed",
            "to_state": "range",
            "timestamp": "2026-04-01T22:00:00Z",
            "details": {"source_close": "2026-04-01T21:00:00+00:00"},
        },
        {
            "event_type": "signal_rejected",
            "timestamp": "2026-04-02T15:00:00Z",
            "details": {"reason": "daily_regime_range"},
        },
        {
            "event_type": "daily_regime_changed",
            "to_state": "trend",
            "timestamp": "2026-04-10T22:00:00Z",
            "details": {"source_close": "2026-04-10T21:00:00+00:00"},
        },
        {
            "event_type": "signal_created",
            "timestamp": "2026-04-11T15:00:00Z",
            "details": {},
        },
        {
            "event_type": "signal_rejected",
            "timestamp": "2026-04-12T15:00:00Z",
            "details": {"reason": "mid_pullback_not_qualified"},
        },
        {
            "event_type": "daily_regime_changed",
            "to_state": "range",
            "timestamp": "2026-04-20T22:00:00Z",
            "details": {"source_close": "2026-04-20T21:00:00+00:00"},
        },
        {
            "event_type": "signal_rejected",
            "timestamp": "2026-04-21T15:00:00Z",
            "details": {"reason": "daily_regime_range"},
        },
    ]
    funnel = compute_opportunity_funnel(events, fills=1)
    # Event dates: 4/1,4/2,4/10,4/11,4/12,4/20,4/21
    # trend active for dates >= 4/10 and < 4/20 → 4/10, 4/11, 4/12 = 3
    assert funnel.daily_trend_days == 3
    # evaluations: 1 created + 3 rejected = 4; daily blocks = 2 → pass daily = 2
    assert funnel.evaluations_passing_daily_gate == 2
    assert funnel.signals_created == 1
    assert funnel.fills == 1


def test_empty_events_funnel_is_insufficient_sample() -> None:
    funnel = compute_opportunity_funnel((), fills=0)
    assert funnel.status == "insufficient_sample"
    assert funnel.evaluations_passing_daily_gate == 0
    assert funnel.daily_trend_days == 0
    assert funnel.fills == 0


def test_top_n_removed_net_r_golden() -> None:
    """Remove top-5 winners: remaining net_r is exact."""
    # R: 5,4,3,2,1,-1,-1  net=13; top5 remove 5+4+3+2+1 → remaining -2
    trades = tuple(
        _trade(r, day=i + 1) for i, r in enumerate([5.0, 4.0, 3.0, 2.0, 1.0, -1.0, -1.0])
    )
    extended = compute_extended_metrics(trades)
    assert extended.top5_removed_net_r == -2.0
    assert extended.top10_removed_net_r is None  # only 7 trades
    assert sum(t.net_r for t in trades) == 13.0


def test_psr_closed_form_golden_numbers() -> None:
    """PSR closed form matches hand-checked values for a fixed 5-trade book."""
    # R = [1,1,1,-1,-1]; mean=0.2; sample std=√1.2; SR=0.2/√1.2
    r_values = (1.0, 1.0, 1.0, -1.0, -1.0)
    n = 5
    mean = 0.2
    std = math.sqrt(1.2)
    sharpe = mean / std
    # moment skew/kurt on population m2/m3/m4
    m2 = sum((x - mean) ** 2 for x in r_values) / n
    m3 = sum((x - mean) ** 3 for x in r_values) / n
    m4 = sum((x - mean) ** 4 for x in r_values) / n
    skew = m3 / (m2**1.5)
    kurtosis = m4 / (m2**2)
    psr = probabilistic_sharpe_ratio(
        sharpe_observed=sharpe,
        n=n,
        skewness=skew,
        kurtosis=kurtosis,
        sharpe_benchmark=0.0,
    )
    assert psr is not None
    # Hand-derived: variance_term = (1 - skew*SR + (kurt-1)/4 * SR^2)/(n-1)
    variance_term = (1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe**2) / (n - 1)
    z = sharpe / math.sqrt(variance_term)
    expected = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    assert abs(psr - expected) < 1e-12
    assert 0.5 < psr < 0.8  # modest positive edge, not near 1

    trades = tuple(_trade(r, day=i + 1) for i, r in enumerate(r_values))
    extended = compute_extended_metrics(trades)
    assert extended.sharpe_per_trade is not None
    assert abs(extended.sharpe_per_trade - sharpe) < 1e-12
    assert extended.psr is not None
    assert abs(extended.psr - expected) < 1e-12


def test_distribution_var_cvar_tail_ratio_golden() -> None:
    """VaR95 / CVaR95 / tail_ratio on a fixed sorted R book."""
    # 10 trades: enough for q05/q95 interpolation
    r_values = [-3.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 4.0]
    trades = tuple(_trade(r, day=i + 1) for i, r in enumerate(r_values))
    extended = compute_extended_metrics(trades)
    assert extended.var95_r is not None
    assert extended.cvar95_r is not None
    assert extended.tail_ratio is not None
    # q05 of 10 pts (index 0.05*9=0.45) between -3 and -2
    assert -3.0 <= extended.var95_r <= -2.0
    # CVaR is mean of left tail ≤ VaR → at least as bad as VaR
    assert extended.cvar95_r <= extended.var95_r + 1e-12
    # Right tail larger than left magnitude → tail_ratio > 1
    assert extended.tail_ratio > 1.0
    assert extended.skew is not None
    assert extended.kurtosis is not None
    assert extended.kurtosis > 0


def test_cost_reprice_multipliers_golden() -> None:
    """×1 reproduces baseline; ×2 subtracts one full friction unit from net."""
    trades = (
        _trade(
            1.0,
            net_pnl=100.0,
            day=1,
            total_commission=10.0,
            slippage_dollars=10.0,
            risk_dollars=100.0,
        ),
        _trade(
            -0.5,
            net_pnl=-50.0,
            day=2,
            total_commission=10.0,
            slippage_dollars=10.0,
            risk_dollars=100.0,
        ),
    )
    # friction each = 20; total baseline net = 50
    scenarios = compute_cost_scenarios(trades)
    assert scenarios["x1"]["net_pnl"] == 50.0
    assert scenarios["x1"]["net_r"] == 0.5
    # ×1.5: subtract 0.5 * 20 * 2 trades = 20 → net_pnl 30
    assert scenarios["x1.5"]["net_pnl"] == 30.0
    assert scenarios["x1.5"]["net_r"] == pytest.approx(0.3)
    # ×2: subtract 1.0 * 40 = 40 → net_pnl 10
    assert scenarios["x2"]["net_pnl"] == 10.0
    assert scenarios["x2"]["net_r"] == pytest.approx(0.1)


def test_period_cuts_by_year_and_month() -> None:
    trades = (
        _trade(1.0, day=10, month=3, year=2025),
        _trade(-0.5, day=20, month=3, year=2025),
        _trade(2.0, day=5, month=1, year=2026),
    )
    extended = compute_extended_metrics(trades)
    assert extended.period_cuts_year["2025"]["trade_count"] == 2
    assert extended.period_cuts_year["2025"]["net_r"] == 0.5
    assert extended.period_cuts_year["2026"]["net_r"] == 2.0
    assert extended.period_cuts_month["2025-03"]["trade_count"] == 2
    assert extended.period_cuts_month["2026-01"]["net_r"] == 2.0


def test_full_scorecard_statuses_include_p2_placeholders() -> None:
    trades = tuple(
        _trade(r, day=i + 1, total_commission=5.0, slippage_dollars=5.0, risk_dollars=100.0)
        for i, r in enumerate([1.0, 1.0, 1.0, 1.0, 1.0, -1.0, -1.0, 0.5, 0.5, 2.0])
    )
    metrics = compute_core_metrics(trades, param_count=5)
    extended = compute_extended_metrics(trades)
    items = {item.dim: item for item in build_full_scorecard(metrics, extended)}
    assert set(items) >= {
        "1_樣本量",
        "2_期望值結構",
        "2b_好運依賴",
        "3_風險形狀",
        "3b_長尾",
        "4_成本敏感度",
        "5_參數平原",
        "5b_機率評估",
        "6_跨時期",
        "7_簡潔度",
        "8_經濟原理",
    }
    assert items["5_參數平原"].status == "not_available_p2"
    assert items["5b_機率評估"].status == "not_available_p2"
    assert items["2b_好運依賴"].status in {"pass", "warn"}
    assert items["3b_長尾"].status in {"pass", "warn"}
    assert items["4_成本敏感度"].status in {"pass", "warn"}
    assert items["8_經濟原理"].status == "insufficient_sample"


def test_zero_trades_full_scorecard_marks_trade_dims_insufficient() -> None:
    metrics = compute_core_metrics((), param_count=8)
    extended = compute_extended_metrics(())
    items = {item.dim: item for item in build_full_scorecard(metrics, extended)}
    for dim in (
        "1_樣本量",
        "2_期望值結構",
        "2b_好運依賴",
        "3_風險形狀",
        "3b_長尾",
        "4_成本敏感度",
        "6_跨時期",
    ):
        assert items[dim].status == "insufficient_sample", dim
    assert items["5_參數平原"].status == "not_available_p2"
    assert items["7_簡潔度"].status == "pass"


def test_scorecard_report_enriches_result_document_shape() -> None:
    trades = (_trade(1.0), _trade(-1.0), _trade(2.0))
    events = [
        {
            "event_type": "daily_regime_changed",
            "to_state": "trend",
            "timestamp": "2026-05-01T22:00:00Z",
            "details": {"source_close": "2026-05-01T21:00:00+00:00"},
        },
        {
            "event_type": "signal_created",
            "timestamp": "2026-05-02T15:00:00Z",
            "details": {},
        },
        {
            "event_type": "signal_rejected",
            "timestamp": "2026-05-03T15:00:00Z",
            "details": {"reason": "daily_regime_range"},
        },
    ]
    report = compute_scorecard_report(trades=trades, events=events, param_count=8)
    doc = report.apply_to_result_document(
        {
            "schema": "result.v1",
            "metrics": {"trade_count": 0},
            "scorecard": [],
        }
    )
    assert doc["funnel"]["schema"] == "funnel.v1"
    assert doc["funnel"]["signals_created"] == 1
    assert doc["funnel"]["fills"] == 3
    assert doc["funnel"]["daily_trend_days"] >= 1
    assert "evaluations_passing_daily_gate" in doc["funnel"]
    assert "units" in doc["funnel"]
    assert len(doc["scorecard"]) == 11
    assert doc["metrics"]["expectancy_r"] == report.metrics.expectancy_r
    assert doc["metrics"]["param_count"] == 8
    assert "psr" in doc["metrics"]
    assert "profit_concentration" in doc["metrics"]
    assert "cost_scenarios" in doc["metrics"]
    assert "period_cuts" in doc["metrics"]


def test_luck_dependent_book_warns_on_2b() -> None:
    """Edge entirely in top-5 winners → top5_removed_net_r <= 0 → warn."""
    trades = tuple(
        _trade(r, day=i + 1)
        for i, r in enumerate([10.0, 8.0, 6.0, 4.0, 2.0, -1.0, -1.0, -1.0, -1.0, -1.0])
    )
    metrics = compute_core_metrics(trades, param_count=3)
    extended = compute_extended_metrics(trades)
    assert extended.top5_removed_net_r is not None
    assert extended.top5_removed_net_r < 0
    items = {item.dim: item for item in build_full_scorecard(metrics, extended)}
    assert items["2b_好運依賴"].status == "warn"


def test_default_strategy_param_count_is_positive_and_stable() -> None:
    first = count_strategy_parameters()
    second = count_strategy_parameters()
    assert first == second
    assert first >= 10
