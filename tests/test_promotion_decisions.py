"""Batch 4 regression tests for immutable PromotionDecision APIs and storage."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from futures_research.api.main import app
from futures_research.api.promotion_decisions import (
    PromotionDecisionIntegrityError,
    PromotionDecisionStore,
)
from futures_research.api.results_catalog import ResultsCatalog

_STRICT_MAIN_CORRUPTIONS = (
    "scorecard_empty_item",
    "scorecard_missing_dim",
    "scorecard_missing_status",
    "scorecard_missing_detail",
    "scorecard_extra_key",
    "scorecard_blank_dim",
    "scorecard_trimmed_dim",
    "scorecard_duplicate_dim",
    "scorecard_unknown_status",
    "scorecard_non_object_detail",
    "scorecard_non_finite_detail",
    "metrics_missing",
    "metrics_partial",
    "metrics_extra",
    "engine_invalid",
    "engine_missing",
    "engine_extra",
    "nonstandard_nan",
    "nonstandard_infinity",
    "nonstandard_negative_infinity",
    "manifest_contract_leading",
    "manifest_contract_trailing",
    "manifest_session_leading",
    "manifest_session_trailing",
    "engine_nautilus_blank",
    "engine_nautilus_whitespace",
    "engine_nautilus_trimmed",
    "engine_app_blank",
    "engine_app_whitespace",
    "engine_app_trimmed",
)

_ENRICHED_METRICS_KEYS = {
    "trade_count",
    "gross_pnl",
    "net_pnl",
    "net_r",
    "win_rate",
    "profit_factor",
    "expectancy_r",
    "max_drawdown_pnl",
    "max_drawdown_r",
    "payoff_ratio",
    "max_losing_streak",
    "dd_duration_trades",
    "calmar_r",
    "param_count",
    "trades_per_param",
    "rule_count",
    "skew",
    "kurtosis",
    "tail_ratio",
    "var95_r",
    "cvar95_r",
    "psr",
    "sharpe_per_trade",
    "profit_concentration",
    "cost_scenarios",
    "period_cuts",
}
_ARBITRARY_PRECISION_JSON_INTEGER = 10**1000
_LARGE_INTEGER_LOCATIONS = (
    "top_level_nullable",
    "profit_concentration",
    "non_x1_cost_scenario",
    "period_bucket",
)
_ENRICHED_CORRUPTION_CASES = (
    ("enriched_unknown", False),
    ("partial_enrichment", False),
    ("nonnegative_integer_bool", False),
    ("nonnegative_integer_wrong_type", False),
    ("nonnegative_integer_negative", False),
    ("nonnegative_integer_exceeds_trades", False),
    ("positive_integer_bool", False),
    ("positive_integer_wrong_type", False),
    ("positive_integer_zero", False),
    ("nullable_number_bool", False),
    ("nullable_number_wrong_type", False),
    ("nullable_number_non_finite", False),
    ("zero_trades_per_param_nonnull", False),
    ("nonzero_trades_per_param_null", True),
    ("profit_wrong_type", False),
    ("profit_missing_key", False),
    ("profit_extra_key", False),
    ("profit_non_finite", False),
    ("scenarios_wrong_type", False),
    ("scenarios_missing_key", False),
    ("scenarios_extra_key", False),
    ("scenario_wrong_type", False),
    ("scenario_missing_member", False),
    ("scenario_extra_member", False),
    ("scenario_multiplier_mismatch", False),
    ("scenario_trade_count_bool", False),
    ("scenario_trade_count_mismatch", False),
    ("scenario_x1_net_pnl_mismatch", False),
    ("scenario_x1_net_r_mismatch", False),
    ("scenario_x1_expectancy_mismatch", True),
    ("scenario_number_non_finite", False),
    ("zero_scenario_expectancy_nonnull", False),
    ("nonzero_scenario_expectancy_null", True),
    ("period_wrong_type", False),
    ("period_missing_key", False),
    ("period_extra_key", False),
    ("period_group_wrong_type", True),
    ("zero_period_nonempty", False),
    ("year_key_invalid", True),
    ("month_key_invalid", True),
    ("period_bucket_wrong_type", True),
    ("period_bucket_missing_member", True),
    ("period_bucket_extra_member", True),
    ("period_bucket_count_bool", True),
    ("period_bucket_count_zero", True),
    ("period_bucket_non_finite", True),
    ("year_count_sum_mismatch", True),
    ("month_count_sum_mismatch", True),
)


def _condition_fact(source_sequence: int) -> dict[str, object]:
    return {
        "condition_id": "entry_gate",
        "layer_id": "entry",
        "observed_at": "2026-07-22T22:00:00Z",
        "status": "passed",
        "actual": True,
        "operator": "eq",
        "required": True,
        "unit": "bool",
        "source_sequences": [source_sequence],
    }


def _trade_record() -> dict[str, object]:
    return {
        "trade_id": "trade-00001",
        "decision_evidence": {
            "trade_id": "trade-00001",
            "ordinal": 1,
            "entry": {
                "signal_kind": "inside",
                "signal_timestamp": "2026-07-22T22:00:00Z",
                "entry_timestamp": "2026-07-22T22:01:00Z",
                "condition_facts": [_condition_fact(1)],
                "entry_reference": 20_000.0,
                "fill_price": 20_000.25,
                "source_event_sequences": [1],
            },
            "stop": {
                "reference_type": "signal_low",
                "reference_price": 19_990.0,
                "offset_ticks": 1,
                "final_stop_price": 19_989.75,
                "condition_facts": [_condition_fact(1)],
                "source_event_sequences": [1],
            },
            "exit": {
                "reason": "target",
                "timestamp": "2026-07-22T22:06:00Z",
                "price": 20_010.0,
                "candidates": [
                    {
                        "candidate_id": "target",
                        "triggered": True,
                        "reference_price": 20_010.0,
                        "observed_price": 20_010.25,
                    }
                ],
                "selected_candidate_id": "target",
                "resolution_code": None,
                "source_event_sequences": [2],
            },
            "conservative_assumptions": [
                {
                    "code": "same_minute_stop_first",
                    "applied": False,
                    "effects": ["no same-minute stop/target collision"],
                    "source_sequences": [2],
                }
            ],
        },
    }


def _manifest(
    run_id: str,
    *,
    strategy_id: str,
    strategy_hash: str,
) -> dict[str, object]:
    return {
        "schema": "run_manifest.v1",
        "run_id": run_id,
        "strategy_version": strategy_id,
        "contract_id": "NQ-202609-CME",
        "session_name": "eth",
        "range_start": "2026-07-01T00:00:00Z",
        "range_end": "2026-07-24T00:00:00Z",
        "initial_capital": 100_000.0,
        "quantity": 1,
        "costs": {
            "commission_per_side": 2.8,
            "slippage_ticks": {
                "breakout_entry": 1,
                "stop_exit": 2,
                "target_exit": 0,
                "day_end_exit": 1,
            },
            "target_requires_through": False,
        },
        "fill_model": "conservative",
        "data_fingerprint": {
            "schema": "canonical-bars.v1",
            "algorithm": "sha256",
            "digest": "d" * 64,
            "bar_count": 0,
            "source_partitions": [],
            "quality_report_ids": [],
        },
        "strategy_binding": {
            "schema": "strategy_binding.v1",
            "source": "strategy_file",
            "strategy_id": strategy_id,
            "strategy_name": "Locked strategy",
            "content_sha256": strategy_hash,
            "universe_contracts": ["NQ-202609-CME"],
            "universe_authorized": True,
            "overrides": [],
        },
        "quality_gate_mode": "enforce",
        "validation_run": False,
        "excluded_trading_dates": [],
        "created_at": "2026-07-25T01:00:00Z",
    }


def _enriched_metrics(*, with_trade: bool) -> dict[str, object]:
    trade_count = 1 if with_trade else 0
    net_pnl = 250.0 if with_trade else 0.0
    net_r = 1.0 if with_trade else 0.0
    expectancy_r = 1.0 if with_trade else None
    period_bucket = {
        "trade_count": 1,
        "net_r": 1.0,
        "net_pnl": 250.0,
        "expectancy_r": 1.0,
    }
    metrics: dict[str, object] = {
        "trade_count": trade_count,
        "gross_pnl": net_pnl,
        "net_pnl": net_pnl,
        "net_r": net_r,
        "win_rate": 1.0 if with_trade else None,
        "profit_factor": None,
        "expectancy_r": expectancy_r,
        "max_drawdown_pnl": 0.0,
        "max_drawdown_r": 0.0 if with_trade else None,
        "payoff_ratio": None,
        "max_losing_streak": 0,
        "dd_duration_trades": 0,
        "calmar_r": None,
        "param_count": 21,
        "trades_per_param": (1.0 / 21.0) if with_trade else None,
        "rule_count": 7,
        "skew": None,
        "kurtosis": None,
        "tail_ratio": None,
        "var95_r": 1.0 if with_trade else None,
        "cvar95_r": 1.0 if with_trade else None,
        "psr": None,
        "sharpe_per_trade": None,
        "profit_concentration": {
            "top5_removed_net_r": None,
            "top10_removed_net_r": None,
        },
        "cost_scenarios": {
            "x1": {
                "multiplier": 1.0,
                "net_pnl": net_pnl,
                "net_r": net_r,
                "expectancy_r": expectancy_r,
                "trade_count": trade_count,
            },
            "x1.5": {
                "multiplier": 1.5,
                "net_pnl": 245.0 if with_trade else 0.0,
                "net_r": 0.98 if with_trade else 0.0,
                "expectancy_r": 0.98 if with_trade else None,
                "trade_count": trade_count,
            },
            "x2": {
                "multiplier": 2.0,
                "net_pnl": 240.0 if with_trade else 0.0,
                "net_r": 0.96 if with_trade else 0.0,
                "expectancy_r": 0.96 if with_trade else None,
                "trade_count": trade_count,
            },
        },
        "period_cuts": {
            "by_year": {"2026": dict(period_bucket)} if with_trade else {},
            "by_month": {"2026-07": dict(period_bucket)} if with_trade else {},
        },
    }
    assert set(metrics) == _ENRICHED_METRICS_KEYS
    return metrics


def _set_large_enriched_integer(
    metrics: dict[str, object],
    location: str,
) -> int:
    value = (
        _ARBITRARY_PRECISION_JSON_INTEGER
        if location in {"top_level_nullable", "non_x1_cost_scenario"}
        else -_ARBITRARY_PRECISION_JSON_INTEGER
    )
    if location == "top_level_nullable":
        metrics["skew"] = value
    elif location == "profit_concentration":
        concentration = metrics["profit_concentration"]
        assert isinstance(concentration, dict)
        concentration["top5_removed_net_r"] = value
    elif location == "non_x1_cost_scenario":
        scenarios = metrics["cost_scenarios"]
        assert isinstance(scenarios, dict)
        scenario = scenarios["x1.5"]
        assert isinstance(scenario, dict)
        scenario["net_pnl"] = value
    elif location == "period_bucket":
        period_cuts = metrics["period_cuts"]
        assert isinstance(period_cuts, dict)
        by_year = period_cuts["by_year"]
        assert isinstance(by_year, dict)
        bucket = by_year["2026"]
        assert isinstance(bucket, dict)
        bucket["net_r"] = value
    else:  # pragma: no cover - the closed parametrization makes this unreachable
        raise AssertionError(f"unsupported large-integer location: {location}")
    return value


def _get_large_enriched_integer(
    metrics: dict[str, object],
    location: str,
) -> int:
    if location == "top_level_nullable":
        value = metrics["skew"]
    elif location == "profit_concentration":
        concentration = metrics["profit_concentration"]
        assert isinstance(concentration, dict)
        value = concentration["top5_removed_net_r"]
    elif location == "non_x1_cost_scenario":
        scenarios = metrics["cost_scenarios"]
        assert isinstance(scenarios, dict)
        scenario = scenarios["x1.5"]
        assert isinstance(scenario, dict)
        value = scenario["net_pnl"]
    elif location == "period_bucket":
        period_cuts = metrics["period_cuts"]
        assert isinstance(period_cuts, dict)
        by_year = period_cuts["by_year"]
        assert isinstance(by_year, dict)
        bucket = by_year["2026"]
        assert isinstance(bucket, dict)
        value = bucket["net_r"]
    else:  # pragma: no cover - the closed parametrization makes this unreachable
        raise AssertionError(f"unsupported large-integer location: {location}")
    assert type(value) is int
    return value


def _write_decision_result(
    root: Path,
    *,
    run_id: str,
    strategy_id: str = "strategy-0042",
    strategy_hash: str = "a" * 64,
    with_trade: bool = False,
    enriched: bool = False,
) -> Path:
    (root / "trades").mkdir(parents=True, exist_ok=True)
    (root / "equity").mkdir(parents=True, exist_ok=True)
    (root / "events").mkdir(parents=True, exist_ok=True)
    scorecard = [
        {"dim": "1_sample", "status": "pass", "detail": {"trades": 120}},
        {"dim": "7_simplicity", "status": "warn", "detail": {"params": 21}},
    ]
    trades = [_trade_record()] if with_trade else []
    events = (
        [
            {"sequence": 1, "event_type": "signal_created"},
            {"sequence": 2, "event_type": "position_closed"},
        ]
        if with_trade
        else []
    )
    main = {
        "schema": "result.v1",
        "run": {
            "run_id": run_id,
            "strategy_version": strategy_id,
            "manifest": _manifest(
                run_id,
                strategy_id=strategy_id,
                strategy_hash=strategy_hash,
            ),
            "engine": {"nautilus": "1.230.0", "app": "0.1.0"},
        },
        "metrics": (
            _enriched_metrics(with_trade=with_trade)
            if enriched
            else {
                "trade_count": len(trades),
                "gross_pnl": 250.0 if with_trade else 0.0,
                "net_pnl": 250.0 if with_trade else 0.0,
                "net_r": 1.0 if with_trade else 0.0,
                "win_rate": 1.0 if with_trade else None,
                "profit_factor": None,
                "expectancy_r": 1.0 if with_trade else None,
                "max_drawdown_pnl": 0.0,
            }
        ),
        "scorecard": scorecard,
        "trades_ref": f"trades/{run_id}.json",
        "equity_curve_ref": f"equity/{run_id}.json",
        "events_ref": f"events/{run_id}.json",
        "decision_evidence_complete": True,
    }
    main_path = root / f"{run_id}.json"
    main_path.write_text(
        json.dumps(main, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "trades" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "trades.v1",
                "run_id": run_id,
                "trades": trades,
                "decision_evidence_complete": True,
            }
        ),
        encoding="utf-8",
    )
    (root / "equity" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "equity_curve.v1",
                "run_id": run_id,
                "points": (
                    [
                        {
                            "timestamp": "2026-07-22T22:06:00Z",
                            "equity": 100_250.0,
                            "cumulative_net_pnl": 250.0,
                        }
                    ]
                    if with_trade
                    else []
                ),
            }
        ),
        encoding="utf-8",
    )
    (root / "events" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "events.v1",
                "run_id": run_id,
                "events": events,
                "rejection_evidence": [],
                "evidence_summary": {
                    "availability": "available",
                    "complete": True,
                    "evaluation_count": 1 if with_trade else 0,
                    "rejection_count": 0,
                    "layer_reached_counts": {},
                    "blocking_condition_counts": {},
                    "deepest_layer": None,
                    "trade_count": len(trades),
                },
                "evidence_complete": True,
            }
        ),
        encoding="utf-8",
    )
    return main_path


def _store(path: Path) -> PromotionDecisionStore:
    counter = iter(range(1, 100))
    origin = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)
    return PromotionDecisionStore(
        path,
        clock=lambda: origin + timedelta(seconds=next(counter)),
        decision_id_factory=lambda: f"promotion-{next(counter):04d}",
    )


@asynccontextmanager
async def _client(
    results_root: Path,
    store: PromotionDecisionStore,
) -> AsyncIterator[httpx.AsyncClient]:
    app.state.results_catalog = ResultsCatalog(results_root)
    app.state.promotion_decision_store = store
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        delattr(app.state, "results_catalog")
        delattr(app.state, "promotion_decision_store")


def _request(
    request_id: str,
    decision: str,
    reason: str,
) -> dict[str, object]:
    return {
        "schema": "promotion_decision_request.v1",
        "request_id": request_id,
        "decision": decision,
        "reason": reason,
    }


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _corrupt_enriched_metrics(metrics: dict[str, object], corruption: str) -> None:
    concentration = metrics["profit_concentration"]
    scenarios = metrics["cost_scenarios"]
    cuts = metrics["period_cuts"]
    assert isinstance(concentration, dict)
    assert isinstance(scenarios, dict)
    assert isinstance(cuts, dict)
    if corruption == "enriched_unknown":
        metrics["forged_metric"] = 1
    elif corruption == "partial_enrichment":
        del metrics["period_cuts"]
    elif corruption == "nonnegative_integer_bool":
        metrics["max_losing_streak"] = True
    elif corruption == "nonnegative_integer_wrong_type":
        metrics["dd_duration_trades"] = 0.5
    elif corruption == "nonnegative_integer_negative":
        metrics["max_losing_streak"] = -1
    elif corruption == "nonnegative_integer_exceeds_trades":
        metrics["dd_duration_trades"] = int(metrics["trade_count"]) + 1
    elif corruption == "positive_integer_bool":
        metrics["param_count"] = True
    elif corruption == "positive_integer_wrong_type":
        metrics["rule_count"] = 7.5
    elif corruption == "positive_integer_zero":
        metrics["param_count"] = 0
    elif corruption == "nullable_number_bool":
        metrics["skew"] = False
    elif corruption == "nullable_number_wrong_type":
        metrics["kurtosis"] = "0"
    elif corruption == "nullable_number_non_finite":
        metrics["tail_ratio"] = float("nan")
    elif corruption == "zero_trades_per_param_nonnull":
        metrics["trades_per_param"] = 0.0
    elif corruption == "nonzero_trades_per_param_null":
        metrics["trades_per_param"] = None
    elif corruption == "profit_wrong_type":
        metrics["profit_concentration"] = []
    elif corruption == "profit_missing_key":
        del concentration["top5_removed_net_r"]
    elif corruption == "profit_extra_key":
        concentration["forged"] = 0.0
    elif corruption == "profit_non_finite":
        concentration["top10_removed_net_r"] = float("inf")
    elif corruption == "scenarios_wrong_type":
        metrics["cost_scenarios"] = []
    elif corruption == "scenarios_missing_key":
        del scenarios["x2"]
    elif corruption == "scenarios_extra_key":
        scenarios["x3"] = dict(scenarios["x2"])
    elif corruption == "scenario_wrong_type":
        scenarios["x1.5"] = []
    else:
        scenario = scenarios["x1"]
        assert isinstance(scenario, dict)
        by_year = cuts["by_year"]
        by_month = cuts["by_month"]
        assert isinstance(by_year, dict)
        assert isinstance(by_month, dict)
        if corruption == "scenario_missing_member":
            del scenario["net_r"]
        elif corruption == "scenario_extra_member":
            scenario["forged"] = 0
        elif corruption == "scenario_multiplier_mismatch":
            scenario["multiplier"] = 1.01
        elif corruption == "scenario_trade_count_bool":
            scenario["trade_count"] = False
        elif corruption == "scenario_trade_count_mismatch":
            scenario["trade_count"] = int(metrics["trade_count"]) + 1
        elif corruption == "scenario_x1_net_pnl_mismatch":
            scenario["net_pnl"] = float(metrics["net_pnl"]) + 1.0
        elif corruption == "scenario_x1_net_r_mismatch":
            scenario["net_r"] = float(metrics["net_r"]) + 1.0
        elif corruption == "scenario_x1_expectancy_mismatch":
            scenario["expectancy_r"] = 2.0
        elif corruption == "scenario_number_non_finite":
            scenario["net_pnl"] = float("-inf")
        elif corruption == "zero_scenario_expectancy_nonnull":
            scenario["expectancy_r"] = 0.0
        elif corruption == "nonzero_scenario_expectancy_null":
            scenario["expectancy_r"] = None
        elif corruption == "period_wrong_type":
            metrics["period_cuts"] = []
        elif corruption == "period_missing_key":
            del cuts["by_month"]
        elif corruption == "period_extra_key":
            cuts["forged"] = {}
        elif corruption == "period_group_wrong_type":
            cuts["by_year"] = []
        elif corruption == "zero_period_nonempty":
            by_year["2026"] = {
                "trade_count": 1,
                "net_r": 0.0,
                "net_pnl": 0.0,
                "expectancy_r": 0.0,
            }
        elif corruption == "year_key_invalid":
            by_year["0000"] = by_year.pop("2026")
        elif corruption == "month_key_invalid":
            by_month["2026-13"] = by_month.pop("2026-07")
        else:
            buckets = by_year if corruption.startswith("period_bucket") else None
            if buckets is not None:
                bucket = buckets["2026"]
                assert isinstance(bucket, dict)
                if corruption == "period_bucket_wrong_type":
                    buckets["2026"] = []
                elif corruption == "period_bucket_missing_member":
                    del bucket["net_r"]
                elif corruption == "period_bucket_extra_member":
                    bucket["forged"] = 0
                elif corruption == "period_bucket_count_bool":
                    bucket["trade_count"] = True
                elif corruption == "period_bucket_count_zero":
                    bucket["trade_count"] = 0
                elif corruption == "period_bucket_non_finite":
                    bucket["net_pnl"] = float("nan")
                else:
                    raise AssertionError(corruption)
            elif corruption == "year_count_sum_mismatch":
                by_year["2026"]["trade_count"] = 2
            elif corruption == "month_count_sum_mismatch":
                by_month["2026-07"]["trade_count"] = 2
            else:
                raise AssertionError(corruption)


def _corrupt_strict_main(main: dict[str, object], corruption: str) -> None:
    scorecard = main["scorecard"]
    run = main["run"]
    metrics = main["metrics"]
    assert isinstance(scorecard, list)
    assert isinstance(run, dict)
    assert isinstance(metrics, dict)
    assert isinstance(scorecard[0], dict)
    if corruption == "scorecard_empty_item":
        main["scorecard"] = [{}]
    elif corruption == "scorecard_missing_dim":
        del scorecard[0]["dim"]
    elif corruption == "scorecard_missing_status":
        del scorecard[0]["status"]
    elif corruption == "scorecard_missing_detail":
        del scorecard[0]["detail"]
    elif corruption == "scorecard_extra_key":
        scorecard[0]["extra"] = "forged"
    elif corruption == "scorecard_blank_dim":
        scorecard[0]["dim"] = ""
    elif corruption == "scorecard_trimmed_dim":
        scorecard[0]["dim"] = " 1_sample"
    elif corruption == "scorecard_duplicate_dim":
        scorecard.append(dict(scorecard[0]))
    elif corruption == "scorecard_unknown_status":
        scorecard[0]["status"] = "approved"
    elif corruption == "scorecard_non_object_detail":
        scorecard[0]["detail"] = []
    elif corruption == "scorecard_non_finite_detail":
        scorecard[0]["detail"] = {"nested": [float("nan")]}
    elif corruption == "metrics_missing":
        del metrics["gross_pnl"]
    elif corruption == "metrics_partial":
        main["metrics"] = {"trade_count": 0}
    elif corruption == "metrics_extra":
        metrics["forged_metric"] = 1
    elif corruption == "nonstandard_nan":
        main["warnings"] = [{"risk": float("nan")}]
    elif corruption == "nonstandard_infinity":
        main["warnings"] = [{"risk": float("inf")}]
    elif corruption == "nonstandard_negative_infinity":
        main["warnings"] = [{"risk": float("-inf")}]
    elif corruption.startswith("manifest_"):
        manifest = run["manifest"]
        assert isinstance(manifest, dict)
        replacements = {
            "manifest_contract_leading": ("contract_id", " NQ-202609-CME"),
            "manifest_contract_trailing": ("contract_id", "NQ-202609-CME "),
            "manifest_session_leading": ("session_name", " eth"),
            "manifest_session_trailing": ("session_name", "eth "),
        }
        field, value = replacements[corruption]
        manifest[field] = value
    else:
        engine = run["engine"]
        assert isinstance(engine, dict)
        if corruption == "engine_invalid":
            engine["nautilus"] = True
        elif corruption == "engine_missing":
            del engine["app"]
        elif corruption == "engine_extra":
            engine["forged"] = "version"
        else:
            replacements = {
                "engine_nautilus_blank": ("nautilus", ""),
                "engine_nautilus_whitespace": ("nautilus", " "),
                "engine_nautilus_trimmed": ("nautilus", "1.230.0 "),
                "engine_app_blank": ("app", ""),
                "engine_app_whitespace": ("app", "\t"),
                "engine_app_trimmed": ("app", " 0.1.0"),
            }
            field, value = replacements[corruption]
            engine[field] = value


@pytest.mark.asyncio
async def test_all_three_decisions_append_server_truth_and_preserve_owner_reason(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-run-001"
    main_path = _write_decision_result(root, run_id=run_id)
    main = json.loads(main_path.read_text(encoding="utf-8"))
    expected_scorecard = main["scorecard"]
    expected_result_hash = sha256(main_path.read_bytes()).hexdigest()
    store = _store(tmp_path / "promotion.sqlite3")

    async with _client(root, store) as client:
        created = []
        for request_id, decision, reason in (
            ("req-use", "use", "  Owner keeps exact spacing.  "),
            ("req-return", "return", "Need a clearer entry definition."),
            ("req-abandon", "abandon", "This direction is not worth more time."),
        ):
            response = await client.post(
                f"/api/v1/runs/{run_id}/promotion-decisions",
                json=_request(request_id, decision, reason),
            )
            assert response.status_code == 200
            created.append(response.json())
        listed = await client.get(f"/api/v1/runs/{run_id}/promotion-decisions")

    assert [item["decision"] for item in created] == ["use", "return", "abandon"]
    assert created[0]["reason"] == "  Owner keeps exact spacing.  "
    for item in created:
        assert item["schema"] == "promotion_decision.v1"
        assert item["run_id"] == run_id
        assert item["strategy"] == {
            "strategy_id": "strategy-0042",
            "content_sha256": "a" * 64,
        }
        assert item["result_sha256"] == expected_result_hash
        assert item["scorecard_snapshot"] == expected_scorecard
        assert item["created_at"].endswith("Z")
    assert listed.status_code == 200
    assert listed.json()["count"] == 3
    assert [item["decision"] for item in listed.json()["decisions"]] == [
        "use",
        "return",
        "abandon",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_trade", [False, True], ids=["zero_trade", "nonzero"])
async def test_decision_accepts_exact_enriched_metrics_through_shared_gate(
    tmp_path: Path,
    with_trade: bool,
) -> None:
    root = tmp_path / "results"
    run_id = (
        "promotion-enriched-trade-001"
        if with_trade
        else "promotion-enriched-zero-001"
    )
    main_path = _write_decision_result(
        root,
        run_id=run_id,
        with_trade=with_trade,
        enriched=True,
    )
    main = json.loads(main_path.read_text(encoding="utf-8"))
    assert set(main["metrics"]) == _ENRICHED_METRICS_KEYS
    before = _tree_hashes(root)
    expected_result_hash = sha256(main_path.read_bytes()).hexdigest()
    database = tmp_path / "promotion.sqlite3"

    async with _client(root, _store(database)) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-enriched", "use", "Validated enriched result"),
        )

    assert response.status_code == 200
    assert response.json()["result_sha256"] == expected_result_hash
    assert response.json()["scorecard_snapshot"] == main["scorecard"]
    assert _tree_hashes(root) == before
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM promotion_decisions"
        ).fetchone() == (1,)


@pytest.mark.asyncio
@pytest.mark.parametrize("location", _LARGE_INTEGER_LOCATIONS)
async def test_decision_accepts_arbitrary_precision_integer_in_enriched_metrics(
    tmp_path: Path,
    location: str,
) -> None:
    root = tmp_path / "results"
    run_id = f"promotion-enriched-large-int-{location}"
    main_path = _write_decision_result(
        root,
        run_id=run_id,
        with_trade=True,
        enriched=True,
    )
    main = json.loads(main_path.read_text(encoding="utf-8"))
    metrics = main["metrics"]
    assert isinstance(metrics, dict)
    expected = _set_large_enriched_integer(metrics, location)
    main_path.write_text(
        json.dumps(main, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    persisted_main = json.loads(main_path.read_text(encoding="utf-8"))
    persisted_metrics = persisted_main["metrics"]
    assert isinstance(persisted_metrics, dict)
    assert _get_large_enriched_integer(persisted_metrics, location) == expected
    before = _tree_hashes(root)
    expected_result_hash = sha256(main_path.read_bytes()).hexdigest()
    database = tmp_path / "promotion.sqlite3"

    async with _client(root, _store(database)) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-enriched-large-int", "use", "Valid large integer"),
        )

    assert response.status_code == 200
    assert response.json()["result_sha256"] == expected_result_hash
    assert response.json()["scorecard_snapshot"] == main["scorecard"]
    assert _tree_hashes(root) == before
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM promotion_decisions"
        ).fetchone() == (1,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "with_trade"),
    _ENRICHED_CORRUPTION_CASES,
    ids=[case[0] for case in _ENRICHED_CORRUPTION_CASES],
)
async def test_decision_rejects_invalid_enriched_metrics_without_creating_store(
    tmp_path: Path,
    corruption: str,
    with_trade: bool,
) -> None:
    root = tmp_path / "results"
    run_id = f"promotion-enriched-invalid-{corruption}"
    main_path = _write_decision_result(
        root,
        run_id=run_id,
        with_trade=with_trade,
        enriched=True,
    )
    main = json.loads(main_path.read_text(encoding="utf-8"))
    metrics = main["metrics"]
    assert isinstance(metrics, dict)
    _corrupt_enriched_metrics(metrics, corruption)
    main_path.write_text(json.dumps(main), encoding="utf-8")
    before = _tree_hashes(root)
    database = tmp_path / "promotion.sqlite3"

    async with _client(root, _store(database)) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-invalid-enriched", "use", "Must not persist"),
        )

    assert response.status_code == 503
    assert not database.exists()
    assert _tree_hashes(root) == before


@pytest.mark.asyncio
async def test_invalid_enriched_metrics_preserve_existing_store_rows_and_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-enriched-existing-store-001"
    main_path = _write_decision_result(
        root,
        run_id=run_id,
        with_trade=True,
        enriched=True,
    )
    database = tmp_path / "promotion.sqlite3"
    store = _store(database)

    async with _client(root, store) as client:
        created = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-before-enriched-corruption", "return", "Keep this row"),
        )
        assert created.status_code == 200
        main = json.loads(main_path.read_text(encoding="utf-8"))
        metrics = main["metrics"]
        assert isinstance(metrics, dict)
        _corrupt_enriched_metrics(metrics, "year_count_sum_mismatch")
        main_path.write_text(json.dumps(main), encoding="utf-8")
        source_before = _tree_hashes(root)
        database_before = database.read_bytes()
        rejected = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-after-enriched-corruption", "use", "Must not append"),
        )

    assert rejected.status_code == 503
    assert database.read_bytes() == database_before
    assert _tree_hashes(root) == source_before
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM promotion_decisions"
        ).fetchone() == (1,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forged",
    [
        {"strategy": {"strategy_id": "strategy-forged"}},
        {"scorecard_snapshot": []},
        {"created_at": "2000-01-01T00:00:00Z"},
        {"result_sha256": "f" * 64},
        {"timestamp": "2000-01-01T00:00:00Z"},
    ],
)
async def test_client_cannot_forge_server_derived_fields(
    tmp_path: Path,
    forged: dict[str, object],
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-forge-001"
    _write_decision_result(root, run_id=run_id)
    database = tmp_path / "promotion.sqlite3"
    body = _request("req-forged", "use", "Owner reason")
    body.update(forged)

    async with _client(root, _store(database)) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=body,
        )

    assert response.status_code == 422
    assert not database.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["", " ", "\t\r\n"])
async def test_reason_is_required_and_whitespace_only_fails_before_store(
    tmp_path: Path,
    reason: str,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-reason-001"
    _write_decision_result(root, run_id=run_id)
    database = tmp_path / "promotion.sqlite3"

    async with _client(root, _store(database)) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-blank", "use", reason),
        )

    assert response.status_code == 422
    assert not database.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("schema", True),
        ("request_id", 1),
        ("decision", True),
        ("reason", False),
    ],
)
async def test_request_primitives_are_strict_and_fail_before_store(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-primitives-001"
    _write_decision_result(root, run_id=run_id)
    database = tmp_path / "promotion.sqlite3"
    body = _request("req-primitives", "use", "Strict primitives")
    body[field] = bad_value

    async with _client(root, _store(database)) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=body,
        )

    assert response.status_code == 422
    assert not database.exists()


@pytest.mark.asyncio
async def test_same_request_is_idempotent_and_changed_payload_conflicts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-idempotent-001"
    _write_decision_result(root, run_id=run_id)
    store = _store(tmp_path / "promotion.sqlite3")
    body = _request("req-stable", "use", "Exact intent")

    async with _client(root, store) as client:
        first = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=body,
        )
        replay = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=body,
        )
        conflict = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-stable", "return", "Different intent"),
        )
        listed = await client.get(f"/api/v1/runs/{run_id}/promotion-decisions")

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert listed.json()["count"] == 1


@pytest.mark.asyncio
async def test_concurrent_double_click_appends_exactly_one_record(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-race-001"
    _write_decision_result(root, run_id=run_id)
    store = PromotionDecisionStore(tmp_path / "promotion.sqlite3")
    body = _request("req-double-click", "use", "One Owner click intent")

    async with _client(root, store) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    f"/api/v1/runs/{run_id}/promotion-decisions",
                    json=body,
                )
                for _ in range(8)
            )
        )
        listed = await client.get(f"/api/v1/runs/{run_id}/promotion-decisions")

    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["decision_id"] for response in responses}) == 1
    assert listed.json()["count"] == 1


@pytest.mark.asyncio
async def test_scorecard_snapshot_is_deep_and_does_not_follow_later_main_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-snapshot-001"
    main_path = _write_decision_result(root, run_id=run_id)
    store = _store(tmp_path / "promotion.sqlite3")

    async with _client(root, store) as client:
        created = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-snapshot", "use", "Capture this scorecard"),
        )
        main = json.loads(main_path.read_text(encoding="utf-8"))
        main["scorecard"][0]["detail"]["trades"] = 999
        main_path.write_text(json.dumps(main), encoding="utf-8")
        listed = await client.get(f"/api/v1/runs/{run_id}/promotion-decisions")

    assert created.status_code == 200
    assert created.json()["scorecard_snapshot"][0]["detail"]["trades"] == 120
    assert listed.json()["decisions"][0]["scorecard_snapshot"][0]["detail"]["trades"] == 120


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ["missing_result", "bad_evidence", "missing_binding", "missing_scorecard"],
)
async def test_invalid_source_artifact_never_creates_decision_store(
    tmp_path: Path,
    corruption: str,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-invalid-source-001"
    main_path = _write_decision_result(root, run_id=run_id)
    if corruption == "missing_result":
        main_path.unlink()
    elif corruption == "bad_evidence":
        (root / "events" / f"{run_id}.json").write_text("{bad-json", encoding="utf-8")
    else:
        main = json.loads(main_path.read_text(encoding="utf-8"))
        if corruption == "missing_binding":
            del main["run"]["manifest"]["strategy_binding"]
        else:
            del main["scorecard"]
        main_path.write_text(json.dumps(main), encoding="utf-8")
    database = tmp_path / "promotion.sqlite3"

    async with _client(root, _store(database)) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-invalid-source", "use", "Must not persist"),
        )

    assert response.status_code == (404 if corruption == "missing_result" else 503)
    assert not database.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", _STRICT_MAIN_CORRUPTIONS)
async def test_decision_rejects_every_strict_result_main_corruption_without_writes(
    tmp_path: Path,
    corruption: str,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-strict-main-001"
    main_path = _write_decision_result(root, run_id=run_id)
    main = json.loads(main_path.read_text(encoding="utf-8"))
    _corrupt_strict_main(main, corruption)
    main_path.write_text(json.dumps(main), encoding="utf-8")
    before = _tree_hashes(root)
    database = tmp_path / "promotion.sqlite3"

    async with _client(root, _store(database)) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-strict-main", "use", "Malformed mains never persist"),
        )

    assert response.status_code == 503
    assert not database.exists()
    assert _tree_hashes(root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "scorecard_empty_item",
        "nonstandard_nan",
        "manifest_contract_leading",
        "manifest_session_trailing",
        "engine_nautilus_blank",
        "engine_app_trimmed",
    ],
)
async def test_malformed_main_does_not_append_to_an_existing_decision_store(
    tmp_path: Path,
    corruption: str,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-existing-store-001"
    main_path = _write_decision_result(root, run_id=run_id)
    database = tmp_path / "promotion.sqlite3"

    async with _client(root, _store(database)) as client:
        first = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-valid", "return", "Preserve this one record"),
        )
        assert first.status_code == 200
        main = json.loads(main_path.read_text(encoding="utf-8"))
        _corrupt_strict_main(main, corruption)
        main_path.write_text(json.dumps(main), encoding="utf-8")
        before = _tree_hashes(root)
        rejected = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request(
                f"req-malformed-{corruption}",
                "use",
                "This must not append",
            ),
        )

    assert rejected.status_code == 503
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM promotion_decisions"
        ).fetchone() == (1,)
    assert _tree_hashes(root) == before


@pytest.mark.asyncio
async def test_filters_order_and_eligibility_use_exact_identity_only_once(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    _write_decision_result(
        root,
        run_id="run-alpha-001",
        strategy_id="strategy-alpha",
        strategy_hash="a" * 64,
    )
    _write_decision_result(
        root,
        run_id="run-alpha-002",
        strategy_id="strategy-alpha",
        strategy_hash="a" * 64,
    )
    _write_decision_result(
        root,
        run_id="run-return-001",
        strategy_id="strategy-return",
        strategy_hash="b" * 64,
    )
    _write_decision_result(
        root,
        run_id="run-abandon-001",
        strategy_id="strategy-abandon",
        strategy_hash="c" * 64,
    )
    store = _store(tmp_path / "promotion.sqlite3")

    async with _client(root, store) as client:
        requests = (
            ("run-alpha-001", "req-a1", "use"),
            ("run-return-001", "req-r1", "return"),
            ("run-abandon-001", "req-x1", "abandon"),
            ("run-alpha-002", "req-a2", "use"),
            ("run-alpha-002", "req-a3", "return"),
        )
        for run_id, request_id, decision in requests:
            response = await client.post(
                f"/api/v1/runs/{run_id}/promotion-decisions",
                json=_request(request_id, decision, f"Reason for {request_id}"),
            )
            assert response.status_code == 200
        all_records = await client.get("/api/v1/promotion-decisions")
        run_filter = await client.get(
            "/api/v1/promotion-decisions",
            params={"run_id": "run-alpha-002"},
        )
        strategy_filter = await client.get(
            "/api/v1/promotion-decisions",
            params={"strategy_id": "strategy-alpha"},
        )
        decision_filter = await client.get(
            "/api/v1/promotion-decisions",
            params={"decision": "return"},
        )
        eligible = await client.get("/api/v1/promotion-decisions/eligible-strategies")

    assert all_records.json()["count"] == 5
    assert run_filter.json()["count"] == 2
    assert strategy_filter.json()["count"] == 3
    assert decision_filter.json()["count"] == 2
    assert eligible.status_code == 200
    assert eligible.json() == {
        "schema": "eligible_strategy_list.v1",
        "count": 1,
        "strategies": [
            {
                "strategy_id": "strategy-alpha",
                "content_sha256": "a" * 64,
                "supporting_decisions": [
                    {
                        "decision_id": all_records.json()["decisions"][0]["decision_id"],
                        "run_id": "run-alpha-001",
                    },
                    {
                        "decision_id": all_records.json()["decisions"][3]["decision_id"],
                        "run_id": "run-alpha-002",
                    },
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_database_guards_reject_update_delete_and_source_artifacts_stay_unchanged(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-guards-001"
    _write_decision_result(root, run_id=run_id)
    before = _tree_hashes(root)
    run_db = tmp_path / "runs.sqlite3"
    run_db.write_bytes(b"immutable-run-db-sentinel")
    run_db_before = sha256(run_db.read_bytes()).hexdigest()
    store = _store(tmp_path / "promotion.sqlite3")

    async with _client(root, store) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-guards", "use", "Keep immutable"),
        )

    assert response.status_code == 200
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE promotion_decisions SET reason = 'changed' WHERE request_id = ?",
                ("req-guards",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM promotion_decisions WHERE request_id = ?",
                ("req-guards",),
            )
    assert _tree_hashes(root) == before
    assert sha256(run_db.read_bytes()).hexdigest() == run_db_before


@pytest.mark.asyncio
async def test_post_decision_never_calls_export_packaging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-no-export-001"
    _write_decision_result(root, run_id=run_id)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("PromotionDecision must remain independent from result export")

    monkeypatch.setattr(ResultsCatalog, "get_export_artifacts", forbidden)
    async with _client(root, _store(tmp_path / "promotion.sqlite3")) as client:
        response = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-no-export", "use", "Independent decision"),
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_noop_database_guard_fails_closed_without_self_healing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-tampered-guard-001"
    _write_decision_result(root, run_id=run_id)
    store = _store(tmp_path / "promotion.sqlite3")

    async with _client(root, store) as client:
        first = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-before-tamper", "use", "Initial immutable record"),
        )
        assert first.status_code == 200
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP TRIGGER promotion_decisions_are_immutable")
            connection.execute(
                """
                CREATE TRIGGER promotion_decisions_are_immutable
                BEFORE UPDATE ON promotion_decisions
                BEGIN
                    SELECT 1;
                END
                """
            )
        listed = await client.get("/api/v1/promotion-decisions")
        second = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-after-tamper", "return", "Must not self-heal"),
        )

    assert listed.status_code == 503
    assert second.status_code == 503
    with sqlite3.connect(store.path) as connection:
        count = connection.execute("SELECT count(*) FROM promotion_decisions").fetchone()
    assert count == (1,)


@pytest.mark.asyncio
async def test_store_read_rejects_malformed_scorecard_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "promotion-store-snapshot-001"
    _write_decision_result(root, run_id=run_id)
    store = _store(tmp_path / "promotion.sqlite3")

    async with _client(root, store) as client:
        created = await client.post(
            f"/api/v1/runs/{run_id}/promotion-decisions",
            json=_request("req-store-snapshot", "use", "Create one valid snapshot"),
        )
        assert created.status_code == 200
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP TRIGGER promotion_decisions_are_immutable")
            connection.execute(
                "UPDATE promotion_decisions SET scorecard_snapshot_json = '[{}]'"
            )
            connection.execute(
                """
                CREATE TRIGGER promotion_decisions_are_immutable
                BEFORE UPDATE ON promotion_decisions
                BEGIN
                    SELECT RAISE(ABORT, 'promotion decisions are immutable');
                END
                """
            )
        listed = await client.get("/api/v1/promotion-decisions")

    assert listed.status_code == 503


@pytest.mark.parametrize("corruption", ["missing_index", "missing_unique"])
def test_existing_store_rejects_schema_corruption_without_self_healing(
    tmp_path: Path,
    corruption: str,
) -> None:
    database = tmp_path / "promotion.sqlite3"
    unique = "" if corruption == "missing_unique" else " UNIQUE"
    run_index = (
        ""
        if corruption == "missing_index"
        else """
        CREATE INDEX promotion_decisions_by_run
        ON promotion_decisions (run_id, created_at, decision_id);
        """
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            f"""
            CREATE TABLE promotion_decisions (
                decision_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL{unique},
                request_payload_sha256 TEXT NOT NULL,
                run_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_content_sha256 TEXT NOT NULL,
                result_sha256 TEXT NOT NULL,
                decision TEXT NOT NULL
                    CHECK (decision IN ('use', 'return', 'abandon')),
                reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
                scorecard_snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            {run_index}
            CREATE INDEX promotion_decisions_by_strategy
            ON promotion_decisions (
                strategy_id, strategy_content_sha256, created_at, decision_id
            );
            CREATE TRIGGER promotion_decisions_are_immutable
            BEFORE UPDATE ON promotion_decisions
            BEGIN
                SELECT RAISE(ABORT, 'promotion decisions are immutable');
            END;
            CREATE TRIGGER promotion_decisions_cannot_be_deleted
            BEFORE DELETE ON promotion_decisions
            BEGIN
                SELECT RAISE(ABORT, 'promotion decisions are immutable');
            END;
            """
        )

    with pytest.raises(PromotionDecisionIntegrityError, match="invalid table"):
        PromotionDecisionStore(database).list()

    with sqlite3.connect(database) as connection:
        index_rows = connection.execute(
            "PRAGMA index_list('promotion_decisions')"
        ).fetchall()
        indexes = {row[1] for row in index_rows}
        unique_request_id = any(
            row[2] == 1
            and tuple(
                item[0]
                for item in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (row[1],),
                ).fetchall()
            )
            == ("request_id",)
            for row in index_rows
        )
    if corruption == "missing_index":
        assert "promotion_decisions_by_run" not in indexes
    else:
        assert not unique_request_id
