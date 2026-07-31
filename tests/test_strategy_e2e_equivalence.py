"""WO-005 / 5-2: parse → full-run bit-for-bit vs hand-built StrategySpec + reject pins."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from futures_research.backtest.persistence import ResultExporter, SqliteRunStore
from futures_research.backtest.records import RunResult, strategy_event_to_dict
from futures_research.backtest.runner import BacktestRunArtifacts, BacktestRunConfig, BacktestRunner
from futures_research.backtest.strategy import (
    EntrySettings,
    RegimeSettings,
    RiskSettings,
    SignalKind,
    StrategySpec,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT
from futures_research.strategy import (
    StrategyValidationError,
    load_strategy_file,
    parse_strategy_document,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "strategies"
TREND_MVP = FIXTURES / "trend_mvp_p1.yaml"

# Real-data window short enough for CI-ish local runs (~15s × 2).
_RANGE_START = datetime(2026, 5, 6, 22, 0, tzinfo=UTC)
_RANGE_END = datetime(2026, 5, 20, 21, 0, tzinfo=UTC)

# --- Exact reject lines (docs/05 paste-back format); do not rephrase ---
_REJECT_UNKNOWN_STRUCTURE = (
    "structures[4].type: structure type 'lmr' is P2 未支持 "
    "(not in P1 vocabulary ['pullback_lifecycle', 'signal_bar']) — "
    "remove this structure or replace with pullback_lifecycle / signal_bar; "
    "LMR and other P2 structures are not accepted yet"
)
_REJECT_MISSING_PROVENANCE = (
    "provenance[risk.sizing.risk_pct]: missing provenance for numeric parameter — "
    "add provenance entry { path: risk.sizing.risk_pct, source: "
    "owner_explicit|owner_inferred|web_researched|market_convention|system_default|derived }"
)
_REJECT_BAD_REFERENCE = (
    "entry.sequence[1].require: structure id 'pb_missing' is not defined in structures — "
    "add the structure or fix the .completed reference"
)
_REJECT_OUT_OF_RANGE = (
    "regime.sep_mult.value: percentile 90.0 outside P1 band [50.0, 70.0] — "
    "set value between 50.0 and 70.0 (matrix/spec band)"
)


def _hand_built_spec_matching_fixture() -> StrategySpec:
    """Hand-built StrategySpec equivalent to tests/fixtures/strategies/trend_mvp_p1.yaml."""
    return StrategySpec(
        universe_session="eth",
        regime=RegimeSettings(
            separation_percentile=65.0,
            slope_percentile=65.0,
        ),
        entry=EntrySettings(
            pullback_ema_period=90,
            signal_bars=(SignalKind.INSIDE, SignalKind.MAGIC),
        ),
        risk=RiskSettings(target_r_multiple=1.0),
    )


def _load_raw() -> dict[str, Any]:
    loaded = yaml.safe_load(TREND_MVP.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _require_real_nq_data() -> None:
    market = PROJECT_ROOT / "data" / "market"
    daily = PROJECT_ROOT / "data" / "market-daily"
    if not market.is_dir() or not daily.is_dir():
        pytest.skip("real canonical market / market-daily data not present")
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    contract = registry.by_symbol("NQ")
    bars = list(
        CanonicalStore(market).read(
            contract.contract_id,
            start=_RANGE_START,
            end=_RANGE_END,
        )
    )
    if len(bars) < 100:
        pytest.skip("insufficient NQ bars in equivalence window")


def test_parsed_fixture_spec_equals_hand_built() -> None:
    """docs/05 P1-adjusted example parses to the same StrategySpec as hand construction."""
    parsed = load_strategy_file(TREND_MVP)
    hand = _hand_built_spec_matching_fixture()
    assert parsed.spec == hand
    assert parsed.spec.model_dump(mode="json") == hand.model_dump(mode="json")


def test_reject_case_unknown_structure_exact_string() -> None:
    raw = _load_raw()
    raw["structures"].append({"id": "lmr1", "type": "lmr", "layer": "entry"})
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    assert exc_info.value.format_report() == _REJECT_UNKNOWN_STRUCTURE


def test_reject_case_missing_provenance_exact_string() -> None:
    raw = _load_raw()
    raw["provenance"] = [
        item for item in raw["provenance"] if item["path"] != "risk.sizing.risk_pct"
    ]
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    assert exc_info.value.format_report() == _REJECT_MISSING_PROVENANCE


def test_reject_case_bad_reference_exact_string() -> None:
    raw = _load_raw()
    raw["entry"]["sequence"][1]["require"] = "pb_missing.completed"
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    assert exc_info.value.format_report() == _REJECT_BAD_REFERENCE


def test_reject_case_out_of_range_exact_string() -> None:
    raw = _load_raw()
    raw["regime"]["sep_mult"]["value"] = 90
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    assert exc_info.value.format_report() == _REJECT_OUT_OF_RANGE


def test_parse_and_hand_built_full_run_bit_for_bit(tmp_path: Path) -> None:
    """Real-data full-run: parsed StrategySpec vs hand-built StrategySpec identical results."""
    _require_real_nq_data()

    parsed = load_strategy_file(TREND_MVP)
    hand = _hand_built_spec_matching_fixture()
    assert parsed.spec == hand

    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    contract = registry.by_symbol("NQ")
    market_store = CanonicalStore(PROJECT_ROOT / "data" / "market")
    daily_store = CanonicalStore(PROJECT_ROOT / "data" / "market-daily")

    def _run(run_id: str, spec: StrategySpec) -> BacktestRunArtifacts:
        runner = BacktestRunner(
            canonical_store=market_store,
            daily_canonical_store=daily_store,
            run_store=SqliteRunStore(tmp_path / f"{run_id}.sqlite3"),
            result_exporter=ResultExporter(tmp_path / "results"),
            quality_reports_root=None,
        )
        return runner.run(
            contract=contract,
            config=BacktestRunConfig(
                run_id=run_id,
                strategy_version="strategy-v1-p1-fixture",
                session_name="eth",
                range_start=_RANGE_START,
                range_end=_RANGE_END,
                initial_capital=100_000.0,
                quantity=1,
                verify_nautilus_replay=False,
                validation_run=True,
                strategy_spec=spec,
            ),
        )

    from_parse = _run("wo005-5-2-parse", parsed.spec)
    from_hand = _run("wo005-5-2-hand", hand)

    assert _result_fingerprint(from_parse.result) == _result_fingerprint(from_hand.result)
    parse_ev = json.loads(from_parse.exported.events_path.read_text(encoding="utf-8"))
    hand_ev = json.loads(from_hand.exported.events_path.read_text(encoding="utf-8"))
    assert parse_ev["events"] == hand_ev["events"]
    parse_tr = json.loads(from_parse.exported.trades_path.read_text(encoding="utf-8"))
    hand_tr = json.loads(from_hand.exported.trades_path.read_text(encoding="utf-8"))
    assert _strip_run_id_trades(parse_tr) == _strip_run_id_trades(hand_tr)
    parse_main = json.loads(from_parse.exported.result_path.read_text(encoding="utf-8"))
    hand_main = json.loads(from_hand.exported.result_path.read_text(encoding="utf-8"))
    assert _strip_run_identity_main(parse_main) == _strip_run_identity_main(hand_main)


def _result_fingerprint(result: RunResult) -> dict[str, Any]:
    """Stable comparison payload: metrics, warnings, events, trades (no run_id / clock)."""
    return {
        "metrics": result.metrics.model_dump(mode="json"),
        "warnings": list(result.warnings),
        "events": [strategy_event_to_dict(event) for event in result.event_log],
        "trades": [
            {
                **record.to_dict(),
                "trade_id": None,
                "run_id": None,
            }
            for record in result.trade_records
        ],
        "calibration_status": (
            result.manifest.calibration.status if result.manifest.calibration else None
        ),
        "calibration_sample_size": (
            result.manifest.calibration.sample_size if result.manifest.calibration else None
        ),
        "data_fingerprint_digest": result.manifest.data_fingerprint.digest,
    }


def _strip_run_id_trades(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(payload)
    cleaned.pop("run_id", None)
    for trade in cleaned.get("trades") or []:
        if isinstance(trade, dict):
            trade.pop("run_id", None)
            trade.pop("trade_id", None)
    return cleaned


def _strip_run_identity_main(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(payload)
    run = cleaned.get("run")
    if isinstance(run, dict):
        run.pop("run_id", None)
        manifest = run.get("manifest")
        if isinstance(manifest, dict):
            manifest.pop("run_id", None)
            manifest.pop("created_at", None)
    for ref_key in ("trades_ref", "equity_curve_ref", "events_ref"):
        cleaned.pop(ref_key, None)
    cleaned.pop("completed_at", None)
    return cleaned
