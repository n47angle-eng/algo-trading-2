"""Strict P6 runtime wire contracts and identity helpers."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from futures_research.paper.timeframes import (
    DEFAULT_TIMEFRAME_CAPABILITIES,
    TimeframeRegistry,
)

Sha256Text = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TraderId = Annotated[str, StringConstraints(pattern=r"^trader-[0-9a-f]{32}$")]
PaperAccountId = Annotated[
    str,
    StringConstraints(pattern=r"^paper-account-[0-9a-f]{32}$"),
]
PaperLedgerId = Annotated[
    str,
    StringConstraints(pattern=r"^paper-ledger-[0-9a-f]{32}$"),
]

MarketMode = Literal["live", "test_delayed", "replay_test"]
PublicMarketMode = Literal["live", "test_delayed"]
LifecycleState = Literal[
    "provisioned",
    "starting",
    "running",
    "pausing",
    "paused",
    "tripped",
    "stopping",
    "recovery_required",
    "permanently_stopped",
]

LIFECYCLE_STATES: tuple[LifecycleState, ...] = (
    "provisioned",
    "starting",
    "running",
    "pausing",
    "paused",
    "tripped",
    "stopping",
    "recovery_required",
    "permanently_stopped",
)


class StrictRuntimeModel(BaseModel):
    """Strict, frozen, alias-only public model."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        populate_by_name=False,
    )


class RuntimeTimeframeSelection(StrictRuntimeModel):
    market_input: str = Field(min_length=1, max_length=16)
    execution: str = Field(min_length=1, max_length=16)
    chart_display: str = Field(min_length=1, max_length=16)


class PaperTraderSelectionV2(StrictRuntimeModel):
    strategy_id: str = Field(min_length=1, max_length=256)
    content_sha256: Sha256Text
    contract_id: str = Field(min_length=1, max_length=256)
    baseline_run_id: str = Field(min_length=1, max_length=256)
    baseline_result_sha256: Sha256Text
    timeframes: RuntimeTimeframeSelection

    @field_validator("strategy_id", "contract_id", "baseline_run_id")
    @classmethod
    def require_exact_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("identity text must not contain surrounding whitespace")
        return value


class PaperTraderCreateRequestV2(StrictRuntimeModel):
    schema_name: Literal["paper_trader_create_request.v2"] = Field(alias="schema")
    request_id: str = Field(min_length=1, max_length=64)
    selection: PaperTraderSelectionV2

    @field_validator("request_id")
    @classmethod
    def require_uuid4(cls, value: str) -> str:
        return _canonical_uuid4(value)


class StrategyTimeframeProfileWire(StrictRuntimeModel):
    bias: str
    mid: str
    entry: str
    source: Literal["strategy.v1"]
    client_override: Literal[False]


class PaperSafetyLimits(StrictRuntimeModel):
    max_drawdown_r: Literal[8]
    max_losing_streak: Literal[8]
    blind_minutes: Literal[5]


class PaperTraderV2(StrictRuntimeModel):
    schema_name: Literal["paper_trader.v2"] = Field(alias="schema")
    trader_id: TraderId
    request_id: str
    selection: PaperTraderSelectionV2
    selection_fingerprint: Sha256Text
    strategy_timeframe_profile: StrategyTimeframeProfileWire
    lifecycle: LifecycleState
    lifecycle_version: int = Field(ge=1)
    lifecycle_reason: str = Field(min_length=1)
    account_id: PaperAccountId
    ledger_origin_id: PaperLedgerId
    safety: PaperSafetyLimits
    created_at: str

    @field_validator("request_id")
    @classmethod
    def require_uuid4(cls, value: str) -> str:
        return _canonical_uuid4(value)

    @field_validator("created_at")
    @classmethod
    def require_created_at(cls, value: str) -> str:
        return _canonical_utc_text(value)


class PaperTraderListV2(StrictRuntimeModel):
    schema_name: Literal["paper_trader_list.v2"] = Field(alias="schema")
    count: int = Field(ge=0)
    traders: tuple[PaperTraderV2, ...]

    @model_validator(mode="after")
    def require_count(self) -> PaperTraderListV2:
        if self.count != len(self.traders):
            raise ValueError("count must equal traders length")
        return self


