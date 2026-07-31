"""Tests for WO-006 / 6-2 read-only result APIs."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from futures_research.api.main import app
from futures_research.api.results_catalog import ResultArtifactIntegrityError, ResultsCatalog
from futures_research.backtest.scorecard import enrich_result_file


def _write_fixture_results(root: Path) -> None:
    """Minimal result.v1 + sidecars for API tests."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "trades").mkdir()
    (root / "events").mkdir()

    main = {
        "schema": "result.v1",
        "run": {
            "run_id": "fixture-val-001",
            "strategy_version": "strategy-0001",
            "manifest": {
                "contract_id": "NQ-202609-CME",
                "session_name": "eth",
                "range_start": "2026-04-06T22:00:00Z",
                "range_end": "2026-07-23T21:00:00Z",
                "validation_run": True,
            },
            "engine": {"nautilus": "1.230.0", "app": "0.1.0"},
        },
        "metrics": {
            "trade_count": 0,
            "net_r": 0.0,
            "net_pnl": 0.0,
            "win_rate": None,
            "profit_factor": None,
            "max_drawdown_pnl": 0.0,
            "expectancy_r": None,
        },
        "scorecard": [
            {"dim": "1_樣本量", "status": "insufficient_sample", "detail": {"trade_count": 0}},
            {"dim": "7_簡潔度", "status": "pass", "detail": {"param_count": 21}},
        ],
        "funnel": {
            "schema": "funnel.v1",
            "status": "ok",
            "daily_trend_days": 2,
            "evaluations_passing_daily_gate": 10,
            "evaluations_passing_mid_gate": 1,
            "signals_created": 1,
            "fills": 0,
            "reject_reasons": {"daily_regime_range": 5},
            "notes": "fixture",
        },
        "trades_ref": "trades/fixture-val-001.json",
        "events_ref": "events/fixture-val-001.json",
        "equity_curve_ref": "equity/fixture-val-001.json",
        "warnings": [],
        "owner_action": None,
    }
    (root / "fixture-val-001.json").write_text(
        json.dumps(main, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "trades" / "fixture-val-001.json").write_text(
        json.dumps({"schema": "trades.v1", "run_id": "fixture-val-001", "trades": []}),
        encoding="utf-8",
    )
    (root / "events" / "fixture-val-001.json").write_text(
        json.dumps(
            {
                "schema": "events.v1",
                "run_id": "fixture-val-001",
                "events": [
                    {
                        "sequence": 1,
                        "event_type": "daily_regime_changed",
                        "to_state": "trend",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    other = {
        "schema": "result.v1",
        "run": {
            "run_id": "fixture-std-001",
            "strategy_version": "strategy-0002",
            "manifest": {
                "contract_id": "GC-202608-COMEX",
                "session_name": "eth",
                "validation_run": False,
            },
            "engine": {},
        },
        "metrics": {"trade_count": 0, "net_r": 0.0, "net_pnl": 0.0},
        "scorecard": [],
        "trades_ref": "trades/fixture-std-001.json",
        "events_ref": "events/fixture-std-001.json",
        "warnings": [],
    }
    (root / "fixture-std-001.json").write_text(
        json.dumps(other, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "trades" / "fixture-std-001.json").write_text(
        json.dumps({"schema": "trades.v1", "run_id": "fixture-std-001", "trades": []}),
        encoding="utf-8",
    )
    (root / "events" / "fixture-std-001.json").write_text(
        json.dumps({"schema": "events.v1", "run_id": "fixture-std-001", "events": []}),
        encoding="utf-8",
    )


@pytest.fixture
def catalog_client(tmp_path: Path):
    """ASGI client with isolated results root."""
    results_root = tmp_path / "results"
    _write_fixture_results(results_root)
    app.state.results_catalog = ResultsCatalog(results_root=results_root)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    yield client
    # cleanup override
    if hasattr(app.state, "results_catalog"):
        delattr(app.state, "results_catalog")


@pytest.mark.asyncio
async def test_list_runs_returns_summaries(catalog_client: httpx.AsyncClient) -> None:
    response = await catalog_client.get("/api/v1/runs")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "run_list.v1"
    assert body["count"] == 2
    ids = {row["run_id"] for row in body["runs"]}
    assert ids == {"fixture-val-001", "fixture-std-001"}
    val = next(row for row in body["runs"] if row["run_id"] == "fixture-val-001")
    assert val["validation_run"] is True
    assert val["has_scorecard"] is True
    assert val["funnel_fills"] == 0
    assert val["contract_id"] == "NQ-202609-CME"


@pytest.mark.asyncio
async def test_get_run_includes_scorecard_and_funnel(
    catalog_client: httpx.AsyncClient,
) -> None:
    response = await catalog_client.get("/api/v1/runs/fixture-val-001")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "result.v1"
    assert len(body["scorecard"]) == 2
    assert body["funnel"]["daily_trend_days"] == 2


@pytest.mark.asyncio
async def test_get_trades_and_events(catalog_client: httpx.AsyncClient) -> None:
    trades = await catalog_client.get("/api/v1/runs/fixture-val-001/trades")
    events = await catalog_client.get("/api/v1/runs/fixture-val-001/events")
    assert trades.status_code == 200
    assert events.status_code == 200
    assert trades.json()["trades"] == []
    assert events.json()["events"][0]["event_type"] == "daily_regime_changed"


@pytest.mark.asyncio
async def test_batches_and_batch_runs(catalog_client: httpx.AsyncClient) -> None:
    batches = await catalog_client.get("/api/v1/batches")
    assert batches.status_code == 200
    body = batches.json()
    ids = {item["batch_id"] for item in body["batches"]}
    assert "validation" in ids
    assert "all" in ids

    val_runs = await catalog_client.get("/api/v1/batches/validation/runs")
    assert val_runs.status_code == 200
    assert val_runs.json()["count"] == 1
    assert val_runs.json()["runs"][0]["run_id"] == "fixture-val-001"


@pytest.mark.asyncio
async def test_missing_run_is_404(catalog_client: httpx.AsyncClient) -> None:
    response = await catalog_client.get("/api/v1/runs/does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_path_traversal_rejected(catalog_client: httpx.AsyncClient) -> None:
    response = await catalog_client.get("/api/v1/runs/../secrets")
    assert response.status_code == 404


def _write_complete_result(root: Path, *, run_id: str = "complete-001") -> None:
    """Hand-write the smallest legal new Batch-3 artifact bundle."""
    (root / "trades").mkdir(parents=True, exist_ok=True)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "result.v1",
                "run": {"run_id": run_id, "manifest": {}},
                "metrics": {"trade_count": 0},
                "trades_ref": f"trades/{run_id}.json",
                "events_ref": f"events/{run_id}.json",
                "decision_evidence_complete": True,
            }
        ),
        encoding="utf-8",
    )
    (root / "trades" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "trades.v1",
                "run_id": run_id,
                "trades": [],
                "decision_evidence_complete": True,
            }
        ),
        encoding="utf-8",
    )
    (root / "events" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "events.v1",
                "run_id": run_id,
                "events": [],
                "rejection_evidence": [],
                "evidence_summary": {
                    "availability": "available",
                    "complete": True,
                    "evaluation_count": 0,
                    "rejection_count": 0,
                    "layer_reached_counts": {},
                    "blocking_condition_counts": {},
                    "deepest_layer": None,
                    "trade_count": 0,
                },
                "evidence_complete": True,
            }
        ),
        encoding="utf-8",
    )


def _manual_condition_fact(
    *,
    condition_id: str,
    layer_id: str,
    status: str,
    source_sequence: int,
) -> dict[str, object]:
    """Hand-written canonical fact; never sourced from the production serializer."""
    return {
        "condition_id": condition_id,
        "layer_id": layer_id,
        "observed_at": "2026-07-22T22:00:00Z",
        "status": status,
        "actual": status == "passed",
        "operator": "eq",
        "required": True,
        "unit": "bool",
        "source_sequences": [source_sequence],
    }


def _manual_trade_record(
    *,
    trade_id: str = "trade-00001",
    ordinal: int = 1,
    entry_sequence: int = 1,
    exit_sequence: int = 2,
) -> dict[str, object]:
    """Return one manually-authored complete four-segment decision record."""
    return {
        "trade_id": trade_id,
        "decision_evidence": {
            "trade_id": trade_id,
            "ordinal": ordinal,
            "entry": {
                "signal_kind": "inside",
                "signal_timestamp": "2026-07-22T22:00:00Z",
                "entry_timestamp": "2026-07-22T22:01:00Z",
                "condition_facts": [
                    _manual_condition_fact(
                        condition_id="entry_gate",
                        layer_id="entry",
                        status="passed",
                        source_sequence=entry_sequence,
                    )
                ],
                "entry_reference": 20_000.0,
                "fill_price": 20_000.25,
                "source_event_sequences": [entry_sequence],
            },
            "stop": {
                "reference_type": "signal_low",
                "reference_price": 19_990.0,
                "offset_ticks": 1,
                "final_stop_price": 19_989.75,
                "condition_facts": [
                    _manual_condition_fact(
                        condition_id="stop_reference_price",
                        layer_id="execution",
                        status="passed",
                        source_sequence=entry_sequence,
                    )
                ],
                "source_event_sequences": [entry_sequence],
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
                "source_event_sequences": [exit_sequence],
            },
            "conservative_assumptions": [
                {
                    "code": "same_minute_stop_first",
                    "applied": False,
                    "effects": ["no same-minute stop/target collision"],
                    "source_sequences": [exit_sequence],
                }
            ],
        },
    }


def _manual_rejection_record(
    *,
    evidence_id: str = "rejection_0001",
    evaluation_sequence: int = 1,
    source_sequence: int = 1,
) -> dict[str, object]:
    """Return one manually-authored complete rejection record."""
    return {
        "evidence_id": evidence_id,
        "timestamp": "2026-07-22T22:00:00Z",
        "ts_init": "2026-07-22T22:05:00Z",
        "trading_date": "2026-07-22",
        "direction": "long",
        "evaluation_sequence": evaluation_sequence,
        "reached_layers": ["entry"],
        "condition_facts": [
            _manual_condition_fact(
                condition_id="entry_gate",
                layer_id="entry",
                status="failed",
                source_sequence=source_sequence,
            )
        ],
        "blocking_condition_ids": ["entry_gate"],
        "context": {
            "candidate_signal_kinds": ["inside"],
            "inside_count": 1,
            "entry_pullback_state": "awaiting_signal",
            "mid_pullback_state": "awaiting_touch",
            "daily_regime": "trend",
        },
        "source_event_sequences": [source_sequence],
    }


def _manual_summary(
    *,
    evaluation_count: int,
    rejection_count: int,
    trade_count: int,
    layer_reached_counts: dict[str, int] | None = None,
    blocking_condition_counts: dict[str, int] | None = None,
    deepest_layer: str | None = None,
) -> dict[str, object]:
    return {
        "availability": "available",
        "complete": True,
        "evaluation_count": evaluation_count,
        "rejection_count": rejection_count,
        "layer_reached_counts": layer_reached_counts or {},
        "blocking_condition_counts": blocking_condition_counts or {},
        "deepest_layer": deepest_layer,
        "trade_count": trade_count,
    }


def _write_manual_complete_result(
    root: Path,
    *,
    run_id: str,
    trades: list[dict[str, object]],
    events: list[dict[str, object]],
    rejection_evidence: list[dict[str, object]],
    evidence_summary: dict[str, object],
) -> None:
    """Write an isolated hand-authored new-complete bundle for reader tests."""
    (root / "trades").mkdir(parents=True, exist_ok=True)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "result.v1",
                "run": {"run_id": run_id, "manifest": {}},
                "metrics": {"trade_count": len(trades)},
                "trades_ref": f"trades/{run_id}.json",
                "events_ref": f"events/{run_id}.json",
                "decision_evidence_complete": True,
            }
        ),
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
    (root / "events" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "events.v1",
                "run_id": run_id,
                "events": events,
                "rejection_evidence": rejection_evidence,
                "evidence_summary": evidence_summary,
                "evidence_complete": True,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("probe", ["empty_trade", "empty_rejection", "summary_drift"])
def test_c_complete_evidence_corruption_probes_are_rejected(
    tmp_path: Path,
    probe: str,
) -> None:
    """The three corrected C probes must move from ACCEPTED to REJECTED."""
    root = tmp_path / "results"
    run_id = "c-probe-001"
    trades: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    rejections: list[dict[str, object]] = []
    summary = _manual_summary(evaluation_count=0, rejection_count=0, trade_count=0)
    if probe == "empty_trade":
        trades = [_manual_trade_record()]
        events = [
            {"sequence": 1, "event_type": "signal_created"},
            {"sequence": 2, "event_type": "position_closed"},
        ]
        summary = _manual_summary(evaluation_count=1, rejection_count=0, trade_count=1)
        decision = trades[0]["decision_evidence"]
        assert isinstance(decision, dict)
        trades[0]["decision_evidence"] = {}
    elif probe == "empty_rejection":
        events = [{"sequence": 1, "event_type": "signal_rejected"}]
        rejections = [{}]
        summary = _manual_summary(evaluation_count=1, rejection_count=1, trade_count=0)
    else:
        summary.update(
            {
                "evaluation_count": 99,
                "rejection_count": 99,
                "layer_reached_counts": {"entry": 99},
                "blocking_condition_counts": {"entry_gate": 99},
                "deepest_layer": "entry",
                "trade_count": 77,
            }
        )
    _write_manual_complete_result(
        root,
        run_id=run_id,
        trades=trades,
        events=events,
        rejection_evidence=rejections,
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="evidence|summary|trade"):
        ResultsCatalog(root).get_result(run_id)


def _as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def _manual_trade_bundle() -> tuple[
    list[dict[str, object]], list[dict[str, object]], dict[str, object]
]:
    return (
        [_manual_trade_record()],
        [
            {"sequence": 1, "event_type": "signal_created"},
            {"sequence": 2, "event_type": "position_closed"},
        ],
        _manual_summary(evaluation_count=1, rejection_count=0, trade_count=1),
    )


def _manual_rejection_bundle() -> tuple[
    list[dict[str, object]], list[dict[str, object]], dict[str, object]
]:
    return (
        [{"sequence": 1, "event_type": "signal_rejected"}],
        [_manual_rejection_record()],
        _manual_summary(
            evaluation_count=1,
            rejection_count=1,
            trade_count=0,
            layer_reached_counts={"entry": 1},
            blocking_condition_counts={"entry_gate": 1},
            deepest_layer="entry",
        ),
    )


def _manual_two_trade_bundle() -> tuple[
    list[dict[str, object]], list[dict[str, object]], dict[str, object]
]:
    first = _manual_trade_record()
    second = deepcopy(first)
    second["trade_id"] = "trade-00002"
    second_decision = _as_dict(second["decision_evidence"])
    second_decision["trade_id"] = "trade-00002"
    second_decision["ordinal"] = 2
    return (
        [first, second],
        [
            {"sequence": 1, "event_type": "signal_created"},
            {"sequence": 2, "event_type": "trade_closed"},
        ],
        _manual_summary(evaluation_count=1, rejection_count=0, trade_count=2),
    )


def _manual_two_rejection_bundle() -> tuple[
    list[dict[str, object]], list[dict[str, object]], dict[str, object]
]:
    first = _manual_rejection_record()
    second = deepcopy(first)
    second["evidence_id"] = "rejection_0002"
    second["evaluation_sequence"] = 2
    second["source_event_sequences"] = [2]
    second_fact = _as_dict(_as_list(second["condition_facts"])[0])
    second_fact["source_sequences"] = [2]
    return (
        [
            {"sequence": 1, "event_type": "signal_rejected"},
            {"sequence": 2, "event_type": "signal_rejected"},
        ],
        [first, second],
        _manual_summary(
            evaluation_count=2,
            rejection_count=2,
            trade_count=0,
            layer_reached_counts={"entry": 2},
            blocking_condition_counts={"entry_gate": 2},
            deepest_layer="entry",
        ),
    )


@pytest.mark.parametrize("missing_segment", ["entry", "stop", "exit", "conservative_assumptions"])
def test_complete_trade_requires_all_four_decision_segments(
    tmp_path: Path,
    missing_segment: str,
) -> None:
    trades, events, summary = _manual_trade_bundle()
    decision = _as_dict(trades[0]["decision_evidence"])
    del decision[missing_segment]
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="missing-segment-001",
        trades=trades,
        events=events,
        rejection_evidence=[],
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="missing|required|evidence"):
        ResultsCatalog(root).get_result("missing-segment-001")


