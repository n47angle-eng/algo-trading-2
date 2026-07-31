"""Pydantic surface for the ``insight.v1`` document (docs/05 §3.5.1).

The insight branch shares the sketch identity vocabulary on purpose: ``origin``
and the composite sketch lineage are parsed by the same canonical functions the
package intake and ``strategy.v1`` references layer use, so one identifier can
never mean two things in two layers.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from futures_research.sketch.identity import parse_sketch_id, parse_sketch_origin

INSIGHT_VALIDATION_STATUSES = (
    "unverified",
    "recording",
    "supported",
    "rejected",
)
_INSIGHT_ID = re.compile(r"^insight-[0-9]{3,}$")


class InsightIdentityError(ValueError):
    """Raised when an insight identifier is not canonical."""


def parse_insight_id(value: object) -> str:
    """Return an exact ``insight-NNN`` id without normalizing or padding it."""
    if not isinstance(value, str) or value != value.strip():
        msg = "insight_id must be an unpadded string in insight-NNN form"
        raise InsightIdentityError(msg)
    if _INSIGHT_ID.fullmatch(value) is None:
        msg = (
            "insight_id must use the canonical insight-NNN form "
            "(insight- prefix plus at least three digits)"
        )
        raise InsightIdentityError(msg)
    return value


class InsightCondition(BaseModel):
    """The quantified condition the terminal AI derived from the sketch package."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    suggested_params: dict[str, Any] | None = None

    @field_validator("suggested_params", mode="before")
    @classmethod
    def reject_explicit_null_optional_fields(cls, value: object) -> object:
        """Optional means omitted; explicit null has different cross-app semantics."""
        if value is None:
            msg = "optional insight.v1 fields must be omitted instead of null"
            raise ValueError(msg)
        return value

    @field_validator("type")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        if not value or value != value.strip():
            msg = "field must be non-empty and have no surrounding whitespace"
            raise ValueError(msg)
        return value


class InsightMeasurement(BaseModel):
    """How the record layer should tag trades so this insight can be tested."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tag: str
    hypothesis: str

    @field_validator("tag", "hypothesis")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        if not value or value != value.strip():
            msg = "field must be non-empty and have no surrounding whitespace"
            raise ValueError(msg)
        return value


class InsightDocument(BaseModel):
    """One immutable ``insight.v1`` version as written by the terminal AI."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_name: Literal["insight.v1"] = Field(alias="schema")
    insight_id: str
    origin: Literal["workshop", "journal-app"]
    version: int
    based_on_sketch: str
    based_on_sketch_origin: Literal["workshop", "journal-app"]
    instrument: str
    asset_class: Literal["equity_index_futures", "commodity_futures"]
    title: str
    condition: InsightCondition
    measurement: InsightMeasurement
    validation_status: Literal["unverified", "recording", "supported", "rejected"]

    @field_validator("insight_id", mode="before")
    @classmethod
    def validate_insight_id(cls, value: object) -> str:
        return parse_insight_id(value)

    @field_validator("based_on_sketch", mode="before")
    @classmethod
    def validate_sketch_id(cls, value: object) -> str:
        return parse_sketch_id(value)

    @field_validator("origin", "based_on_sketch_origin", mode="before")
    @classmethod
    def validate_origin(cls, value: object) -> str:
        return parse_sketch_origin(value)

    @field_validator("version", mode="before")
    @classmethod
    def require_positive_integer_version(cls, value: object) -> int:
        """A boolean is an int in Python but is never a document version."""
        if type(value) is not int or value < 1:
            msg = "version must be a positive integer"
            raise ValueError(msg)
        return value

    @field_validator("instrument", "title")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        if not value or value != value.strip():
            msg = "field must be non-empty and have no surrounding whitespace"
            raise ValueError(msg)
        return value
