"""WO-006 / 6-5: A2 StrategyVersion store, strategies API, and real spec injection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from futures_research.api import strategy_resolution
from futures_research.api.main import app
from futures_research.api.strategy_resolution import (
    StrategyResolutionError,
    resolve_strategy,
)
from futures_research.backtest.records import (
    RunManifest,
    StrategyBinding,
    StrategyParamOverride,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.paths import PROJECT_ROOT
from futures_research.strategy.errors import StrategyValidationError
from futures_research.strategy.store import StrategyStore

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "strategies"
P50_EMA18 = FIXTURES / "trend_p50_ema18.yaml"
P65_EMA90 = FIXTURES / "trend_p65_ema90.yaml"


@pytest.fixture
def store(tmp_path: Path) -> StrategyStore:
    return StrategyStore(root=tmp_path / "strategies")


def _import(store: StrategyStore, path: Path) -> dict[str, object]:
    return store.import_document(path.read_text(encoding="utf-8")).record


# --- store ---------------------------------------------------------------


def test_import_assigns_running_ids_and_keeps_yaml_verbatim(store: StrategyStore) -> None:
    text = P50_EMA18.read_text(encoding="utf-8")
    first = store.import_document(text)
    second = store.import_document(P65_EMA90.read_text(encoding="utf-8"))

    assert first.record["strategy_id"] == "strategy-0001"
    assert second.record["strategy_id"] == "strategy-0002"
    assert first.record["status"] == "draft"
    # source_text is the exact bytes the terminal AI wrote, not a re-emission.
    assert first.record["source_text"] == text


def test_reimporting_identical_bytes_returns_the_same_version(store: StrategyStore) -> None:
    text = P50_EMA18.read_text(encoding="utf-8")
    first = store.import_document(text)
    again = store.import_document(text)

    assert again.deduplicated is True
    assert again.record["strategy_id"] == first.record["strategy_id"]
    assert len(store.list_versions()) == 1


def test_confirm_is_idempotent_and_never_rewrites_content(store: StrategyStore) -> None:
    record = _import(store, P50_EMA18)
    strategy_id = str(record["strategy_id"])

    confirmed = store.confirm(strategy_id)
    again = store.confirm(strategy_id)

    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_at"] is not None
    assert again["confirmed_at"] == confirmed["confirmed_at"]
    assert again["content_sha256"] == record["content_sha256"]
    assert again["source_text"] == record["source_text"]


def test_invalid_document_is_rejected_before_storage(store: StrategyStore) -> None:
    broken = P50_EMA18.read_text(encoding="utf-8").replace("value: 50", "value: 90")

    with pytest.raises(StrategyValidationError) as excinfo:
        store.import_document(broken)

    report = excinfo.value.format_report()
    assert "regime.sep_mult.value" in report
    assert "outside P1 band" in report
    assert store.list_versions() == []


def test_parameter_rows_carry_provenance_for_numeric_paths(store: StrategyStore) -> None:
    record = _import(store, P50_EMA18)
    rows = record["parameters"]
    assert isinstance(rows, list)

    numeric = {
        str(row["path"]): row
        for row in rows
        if isinstance(row, dict) and row.get("kind") == "numeric"
    }
    assert numeric["regime.sep_mult.value"]["source"] == "owner_explicit"
    assert numeric["risk.sizing.risk_pct"]["source"] == "market_convention"
    # Structural declarations are labelled, never left blank.
    structure = [row for row in rows if isinstance(row, dict) and row.get("kind") == "structure"]
    assert structure and all(row["source"] is None for row in structure)


# --- resolution ----------------------------------------------------------


def test_confirmed_version_injects_its_own_spec(store: StrategyStore) -> None:
    record = _import(store, P50_EMA18)
    store.confirm(str(record["strategy_id"]))

    resolved = resolve_strategy(
        strategy_version=str(record["strategy_id"]),
        symbol="NQ",
        validation_run=True,
        store=store,
    )

    assert resolved.spec is not None
    assert resolved.spec.regime.separation_percentile == 50.0
    assert resolved.spec.entry.pullback_ema_period == 18
    assert resolved.session_name == "eth"
    assert resolved.binding.source == "strategy_file"
    assert resolved.binding.overrides == ()


def test_two_versions_resolve_to_different_specs(store: StrategyStore) -> None:
    """The acceptance property: picking another version must change the replay."""
    first = _import(store, P50_EMA18)
    second = _import(store, P65_EMA90)
    store.confirm(str(first["strategy_id"]))
    store.confirm(str(second["strategy_id"]))

    a = resolve_strategy(
        strategy_version=str(first["strategy_id"]),
        symbol="NQ",
        validation_run=True,
        store=store,
    )
    b = resolve_strategy(
        strategy_version=str(second["strategy_id"]),
        symbol="NQ",
        validation_run=True,
        store=store,
    )

    assert a.spec is not None
    assert b.spec is not None
    assert a.spec != b.spec
    assert (
        a.spec.entry.pullback_ema_period,
        a.spec.regime.separation_percentile,
    ) == (18, 50.0)
    assert (
        b.spec.entry.pullback_ema_period,
        b.spec.regime.separation_percentile,
    ) == (90, 65.0)


def test_draft_version_cannot_run(store: StrategyStore) -> None:
    record = _import(store, P50_EMA18)

    with pytest.raises(StrategyResolutionError, match="draft"):
        resolve_strategy(
            strategy_version=str(record["strategy_id"]),
            symbol="NQ",
            validation_run=True,
            store=store,
        )


def test_unauthorized_contract_is_blocked_with_the_documented_remedy(
    store: StrategyStore,
) -> None:
    record = _import(store, P50_EMA18)
    store.confirm(str(record["strategy_id"]))

    with pytest.raises(StrategyResolutionError) as excinfo:
        resolve_strategy(
            strategy_version=str(record["strategy_id"]),
            symbol="GC",
            validation_run=False,
            store=store,
        )

    message = str(excinfo.value)
    assert "universe.contracts" in message
    assert "GC" in message


def test_validation_run_cannot_cross_the_documented_universe(store: StrategyStore) -> None:
    record = _import(store, P50_EMA18)
    store.confirm(str(record["strategy_id"]))

    with pytest.raises(StrategyResolutionError, match="universe.contracts"):
        resolve_strategy(
            strategy_version=str(record["strategy_id"]),
            symbol="GC",
            validation_run=True,
            store=store,
        )


def test_new_run_resolution_revalidates_the_canonical_p2_universe_without_rewriting(
    tmp_path: Path,
) -> None:
    source_text = P50_EMA18.read_text(encoding="utf-8").replace(
        "asset_class: equity_index_futures", "asset_class: commodity_futures"
    )
    store = StrategyStore(root=tmp_path / "strategies")
    path = store.root / "strategy-0001.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": "strategy_version.v1",
                "strategy_id": "strategy-0001",
                "status": "confirmed",
                "name": "invalid P2 catalog binding",
                "source_text": source_text,
            }
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()

    with pytest.raises(StrategyResolutionError, match="universe.asset_class"):
        resolve_strategy(
            strategy_version="strategy-0001",
            symbol="NQ",
            validation_run=True,
            store=store,
        )

    assert path.read_bytes() == before


def test_override_is_recorded_with_original_and_applied_values(
    store: StrategyStore,
) -> None:
    record = _import(store, P50_EMA18)
    store.confirm(str(record["strategy_id"]))

    resolved = resolve_strategy(
        strategy_version=str(record["strategy_id"]),
        symbol="NQ",
        validation_run=True,
        override_separation_percentile=65.0,
        override_pullback_ema_period=90,
        store=store,
    )

    assert resolved.spec is not None
    assert resolved.spec.regime.separation_percentile == 65.0
    assert resolved.spec.entry.pullback_ema_period == 90
    recorded = {
        item.path: (item.spec_value, item.applied_value) for item in resolved.binding.overrides
    }
    assert recorded["regime.sep_mult.value"] == ("50", "65")
    assert recorded["structures.pullback_lifecycle.touch.period"] == ("18", "90")


def test_override_on_a_non_validation_run_is_refused(store: StrategyStore) -> None:
    record = _import(store, P50_EMA18)
    store.confirm(str(record["strategy_id"]))

    with pytest.raises(StrategyResolutionError, match="validation run"):
        resolve_strategy(
            strategy_version=str(record["strategy_id"]),
            symbol="NQ",
            validation_run=False,
            override_separation_percentile=65.0,
            store=store,
        )


def test_session_conflict_is_refused_rather_than_ignored(store: StrategyStore) -> None:
    record = _import(store, P50_EMA18)
    store.confirm(str(record["strategy_id"]))

    with pytest.raises(StrategyResolutionError, match="universe.session"):
        resolve_strategy(
            strategy_version=str(record["strategy_id"]),
            symbol="NQ",
            validation_run=True,
            requested_session="rth",
            store=store,
        )


def test_unmanaged_label_keeps_the_pre_65_engine_default_path(store: StrategyStore) -> None:
    resolved = resolve_strategy(
        strategy_version="trend-v0",
        symbol="NQ",
        validation_run=True,
        store=store,
    )

    assert resolved.spec is None
    assert resolved.binding.source == "engine_default"
    assert resolved.regime_separation_percentile == strategy_resolution.LEGACY_SEPARATION_PERCENTILE
    assert resolved.pullback_ema_period == strategy_resolution.LEGACY_PULLBACK_EMA_PERIOD


def test_missing_managed_id_is_an_error_not_a_silent_default(store: StrategyStore) -> None:
    with pytest.raises(StrategyResolutionError, match="strategy-9999"):
        resolve_strategy(
            strategy_version="strategy-9999",
            symbol="NQ",
            validation_run=True,
            store=store,
        )


# --- manifest guard ------------------------------------------------------


def _manifest(**overrides: object) -> RunManifest:
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    contract = registry.by_symbol("NQ")
    base: dict[str, object] = {
        "run_id": "nq-test-0001",
        "strategy_version": "strategy-0001",
        "contract_id": contract.contract_id,
        "session_name": "eth",
        "range_start": datetime(2026, 5, 6, 22, tzinfo=UTC),
        "range_end": datetime(2026, 5, 8, 21, tzinfo=UTC),
        "initial_capital": 100_000.0,
        "quantity": 1,
        "costs": contract.execution_costs,
        "data_fingerprint": {
            "digest": "0" * 64,
            "bar_count": 0,
        },
    }
    base.update(overrides)
    return RunManifest.model_validate(base)


def test_manifest_refuses_overrides_outside_a_validation_run() -> None:
    binding = StrategyBinding(
        source="strategy_file",
        strategy_id="strategy-0001",
        content_sha256="a" * 64,
        overrides=(
            StrategyParamOverride(
                path="regime.sep_mult.value",
                spec_value="50",
                applied_value="65",
            ),
        ),
    )

    with pytest.raises(ValueError, match="validation_run"):
        _manifest(strategy_binding=binding, validation_run=False)

    assert _manifest(strategy_binding=binding, validation_run=True).validation_run is True


def test_manifest_refuses_unauthorized_contract_outside_a_validation_run() -> None:
    binding = StrategyBinding(
        source="strategy_file",
        strategy_id="strategy-0001",
        content_sha256="a" * 64,
        universe_contracts=("NQ",),
        universe_authorized=False,
    )

    with pytest.raises(ValueError, match="universe.contracts"):
        _manifest(strategy_binding=binding, validation_run=False)


# --- API -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_api_roundtrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    monkeypatch.setattr(
        "futures_research.api.routes_strategies.default_strategy_store",
        lambda: store,
    )
    text = P50_EMA18.read_text(encoding="utf-8")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bad = await client.post(
            "/api/v1/strategies/validate",
            json={"source_text": text.replace("value: 50", "value: 90")},
        )
        assert bad.status_code == 200
        payload = bad.json()
        assert payload["valid"] is False
        # Paste-back text is the plain validator report, one line per issue.
        assert payload["report_text"].splitlines() == [issue["line"] for issue in payload["issues"]]

        imported = await client.post("/api/v1/strategies/import", json={"source_text": text})
        assert imported.status_code == 200, imported.text
        strategy_id = imported.json()["version"]["strategy_id"]
        assert imported.json()["version"]["status"] == "draft"

        listed = await client.get("/api/v1/strategies", params={"status": "confirmed"})
        assert listed.json()["count"] == 0

        confirmed = await client.post(f"/api/v1/strategies/{strategy_id}/confirm")
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "confirmed"

        listed = await client.get("/api/v1/strategies", params={"status": "confirmed"})
        assert [v["strategy_id"] for v in listed.json()["versions"]] == [strategy_id]

        rejected = await client.post(
            "/api/v1/strategies/import",
            json={"source_text": text.replace("value: 50", "value: 90")},
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["issue_count"] == 2


@pytest.mark.asyncio
async def test_ib_status_endpoint_reports_a_known_state() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/system/ib-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "ib_status.v1"
    assert payload["state"] in {
        "unconfigured",
        "port_unreachable",
        "port_reachable_unverified",
    }
    # Never claims a verified API session — a TCP connect cannot prove one.
    assert "已連接" not in payload["detail"]
    assert payload["probe_timeout_seconds"] <= 1.5
