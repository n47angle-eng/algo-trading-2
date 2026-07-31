"""Product seam: policy → admit Rust → compute → validate; always Python fallback."""

from __future__ import annotations

import ctypes
import json
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from futures_research.backtest.chart_kernel_python import compute_chart_python
from futures_research.contracts.chart_compute import (
    ChartComputeDraft,
    ChartComputeRequest,
)
from futures_research.paths import PROJECT_ROOT
from futures_research.platform.backend_policy import (
    requested_backend_from_env,
    resolve_compute_backend,
)
from futures_research.platform.native_runtime import (
    NativeAdmissionError,
    admit_native_library,
    clear_native_registry,
)
from futures_research.platform.provenance import ComputeProvenance

# Search order for built dylib (dev + release).
_CANDIDATE_LIBS = (
    PROJECT_ROOT / "native" / "fr_compute" / "target" / "release" / "libfr_compute.dylib",
    PROJECT_ROOT / "native" / "fr_compute" / "target" / "release" / "libfr_compute.so",
    PROJECT_ROOT / "native" / "fr_compute" / "target" / "debug" / "libfr_compute.dylib",
    PROJECT_ROOT / "native" / "fr_compute" / "target" / "debug" / "libfr_compute.so",
    PROJECT_ROOT / "data" / "native" / "admitted",
)


def chart_compute_v1(
    request: ChartComputeRequest,
    *,
    force_backend: str | None = None,
) -> ChartComputeDraft:
    """Coarse-grained chart compute. Never writes authority stores."""
    requested = force_backend or request.requested_backend
    if requested not in {"stable", "accelerated", "auto"}:
        requested = requested_backend_from_env()
    rust_ok, rust_reason = _try_load_rust()
    effective, fallback = resolve_compute_backend(
        feature="chart_compute_v1",
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
                feature="chart_compute_v1",
                requested=requested,  # type: ignore[arg-type]
                effective="rust",
                fallback_reason=None,
                input_sha256=input_hash,
                elapsed_ms=elapsed,
            )
        except Exception as exc:  # FULL_JOB_PYTHON_FALLBACK — discard partial
            fallback = f"rust_failed:{type(exc).__name__}:{exc}"
            effective = "python"

    draft = compute_chart_python(request)
    elapsed = (time.perf_counter() - started) * 1000.0
    return _with_provenance(
        draft,
        feature="chart_compute_v1",
        requested=requested,  # type: ignore[arg-type]
        effective="python",
        fallback_reason=fallback,
        input_sha256=input_hash,
        elapsed_ms=elapsed,
    )


def _with_provenance(
    draft: ChartComputeDraft,
    *,
    feature: str,
    requested: str,
    effective: str,
    fallback_reason: str | None,
    input_sha256: str,
    elapsed_ms: float,
) -> ChartComputeDraft:
    prov = ComputeProvenance(
        feature=feature,
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
    return ChartComputeDraft.model_validate(data)


def _try_load_rust() -> tuple[bool, str | None]:
    lib_path = _find_lib()
    if lib_path is None:
        return False, "dylib_not_found"
    try:
        cap_path = PROJECT_ROOT / "native" / "fr_compute" / "fr_compute.capabilities"
        # Ensure capabilities file is next to dylib for admission.
        side = Path(str(lib_path) + ".capabilities")
        if not side.is_file() and cap_path.is_file():
            side.write_text(cap_path.read_text(encoding="utf-8"), encoding="utf-8")
        admit_native_library(
            lib_path,
            module_key="fr_compute",
            required_capabilities=(
                "chart_compute_v1",
                "backtest_loop_v1",
                "trend_strategy_v1",
                "writes_authority=false",
            ),
            admit_root=PROJECT_ROOT / "data" / "native" / "admitted",
        )
        return True, None
    except NativeAdmissionError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, str(exc)


def _find_lib() -> Path | None:
    for candidate in _CANDIDATE_LIBS:
        if candidate.is_dir():
            for child in candidate.glob("libfr_compute.*"):
                if child.suffix in {".dylib", ".so"} and child.is_file():
                    return child
            continue
        if candidate.is_file():
            return candidate
    return None


def _compute_rust(request: ChartComputeRequest) -> ChartComputeDraft:
    lib_path = _find_lib()
    if lib_path is None:
        raise RuntimeError("rust dylib missing")
    admitted = admit_native_library(
        lib_path,
        module_key="fr_compute",
        required_capabilities=(
            "chart_compute_v1",
            "backtest_loop_v1",
            "trend_strategy_v1",
            "writes_authority=false",
        ),
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

    # C ABI: fr_chart_compute_v1(in_ptr, in_len, out_ptr, out_len) -> i32
    # out_ptr is **u8 allocated by Rust; free with fr_string_free.
    lib.fr_chart_compute_v1.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.fr_chart_compute_v1.restype = ctypes.c_int32
    lib.fr_string_free.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.fr_string_free.restype = None

    out_ptr = ctypes.c_void_p()
    out_len = ctypes.c_size_t()
    buf = ctypes.create_string_buffer(req_bytes)
    rc = lib.fr_chart_compute_v1(
        ctypes.cast(buf, ctypes.c_void_p),
        len(req_bytes),
        ctypes.byref(out_ptr),
        ctypes.byref(out_len),
    )
    if rc != 0 or not out_ptr.value:
        raise RuntimeError(f"rust chart_compute failed rc={rc}")
    try:
        raw = ctypes.string_at(out_ptr.value, out_len.value)
        payload = json.loads(raw.decode("utf-8"))
        return ChartComputeDraft.model_validate(payload)
    finally:
        lib.fr_string_free(out_ptr, out_len)


def reset_chart_compute_state_for_tests() -> None:
    clear_native_registry()