@pytest.mark.parametrize(
    "case_name",
    [
        "missing unit",
        "invalid operator",
        "dangling fact sequence",
        "dangling entry sequence",
        "naive timestamp",
        "blank signal kind",
        "non-finite native",
    ],
)
def test_complete_trade_rejects_partial_or_invalid_nested_condition_facts(
    tmp_path: Path,
    case_name: str,
) -> None:
    trades, events, summary = _manual_trade_bundle()
    decision = _as_dict(trades[0]["decision_evidence"])
    entry = _as_dict(decision["entry"])
    fact = _as_dict(_as_list(entry["condition_facts"])[0])
    if case_name == "missing unit":
        del fact["unit"]
    elif case_name == "invalid operator":
        fact["operator"] = "approximately"
    elif case_name == "dangling fact sequence":
        fact["source_sequences"] = [99]
    elif case_name == "dangling entry sequence":
        entry["source_event_sequences"] = [99]
    elif case_name == "naive timestamp":
        fact["observed_at"] = "2026-07-22T22:00:00"
    elif case_name == "blank signal kind":
        entry["signal_kind"] = " "
    else:
        fact["actual"] = float("nan")
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="bad-fact-001",
        trades=trades,
        events=events,
        rejection_evidence=[],
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="evidence|condition|sequence"):
        ResultsCatalog(root).get_result("bad-fact-001")


