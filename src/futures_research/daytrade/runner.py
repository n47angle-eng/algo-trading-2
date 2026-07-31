"""Daytrade live runner: completed bars → run_session → append new events.

Default runner_enabled=0 (fail-closed). Owner must enable via API.
Never uses native/Rust authority for ledger writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from futures_research.daytrade.ledger import DaytradeLedger
from futures_research.daytrade.models import DayBar, PrefixMismatchError
from futures_research.daytrade.modes import LIVE_DEFAULT_MODE_ID, get_execution_mode
from futures_research.daytrade.session_engine import run_session


@dataclass(frozen=True, slots=True)
class StepReport:
    trader_id: str
    trading_date: str
    bars_used: int
    events_total: int
    events_appended: int
    equity: float
    day_return: float
    open_positions: int
    risk_flags: list[str]
    skipped_reason: str | None = None


class DaytradeRunner:
    """In-process runner — steps one or all enabled simulated traders."""

    def __init__(self, ledger: DaytradeLedger | None = None) -> None:
        self.ledger = ledger or DaytradeLedger()
        self.ledger.bootstrap_traders_from_presets()

    def step_trader(
        self,
        trader_id: str,
        trading_date: date,
        *,
        complete: bool = False,
        require_enabled: bool = True,
    ) -> StepReport:
        if require_enabled and not self.ledger.get_runner_enabled():
            return StepReport(
                trader_id=trader_id,
                trading_date=trading_date.isoformat(),
                bars_used=0,
                events_total=0,
                events_appended=0,
                equity=0.0,
                day_return=0.0,
                open_positions=0,
                risk_flags=[],
                skipped_reason="runner_disabled",
            )

        try:
            cfg = self.ledger.get_session_config(trader_id)
        except KeyError:
            return StepReport(
                trader_id=trader_id,
                trading_date=trading_date.isoformat(),
                bars_used=0,
                events_total=0,
                events_appended=0,
                equity=0.0,
                day_return=0.0,
                open_positions=0,
                risk_flags=[],
                skipped_reason="trader_not_found",
            )

        mode = get_execution_mode(LIVE_DEFAULT_MODE_ID)
        bars = self.ledger.load_bars(
            symbol=cfg.symbol, trading_date=trading_date, bar_mode=mode.bar_mode
        )
        if not bars:
            return StepReport(
                trader_id=trader_id,
                trading_date=trading_date.isoformat(),
                bars_used=0,
                events_total=0,
                events_appended=0,
                equity=cfg.starting_equity,
                day_return=0.0,
                open_positions=0,
                risk_flags=[],
                skipped_reason="no_completed_bars",
            )

        self.ledger.ensure_session(
            cfg,
            trading_date,
            execution_mode_id=mode.mode_id,
            bar_mode=mode.bar_mode,
            fill_price_policy=mode.fill_price_policy,
            semantics_version=mode.semantics_version,
        )
        committed = self.ledger.load_committed_events(trader_id, trading_date)

        cmds = self.ledger.pending_operator_commands(trader_id)
        force_flat = any(c["command_kind"] == "force_flat" for c in cmds)
        paused = False
        for c in cmds:
            if c["command_kind"] == "pause":
                paused = True
            elif c["command_kind"] == "resume":
                paused = False

        try:
            result = run_session(
                cfg,
                trading_date=trading_date,
                bars=bars,
                starting_equity=cfg.starting_equity,
                complete=complete,
                execution_mode_id=mode.mode_id,
                committed_events=committed,
                force_flat_command=force_flat,
                paused=paused,
            )
        except PrefixMismatchError as exc:
            self.ledger.record_incident(
                kind="prefix_mismatch",
                message=str(exc),
                trader_id=trader_id,
                severity="critical",
            )
            return StepReport(
                trader_id=trader_id,
                trading_date=trading_date.isoformat(),
                bars_used=len(bars),
                events_total=len(committed),
                events_appended=0,
                equity=cfg.starting_equity,
                day_return=0.0,
                open_positions=0,
                risk_flags=["PREFIX_MISMATCH"],
                skipped_reason="prefix_mismatch",
            )

        appended = self.ledger.append_session_result(result)
        if cmds:
            self.ledger.consume_operator_commands([c["command_id"] for c in cmds])
        self.ledger.heartbeat("last_step_at", result.trading_date.isoformat())
        self.ledger.heartbeat("last_trader_id", trader_id)

        if "OVERNIGHT_BREACH" in result.risk_flags:
            self.ledger.record_incident(
                kind="overnight_breach",
                message=f"{trader_id} still open at complete",
                trader_id=trader_id,
                severity="critical",
            )

        return StepReport(
            trader_id=trader_id,
            trading_date=trading_date.isoformat(),
            bars_used=len(bars),
            events_total=len(result.events),
            events_appended=appended,
            equity=result.equity,
            day_return=result.day_return,
            open_positions=1 if result.open_position else 0,
            risk_flags=list(result.risk_flags),
        )

    def step_all(self, trading_date: date, *, complete: bool = False) -> list[StepReport]:
        return [
            self.step_trader(tid, trading_date, complete=complete)
            for tid in self.ledger.list_enabled_trader_ids()
        ]

    def step_personal(
        self, trading_date: date, *, complete: bool = False
    ) -> StepReport:
        ids = self.ledger.list_enabled_trader_ids()
        if not ids:
            return StepReport(
                trader_id="",
                trading_date=trading_date.isoformat(),
                bars_used=0,
                events_total=0,
                events_appended=0,
                equity=0.0,
                day_return=0.0,
                open_positions=0,
                risk_flags=[],
                skipped_reason="no_traders",
            )
        return self.step_trader(ids[0], trading_date, complete=complete)


def ingest_bars(
    bars: list[DayBar],
    trading_date: date,
    *,
    ledger: DaytradeLedger | None = None,
) -> int:
    store = ledger or DaytradeLedger()
    return store.insert_bars(bars, trading_date)


def default_ledger_path() -> Path:
    return DaytradeLedger().path


def health_payload(ledger: DaytradeLedger | None = None) -> dict[str, Any]:
    store = ledger or DaytradeLedger()
    status = store.runtime_status()
    incidents = store.list_incidents(limit=10)
    pending = [i for i in incidents if not i.get("resolved")]
    return {
        "service": "daytrade",
        "runner_enabled": store.get_runner_enabled(),
        "runtime": status,
        "pending_incidents": len(pending),
        "recent_incidents": incidents[:5],
        "trader_count": len(store.list_enabled_trader_ids()),
        "live_execution_mode_id": LIVE_DEFAULT_MODE_ID,
        "live_scale_label": "1m_close（穩定 live paper）— 唔可比對 1s_worst 回測",
        "authority": "python_only",
        "writes_authority": False,
    }
