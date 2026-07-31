"""Phase C product E2E: chart materialize prefers Rust, falls back to Python MTF.

Covers:
- Direct ``build_chart_series`` / ``get_chart_series`` with synthetic market bars
- HTTP ``GET /api/v1/runs/{run_id}/chart`` via ASGI TestClient
- stable → python MTF; accelerated → rust when dylib present
- FR_RUST_DISABLE / missing dylib → python fallback without 500
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from futures_research.api.chart_series import (
    build_chart_series,
    clear_chart_memory_cache,
    get_chart_series,
)
from futures_research.api.main import app
from futures_research.api.results_catalog import ResultsCatalog
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT
from futures_research.platform.chart_compute_seam import reset_chart_compute_state_for_tests
from futures_research.platform.native_runtime import clear_native_registry

CONTRACT_ID = "NQ-202609-CME"
RUN_ID = "phase-c-chart-e2e-001"


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
            side = Path(str(lib) + ".capabilities")
            side.write_text(cap.read_text(encoding="utf-8"), encoding="utf-8")


def _seed_market(market_root: Path, *, minutes: int = 200) -> None:
    """Seed continuous 1m bars (UTC) so wall-clock 5m resample has enough warm-up."""
    store = CanonicalStore(market_root)
    # Weekday session-ish window in UTC for NQ eth (Chicago evenings → next day).
    start = datetime(2026, 5, 20, 18, 0, tzinfo=UTC)
    bars: list[CanonicalBar] = []
    for i in range(minutes):
        ts = start + timedelta(minutes=i)
        px = 20000.0 + i * 0.25
        bars.append(
            CanonicalBar(
                timestamp=ts,
                open=px,
                high=px + 1.0,
                low=px - 1.0,
                close=px + 0.5,
                volume=100 + i,
                contract_id=CONTRACT_ID,
                source="phase_c_fixture",
            )
        )
    store.append(bars)


def _seed_result(results_root: Path) -> ResultsCatalog:
    results_root.mkdir(parents=True, exist_ok=True)
    (results_root / "events").mkdir(exist_ok=True)
    (results_root / "trades").mkdir(exist_ok=True)
    (results_root / f"{RUN_ID}.json").write_text(
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
                            "schema": "canonical-bars.v1",
                            "algorithm": "sha256",
                            "digest": "phase-c-fixture-digest",
                            "bar_count": 200,
                        },
                    },
                },
                "metrics": {"trade_count": 0},
                "events_ref": f"events/{RUN_ID}.json",
                "trades_ref": f"trades/{RUN_ID}.json",
            }
        ),
        encoding="utf-8",
    )
    (results_root / "events" / f"{RUN_ID}.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "event_type": "signal_created",
                        "timestamp": "2026-05-20T19:05:00Z",
                        "direction": "long",
                        "price": 20050.0,
                        "details": {"signal_kind": "inside", "stop_reference": 20000.0},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (results_root / "trades" / f"{RUN_ID}.json").write_text(
        json.dumps({"trades": []}),
        encoding="utf-8",
    )
    return ResultsCatalog(results_root=results_root)


@pytest.fixture(autouse=True)
def _reset_native() -> None:
    clear_chart_memory_cache()
    reset_chart_compute_state_for_tests()
    clear_native_registry()
    yield
    clear_chart_memory_cache()
    reset_chart_compute_state_for_tests()
    clear_native_registry()


@pytest.fixture
def product_env(tmp_path: Path) -> dict[str, Path]:
    market = tmp_path / "market"
    daily = tmp_path / "market-daily"
    results = tmp_path / "results"
    market.mkdir()
    daily.mkdir()
    _seed_market(market)
    catalog = _seed_result(results)
    return {"market": market, "daily": daily, "results": results, "catalog": catalog}  # type: ignore[dict-item]


def test_product_stable_uses_python_mtf(product_env: dict[str, Path]) -> None:
    catalog: ResultsCatalog = product_env["catalog"]  # type: ignore[assignment]
    payload = build_chart_series(
        catalog,
        RUN_ID,
        timeframe="5m",
        market_root=product_env["market"],
        daily_root=product_env["daily"],
        force_backend="stable",
    )
    assert payload["schema"] == "chart_series.v1"
    assert payload["compute_provenance"]["effective_backend"] == "python"
    assert payload["compute_provenance"]["writes_authority"] is False
    assert payload["source"] in {
        "backend_mtf_precompute",
        "backend_native_daily_mtf",
    }
    # Markers still come from Python authority-side event mapping.
    assert any(m.get("text") == "signal" for m in payload["markers"])


def test_product_accelerated_uses_rust_when_dylib_present(
    product_env: dict[str, Path],
) -> None:
    if not _dylib_present():
        pytest.skip("Rust dylib not built")
    _ensure_capabilities_sidecar()

    catalog: ResultsCatalog = product_env["catalog"]  # type: ignore[assignment]
    payload = build_chart_series(
        catalog,
        RUN_ID,
        timeframe="5m",
        market_root=product_env["market"],
        daily_root=product_env["daily"],
        force_backend="accelerated",
    )
    assert payload["compute_provenance"]["effective_backend"] == "rust"
    assert payload["compute_provenance"]["writes_authority"] is False
    assert payload["source"] == "backend_rust_chart_compute"
    assert len(payload["candles"]) > 0
    assert "open" in payload["candles"][0]
    # EMA warm-up: with 200 minutes → 40 five-minute bars, ema18 should appear.
    assert len(payload["ema18"]) > 0
    assert any(m.get("text") == "signal" for m in payload["markers"])


def test_product_kill_switch_forces_python_mtf(
    product_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FR_RUST_DISABLE", "1")
    catalog: ResultsCatalog = product_env["catalog"]  # type: ignore[assignment]
    payload = build_chart_series(
        catalog,
        RUN_ID,
        timeframe="5m",
        market_root=product_env["market"],
        daily_root=product_env["daily"],
        force_backend="accelerated",
    )
    assert payload["compute_provenance"]["effective_backend"] == "python"
    assert payload["source"] == "backend_mtf_precompute"


def test_product_get_chart_series_cache_bypass_on_force_backend(
    product_env: dict[str, Path],
) -> None:
    if not _dylib_present():
        pytest.skip("Rust dylib not built")
    _ensure_capabilities_sidecar()

    catalog: ResultsCatalog = product_env["catalog"]  # type: ignore[assignment]
    rust = get_chart_series(
        catalog,
        RUN_ID,
        timeframe="5m",
        market_root=product_env["market"],
        daily_root=product_env["daily"],
        force_backend="accelerated",
    )
    assert rust["cache"] == "force_backend_bypass"
    assert rust["compute_provenance"]["effective_backend"] == "rust"

    py = get_chart_series(
        catalog,
        RUN_ID,
        timeframe="5m",
        market_root=product_env["market"],
        daily_root=product_env["daily"],
        force_backend="stable",
    )
    assert py["compute_provenance"]["effective_backend"] == "python"


def _patch_build_with_fixture_roots(
    monkeypatch: pytest.MonkeyPatch,
    product_env: dict[str, Path],
    *,
    force_backend: str,
) -> None:
    """Route handlers pass market_root=None explicitly — override None, not only missing keys."""
    from futures_research.api import chart_series as chart_mod

    original = chart_mod.build_chart_series

    def _build(*args: object, **kwargs: object) -> dict:
        kwargs = dict(kwargs)
        if kwargs.get("market_root") is None:
            kwargs["market_root"] = product_env["market"]
        if kwargs.get("daily_root") is None:
            kwargs["daily_root"] = product_env["daily"]
        kwargs["force_backend"] = force_backend
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(chart_mod, "build_chart_series", _build)


def test_http_chart_endpoint_product_e2e_stable(
    product_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FR_COMPUTE_BACKEND", raising=False)
    monkeypatch.delenv("FR_RUST_DISABLE", raising=False)
    catalog: ResultsCatalog = product_env["catalog"]  # type: ignore[assignment]
    app.state.results_catalog = catalog
    _patch_build_with_fixture_roots(monkeypatch, product_env, force_backend="stable")
    clear_chart_memory_cache()

    client = TestClient(app)
    response = client.get(f"/api/v1/runs/{RUN_ID}/chart", params={"tf": "5m"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "chart_series.v1"
    assert body["compute_provenance"]["effective_backend"] == "python"
    assert body["compute_provenance"]["writes_authority"] is False
    assert len(body["candles"]) > 0


def test_http_chart_endpoint_product_e2e_accelerated_rust(
    product_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _dylib_present():
        pytest.skip("Rust dylib not built")
    _ensure_capabilities_sidecar()

    monkeypatch.setenv("FR_COMPUTE_BACKEND", "accelerated")
    monkeypatch.delenv("FR_RUST_DISABLE", raising=False)
    catalog: ResultsCatalog = product_env["catalog"]  # type: ignore[assignment]
    app.state.results_catalog = catalog
    _patch_build_with_fixture_roots(monkeypatch, product_env, force_backend="accelerated")
    clear_chart_memory_cache()

    client = TestClient(app)
    response = client.get(f"/api/v1/runs/{RUN_ID}/chart", params={"tf": "5m"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "chart_series.v1"
    assert body["compute_provenance"]["effective_backend"] == "rust"
    assert body["source"] == "backend_rust_chart_compute"
    assert body["compute_provenance"]["writes_authority"] is False
    assert len(body["candles"]) > 0
    assert any(m.get("text") == "signal" for m in body["markers"])


def test_http_chart_endpoint_fallback_when_rust_disabled(
    product_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FR_COMPUTE_BACKEND", "accelerated")
    monkeypatch.setenv("FR_RUST_DISABLE", "1")
    catalog: ResultsCatalog = product_env["catalog"]  # type: ignore[assignment]
    app.state.results_catalog = catalog
    _patch_build_with_fixture_roots(monkeypatch, product_env, force_backend="accelerated")
    clear_chart_memory_cache()

    client = TestClient(app)
    response = client.get(f"/api/v1/runs/{RUN_ID}/chart", params={"tf": "5m"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["compute_provenance"]["effective_backend"] == "python"
    assert body["source"] == "backend_mtf_precompute"
    assert len(body["candles"]) > 0


def test_env_accelerated_without_force_uses_rust(
    product_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ops path: FR_COMPUTE_BACKEND=accelerated with no force_backend."""
    if not _dylib_present():
        pytest.skip("Rust dylib not built")
    _ensure_capabilities_sidecar()
    monkeypatch.setenv("FR_COMPUTE_BACKEND", "accelerated")
    monkeypatch.delenv("FR_RUST_DISABLE", raising=False)

    catalog: ResultsCatalog = product_env["catalog"]  # type: ignore[assignment]
    payload = build_chart_series(
        catalog,
        RUN_ID,
        timeframe="5m",
        market_root=product_env["market"],
        daily_root=product_env["daily"],
    )
    assert payload["compute_provenance"]["effective_backend"] == "rust"
    assert os.environ.get("FR_COMPUTE_BACKEND") == "accelerated"


def test_production_default_auto_uses_rust_without_env(
    product_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production default is auto: unset env + admitted dylib → Rust chart."""
    if not _dylib_present():
        pytest.skip("Rust dylib not built")
    _ensure_capabilities_sidecar()
    monkeypatch.delenv("FR_COMPUTE_BACKEND", raising=False)
    monkeypatch.delenv("FR_RUST_DISABLE", raising=False)

    catalog: ResultsCatalog = product_env["catalog"]  # type: ignore[assignment]
    payload = build_chart_series(
        catalog,
        RUN_ID,
        timeframe="5m",
        market_root=product_env["market"],
        daily_root=product_env["daily"],
    )
    assert payload["compute_provenance"]["effective_backend"] == "rust"
    assert payload["source"] == "backend_rust_chart_compute"
    assert payload["compute_provenance"]["writes_authority"] is False
