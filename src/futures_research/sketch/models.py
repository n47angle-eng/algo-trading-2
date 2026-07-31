"""Pydantic surface for the ``sketch.v1`` package metadata (docs/05 §3.5)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from futures_research.sketch.identity import parse_sketch_id, parse_sketch_origin

EXPECTED_CHART_FILES = frozenset(
    {
        "chart-D.png",
        "chart-1H.png",
        "chart-30m.png",
        "chart-5m.png",
    }
)
_DATE_LABEL = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_PERIOD_INDICATOR = re.compile(r"^(?:ema|sma|atr|rsi)[1-9][0-9]*$")
_SINGLE_INDICATOR = re.compile(r"^(?:vwap|volume|macd)$")
_BOLLINGER_INDICATOR = re.compile(r"^bb[1-9][0-9]*_(?P<width>[0-9]+(?:\.[0-9]+)?)$")


def parse_date_label(value: object, *, field: str) -> date:
    """Accept only a calendar date object or exact YYYY-MM-DD date label."""
    if isinstance(value, datetime):
        msg = f"{field} is a date label (YYYY-MM-DD), not a timestamp"
        raise ValueError(msg)
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or _DATE_LABEL.fullmatch(value) is None:
        msg = f"{field} must be a date label in exact YYYY-MM-DD form"
        raise ValueError(msg)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        msg = f"{field} must contain a real calendar date"
        raise ValueError(msg) from exc


def is_canonical_indicator_token(value: object) -> bool:
    """Return whether an indicator belongs to the compact ``sketch.v1`` vocabulary."""
    if not isinstance(value, str):
        return False
    if _PERIOD_INDICATOR.fullmatch(value) or _SINGLE_INDICATOR.fullmatch(value):
        return True
    match = _BOLLINGER_INDICATOR.fullmatch(value)
    return match is not None and float(match.group("width")) > 0


class SketchChart(BaseModel):
    """One PNG plus the Owner's machine-readable chart annotation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    timeframe: str
    role: Literal["bias", "mid", "auxiliary", "entry"] | None = None
    range: tuple[date, date] | None = None
    indicators_shown: list[str]
    indicators_other: list[str] | None = None
    drawings: list[dict[str, Any]] | None = None
    owner_view: str

    @field_validator("role", "indicators_other", "drawings", mode="before")
    @classmethod
    def reject_explicit_null_optional_fields(cls, value: object) -> object:
        """Optional means omitted; explicit null has different cross-app semantics."""
        if value is None:
            msg = "optional sketch.v1 fields must be omitted instead of null"
            raise ValueError(msg)
        return value

    @field_validator("range", mode="before")
    @classmethod
    def require_date_range_not_timestamps(cls, value: object) -> object:
        if value is None:
            msg = "optional sketch.v1 fields must be omitted instead of null"
            raise ValueError(msg)
        if not isinstance(value, (list, tuple)):
            return value
        return tuple(parse_date_label(item, field="chart range values") for item in value)

    @field_validator("file")
    @classmethod
    def require_canonical_chart_filename(cls, value: str) -> str:
        if value not in EXPECTED_CHART_FILES:
            msg = f"chart file must be one of {sorted(EXPECTED_CHART_FILES)}"
            raise ValueError(msg)
        return value

    @field_validator("timeframe", "owner_view")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            msg = "field must not be empty"
            raise ValueError(msg)
        return cleaned

    @field_validator("indicators_shown")
    @classmethod
    def validate_indicator_tokens(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            msg = "indicators_shown must not contain duplicates"
            raise ValueError(msg)
        invalid = [token for token in value if not is_canonical_indicator_token(token)]
        if invalid:
            msg = (
                "indicators_shown entries must use canonical indicator tokens; "
                f"put free text in indicators_other instead: {invalid}"
            )
            raise ValueError(msg)
        return value

    @field_validator("indicators_other")
    @classmethod
    def validate_other_indicators(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = [item.strip() for item in value]
        if not cleaned or any(not item for item in cleaned):
            msg = "indicators_other must be omitted when empty and contain no blank entries"
            raise ValueError(msg)
        return cleaned

    @model_validator(mode="after")
    def validate_range_order(self) -> SketchChart:
        if self.range is not None and self.range[0] > self.range[1]:
            msg = "chart range start must not be after its end"
            raise ValueError(msg)
        return self


class SketchMeta(BaseModel):
    """Validated ``meta.yaml`` for one six-file sketch package."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_name: Literal["sketch.v1"] = Field(alias="schema")
    sketch_id: str
    kind: Literal["strategy", "insight"]
    origin: Literal["workshop", "journal-app"]
    chart_source: Literal["rendered", "screenshot"]
    instructions_template: Literal["instructions.v1"]
    # Pre-P2 packages had no catalog identity.  The store accepts those only
    # while reading an existing package; every newly imported package is
    # checked by SketchStore and must provide both values.
    instrument: str | None = None
    asset_class: Literal["equity_index_futures", "commodity_futures"] | None = None
    created: date
    title: str
    rationale: str | None = None
    charts: list[SketchChart]

    @field_validator("rationale", mode="before")
    @classmethod
    def reject_null_rationale(cls, value: object) -> object:
        if value is None:
            msg = "optional rationale must be omitted instead of null"
            raise ValueError(msg)
        return value

    @field_validator("sketch_id", mode="before")
    @classmethod
    def validate_sketch_id(cls, value: object) -> str:
        return parse_sketch_id(value)

    @field_validator("origin", mode="before")
    @classmethod
    def validate_origin(cls, value: object) -> str:
        return parse_sketch_origin(value)

    @field_validator("instrument", "title")
    @classmethod
    def require_nonempty_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value or value != value.strip():
            msg = "field must not be empty"
            raise ValueError(msg)
        return value

    @field_validator("created", mode="before")
    @classmethod
    def require_date_not_timestamp(cls, value: object) -> object:
        return parse_date_label(value, field="created")

    @field_validator("rationale")
    @classmethod
    def require_nonempty_rationale_when_present(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            msg = "rationale must be omitted or contain non-empty text"
            raise ValueError(msg)
        return cleaned

    @model_validator(mode="after")
    def validate_kind_and_charts(self) -> SketchMeta:
        if self.kind == "strategy" and self.rationale is None:
            msg = "kind strategy requires rationale"
            raise ValueError(msg)
        files = [chart.file for chart in self.charts]
        if len(files) != 4 or set(files) != EXPECTED_CHART_FILES:
            msg = (
                "charts must declare exactly chart-D.png, chart-1H.png, "
                "chart-30m.png, and chart-5m.png"
            )
            raise ValueError(msg)
        if len(files) != len(set(files)):
            msg = "charts must not contain duplicate file references"
            raise ValueError(msg)
        return self
