"""Create TrendStrategy backends: python (oracle) or rust (bit-exact port).

Phase E.3: product prefers Rust when admitted; golden tests can force either.
"""

from __future__ import annotations

import os
from typing import Literal

from futures_research.backtest.strategy import StrategySpec, TrendStrategy

StrategyBackend = Literal["python", "rust", "auto"]


def resolve_strategy_backend(
    requested: StrategyBackend | None = None,
) -> StrategyBackend:
    if requested in {"python", "rust"}:
        return requested
    raw = os.environ.get("FR_STRATEGY_BACKEND", "").strip().lower()
    if raw in {"python", "rust", "auto"}:
        return raw  # type: ignore[return-value]
    # Default: follow compute backend (auto prefers rust when available).
    from futures_research.platform.backend_policy import requested_backend_from_env

    compute = requested_backend_from_env()
    if compute == "stable":
        return "python"
    return "auto"


def rust_strategy_available() -> bool:
    try:
        from futures_research.platform.trend_strategy_seam import rust_trend_available

        return rust_trend_available()
    except Exception:  # noqa: BLE001
        return False


def create_trend_strategy(
    spec: StrategySpec | None = None,
    *,
    tick_size: float,
    backend: StrategyBackend | None = None,
) -> TrendStrategy:
    """Return a strategy instance with the same public surface as ``TrendStrategy``.

    When backend resolves to rust and the native library is admitted, returns a
    ``RustTrendStrategy`` that is API-compatible. Otherwise returns Python
    ``TrendStrategy`` (oracle / fallback).
    """
    spec = spec if spec is not None else StrategySpec()
    mode = resolve_strategy_backend(backend)
    if mode in {"rust", "auto"} and rust_strategy_available():
        try:
            from futures_research.platform.trend_strategy_seam import RustTrendStrategy

            return RustTrendStrategy(spec, tick_size=tick_size)  # type: ignore[return-value]
        except Exception:  # noqa: BLE001 — never break product construction
            if mode == "rust":
                raise
    return TrendStrategy(spec, tick_size=tick_size)