def test_complete_trade_decision_id_must_match_outer_trade(tmp_path: Path) -> None:
    trades, events, summary = _manual_trade_bundle()
    decision = _as_dict(trades[0]["decision_evidence"])
    decision["trade_id"] = "another-trade"
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="trade-mismatch-001",
        trades=trades,
        events=events,
        rejection_evidence=[],
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="trade_id"):
        ResultsCatalog(root).get_result("trade-mismatch-001")


@pytest.mark.parametrize(
    "case_name",
    ["duplicate ordinal", "ordinal gap", "out of order", "duplicate trade id"],
)
def test_complete_trade_ids_and_ordinals_match_exact_trade_order(
    tmp_path: Path,
    case_name: str,
) -> None:
    trades, events, summary = _manual_two_trade_bundle()
    second_decision = _as_dict(trades[1]["decision_evidence"])
    if case_name == "duplicate ordinal":
        second_decision["ordinal"] = 1
    elif case_name == "ordinal gap":
        second_decision["ordinal"] = 3
    elif case_name == "out of order":
        trades.reverse()
    else:
        trades[1]["trade_id"] = "trade-00001"
        second_decision["trade_id"] = "trade-00001"
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="trade-order-001",
        trades=trades,
        events=events,
        rejection_evidence=[],
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="trade|ordinal"):
        ResultsCatalog(root).get_result("trade-order-001")


