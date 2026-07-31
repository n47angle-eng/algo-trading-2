"""Process-local compute error ring buffer for UI drill-down (Phase E)."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

ErrorSeverity = Literal["error", "warn", "info"]

_LOCK = Lock()
_ERRORS: deque[ComputeErrorRecord] = deque(maxlen=50)


@dataclass(frozen=True, slots=True)
class ComputeErrorRecord:
    id: str
    schema: str = "compute_error.v1"
    feature: str = ""
    severity: ErrorSeverity = "error"
    message: str = ""
    detail: str = ""
    tip: str = ""
    effective_backend: str | None = None
    requested_backend: str | None = None
    fallback_used: bool = False
    writes_authority: bool = False
    context: dict[str, Any] = field(default_factory=dict)
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def record_compute_error(
    *,
    feature: str,
    message: str,
    detail: str = "",
    tip: str = "",
    severity: ErrorSeverity = "error",
    effective_backend: str | None = None,
    requested_backend: str | None = None,
    fallback_used: bool = False,
    context: dict[str, Any] | None = None,
) -> ComputeErrorRecord:
    """Append one operator-visible compute incident."""
    rec = ComputeErrorRecord(
        id=uuid4().hex[:12],
        feature=feature,
        severity=severity,
        message=message,
        detail=detail or message,
        tip=tip
        or "可設 FR_COMPUTE_BACKEND=stable 強制 Python；或檢查 native dylib / FR_RUST_DISABLE。",
        effective_backend=effective_backend,
        requested_backend=requested_backend,
        fallback_used=fallback_used,
        writes_authority=False,
        context=dict(context or {}),
        at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    with _LOCK:
        _ERRORS.appendleft(rec)
    return rec


def list_compute_errors(*, limit: int = 20) -> list[dict[str, Any]]:
    with _LOCK:
        items = list(_ERRORS)[: max(1, min(limit, 50))]
    return [item.to_dict() for item in items]


def get_compute_error(error_id: str) -> dict[str, Any] | None:
    with _LOCK:
        for item in _ERRORS:
            if item.id == error_id:
                return item.to_dict()
    return None


def clear_compute_errors_for_tests() -> None:
    with _LOCK:
        _ERRORS.clear()
