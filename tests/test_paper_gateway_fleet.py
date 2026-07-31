"""Gateway probe fail-closed + multi-trader fleet overview."""

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
from futures_research.api.paper_runtime_manager import (
    build_fleet_overview,
    clear_manager_for_tests,
    gateway_status,
    probe_gateway_tcp,
    start_runtime,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.paper.models import BaselineMember
from futures_research.paper.store import PaperRuntimeStore
from futures_research.paths import PROJECT_ROOT

NOW = datetime(2026, 7, 31, 13, 30, tzinfo=UTC)
STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
BASELINE_RESULT = b'{"schema":"backtest_result.v1"}'
BASELINE_SHA = sha256(BASELINE_RESULT).hexdigest()


def test_probe_gateway_tcp_fail_closed_when_port_closed() -> None:
    # High unused port — must not invent "connected".
    assert probe_gateway_tcp(port=1, timeout_seconds=0.2) is False


def test_gateway_status_schema_never_claims_orders() -> None:
    status = gateway_status()
    assert status["schema"] == "paper_gateway_status.v1"
    assert status["order_paths"] == 0
    assert "port_reachable" in status


def test_fleet_overview_aggregates_runtime_metrics(tmp_path: Path) -> None:
    clear_manager_for_tests()
    store = PaperRuntimeStore(tmp_path / "rt.sqlite3")
    store.initialize()
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
    contract = ContractRegistry.from_yaml(
        PROJECT_ROOT / "config" / "contracts.yaml"
    ).contracts["NQ"]
    # start without requiring real Gateway
    started = start_runtime(
        store=store,
        trader_id=trader.trader_id,
        contract=contract,
        now=NOW,
        attach_gateway=True,
    )
    assert started["lifecycle"] == "running"
    # Gateway may be unavailable — still honest payload
    assert "gateway" in started

    fleet = build_fleet_overview(
        stage_a_traders=[
            {
                "trader_id": trader.trader_id,
                "strategy": {
                    "strategy_id": "strategy-0003",
                    "content_sha256": STRATEGY_SHA,
                },
                "contract_id": "NQ-202609-CME",
                "baseline": {"run_id": "baseline-test"},
                "account": {"currency": "USD", "initial_capital": 100_000},
            }
        ],
        runtime_store=store,
    )
    assert fleet["schema"] == "paper_fleet_overview.v1"
    assert fleet["count"] == 1
    assert fleet["traders"][0]["runtime_available"] is True
    assert fleet["traders"][0]["lifecycle"] == "running"
    assert fleet["totals"]["running_count"] == 1
    assert "from_results" in fleet["closed_loop"]
    clear_manager_for_tests()


def test_gateway_status_route() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/paper/runtime/gateway-status")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "paper_gateway_status.v1"
    assert body["order_paths"] == 0
