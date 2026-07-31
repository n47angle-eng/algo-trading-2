"""Regression contract for the five strategy APIs already consumed by P2/P4."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest

from futures_research.api.main import app
from futures_research.api.strategy_resolution import StrategyResolutionError, resolve_strategy
from futures_research.paths import PROJECT_ROOT
from futures_research.strategy.store import StrategyStore

STRATEGY_FIXTURE = (
    PROJECT_ROOT / "tests" / "fixtures" / "strategies" / "trend_p50_ema18.yaml"
)
LEGACY_PATH_METHODS = {
    "/api/v1/strategies/validate": "post",
    "/api/v1/strategies/import": "post",
    "/api/v1/strategies": "get",
    "/api/v1/strategies/{strategy_id}": "get",
    "/api/v1/strategies/{strategy_id}/confirm": "post",
}
VERSION_KEYS = {
    "schema",
    "strategy_id",
    "status",
    "name",
    "created",
    "imported_at",
    "confirmed_at",
    "content_sha256",
    "source_text",
    "spec_ref",
    "based_on",
    "based_on_sketch",
    "based_on_sketch_origin",
    "based_on_insights",
    "rationale",
    "unquantified_notes",
    "universe",
    "parameters",
    "spec",
}
_ISSUE_LAYERS = {"format", "references", "semantics", "provenance"}
REAL_LEGACY_SHA256 = {
    "strategy-0001": "90f019c2b479cd01393d4829ab0fcfe2d15b15ad4fe381cd802d896c2e55bb40",
    "strategy-0002": "17f0d7e367684f71fd0554a95d049a57ba7c8fc59e02647f5b82268860e26f1d",
}
UNICODE_SKETCH_IDS = (
    "sketch-\u0662\u0660\u0662\u0666\u0660\u0667\u0662\u0665-\u0660\u0661",
    "sketch-\uff12\uff10\uff12\uff16\uff10\uff17\uff12\uff15-\uff10\uff11",
)


def _assert_string(value: object) -> None:
    assert type(value) is str


def _assert_optional_string(value: object) -> None:
    assert value is None or type(value) is str


def _assert_validation_payload(payload: dict[str, Any], *, valid: bool) -> None:
    assert {
        "schema",
        "valid",
        "issue_count",
        "issues",
        "report_text",
    } <= set(payload)
    assert payload["schema"] == "strategy_validation.v1"
    assert type(payload["valid"]) is bool
    assert payload["valid"] is valid
    assert type(payload["issue_count"]) is int
    assert type(payload["issues"]) is list
    assert type(payload["report_text"]) is str

    if valid:
        assert payload["issue_count"] == 0
        assert payload["issues"] == []
        _assert_string(payload["name"])
        universe = payload["universe"]
        assert type(universe) is dict
        assert {
            "primary_instrument",
            "asset_class",
            "contracts",
            "expansion_rationale",
            "session",
        } <= set(universe)
        _assert_string(universe["primary_instrument"])
        assert universe["asset_class"] in {"equity_index_futures", "commodity_futures"}
        assert type(universe["contracts"]) is list
        assert all(type(contract) is str for contract in universe["contracts"])
        assert type(universe["expansion_rationale"]) is dict
        assert all(type(key) is str for key in universe["expansion_rationale"])
        assert all(type(value) is str for value in universe["expansion_rationale"].values())
        assert universe["session"] in {"rth", "eth"}
        return

    assert payload["issue_count"] == len(payload["issues"])
    assert payload["issues"]
    for issue in payload["issues"]:
        assert type(issue) is dict
        assert {"path", "message", "fix", "layer", "line"} <= set(issue)
        for field in ("path", "message", "fix", "line"):
            _assert_string(issue[field])
        assert issue["layer"] in _ISSUE_LAYERS


def _assert_version_shape(payload: dict[str, Any], *, status: str | None = None) -> None:
    """Assert primitives, nested shapes, and enum values—not only key presence."""
    assert set(payload) >= VERSION_KEYS
    assert payload["schema"] == "strategy_version.v1"
    _assert_string(payload["strategy_id"])
    assert payload["status"] in {"draft", "confirmed"}
    if status is not None:
        assert payload["status"] == status
    for field in (
        "name",
        "created",
        "imported_at",
        "content_sha256",
        "source_text",
        "rationale",
    ):
        _assert_string(payload[field])
    _assert_optional_string(payload["confirmed_at"])
    _assert_optional_string(payload["spec_ref"])
    _assert_optional_string(payload["based_on"])
    _assert_string(payload["based_on_sketch"])
    assert payload["based_on_sketch_origin"] in {"workshop", "journal-app"}

    assert type(payload["based_on_insights"]) is list
    assert all(type(item) is str for item in payload["based_on_insights"])
    assert type(payload["unquantified_notes"]) is list
    for note in payload["unquantified_notes"]:
        assert type(note) is dict
        assert {"note", "action_needed"} <= set(note)
        _assert_string(note["note"])
        _assert_optional_string(note["action_needed"])

    universe = payload["universe"]
    assert type(universe) is dict
    assert {
        "primary_instrument",
        "asset_class",
        "contracts",
        "expansion_rationale",
        "session",
    } <= set(universe)
    _assert_string(universe["primary_instrument"])
    assert universe["asset_class"] in {"equity_index_futures", "commodity_futures"}
    assert type(universe["contracts"]) is list
    assert all(type(contract) is str for contract in universe["contracts"])
    assert type(universe["expansion_rationale"]) is dict
    assert all(type(key) is str for key in universe["expansion_rationale"])
    assert all(type(value) is str for value in universe["expansion_rationale"].values())
    assert universe["session"] in {"rth", "eth"}

    assert type(payload["parameters"]) is list
    assert payload["parameters"]
    for parameter in payload["parameters"]:
        assert type(parameter) is dict
        assert {"label", "value", "path", "source", "note", "kind"} <= set(parameter)
        _assert_string(parameter["label"])
        _assert_string(parameter["value"])
        _assert_optional_string(parameter["path"])
        _assert_optional_string(parameter["source"])
        _assert_optional_string(parameter["note"])
        assert parameter["kind"] in {"numeric", "structure"}

    spec = payload["spec"]
    assert type(spec) is dict
    assert {
        "universe_session",
        "regime_separation_percentile",
        "regime_slope_percentile",
        "pullback_ema_period",
        "entry_layers",
        "signal_bars",
        "target_r_multiple",
        "stop_offset_ticks",
    } <= set(spec)
    assert spec["universe_session"] in {"rth", "eth"}
    assert type(spec["regime_separation_percentile"]) is float
    assert type(spec["regime_slope_percentile"]) is float
    assert type(spec["pullback_ema_period"]) is int
    assert type(spec["entry_layers"]) is int
    assert type(spec["signal_bars"]) is list
    assert all(item in {"inside", "magic"} for item in spec["signal_bars"])
    assert type(spec["target_r_multiple"]) is float
    assert type(spec["stop_offset_ticks"]) is int


def test_legacy_strategy_paths_and_methods_remain_available() -> None:
    paths = app.openapi()["paths"]

    for path, method in LEGACY_PATH_METHODS.items():
        assert path in paths
        assert method in paths[path]


@pytest.mark.asyncio
async def test_legacy_strategy_response_shapes_are_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    monkeypatch.setattr(
        "futures_research.api.routes_strategies.default_strategy_store",
        lambda: store,
    )
    source_text = STRATEGY_FIXTURE.read_text(encoding="utf-8")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        validated = await client.post(
            "/api/v1/strategies/validate",
            json={"source_text": source_text, "filename": "strategy.yaml"},
        )
        assert validated.status_code == 200
        _assert_validation_payload(validated.json(), valid=True)

        invalid = await client.post(
            "/api/v1/strategies/validate",
            json={"source_text": source_text.replace("value: 50", "value: 90")},
        )
        assert invalid.status_code == 200
        _assert_validation_payload(invalid.json(), valid=False)

        imported = await client.post(
            "/api/v1/strategies/import",
            json={"source_text": source_text},
        )
        assert imported.status_code == 200
        import_payload = imported.json()
        assert import_payload["schema"] == "strategy_import.v1"
        assert type(import_payload["deduplicated"]) is bool
        assert import_payload["deduplicated"] is False
        _assert_string(import_payload["message"])
        _assert_version_shape(import_payload["version"], status="draft")
        strategy_id = import_payload["version"]["strategy_id"]

        duplicate = await client.post(
            "/api/v1/strategies/import",
            json={"source_text": source_text},
        )
        assert duplicate.status_code == 200
        assert type(duplicate.json()["deduplicated"]) is bool
        assert duplicate.json()["deduplicated"] is True
        _assert_version_shape(duplicate.json()["version"], status="draft")

        listed = await client.get("/api/v1/strategies")
        assert listed.status_code == 200
        list_payload = listed.json()
        assert list_payload["schema"] == "strategy_version_list.v1"
        assert type(list_payload["count"]) is int
        assert type(list_payload["versions"]) is list
        assert list_payload["count"] == len(list_payload["versions"]) == 1
        _assert_version_shape(list_payload["versions"][0], status="draft")

        fetched = await client.get(f"/api/v1/strategies/{strategy_id}")
        assert fetched.status_code == 200
        _assert_version_shape(fetched.json(), status="draft")

        confirmed = await client.post(f"/api/v1/strategies/{strategy_id}/confirm")
        assert confirmed.status_code == 200
        _assert_version_shape(confirmed.json(), status="confirmed")


@pytest.mark.asyncio
@pytest.mark.parametrize("sketch_id", UNICODE_SKETCH_IDS)
async def test_strategy_validate_rejects_unicode_sketch_id_at_references_layer(
    sketch_id: str,
) -> None:
    source_text = STRATEGY_FIXTURE.read_text(encoding="utf-8").replace(
        "sketch-20260725-01", sketch_id
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/strategies/validate",
            json={"source_text": source_text, "filename": "strategy.yaml"},
        )

    assert response.status_code == 200
    payload = response.json()
    _assert_validation_payload(payload, valid=False)
    issue = next(issue for issue in payload["issues"] if issue["path"] == "meta.based_on_sketch")
    assert issue["layer"] == "references"
    assert "canonical" in issue["message"]


@pytest.mark.asyncio
async def test_strategy_validate_accepts_complete_nonlocal_composite_lineage() -> None:
    source_text = (
        STRATEGY_FIXTURE.read_text(encoding="utf-8")
        .replace("sketch-20260725-01", "sketch-20991231-99")
        .replace("based_on_sketch_origin: workshop", "based_on_sketch_origin: journal-app")
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/strategies/validate",
            json={"source_text": source_text, "filename": "strategy.yaml"},
        )

    assert response.status_code == 200
    _assert_validation_payload(response.json(), valid=True)


@pytest.mark.asyncio
async def test_invalid_p2_universe_is_rejected_by_validate_import_and_confirm_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three strategy API entry points share one canonical universe validator."""
    store = StrategyStore(root=tmp_path / "strategies")
    monkeypatch.setattr(
        "futures_research.api.routes_strategies.default_strategy_store",
        lambda: store,
    )
    source_text = STRATEGY_FIXTURE.read_text(encoding="utf-8").replace(
        "asset_class: equity_index_futures", "asset_class: commodity_futures"
    )
    draft_path = store.root / "strategy-0001.json"
    draft_path.parent.mkdir(parents=True)
    draft_path.write_text(
        json.dumps(
            {
                "schema": "strategy_version.v1",
                "strategy_id": "strategy-0001",
                "status": "draft",
                "source_text": source_text,
            }
        ),
        encoding="utf-8",
    )
    before_draft = draft_path.read_bytes()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        validated = await client.post(
            "/api/v1/strategies/validate", json={"source_text": source_text}
        )
        imported = await client.post(
            "/api/v1/strategies/import", json={"source_text": source_text}
        )
        confirmed = await client.post("/api/v1/strategies/strategy-0001/confirm")

    assert validated.status_code == 200
    _assert_validation_payload(validated.json(), valid=False)
    assert any(issue["path"] == "universe.asset_class" for issue in validated.json()["issues"])
    assert imported.status_code == 422
    assert confirmed.status_code == 422
    assert draft_path.read_bytes() == before_draft
    assert list(store.root.glob("strategy-*.json")) == [draft_path]


