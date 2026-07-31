"""Batch 4 regression tests for the immutable single-run result ZIP."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from futures_research.api.main import app
from futures_research.api.promotion_decisions import PromotionDecisionStore
from futures_research.api.result_main_validation import validate_result_main
from futures_research.api.results_catalog import ResultsCatalog
from futures_research.backtest.persistence import ResultExporter
from futures_research.backtest.records import RunMetrics
from futures_research.backtest.runner import BacktestRunner

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
_APPROVED_BROWSER_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
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


def _manifest(run_id: str) -> dict[str, object]:
    return {
        "schema": "run_manifest.v1",
        "run_id": run_id,
        "strategy_version": "strategy-0042",
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
            "strategy_id": "strategy-0042",
            "strategy_name": "Export fixture",
            "content_sha256": "a" * 64,
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


def _write_bundle(
    root: Path,
    *,
    run_id: str,
    with_trade: bool = False,
    enriched: bool = False,
) -> dict[str, Path]:
    for directory in ("trades", "equity", "events"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    trades = [_trade_record()] if with_trade else []
    events = (
        [
            {"sequence": 1, "event_type": "signal_created"},
            {"sequence": 2, "event_type": "position_closed"},
        ]
        if with_trade
        else []
    )
    summary = {
        "availability": "available",
        "complete": True,
        "evaluation_count": 1 if with_trade else 0,
        "rejection_count": 0,
        "layer_reached_counts": {},
        "blocking_condition_counts": {},
        "deepest_layer": None,
        "trade_count": len(trades),
    }
    documents = {
        f"{run_id}.json": {
            "schema": "result.v1",
            "run": {
                "run_id": run_id,
                "strategy_version": "strategy-0042",
                "manifest": _manifest(run_id),
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
            "scorecard": [{"dim": "1_sample", "status": "pass", "detail": {}}],
            "trades_ref": f"trades/{run_id}.json",
            "equity_curve_ref": f"equity/{run_id}.json",
            "events_ref": f"events/{run_id}.json",
            "decision_evidence_complete": True,
        },
        f"trades/{run_id}.json": {
            "schema": "trades.v1",
            "run_id": run_id,
            "trades": trades,
            "decision_evidence_complete": True,
        },
        f"equity/{run_id}.json": {
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
        },
        f"events/{run_id}.json": {
            "schema": "events.v1",
            "run_id": run_id,
            "events": events,
            "rejection_evidence": [],
            "evidence_summary": summary,
            "evidence_complete": True,
        },
    }
    paths: dict[str, Path] = {}
    for relative, document in documents.items():
        path = root / relative
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths[relative] = path
    return paths


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


def _tree_fingerprint(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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


async def _export(
    root: Path,
    run_id: str,
    decision_path: Path,
    *,
    origin: str | None = None,
) -> httpx.Response:
    app.state.results_catalog = ResultsCatalog(root)
    app.state.promotion_decision_store = PromotionDecisionStore(decision_path)
    transport = httpx.ASGITransport(app=app)
    headers = {} if origin is None else {"Origin": origin}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                f"/api/v1/runs/{run_id}/export",
                headers=headers,
            )
    finally:
        delattr(app.state, "results_catalog")
        delattr(app.state, "promotion_decision_store")


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", _APPROVED_BROWSER_ORIGINS)
async def test_export_exposes_only_content_disposition_to_approved_browser_origins_without_writes(
    tmp_path: Path,
    origin: str,
) -> None:
    root = tmp_path / "results"
    run_id = "export-cors-001"
    paths = _write_bundle(root, run_id=run_id)
    before = _tree_fingerprint(root)
    decision_path = tmp_path / "promotion.sqlite3"

    response = await _export(
        root,
        run_id,
        decision_path,
        origin=origin,
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    exposed = response.headers.get("access-control-expose-headers")
    assert exposed is not None
    assert {
        token.strip().lower()
        for token in exposed.split(",")
        if token.strip()
    } == {"content-disposition"}
    assert response.headers["content-disposition"] == (
        f'attachment; filename="result-{run_id}.zip"'
    )
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        expected_members = [
            "result.json",
            f"trades/{run_id}.json",
            f"equity/{run_id}.json",
            f"events/{run_id}.json",
        ]
        assert archive.namelist() == expected_members
        assert archive.read("result.json") == paths[f"{run_id}.json"].read_bytes()
        for member in expected_members[1:]:
            assert archive.read(member) == paths[member].read_bytes()
    assert _tree_fingerprint(root) == before
    assert not decision_path.exists()


@pytest.mark.asyncio
async def test_export_does_not_authorize_unapproved_browser_origin(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "export-cors-unapproved-001"
    _write_bundle(root, run_id=run_id)
    before = _tree_fingerprint(root)
    decision_path = tmp_path / "promotion.sqlite3"

    response = await _export(
        root,
        run_id,
        decision_path,
        origin="https://not-approved.invalid",
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert response.headers["content-disposition"] == (
        f'attachment; filename="result-{run_id}.zip"'
    )
    assert _tree_fingerprint(root) == before
    assert not decision_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_trade", [False, True])
async def test_export_has_exact_members_and_original_bytes_without_writes(
    tmp_path: Path,
    with_trade: bool,
) -> None:
    root = tmp_path / "results"
    run_id = "export-trade-001" if with_trade else "export-zero-001"
    paths = _write_bundle(root, run_id=run_id, with_trade=with_trade)
    before = _tree_fingerprint(root)
    decision_path = tmp_path / "promotion.sqlite3"

    response = await _export(root, run_id, decision_path)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="result-{run_id}.zip"'
    )
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == [
            "result.json",
            f"trades/{run_id}.json",
            f"equity/{run_id}.json",
            f"events/{run_id}.json",
        ]
        assert archive.read("result.json") == paths[f"{run_id}.json"].read_bytes()
        for folder in ("trades", "equity", "events"):
            member = f"{folder}/{run_id}.json"
            assert archive.read(member) == paths[member].read_bytes()
        result = json.loads(archive.read("result.json"))
        assert result["trades_ref"] in archive.namelist()
        assert result["equity_curve_ref"] in archive.namelist()
        assert result["events_ref"] in archive.namelist()
    assert _tree_fingerprint(root) == before
    assert not decision_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_trade", [False, True], ids=["zero_trade", "nonzero"])
async def test_export_accepts_exact_enriched_metrics_and_preserves_source_bytes(
    tmp_path: Path,
    with_trade: bool,
) -> None:
    root = tmp_path / "results"
    run_id = "export-enriched-trade-001" if with_trade else "export-enriched-zero-001"
    paths = _write_bundle(
        root,
        run_id=run_id,
        with_trade=with_trade,
        enriched=True,
    )
    main_path = paths[f"{run_id}.json"]
    main = json.loads(main_path.read_text(encoding="utf-8"))
    assert set(main["metrics"]) == _ENRICHED_METRICS_KEYS
    before = _tree_fingerprint(root)
    decision_path = tmp_path / "promotion.sqlite3"

    response = await _export(root, run_id, decision_path)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        members = [
            "result.json",
            f"trades/{run_id}.json",
            f"equity/{run_id}.json",
            f"events/{run_id}.json",
        ]
        assert archive.namelist() == members
        assert archive.read("result.json") == main_path.read_bytes()
        for member in members[1:]:
            assert archive.read(member) == paths[member].read_bytes()
    assert _tree_fingerprint(root) == before
    assert not decision_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("location", _LARGE_INTEGER_LOCATIONS)
async def test_export_accepts_arbitrary_precision_integer_in_enriched_metrics(
    tmp_path: Path,
    location: str,
) -> None:
    root = tmp_path / "results"
    run_id = f"export-enriched-large-int-{location}"
    paths = _write_bundle(
        root,
        run_id=run_id,
        with_trade=True,
        enriched=True,
    )
    main_path = paths[f"{run_id}.json"]
    main = json.loads(main_path.read_text(encoding="utf-8"))
    metrics = main["metrics"]
    assert isinstance(metrics, dict)
    expected = _set_large_enriched_integer(metrics, location)
    main_path.write_text(
        json.dumps(main, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    source_bytes = {member: path.read_bytes() for member, path in paths.items()}
    before = _tree_fingerprint(root)
    decision_path = tmp_path / "promotion.sqlite3"

    response = await _export(root, run_id, decision_path)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        members = [
            "result.json",
            f"trades/{run_id}.json",
            f"equity/{run_id}.json",
            f"events/{run_id}.json",
        ]
        assert archive.namelist() == members
        assert archive.read("result.json") == source_bytes[f"{run_id}.json"]
        for member in members[1:]:
            assert archive.read(member) == source_bytes[member]
        archived_main = json.loads(archive.read("result.json"))
        archived_metrics = archived_main["metrics"]
        assert isinstance(archived_metrics, dict)
        assert _get_large_enriched_integer(archived_metrics, location) == expected
    assert _tree_fingerprint(root) == before
    assert not decision_path.exists()


def test_enriched_validation_returns_compact_metrics_without_mutating_document(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    run_id = "export-enriched-pure-validator-001"
    paths = _write_bundle(root, run_id=run_id, with_trade=True, enriched=True)
    main = json.loads(paths[f"{run_id}.json"].read_text(encoding="utf-8"))
    before = deepcopy(main)

    validated = validate_result_main(main, expected_run_id=run_id)

    assert isinstance(validated.metrics, RunMetrics)
    assert set(validated.metrics.model_dump()) == {
        "trade_count",
        "gross_pnl",
        "net_pnl",
        "net_r",
        "win_rate",
        "profit_factor",
        "expectancy_r",
        "max_drawdown_pnl",
    }
    assert main == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "with_trade"),
    _ENRICHED_CORRUPTION_CASES,
    ids=[case[0] for case in _ENRICHED_CORRUPTION_CASES],
)
async def test_export_rejects_invalid_enriched_metrics_without_partial_zip_or_writes(
    tmp_path: Path,
    corruption: str,
    with_trade: bool,
) -> None:
    root = tmp_path / "results"
    run_id = f"export-enriched-invalid-{corruption}"
    paths = _write_bundle(
        root,
        run_id=run_id,
        with_trade=with_trade,
        enriched=True,
    )
    main_path = paths[f"{run_id}.json"]
    main = json.loads(main_path.read_text(encoding="utf-8"))
    metrics = main["metrics"]
    assert isinstance(metrics, dict)
    _corrupt_enriched_metrics(metrics, corruption)
    main_path.write_text(json.dumps(main), encoding="utf-8")
    before = _tree_fingerprint(root)
    decision_path = tmp_path / "promotion.sqlite3"

    response = await _export(root, run_id, decision_path)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert not response.content.startswith(b"PK")
    assert _tree_fingerprint(root) == before
    assert not decision_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "bad_ref"),
    [
        ("trades_ref", "../outside.json"),
        ("trades_ref", "C:/outside.json"),
        ("trades_ref", "trades/other-run.json"),
        ("trades_ref", "events/export-ref-001.json"),
    ],
)
async def test_export_rejects_every_noncanonical_reference(
    tmp_path: Path,
    field: str,
    bad_ref: str,
) -> None:
    root = tmp_path / "results"
    run_id = "export-ref-001"
    paths = _write_bundle(root, run_id=run_id)
    main_path = paths[f"{run_id}.json"]
    main = json.loads(main_path.read_text(encoding="utf-8"))
    main[field] = bad_ref
    main_path.write_text(json.dumps(main), encoding="utf-8")

    response = await _export(root, run_id, tmp_path / "decisions.sqlite3")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert not response.content.startswith(b"PK")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ["missing", "bad_json", "bad_schema", "wrong_run", "summary_drift"],
)
async def test_export_fails_closed_for_sidecar_corruption(
    tmp_path: Path,
    corruption: str,
) -> None:
    root = tmp_path / "results"
    run_id = "export-corrupt-001"
    paths = _write_bundle(root, run_id=run_id)
    target = paths[f"equity/{run_id}.json"]
    if corruption == "missing":
        target.unlink()
    elif corruption == "bad_json":
        target.write_bytes(b"{not-json")
    elif corruption == "bad_schema":
        target.write_text(
            json.dumps({"schema": "other.v1", "run_id": run_id, "points": []}),
            encoding="utf-8",
        )
    elif corruption == "wrong_run":
        target.write_text(
            json.dumps(
                {"schema": "equity_curve.v1", "run_id": "other-run", "points": []}
            ),
            encoding="utf-8",
        )
    else:
        events_path = paths[f"events/{run_id}.json"]
        events = json.loads(events_path.read_text(encoding="utf-8"))
        events["evidence_summary"]["trade_count"] = 99
        events_path.write_text(json.dumps(events), encoding="utf-8")

    response = await _export(root, run_id, tmp_path / "decisions.sqlite3")

    assert response.status_code == 503
    assert not response.content.startswith(b"PK")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ["schema", "run_id", "manifest_hash", "metrics_type"],
)
async def test_export_fails_closed_for_main_schema_or_hash_corruption(
    tmp_path: Path,
    corruption: str,
) -> None:
    root = tmp_path / "results"
    run_id = "export-main-corrupt-001"
    paths = _write_bundle(root, run_id=run_id)
    main_path = paths[f"{run_id}.json"]
    main = json.loads(main_path.read_text(encoding="utf-8"))
    if corruption == "schema":
        main["schema"] = "other.v1"
    elif corruption == "run_id":
        main["run"]["run_id"] = "other-run"
    elif corruption == "manifest_hash":
        main["run"]["manifest"]["data_fingerprint"]["digest"] = "not-a-sha256"
    else:
        main["metrics"]["trade_count"] = True
    main_path.write_text(json.dumps(main), encoding="utf-8")

    response = await _export(root, run_id, tmp_path / "decisions.sqlite3")

    assert response.status_code == 503
    assert not response.content.startswith(b"PK")


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", _STRICT_MAIN_CORRUPTIONS)
async def test_export_rejects_every_strict_result_main_corruption_without_partial_zip(
    tmp_path: Path,
    corruption: str,
) -> None:
    root = tmp_path / "results"
    run_id = "export-strict-main-001"
    paths = _write_bundle(root, run_id=run_id)
    main_path = paths[f"{run_id}.json"]
    main = json.loads(main_path.read_text(encoding="utf-8"))
    _corrupt_strict_main(main, corruption)
    main_path.write_text(json.dumps(main), encoding="utf-8")
    before = _tree_fingerprint(root)
    decision_path = tmp_path / "decisions.sqlite3"

    response = await _export(root, run_id, decision_path)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert not response.content.startswith(b"PK")
    assert _tree_fingerprint(root) == before
    assert not decision_path.exists()


@pytest.mark.asyncio
async def test_export_distinguishes_missing_run_and_incomplete_legacy(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    paths = _write_bundle(root, run_id="legacy-export-001")
    main_path = paths["legacy-export-001.json"]
    main = json.loads(main_path.read_text(encoding="utf-8"))
    del main["decision_evidence_complete"]
    main_path.write_text(json.dumps(main), encoding="utf-8")

    missing = await _export(root, "does-not-exist", tmp_path / "decisions.sqlite3")
    legacy = await _export(root, "legacy-export-001", tmp_path / "decisions.sqlite3")

    assert missing.status_code == 404
    assert legacy.status_code == 503
    assert "legacy" in legacy.text


@pytest.mark.asyncio
async def test_export_never_calls_runner_writer_or_scorecard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "results"
    run_id = "export-read-only-001"
    _write_bundle(root, run_id=run_id)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("export must remain a raw read-only package operation")

    monkeypatch.setattr(BacktestRunner, "run", forbidden)
    monkeypatch.setattr(ResultExporter, "export", forbidden)
    monkeypatch.setattr(
        "futures_research.backtest.scorecard.enrich_result_file",
        forbidden,
    )

    response = await _export(root, run_id, tmp_path / "decisions.sqlite3")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_packaging_error_occurs_before_any_downloadable_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "results"
    run_id = "export-package-failure-001"
    _write_bundle(root, run_id=run_id)

    def fail_before_response(*args: object, **kwargs: object) -> bytes:
        raise OSError("simulated in-memory ZIP failure")

    monkeypatch.setattr(
        "futures_research.api.routes_results.build_result_export",
        fail_before_response,
    )
    response = await _export(root, run_id, tmp_path / "decisions.sqlite3")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert not response.content.startswith(b"PK")
