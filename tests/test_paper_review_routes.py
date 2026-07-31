"""Registered P6 Stage B review HTTP contract tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import futures_research.api.paper_review_artifact as artifact_module
from futures_research.api.main import app
from futures_research.api.paper_provisioning import (
    OneOffPaperProvisioningAuthorizationPolicy,
)
from futures_research.api.paper_review import (
    PaperReviewBuilderRegistry,
    PaperReviewCreateRequest,
    PaperReviewService,
)
from futures_research.api.paper_review_artifact import (
    PaperReviewArtifactBuilder,
)
from futures_research.api.paper_traders import (
    IsolatedPaperExternalReadinessProvider,
    PaperTrader,
    PaperTraderCreateRequest,
    PaperTraderStore,
    create_paper_trader,
)
from futures_research.api.promotion_decisions import (
    PromotionDecisionStore,
    source_from_result_snapshot,
)
from futures_research.api.results_catalog import ResultsCatalog
from futures_research.api.routes_paper import router
from futures_research.data.contracts import ContractRegistry

_REVIEW_ROUTES = {
    (
        "/api/v1/paper/traders/{trader_id}/ledger-origin",
        "GET",
    ),
    (
        "/api/v1/paper/traders/{trader_id}/review-snapshots",
        "POST",
    ),
    (
        "/api/v1/paper/review-requests/{request_id}",
        "GET",
    ),
    (
        "/api/v1/paper/review-snapshots/{snapshot_id}/download",
        "GET",
    ),
    (
        "/api/v1/paper/review-snapshots/{snapshot_id}/terminal-opener",
        "GET",
    ),
}
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_RESULTS_ROOT = _PROJECT_ROOT / "data" / "backtests" / "results"
_RUN_ID = "nq-20260728-standard-365adf"
_RESULT_SHA = (
    "5edc3cf2e5d314ddc2594f57cfbc52987e35b855d804c147f86d7e6e3cf486f7"
)
_STRATEGY_SHA = (
    "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
)
_TRADER_CREATED_AT = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
_CAPTURED_AT = datetime(2026, 7, 29, 1, 2, 3, 4, tzinfo=UTC)
_READY_AT = datetime(2026, 7, 29, 2, 3, 4, 5, tzinfo=UTC)
_REQUEST_ID = "00000000-0000-4000-8000-000000000101"
_SNAPSHOT_ID = "paper-review-00000000000040008000000000000301"
_BUILDER_ID = "00000000-0000-4000-8000-000000000201"
_REVIEW_TABLES = (
    "paper_review_requests",
    "paper_review_status_events",
    "paper_review_ready_artifacts",
    "paper_review_failures",
)


class _CountingBuilder(PaperReviewArtifactBuilder):
    def __init__(
        self,
        service: PaperReviewService,
        *,
        artifact_root: Path,
        completion_clock: Callable[[], datetime],
    ) -> None:
        super().__init__(
            service,
            artifact_root=artifact_root,
            completion_clock=completion_clock,
        )
        self.build_calls = 0

    def build(self, request_id: str) -> object:
        self.build_calls += 1
        return super().build(request_id)


@dataclass(frozen=True, slots=True)
class _Context:
    app: FastAPI
    client: TestClient
    store: PaperTraderStore
    service: PaperReviewService
    builder: _CountingBuilder
    artifact_root: Path
    trader: PaperTrader
    catalog: ResultsCatalog
    registry: ContractRegistry
    decisions: PromotionDecisionStore
    factory_calls: dict[str, int]


def _ids(prefix: str, start: int) -> Iterator[str]:
    for value in range(start, start + 100):
        yield f"{prefix}{value:032x}"


def _create_trader_body(request_id: str) -> PaperTraderCreateRequest:
    return PaperTraderCreateRequest(
        schema="paper_trader_create_request.v1",
        request_id=request_id,
        selection={
            "strategy_id": "strategy-0003",
            "content_sha256": _STRATEGY_SHA,
            "contract_id": "NQ-202609-CME",
            "baseline_run_id": _RUN_ID,
            "baseline_result_sha256": _RESULT_SHA,
        },
    )


def _provision_trader(
    *,
    store: PaperTraderStore,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    decisions: PromotionDecisionStore,
    request_id: str,
) -> PaperTrader:
    body = _create_trader_body(request_id)
    trader, created = create_paper_trader(
        body=body,
        catalog=catalog,
        registry=registry,
        eligibility_store=decisions,
        readiness_provider=IsolatedPaperExternalReadinessProvider(
            checked_at=_TRADER_CREATED_AT
        ),
        authorization_policy=OneOffPaperProvisioningAuthorizationPolicy(
            operation_id=f"paper-provision-{request_id.replace('-', '')}",
            authorized_selection=body.selection,
            authorized_at=_TRADER_CREATED_AT,
            clock=lambda: _TRADER_CREATED_AT,
        ),
        trader_store=store,
    )
    assert created is True
    return trader


def _review_body(request_id: str = _REQUEST_ID) -> dict[str, str]:
    return {
        "schema": "paper_review_create_request.v1",
        "request_id": request_id,
    }


def _review_url(context: _Context) -> str:
    return (
        f"/api/v1/paper/traders/{context.trader.trader_id}/"
        "review-snapshots"
    )


def _review_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(
                f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed test names
            ).fetchone()[0]
            for table in _REVIEW_TABLES
        }


def _db_facts(path: Path) -> tuple[bytes, int, dict[str, int], tuple[bool, ...]]:
    return (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        _review_counts(path),
        tuple(
            Path(f"{path}{suffix}").exists()
            for suffix in ("-journal", "-wal", "-shm")
        ),
    )


def _artifact_facts(root: Path) -> dict[str, tuple[bytes, int]]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def _artifact_path(context: _Context) -> Path:
    with sqlite3.connect(context.store.path) as connection:
        row = connection.execute(
            "SELECT artifact_relpath FROM paper_review_ready_artifacts"
        ).fetchone()
    assert row is not None
    return context.artifact_root.joinpath(*str(row[0]).split("/"))


def _drop_mutate_restore(
    path: Path,
    trigger_names: tuple[str, ...],
    mutate: Callable[[sqlite3.Connection], None],
) -> None:
    with sqlite3.connect(path) as connection:
        placeholders = ",".join("?" for _name in trigger_names)
        triggers = connection.execute(
            f"""
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'trigger' AND name IN ({placeholders})
            ORDER BY name
            """,
            trigger_names,
        ).fetchall()
        assert len(triggers) == len(trigger_names)
        for name, _sql in triggers:
            connection.execute(f'DROP TRIGGER "{name}"')
        mutate(connection)
        for _name, sql in triggers:
            assert sql is not None
            connection.execute(sql)
        connection.commit()


def _assert_review_error(
    response: object,
    *,
    status: int,
    code: str,
    request_id: str | None,
    snapshot_id: str | None,
    retryable: bool = False,
) -> dict[str, object]:
    assert hasattr(response, "status_code")
    assert hasattr(response, "json")
    assert response.status_code == status
    payload = response.json()
    assert set(payload) == {"detail"}
    detail = payload["detail"]
    assert set(detail) == {
        "schema",
        "code",
        "message",
        "retryable",
        "request_id",
        "snapshot_id",
        "progress",
        "issues",
    }
    assert detail["schema"] == "paper_review_error.v1"
    assert detail["code"] == code
    assert detail["retryable"] is retryable
    assert detail["request_id"] == request_id
    assert detail["snapshot_id"] == snapshot_id
    assert isinstance(detail["message"], str) and detail["message"]
    assert isinstance(detail["issues"], list)
    for issue in detail["issues"]:
        assert set(issue) == {
            "kind",
            "path",
            "source_ref",
            "expected_sha256",
            "actual_sha256",
            "ref_chain",
        }
    if detail["progress"] is not None:
        assert set(detail["progress"]) == {
            "completed_parts",
            "total_parts",
            "current_part",
        }
    assert not response.content.startswith(b"PK")
    return detail


def _accept_without_build(context: _Context) -> object:
    status, created = context.service.accept_review_snapshot(
        trader_id=context.trader.trader_id,
        body=PaperReviewCreateRequest.model_validate(_review_body()),
    )
    assert created is True
    return status


def _create_ready(context: _Context) -> object:
    response = context.client.post(_review_url(context), json=_review_body())
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "ready"
    return response


@pytest.fixture
def context(tmp_path: Path) -> Iterator[_Context]:
    catalog = ResultsCatalog(_RESULTS_ROOT)
    registry = ContractRegistry.from_yaml(
        _PROJECT_ROOT / "config" / "contracts.yaml"
    )
    decisions = PromotionDecisionStore(
        tmp_path / "promotion.sqlite3",
        clock=lambda: _TRADER_CREATED_AT,
        decision_id_factory=lambda: "promotion-" + ("9" * 32),
    )
    snapshot = catalog.get_verified_snapshot(_RUN_ID)
    decisions.append(
        request_id="phase-four-route-eligibility",
        request_payload_sha256=sha256(
            b"phase-four-route-eligibility"
        ).hexdigest(),
        decision="use",
        reason="P6 Stage B Phase 4 temp authority",
        source=source_from_result_snapshot(
            snapshot,
            expected_run_id=_RUN_ID,
        ),
    )
    trader_ids = _ids("trader-", 1)
    account_ids = _ids("paper-account-", 101)
    ledger_ids = _ids("paper-ledger-", 201)
    store = PaperTraderStore(
        tmp_path / "paper" / "paper-traders.sqlite3",
        clock=lambda: _TRADER_CREATED_AT,
        trader_id_factory=lambda: next(trader_ids),
        account_id_factory=lambda: next(account_ids),
        ledger_origin_id_factory=lambda: next(ledger_ids),
    )
    trader = _provision_trader(
        store=store,
        catalog=catalog,
        registry=registry,
        decisions=decisions,
        request_id="00000000-0000-4000-8000-000000000001",
    )
    factory_calls = {
        "capture_clock": 0,
        "snapshot": 0,
        "builder_id": 0,
        "completion_clock": 0,
    }

    def capture_clock() -> datetime:
        factory_calls["capture_clock"] += 1
        return _CAPTURED_AT

    def snapshot_id() -> str:
        factory_calls["snapshot"] += 1
        return _SNAPSHOT_ID

    def builder_id() -> str:
        factory_calls["builder_id"] += 1
        return _BUILDER_ID

    def completion_clock() -> datetime:
        factory_calls["completion_clock"] += 1
        return _READY_AT

    service = PaperReviewService(
        store,
        clock=capture_clock,
        snapshot_id_factory=snapshot_id,
        builder_instance_id_factory=builder_id,
        builder_registry=PaperReviewBuilderRegistry(),
    )
    artifact_root = tmp_path / "artifacts"
    builder = _CountingBuilder(
        service,
        artifact_root=artifact_root,
        completion_clock=completion_clock,
    )
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.state.paper_trader_store = store
    test_app.state.paper_review_service = service
    test_app.state.paper_review_artifact_root = artifact_root
    test_app.state.paper_review_artifact_builder = builder
    with TestClient(test_app) as client:
        yield _Context(
            app=test_app,
            client=client,
            store=store,
            service=service,
            builder=builder,
            artifact_root=artifact_root,
            trader=trader,
            catalog=catalog,
            registry=registry,
            decisions=decisions,
            factory_calls=factory_calls,
        )


@contextmanager
def _production_client(context: _Context) -> Iterator[TestClient]:
    names = (
        "paper_trader_store",
        "paper_review_service",
        "paper_review_artifact_root",
        "paper_review_artifact_builder",
    )
    sentinel = object()
    previous = {
        name: getattr(app.state, name, sentinel)
        for name in names
    }
    app.state.paper_trader_store = context.store
    app.state.paper_review_service = context.service
    app.state.paper_review_artifact_root = context.artifact_root
    app.state.paper_review_artifact_builder = context.builder
    try:
        with TestClient(app) as client:
            yield client
    finally:
        for name, value in previous.items():
            if value is sentinel:
                delattr(app.state, name)
            else:
                setattr(app.state, name, value)


def test_review_routes_are_registered_exactly_once() -> None:
    registered = [
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if (route.path, method) in _REVIEW_ROUTES
    ]
    openapi = {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if (path, method.upper()) in _REVIEW_ROUTES
    }

    assert sorted(registered) == sorted(_REVIEW_ROUTES)
    assert openapi == _REVIEW_ROUTES


def test_ledger_route_returns_exact_domain_body_and_is_readonly(
    context: _Context,
) -> None:
    expected = context.service.ledger_origin(
        context.trader.trader_id
    ).model_dump(by_alias=True, mode="json")
    before_db = _db_facts(context.store.path)
    before_artifacts = _artifact_facts(context.artifact_root)

    response = context.client.get(
        f"/api/v1/paper/traders/{context.trader.trader_id}/ledger-origin"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == expected
    assert set(response.json()) == {
        "schema",
        "ledger_origin_id",
        "trader_id",
        "origin_at",
        "lifecycle",
        "strategy",
        "contract",
        "baseline",
        "account",
        "balances",
        "high_water_marks",
        "positions",
        "orders",
        "safety",
        "readiness_snapshot",
        "interpretation",
    }
    assert _db_facts(context.store.path) == before_db
    assert _artifact_facts(context.artifact_root) == before_artifacts


def test_ledger_route_maps_missing_trader_and_missing_store_without_creation(
    context: _Context,
    tmp_path: Path,
) -> None:
    missing_trader = "trader-" + ("f" * 32)
    response = context.client.get(
        f"/api/v1/paper/traders/{missing_trader}/ledger-origin"
    )
    _assert_review_error(
        response,
        status=404,
        code="trader_not_found",
        request_id=None,
        snapshot_id=None,
    )

    missing_path = tmp_path / "missing" / "paper.sqlite3"
    store = PaperTraderStore(missing_path)
    service = PaperReviewService(store)
    missing_app = FastAPI()
    missing_app.include_router(router)
    missing_app.state.paper_trader_store = store
    missing_app.state.paper_review_service = service
    missing_app.state.paper_review_artifact_root = tmp_path / "missing-artifacts"
    with TestClient(missing_app) as client:
        missing = client.get(
            f"/api/v1/paper/traders/{missing_trader}/ledger-origin"
        )

    _assert_review_error(
        missing,
        status=404,
        code="trader_not_found",
        request_id=None,
        snapshot_id=None,
    )
    assert not missing_path.exists()
    assert not missing_path.parent.exists()
    assert not (tmp_path / "missing-artifacts").exists()


def test_missing_post_does_not_create_injected_store_or_artifact_root(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "missing-post" / "paper.sqlite3"
    artifact_root = tmp_path / "missing-post-artifacts"
    store = PaperTraderStore(store_path)
    service = PaperReviewService(store)
    missing_app = FastAPI()
    missing_app.include_router(router)
    missing_app.state.paper_trader_store = store
    missing_app.state.paper_review_service = service
    missing_app.state.paper_review_artifact_root = artifact_root
    with TestClient(missing_app) as client:
        response = client.post(
            "/api/v1/paper/traders/trader-"
            + ("f" * 32)
            + "/review-snapshots",
            json=_review_body(),
        )

    _assert_review_error(
        response,
        status=404,
        code="trader_not_found",
        request_id=_REQUEST_ID,
        snapshot_id=None,
    )
    assert not store_path.exists()
    assert not store_path.parent.exists()
    assert not artifact_root.exists()


def test_ledger_route_maps_missing_ledger_and_integrity_drift(
    context: _Context,
) -> None:
    _drop_mutate_restore(
        context.store.path,
        (
            "paper_ledger_baseline_members_cannot_be_deleted",
            "paper_ledger_equity_points_cannot_be_deleted",
            "paper_ledger_origins_cannot_be_deleted",
        ),
        lambda connection: (
            connection.execute("DELETE FROM paper_ledger_baseline_members"),
            connection.execute("DELETE FROM paper_ledger_equity_points"),
            connection.execute("DELETE FROM paper_ledger_origins"),
        ),
    )

    missing = context.client.get(
        f"/api/v1/paper/traders/{context.trader.trader_id}/ledger-origin"
    )

    _assert_review_error(
        missing,
        status=409,
        code="ledger_not_ready",
        request_id=None,
        snapshot_id=None,
    )


def test_ledger_route_maps_persisted_drift_to_integrity_failure(
    context: _Context,
) -> None:
    _drop_mutate_restore(
        context.store.path,
        ("paper_ledger_origins_are_immutable",),
        lambda connection: connection.execute(
            "UPDATE paper_ledger_origins SET interpretation_json = '{}'"
        ),
    )

    response = context.client.get(
        f"/api/v1/paper/traders/{context.trader.trader_id}/ledger-origin"
    )

    _assert_review_error(
        response,
        status=503,
        code="ledger_integrity_failed",
        request_id=None,
        snapshot_id=None,
    )


@pytest.mark.parametrize("version", [0, 1, 2, 99])
def test_ledger_route_maps_non_v3_store_without_mutation(
    tmp_path: Path,
    version: int,
) -> None:
    path = tmp_path / f"non-v3-{version}.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy (value TEXT)")
        connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    store = PaperTraderStore(path)
    service = PaperReviewService(store)
    legacy_app = FastAPI()
    legacy_app.include_router(router)
    legacy_app.state.paper_trader_store = store
    legacy_app.state.paper_review_service = service
    legacy_app.state.paper_review_artifact_root = tmp_path / "artifacts"
    with TestClient(legacy_app) as client:
        response = client.get(
            "/api/v1/paper/traders/trader-"
            + ("f" * 32)
            + "/ledger-origin"
        )

    _assert_review_error(
        response,
        status=503,
        code="store_schema_upgrade_required",
        request_id=None,
        snapshot_id=None,
    )
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not (tmp_path / "artifacts").exists()


def test_first_post_builds_exact_once_then_ready_replay_is_readonly(
    context: _Context,
) -> None:
    first = context.client.post(_review_url(context), json=_review_body())
    assert first.status_code == 201, first.text
    payload = first.json()
    assert payload["status"] == "ready"
    assert payload["request_id"] == _REQUEST_ID
    assert payload["snapshot_id"] == _SNAPSHOT_ID
    assert payload["trader_id"] == context.trader.trader_id
    assert payload["ready"]["member_count"] == 10
    assert len(payload["ready"]["members"]) == 10
    assert context.builder.build_calls == 1
    assert context.factory_calls == {
        "capture_clock": 1,
        "snapshot": 1,
        "builder_id": 1,
        "completion_clock": 1,
    }
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 1,
        "paper_review_failures": 0,
    }
    assert len(list(context.artifact_root.rglob("*.zip"))) == 1
    before_db = _db_facts(context.store.path)
    before_artifacts = _artifact_facts(context.artifact_root)

    replay = context.client.post(_review_url(context), json=_review_body())

    assert replay.status_code == 200
    assert replay.content == first.content
    assert context.builder.build_calls == 1
    assert context.factory_calls == {
        "capture_clock": 1,
        "snapshot": 1,
        "builder_id": 1,
        "completion_clock": 1,
    }
    assert _db_facts(context.store.path) == before_db
    assert _artifact_facts(context.artifact_root) == before_artifacts


def test_active_replay_is_202_same_identity_and_zero_builder(
    context: _Context,
) -> None:
    accepted = _accept_without_build(context)
    before = _db_facts(context.store.path)

    response = context.client.post(_review_url(context), json=_review_body())

    assert response.status_code == 202
    assert response.json() == accepted.model_dump(
        by_alias=True,
        mode="json",
    )
    assert response.json()["status"] == "preparing"
    assert context.builder.build_calls == 0
    assert _db_facts(context.store.path) == before
    assert not context.artifact_root.exists()


def test_failed_replay_returns_original_failure_without_builder_or_write(
    context: _Context,
) -> None:
    accepted = _accept_without_build(context)
    context.service._save_failure_for_test(
        request_id=_REQUEST_ID,
        http_status=503,
        error_payload={
            "schema": "paper_review_error.v1",
            "code": "artifact_build_failed",
            "message": "Synthetic persisted route failure.",
            "retryable": False,
            "request_id": _REQUEST_ID,
            "snapshot_id": accepted.snapshot_id,
            "progress": {
                "completed_parts": 3,
                "total_parts": 10,
                "current_part": None,
            },
            "issues": [],
        },
    )
    before = _db_facts(context.store.path)

    response = context.client.post(_review_url(context), json=_review_body())

    detail = _assert_review_error(
        response,
        status=503,
        code="artifact_build_failed",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert detail["message"] == "Synthetic persisted route failure."
    assert context.builder.build_calls == 0
    assert _db_facts(context.store.path) == before
    assert not context.artifact_root.exists()


def test_first_build_terminal_failure_returns_exact_outer_error(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        artifact_module.os,
        "link",
        lambda _source, _target: (_ for _ in ()).throw(
            OSError("injected publish failure")
        ),
    )

    response = context.client.post(_review_url(context), json=_review_body())

    detail = _assert_review_error(
        response,
        status=503,
        code="artifact_build_failed",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert detail["progress"] == {
        "completed_parts": 9,
        "total_parts": 10,
        "current_part": None,
    }
    assert context.builder.build_calls == 1
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 1,
    }


def test_request_id_conflict_is_zero_builder_and_zero_write(
    context: _Context,
) -> None:
    _accept_without_build(context)
    other = _provision_trader(
        store=context.store,
        catalog=context.catalog,
        registry=context.registry,
        decisions=context.decisions,
        request_id="00000000-0000-4000-8000-000000000002",
    )
    before = _db_facts(context.store.path)

    response = context.client.post(
        f"/api/v1/paper/traders/{other.trader_id}/review-snapshots",
        json=_review_body(),
    )

    _assert_review_error(
        response,
        status=409,
        code="request_id_conflict",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert context.builder.build_calls == 0
    assert _db_facts(context.store.path) == before


@pytest.mark.parametrize(
    "body",
    [
        {"schema": "paper_review_create_request.v2", "request_id": _REQUEST_ID},
        {"schema": "paper_review_create_request.v1"},
        {
            "schema": "paper_review_create_request.v1",
            "request_id": _REQUEST_ID,
            "snapshot_id": _SNAPSHOT_ID,
        },
        {
            "schema": "paper_review_create_request.v1",
            "request_id": "ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF",
        },
    ],
)
def test_post_invalid_body_is_framework_422_and_zero_write(
    context: _Context,
    body: dict[str, object],
) -> None:
    before = _db_facts(context.store.path)

    response = context.client.post(_review_url(context), json=body)

    assert response.status_code == 422
    assert context.builder.build_calls == 0
    assert _db_facts(context.store.path) == before
    assert not context.artifact_root.exists()


@pytest.mark.parametrize(
    ("url", "method"),
    [
        (
            "/api/v1/paper/traders/TRADER-"
            + ("a" * 32)
            + "/ledger-origin",
            "get",
        ),
        (
            "/api/v1/paper/review-requests/"
            "00000000-0000-1000-8000-000000000101",
            "get",
        ),
        (
            "/api/v1/paper/review-snapshots/paper-review-"
            + ("A" * 32)
            + "/download",
            "get",
        ),
        (
            "/api/v1/paper/review-snapshots/paper-review-"
            + ("a" * 31)
            + "/terminal-opener",
            "get",
        ),
    ],
)
def test_invalid_review_paths_are_framework_422(
    context: _Context,
    url: str,
    method: str,
) -> None:
    before = _db_facts(context.store.path)

    response = getattr(context.client, method)(url)

    assert response.status_code == 422
    assert _db_facts(context.store.path) == before
    assert not context.artifact_root.exists()


@pytest.mark.parametrize(
    ("state", "expected_status", "expected_error"),
    [
        ("preparing", "preparing", None),
        ("ready", "ready", None),
        ("failed", "failed", "artifact_build_failed"),
        ("interrupted", "failed", "build_interrupted"),
    ],
)
def test_status_get_projects_each_state_and_is_zero_write(
    context: _Context,
    state: str,
    expected_status: str,
    expected_error: str | None,
) -> None:
    if state == "ready":
        _create_ready(context)
    else:
        accepted = _accept_without_build(context)
        if state == "failed":
            context.service._save_failure_for_test(
                request_id=_REQUEST_ID,
                http_status=503,
                error_payload={
                    "schema": "paper_review_error.v1",
                    "code": "artifact_build_failed",
                    "message": "Persisted status failure.",
                    "retryable": False,
                    "request_id": _REQUEST_ID,
                    "snapshot_id": accepted.snapshot_id,
                    "progress": {
                        "completed_parts": 2,
                        "total_parts": 10,
                        "current_part": None,
                    },
                    "issues": [],
                },
            )
        elif state == "interrupted":
            context.service._deactivate_builder_for_test(_REQUEST_ID)
    before_db = _db_facts(context.store.path)
    before_artifacts = _artifact_facts(context.artifact_root)

    response = context.client.get(
        f"/api/v1/paper/review-requests/{_REQUEST_ID}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "schema",
        "request_id",
        "snapshot_id",
        "trader_id",
        "status",
        "captured_at",
        "progress",
        "ready",
        "error",
    }
    assert payload["status"] == expected_status
    assert payload["request_id"] == _REQUEST_ID
    assert payload["snapshot_id"] == _SNAPSHOT_ID
    if expected_error is None:
        assert payload["error"] is None
    else:
        assert payload["error"]["code"] == expected_error
        assert set(payload["error"]) == {
            "schema",
            "code",
            "message",
            "retryable",
            "request_id",
            "snapshot_id",
            "progress",
            "issues",
        }
    assert _db_facts(context.store.path) == before_db
    assert _artifact_facts(context.artifact_root) == before_artifacts


def test_missing_status_get_is_exact_error_and_zero_write(
    context: _Context,
) -> None:
    before = _db_facts(context.store.path)

    response = context.client.get(
        f"/api/v1/paper/review-requests/{_REQUEST_ID}"
    )

    _assert_review_error(
        response,
        status=404,
        code="request_not_found",
        request_id=_REQUEST_ID,
        snapshot_id=None,
    )
    assert _db_facts(context.store.path) == before
    assert not context.artifact_root.exists()


def test_download_captures_once_then_returns_exact_bytes_and_headers(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create_ready(context)
    ready = created.json()["ready"]
    artifact = _artifact_path(context)
    expected = artifact.read_bytes()
    before_db = _db_facts(context.store.path)
    before_artifacts = _artifact_facts(context.artifact_root)
    original_open = Path.open
    calls = 0

    def counted_open(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal calls
        if path == artifact:
            calls += 1
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/download"
    )

    assert response.status_code == 200
    assert response.content == expected
    assert response.content.startswith(b"PK")
    assert len(response.content) == ready["artifact_bytes"]
    assert sha256(response.content).hexdigest() == ready["artifact_sha256"]
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{ready["display_filename"]}"'
    )
    assert calls == 1
    assert _db_facts(context.store.path) == before_db
    assert _artifact_facts(context.artifact_root) == before_artifacts


def test_production_cors_exposes_content_disposition(
    context: _Context,
) -> None:
    created = _create_ready(context)
    expected_filename = created.json()["ready"]["display_filename"]
    with _production_client(context) as client:
        response = client.get(
            f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/download",
            headers={"Origin": "http://127.0.0.1:5173"},
        )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{expected_filename}"'
    )
    exposed = {
        value.strip().lower()
        for value in response.headers[
            "access-control-expose-headers"
        ].split(",")
    }
    assert exposed == {"content-disposition"}
    assert response.headers["access-control-allow-origin"] == (
        "http://127.0.0.1:5173"
    )


def test_download_missing_snapshot_is_exact_error_without_root_creation(
    context: _Context,
) -> None:
    missing = "paper-review-00000000000040008000000000000999"
    before = _db_facts(context.store.path)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{missing}/download"
    )

    _assert_review_error(
        response,
        status=404,
        code="snapshot_not_found",
        request_id=None,
        snapshot_id=missing,
    )
    assert _db_facts(context.store.path) == before
    assert not context.artifact_root.exists()


def test_download_preparing_is_retryable_and_zero_write(
    context: _Context,
) -> None:
    _accept_without_build(context)
    before = _db_facts(context.store.path)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/download"
    )

    detail = _assert_review_error(
        response,
        status=409,
        code="snapshot_not_ready",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
        retryable=True,
    )
    assert detail["progress"] == {
        "completed_parts": 0,
        "total_parts": 10,
        "current_part": None,
    }
    assert _db_facts(context.store.path) == before
    assert not context.artifact_root.exists()


def test_download_failed_snapshot_maps_to_snapshot_failed(
    context: _Context,
) -> None:
    accepted = _accept_without_build(context)
    context.service._save_failure_for_test(
        request_id=_REQUEST_ID,
        http_status=503,
        error_payload={
            "schema": "paper_review_error.v1",
            "code": "artifact_build_failed",
            "message": "Persisted download failure.",
            "retryable": False,
            "request_id": _REQUEST_ID,
            "snapshot_id": accepted.snapshot_id,
            "progress": {
                "completed_parts": 4,
                "total_parts": 10,
                "current_part": None,
            },
            "issues": [],
        },
    )
    before = _db_facts(context.store.path)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/download"
    )

    _assert_review_error(
        response,
        status=409,
        code="snapshot_failed",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert _db_facts(context.store.path) == before
    assert not context.artifact_root.exists()


@pytest.mark.parametrize("corruption", ["missing", "length", "sha"])
def test_download_file_absent_or_drift_is_artifact_unavailable_and_readonly(
    context: _Context,
    corruption: str,
) -> None:
    _create_ready(context)
    artifact = _artifact_path(context)
    if corruption == "missing":
        artifact.unlink()
    elif corruption == "length":
        artifact.write_bytes(artifact.read_bytes() + b"x")
    else:
        payload = artifact.read_bytes()
        artifact.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    before_db = _db_facts(context.store.path)
    before_artifacts = _artifact_facts(context.artifact_root)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/download"
    )

    _assert_review_error(
        response,
        status=503,
        code="artifact_unavailable",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert _db_facts(context.store.path) == before_db
    assert _artifact_facts(context.artifact_root) == before_artifacts


def test_download_rejects_persisted_byte_count_drift(
    context: _Context,
) -> None:
    _create_ready(context)
    _drop_mutate_restore(
        context.store.path,
        ("paper_review_ready_artifacts_are_immutable",),
        lambda connection: connection.execute(
            """
            UPDATE paper_review_ready_artifacts
            SET artifact_bytes = artifact_bytes + 1
            """
        ),
    )
    before_db = _db_facts(context.store.path)
    before_artifacts = _artifact_facts(context.artifact_root)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/download"
    )

    _assert_review_error(
        response,
        status=503,
        code="artifact_unavailable",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert _db_facts(context.store.path) == before_db
    assert _artifact_facts(context.artifact_root) == before_artifacts


def test_download_unsafe_persisted_path_is_artifact_unavailable(
    context: _Context,
) -> None:
    _create_ready(context)
    artifact = _artifact_path(context)
    (context.artifact_root.parent / "escape.zip").write_bytes(
        artifact.read_bytes()
    )
    _drop_mutate_restore(
        context.store.path,
        ("paper_review_ready_artifacts_are_immutable",),
        lambda connection: connection.execute(
            """
            UPDATE paper_review_ready_artifacts
            SET artifact_relpath = '../escape.zip'
            """
        ),
    )
    before = _db_facts(context.store.path)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/download"
    )

    _assert_review_error(
        response,
        status=503,
        code="artifact_unavailable",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert _db_facts(context.store.path) == before


def test_download_ready_metadata_contradiction_is_integrity_failure(
    context: _Context,
) -> None:
    _create_ready(context)
    _drop_mutate_restore(
        context.store.path,
        ("paper_review_ready_artifacts_are_immutable",),
        lambda connection: connection.execute(
            """
            UPDATE paper_review_ready_artifacts
            SET terminal_opener_bytes = terminal_opener_bytes + 1
            """
        ),
    )
    before = _db_facts(context.store.path)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/download"
    )

    _assert_review_error(
        response,
        status=503,
        code="snapshot_integrity_failed",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert _db_facts(context.store.path) == before


def test_terminal_opener_returns_persisted_text_without_generator_or_artifact(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_ready(context)
    artifact = _artifact_path(context)
    artifact.unlink()
    with sqlite3.connect(context.store.path) as connection:
        row = connection.execute(
            """
            SELECT terminal_opener_text, terminal_opener_bytes,
                   terminal_opener_sha256
            FROM paper_review_ready_artifacts
            """
        ).fetchone()
    assert row is not None
    before_db = _db_facts(context.store.path)
    before_artifacts = _artifact_facts(context.artifact_root)
    monkeypatch.setattr(
        PaperReviewArtifactBuilder,
        "_terminal_opener",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("terminal opener was regenerated")
        ),
    )

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/terminal-opener"
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema": "paper_review_terminal_opener.v1",
        "snapshot_id": _SNAPSHOT_ID,
        "text": row[0],
        "bytes": row[1],
        "sha256": row[2],
    }
    assert len(response.json()["text"].encode("utf-8")) == row[1]
    assert sha256(response.json()["text"].encode("utf-8")).hexdigest() == row[2]
    assert _db_facts(context.store.path) == before_db
    assert _artifact_facts(context.artifact_root) == before_artifacts


@pytest.mark.parametrize("field", ["terminal_opener_bytes", "terminal_opener_sha256"])
def test_terminal_opener_wrong_bytes_or_sha_fails_closed(
    context: _Context,
    field: str,
) -> None:
    _create_ready(context)
    if field == "terminal_opener_bytes":
        statement = (
            "UPDATE paper_review_ready_artifacts "
            "SET terminal_opener_bytes = terminal_opener_bytes + 1"
        )
    else:
        statement = (
            "UPDATE paper_review_ready_artifacts "
            "SET terminal_opener_sha256 = '" + ("0" * 64) + "'"
        )
    _drop_mutate_restore(
        context.store.path,
        ("paper_review_ready_artifacts_are_immutable",),
        lambda connection: connection.execute(statement),
    )
    before = _db_facts(context.store.path)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/terminal-opener"
    )

    _assert_review_error(
        response,
        status=503,
        code="snapshot_integrity_failed",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert _db_facts(context.store.path) == before


@pytest.mark.parametrize(
    ("state", "status", "code", "retryable"),
    [
        ("missing", 404, "snapshot_not_found", False),
        ("preparing", 409, "snapshot_not_ready", True),
        ("failed", 409, "snapshot_failed", False),
    ],
)
def test_terminal_opener_nonready_state_mapping(
    context: _Context,
    state: str,
    status: int,
    code: str,
    retryable: bool,
) -> None:
    snapshot_id = _SNAPSHOT_ID
    request_id: str | None = _REQUEST_ID
    if state == "missing":
        snapshot_id = "paper-review-00000000000040008000000000000999"
        request_id = None
    else:
        accepted = _accept_without_build(context)
        if state == "failed":
            context.service._save_failure_for_test(
                request_id=_REQUEST_ID,
                http_status=503,
                error_payload={
                    "schema": "paper_review_error.v1",
                    "code": "artifact_build_failed",
                    "message": "Persisted opener failure.",
                    "retryable": False,
                    "request_id": _REQUEST_ID,
                    "snapshot_id": accepted.snapshot_id,
                    "progress": {
                        "completed_parts": 1,
                        "total_parts": 10,
                        "current_part": None,
                    },
                    "issues": [],
                },
            )
    before = _db_facts(context.store.path)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{snapshot_id}/terminal-opener"
    )

    _assert_review_error(
        response,
        status=status,
        code=code,
        request_id=request_id,
        snapshot_id=snapshot_id,
        retryable=retryable,
    )
    assert _db_facts(context.store.path) == before
    assert not context.artifact_root.exists()


def test_regex_valid_non_uuid_snapshot_is_404_without_invalid_error_identity(
    context: _Context,
) -> None:
    non_uuid = "paper-review-" + ("f" * 32)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{non_uuid}/download"
    )

    _assert_review_error(
        response,
        status=404,
        code="snapshot_not_found",
        request_id=None,
        snapshot_id=None,
    )


def test_unexpected_builder_exception_fails_closed_without_internal_leak(
    context: _Context,
) -> None:
    def fail(step: str) -> None:
        if step == "member_generation":
            raise RuntimeError(
                "SECRET-INTERNAL-PATH C:\\private\\review-artifact"
            )

    context.app.state.paper_review_artifact_builder = (
        PaperReviewArtifactBuilder(
            context.service,
            artifact_root=context.artifact_root,
            completion_clock=lambda: _READY_AT,
            step_hook=fail,
        )
    )

    response = context.client.post(_review_url(context), json=_review_body())

    detail = _assert_review_error(
        response,
        status=503,
        code="build_interrupted",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert "SECRET-INTERNAL-PATH" not in response.text
    assert "private" not in detail["message"]
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 1,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 0,
    }


def test_default_five_routes_do_not_lazy_create_store_or_artifact_root() -> None:
    default_root = _PROJECT_ROOT / "data" / "paper"
    assert not default_root.exists()
    default_app = FastAPI()
    default_app.include_router(router)
    trader_id = "trader-" + ("f" * 32)
    snapshot_id = "paper-review-00000000000040008000000000000999"
    with TestClient(default_app) as client:
        ledger = client.get(
            f"/api/v1/paper/traders/{trader_id}/ledger-origin"
        )
        post = client.post(
            f"/api/v1/paper/traders/{trader_id}/review-snapshots",
            json=_review_body(),
        )
        status = client.get(
            f"/api/v1/paper/review-requests/{_REQUEST_ID}"
        )
        download = client.get(
            f"/api/v1/paper/review-snapshots/{snapshot_id}/download"
        )
        opener = client.get(
            f"/api/v1/paper/review-snapshots/{snapshot_id}/terminal-opener"
        )

    _assert_review_error(
        ledger,
        status=404,
        code="trader_not_found",
        request_id=None,
        snapshot_id=None,
    )
    _assert_review_error(
        post,
        status=404,
        code="trader_not_found",
        request_id=_REQUEST_ID,
        snapshot_id=None,
    )
    _assert_review_error(
        status,
        status=404,
        code="request_not_found",
        request_id=_REQUEST_ID,
        snapshot_id=None,
    )
    _assert_review_error(
        download,
        status=404,
        code="snapshot_not_found",
        request_id=None,
        snapshot_id=snapshot_id,
    )
    _assert_review_error(
        opener,
        status=404,
        code="snapshot_not_found",
        request_id=None,
        snapshot_id=snapshot_id,
    )
    assert not default_root.exists()


def test_same_request_concurrency_builds_one_snapshot_and_one_artifact(
    context: _Context,
) -> None:
    def post(_index: int) -> object:
        return context.client.post(_review_url(context), json=_review_body())

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(post, range(16)))

    assert sum(response.status_code == 201 for response in responses) == 1
    assert {response.status_code for response in responses} <= {200, 201, 202}
    assert {
        response.json()["request_id"] for response in responses
    } == {_REQUEST_ID}
    assert {
        response.json()["snapshot_id"] for response in responses
    } == {_SNAPSHOT_ID}
    assert context.builder.build_calls == 1
    assert context.factory_calls == {
        "capture_clock": 1,
        "snapshot": 1,
        "builder_id": 1,
        "completion_clock": 1,
    }
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 1,
        "paper_review_failures": 0,
    }
    assert len(list(context.artifact_root.rglob("*.zip"))) == 1


def test_download_reparse_file_is_rejected_when_supported(
    context: _Context,
    tmp_path: Path,
) -> None:
    _create_ready(context)
    artifact = _artifact_path(context)
    external = tmp_path / "external.zip"
    external.write_bytes(artifact.read_bytes())
    artifact.unlink()
    try:
        artifact.symlink_to(external)
    except OSError:
        pytest.skip("file symlink creation is unavailable on this platform")
    before = _db_facts(context.store.path)

    response = context.client.get(
        f"/api/v1/paper/review-snapshots/{_SNAPSHOT_ID}/download"
    )

    _assert_review_error(
        response,
        status=503,
        code="artifact_unavailable",
        request_id=_REQUEST_ID,
        snapshot_id=_SNAPSHOT_ID,
    )
    assert _db_facts(context.store.path) == before
