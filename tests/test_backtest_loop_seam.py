"""Phase E: backtest_loop_v1 oracle + rust exact + fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from futures_research.backtest.backtest_kernel_python import compute_backtest_loop_python
from futures_research.contracts.backtest_loop import BacktestLoopRequest
from futures_research.paths import PROJECT_ROOT
from futures_research.platform.backtest_loop_seam import (
    backtest_loop_v1,
    reset_backtest_loop_state_for_tests,
)
from futures_research.platform.native_runtime import clear_native_registry


def _bars(n: int = 120) -> list[dict[str, float | str]]:
    out: list[dict[str, float | str]] = []
    for i in range(n):
        minute = i % 60
        hour = 12 + i // 60
        t = f"2026-07-01T{hour:02d}:{minute:02d}:00Z"
        # mild wave so dual EMA crosses
        c = 100.0 + (i % 20) * 0.5 - (i // 20) * 0.2
        out.append({"t": t, "o": c - 0.1, "h": c + 0.5, "l": c - 0.5, "c": c, "v": 10.0})
    return out


def _request(backend: str = "stable") -> BacktestLoopRequest:
    return BacktestLoopRequest.model_validate(
        {
            "schema": "backtest_loop_request.v1",
            "timeframe_minutes": 5,
            "ema_fast": 3,
            "ema_slow": 8,
            "quantity": 1,
            "point_value": 20.0,
            "commission_per_side": 2.5,
            "bars": _bars(),
            "requested_backend": backend,
        }
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_backtest_loop_state_for_tests()
    clear_native_registry()
    yield
    reset_backtest_loop_state_for_tests()
    clear_native_registry()


def _ensure_caps() -> Path | None:
    release = PROJECT_ROOT / "native" / "fr_compute" / "target" / "release"
    lib = release / "libfr_compute.dylib"
    if not lib.is_file():
        lib = release / "libfr_compute.so"
    if not lib.is_file():
        return None
    cap = PROJECT_ROOT / "native" / "fr_compute" / "fr_compute.capabilities"
    Path(str(lib) + ".capabilities").write_text(
        cap.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return lib


def test_python_oracle_deterministic() -> None:
    a = compute_backtest_loop_python(_request())
    b = compute_backtest_loop_python(_request())
    assert a.artifact_sha256 == b.artifact_sha256
    assert a.provenance["writes_authority"] is False
    assert a.provenance["effective_backend"] == "python"


def test_seam_stable_python() -> None:
    draft = backtest_loop_v1(_request("stable"))
    assert draft.provenance["effective_backend"] == "python"


def test_rust_exact_when_dylib_present() -> None:
    if _ensure_caps() is None:
        pytest.skip("Rust dylib not built")
    py = compute_backtest_loop_python(_request("stable"))
    rust = backtest_loop_v1(_request("accelerated"), force_backend="accelerated")
    if rust.provenance["effective_backend"] != "rust":
        pytest.skip(f"rust not admitted: {rust.provenance.get('fallback_reason')}")
    assert rust.trade_count == py.trade_count
    assert abs(rust.net_pnl - py.net_pnl) < 1e-9
    assert len(rust.trades) == len(py.trades)
    for rt, pt in zip(rust.trades, py.trades, strict=True):
        assert rt.entry_t == pt.entry_t
        assert rt.exit_t == pt.exit_t
        assert abs(rt.net_pnl - pt.net_pnl) < 1e-9
