"""Chart compute seam: Python oracle, optional Rust exact, fallback."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from futures_research.backtest.chart_kernel_python import compute_chart_python
from futures_research.contracts.chart_compute import ChartBar, ChartComputeRequest
from futures_research.paths import PROJECT_ROOT
from futures_research.platform.backend_policy import resolve_compute_backend
from futures_research.platform.chart_compute_seam import (
    chart_compute_v1,
    reset_chart_compute_state_for_tests,
)
from futures_research.platform.native_runtime import clear_native_registry


def _bars() -> tuple[ChartBar, ...]:
    # 10 minutes of 1m bars starting 2026-07-01T12:00:00Z
    out: list[ChartBar] = []
    base = 100.0
    for i in range(10):
        minute = f"{i:02d}"
        t = f"2026-07-01T12:{minute}:00Z"
        o = base + i
        out.append(
            ChartBar(
                t=t,
                o=o,
                h=o + 1,
                l=o - 1,
                c=o + 0.5,
                v=100.0 + i,
            )
        )
    return tuple(out)


def _request(backend: str = "stable") -> ChartComputeRequest:
    return ChartComputeRequest.model_validate(
        {
            "schema": "chart_compute_request.v1",
            "timeframe_minutes": 5,
            "ema_period": 3,
            "bars": [b.model_dump(mode="json") for b in _bars()],
            "requested_backend": backend,
        }
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_chart_compute_state_for_tests()
    clear_native_registry()
    yield
    reset_chart_compute_state_for_tests()
    clear_native_registry()


def test_python_oracle_is_deterministic() -> None:
    a = compute_chart_python(_request())
    b = compute_chart_python(_request())
    assert a.artifact_sha256 == b.artifact_sha256
    assert len(a.points) == 2  # 10 minutes → two 5m buckets
    assert a.provenance["writes_authority"] is False
    assert a.provenance["effective_backend"] == "python"


def test_seam_stable_always_python() -> None:
    draft = chart_compute_v1(_request("stable"))
    assert draft.provenance["effective_backend"] == "python"
    assert draft.provenance["writes_authority"] is False


def test_authority_path_never_selects_rust() -> None:
    backend, reason = resolve_compute_backend(
        feature="paper_ledger",
        requested="accelerated",
        rust_available=True,
        rust_qualified=True,
        authority_path=True,
    )
    assert backend == "python"
    assert reason == "authority_path_forced_python"


def test_kill_switch_forces_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FR_RUST_DISABLE", "1")
    draft = chart_compute_v1(_request("auto"))
    assert draft.provenance["effective_backend"] == "python"
    assert draft.provenance.get("fallback_reason") in {
        "FR_RUST_DISABLE",
        "rust_unavailable",
        "rust_not_qualified",
        "rust_unavailable_accelerated_fallback",
        "rust_not_qualified_accelerated_fallback",
        None,
    } or (
        draft.provenance.get("fallback_reason") is not None
        and "FR_RUST" in str(draft.provenance.get("fallback_reason"))
    )


def test_reject_non_finite_bars() -> None:
    with pytest.raises(Exception):
        ChartBar(t="2026-07-01T12:00:00Z", o=1.0, h=1.0, l=1.0, c=float("nan"), v=1.0)


def test_rust_exact_when_dylib_present() -> None:
    lib = (
        PROJECT_ROOT
        / "native"
        / "fr_compute"
        / "target"
        / "release"
        / "libfr_compute.dylib"
    )
    if not lib.is_file():
        lib = (
            PROJECT_ROOT
            / "native"
            / "fr_compute"
            / "target"
            / "release"
            / "libfr_compute.so"
        )
    if not lib.is_file():
        pytest.skip("Rust dylib not built yet")

    # Copy capabilities beside dylib for admission.
    cap = PROJECT_ROOT / "native" / "fr_compute" / "fr_compute.capabilities"
    side = Path(str(lib) + ".capabilities")
    side.write_text(cap.read_text(encoding="utf-8"), encoding="utf-8")

    py = compute_chart_python(_request("stable"))
    rust = chart_compute_v1(_request("accelerated"))
    assert rust.provenance["effective_backend"] in {"rust", "python"}
    if rust.provenance["effective_backend"] != "rust":
        pytest.skip(f"rust not admitted: {rust.provenance.get('fallback_reason')}")

    assert len(rust.points) == len(py.points)
    for rp, pp in zip(rust.points, py.points, strict=True):
        assert rp.t == pp.t
        assert rp.o == pp.o
        assert rp.h == pp.h
        assert rp.l == pp.l
        assert rp.c == pp.c
        assert rp.v == pp.v
        assert abs(rp.ema - pp.ema) < 1e-12
