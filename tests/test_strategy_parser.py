"""WO-005 / 5-1: strategy.v1 parser four-layer validation + StrategySpec mapping."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from futures_research.backtest.strategy import SignalKind, StrategySpec
from futures_research.sketch.store import SketchStore
from futures_research.strategy import (
    StrategyValidationError,
    load_strategy_file,
    parse_strategy_document,
)
from futures_research.strategy.parser import required_provenance_paths

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "strategies"
TREND_MVP = FIXTURES / "trend_mvp_p1.yaml"


def _load_raw() -> dict:
    return yaml.safe_load(TREND_MVP.read_text(encoding="utf-8"))


def test_load_fixture_maps_to_default_aligned_spec() -> None:
    """docs/05-style P1 example → StrategySpec matching engine defaults (touch 90)."""
    parsed = load_strategy_file(TREND_MVP)
    assert parsed.document.schema_name == "strategy.v1"
    assert parsed.document.meta.based_on_sketch == "sketch-20260724-01"
    assert parsed.document.meta.based_on_sketch_origin == "workshop"
    assert parsed.document.meta.based_on_insights == ["insight-2026-07-first-pullback"]
    assert parsed.spec == StrategySpec(
        universe_session="eth",
        entry=StrategySpec().entry.model_copy(
            update={
                "pullback_ema_period": 90,
                "signal_bars": (SignalKind.INSIDE, SignalKind.MAGIC),
            }
        ),
        regime=StrategySpec().regime.model_copy(
            update={"separation_percentile": 65.0, "slope_percentile": 65.0}
        ),
        risk=StrategySpec().risk.model_copy(update={"target_r_multiple": 1.0}),
    )
    assert parsed.spec.universe_session == "eth"
    assert parsed.spec.entry.pullback_ema_period == 90
    assert parsed.spec.entry.signal_bars == (SignalKind.INSIDE, SignalKind.MAGIC)


def test_touch_ema_fast_maps_pullback_period_18() -> None:
    raw = _load_raw()
    for structure in raw["structures"]:
        if structure.get("type") == "pullback_lifecycle":
            structure["touch"] = "ema_fast"
    parsed = parse_strategy_document(raw)
    assert parsed.spec.entry.pullback_ema_period == 18


def test_reject_unknown_structure_type_p2_explicit() -> None:
    raw = _load_raw()
    raw["structures"].append({"id": "lmr1", "type": "lmr", "layer": "entry"})
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    report = exc_info.value.format_report()
    assert "structures[" in report
    assert "lmr" in report
    assert "P2 未支持" in report
    assert " — " in report
    # Must not silently skip: error is raised
    assert any(issue.layer == "semantics" for issue in exc_info.value.issues)


def test_reject_unknown_contract_symbol() -> None:
    raw = _load_raw()
    raw["universe"]["contracts"] = ["ZZ"]
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    line = exc_info.value.issues[0].format_line()
    assert line.startswith("universe.contracts[0]:")
    assert "ZZ" in line
    assert " — " in line


def test_reject_bad_structure_reference_in_sequence() -> None:
    raw = _load_raw()
    raw["entry"]["sequence"][1]["require"] = "pb_missing.completed"
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    assert any("pb_missing" in issue.message for issue in exc_info.value.issues)


def test_reject_missing_provenance() -> None:
    raw = _load_raw()
    raw["provenance"] = [
        item for item in raw["provenance"] if item["path"] != "risk.sizing.risk_pct"
    ]
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    assert any(
        issue.layer == "provenance" and "risk.sizing.risk_pct" in issue.path
        for issue in exc_info.value.issues
    )


def test_reject_percentile_out_of_range() -> None:
    raw = _load_raw()
    raw["regime"]["sep_mult"]["value"] = 90
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    assert any("sep_mult" in issue.path for issue in exc_info.value.issues)


def test_reject_wrong_trio() -> None:
    raw = _load_raw()
    raw["timeframes"]["trio"]["entry"] = "15m"
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    assert any("trio" in issue.path for issue in exc_info.value.issues)


def test_reject_format_missing_rationale() -> None:
    raw = _load_raw()
    raw["rationale"] = "   "
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    assert any(issue.layer == "format" for issue in exc_info.value.issues)


def test_reject_yaml_syntax() -> None:
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document("schema: strategy.v1\nmeta: [unterminated")
    assert "YAML" in exc_info.value.format_report() or "mapping" in exc_info.value.format_report()


def test_error_format_is_path_message_fix() -> None:
    raw = _load_raw()
    raw["direction"]["mode"] = "reversal"
    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)
    for issue in exc_info.value.issues:
        line = issue.format_line()
        assert ": " in line
        assert " — " in line
        path, rest = line.split(": ", 1)
        assert path
        assert " — " in rest


def test_required_provenance_paths_cover_numeric_fields() -> None:
    parsed = load_strategy_file(TREND_MVP)
    required = required_provenance_paths(parsed.document)
    assert "indicators.ema_fast.period" in required
    assert "regime.sep_mult.value" in required
    assert "risk.daily_loss_limit_r" in required
    present = {entry.path for entry in parsed.document.provenance}
    assert set(required) <= present


def test_based_on_sketch_and_insights_accepted() -> None:
    parsed = load_strategy_file(TREND_MVP)
    assert parsed.document.meta.based_on_sketch == "sketch-20260724-01"
    assert parsed.document.meta.based_on_sketch_origin == "workshop"
    assert parsed.document.meta.based_on_insights is not None


@pytest.mark.parametrize(
    ("replacement", "case_name"),
    [
        (None, "missing"),
        ("  based_on_sketch: null\n", "null"),
        ('  based_on_sketch: "   "\n', "blank"),
    ],
)
def test_based_on_sketch_must_be_declared_and_nonempty_at_references_layer(
    replacement: str | None,
    case_name: str,
) -> None:
    raw = TREND_MVP.read_text(encoding="utf-8")
    original = "  based_on_sketch: sketch-20260724-01\n"
    source = raw.replace(original, replacement or "")

    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(source)

    lineage_issues = [
        issue for issue in exc_info.value.issues if issue.path == "meta.based_on_sketch"
    ]
    assert len(lineage_issues) == 1, case_name
    issue = lineage_issues[0]
    assert issue.layer == "references"
    assert "meta.based_on_sketch" in issue.fix
    assert "sketch-" in issue.fix


@pytest.mark.parametrize(
    ("replacement", "case_name"),
    [
        (None, "missing"),
        ("  based_on_sketch_origin: null\n", "null"),
        ('  based_on_sketch_origin: "   "\n', "blank"),
        ("  based_on_sketch_origin: other-app\n", "invalid enum"),
    ],
)
def test_based_on_sketch_origin_must_be_declared_and_valid_at_references_layer(
    replacement: str | None,
    case_name: str,
) -> None:
    raw = TREND_MVP.read_text(encoding="utf-8")
    original = "  based_on_sketch_origin: workshop\n"
    source = raw.replace(original, replacement or "")

    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(source)

    lineage_issues = [
        issue for issue in exc_info.value.issues if issue.path == "meta.based_on_sketch_origin"
    ]
    assert len(lineage_issues) == 1, case_name
    assert lineage_issues[0].layer == "references"
    assert "meta.based_on_sketch_origin" in lineage_issues[0].fix


@pytest.mark.parametrize(
    "invalid_id",
    [
        "not-a-sketch-id",
        "sketch-20260726-001",
        "sketch-20260230-01",
        " sketch-20260726-01",
        "sketch-20260726-01 ",
        "sketch-\u0662\u0660\u0662\u0666\u0660\u0667\u0662\u0666-\u0660\u0661",
        "sketch-\uff12\uff10\uff12\uff16\uff10\uff17\uff12\uff16-\uff10\uff11",
    ],
)
def test_based_on_sketch_must_use_the_shared_canonical_identity_parser(
    invalid_id: str,
) -> None:
    raw = _load_raw()
    raw["meta"]["based_on_sketch"] = invalid_id

    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)

    issue = next(issue for issue in exc_info.value.issues if issue.path == "meta.based_on_sketch")
    assert issue.layer == "references"
    assert "canonical" in issue.message


def test_complete_nonlocal_composite_lineage_does_not_require_a_local_sketch() -> None:
    raw = _load_raw()
    raw["meta"]["based_on_sketch"] = "sketch-20991231-99"
    raw["meta"]["based_on_sketch_origin"] = "journal-app"

    parsed = parse_strategy_document(raw)

    assert parsed.document.meta.based_on_sketch == "sketch-20991231-99"
    assert parsed.document.meta.based_on_sketch_origin == "journal-app"


@pytest.mark.parametrize(
    ("case_name", "mutate", "expected_path"),
    [
        (
            "primary must be a member",
            lambda raw: raw["universe"].update({"primary_instrument": "YM"}),
            "universe.primary_instrument",
        ),
        (
            "unknown member",
            lambda raw: raw["universe"].update({"contracts": ["ZZ"]}),
            "universe.contracts[0]",
        ),
        (
            "declared primary class mismatch",
            lambda raw: raw["universe"].update({"asset_class": "commodity_futures"}),
            "universe.asset_class",
        ),
        (
            "different member class",
            lambda raw: raw["universe"].update(
                {
                    "contracts": ["NQ", "GC"],
                    "expansion_rationale": {"GC": "comparison"},
                }
            ),
            "universe.contracts[1]",
        ),
        (
            "rationale must have exact keys",
            lambda raw: raw["universe"].update(
                {"contracts": ["NQ", "YM"], "expansion_rationale": {}}
            ),
            "universe.expansion_rationale",
        ),
        (
            "universe whitespace is not normalized",
            lambda raw: raw["universe"].update({"contracts": [" NQ"]}),
            "universe.contracts",
        ),
    ],
)
def test_p2_universe_is_self_contained_and_fail_closed(
    case_name: str,
    mutate,
    expected_path: str,
) -> None:
    raw = _load_raw()
    mutate(raw)

    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw)

    assert any(issue.path == expected_path for issue in exc_info.value.issues), case_name
    assert all(" — " in issue.format_line() for issue in exc_info.value.issues)


@pytest.mark.parametrize("field", ["sessions", "currency"])
def test_p2_universe_uses_the_catalog_for_session_and_currency_gates(
    tmp_path: Path,
    field: str,
) -> None:
    raw = _load_raw()
    raw["universe"] = {
        "primary_instrument": "NQ",
        "asset_class": "equity_index_futures",
        "contracts": ["NQ", "YM"],
        "expansion_rationale": {"YM": "same equity-index trend structure"},
        "session": "eth",
    }
    config_source = Path(__file__).resolve().parents[1] / "config" / "contracts.yaml"
    config = yaml.safe_load(config_source.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    if field == "sessions":
        config["contracts"]["YM"]["sessions"] = {
            "rth": {"start": "08:30", "end": "15:00"}
        }
        expected_path = "universe.session"
    else:
        config["contracts"]["YM"]["currency"] = "EUR"
        expected_path = "universe.contracts[1]"
    config_path = tmp_path / "contracts.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(raw, contracts_config=config_path)

    assert any(issue.path == expected_path for issue in exc_info.value.issues), field


def test_local_complete_sketch_must_match_primary_and_asset_class(tmp_path: Path) -> None:
    store = SketchStore(root=tmp_path / "sketches")
    _write_local_sketch(
        store,
        sketch_id="sketch-20260724-01",
        instrument="YM",
        asset_class="equity_index_futures",
    )

    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(_load_raw(), sketch_store=store)

    assert any(issue.path == "universe.primary_instrument" for issue in exc_info.value.issues)


def test_local_legacy_sketch_is_not_treated_as_a_missing_external_package(tmp_path: Path) -> None:
    store = SketchStore(root=tmp_path / "sketches")
    _write_local_sketch(
        store,
        sketch_id="sketch-20260724-01",
        instrument="NQ",
        asset_class=None,
    )

    with pytest.raises(StrategyValidationError) as exc_info:
        parse_strategy_document(_load_raw(), sketch_store=store)

    issue = next(issue for issue in exc_info.value.issues if issue.path == "meta.based_on_sketch")
    assert "duplicate" in issue.fix


def _write_local_sketch(
    store: SketchStore,
    *,
    sketch_id: str,
    instrument: str,
    asset_class: str | None,
) -> None:
    """Hand-write an immutable-looking local package for parser read-policy tests."""
    folder = store.root / "workshop" / sketch_id
    folder.mkdir(parents=True)
    asset_line = f"asset_class: {asset_class}\n" if asset_class is not None else ""
    (folder / "meta.yaml").write_text(
        "schema: sketch.v1\n"
        f"sketch_id: {sketch_id}\n"
        "kind: strategy\n"
        "origin: workshop\n"
        "chart_source: rendered\n"
        "instructions_template: instructions.v1\n"
        f"instrument: {instrument}\n"
        f"{asset_line}"
        "created: 2026-07-24\n"
        "title: Local test sketch\n"
        "rationale: local package identity test\n"
        "charts:\n"
        "  - {file: chart-D.png, timeframe: D, indicators_shown: [], owner_view: bias}\n"
        "  - {file: chart-1H.png, timeframe: 1H, indicators_shown: [], owner_view: mid}\n"
        "  - {file: chart-30m.png, timeframe: 30m, indicators_shown: [], owner_view: aux}\n"
        "  - {file: chart-5m.png, timeframe: 5m, indicators_shown: [], owner_view: entry}\n",
        encoding="utf-8",
    )
    (folder / "INSTRUCTIONS.md").write_text("<!-- instructions.v1 -->\n", encoding="utf-8")
    for filename in ("chart-D.png", "chart-1H.png", "chart-30m.png", "chart-5m.png"):
        (folder / filename).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
