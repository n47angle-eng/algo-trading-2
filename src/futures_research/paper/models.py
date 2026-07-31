"""Immutable domain values shared by the local paper runtime."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

PaperMarketMode = Literal["live", "test_delayed", "replay_test"]
MarketInputSource = Literal["live", "recovered"]

_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_SAFE_MEMBER_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")


def canonical_utc(value: datetime) -> str:
    """Return canonical UTC text with no offset alias."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    normalized = value.astimezone(UTC)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def require_canonical_utc(value: str) -> str:
    """Validate canonical UTC text without normalizing caller bytes."""
    if _UTC_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp must be canonical UTC text")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.utcoffset() != datetime.now(UTC).utcoffset():
        raise ValueError("timestamp must be UTC")
    return value


def finite_number(value: int | float, *, field_name: str) -> float:
    """Reject booleans, non-finite values, and JSON negative zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be a finite JSON number")
    if result == 0 and math.copysign(1.0, result) < 0:
        raise ValueError(f"{field_name} must not be negative zero")
    return result


@dataclass(frozen=True, slots=True)
class BaselineMember:
    """One locked source member copied byte-exactly into the v4 origin."""

    path: str
    content: bytes
    byte_length: int
    sha256: str

    @classmethod
    def from_bytes(cls, path: str, content: bytes) -> BaselineMember:
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or ".." in path.split("/")
            or _SAFE_MEMBER_PATTERN.fullmatch(path) is None
        ):
            raise ValueError("baseline member path must be safe and relative")
        payload = bytes(content)
        return cls(
            path=path,
            content=payload,
            byte_length=len(payload),
            sha256=sha256(payload).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class ClosedMarketInput:
    """Canonical immutable closed bar accepted by the runtime journal."""

    input_id: str
    provider_session_id: str
    contract_id: str
    timeframe: str
    mode: PaperMarketMode
    event_at: str
    received_at: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    payload_sha256: str
    source_kind: MarketInputSource

    @classmethod
    def create(
        cls,
        *,
        provider_session_id: str,
        contract_id: str,
        timeframe: str,
        mode: PaperMarketMode,
        event_at: str,
        received_at: str,
        open_price: int | float,
        high_price: int | float,
        low_price: int | float,
        close_price: int | float,
        volume: int | float,
        source_kind: MarketInputSource,
    ) -> ClosedMarketInput:
        for name, value in (
            ("provider_session_id", provider_session_id),
            ("contract_id", contract_id),
            ("timeframe", timeframe),
        ):
            if not value or value != value.strip():
                raise ValueError(f"{name} must be exact non-empty text")
        event = require_canonical_utc(event_at)
        received = require_canonical_utc(received_at)
        open_value = finite_number(open_price, field_name="open_price")
        high_value = finite_number(high_price, field_name="high_price")
        low_value = finite_number(low_price, field_name="low_price")
        close_value = finite_number(close_price, field_name="close_price")
        volume_value = finite_number(volume, field_name="volume")
        if volume_value < 0:
            raise ValueError("volume must be non-negative")
        if low_value > min(open_value, close_value, high_value):
            raise ValueError("low_price exceeds another OHLC value")
        if high_value < max(open_value, close_value, low_value):
            raise ValueError("high_price is below another OHLC value")
        payload = {
            "provider_session_id": provider_session_id,
            "contract_id": contract_id,
            "timeframe": timeframe,
            "mode": mode,
            "event_at": event,
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": volume_value,
            "source_kind": source_kind,
        }
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        payload_sha = sha256(encoded).hexdigest()
        identity = sha256(
            (
                f"{provider_session_id}\0{contract_id}\0{timeframe}\0"
                f"{mode}\0{event}\0{payload_sha}"
            ).encode()
        ).hexdigest()
        return cls(
            input_id=f"paper-input-{identity}",
            provider_session_id=provider_session_id,
            contract_id=contract_id,
            timeframe=timeframe,
            mode=mode,
            event_at=event,
            received_at=received,
            open_price=open_value,
            high_price=high_value,
            low_price=low_value,
            close_price=close_value,
            volume=volume_value,
            payload_sha256=payload_sha,
            source_kind=source_kind,
        )


@dataclass(frozen=True, slots=True)
class SimulatedFillWrite:
    """One app-owned simulated fill to commit with its input checkpoint."""

    role: Literal["entry", "exit"]
    quantity: int
    price: float
    commission: float
    slippage: float
    payload_json: str


@dataclass(frozen=True, slots=True)
class CompletedTradeWrite:
    """One completed trade derived by the shared conservative execution core."""

    side: Literal["long", "short"]
    quantity: int
    gross_pnl: float
    net_pnl: float
    net_r: float
    opened_at: str
    closed_at: str


@dataclass(frozen=True, slots=True)
class RuntimeStepWrite:
    """All append-only evidence and projections for one trader/input transaction."""

    decision_payload_json: str | None
    intent_payload_json: str | None
    event_payloads_json: tuple[str, ...]
    fills: tuple[SimulatedFillWrite, ...]
    completed_trade: CompletedTradeWrite | None
    position_quantity: int
    average_entry_price: float | None
    stop_price: float | None
    target_price: float | None
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    realized_r: float
    unrealized_r: float
    equity_high_water_r: float
    drawdown_r: float
    losing_streak: int
    pending_intent_count: int
    flatten_pending: bool = False
    safety_trigger: Literal["drawdown", "losing_streak"] | None = None
    lifecycle_after: Literal["paused", "tripped", "permanently_stopped"] | None = None
    lifecycle_reason: str | None = None
