"""Phase E: backtest_loop_v1 seam — prefer Rust, full-job Python fallback."""

from __future__ import annotations

import ctypes
import json
import time
from hashlib import sha256
from typing import Any

from futures_research.backtest.backtest_kernel_python import compute_backtest_loop_python
from futures_research.contracts.backtest_loop import BacktestLoopDraft, BacktestLoopRequest
from futures_research.paths import PROJECT_ROOT
from futures_research.platform.backend_policy import (
    requested_backend_from_env,
    resolve_compute_backend,
)
from futures_research.platform.chart_compute_seam import _find_lib, _try_load_rust
from futures_research.platform.compute_errors import record_compute_error
from futures_research.platform.native_runtime import (
    NativeAdmissionError,
    admit_native_library,
    clear_native_registry,
)
from futures_research.platform.provenance import ComputeProvenance

_REQUIRED_CAPS = (
    "backtest_loop_v1",
    "chart_compute_v1",
    "trend_strategy_v1",
    "writes_authority=false",
)


def backtest_loop_v1(
    request: BacktestLoopRequest,
    *,
    force_backend: str | None = None,
    context: dict[str, Any] | None = None,
) -> BacktestLoopDraft:
    """Closed-bar dual-EMA draft loop. Never writes authority stores."""
    requested = force_backend or request.requested_backend
    if requested not in {"stable", "accelerated", "auto"}:
        requested = requested_backend_from_env()
    rust_ok, rust_reason = _try_load_rust_backtest()
    effective, fallback = resolve_compute_backend(
        feature="backtest_loop_v1",
        requested=requested,  # type: ignore[arg-type]
        rust_available=rust_ok,
        rust_qualified=rust_ok,
        authority_path=False,
    )
    input_hash = sha256(
        json.dumps(
            request.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    started = time.perf_counter()

    if effective == "rust":
        try:
            draft = _compute_rust(request)
            elapsed = (time.perf_counter() - started) * 1000.0
            return _with_provenance(
                draft,
                requested=requested,  # type: ignore[arg-type]
                effective="rust",
                fallback_reason=None,
                input_sha256=input_hash,
                elapsed_ms=elapsed,
            )
        except Exception as exc:  # FULL_JOB_PYTHON_FALLBACK
            fallback = f"rust_failed:{type(exc).__name__}:{exc}"
            record_compute_error(
                feature="backtest_loop_v1",
                message="Rust 回測主循環失敗，已 fallback 到 Python",
                detail=str(exc),
                tip="檢查 libfr_compute 同 backtest_loop_v1 capability；或設 FR_COMPUTE_BACKEND=stable。",
                severity="warn",
                effective_backend="python",
                requested_backend=requested,
                fallback_used=True,
                context={
                    **(context or {}),
                    "rust_reason": rust_reason,
                    "error_type": type(exc).__name__,
                },
            )
            effective = "python"

    draft = compute_backtest_loop_python(request)
    elapsed = (time.perf_counter() - started) * 1000.0
    return _with_provenance(
        draft,
        requested=requested,  # type: ignore[arg-type]
        effective="python",
        fallback_reason=fallback,
        input_sha256=input_hash,
        elapsed_ms=elapsed,
    )


def product_backtest_should_try_rust(*, force_backend: str | None = None) -> bool:
    requested = force_backend or requested_backend_from_env()
    if requested == "stable":
        return False
    rust_ok, _ = _try_load_rust_backtest()
    effective, _ = resolve_compute_backend(
        feature="backtest_loop_v1",
        requested=requested,  # type: ignore[arg-type]
        rust_available=rust_ok,
        rust_qualified=rust_ok,
        authority_path=False,
    )
    return effective == "rust"


def _with_provenance(
    draft: BacktestLoopDraft,
    *,
    requested: str,
    effective: str,
    fallback_reason: str | None,
    input_sha256: str,
    elapsed_ms: float,
) -> BacktestLoopDraft:
    prov = ComputeProvenance(
        feature="backtest_loop_v1",
        requested_backend=requested,  # type: ignore[arg-type]
        effective_backend=effective,  # type: ignore[arg-type]
        fallback_reason=fallback_reason,
        artifact_sha256=draft.artifact_sha256,
        input_sha256=input_sha256,
        elapsed_ms=elapsed_ms,
        writes_authority=False,
    )
    data = draft.model_dump(mode="json", by_alias=True)
    data["provenance"] = prov.to_dict()
    return BacktestLoopDraft.model_validate(data)


def _try_load_rust_backtest() -> tuple[bool, str | None]:
    # Reuse chart admit path; require expanded capabilities including backtest_loop_v1.
    lib_path = _find_lib()
    if lib_path is None:
        return False, "dylib_not_found"
    try:
        from pathlib import Path

        cap_path = PROJECT_ROOT / "native" / "fr_compute" / "fr_compute.capabilities"
        side = Path(str(lib_path) + ".capabilities")
        if not side.is_file() and cap_path.is_file():
            side.write_text(cap_path.read_text(encoding="utf-8"), encoding="utf-8")
        admit_native_library(
            lib_path,
            module_key="fr_compute",
            required_capabilities=_REQUIRED_CAPS,
            admit_root=PROJECT_ROOT / "data" / "native" / "admitted",
        )
        return True, None
    except NativeAdmissionError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, str(exc)


def _compute_rust(request: BacktestLoopRequest) -> BacktestLoopDraft:
    lib_path = _find_lib()
    if lib_path is None:
        raise RuntimeError("rust dylib missing")
    admitted = admit_native_library(
        lib_path,
        module_key="fr_compute",
        required_capabilities=_REQUIRED_CAPS,
        admit_root=PROJECT_ROOT / "data" / "native" / "admitted",
    )
    lib = admitted.lib
    req_bytes = json.dumps(
        request.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    lib.fr_backtest_loop_v1.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.fr_backtest_loop_v1.restype = ctypes.c_int32
    lib.fr_string_free.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.fr_string_free.restype = None

    out_ptr = ctypes.c_void_p()
    out_len = ctypes.c_size_t()
    buf = ctypes.create_string_buffer(req_bytes)
    rc = lib.fr_backtest_loop_v1(
        ctypes.cast(buf, ctypes.c_void_p),
        len(req_bytes),
        ctypes.byref(out_ptr),
        ctypes.byref(out_len),
    )
    if rc != 0 or not out_ptr.value:
        raise RuntimeError(f"rust backtest_loop failed rc={rc}")
    try:
        raw = ctypes.string_at(out_ptr.value, out_len.value)
        payload = json.loads(raw.decode("utf-8"))
        return BacktestLoopDraft.model_validate(payload)
    finally:
        lib.fr_string_free(out_ptr, out_len)


def reset_backtest_loop_state_for_tests() -> None:
    clear_native_registry()