def test_strategy_contract_assertions_reject_boolean_and_nested_type_mutations() -> None:
    """These guards deliberately red if an existing API silently changes primitive types."""
    validation = {
        "schema": "strategy_validation.v1",
        "valid": True,
        "issue_count": 0,
        "issues": [],
        "report_text": "",
        "name": "fixture",
        "universe": {
            "primary_instrument": "NQ",
            "asset_class": "equity_index_futures",
            "contracts": ["NQ"],
            "expansion_rationale": {},
            "session": "eth",
        },
    }
    bad_boolean = deepcopy(validation)
    bad_boolean["valid"] = "true"
    with pytest.raises(AssertionError):
        _assert_validation_payload(bad_boolean, valid=True)

    bad_nested = deepcopy(validation)
    bad_nested["universe"]["contracts"] = "NQ"
    with pytest.raises(AssertionError):
        _assert_validation_payload(bad_nested, valid=True)


def _write_legacy_record(root: Path, strategy_id: str) -> tuple[StrategyStore, bytes]:
    source_text = STRATEGY_FIXTURE.read_text(encoding="utf-8")
    source_text = source_text.replace("  based_on_sketch: sketch-20260725-01\n", "")
    source_text = source_text.replace("  based_on_sketch_origin: workshop\n", "")
    record = {
        "schema": "strategy_version.v1",
        "strategy_id": strategy_id,
        "status": "confirmed",
        "name": "pre-v1.2 legacy",
        "content_sha256": sha256(source_text.encode("utf-8")).hexdigest(),
        "source_text": source_text,
    }
    root.mkdir(parents=True)
    path = root / f"{strategy_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return StrategyStore(root=root), path.read_bytes()


