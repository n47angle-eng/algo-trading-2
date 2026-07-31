"""P1 overview system status (WO-006 / 6-5).

The IB card deliberately reports only what it can prove. A TCP connect to the
Gateway port says the socket is listening — it does **not** say the Gateway is
logged in or that the API is enabled, so the wording stops at
"port 可達 · 未驗證 API session" (channel [083] Q4).
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from typing import Any, Final

from fastapi import APIRouter

from futures_research.data.ib import IbConnectionConfig
from fastapi import HTTPException, Query

from futures_research.platform.backend_policy import (
    PRODUCTION_DEFAULT_BACKEND,
    requested_backend_from_env,
    resolve_compute_backend,
    rust_globally_disabled,
)
from futures_research.platform.backtest_loop_seam import product_backtest_should_try_rust
from futures_research.platform.chart_compute_seam import _find_lib, _try_load_rust
from futures_research.platform.chart_series_backend import product_chart_should_try_rust
from futures_research.platform.compute_errors import get_compute_error, list_compute_errors

router = APIRouter(prefix="/api/v1/system", tags=["system"])

#: Kept short so the overview never waits on a Gateway that is simply off.
_PROBE_TIMEOUT_SECONDS: Final = 1.5


@router.get("/compute-status")
def get_compute_status() -> dict[str, Any]:
    """Report architecture + Rust compute readiness. Authority stays Python."""
    requested = requested_backend_from_env()
    lib = _find_lib()
    rust_ok, rust_reason = _try_load_rust()
    chart_effective, chart_fallback = resolve_compute_backend(
        feature="chart_series_product",
        requested=requested,
        rust_available=rust_ok,
        rust_qualified=rust_ok,
        authority_path=False,
    )
    bt_effective, bt_fallback = resolve_compute_backend(
        feature="backtest_loop_v1",
        requested=requested,
        rust_available=rust_ok,
        rust_qualified=rust_ok,
        authority_path=False,
    )
    errors = list_compute_errors(limit=10)
    has_error = any(e.get("severity") == "error" for e in errors)
    has_warn = any(e.get("severity") == "warn" for e in errors)
    health = "error" if has_error else ("warn" if has_warn else "ok")
    return {
        "schema": "compute_status.v1",
        "health": health,
        "production_default_backend": PRODUCTION_DEFAULT_BACKEND,
        "requested_backend": requested,
        "effective_chart_backend": chart_effective,
        "effective_backtest_backend": bt_effective,
        "fallback_reason": chart_fallback or bt_fallback,
        "rust_globally_disabled": rust_globally_disabled(),
        "dylib_path": str(lib) if lib is not None else None,
        "rust_admitted": rust_ok,
        "rust_admit_detail": rust_reason,
        "product_chart_will_try_rust": product_chart_should_try_rust(),
        "product_backtest_will_try_rust": product_backtest_should_try_rust(),
        "writes_authority": False,
        "architecture": {
            "authority": "python",
            "compute": "rust_preferred_python_fallback",
            "summary_zh": "Python 做權威（IB／帳本／seal）；Rust 做圖表同回測主循環計算；失敗自動 fallback Python。",
        },
        "authority_owners": {
            "ib_transport": "python",
            "paper_ledger": "python",
            "result_seal": "python",
            "promotion": "python",
            "batch_admission": "python",
            "web_api": "python",
            "chart_ohlc_ema_compute": "rust_when_admitted_else_python",
            "backtest_main_loop": "rust_when_admitted_else_python",
            "backtest_product_trade_seal": "rust_kernel_v1_when_admitted_else_trend_strategy",
            "trend_strategy_fsm": "rust_dual_run_bit_exact_else_python",
            "backtest_result_sqlite_export": "python",
        },
        "recent_errors": errors,
        "error_count": len(errors),
        "checked_at": _now_iso(),
    }


@router.get("/compute-errors")
def get_compute_errors(
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    """List recent compute incidents for the UI error drawer."""
    items = list_compute_errors(limit=limit)
    return {
        "schema": "compute_error_list.v1",
        "count": len(items),
        "errors": items,
        "checked_at": _now_iso(),
    }


@router.get("/compute-errors/{error_id}")
def get_compute_error_detail(error_id: str) -> dict[str, Any]:
    """Return one compute error for drill-down."""
    item = get_compute_error(error_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"compute error not found: {error_id}")
    return item


@router.get("/ib-status")
def get_ib_status() -> dict[str, Any]:
    """Report a three-state IB reachability summary; never raises on failure."""
    try:
        config = IbConnectionConfig.from_environment()
    except (ValueError, OSError) as exc:
        return {
            "schema": "ib_status.v1",
            "state": "unconfigured",
            "host": None,
            "port": None,
            "detail": f"IB 連接設定未就緒（{exc}）",
            "probe_timeout_seconds": _PROBE_TIMEOUT_SECONDS,
            "checked_at": _now_iso(),
        }

    reachable, reason = _probe_tcp(config.host, config.port)
    if reachable:
        state = "port_reachable_unverified"
        detail = "Gateway port 可達 · 未驗證 API session（未做 IB handshake）"
    else:
        # Not an error: a Gateway that is not running is a normal daily state.
        state = "port_unreachable"
        detail = f"Gateway port 不可達（{reason}）——Gateway 未開係正常狀態"
    return {
        "schema": "ib_status.v1",
        "state": state,
        "host": config.host,
        "port": config.port,
        "detail": detail,
        "probe_timeout_seconds": _PROBE_TIMEOUT_SECONDS,
        "checked_at": _now_iso(),
    }


def _probe_tcp(host: str, port: int) -> tuple[bool, str]:
    """Return whether a TCP connection can be opened within the probe timeout."""
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_SECONDS):
            return True, "connected"
    except (TimeoutError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
