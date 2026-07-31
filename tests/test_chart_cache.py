"""WO-006 / 6-3b: chart sidecar cache (file L0 + process memory L1)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from futures_research.api.chart_series import (
    chart_sidecar_path,
    clear_chart_memory_cache,
    get_chart_series,
    resolve_lookback_days,
)
from futures_research.api.main import app
from futures_research.api.results_catalog import ResultsCatalog


def _seed_result(
    root: Path,
    run_id: str = "cache-run-001",
    *,
    data_fingerprint: str | None = None,
) -> ResultsCatalog:
    root.mkdir(parents=True, exist_ok=True)
    (root / "events").mkdir()
    (root / "trades").mkdir()
    manifest: dict[str, object] = {
        "contract_id": "NQ-202609-CME",
        "session_name": "eth",
        "range_start": "2026-05-01T00:00:00Z",
        "range_end": "2026-05-22T00:00:00Z",
    }
    if data_fingerprint is not None:
        manifest["data_fingerprint"] = {
            "schema": "canonical-bars.v1",
            "algorithm": "sha256",
            "digest": data_fingerprint,
            "bar_count": 1,
        }
    (root / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema": "result.v1",
                "run": {
                    "run_id": run_id,
                    "manifest": manifest,
                },
                "metrics": {},
                "events_ref": f"events/{run_id}.json",
                "trades_ref": f"trades/{run_id}.json",
            }
        ),
        encoding="utf-8",
    )
    (root / "events" / f"{run_id}.json").write_text(json.dumps({"events": []}), encoding="utf-8")
    (root / "trades" / f"{run_id}.json").write_text(json.dumps({"trades": []}), encoding="utf-8")
    return ResultsCatalog(results_root=root)


def test_sidecar_hit_is_pure_file_read(tmp_path: Path) -> None:
    clear_chart_memory_cache()
    catalog = _seed_result(tmp_path / "results")
    run_id = "cache-run-001"
    lookback = resolve_lookback_days("5m", None)
    path = chart_sidecar_path(catalog.results_root, run_id, "5m", lookback)
    path.parent.mkdir(parents=True, exist_ok=True)
    canned = {
        "schema": "chart_series.v1",
        "run_id": run_id,
        "timeframe": "5m",
        "lookback_days": lookback,
        "candles": [{"time": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5}],
        "ema18": [],
        "ema50": [],
        "ema90": [],
        "markers": [],
        "levels": [],
        "source": "fixture",
    }
    path.write_text(json.dumps(canned), encoding="utf-8")

    first = get_chart_series(catalog, run_id, timeframe="5m")
    assert first["cache"] == "sidecar"
    assert first["candles"][0]["close"] == 1.5

    second = get_chart_series(catalog, run_id, timeframe="5m")
    assert second["cache"] == "memory"
    assert second["candles"][0]["close"] == 1.5


@pytest.mark.asyncio
async def test_chart_endpoint_second_hit_under_300ms_with_sidecar(
    tmp_path: Path,
) -> None:
    """Acceptance: second default 5m request is a file/memory hit, not recompute."""
    clear_chart_memory_cache()
    catalog = _seed_result(tmp_path / "results")
    run_id = "cache-run-001"
    lookback = resolve_lookback_days("5m", None)
    path = chart_sidecar_path(catalog.results_root, run_id, "5m", lookback)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "chart_series.v1",
                "run_id": run_id,
                "timeframe": "5m",
                "candles": [
                    {"time": i, "open": 1, "high": 2, "low": 0, "close": 1} for i in range(100)
                ],
                "ema18": [],
                "ema50": [],
                "ema90": [],
                "markers": [],
                "levels": [],
                "source": "fixture",
            }
        ),
        encoding="utf-8",
    )
    app.state.results_catalog = catalog
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get(f"/api/v1/runs/{run_id}/chart?tf=5m")
        assert r1.status_code == 200
        assert r1.json()["cache"] in {"sidecar", "memory"}
        t0 = time.perf_counter()
        r2 = await client.get(f"/api/v1/runs/{run_id}/chart?tf=5m")
        elapsed_ms = (time.perf_counter() - t0) * 1000
    delattr(app.state, "results_catalog")
    assert r2.status_code == 200
    assert r2.json()["cache"] == "memory"
    assert elapsed_ms < 300, f"second hit {elapsed_ms:.1f}ms exceeds 300ms"


def _write_sidecar(
    catalog: ResultsCatalog,
    run_id: str,
    lookback: int,
    payload: dict[str, object],
) -> Path:
    path = chart_sidecar_path(catalog.results_root, run_id, "5m", lookback)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _canned(run_id: str, lookback: int, **extra: object) -> dict[str, object]:
    return {
        "schema": "chart_series.v1",
        "run_id": run_id,
        "timeframe": "5m",
        "lookback_days": lookback,
        "candles": [{"time": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5}],
        "ema18": [],
        "ema50": [],
        "ema90": [],
        "markers": [],
        "levels": [],
        "source": "fixture",
        **extra,
    }


def test_sidecar_with_matching_fingerprint_is_served(tmp_path: Path) -> None:
    """WO-006 / 6-5 advisory 2: a digest that still matches keeps the fast path."""
    clear_chart_memory_cache()
    digest = "a" * 64
    catalog = _seed_result(tmp_path / "results", data_fingerprint=digest)
    run_id = "cache-run-001"
    lookback = resolve_lookback_days("5m", None)
    _write_sidecar(catalog, run_id, lookback, _canned(run_id, lookback, data_fingerprint=digest))

    served = get_chart_series(catalog, run_id, timeframe="5m")

    assert served["cache"] == "sidecar"
    assert served["candles"][0]["close"] == 1.5


def test_stale_fingerprint_forces_a_recompute(tmp_path: Path) -> None:
    """A backfill changes the run digest; the old chart must not be served on."""
    clear_chart_memory_cache()
    catalog = _seed_result(tmp_path / "results", data_fingerprint="b" * 64)
    run_id = "cache-run-001"
    lookback = resolve_lookback_days("5m", None)
    path = _write_sidecar(
        catalog,
        run_id,
        lookback,
        _canned(run_id, lookback, data_fingerprint="a" * 64),
    )

    served = get_chart_series(
        catalog,
        run_id,
        timeframe="5m",
        market_root=tmp_path / "empty-market",
        daily_root=tmp_path / "empty-daily",
    )

    assert served["cache"] == "write_stale_fingerprint"
    assert served["data_fingerprint"] == "b" * 64
    # The stale sidecar is replaced on disk, not merely bypassed in memory.
    assert json.loads(path.read_text(encoding="utf-8"))["data_fingerprint"] == "b" * 64


def test_pre_65_sidecar_without_a_digest_counts_as_stale(tmp_path: Path) -> None:
    """A sidecar written before 6-5 cannot be shown to predate a backfill."""
    clear_chart_memory_cache()
    catalog = _seed_result(tmp_path / "results", data_fingerprint="c" * 64)
    run_id = "cache-run-001"
    lookback = resolve_lookback_days("5m", None)
    _write_sidecar(catalog, run_id, lookback, _canned(run_id, lookback))

    served = get_chart_series(
        catalog,
        run_id,
        timeframe="5m",
        market_root=tmp_path / "empty-market",
        daily_root=tmp_path / "empty-daily",
    )

    assert served["cache"] == "write_stale_fingerprint"
