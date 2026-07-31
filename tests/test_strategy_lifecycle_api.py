"""P2 version lifecycle: numeric direct-child derive and lossless delete archive."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from futures_research.api.main import app
from futures_research.backtest.persistence import RunIndexIntegrityError, SqliteRunStore
from futures_research.backtest.run_reference_catalog import (
    CatalogRun,
    RunReferenceCatalog,
    RunReferenceMigrationRequired,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.paths import PROJECT_ROOT
from futures_research.strategy.parser import parse_strategy_document
from futures_research.strategy.store import StrategyStore

STRATEGY_FIXTURE = (
    PROJECT_ROOT / "tests" / "fixtures" / "strategies" / "trend_p50_ema18.yaml"
)


@contextmanager
def _api_dependencies(
    store: StrategyStore,
    catalog: RunReferenceCatalog | None,
) -> Iterator[None]:
    """Install temp production dependencies and restore any prior app state."""
    missing = object()
    prior_store = getattr(app.state, "strategy_store", missing)
    prior_catalog = getattr(app.state, "run_reference_catalog", missing)
    app.state.strategy_store = store
    if catalog is None:
        if hasattr(app.state, "run_reference_catalog"):
            delattr(app.state, "run_reference_catalog")
    else:
        app.state.run_reference_catalog = catalog
    try:
        yield
    finally:
        if prior_store is missing:
            delattr(app.state, "strategy_store")
        else:
            app.state.strategy_store = prior_store
        if prior_catalog is missing:
            if hasattr(app.state, "run_reference_catalog"):
                delattr(app.state, "run_reference_catalog")
        else:
            app.state.run_reference_catalog = prior_catalog


def _empty_catalog(tmp_path: Path) -> RunReferenceCatalog:
    return RunReferenceCatalog(
        _runs=(),
        main_database=tmp_path / "runs.sqlite3",
        audit_database=None,
    )


def _catalog(
    tmp_path: Path,
    *runs: CatalogRun,
) -> RunReferenceCatalog:
    return RunReferenceCatalog(
        _runs=tuple(runs),
        main_database=tmp_path / "runs.sqlite3",
        audit_database=None,
    )


def _catalog_run(
    strategy_id: str,
    *,
    run_id: str,
    validation_run: bool,
    symbol: str | None = "NQ",
) -> CatalogRun:
    return CatalogRun(
        source="main",
        lookup_available=True,
        run_id=run_id,
        validation_run=validation_run,
        strategy_version=strategy_id,
        strategy_content_sha256="a" * 64,
        contract_id="NQ-202609-CME",
        symbol=symbol,
        session_name="eth",
        range_start="2026-07-20T22:00:00Z",
        range_end="2026-07-21T21:00:00Z",
        trading_dates_status="complete",
        trading_dates=(date(2026, 7, 21),),
    )


def _source_with_grandparent(grandparent: str = "strategy-7777") -> str:
    return STRATEGY_FIXTURE.read_text(encoding="utf-8").replace(
        "based_on: strategy-0001",
        f"based_on: {grandparent}",
    )


def _import_parent(store: StrategyStore) -> tuple[str, str]:
    source_text = _source_with_grandparent()
    record = store.import_document(source_text).record
    return str(record["strategy_id"]), source_text


def _file_inventory(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _set_numeric_leaf(raw: dict[str, Any], path: str, value: int | float) -> None:
    """Set one approved test path, including id-addressed indicator rows."""
    parts = path.split(".")
    current: Any = raw
    for part in parts[:-1]:
        if isinstance(current, list):
            current = next(
                item
                for item in current
                if isinstance(item, dict) and item.get("id") == part
            )
        else:
            current = current[part]
    current[parts[-1]] = value


def _expected_derived_mapping(
    parent_source: str,
    *,
    parent_id: str,
    patches: dict[str, int | float],
) -> dict[str, Any]:
    expected = yaml.safe_load(parent_source)
    assert isinstance(expected, dict)
    expected["meta"]["based_on"] = parent_id
    for path, value in patches.items():
        _set_numeric_leaf(expected, path, value)
        entry = next(item for item in expected["provenance"] if item["path"] == path)
        entry["source"] = "owner_explicit"
        entry["note"] = "Owner UI 微調"
    return expected


def _write_legacy_record(
    store: StrategyStore,
    *,
    strategy_id: str = "strategy-0001",
) -> tuple[Path, str]:
    source_text = STRATEGY_FIXTURE.read_text(encoding="utf-8")
    source_text = source_text.replace("  based_on_sketch: sketch-20260725-01\n", "")
    source_text = source_text.replace("  based_on_sketch_origin: workshop\n", "")
    record = {
        "schema": "strategy_version.v1",
        "strategy_id": strategy_id,
        "status": "confirmed",
        "name": "legacy",
        "content_sha256": sha256(source_text.encode("utf-8")).hexdigest(),
        "source_text": source_text,
    }
    store.root.mkdir(parents=True)
    path = store.root / f"{strategy_id}.json"
    path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path, source_text


def test_lifecycle_routes_are_additive_to_the_five_legacy_operations() -> None:
    paths = app.openapi()["paths"]
    assert "post" in paths["/api/v1/strategies/{parent_strategy_id}/derive"]
    assert "delete" in paths["/api/v1/strategies/{strategy_id}"]
    assert "get" in paths["/api/v1/strategies/{strategy_id}"]
    assert "post" in paths["/api/v1/strategies/{strategy_id}/confirm"]
    assert "post" in paths["/api/v1/strategies/validate"]
    assert "post" in paths["/api/v1/strategies/import"]
    assert "get" in paths["/api/v1/strategies"]


@pytest.mark.asyncio
async def test_derive_is_numeric_only_direct_child_and_retry_deduplicates(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    parent_id, parent_source = _import_parent(store)
    assert parent_id != "strategy-7777"
    store.confirm(parent_id)
    parent_path = store.root / f"{parent_id}.json"
    parent_bytes = parent_path.read_bytes()
    parent_record = deepcopy(store.get(parent_id))
    catalog = _empty_catalog(tmp_path)
    transport = httpx.ASGITransport(app=app)
    request = {
        "patches": [
            {"path": "risk.sizing.risk_pct", "value": 2},
            {"path": "regime.sep_mult.value", "value": 65},
        ]
    }

    with _api_dependencies(store, catalog):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/strategies/{parent_id}/derive",
                json=request,
            )
            retry = await client.post(
                f"/api/v1/strategies/{parent_id}/derive",
                json={
                    "patches": [
                        {"path": "regime.sep_mult.value", "value": 65.0},
                        {"path": "risk.sizing.risk_pct", "value": 2.0},
                    ]
                },
            )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {
        "schema",
        "parent_strategy_id",
        "changed_count",
        "changed_paths",
        "deduplicated",
        "version",
    }
    assert payload["schema"] == "strategy_derive.v1"
    assert payload["parent_strategy_id"] == parent_id
    assert type(payload["changed_count"]) is int
    assert payload["changed_count"] == 2
    assert payload["changed_paths"] == [
        "regime.sep_mult.value",
        "risk.sizing.risk_pct",
    ]
    assert payload["deduplicated"] is False
    child = payload["version"]
    assert child["status"] == "draft"
    assert child["based_on"] == parent_id
    assert child["strategy_id"] != parent_id

    child_mapping = yaml.safe_load(child["source_text"])
    assert child_mapping == _expected_derived_mapping(
        parent_source,
        parent_id=parent_id,
        patches={
            "regime.sep_mult.value": 65.0,
            "risk.sizing.risk_pct": 2.0,
        },
    )
    parse_strategy_document(child["source_text"])
    for path in payload["changed_paths"]:
        provenance = next(
            item for item in child_mapping["provenance"] if item["path"] == path
        )
        assert provenance == {
            "path": path,
            "source": "owner_explicit",
            "note": "Owner UI 微調",
        }

    assert retry.status_code == 200
    retry_payload = retry.json()
    assert retry_payload["deduplicated"] is True
    assert retry_payload["version"] == child
    assert store.list_versions() == [child, parent_record]
    assert parent_path.read_bytes() == parent_bytes
    assert store.get(parent_id) == parent_record


@pytest.mark.asyncio
async def test_derive_publish_failure_keeps_parent_and_inventory_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    parent_id, _ = _import_parent(store)
    before = _file_inventory(store.root)

    def fail_publish(_store: StrategyStore, _record: dict[str, Any]) -> None:
        raise OSError("injected C:/secret/derive publication failure")

    monkeypatch.setattr(StrategyStore, "_write", fail_publish)
    transport = httpx.ASGITransport(app=app)
    with _api_dependencies(store, _empty_catalog(tmp_path)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/strategies/{parent_id}/derive",
                json={
                    "patches": [
                        {"path": "regime.sep_mult.value", "value": 60},
                    ]
                },
            )

    assert response.status_code == 503
    assert response.json()["detail"] == "策略儲存暫時不可用；未有建立新版本"
    assert "C:/secret" not in response.text
    assert _file_inventory(store.root) == before
    assert not list(store.root.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_derive_rejects_every_non_numeric_or_unauthorized_change_without_writes(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    parent_id, _ = _import_parent(store)
    catalog = _empty_catalog(tmp_path)
    transport = httpx.ASGITransport(app=app)
    invalid_requests: list[dict[str, Any]] = [
        {"patches": []},
        {
            "patches": [
                {"path": "regime.sep_mult.value", "value": 60},
                {"path": "regime.sep_mult.value", "value": 65},
            ]
        },
        {"patches": [{"path": "", "value": 60}]},
        {"patches": [{"path": " regime.sep_mult.value", "value": 60}]},
        {"patches": [{"path": "regime.sep_mult.value ", "value": 60}]},
        {"patches": [{"path": "regime.sep_mult.value", "value": 50}]},
        {"patches": [{"path": "regime.unknown.value", "value": 60}]},
        {"patches": [{"path": "structures.pb_mid.touch", "value": 90}]},
        {"patches": [{"path": "meta.based_on", "value": 9}]},
        {"patches": [{"path": "universe.contracts", "value": 9}]},
        {"patches": [{"path": "entry.sequence.0", "value": 9}]},
        {"patches": [{"path": "indicators.atr.period", "value": 15}]},
        {
            "patches": [{"path": "regime.sep_mult.value", "value": 60}],
            "source_text": "schema: strategy.v1",
        },
        {
            "patches": [
                {
                    "path": "regime.sep_mult.value",
                    "value": 60,
                    "note": "client controls provenance",
                }
            ]
        },
    ]
    invalid_values: list[Any] = [
        True,
        False,
        "60",
        None,
        [60],
        {"value": 60},
    ]
    before = _file_inventory(store.root)

    with _api_dependencies(store, catalog):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = [
                await client.post(
                    f"/api/v1/strategies/{parent_id}/derive",
                    json=request,
                )
                for request in invalid_requests
            ]
            responses.extend(
                [
                    await client.post(
                        f"/api/v1/strategies/{parent_id}/derive",
                        json={
                            "patches": [
                                {
                                    "path": "regime.sep_mult.value",
                                    "value": value,
                                }
                            ]
                        },
                    )
                    for value in invalid_values
                ]
            )
            for constant in ("NaN", "Infinity", "-Infinity"):
                responses.append(
                    await client.post(
                        f"/api/v1/strategies/{parent_id}/derive",
                        content=(
                            '{"patches":[{"path":"regime.sep_mult.value",'
                            f'"value":{constant}}}]'
                        ),
                        headers={"Content-Type": "application/json"},
                    )
                )

    assert responses
    assert all(response.status_code == 422 for response in responses)
    assert _file_inventory(store.root) == before
    assert not list(store.root.rglob("*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("indicators.ema_fast.period", 90),
        ("risk.target.value", 2),
        ("risk.daily_loss_limit_r", 4),
    ],
)
async def test_derive_uses_the_parent_catalog_for_each_numeric_path_shape(
    tmp_path: Path,
    path: str,
    value: int,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    parent_id, _ = _import_parent(store)
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, _empty_catalog(tmp_path)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/strategies/{parent_id}/derive",
                json={"patches": [{"path": path, "value": value}]},
            )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["changed_paths"] == [path]
    mapping = yaml.safe_load(payload["version"]["source_text"])
    assert _path_from_mapping(mapping, path) == value
    provenance = next(item for item in mapping["provenance"] if item["path"] == path)
    assert provenance["source"] == "owner_explicit"
    assert provenance["note"] == "Owner UI 微調"


def _path_from_mapping(mapping: dict[str, Any], path: str) -> Any:
    current: Any = mapping
    for part in path.split("."):
        if isinstance(current, list):
            current = next(
                item
                for item in current
                if isinstance(item, dict) and item.get("id") == part
            )
        else:
            current = current[part]
    return current


@pytest.mark.asyncio
async def test_derive_rejects_legacy_parent_at_canonical_lineage_gate(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    parent_path, _ = _write_legacy_record(store)
    before = _file_inventory(store.root)
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, _empty_catalog(tmp_path)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/strategies/strategy-0001/derive",
                json={
                    "patches": [
                        {"path": "regime.sep_mult.value", "value": 60},
                    ]
                },
            )

    assert response.status_code == 422
    assert "meta.based_on_sketch" in response.text
    assert _file_inventory(store.root) == before
    assert parent_path.is_file()


@pytest.mark.asyncio
async def test_concurrent_derives_allocate_unique_monotonic_ids(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    parent_id, _ = _import_parent(store)
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, _empty_catalog(tmp_path)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.gather(
                *(
                    client.post(
                        f"/api/v1/strategies/{parent_id}/derive",
                        json={
                            "patches": [
                                {"path": "regime.sep_mult.value", "value": value},
                            ]
                        },
                    )
                    for value in (51, 52, 53, 54, 55, 56)
                )
            )

    assert all(response.status_code == 200 for response in responses)
    ids = sorted(response.json()["version"]["strategy_id"] for response in responses)
    assert ids == [f"strategy-{number:04d}" for number in range(2, 8)]
    assert len(store.list_versions()) == 7
    assert not list(store.root.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_delete_known_zero_archives_losslessly_hides_routes_and_never_reuses_id(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    strategy_id, source_text = _import_parent(store)
    active_path = store.root / f"{strategy_id}.json"
    active_record = store.get(strategy_id)
    expected_digest = sha256(source_text.encode("utf-8")).hexdigest()
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, _empty_catalog(tmp_path)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            deleted = await client.delete(f"/api/v1/strategies/{strategy_id}")
            listed = await client.get("/api/v1/strategies")
            detailed = await client.get(f"/api/v1/strategies/{strategy_id}")
            confirmed = await client.post(f"/api/v1/strategies/{strategy_id}/confirm")
            derived = await client.post(
                f"/api/v1/strategies/{strategy_id}/derive",
                json={
                    "patches": [
                        {"path": "regime.sep_mult.value", "value": 60},
                    ]
                },
            )

    assert deleted.status_code == 200, deleted.text
    payload = deleted.json()
    assert set(payload) == {
        "schema",
        "strategy_id",
        "deleted_at",
        "archived_status",
        "archived_to",
    }
    assert payload == {
        "schema": "strategy_delete.v1",
        "strategy_id": strategy_id,
        "deleted_at": payload["deleted_at"],
        "archived_status": active_record["status"],
        "archived_to": f"data/strategies/_deleted/{strategy_id}.yaml",
    }
    assert payload["deleted_at"].endswith("Z")
    datetime.fromisoformat(payload["deleted_at"].replace("Z", "+00:00"))

    archive_path = store.root / "_deleted" / f"{strategy_id}.yaml"
    archive = yaml.safe_load(archive_path.read_text(encoding="utf-8"))
    assert archive == {
        "schema": "strategy_delete_archive.v1",
        "strategy_id": strategy_id,
        "deleted_at": payload["deleted_at"],
        "status": active_record["status"],
        "content_sha256": expected_digest,
        "source_text": source_text,
    }
    assert sha256(archive["source_text"].encode("utf-8")).hexdigest() == archive[
        "content_sha256"
    ]
    assert not active_path.exists()
    assert listed.status_code == 200
    assert listed.json()["versions"] == []
    assert detailed.status_code == confirmed.status_code == derived.status_code == 404

    next_source = source_text.replace(
        "name: Trend 回踩 18EMA · p50 閘",
        "name: Trend 回踩 18EMA · p50 閘 next",
    )
    next_record = store.import_document(next_source).record
    assert next_record["strategy_id"] == "strategy-0002"
    assert archive_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_validation_only_reference_does_not_block_delete(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    strategy_id, _ = _import_parent(store)
    catalog = _catalog(
        tmp_path,
        _catalog_run(
            strategy_id,
            run_id="validation-001",
            validation_run=True,
        ),
    )
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, catalog):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 200
    assert not (store.root / f"{strategy_id}.json").exists()
    assert (store.root / "_deleted" / f"{strategy_id}.yaml").is_file()


@pytest.mark.asyncio
async def test_standard_reference_blocks_delete_with_exact_count_and_ids(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    strategy_id, _ = _import_parent(store)
    catalog = _catalog(
        tmp_path,
        _catalog_run(strategy_id, run_id="run-002", validation_run=False),
        _catalog_run(strategy_id, run_id="run-001", validation_run=False),
        _catalog_run(strategy_id, run_id="validation-001", validation_run=True),
    )
    before = _file_inventory(store.root)
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, catalog):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "schema": "strategy_delete_blocked.v1",
        "message": "呢個策略版本仍有 standard run 引用，未能刪除",
        "standard_run_count": 2,
        "run_ids": ["run-001", "run-002"],
    }
    assert _file_inventory(store.root) == before


@pytest.mark.asyncio
async def test_unknown_reference_candidate_fails_closed_without_writes(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    strategy_id, _ = _import_parent(store)
    catalog = _catalog(
        tmp_path,
        _catalog_run(
            strategy_id,
            run_id="unindexed-001",
            validation_run=False,
            symbol=None,
        ),
    )
    before = _file_inventory(store.root)
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, catalog):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "未能證實 standard run 引用數量；請先修復 run reference index"
    )
    assert _file_inventory(store.root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "secret"),
    [
        (
            RunReferenceMigrationRequired(
                "run C:/secret/runs.sqlite3 migrate-run-index"
            ),
            "C:/secret",
        ),
        (
            RunIndexIntegrityError("proof broke at C:/secret/runs.sqlite3"),
            "C:/secret",
        ),
        (OSError("lookup exploded at C:/secret/runs.sqlite3"), "C:/secret"),
    ],
)
async def test_reference_dependency_failures_are_sanitized_503_and_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    secret: str,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    strategy_id, _ = _import_parent(store)
    before = _file_inventory(store.root)

    def fail_lookup(_request: object) -> RunReferenceCatalog:
        raise error

    monkeypatch.setattr(
        "futures_research.api.routes_strategies.run_reference_catalog_for_request",
        fail_lookup,
    )
    transport = httpx.ASGITransport(app=app)
    with _api_dependencies(store, None):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 503
    assert response.json()["detail"] == "run reference lookup 暫時不可用；未有刪除任何策略"
    assert secret not in response.text
    assert "Traceback" not in response.text
    assert _file_inventory(store.root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["migration", "proof"])
async def test_real_temp_run_reference_migration_and_proof_failures_return_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    """Exercise the actual read-only catalog loader, never a fake known-zero result."""
    store = StrategyStore(root=tmp_path / "strategies")
    strategy_id, _ = _import_parent(store)
    database = tmp_path / f"{failure_kind}.sqlite3"
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    SqliteRunStore(database).backfill_run_indexes(registry=registry)
    with sqlite3.connect(database) as connection:
        if failure_kind == "migration":
            connection.executescript(
                """
                DROP TABLE run_index_proof;
                DROP TABLE run_trading_dates;
                DROP TABLE run_lookup;
                """
            )
        else:
            connection.execute(
                "UPDATE run_index_proof SET publication_sha256 = ? WHERE proof_id = 1",
                ("0" * 64,),
            )
        connection.commit()
    database_before = database.read_bytes()
    strategy_before = _file_inventory(store.root)

    def load_real_catalog(_request: object) -> RunReferenceCatalog:
        return RunReferenceCatalog.from_sources(
            main_database=database,
            audit_database=None,
            registry=registry,
        )

    monkeypatch.setattr(
        "futures_research.api.routes_strategies.run_reference_catalog_for_request",
        load_real_catalog,
    )
    transport = httpx.ASGITransport(app=app)
    with _api_dependencies(store, None):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 503
    assert response.json()["detail"] == "run reference lookup 暫時不可用；未有刪除任何策略"
    assert database.read_bytes() == database_before
    assert _file_inventory(store.root) == strategy_before


@pytest.mark.asyncio
async def test_archive_collision_never_overwrites_existing_bytes(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    strategy_id, _ = _import_parent(store)
    archive_path = store.root / "_deleted" / f"{strategy_id}.yaml"
    archive_path.parent.mkdir()
    archive_path.write_bytes(b"existing archive sentinel\n")
    before = _file_inventory(store.root)
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, _empty_catalog(tmp_path)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == "策略歸檔已存在；未有覆蓋或刪除任何檔案"
    assert _file_inventory(store.root) == before
    assert archive_path.read_bytes() == b"existing archive sentinel\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_method",
    [
        "_write_archive_temp",
        "_publish_archive",
        "_remove_active",
    ],
)
async def test_archive_faults_leave_active_and_visible_archive_inventories_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_method: str,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    strategy_id, _ = _import_parent(store)
    before = _file_inventory(store.root)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected C:/secret/archive failure")

    monkeypatch.setattr(StrategyStore, fault_method, fail)
    transport = httpx.ASGITransport(app=app)
    with _api_dependencies(store, _empty_catalog(tmp_path)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 503
    assert response.json()["detail"] == "策略歸檔暫時不可用；原版本仍然保留"
    assert "C:/secret" not in response.text
    assert "Traceback" not in response.text
    assert _file_inventory(store.root) == before
    assert (store.root / f"{strategy_id}.json").is_file()
    assert not (store.root / "_deleted" / f"{strategy_id}.yaml").exists()
    assert not (store.root / "_deleted").exists()
    assert not list(store.root.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_delete_fails_closed_for_digest_drift_without_archive(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    strategy_id, _ = _import_parent(store)
    active_path = store.root / f"{strategy_id}.json"
    record = json.loads(active_path.read_text(encoding="utf-8"))
    record["content_sha256"] = "0" * 64
    active_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    before = _file_inventory(store.root)
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, _empty_catalog(tmp_path)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 503
    assert response.json()["detail"] == "策略儲存完整性檢查失敗；未有刪除任何檔案"
    assert _file_inventory(store.root) == before
    assert not (store.root / "_deleted" / f"{strategy_id}.yaml").exists()


@pytest.mark.asyncio
async def test_legacy_source_can_be_archived_without_lineage_backfill(
    tmp_path: Path,
) -> None:
    store = StrategyStore(root=tmp_path / "strategies")
    active_path, source_text = _write_legacy_record(store)
    before_source_hash = sha256(source_text.encode("utf-8")).hexdigest()
    transport = httpx.ASGITransport(app=app)

    with _api_dependencies(store, _empty_catalog(tmp_path)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete("/api/v1/strategies/strategy-0001")

    assert response.status_code == 200
    archive_path = store.root / "_deleted" / "strategy-0001.yaml"
    archive = yaml.safe_load(archive_path.read_text(encoding="utf-8"))
    assert archive["source_text"] == source_text
    assert archive["content_sha256"] == before_source_hash
    assert "based_on_sketch" not in archive["source_text"]
    assert not active_path.exists()
