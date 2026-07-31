"""Execution mode matrix for daytrade live vs formal backtest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

BarMode = Literal["1m", "1s"]
FillPricePolicy = Literal["close", "worst_side"]

LIVE_DEFAULT_MODE_ID: Final = "1m_close"
BACKTEST_DEFAULT_MODE_ID: Final = "1s_worst"

LIVE_SEMANTICS_VERSION: Final = "daytrade-live-1m-close-v1"
BACKTEST_SEMANTICS_VERSION: Final = "daytrade-backtest-1s-worst-v1"


@dataclass(frozen=True, slots=True)
class ExecutionMode:
    mode_id: str
    bar_mode: BarMode
    fill_price_policy: FillPricePolicy
    semantics_version: str
    purpose: str


_MODES: Final[dict[str, ExecutionMode]] = {
    "1m_close": ExecutionMode(
        mode_id="1m_close",
        bar_mode="1m",
        fill_price_policy="close",
        semantics_version=LIVE_SEMANTICS_VERSION,
        purpose="Live paper stability / golden alignment",
    ),
    "1m_worst": ExecutionMode(
        mode_id="1m_worst",
        bar_mode="1m",
        fill_price_policy="worst_side",
        semantics_version="daytrade-diag-1m-worst-v1",
        purpose="Mid pessimistic diagnosis",
    ),
    "1s_worst": ExecutionMode(
        mode_id="1s_worst",
        bar_mode="1s",
        fill_price_policy="worst_side",
        semantics_version=BACKTEST_SEMANTICS_VERSION,
        purpose="Formal backtest / launch evaluation default",
    ),
    "1s_close": ExecutionMode(
        mode_id="1s_close",
        bar_mode="1s",
        fill_price_policy="close",
        semantics_version="daytrade-diag-1s-close-v1",
        purpose="Diagnosis only — not a formal gate",
    ),
}


def get_execution_mode(mode_id: str) -> ExecutionMode:
    try:
        return _MODES[mode_id]
    except KeyError as exc:
        known = ", ".join(sorted(_MODES))
        raise ValueError(f"unknown execution_mode_id={mode_id!r}; known: {known}") from exc


def list_execution_modes() -> tuple[ExecutionMode, ...]:
    return tuple(_MODES[k] for k in sorted(_MODES))
