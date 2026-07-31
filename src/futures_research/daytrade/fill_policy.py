"""Daytrade fill price policies.

``worst_side`` may only make a fill *worse*, never better.
Friction (spread / slippage / commission) is applied *after* this price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from futures_research.daytrade.modes import FillPricePolicy

Side = Literal["buy", "sell"]
PositionSide = Literal["long", "short"]
BarSideUsed = Literal["high", "low", "close", "reference"]


@dataclass(frozen=True, slots=True)
class BarOHLC:
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True, slots=True)
class FillQuote:
    price: float
    policy: FillPricePolicy
    bar_side_used: BarSideUsed
    clamped: bool
    missing_ohlc: bool


def action_from_position(*, position_side: PositionSide, is_open: bool) -> Side:
    """Map position open/close to buy/sell action (not residual 'long uses low')."""
    if position_side == "long":
        return "buy" if is_open else "sell"
    return "sell" if is_open else "buy"


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(
        float(value)
    )


def resolve_worst_side_fill(
    *,
    action: Side,
    reference: float,
    bar: BarOHLC | None,
) -> FillQuote:
    """Worst-side fill with clamp vs reference.

    buy  → max(reference, high)  # pay more
    sell → min(reference, low)   # receive less
    """
    if not _finite(reference):
        raise ValueError("reference must be finite")

    if bar is None or not _finite(bar.high) or not _finite(bar.low):
        return FillQuote(
            price=float(reference),
            policy="worst_side",
            bar_side_used="reference",
            clamped=False,
            missing_ohlc=True,
        )

    high = float(bar.high)
    low = float(bar.low)
    if low > high:
        return FillQuote(
            price=float(reference),
            policy="worst_side",
            bar_side_used="reference",
            clamped=False,
            missing_ohlc=True,
        )

    ref = float(reference)
    if action == "buy":
        price = max(ref, high)
        return FillQuote(
            price=price,
            policy="worst_side",
            bar_side_used="high",
            clamped=price != high,
            missing_ohlc=False,
        )

    price = min(ref, low)
    return FillQuote(
        price=price,
        policy="worst_side",
        bar_side_used="low",
        clamped=price != low,
        missing_ohlc=False,
    )


def resolve_close_fill(
    *,
    reference: float,
    bar: BarOHLC | None,
) -> FillQuote:
    """Live-stable close policy (still requires a finite bar close when present)."""
    if not _finite(reference):
        raise ValueError("reference must be finite")
    if bar is None or not _finite(bar.close):
        return FillQuote(
            price=float(reference),
            policy="close",
            bar_side_used="reference",
            clamped=False,
            missing_ohlc=True,
        )
    return FillQuote(
        price=float(bar.close),
        policy="close",
        bar_side_used="close",
        clamped=False,
        missing_ohlc=False,
    )


def resolve_fill(
    *,
    policy: FillPricePolicy,
    action: Side,
    reference: float,
    bar: BarOHLC | None,
) -> FillQuote:
    if policy == "worst_side":
        return resolve_worst_side_fill(action=action, reference=reference, bar=bar)
    if policy == "close":
        return resolve_close_fill(reference=reference, bar=bar)
    raise ValueError(f"unknown fill_price_policy={policy!r}")