@pytest.mark.parametrize("case_name", ["empty", "partial fact", "partial context"])
def test_complete_rejection_requires_full_record_facts_and_context(
    tmp_path: Path,
    case_name: str,
) -> None:
    events, rejections, summary = _manual_rejection_bundle()
    if case_name == "empty":
        rejections[0] = {}
    elif case_name == "partial fact":
        fact = _as_dict(_as_list(rejections[0]["condition_facts"])[0])
        del fact["unit"]
    else:
        context = _as_dict(rejections[0]["context"])
        del context["mid_pullback_state"]
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="rejection-shape-001",
        trades=[],
        events=events,
        rejection_evidence=rejections,
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="evidence|missing|required"):
        ResultsCatalog(root).get_result("rejection-shape-001")


@pytest.mark.parametrize("case_name", ["duplicate id", "out of order"])
def test_complete_rejection_ids_are_unique_and_canonically_ordered(
    tmp_path: Path,
    case_name: str,
) -> None:
    events, rejections, summary = _manual_two_rejection_bundle()
    if case_name == "duplicate id":
        rejections[1]["evidence_id"] = "rejection_0001"
    else:
        rejections.reverse()
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="rejection-order-001",
        trades=[],
        events=events,
        rejection_evidence=rejections,
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="rejection evidence|evidence ids"):
        ResultsCatalog(root).get_result("rejection-order-001")


