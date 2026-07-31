"""Native library admission: content-hash, size cap, no symlink, capability check."""

from __future__ import annotations

import ctypes
import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any

# Default 64 MiB cap for admitted native blobs.
DEFAULT_MAX_NATIVE_BYTES = 64 * 1024 * 1024

_REGISTRY_LOCK = Lock()
_REGISTRY: dict[str, "AdmittedNative"] = {}

# Substrings that must never appear as exported symbol names (authority surface).
_FORBIDDEN_EXPORT_MARKERS = (
    "placeOrder",
    "cancelOrder",
    "reqGlobalCancel",
    "write_authority",
    "publish_run",
    "sqlite3_exec",
)


class NativeAdmissionError(RuntimeError):
    """Native library failed fail-closed admission."""


@dataclass(frozen=True, slots=True)
class AdmittedNative:
    module_key: str
    path: Path
    sha256: str
    size_bytes: int
    capabilities: tuple[str, ...]
    writes_authority: bool
    lib: Any  # ctypes.CDLL


def assert_no_authority_exports(symbol_names: list[str]) -> None:
    """Static/runtime guard: native must not export authority-facing symbols."""
    for name in symbol_names:
        for marker in _FORBIDDEN_EXPORT_MARKERS:
            if marker.lower() in name.lower():
                raise NativeAdmissionError(
                    f"forbidden authority-facing export: {name}"
                )


def admit_native_library(
    source_path: Path,
    *,
    module_key: str,
    expected_sha256: str | None = None,
    max_bytes: int = DEFAULT_MAX_NATIVE_BYTES,
    required_capabilities: tuple[str, ...] = ("chart_compute_v1",),
    admit_root: Path | None = None,
) -> AdmittedNative:
    """Admit a regular-file native library after hash/size/capability checks.

    - Rejects symlinks (follow-link open refused via ``lstat`` + non-link check).
    - Size cap and re-stat after read to catch swap-under-read.
    - Optional content-addressed copy under ``admit_root``.
    - Process-local single registry per module_key + sha256.
    """
    path = Path(source_path)
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise NativeAdmissionError("symlink native library rejected")
    if not stat.S_ISREG(st.st_mode):
        raise NativeAdmissionError("native library must be a regular file")
    if st.st_size > max_bytes:
        raise NativeAdmissionError(
            f"native library exceeds size cap ({st.st_size} > {max_bytes})"
        )
    if st.st_size <= 0:
        raise NativeAdmissionError("native library is empty")

    data = path.read_bytes()
    st_after = path.lstat()
    if st_after.st_size != st.st_size or st_after.st_mtime_ns != st.st_mtime_ns:
        raise NativeAdmissionError("native library changed during read")
    if len(data) != st.st_size:
        raise NativeAdmissionError("native library size mismatch after read")

    digest = sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise NativeAdmissionError("native library sha256 mismatch")

    # Capability sidecar (optional JSON next to library) or embedded env.
    capabilities = _load_capabilities(path)
    if not capabilities:
        raise NativeAdmissionError("native capability list is empty")
    for req in required_capabilities:
        if req not in capabilities:
            raise NativeAdmissionError(f"missing required capability: {req}")
    if "writes_authority=true" in capabilities:
        raise NativeAdmissionError("writes_authority=true is forbidden")
    if "writes_authority=false" not in capabilities:
        raise NativeAdmissionError("writes_authority=false capability required")

    publish_path = path
    if admit_root is not None:
        admit_root = Path(admit_root)
        admit_root.mkdir(parents=True, exist_ok=True)
        publish_path = admit_root / f"{module_key}-{digest[:16]}.dylib"
        if not publish_path.exists():
            # Atomic-ish write then replace.
            tmp = publish_path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, publish_path)

    registry_key = f"{module_key}:{digest}"
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(registry_key)
        if existing is not None:
            return existing
        lib = ctypes.CDLL(str(publish_path))
        admitted = AdmittedNative(
            module_key=module_key,
            path=publish_path,
            sha256=digest,
            size_bytes=len(data),
            capabilities=capabilities,
            writes_authority=False,
            lib=lib,
        )
        _REGISTRY[registry_key] = admitted
        return admitted


def clear_native_registry() -> None:
    """Test helper."""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()


def _load_capabilities(lib_path: Path) -> tuple[str, ...]:
    side = lib_path.with_suffix(lib_path.suffix + ".capabilities")
    alt = lib_path.parent / f"{lib_path.name}.capabilities"
    for candidate in (side, alt):
        if candidate.is_file() and not candidate.is_symlink():
            text = candidate.read_text(encoding="utf-8")
            parts = tuple(
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
            return parts
    # Built-in default for our crate when sidecar shipped beside source build.
    default = lib_path.parent / "fr_compute.capabilities"
    if default.is_file() and not default.is_symlink():
        text = default.read_text(encoding="utf-8")
        return tuple(
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    return ()
