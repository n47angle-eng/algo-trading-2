"""Daytrader presets + contract defaults for creating simulated traders."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from futures_research.daytrade.models import SessionConfig
from futures_research.paths import PROJECT_ROOT

DEFAULT_PRESETS_PATH = PROJECT_ROOT / "config" / "daytrader_presets.json"

_CONTRACT_DEFAULTS: dict[str, dict[str, Any]] = {
    "NQ": {
        "contract_id": "NQ-202609-CME",
        "tick_size": 0.25,
        "point_value": 20.0,
        "commission_per_side": 2.50,
    },
    "YM": {
        "contract_id": "YM-202609-CBOT",
        "tick_size": 1.0,
        "point_value": 5.0,
        "commission_per_side": 2.50,
    },
    "GC": {
        "contract_id": "GC-202608-COMEX",
        "tick_size": 0.10,
        "point_value": 100.0,
        "commission_per_side": 2.50,
    },
}

_DEFAULT_SESSION = {
    "strategy_id": "opening_range_breakout_v1",
    "starting_equity": 100_000.0,
    "quantity": 1,
    "or_minutes": 5,
    "no_new_entry_after": "14:30",
    "force_flat_time": "14:45",
    "rth_start": "08:30",
    "rth_end": "15:00",
    "timezone": "America/Chicago",
    "max_daily_loss_r": 4.0,
}

_TRADER_ID_RE = re.compile(r"^dt-[a-z0-9-]{4,48}$")


def load_presets(path: Path | None = None) -> dict[str, Any]:
    preset_path = path or DEFAULT_PRESETS_PATH
    raw = json.loads(preset_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("daytrader_presets must be a JSON object")
    return raw


def supported_symbols() -> list[str]:
    return sorted(_CONTRACT_DEFAULTS)


def contract_defaults(symbol: str) -> dict[str, Any]:
    key = symbol.strip().upper()
    if key not in _CONTRACT_DEFAULTS:
        raise ValueError(f"unsupported symbol={symbol!r}; use {supported_symbols()}")
    return dict(_CONTRACT_DEFAULTS[key])


def session_config_from_row(
    row: dict[str, Any],
    *,
    config_generation: int = 1,
) -> SessionConfig:
    symbol = str(row["symbol"]).upper()
    econ = contract_defaults(symbol)
    return SessionConfig(
        trader_id=str(row["trader_id"]),
        display_name=str(row.get("display_name") or row["trader_id"]),
        symbol=symbol,
        contract_id=str(row.get("contract_id") or econ["contract_id"]),
        strategy_id=str(row.get("strategy_id") or _DEFAULT_SESSION["strategy_id"]),
        starting_equity=float(row.get("starting_equity", _DEFAULT_SESSION["starting_equity"])),
        quantity=int(row.get("quantity", _DEFAULT_SESSION["quantity"])),
        or_minutes=int(row.get("or_minutes", _DEFAULT_SESSION["or_minutes"])),
        no_new_entry_after=str(
            row.get("no_new_entry_after", _DEFAULT_SESSION["no_new_entry_after"])
        ),
        force_flat_time=str(row.get("force_flat_time", _DEFAULT_SESSION["force_flat_time"])),
        rth_start=str(row.get("rth_start", _DEFAULT_SESSION["rth_start"])),
        rth_end=str(row.get("rth_end", _DEFAULT_SESSION["rth_end"])),
        timezone=str(row.get("timezone", _DEFAULT_SESSION["timezone"])),
        max_daily_loss_r=float(
            row.get("max_daily_loss_r", _DEFAULT_SESSION["max_daily_loss_r"])
        ),
        config_generation=int(row.get("config_generation", config_generation)),
        tick_size=float(row.get("tick_size", econ["tick_size"])),
        point_value=float(row.get("point_value", econ["point_value"])),
        commission_per_side=float(
            row.get("commission_per_side", econ["commission_per_side"])
        ),
    )


def list_trader_configs(path: Path | None = None) -> list[SessionConfig]:
    raw = load_presets(path)
    generation = int(raw.get("config_generation", 1))
    traders = raw.get("traders") or []
    out: list[SessionConfig] = []
    for row in traders:
        if not isinstance(row, dict):
            continue
        if not row.get("enabled", True):
            continue
        out.append(session_config_from_row(row, config_generation=generation))
    return out


def get_trader_config(trader_id: str, path: Path | None = None) -> SessionConfig:
    for cfg in list_trader_configs(path):
        if cfg.trader_id == trader_id:
            return cfg
    raise KeyError(trader_id)


def new_trader_id(symbol: str) -> str:
    slug = symbol.strip().lower()
    short = uuid.uuid4().hex[:8]
    candidate = f"dt-{slug}-{short}"
    if not _TRADER_ID_RE.match(candidate):
        candidate = f"dt-{short}"
    return candidate


def build_create_config(
    *,
    display_name: str,
    symbol: str,
    strategy_id: str | None = None,
    starting_equity: float | None = None,
    quantity: int | None = None,
    or_minutes: int | None = None,
    no_new_entry_after: str | None = None,
    force_flat_time: str | None = None,
    max_daily_loss_r: float | None = None,
    trader_id: str | None = None,
    notes: str | None = None,
) -> SessionConfig:
    sym = symbol.strip().upper()
    econ = contract_defaults(sym)
    name = display_name.strip()
    if not name:
        raise ValueError("display_name required")
    tid = (trader_id or new_trader_id(sym)).strip()
    if not _TRADER_ID_RE.match(tid):
        raise ValueError(f"invalid trader_id={tid!r}")
    if or_minutes is not None and not (1 <= int(or_minutes) <= 60):
        raise ValueError("or_minutes must be 1..60")
    if max_daily_loss_r is not None and float(max_daily_loss_r) <= 0:
        raise ValueError("max_daily_loss_r must be > 0")
    row: dict[str, Any] = {
        "trader_id": tid,
        "display_name": name,
        "symbol": sym,
        "contract_id": econ["contract_id"],
        "strategy_id": strategy_id or _DEFAULT_SESSION["strategy_id"],
        "starting_equity": starting_equity
        if starting_equity is not None
        else _DEFAULT_SESSION["starting_equity"],
        "quantity": quantity if quantity is not None else _DEFAULT_SESSION["quantity"],
        "or_minutes": or_minutes if or_minutes is not None else _DEFAULT_SESSION["or_minutes"],
        "no_new_entry_after": no_new_entry_after
        if no_new_entry_after is not None
        else _DEFAULT_SESSION["no_new_entry_after"],
        "force_flat_time": force_flat_time
        if force_flat_time is not None
        else _DEFAULT_SESSION["force_flat_time"],
        "max_daily_loss_r": max_daily_loss_r
        if max_daily_loss_r is not None
        else _DEFAULT_SESSION["max_daily_loss_r"],
        "notes": notes or "",
    }
    return session_config_from_row(row, config_generation=1)


# Back-compat alias used by older call sites
def get_personal_trader_config(path: Path | None = None) -> SessionConfig:
    traders = list_trader_configs(path)
    if not traders:
        raise KeyError("no_personal_daytrader_configured")
    return traders[0]
