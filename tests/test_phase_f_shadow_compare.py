"""Phase F: shadow compare Rust vs Python MTF + HTTP report."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from futures_research.api.chart_series import (
    clear_chart_memory_cache,
    shadow_compare_run_chart,
)
from futures_research.api.main import app
from futures_research.api.results_catalog import ResultsCatalog
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT
from futures_research.platform.chart_compute_seam import reset_chart_compute_state_for_tests
from futures_research.platform.chart_shadow_compare import compare_chart_series_payloads
from futures_research.platform.native_runtime import clear_native_registry

CONTRACT_ID = "NQ-202609-CME"
RUN_ID = "phase-f-shadow-001"


def _dylib_present() -> bool:
    release = PROJECT_ROOT / "native" / "fr_compute" / "target" / "release"
    return (release / "libfr_compute.dylib").is_file() or (
        release / "libfr_compute.so"
    ).is_file()


def _ensure_capabilities_sidecar() -> None:
    cap = PROJECT_ROOT / "native" / "fr_compute" / "fr_compute.capabilities"
    for name in ("libfr_compute.dylib", "libfr_compute.so"):
        lib = PROJECT_ROOT / "native" / "fr_compute" / "target" / "release" / name
        if lib.is_file():
            Path(str(lib) + ".capabilities").write_text(
                cap.read_text(encoding="utf-8"), encoding="utf-8"
            )


def _seed(tmp_path: Path) -> tuple[ResultsCatalog, Path, Path]:
    market = tmp_path / "market"
    daily = tmp_path / "daily"
    results = tmp_path / "results"
    market.mkdir()
    daily.mkdir()
    store = CanonicalStore(market)
    start = datetime(2026, 5, 20, 18, 0, tzinfo=UTC)
    bars = []
    for i in range(200):
        ts = start + timedelta(minutes=i)
        px = 20000.0 + i * 0.25
        bars.append(
            CanonicalBar(
                timestamp=ts,
                open=px,
                high=px + 1,
                low=px - 1,
                close=px + 0.5,
                volume=100 + i,
                contract_id=CONTRACT_ID,
                source="phase_f",
            )
        )
    store.append(bars)
    results.mkdir()
    (results / "events").mkdir()
    (results / "trades").mkdir()
    (results / f"{RUN_ID}.json").write_text(
        json.dumps(
            {
                "schema": "result.v1",
                "run": {
                    "run_id": RUN_ID,
                    "manifest": {
                        "contract_id": CONTRACT_ID,
                        "session_name": "eth",
                        "range_start": "2026-05-20T18:00:00Z",
                        "range_end": "2026-05-20T21:20:00Z",
                        "data_fingerprint": {
                            "digest": "phase-f-digest",
                            "algorithm": "sha256",
                            "schema": "canonical-bars.v1",
                            "bar_count": 200,
                        },
                    },
                },
                "metrics": {},
                "events_ref": f"events/{RUN_ID}.json",
                "trades_ref": f"trades/{RUN_ID}.json",
            }
        ),
        encoding="utf-8",
    )
    (results / "events" / f"{RUN_ID}.json").write_text(
        json.dumps({"events": []}), encoding="utf-8"
    )
    (results / "trades" / f"{RUN_ID}.json").write_text(
        json.dumps({"trades": []}), encoding="utf-8"
    )
    return ResultsCatalog(results_root=results), market, daily


@pytest.fixture(autouse=True)
def _reset() -> None:
    clear_chart_memory_cache()
    reset_chart_compute_state_for_tests()
    clear_native_registry()
    yield
    clear_chart_memory_cache()
    reset_chart_compute_state_for_tests()
    clear_native_registry()


def test_compare_identical_payloads_pass() -> None:
    payload = {
        "schema": "chart_series.v1",
        "run_id": "x",
        "timeframe": "5m",
        "source": "t",
        "candles": [
            {"time": 1, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
            {"time": 2, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0},
        ],
        "ema18": [{"time": 1, "value": 1.5}, {"time": 2, "value": 1.8}],
        "ema50": [],
        "ema90": [],
        "compute_provenance": {"effective_backend": "python"},
    }
    report = compare_chart_series_payloads(reference=payload, candidate=payload)
    assert report["verdict"] == "PASS"
    assert report["summary"]["max_abs_ohlc_diff"] == 0.0


def test_compare_detects_ohlc_diff() -> None:
    ref = {
        "candles": [{"time": 1, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}],
        "ema18": [{"time": 1, "value": 1.0}],
        "ema50": [],
        "ema90": [],
    }
    cand = {
        "candles": [{"time": 1, "open": 1.0, "high": 2.0, "low": 0.5, "close": 9.0}],
        "ema18": [{"time": 1, "value": 1.0}],
        "ema50": [],
        "ema90": [],
    }
    report = compare_chart_series_payloads(reference=ref, candidate=cand, abs_eps=1e-6)
    assert report["verdict"] == "DIFF"
    assert report["ohlc"]["max_abs_diff"] == 7.5


def test_shadow_compare_run_with_rust(tmp_path: Path) -> None:
    if not _dylib_present():
        pytest.skip("Rust dylib not built")
    _ensure_capabilities_sidecar()
    catalog, market, daily = _seed(tmp_path)
    report = shadow_compare_run_chart(
        catalog,
        RUN_ID,
        timeframe="5m",
        market_root=market,
        daily_root=daily,
    )
    assert report["schema"] == "chart_shadow_compare.v1"
    assert report["verdict"] in {"PASS", "DIFF", "SKIPPED", "INCOMPARABLE"}
    # With dylib we expect a real compare, not skip.
    assert report["verdict"] != "SKIPPED"
    assert report["candidate"]["compute_provenance"]["effective_backend"] == "rust"
    assert report["reference"]["compute_provenance"]["effective_backend"] == "python"
    assert "ohlc" in report
    assert "ema" in report
    assert "summary" in report


def test_shadow_compare_skipped_when_rust_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FR_RUST_DISABLE", "1")
    catalog, market, daily = _seed(tmp_path)
    report = shadow_compare_run_chart(
        catalog,
        RUN_ID,
        timeframe="5m",
        market_root=market,
        daily_root=daily,
    )
    assert report["verdict"] == "SKIPPED"
    assert report["reason"] == "rust_not_effective"


def test_http_shadow_compare_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _dylib_present():
        pytest.skip("Rust dylib not built")
    _ensure_capabilities_sidecar()
    catalog, market, daily = _seed(tmp_path)
    app.state.results_catalog = catalog

    from futures_research.api import routes_chart as routes_mod

    original = routes_mod.shadow_compare_run_chart

    def _shadow(*args: object, **kwargs: object) -> dict:
        kwargs = dict(kwargs)
        if kwargs.get("market_root") is None:
            kwargs["market_root"] = market
        if kwargs.get("daily_root") is None:
            kwargs["daily_root"] = daily
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(routes_mod, "shadow_compare_run_chart", _shadow)

    client = TestClient(app)
    response = client.get(
        f"/api/v1/runs/{RUN_ID}/chart/shadow-compare",
        params={"tf": "5m"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "chart_shadow_compare.v1"
    assert body["verdict"] in {"PASS", "DIFF", "SKIPPED", "INCOMPARABLE"}
    assert body["run_id"] == RUN_ID
