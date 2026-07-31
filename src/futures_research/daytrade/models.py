"""Immutable daytrade domain values."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from hashlib import sha256
from typing import Any, Literal

from futures_research.daytrade.modes import BarMode, FillPricePolicy

EventKind = Literal[
    "SESSION_OPEN",
    "OR_READY",
    "OPEN",
    "CLOSE",
    "STOP",
    "TARGET",
    "FORCE_FLAT",
    "NO_NEW_ENTRY",
    "DAILY_LOSS_HALT",
    "OVERNIGHT_BREACH",
    "PAUSE",
    "RESUME",
]

PositionSide = Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class DayBar:
    """One completed OHLCV bar (1m or 1s). Immutable once accepted."""

    symbol: str
    ts: datetime  # exchange-local aware or UTC-aware; engine normalizes
    open: float
    high: float
    low: float
    close: float
    volume: float
    bar_mode: BarMode

    def ohlc_tuple(self) -> tuple[float, float, float, float, float]:
        return (self.open, self.high, self.low, self.close, self.volume)


@dataclass(frozen=True, slots=True)
class SessionConfig:
    trader_id: str
    display_name: str
    symbol: str
    contract_id: str
    strategy_id: str
    starting_equity: float
    quantity: int
    or_minutes: int
    no_new_entry_after: str  # HH:MM exchange local
    force_flat_time: str
    rth_start: str
    rth_end: str
    timezone: str
    max_daily_loss_r: float
    config_generation: int
    tick_size: float = 0.25
    point_value: float = 20.0
    commission_per_side: float = 2.50


@dataclass(frozen=True, slots=True)
class SessionEvent:
    ordinal: int
    kind: EventKind
    ts: datetime
    symbol: str
    side: PositionSide | None
    qty: int
    price: float | None
    reference: float | None
    stop: float | None
    target: float | None
    realized_pnl: float
    cash: float
    equity: float
    note: str
    fill_policy: FillPricePolicy | None
    bar_side_used: str | None

    def fingerprint_payload(self) -> dict[str, Any]:
        """Stable subset used for resume prefix checks."""
        return {
            "ordinal": self.ordinal,
            "kind": self.kind,
            "ts": self.ts.isoformat(),
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "price": self.price,
            "reference": self.reference,
            "stop": self.stop,
            "target": self.target,
            "realized_pnl": self.realized_pnl,
            "cash": self.cash,
            "equity": self.equity,
            "note": self.note,
            "fill_policy": self.fill_policy,
            "bar_side_used": self.bar_side_used,
        }

    def fingerprint(self) -> str:
        blob = json.dumps(self.fingerprint_payload(), sort_keys=True, separators=(",", ":"))
        return sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OpenPosition:
    side: PositionSide
    qty: int
    entry_price: float
    entry_ts: datetime
    stop: float
    target: float
    entry_reference: float


@dataclass(frozen=True, slots=True)
class SessionResult:
    trader_id: str
    trading_date: date
    events: tuple[SessionEvent, ...]
    open_position: OpenPosition | None
    cash: float
    equity: float
    starting_equity: float
    day_return: float
    realized_pnl: float
    complete: bool
    bar_mode: BarMode
    fill_price_policy: FillPricePolicy
    execution_mode_id: str
    semantics_version: str
    config_generation: int
    risk_flags: tuple[str, ...]
    prefix_matched: bool
    new_event_start_ordinal: int
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def new_events(self) -> tuple[SessionEvent, ...]:
        if self.new_event_start_ordinal <= 1:
            return self.events
        return tuple(e for e in self.events if e.ordinal >= self.new_event_start_ordinal)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "trader_id": self.trader_id,
            "trading_date": self.trading_date.isoformat(),
            "cash": self.cash,
            "equity": self.equity,
            "starting_equity": self.starting_equity,
            "day_return": self.day_return,
            "realized_pnl": self.realized_pnl,
            "complete": self.complete,
            "bar_mode": self.bar_mode,
            "fill_price_policy": self.fill_price_policy,
            "execution_mode_id": self.execution_mode_id,
            "semantics_version": self.semantics_version,
            "config_generation": self.config_generation,
            "risk_flags": list(self.risk_flags),
            "prefix_matched": self.prefix_matched,
            "event_count": len(self.events),
            "open_position": asdict(self.open_position) if self.open_position else None,
            "limitations": list(self.limitations),
        }


class PrefixMismatchError(RuntimeError):
    """Replayed history does not match committed ledger events — fail closed."""


class DaytradeDataError(RuntimeError):
    """Missing or invalid market data for the requested execution mode."""