def test_complete_rejection_rejects_dangling_source_sequence(tmp_path: Path) -> None:
    events, rejections, summary = _manual_rejection_bundle()
    rejections[0]["source_event_sequences"] = [99]
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="rejection-dangling-001",
        trades=[],
        events=events,
        rejection_evidence=rejections,
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="dangling|sequence"):
        ResultsCatalog(root).get_result("rejection-dangling-001")


@pytest.mark.parametrize(
    "case_name",
    ["count mismatch", "wrong event mapping", "many-to-one mapping"],
)
def test_signal_rejected_events_and_records_have_exact_one_to_one_mapping(
    tmp_path: Path,
    case_name: str,
) -> None:
    events, rejections, summary = _manual_rejection_bundle()
    if case_name == "count mismatch":
        events.append({"sequence": 2, "event_type": "signal_rejected"})
    elif case_name == "wrong event mapping":
        events.append({"sequence": 2, "event_type": "position_closed"})
        rejections[0]["source_event_sequences"] = [2]
        fact = _as_dict(_as_list(rejections[0]["condition_facts"])[0])
        fact["source_sequences"] = [2]
    else:
        events, rejections, summary = _manual_two_rejection_bundle()
        rejections[0]["source_event_sequences"] = [1, 2]
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="rejection-map-001",
        trades=[],
        events=events,
        rejection_evidence=rejections,
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="rejection|signal_rejected"):
        ResultsCatalog(root).get_result("rejection-map-001")


