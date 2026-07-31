"""Pydantic models for the strategy.v1 YAML document surface (docs/05 §1)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from futures_research.data.contracts import ContractAssetClass

ProvenanceSource = Literal[
    "owner_explicit",
    "owner_inferred",
    "web_researched",
    "market_convention",
    "system_default",
    "derived",
]

P1_STRUCTURE_TYPES: frozenset[str] = frozenset({"pullback_lifecycle", "signal_bar"})
P1_INDICATOR_TYPES: frozenset[str] = frozenset({"EMA", "ATR"})
P1_INVALIDATIONS: frozenset[str] = frozenset({"cross_reversal_per_layer", "day_end_clear"})
P1_SESSIONS: frozenset[str] = frozenset({"rth", "eth"})
P1_TRIO_BIAS = "D"
P1_TRIO_MID = "1H"
P1_TRIO_ENTRY = "5m"


class StrategyMeta(BaseModel):
    """Identity and lineage fields (sketch/insights are provenance-only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    created: date
    based_on: str | None = None
    # Identity validation belongs to parser layer 2 (references), so malformed
    # lineage values are reported beside the actionable correction instead of
    # being hidden behind a generic Pydantic type error.
    based_on_sketch: object | None = None
    based_on_sketch_origin: object | None = None
    based_on_insights: list[str] | None = None
    spec_ref: str | None = None

    @field_validator("name")
    @classmethod
    def require_nonempty_name(cls, value: str) -> str:
        """Reject blank strategy names."""
        cleaned = value.strip()
        if not cleaned:
            msg = "meta.name must not be empty"
            raise ValueError(msg)
        return cleaned


class UnquantifiedNote(BaseModel):
    """One AI-unresolved item the Owner must review (docs/05 iron gate)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    note: str
    action_needed: str | None = None


class UniverseSection(BaseModel):
    """Self-contained P2 instrument universe; values are never normalized by the UI/API."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary_instrument: str
    asset_class: ContractAssetClass
    contracts: list[str] = Field(min_length=1)
    expansion_rationale: dict[str, str]
    session: Literal["rth", "eth"]

    @field_validator("primary_instrument", "asset_class")
    @classmethod
    def require_exact_nonempty_text(cls, value: str) -> str:
        if not value or value != value.strip():
            msg = "universe values must be non-empty text without surrounding whitespace"
            raise ValueError(msg)
        return value

    @field_validator("contracts")
    @classmethod
    def require_unique_contracts(cls, value: list[str]) -> list[str]:
        """Keep universe symbols unique, explicit, and unmodified."""
        if any(not item or item != item.strip() for item in value):
            msg = "universe.contracts entries must be non-empty without surrounding whitespace"
            raise ValueError(msg)
        if len(set(value)) != len(value):
            msg = "universe.contracts must not contain duplicates"
            raise ValueError(msg)
        return value

    @field_validator("expansion_rationale")
    @classmethod
    def require_exact_nonempty_rationale_values(cls, value: dict[str, str]) -> dict[str, str]:
        """Do not silently trim, fill, or delete the Owner-visible expansion rationale."""
        for symbol, rationale in value.items():
            if not symbol or symbol != symbol.strip():
                msg = "universe.expansion_rationale keys must be exact non-blank symbols"
                raise ValueError(msg)
            if not rationale or rationale != rationale.strip():
                msg = "universe.expansion_rationale values must be non-blank text"
                raise ValueError(msg)
        return value


class TimeframeTrioDoc(BaseModel):
    """Declared trio timeframes (string labels as in strategy.v1 YAML)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bias: str
    mid: str
    entry: str


class TimeframesSection(BaseModel):
    """Trio plus entry-layer count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trio: TimeframeTrioDoc
    entry_layers: int = Field(ge=2, le=3)


class IndicatorDoc(BaseModel):
    """One named indicator instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    type: str
    period: int = Field(gt=0)

    @field_validator("id")
    @classmethod
    def require_nonempty_id(cls, value: str) -> str:
        """Indicator ids are reference targets."""
        cleaned = value.strip()
        if not cleaned:
            msg = "indicator id must not be empty"
            raise ValueError(msg)
        return cleaned


class StructureDoc(BaseModel):
    """One named structure (engine FSM); extra keys allowed for type-specific params."""

    model_config = ConfigDict(frozen=True, extra="allow")

    id: str
    type: str

    @field_validator("id")
    @classmethod
    def require_nonempty_id(cls, value: str) -> str:
        """Structure ids are reference targets."""
        cleaned = value.strip()
        if not cleaned:
            msg = "structure id must not be empty"
            raise ValueError(msg)
        return cleaned

    def as_mapping(self) -> dict[str, Any]:
        """Full structure dict including type-specific fields."""
        return self.model_dump()


class CalibrationValue(BaseModel):
    """Regime threshold with optional calibration method tag."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calibration: str | None = None
    value: float


class RegimeSection(BaseModel):
    """Daily regime gate settings (spec §11.2–11.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    require_trend: bool
    congestion_no_trade: bool
    sep_mult: CalibrationValue
    flat_mult: CalibrationValue


class DirectionSection(BaseModel):
    """Direction mode and layer-consistency policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: str
    layer_consistency: str


class EntrySequenceStep(BaseModel):
    """One ordered gate predicate in entry.sequence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: Literal["bias", "mid", "entry"]
    require: str


class EntryTrigger(BaseModel):
    """Breakout trigger declaration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    of: str | None = None
    mode: str | None = None


class EntrySection(BaseModel):
    """Entry sequence plus trigger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: list[EntrySequenceStep] = Field(min_length=1)
    trigger: EntryTrigger


class RiskStop(BaseModel):
    """Stop anchor and tick offset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor: str
    offset_ticks: int = Field(ge=0)


class RiskTarget(BaseModel):
    """Profit target definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    value: float = Field(gt=0)


class RiskSizing(BaseModel):
    """Position sizing declaration (documented; not all fields map to P1 engine)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    risk_pct: float = Field(gt=0, le=100)


class RiskSection(BaseModel):
    """Stop / target / sizing / daily loss limit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stop: RiskStop
    target: RiskTarget
    sizing: RiskSizing
    daily_loss_limit_r: float = Field(gt=0)


class ProvenanceEntry(BaseModel):
    """One path annotation for a numeric parameter (docs/05 §1.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    source: ProvenanceSource
    note: str | None = None

    @field_validator("path")
    @classmethod
    def require_nonempty_path(cls, value: str) -> str:
        """Provenance paths must be non-empty dotted paths."""
        cleaned = value.strip()
        if not cleaned:
            msg = "provenance path must not be empty"
            raise ValueError(msg)
        return cleaned


class StrategyDocument(BaseModel):
    """Full strategy.v1 document after YAML load + format-layer validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["strategy.v1"] = Field(alias="schema")
    meta: StrategyMeta
    rationale: str
    unquantified_notes: list[UnquantifiedNote | str] = Field(default_factory=list)
    universe: UniverseSection
    timeframes: TimeframesSection
    indicators: list[IndicatorDoc] = Field(min_length=1)
    structures: list[StructureDoc] = Field(min_length=1)
    regime: RegimeSection
    direction: DirectionSection
    entry: EntrySection
    invalidations: list[str] = Field(min_length=1)
    risk: RiskSection
    provenance: list[ProvenanceEntry] = Field(default_factory=list)

    @field_validator("rationale")
    @classmethod
    def require_rationale(cls, value: str) -> str:
        """Dimension-8 text is mandatory even if human-reviewed later."""
        cleaned = value.strip()
        if not cleaned:
            msg = "rationale must not be empty"
            raise ValueError(msg)
        return cleaned

    @model_validator(mode="after")
    def require_unique_ids(self) -> StrategyDocument:
        """Indicator and structure ids must be unique within the document."""
        indicator_ids = [item.id for item in self.indicators]
        if len(set(indicator_ids)) != len(indicator_ids):
            msg = "indicators ids must be unique"
            raise ValueError(msg)
        structure_ids = [item.id for item in self.structures]
        if len(set(structure_ids)) != len(structure_ids):
            msg = "structures ids must be unique"
            raise ValueError(msg)
        return self
