"""Canonical, cross-surface identity rules for ``sketch.v1`` packages.

The same parser is used at package intake, filesystem lookup, and strategy
lineage validation.  That prevents a syntactically plausible but non-canonical
sketch identifier from meaning different things in different layers.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal, cast

SketchOrigin = Literal["workshop", "journal-app"]

SKETCH_ORIGINS: frozenset[str] = frozenset({"workshop", "journal-app"})
_SKETCH_ID = re.compile(r"^sketch-(?P<day>[0-9]{8})-(?P<sequence>[0-9]{2})$")


class SketchIdentityError(ValueError):
    """Raised when one part of a composite sketch identity is not canonical."""


def parse_sketch_id(value: object) -> str:
    """Return an exact ``sketch-YYYYMMDD-NN`` id after calendar validation."""
    if not isinstance(value, str) or value != value.strip():
        msg = "sketch_id must be an unpadded string in sketch-YYYYMMDD-NN form"
        raise SketchIdentityError(msg)
    match = _SKETCH_ID.fullmatch(value)
    if match is None:
        msg = "sketch_id must use the canonical sketch-YYYYMMDD-NN form"
        raise SketchIdentityError(msg)

    day = match.group("day")
    try:
        date(int(day[:4]), int(day[4:6]), int(day[6:]))
    except ValueError as exc:
        msg = "sketch_id must contain a real calendar date"
        raise SketchIdentityError(msg) from exc
    return value


def parse_sketch_origin(value: object) -> SketchOrigin:
    """Return one approved producer label without guessing or normalizing it."""
    if not isinstance(value, str) or value != value.strip() or value not in SKETCH_ORIGINS:
        msg = "origin must be exactly workshop or journal-app"
        raise SketchIdentityError(msg)
    return cast(SketchOrigin, value)
