"""Owner-editable individual-contract metadata and roll blacklist derivation."""

from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SessionHours(BaseModel):
    """Named exchange-local session window from the contract configuration."""

    start: time
    end: time


class SlippageTicks(BaseModel):
    """Owner-approved conservative slippage by execution order type."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    breakout_entry: int = Field(ge=0)
    stop_exit: int = Field(ge=0)
    target_exit: int = Field(ge=0)
    day_end_exit: int = Field(ge=0)


class ExecutionCostSpec(BaseModel):
    """Per-contract costs used by the shared conservative execution core."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commission_per_side: float = Field(ge=0)
    slippage_ticks: SlippageTicks
    target_requires_through: bool = False


ContractAssetClass = Literal["equity_index_futures", "commodity_futures"]


class ContractSpec(BaseModel):
    """Metadata for one individual futures contract, not a continuous series."""

    model_config = ConfigDict(frozen=True)

    contract_id: str
    symbol: str
    display_name: str
    asset_class: ContractAssetClass
    exchange: str
    ib_exchange: str
    ib_local_symbol: str
    currency: str
    expiry: date
    timezone: str
    tick_size: float
    point_value: float
    execution_costs: ExecutionCostSpec
    roll_blackout_half_window_days: int = Field(ge=0)
    sessions: dict[str, SessionHours]
    roll_blackout_overrides: list[date] = Field(default_factory=list)

    @field_validator(
        "contract_id",
        "symbol",
        "display_name",
        "exchange",
        "ib_exchange",
        "ib_local_symbol",
        "currency",
        "timezone",
    )
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        """Reject empty identifiers before they become storage partition names."""
        normalized = value.strip()
        if not normalized:
            msg = "contract metadata values must not be empty"
            raise ValueError(msg)
        return normalized

    def roll_blackout_dates(self) -> set[date]:
        """Return the inclusive configured calendar-day blackout around expiry.

        Overrides add dates; they never remove the safety window.
        """
        offsets = range(
            -self.roll_blackout_half_window_days,
            self.roll_blackout_half_window_days + 1,
        )
        return {self.expiry + timedelta(days=offset) for offset in offsets} | set(
            self.roll_blackout_overrides
        )

    def is_roll_blackout(self, trading_date: date) -> bool:
        """Return whether the requested exchange-local trading date is blacklisted."""
        return trading_date in self.roll_blackout_dates()


class ContractRegistry(BaseModel):
    """The validated contents of ``config/contracts.yaml``."""

    model_config = ConfigDict(frozen=True)

    version: int
    canonical_storage: dict[str, str]
    contracts: dict[str, ContractSpec]

    @classmethod
    def from_yaml(cls, path: Path) -> ContractRegistry:
        """Load owner-editable contract metadata without embedding vendor state in code."""
        with path.open(encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file)
        if not isinstance(raw, dict):
            msg = f"contract configuration must be a mapping: {path}"
            raise ValueError(msg)
        return cls.model_validate(raw)

    def by_symbol(self, symbol: str) -> ContractSpec:
        """Get a configured contract by its product symbol."""
        try:
            return self.contracts[symbol]
        except KeyError as exc:
            msg = f"unknown configured symbol: {symbol}"
            raise KeyError(msg) from exc