@pytest.mark.parametrize("case_name", ["zero", "duplicate"])
def test_complete_event_sequences_are_positive_and_unique(
    tmp_path: Path,
    case_name: str,
) -> None:
    trades, events, summary = _manual_trade_bundle()
    if case_name == "zero":
        events[0]["sequence"] = 0
    else:
        events[1]["sequence"] = 1
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="event-sequence-001",
        trades=trades,
        events=events,
        rejection_evidence=[],
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="sequence"):
        ResultsCatalog(root).get_result("event-sequence-001")


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("evaluation_count", 2),
        ("rejection_count", 0),
        ("layer_reached_counts", {}),
        ("blocking_condition_counts", {}),
        ("deepest_layer", "daily"),
        ("trade_count", 1),
    ],
)
def test_complete_evidence_summary_must_exactly_match_rebuilt_projection(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    events, rejections, summary = _manual_rejection_bundle()
    summary[field] = bad_value
    root = tmp_path / "results"
    _write_manual_complete_result(
        root,
        run_id="summary-drift-001",
        trades=[],
        events=events,
        rejection_evidence=rejections,
        evidence_summary=summary,
    )

    with pytest.raises(ResultArtifactIntegrityError, match="summary|derived exactly"):
        ResultsCatalog(root).get_result("summary-drift-001")


@pytest.mark.asyncio
async def test_partial_complete_records_fail_for_catalog_apis_and_scorecard_export(
    tmp_path: Path,
) -> None:
    trades, events, summary = _manual_trade_bundle()
    trades[0]["decision_evidence"] = {}
    root = tmp_path / "results"
    run_id = "consumer-gate-001"
    _write_manual_complete_result(
        root,
        run_id=run_id,
        trades=trades,
        events=events,
        rejection_evidence=[],
        evidence_summary=summary,
    )
    catalog = ResultsCatalog(root)

    with pytest.raises(ResultArtifactIntegrityError, match="evidence"):
        catalog.get_result(run_id)
    with pytest.raises(ResultArtifactIntegrityError, match="evidence"):
        enrich_result_file(root / f"{run_id}.json", write=False)

    app.state.results_catalog = catalog
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = [
                await client.get(f"/api/v1/runs/{run_id}"),
                await client.get(f"/api/v1/runs/{run_id}/trades"),
                await client.get(f"/api/v1/runs/{run_id}/events"),
                await client.get(f"/api/v1/runs/{run_id}/chart"),
                await client.get(f"/api/v1/runs/{run_id}/narrative"),
            ]
    finally:
        delattr(app.state, "results_catalog")

    assert all(response.status_code == 503 for response in responses)
    assert all("unavailable" not in response.text for response in responses)


def test_valid_nonempty_complete_trade_and_rejection_records_remain_readable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    trades, trade_events, trade_summary = _manual_trade_bundle()
    _write_manual_complete_result(
        root,
        run_id="valid-trade-001",
        trades=trades,
        events=trade_events,
        rejection_evidence=[],
        evidence_summary=trade_summary,
    )
    rejection_events, rejections, rejection_summary = _manual_rejection_bundle()
    _write_manual_complete_result(
        root,
        run_id="valid-rejection-001",
        trades=[],
        events=rejection_events,
        rejection_evidence=rejections,
        evidence_summary=rejection_summary,
    )
    catalog = ResultsCatalog(root)

    assert catalog.get_result("valid-trade-001")["decision_evidence_complete"] is True
    assert catalog.get_trades("valid-trade-001")["trades"][0]["trade_id"] == "trade-00001"
    assert catalog.get_result("valid-rejection-001")["decision_evidence_complete"] is True
    assert catalog.get_events("valid-rejection-001")["rejection_evidence"][0][
        "evidence_id"
    ] == "rejection_0001"


@pytest.mark.parametrize(
    "case_name, mutate",
    [
        ("missing trades", lambda root, run_id: (root / "trades" / f"{run_id}.json").unlink()),
        ("missing events", lambda root, run_id: (root / "events" / f"{run_id}.json").unlink()),
        (
            "trades missing marker",
            lambda root, run_id: _replace_json(
                root / "trades" / f"{run_id}.json", {"decision_evidence_complete": None}
            ),
        ),
        (
            "partial trade evidence",
            lambda root, run_id: _replace_json(
                root / "trades" / f"{run_id}.json", {"trades": [{"trade_id": "t1"}]}
            ),
        ),
        (
            "partial rejection summary",
            lambda root, run_id: _delete_nested_json_key(
                root / "events" / f"{run_id}.json", "evidence_summary", "trade_count"
            ),
        ),
        (
            "sidecar run id mismatch",
            lambda root, run_id: _replace_json(
                root / "events" / f"{run_id}.json", {"run_id": "another-run"}
            ),
        ),
        (
            "sidecar schema mismatch",
            lambda root, run_id: _replace_json(
                root / "trades" / f"{run_id}.json", {"schema": "trades.v0"}
            ),
        ),
        (
            "invalid sidecar json",
            lambda root, run_id: (root / "events" / f"{run_id}.json").write_text(
                "{", encoding="utf-8"
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_new_complete_main_never_downgrades_broken_sidecars_to_legacy(
    tmp_path: Path,
    case_name: str,
    mutate,
) -> None:
    """The main marker, not a missing sidecar field, decides new-vs-legacy semantics."""
    root = tmp_path / "results"
    run_id = "complete-001"
    _write_complete_result(root, run_id=run_id)
    mutate(root, run_id)
    catalog = ResultsCatalog(root)

    with pytest.raises(ResultArtifactIntegrityError, match="complete|evidence|JSON"):
        catalog.get_result(run_id)

    app.state.results_catalog = catalog
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            detail = await client.get(f"/api/v1/runs/{run_id}")
            trades = await client.get(f"/api/v1/runs/{run_id}/trades")
            events = await client.get(f"/api/v1/runs/{run_id}/events")
            chart = await client.get(f"/api/v1/runs/{run_id}/chart")
            narrative = await client.get(f"/api/v1/runs/{run_id}/narrative")
    finally:
        delattr(app.state, "results_catalog")

    for response in (detail, trades, events, chart, narrative):
        assert response.status_code == 503, case_name
        assert "unavailable" not in response.text


def test_new_complete_artifact_allows_explicitly_empty_evidence_lists(tmp_path: Path) -> None:
    root = tmp_path / "results"
    _write_complete_result(root)
    catalog = ResultsCatalog(root)

    assert catalog.get_result("complete-001")["decision_evidence_complete"] is True
    trades = catalog.get_trades("complete-001")
    events = catalog.get_events("complete-001")
    assert trades["trades"] == []
    assert trades["decision_evidence_complete"] is True
    assert events["rejection_evidence"] == []
    assert events["evidence_summary"]["trade_count"] == 0
    assert events["evidence_complete"] is True


@pytest.mark.parametrize("marker", [False, None, "true", 1])
def test_main_complete_marker_must_be_true_or_absent_for_genuine_legacy(
    tmp_path: Path,
    marker: object,
) -> None:
    root = tmp_path / "results"
    _write_complete_result(root)
    _replace_json(root / "complete-001.json", {"decision_evidence_complete": marker})

    with pytest.raises(ResultArtifactIntegrityError, match="true or absent"):
        ResultsCatalog(root).get_result("complete-001")


def test_legacy_main_keeps_honest_unavailable_fallback_when_sidecars_are_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    root.mkdir()
    (root / "legacy-001.json").write_text(
        json.dumps({"schema": "result.v1", "run": {"run_id": "legacy-001"}}),
        encoding="utf-8",
    )
    catalog = ResultsCatalog(root)

    assert catalog.get_result("legacy-001")["schema"] == "result.v1"
    assert catalog.get_trades("legacy-001")["decision_evidence_availability"] == "unavailable"
    assert catalog.get_events("legacy-001")["evidence_availability"] == "unavailable"


def test_scorecard_export_consumer_cannot_bypass_new_result_integrity_gate(tmp_path: Path) -> None:
    root = tmp_path / "results"
    _write_complete_result(root)
    (root / "events" / "complete-001.json").unlink()

    with pytest.raises(ResultArtifactIntegrityError, match="missing required events_ref"):
        enrich_result_file(root / "complete-001.json", write=False)


def _replace_json(path: Path, changes: dict[str, object]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _delete_nested_json_key(path: Path, parent: str, key: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    nested = payload[parent]
    assert isinstance(nested, dict)
    del nested[key]
    path.write_text(json.dumps(payload), encoding="utf-8")