class PaperTraderRequestStatusV2(StrictRuntimeModel):
    schema_name: Literal["paper_trader_request_status.v2"] = Field(alias="schema")
    request_id: str
    status: Literal["created"]
    trader: PaperTraderV2

    @field_validator("request_id")
    @classmethod
    def require_uuid4(cls, value: str) -> str:
        return _canonical_uuid4(value)


class EnabledTimeframes(StrictRuntimeModel):
    enabled: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique(self) -> EnabledTimeframes:
        if len(set(self.enabled)) != len(self.enabled):
            raise ValueError("enabled timeframe values must be unique")
        return self


class StrategyProfileCapability(StrictRuntimeModel):
    source: Literal["strategy.v1"]
    client_override: Literal[False]


class RuntimeTimeframeCapabilities(StrictRuntimeModel):
    market_input: EnabledTimeframes
    execution: EnabledTimeframes
    chart_display: EnabledTimeframes
    strategy_profile: StrategyProfileCapability


class PaperRuntimeCapabilities(StrictRuntimeModel):
    schema_name: Literal["paper_runtime_capabilities.v1"] = Field(alias="schema")
    timeframes: RuntimeTimeframeCapabilities
    market_modes: tuple[PublicMarketMode, ...]
    safety_defaults: PaperSafetyLimits
    lifecycle_states: tuple[LifecycleState, ...]
    as_of: str

    @field_validator("as_of")
    @classmethod
    def require_as_of(cls, value: str) -> str:
        return _canonical_utc_text(value)

    @model_validator(mode="after")
    def require_closed_order(self) -> PaperRuntimeCapabilities:
        if self.market_modes != ("live", "test_delayed"):
            raise ValueError("market_modes must use canonical order")
        if self.lifecycle_states != LIFECYCLE_STATES:
            raise ValueError("lifecycle_states must use canonical order")
        return self


def build_runtime_capabilities(
    registry: TimeframeRegistry = DEFAULT_TIMEFRAME_CAPABILITIES,
    *,
    as_of: datetime | None = None,
) -> PaperRuntimeCapabilities:
    """Build the public response from the single backend registry."""
    payload = registry.public_capabilities(as_of=as_of or datetime.now(UTC))
    timeframes = payload["timeframes"]
    assert isinstance(timeframes, dict)
    for role in ("market_input", "execution", "chart_display"):
        capability = timeframes[role]
        assert isinstance(capability, dict)
        enabled = capability["enabled"]
        assert isinstance(enabled, list)
        capability["enabled"] = tuple(enabled)
    market_modes = payload["market_modes"]
    lifecycle_states = payload["lifecycle_states"]
    assert isinstance(market_modes, list)
    assert isinstance(lifecycle_states, list)
    payload["market_modes"] = tuple(market_modes)
    payload["lifecycle_states"] = tuple(lifecycle_states)
    return PaperRuntimeCapabilities.model_validate(payload)


def validate_selection_timeframes(
    selection: PaperTraderSelectionV2,
    registry: TimeframeRegistry = DEFAULT_TIMEFRAME_CAPABILITIES,
) -> None:
    """Fail closed when a role is unknown or disabled."""
    registry.require("market_input", selection.timeframes.market_input)
    registry.require("execution", selection.timeframes.execution)
    registry.require("chart_display", selection.timeframes.chart_display)


def selection_fingerprint(selection: PaperTraderSelectionV2) -> str:
    """Hash exact canonical immutable selection bytes, including timeframe roles."""
    canonical = json.dumps(
        selection.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _canonical_uuid4(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ValueError("request_id must be a canonical UUID4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("request_id must be a canonical lowercase UUID4")
    return value


_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


def _canonical_utc_text(value: str) -> str:
    if _UTC_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp must be canonical UTC text ending in Z")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.now(UTC).utcoffset():
        raise ValueError("timestamp must be UTC")
    return value
