"""Provider-neutral data models used by the canonical store."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CanonicalBar(BaseModel):
    """One start-timestamped, one-minute OHLCV bar in the canonical schema.

    Structural price checks deliberately live in :mod:`quality`, where they can be reported to the
    Owner rather than silently deleting questionable source data.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    contract_id: str
    source: str = "IB"
    source_request_id: str | None = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("timestamp", "ingested_at", mode="after")
    @classmethod
    def normalize_to_utc(cls, value: datetime) -> datetime:
        """Require an aware instant and normalize it to the canonical UTC policy."""
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "timestamps must include a timezone"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def require_finite_price(cls, value: float) -> float:
        """Prevent NaN and infinity from entering the Parquet schema."""
        if not math.isfinite(value):
            msg = "prices must be finite"
            raise ValueError(msg)
        return value

    @field_validator("contract_id", "source")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        """Keep partition keys and source audit fields usable."""
        normalized = value.strip()
        if not normalized:
            msg = "value must not be empty"
            raise ValueError(msg)
        return normalized

    def to_record(self) -> dict[str, object]:
        """Return a record with the exact column names used by canonical Parquet."""
        return {
            "ts_event": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "contract_id": self.contract_id,
            "source": self.source,
            "source_request_id": self.source_request_id,
            "ingested_at": self.ingested_at,
        }


QualityCategory = Literal["completeness", "reasonableness", "anomaly", "consistency"]
QualitySeverity = Literal["error", "warning", "info"]


class QualityIssue(BaseModel):
    """One auditable finding from a data-quality check."""

    category: QualityCategory
    severity: QualitySeverity
    code: str
    message: str
    timestamp: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class QualityReport(BaseModel):
    """Serializable result for all four mandatory quality checks."""

    schema_version: int = 1
    contract_id: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_bars: int
    checks: dict[str, str]
    issues: list[QualityIssue]

    @property
    def has_errors(self) -> bool:
        """Indicate whether at least one finding needs operator attention."""
        return any(issue.severity == "error" for issue in self.issues)
