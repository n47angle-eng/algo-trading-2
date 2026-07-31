"""Registered FastAPI paper runtime routes (real ASGI app)."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from futures_research.api.main import app
from futures_research.api.paper_runtime import (
    PaperTraderCreateRequestV2,
    StrategyTimeframeProfileWire,
)
from futures_research.paper.models import BaselineMember
from futures_research.paper.store import PaperRuntimeStore

NOW = datetime(2026, 7, 31, 13, 30, tzinfo=UTC)
STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
BASELINE_RESULT = b'{"schema":"backtest_result.v1"}'
BASELINE_SHA = sha256(BASELINE_RESULT).hexdigest()


@pytest.fixture
def runtime_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PaperRuntimeStore:
    path = tmp_path / "runtime.sqlite3"
    store = PaperRuntimeStore(path)
    store.initialize()

    def _open(request=None, *, create: bool = True) -> PaperRuntimeStore:  # noqa: ANN001
        del request, create
        return store

    monkeypatch.setattr(
        "futures_research.api.deps.open_paper_runtime_store",
        _open,
    )
    # routes import open via deps inside helpers
    monkeypatch.setattr(
        "futures_research.api.routes_paper._runtime_store",
        _open,
    )
    return store


def _seed_trader(store: PaperRuntimeStore) -> tuple[str, str, int]:
    request_id = str(uuid4())
    body = PaperTraderCreateRequestV2.model_validate(
        {
            "schema": "paper_trader_create_request.v2",
            "request_id": request_id,
            "selection": {
                "strategy_id": "strategy-0003",
                "content_sha256": STRATEGY_SHA,
                "contract_id": "NQ-202609-CME",
                "baseline_run_id": "baseline-test",
                "baseline_result_sha256": BASELINE_SHA,
                "timeframes": {
                    "market_input": "1m",
                    "execution": "1m",
                    "chart_display": "30m",
                },
            },
        }
    )
    run_id = body.selection.baseline_run_id
    members = tuple(
        BaselineMember.from_bytes(path, content)
        for path, content in (
            ("baseline/result.json", BASELINE_RESULT),
            (f"baseline/trades/{run_id}.json", b'{"trades":[]}'),
            (f"baseline/equity/{run_id}.json", b'{"equity":[]}'),
            (f"baseline/events/{run_id}.json", b'{"events":[]}'),
        )
    )
    trader, _ = store.create_trader(
        body=body,
        strategy_profile=StrategyTimeframeProfileWire.model_validate(
            {
                "bias": "D",
                "mid": "1H",
                "entry": "5m",
                "source": "strategy.v1",
                "client_override": False,
            }
        ),
        initial_capital=100_000,
        baseline_members=members,
        now=NOW,
    )
    return trader.trader_id, trader.selection_fingerprint, trader.lifecycle_version


def test_runtime_capabilities_route() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/paper/runtime-capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "paper_runtime_capabilities.v1"
    assert body["timeframes"]["market_input"]["enabled"] == ["1m"]
    assert body["safety_defaults"]["max_drawdown_r"] == 8


def test_runtime_snapshot_and_start_command(runtime_store: PaperRuntimeStore) -> None:
    trader_id, fingerprint, version = _seed_trader(runtime_store)
    client = TestClient(app)

    snap = client.get(f"/api/v1/paper/traders/{trader_id}/runtime")
    assert snap.status_code == 200
    assert snap.json()["schema"] == "paper_runtime_snapshot.v1"
    assert snap.json()["lifecycle"] == "provisioned"
    assert snap.json()["selection_fingerprint"] == fingerprint

    start = client.post(
        f"/api/v1/paper/traders/{trader_id}/runtime/start",
        json={
            "request_id": str(uuid4()),
            "expected_lifecycle_version": version,
            "selection_fingerprint": fingerprint,
        },
    )
    assert start.status_code == 200, start.text
    assert start.json()["lifecycle"] == "running"

    snap2 = client.get(f"/api/v1/paper/traders/{trader_id}/runtime")
    assert snap2.json()["lifecycle"] == "running"


def test_review_v2_route_returns_zip(runtime_store: PaperRuntimeStore) -> None:
    trader_id, _fingerprint, _version = _seed_trader(runtime_store)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/paper/traders/{trader_id}/review-v2",
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["x-paper-review-schema"] == "paper-review.v2"
    assert response.content[:2] == b"PK"


def test_health_still_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_trade_lesson_route_empty_position(runtime_store: PaperRuntimeStore) -> None:
    trader_id, _fingerprint, _version = _seed_trader(runtime_store)
    client = TestClient(app)
    response = client.get(f"/api/v1/paper/traders/{trader_id}/trade-lesson")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "paper_trade_lesson.v1"
    assert body["trader_id"] == trader_id
    assert body["selection"]["contract_id"] == "NQ-202609-CME"
    assert body["open_position"] is None
    assert isinstance(body["bars"], list)
    assert isinstance(body["markers"], list)
    assert isinstance(body["conditions"], list)
    assert isinstance(body["fills"], list)
    assert isinstance(body["trades"], list)
    assert body["teaching"]["headline"]
    assert body["teaching"]["tips"]
    assert body["replay"]["supported"] is True
    assert body["counts"]["bar_count"] == len(body["bars"])


def test_trade_lesson_route_after_demo_replay(
    runtime_store: PaperRuntimeStore,
) -> None:
    trader_id, fingerprint, version = _seed_trader(runtime_store)
    client = TestClient(app)
    start = client.post(
        f"/api/v1/paper/traders/{trader_id}/runtime/start",
        json={
            "request_id": str(uuid4()),
            "expected_lifecycle_version": version,
            "selection_fingerprint": fingerprint,
        },
    )
    assert start.status_code == 200, start.text
    replay = client.post(
        f"/api/v1/paper/traders/{trader_id}/runtime/replay",
        json={"use_demo_bars": True, "mode": "replay_test"},
    )
    assert replay.status_code == 200, replay.text
    response = client.get(f"/api/v1/paper/traders/{trader_id}/trade-lesson")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "paper_trade_lesson.v1"
    assert body["counts"]["bar_count"] >= 1 or body["counts"]["fill_count"] >= 0
    assert any(tip["id"] for tip in body["teaching"]["tips"])
