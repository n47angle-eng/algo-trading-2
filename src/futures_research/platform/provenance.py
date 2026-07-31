"""Visible compute provenance attached to drafts and optional product metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ComputeBackend = Literal["python", "rust"]
BackendMode = Literal["stable", "accelerated", "auto"]


@dataclass(frozen=True, slots=True)
class ComputeProvenance:
    schema: Literal["compute_provenance.v1"] = "compute_provenance.v1"
    feature: str = ""
    requested_backend: BackendMode = "stable"
    effective_backend: ComputeBackend = "python"
    fallback_reason: str | None = None
    artifact_sha256: str | None = None
    input_sha256: str | None = None
    elapsed_ms: float | None = None
    peak_rss_bytes: int | None = None
    writes_authority: Literal[False] = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
