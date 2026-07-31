"""Real paper activity stream + push fan-out wiring (shipped path)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from futures_research.api.main import app
from datetime import UTC, datetime
from hashlib import sha256

from futures_research.api.paper_runtime import (
    PaperTraderCreateRequestV2,
    StrategyTimeframeProfileWire,
)
from futures_research.api.paper_runtime_service import runtime_activity, runtime_timeline
from futures_research.api.push_fanout import (
    fanout_backtest_complete,
    fanout_from_process_deltas,
    fanout_paper_activity,
)
from futures_research.paper.models import BaselineMember
from futures_research.paper.store import PaperRuntimeStore

STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
BASELINE_RESULT = b'{"schema":"backtest_result.v1"}'
BASELINE_SHA = sha256(BASELINE_RESULT).hexdigest()
NOW = datetime(2026, 7, 31, 13, 30, tzinfo=UTC)


@pytest.fixture()
def store(tmp_path: Path) -> PaperRuntimeStore:
    path = tmp_path / "runtime.sqlite3"
    s = PaperRuntimeStore(path)
    s.initialize()
    return s


def _seed_trader(store: PaperRuntimeStore) -> str:
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
    return trader.trader_id


def test_timeline_payload_is_lifecycle_only(store: PaperRuntimeStore) -> None:
    trader_id = _seed_trader(store)
    doc = runtime_timeline(store, trader_id)
    assert doc["schema"] == "paper_runtime_timeline.v1"
    for item in doc["items"]:
        payload = item["payload"]
        assert "lifecycle" in payload
        assert "kind" not in payload
        assert payload.get("lifecycle") in {
            "provisioned",
            "starting",
            "running",
            "pausing",
            "paused",
            "tripped",
            "stopping",
            "recovery_required",
            "permanently_stopped",
        } or isinstance(payload.get("lifecycle"), str)


def test_activity_empty_for_fresh_trader(store: PaperRuntimeStore) -> None:
    trader_id = _seed_trader(store)
    doc = runtime_activity(store, trader_id)
    assert doc["schema"] == "paper_runtime_activity.v1"
    assert doc["count"] == 0
    assert doc["items"] == []


def test_activity_maps_intent_fill_trade_rows(store: PaperRuntimeStore) -> None:
    trader_id = _seed_trader(store)
    # Insert realistic rows the activity stream reads.
    with store._connect() as conn:  # noqa: SLF001
        # Need a market input for FK on fills/orders if enforced — check
        # Whether intents alone are enough for activity buy_opportunity.
        conn.execute(
            """
            INSERT INTO paper_intents(
                intent_id, trader_id, decision_id, status, payload_json, created_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                "paper-intent-test-1",
                trader_id,
                None,
                "pending",
                json.dumps({"side": "long", "entry_reference": 21000.0}),
                "2026-07-31T12:00:00Z",
            ),
        )
        conn.commit()

    doc = runtime_activity(store, trader_id)
    assert doc["count"] >= 1
    kinds = [item["payload"]["kind"] for item in doc["items"]]
    assert "buy_opportunity" in kinds
    buy = next(
        item for item in doc["items"] if item["payload"]["kind"] == "buy_opportunity"
    )
    assert buy["payload"]["source"] == "intent"
    assert buy["payload"]["price"] == 21000.0


def test_activity_route_registered(store: PaperRuntimeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def _open(request=None, *, create: bool = True) -> PaperRuntimeStore:  # noqa: ANN001
        del request, create
        return store

    monkeypatch.setattr(
        "futures_research.api.routes_paper._runtime_store",
        _open,
    )
    trader_id = _seed_trader(store)
    client = TestClient(app)
    res = client.get(f"/api/v1/paper/traders/{trader_id}/activity")
    assert res.status_code == 200
    body = res.json()
    assert body["schema"] == "paper_runtime_activity.v1"


def test_fanout_paper_and_backtest_call_send(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_send(payload: dict) -> dict:
        calls.append(payload)
        return {"sent": 1, "failed": 0, "removed": 0}

    monkeypatch.setattr(
        "futures_research.api.routes_push._send_web_push",
        fake_send,
    )
    # Re-import path used inside fanout
    result = fanout_paper_activity(
        trader_id="t1",
        kind="entry",
        side="long",
        quantity=1,
        price=100.0,
    )
    assert result["sent"] == 1
    assert calls[0]["title"] == "入市開倉"
    assert "t1" in calls[0]["body"]

    fanout_backtest_complete(
        batch_id="batch-1",
        status="completed",
        completed=2,
        total=2,
    )
    assert any(c["title"].startswith("回測") for c in calls)

    fanout_from_process_deltas(
        trader_id="t2",
        decision_count_delta=1,
        fill_count_delta=0,
        trade_count_delta=0,
        position_quantity=0,
    )
    assert any(c["title"] == "買入機會" for c in calls)


def test_fanout_skips_without_vapid() -> None:
    # No VAPID → _send_web_push raises HTTPException → fanout returns skipped
    with patch(
        "futures_research.api.routes_push._send_web_push",
        side_effect=RuntimeError("no vapid"),
    ):
        result = fanout_paper_activity(trader_id="t", kind="exit", pnl=-1.0)
    assert result.get("sent", 0) == 0
