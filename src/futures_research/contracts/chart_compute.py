"""chart_compute_request.v1 / chart_compute_draft.v1 wire models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ChartBar(StrictModel):
    t: str = Field(min_length=1)  # UTC Z
    o: float
    h: float
    l: float
    c: float
    v: float

    @field_validator("o", "h", "l", "c", "v")
    @classmethod
    def finite(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite number rejected")
        return value


class ChartComputeRequest(StrictModel):
    schema_name: Literal["chart_compute_request.v1"] = Field(alias="schema")
    timeframe_minutes: int = Field(ge=1, le=1440)
    ema_period: int = Field(ge=1, le=500)
    bars: list[ChartBar]
    requested_backend: Literal["stable", "accelerated", "auto"] = "auto"

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class ChartPoint(StrictModel):
    t: str
    o: float
    h: float
    l: float
    c: float
    v: float
    ema: float

    @field_validator("o", "h", "l", "c", "v", "ema")
    @classmethod
    def finite(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite number rejected")
        return value


class ChartComputeDraft(StrictModel):
    schema_name: Literal["chart_compute_draft.v1"] = Field(alias="schema")
    timeframe_minutes: int
    ema_period: int
    points: list[ChartPoint]
    artifact_sha256: str
    provenance: dict[str, Any]

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