@pytest.mark.parametrize("strategy_id", ["strategy-0001", "strategy-0002"])
def test_legacy_strategy_is_readable_but_cannot_start_any_new_run(
    tmp_path: Path,
    strategy_id: str,
) -> None:
    root = tmp_path / "strategies"
    store, before = _write_legacy_record(root, strategy_id)

    record = store.get(strategy_id)
    assert record["strategy_id"] == strategy_id
    assert record["source_text"].startswith("schema: strategy.v1")
    assert record["based_on_sketch_origin"] is None

    for validation_run in (False, True):
        with pytest.raises(StrategyResolutionError) as exc_info:
            resolve_strategy(
                strategy_version=strategy_id,
                symbol="NQ",
                validation_run=validation_run,
                store=store,
            )

        message = str(exc_info.value)
        assert "meta.based_on_sketch" in message
        assert "sketch-" in message
    assert (root / f"{strategy_id}.json").read_bytes() == before


@pytest.mark.parametrize("strategy_id", sorted(REAL_LEGACY_SHA256))
def test_real_legacy_strategy_artifact_is_unchanged_readable_and_not_runnable(
    strategy_id: str,
) -> None:
    """Guard the two immutable artifacts named in the batch-1 correction order."""
    path = PROJECT_ROOT / "data" / "strategies" / f"{strategy_id}.json"
    before = path.read_bytes()
    assert sha256(before).hexdigest() == REAL_LEGACY_SHA256[strategy_id]

    store = StrategyStore(root=path.parent)
    record = store.get(strategy_id)
    assert record["based_on_sketch_origin"] is None
    assert "based_on_sketch" not in record["source_text"]

    for validation_run in (False, True):
        with pytest.raises(StrategyResolutionError):
            resolve_strategy(
                strategy_version=strategy_id,
                symbol="NQ",
                validation_run=validation_run,
                store=store,
            )
    assert path.read_bytes() == before
