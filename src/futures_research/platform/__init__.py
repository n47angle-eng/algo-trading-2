"""Platform adapters: native admission, backend policy, provenance."""

from futures_research.platform.backend_policy import (
    BackendMode,
    ComputeBackend,
    resolve_compute_backend,
)
from futures_research.platform.native_runtime import (
    NativeAdmissionError,
    admit_native_library,
    assert_no_authority_exports,
)
from futures_research.platform.provenance import ComputeProvenance

__all__ = [
    "BackendMode",
    "ComputeBackend",
    "ComputeProvenance",
    "NativeAdmissionError",
    "admit_native_library",
    "assert_no_authority_exports",
    "resolve_compute_backend",
]
