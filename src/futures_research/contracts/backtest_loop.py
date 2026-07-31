"""backtest_loop_request.v1 / backtest_loop_draft.v1 wire models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LoopBar(StrictModel):
    t: str = Field(min_length=1)
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


class BacktestLoopRequest(StrictModel):
    schema_name: Literal["backtest_loop_request.v1"] = Field(alias="schema")
    timeframe_minutes: int = Field(ge=1, le=1440)
    ema_fast: int = Field(ge=1, le=200)
    ema_slow: int = Field(ge=2, le=500)
    quantity: int = Field(ge=1, le=1000)
    point_value: float = Field(gt=0)
    commission_per_side: float = Field(ge=0)
    bars: list[LoopBar]
    requested_backend: Literal["stable", "accelerated", "auto"] = "auto"

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    @field_validator("ema_slow")
    @classmethod
    def slow_gt_fast(cls, value: int, info: Any) -> int:
        fast = info.data.get("ema_fast")
        if isinstance(fast, int) and value <= fast:
            raise ValueError("ema_slow must be greater than ema_fast")
        return value


class LoopTrade(StrictModel):
    entry_t: str
    exit_t: str
    direction: Literal["long", "short"]
    entry_price: float
    exit_price: float
    quantity: int
    gross_points: float
    net_pnl: float


class LoopEquityPoint(StrictModel):
    t: str
    equity: float


class BacktestLoopDraft(StrictModel):
    schema_name: Literal["backtest_loop_draft.v1"] = Field(alias="schema")
    timeframe_minutes: int
    ema_fast: int
    ema_slow: int
    trades: list[LoopTrade]
    equity_curve: list[LoopEquityPoint]
    trade_count: int
    net_pnl: float
    artifact_sha256: str
    provenance: dict[str, Any]

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
