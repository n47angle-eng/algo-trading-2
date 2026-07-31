"""Backend selection: stable | accelerated | auto. Authority paths have no Rust option."""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Literal

BackendMode = Literal["stable", "accelerated", "auto"]
ComputeBackend = Literal["python", "rust"]


class AuthorityPath(StrEnum):
    """Paths that must never select a Rust backend."""

    PAPER_LEDGER = "paper_ledger"
    IB_TRANSPORT = "ib_transport"
    RESULT_SEAL = "result_seal"
    PROMOTION = "promotion"
    BATCH_ADMISSION = "batch_admission"
    OMS = "oms"


def rust_globally_disabled() -> bool:
    return os.environ.get("FR_RUST_DISABLE", "").strip() in {"1", "true", "TRUE", "yes"}


# Production default: prefer Rust compute when the native library is admitted;
# full-job Python fallback remains mandatory. Authority paths still force Python.
PRODUCTION_DEFAULT_BACKEND: BackendMode = "auto"


def requested_backend_from_env(
    *,
    default: BackendMode = PRODUCTION_DEFAULT_BACKEND,
) -> BackendMode:
    """Resolve ``FR_COMPUTE_BACKEND``; unset → production default ``auto``."""
    raw = os.environ.get("FR_COMPUTE_BACKEND", default).strip().lower()
    if raw in {"stable", "accelerated", "auto"}:
        return raw  # type: ignore[return-value]
    return default


def resolve_compute_backend(
    *,
    feature: str,
    requested: BackendMode,
    rust_available: bool,
    rust_qualified: bool,
    authority_path: bool = False,
) -> tuple[ComputeBackend, str | None]:
    """Return (effective_backend, fallback_reason_or_none).

    Authority paths always force Python.
    """
    del feature  # reserved for sticky per-feature policy
    if authority_path:
        return "python", "authority_path_forced_python"
    if rust_globally_disabled():
        return "python", "FR_RUST_DISABLE"
    if requested == "stable":
        return "python", None
    if not rust_available:
        if requested == "accelerated":
            return "python", "rust_unavailable_accelerated_fallback"
        return "python", "rust_unavailable"
    if not rust_qualified:
        if requested == "accelerated":
            return "python", "rust_not_qualified_accelerated_fallback"
        return "python", "rust_not_qualified"
    if requested in {"accelerated", "auto"}:
        return "rust", None
    return "python", None
