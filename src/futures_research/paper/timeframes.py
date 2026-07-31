"""Role-aware timeframe capabilities and immutable strategy profile loading."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from futures_research.strategy.parser import parse_strategy_document
from futures_research.strategy.store import (
    StrategyStorageIntegrityError,
    StrategyVersionNotFoundError,
    default_strategy_store,
)

TimeframeRole = Literal["market_input", "execution", "chart_display"]

_INTRADAY_PATTERN = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[mH])$")
_KNOWN_DURATIONS: dict[str, int] = {
    "D": 86_400,
    "1H": 3_600,
}


class TimeframeCapabilityError(ValueError):
    """A requested timeframe is unknown, disabled, or invalid for its role."""


class StrategyTimeframeIdentityError(RuntimeError):
    """The immutable strategy artifact cannot prove its declared timeframe trio."""


@dataclass(frozen=True, slots=True)
class TimeframeSpec:
    """Canonical typed timeframe value."""

    value: str
    duration_seconds: int


@dataclass(frozen=True, slots=True)
class StrategyTimeframeProfile:
    """Server-derived immutable strategy timeframe identity."""

    bias: str
    mid: str
    entry: str
    source: Literal["strategy.v1"] = "strategy.v1"
    client_override: Literal[False] = False

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "bias": self.bias,
            "mid": self.mid,
            "entry": self.entry,
            "source": self.source,
            "client_override": self.client_override,
        }


def timeframe_spec(value: str) -> TimeframeSpec:
    """Parse one canonical duration label without silently falling back."""
    if value in _KNOWN_DURATIONS:
        return TimeframeSpec(value=value, duration_seconds=_KNOWN_DURATIONS[value])
    match = _INTRADAY_PATTERN.fullmatch(value)
    if match is None:
        raise TimeframeCapabilityError(f"unsupported_timeframe: {value}")
    count = int(match.group("count"))
    unit = match.group("unit")
    seconds = count * (60 if unit == "m" else 3_600)
    return TimeframeSpec(value=value, duration_seconds=seconds)


@dataclass(frozen=True, slots=True)
class TimeframeRegistry:
    """Single backend authority for role-aware enabled timeframe values."""

    _roles: tuple[
        tuple[TimeframeRole, tuple[TimeframeSpec, ...]],
        tuple[TimeframeRole, tuple[TimeframeSpec, ...]],
        tuple[TimeframeRole, tuple[TimeframeSpec, ...]],
    ]

    @classmethod
    def from_role_values(
        cls,
        *,
        market_input: tuple[str, ...],
        execution: tuple[str, ...],
        chart_display: tuple[str, ...],
    ) -> TimeframeRegistry:
        values: tuple[tuple[TimeframeRole, tuple[str, ...]], ...] = (
            ("market_input", market_input),
            ("execution", execution),
            ("chart_display", chart_display),
        )
        parsed: list[tuple[TimeframeRole, tuple[TimeframeSpec, ...]]] = []
        for role, enabled in values:
            if not enabled or len(set(enabled)) != len(enabled):
                raise TimeframeCapabilityError(
                    f"unsupported_timeframe: invalid {role} capability registry"
                )
            parsed.append((role, tuple(timeframe_spec(value) for value in enabled)))
        return cls(_roles=(parsed[0], parsed[1], parsed[2]))

    def enabled(self, role: TimeframeRole) -> tuple[str, ...]:
        for candidate, specs in self._roles:
            if candidate == role:
                return tuple(spec.value for spec in specs)
        raise TimeframeCapabilityError(f"unsupported_timeframe_role: {role}")

    def require(self, role: TimeframeRole, value: str) -> TimeframeSpec:
        for candidate, specs in self._roles:
            if candidate != role:
                continue
            for spec in specs:
                if spec.value == value:
                    return spec
            raise TimeframeCapabilityError(
                f"unsupported_timeframe: {value} is not enabled for {role}"
            )
        raise TimeframeCapabilityError(f"unsupported_timeframe_role: {role}")

    def public_capabilities(self, *, as_of: datetime) -> dict[str, Any]:
        timestamp = _canonical_utc(as_of)
        return {
            "schema": "paper_runtime_capabilities.v1",
            "timeframes": {
                "market_input": {"enabled": list(self.enabled("market_input"))},
                "execution": {"enabled": list(self.enabled("execution"))},
                "chart_display": {"enabled": list(self.enabled("chart_display"))},
                "strategy_profile": {
                    "source": "strategy.v1",
                    "client_override": False,
                },
            },
            "market_modes": ["live", "test_delayed"],
            "safety_defaults": {
                "max_drawdown_r": 8,
                "max_losing_streak": 8,
                "blind_minutes": 5,
            },
            "lifecycle_states": [
                "provisioned",
                "starting",
                "running",
                "pausing",
                "paused",
                "tripped",
                "stopping",
                "recovery_required",
                "permanently_stopped",
            ],
            "as_of": timestamp,
        }


DEFAULT_TIMEFRAME_CAPABILITIES = TimeframeRegistry.from_role_values(
    market_input=("1m",),
    execution=("1m",),
    chart_display=("1m", "30m"),
)


def load_locked_strategy_timeframe_profile(
    *,
    strategy_id: str,
    content_sha256: str,
) -> StrategyTimeframeProfile:
    """Read, digest-check, and parse the immutable strategy.v1 authority."""
    try:
        record = default_strategy_store().get(strategy_id)
    except (StrategyVersionNotFoundError, StrategyStorageIntegrityError) as exc:
        raise StrategyTimeframeIdentityError(str(exc)) from exc
    if record.get("content_sha256") != content_sha256:
        raise StrategyTimeframeIdentityError(
            "immutable strategy content identity does not match selection"
        )
    source = record.get("source_text")
    if not isinstance(source, str) or not source:
        raise StrategyTimeframeIdentityError(
            "immutable strategy source_text is missing"
        )
    try:
        parsed = parse_strategy_document(source)
    except Exception as exc:
        raise StrategyTimeframeIdentityError(
            "immutable strategy source failed strategy.v1 validation"
        ) from exc
    trio = parsed.document.timeframes.trio
    # Parse every label so malformed identities cannot enter storage/cache keys.
    timeframe_spec(trio.bias)
    timeframe_spec(trio.mid)
    timeframe_spec(trio.entry)
    return StrategyTimeframeProfile(
        bias=trio.bias,
        mid=trio.mid,
        entry=trio.entry,
    )


def _canonical_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    normalized = value.astimezone(UTC)
    if normalized.microsecond:
        text = normalized.isoformat(timespec="microseconds")
    else:
        text = normalized.isoformat(timespec="seconds")
    return text.replace("+00:00", "Z")
