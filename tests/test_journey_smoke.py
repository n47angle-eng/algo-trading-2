"""Cross-page identity smoke: coverage dates → handoff shape; review v2 members."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, date
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
from futures_research.data.coverage import compute_trading_day_coverage
from futures_research.paper.models import BaselineMember
from futures_research.paper.review_v2 import REVIEW_V2_MEMBER_ORDER, build_paper_review_v2
from futures_research.paper.store import PaperRuntimeStore
from tests.test_coverage_facts import _minute_bars, _write_tiny_contract_config

NOW = datetime(2026, 7, 31, 13, 30, tzinfo=UTC)
STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
BASELINE_RESULT = b'{"schema":"backtest_result.v1","run_id":"baseline-test"}'
BASELINE_SHA = sha256(BASELINE_RESULT).hexdigest()


def test_coverage_dates_preserve_identity_for_backtest_handoff(tmp_path: Path) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    bars = [
        *_minute_bars(contract, date(2026, 5, 5)),
        *_minute_bars(contract, date(2026, 5, 6)),
        *_minute_bars(contract, date(2026, 5, 7)),
    ]
    document = compute_trading_day_coverage(
        contract, bars, owner_entries=[]
    ).document
    assert document["status"] == "known"
    complete = document["complete_trading_dates"]
    assert complete == ["2026-05-05", "2026-05-06", "2026-05-07"]
    segment = document["longest_complete_segment"]
    # Handoff query identity fields the FE will carry:
    handoff = {
        "symbol": "NQ",
        "startDate": segment["start_trading_date"],
        "endDate": segment["end_trading_date"],
    }
    assert handoff["startDate"] == "2026-05-05"
    assert handoff["endDate"] == "2026-05-07"
    # Labels are not instants — no Z suffix, no timezone offset.
    assert "T" not in handoff["startDate"]
    assert "Z" not in handoff["endDate"]


def test_paper_review_v2_preserves_strategy_and_baseline_identity(
    tmp_path: Path,
) -> None:
    store = PaperRuntimeStore(tmp_path / "runtime.sqlite3")
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
    package = build_paper_review_v2(
        store,
        trader_id=trader.trader_id,
        request_id=str(uuid4()),
        cutoff_at=NOW,
    )
    with zipfile.ZipFile(io.BytesIO(package.zip_bytes)) as zf:
        assert zf.namelist() == list(REVIEW_V2_MEMBER_ORDER)
        main = json.loads(zf.read("paper-review.json"))
        assert main["schema"] == "paper-review.v2"
        assert main["selection"]["strategy_id"] == "strategy-0003"
        assert main["selection"]["content_sha256"] == STRATEGY_SHA
        assert main["selection"]["baseline_run_id"] == "baseline-test"
        assert main["selection"]["contract_id"] == "NQ-202609-CME"


def test_api_health_and_runtime_capabilities() -> None:
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    caps = client.get("/api/v1/paper/runtime-capabilities")
    assert caps.status_code == 200
    assert caps.json()["schema"] == "paper_runtime_capabilities.v1"
