"""Unit tests for chart markers / narrative templates (WO-006 / 6-3)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from futures_research.api.chart_series import (
    _levels_from_events,
    _markers_from_events,
    _narrative_steps,
    build_narrative,
)
from futures_research.api.main import app
from futures_research.api.results_catalog import ResultsCatalog


def test_markers_and_levels_from_signal_created() -> None:
    events = [
        {
            "event_type": "signal_created",
            "timestamp": "2026-05-21T03:35:00Z",
            "direction": "long",
            "price": 29640.0,
            "details": {"signal_kind": "inside", "stop_reference": 29568.0},
        }
    ]
    markers = _markers_from_events(events, [])
    assert any(item["text"] == "signal" for item in markers)
    levels = _levels_from_events(events, [])
    kinds = {item["kind"] for item in levels}
    assert "entry_ref" in kinds
    assert "stop" in kinds
    assert "target" in kinds


def test_narrative_skips_daily_regime_reject_spam() -> None:
    events = [
        {
            "event_type": "daily_regime_changed",
            "timestamp": "2026-05-07T20:55:00Z",
            "to_state": "trend",
            "direction": "long",
        },
        {
            "event_type": "signal_rejected",
            "timestamp": "2026-05-08T10:00:00Z",
            "details": {"reason": "daily_regime_range"},
        },
        {
            "event_type": "signal_created",
            "timestamp": "2026-05-21T03:35:00Z",
            "direction": "long",
            "price": 100.0,
            "details": {"signal_kind": "inside", "stop_reference": 90.0},
        },
    ]
    steps = _narrative_steps(events, [], limit=20)
    layers = [step["layer"] for step in steps]
    assert "Daily 閘" in layers
    assert "5m 層" in layers
    assert not any("daily_regime_range" in str(step["text"]) for step in steps)


def test_build_narrative_via_catalog(tmp_path: Path) -> None:
    root = tmp_path / "results"
    root.mkdir()
    (root / "events").mkdir()
    (root / "trades").mkdir()
    run_id = "narr-001"
    (root / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "result.v1",
                "run": {
                    "run_id": run_id,
                    "manifest": {
                        "contract_id": "NQ-202609-CME",
                        "session_name": "eth",
                        "range_start": "2026-05-01T00:00:00Z",
                        "range_end": "2026-05-22T00:00:00Z",
                    },
                },
                "metrics": {"trade_count": 0},
                "events_ref": f"events/{run_id}.json",
                "trades_ref": f"trades/{run_id}.json",
            }
        ),
        encoding="utf-8",
    )
    (root / "events" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "event_type": "daily_regime_changed",
                        "timestamp": "2026-05-07T20:55:00Z",
                        "to_state": "trend",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "trades" / f"{run_id}.json").write_text(
        json.dumps({"trades": []}),
        encoding="utf-8",
    )
    catalog = ResultsCatalog(results_root=root)
    payload = build_narrative(catalog, run_id)
    assert payload["schema"] == "narrative.v1"
    assert payload["count"] >= 1
    assert payload["steps"][0]["layer"] == "Daily 閘"


@pytest.mark.asyncio
async def test_narrative_endpoint(tmp_path: Path) -> None:
    root = tmp_path / "results"
    root.mkdir()
    (root / "events").mkdir()
    (root / "trades").mkdir()
    run_id = "api-narr-1"
    (root / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "result.v1",
                "run": {
                    "run_id": run_id,
                    "manifest": {
                        "contract_id": "NQ-202609-CME",
                        "session_name": "eth",
                        "range_start": "2026-05-01T00:00:00Z",
                        "range_end": "2026-05-22T00:00:00Z",
                    },
                },
                "metrics": {},
                "events_ref": f"events/{run_id}.json",
                "trades_ref": f"trades/{run_id}.json",
            }
        ),
        encoding="utf-8",
    )
    (root / "events" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "event_type": "signal_created",
                        "timestamp": "2026-05-21T03:35:00Z",
                        "price": 1.0,
                        "details": {"signal_kind": "inside", "stop_reference": 0.5},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "trades" / f"{run_id}.json").write_text(json.dumps({"trades": []}), encoding="utf-8")
    app.state.results_catalog = ResultsCatalog(results_root=root)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/runs/{run_id}/narrative")
    delattr(app.state, "results_catalog")
    assert response.status_code == 200
    assert response.json()["steps"][0]["layer"] == "5m 層"
