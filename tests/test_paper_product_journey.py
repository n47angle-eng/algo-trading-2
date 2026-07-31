"""Product path: v4 create → start (real decisions) → replay bars → fill → review-v2."""

from __future__ import annotations

import io
import json
import zipfile
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
from futures_research.api.paper_runtime_manager import clear_manager_for_tests
from futures_research.paper.models import BaselineMember
from futures_research.paper.review_v2 import REVIEW_V2_MEMBER_ORDER
from futures_research.paper.store import PaperRuntimeStore, _derived_id

NOW = datetime(2026, 7, 31, 13, 30, tzinfo=UTC)
STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
BASELINE_RESULT = b'{"schema":"backtest_result.v1","run_id":"baseline-test"}'
BASELINE_SHA = sha256(BASELINE_RESULT).hexdigest()


@pytest.fixture
def runtime_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clear_manager_for_tests()
    path = tmp_path / "runtime.sqlite3"
    store = PaperRuntimeStore(path)
    store.initialize()

    def _open(request=None, *, create: bool = True) -> PaperRuntimeStore:  # noqa: ANN001
        del request, create
        return store

    monkeypatch.setattr("futures_research.api.deps.open_paper_runtime_store", _open)
    monkeypatch.setattr("futures_research.api.routes_paper._runtime_store", _open)
    yield TestClient(app), store
    clear_manager_for_tests()


def _seed(store: PaperRuntimeStore) -> tuple[str, str, int]:
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
                    "chart_display": "1m",
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
    assert trader.trader_id == _derived_id("trader", request_id)
    return trader.trader_id, trader.selection_fingerprint, trader.lifecycle_version


def test_product_http_start_replay_fill_review_v2(runtime_client) -> None:
    client, store = runtime_client
    trader_id, fingerprint, version = _seed(store)

    # Snapshot reachable for provisioned trader (same store identity).
    snap = client.get(f"/api/v1/paper/traders/{trader_id}/runtime")
    assert snap.status_code == 200, snap.text
    assert snap.json()["lifecycle"] == "provisioned"

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
    assert start.json()["decision_source"] == "product"

    # Product MD path: demo closed bars → real decision/fill (not NoSignal).
    replay = client.post(
        f"/api/v1/paper/traders/{trader_id}/runtime/replay",
        json={"use_demo_bars": True, "mode": "replay_test"},
    )
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["schema"] == "paper_runtime_replay_result.v1"
    assert body["snapshot"]["decision_count"] >= 1
    assert body["snapshot"]["trade_count"] >= 1
    assert body["snapshot"]["realized_pnl"] != 0

    timeline = client.get(f"/api/v1/paper/traders/{trader_id}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["schema"] == "paper_runtime_timeline.v1"
    assert timeline.json()["count"] >= 1

    chart = client.get(f"/api/v1/paper/traders/{trader_id}/chart")
    assert chart.status_code == 200
    assert chart.json()["schema"] == "paper_runtime_chart.v1"
    assert chart.json()["count"] >= 2

    review = client.post(
        f"/api/v1/paper/traders/{trader_id}/review-v2",
        json={"request_id": str(uuid4())},
    )
    assert review.status_code == 200, review.text
    assert review.headers["x-paper-review-schema"] == "paper-review.v2"
    with zipfile.ZipFile(io.BytesIO(review.content)) as zf:
        assert zf.namelist() == list(REVIEW_V2_MEMBER_ORDER)
        main = json.loads(zf.read("paper-review.json"))
        assert main["schema"] == "paper-review.v2"
        assert main["trader_id"] == trader_id
        assert main["selection"]["strategy_id"] == "strategy-0003"
        assert main["counts"]["trade_count"] >= 1


def test_stage_a_and_runtime_share_derived_trader_id() -> None:
    """Identity derivation must match so UI trader_id hits runtime routes."""
    request_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    assert _derived_id("trader", request_id).startswith("trader-")
    assert len(_derived_id("trader", request_id)) == 39
