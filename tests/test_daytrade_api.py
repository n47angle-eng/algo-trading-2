"""ASGI smoke: multi traders list/create + personal pages."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from futures_research.api import routes_daytrade as dt_routes
from futures_research.api.main import app
from futures_research.daytrade.ledger import DaytradeLedger


def test_list_create_and_profile(tmp_path: Path, monkeypatch: object) -> None:
    ledger = DaytradeLedger(tmp_path / "api.db")
    ledger.bootstrap_traders_from_presets()
    monkeypatch.setattr(dt_routes, "_ledger", ledger)  # type: ignore[attr-defined]
    monkeypatch.setattr(dt_routes, "_runner", None)  # type: ignore[attr-defined]

    client = TestClient(app)

    listed = client.get("/api/v1/daytrade/traders")
    assert listed.status_code == 200
    body = listed.json()
    assert body["count"] >= 1
    assert any(t["trader_id"] == "dt-nq-or-1" for t in body["traders"])
    assert "profile_path" in body["traders"][0]

    created = client.post(
        "/api/v1/daytrade/traders",
        json={
            "display_name": "YM 測試員",
            "symbol": "YM",
            "notes": "unit test",
        },
    )
    assert created.status_code == 201
    new_id = created.json()["trader"]["trader_id"]
    assert new_id.startswith("dt-ym-")
    assert created.json()["entry"] == f"/daytrade/traders/{new_id}"

    listed2 = client.get("/api/v1/daytrade/traders")
    assert listed2.json()["count"] == body["count"] + 1

    profile = client.get(f"/api/v1/daytrade/traders/{new_id}")
    assert profile.status_code == 200
    p = profile.json()
    assert p["profile"]["display_name"] == "YM 測試員"
    assert p["profile"]["symbol"] == "YM"
    assert "scorecard" in p
    assert "links" in p

    score = client.get(f"/api/v1/daytrade/traders/{new_id}/scorecard")
    assert score.status_code == 200
    assert score.json()["formula_version"] == "daytrade-scorecard-v2"

    chart = client.get(f"/api/v1/daytrade/traders/{new_id}/market-chart")
    assert chart.status_code == 200
    assert chart.json()["schema"] == "daytrade_market_chart.v1"
    assert "bars" in chart.json()
    assert "reference" in chart.json()

    eq = client.get(f"/api/v1/daytrade/traders/{new_id}/equity-series")
    assert eq.status_code == 200
    assert eq.json()["schema"] == "daytrade_equity_series.v1"

    st = client.get(f"/api/v1/daytrade/traders/{new_id}/stats?window=today")
    assert st.status_code == 200
    assert st.json()["formula_version"] == "daytrade-stats-v2"
    assert st.json()["window"] == "today"

    # seed bars + step preset trader
    client.post("/api/v1/daytrade/runner", json={"enabled": True})
    tz = ZoneInfo("America/Chicago")
    base = datetime(2026, 7, 30, 8, 30, tzinfo=tz)
    bars = []
    for i in range(8):
        ts = base + timedelta(minutes=i)
        high, close = 101.0, 100.5
        if i == 6:
            high, close = 102.0, 101.8
        bars.append(
            {
                "symbol": "NQ",
                "ts": ts.isoformat(),
                "open": 100.0,
                "high": high,
                "low": 100.0 if i != 6 else 100.5,
                "close": close,
                "volume": 1,
                "bar_mode": "1m",
            }
        )
    client.post(
        "/api/v1/daytrade/bars/ingest",
        json={"trading_date": "2026-07-30", "bars": bars},
    )
    step = client.post(
        "/api/v1/daytrade/step",
        json={"trading_date": "2026-07-30", "trader_id": "dt-nq-or-1"},
    )
    assert step.status_code == 200
    live = client.get(
        "/api/v1/daytrade/traders/dt-nq-or-1/live?trading_date=2026-07-30"
    )
    assert live.status_code == 200
    pos = client.get(
        "/api/v1/daytrade/traders/dt-nq-or-1/positions?trading_date=2026-07-30"
    )
    assert pos.status_code == 200
    assert pos.json()["equity"] == live.json()["equity"]

    bt = client.post(
        "/api/v1/daytrade/bt/run",
        json={
            "trader_id": new_id,
            "start": "2026-07-30",
            "end": "2026-07-30",
            "execution_mode_id": "1s_worst",
        },
    )
    assert bt.status_code == 409
