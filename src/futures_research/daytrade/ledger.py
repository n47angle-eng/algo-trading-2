"""Append-only daytrade ledger — isolated from P6 paper store."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from futures_research.daytrade.models import (
    DayBar,
    OpenPosition,
    SessionConfig,
    SessionEvent,
    SessionResult,
)
from futures_research.daytrade.presets import load_presets, session_config_from_row
from futures_research.paths import PROJECT_ROOT

STORE_VERSION = 1
_LOCK = threading.RLock()

DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "daytrade" / "daytrade_ledger.db"

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dt_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS dt_traders (
    trader_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    config_generation INTEGER NOT NULL,
    starting_equity REAL NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    status TEXT NOT NULL,
    config_json TEXT NOT NULL CHECK(json_valid(config_json))
) STRICT;

CREATE TABLE IF NOT EXISTS dt_sessions (
    session_id TEXT PRIMARY KEY,
    trader_id TEXT NOT NULL REFERENCES dt_traders(trader_id),
    trading_date TEXT NOT NULL,
    starting_equity REAL NOT NULL,
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    day_return REAL NOT NULL,
    complete INTEGER NOT NULL CHECK(complete IN (0,1)),
    bar_mode TEXT NOT NULL,
    fill_price_policy TEXT NOT NULL,
    execution_mode_id TEXT NOT NULL,
    semantics_version TEXT NOT NULL,
    config_generation INTEGER NOT NULL,
    risk_flags_json TEXT NOT NULL CHECK(json_valid(risk_flags_json)),
    open_position_json TEXT,
    event_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(trader_id, trading_date)
) STRICT;

CREATE TABLE IF NOT EXISTS dt_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES dt_sessions(session_id),
    trader_id TEXT NOT NULL,
    trading_date TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    fingerprint TEXT NOT NULL,
    UNIQUE(session_id, ordinal)
) STRICT;

CREATE TABLE IF NOT EXISTS dt_bars (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    bar_mode TEXT NOT NULL,
    ts TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    trading_date TEXT NOT NULL,
    UNIQUE(symbol, bar_mode, ts)
) STRICT;

CREATE TABLE IF NOT EXISTS dt_incidents (
    incident_id TEXT PRIMARY KEY,
    trader_id TEXT,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(acknowledged IN (0,1)),
    resolved INTEGER NOT NULL DEFAULT 0 CHECK(resolved IN (0,1))
) STRICT;

CREATE TABLE IF NOT EXISTS dt_operator_commands (
    command_id TEXT PRIMARY KEY,
    trader_id TEXT NOT NULL,
    command_kind TEXT NOT NULL CHECK(command_kind IN ('pause','resume','force_flat')),
    created_at TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed IN (0,1))
) STRICT;

CREATE TABLE IF NOT EXISTS dt_runtime_status (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
"""

# Append-only protection for events
_APPEND_ONLY_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS dt_events_no_update
BEFORE UPDATE ON dt_events
BEGIN
    SELECT RAISE(ABORT, 'dt_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS dt_events_no_delete
BEFORE DELETE ON dt_events
BEGIN
    SELECT RAISE(ABORT, 'dt_events is append-only');
END;
"""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _session_id(trader_id: str, trading_date: date) -> str:
    return f"{trader_id}:{trading_date.isoformat()}"


class DaytradeLedger:
    """SQLite authority for daytrade paper sessions and events."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_LEDGER_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            self._conn.executescript(_APPEND_ONLY_TRIGGER)
            self._conn.execute(
                "INSERT OR IGNORE INTO dt_meta(key, value) VALUES ('store_version', ?)",
                (str(STORE_VERSION),),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO dt_runtime_status(key, value, updated_at) "
                "VALUES ('runner_enabled', '0', ?)",
                (_utc_now(),),
            )
            self._conn.commit()

    def close(self) -> None:
        with _LOCK:
            self._conn.close()

    def bootstrap_traders_from_presets(self) -> list[str]:
        """Upsert all enabled preset traders (including notes) into the ledger."""
        raw = load_presets()
        generation = int(raw.get("config_generation", 1))
        ids: list[str] = []
        with _LOCK:
            for row in raw.get("traders") or []:
                if not isinstance(row, dict):
                    continue
                if not row.get("enabled", True):
                    continue
                cfg = session_config_from_row(row, config_generation=generation)
                notes = str(row.get("notes") or "")
                self._upsert_trader(cfg, notes=notes)
                ids.append(cfg.trader_id)
            self._conn.commit()
        return ids

    def _upsert_trader(self, cfg: SessionConfig, *, notes: str = "") -> None:
        payload = {
            "trader_id": cfg.trader_id,
            "display_name": cfg.display_name,
            "symbol": cfg.symbol,
            "contract_id": cfg.contract_id,
            "strategy_id": cfg.strategy_id,
            "starting_equity": cfg.starting_equity,
            "quantity": cfg.quantity,
            "or_minutes": cfg.or_minutes,
            "no_new_entry_after": cfg.no_new_entry_after,
            "force_flat_time": cfg.force_flat_time,
            "rth_start": cfg.rth_start,
            "rth_end": cfg.rth_end,
            "timezone": cfg.timezone,
            "max_daily_loss_r": cfg.max_daily_loss_r,
            "tick_size": cfg.tick_size,
            "point_value": cfg.point_value,
            "commission_per_side": cfg.commission_per_side,
            "config_generation": cfg.config_generation,
            "notes": notes,
        }
        # Preserve existing notes when caller did not supply a new one
        if not notes:
            existing = self._conn.execute(
                "SELECT config_json FROM dt_traders WHERE trader_id=?",
                (cfg.trader_id,),
            ).fetchone()
            if existing is not None:
                try:
                    prev = json.loads(existing["config_json"] or "{}")
                    if prev.get("notes"):
                        payload["notes"] = prev["notes"]
                except (TypeError, json.JSONDecodeError):
                    pass
        self._conn.execute(
            """
            INSERT INTO dt_traders(
                trader_id, display_name, symbol, contract_id, strategy_id,
                config_generation, starting_equity, enabled, status, config_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(trader_id) DO UPDATE SET
                display_name=excluded.display_name,
                symbol=excluded.symbol,
                contract_id=excluded.contract_id,
                strategy_id=excluded.strategy_id,
                config_generation=excluded.config_generation,
                starting_equity=excluded.starting_equity,
                enabled=excluded.enabled,
                config_json=excluded.config_json
            """,
            (
                cfg.trader_id,
                cfg.display_name,
                cfg.symbol,
                cfg.contract_id,
                cfg.strategy_id,
                cfg.config_generation,
                cfg.starting_equity,
                1,
                "ready",
                json.dumps(payload, sort_keys=True),
            ),
        )

    def get_runner_enabled(self) -> bool:
        with _LOCK:
            row = self._conn.execute(
                "SELECT value FROM dt_runtime_status WHERE key='runner_enabled'"
            ).fetchone()
            return bool(row and row["value"] == "1")

    def set_runner_enabled(self, enabled: bool) -> None:
        with _LOCK:
            self._conn.execute(
                """
                INSERT INTO dt_runtime_status(key, value, updated_at)
                VALUES ('runner_enabled', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                ("1" if enabled else "0", _utc_now()),
            )
            self._conn.commit()

    def heartbeat(self, key: str, value: str) -> None:
        with _LOCK:
            self._conn.execute(
                """
                INSERT INTO dt_runtime_status(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, _utc_now()),
            )
            self._conn.commit()

    def runtime_status(self) -> dict[str, Any]:
        with _LOCK:
            rows = self._conn.execute(
                "SELECT key, value, updated_at FROM dt_runtime_status"
            ).fetchall()
        return {r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}

    def insert_bars(self, bars: list[DayBar], trading_date: date) -> int:
        """Idempotent completed bar insert. Returns inserted count."""
        inserted = 0
        with _LOCK:
            for bar in bars:
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO dt_bars(
                        symbol, bar_mode, ts, open, high, low, close, volume, trading_date
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        bar.symbol,
                        bar.bar_mode,
                        bar.ts.isoformat(),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        trading_date.isoformat(),
                    ),
                )
                inserted += cur.rowcount
            self._conn.commit()
        return inserted

    def load_bars(
        self,
        *,
        symbol: str,
        trading_date: date,
        bar_mode: str = "1m",
    ) -> list[DayBar]:
        with _LOCK:
            rows = self._conn.execute(
                """
                SELECT symbol, bar_mode, ts, open, high, low, close, volume
                FROM dt_bars
                WHERE symbol=? AND trading_date=? AND bar_mode=?
                ORDER BY ts ASC
                """,
                (symbol, trading_date.isoformat(), bar_mode),
            ).fetchall()
        out: list[DayBar] = []
        for r in rows:
            out.append(
                DayBar(
                    symbol=r["symbol"],
                    ts=datetime.fromisoformat(r["ts"]),
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    volume=r["volume"],
                    bar_mode=r["bar_mode"],
                )
            )
        return out

    def load_committed_events(
        self, trader_id: str, trading_date: date
    ) -> list[SessionEvent]:
        sid = _session_id(trader_id, trading_date)
        with _LOCK:
            rows = self._conn.execute(
                """
                SELECT payload_json FROM dt_events
                WHERE session_id=?
                ORDER BY ordinal ASC
                """,
                (sid,),
            ).fetchall()
        events: list[SessionEvent] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            events.append(_event_from_payload(payload))
        return events

    def ensure_session(
        self,
        cfg: SessionConfig,
        trading_date: date,
        *,
        execution_mode_id: str,
        bar_mode: str,
        fill_price_policy: str,
        semantics_version: str,
    ) -> str:
        sid = _session_id(cfg.trader_id, trading_date)
        with _LOCK:
            self._upsert_trader(cfg)
            row = self._conn.execute(
                "SELECT session_id FROM dt_sessions WHERE session_id=?", (sid,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO dt_sessions(
                        session_id, trader_id, trading_date, starting_equity,
                        cash, equity, realized_pnl, day_return, complete,
                        bar_mode, fill_price_policy, execution_mode_id,
                        semantics_version, config_generation, risk_flags_json,
                        open_position_json, event_count, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        sid,
                        cfg.trader_id,
                        trading_date.isoformat(),
                        cfg.starting_equity,
                        cfg.starting_equity,
                        cfg.starting_equity,
                        0.0,
                        0.0,
                        0,
                        bar_mode,
                        fill_price_policy,
                        execution_mode_id,
                        semantics_version,
                        cfg.config_generation,
                        "[]",
                        None,
                        0,
                        _utc_now(),
                    ),
                )
            self._conn.commit()
        return sid

    def append_session_result(self, result: SessionResult) -> int:
        """Append only *new* events after prefix validation by caller.

        Returns number of events written.
        """
        sid = _session_id(result.trader_id, result.trading_date)
        new_events = result.new_events()
        with _LOCK:
            for ev in new_events:
                payload = ev.fingerprint_payload()
                # store ts already iso in fingerprint_payload
                self._conn.execute(
                    """
                    INSERT INTO dt_events(
                        session_id, trader_id, trading_date, ordinal, kind, ts,
                        payload_json, fingerprint
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        sid,
                        result.trader_id,
                        result.trading_date.isoformat(),
                        ev.ordinal,
                        ev.kind,
                        payload["ts"],
                        json.dumps(payload, sort_keys=True),
                        ev.fingerprint(),
                    ),
                )
            open_json = None
            if result.open_position is not None:
                open_json = json.dumps(
                    {
                        **asdict(result.open_position),
                        "entry_ts": result.open_position.entry_ts.isoformat(),
                    },
                    sort_keys=True,
                )
            self._conn.execute(
                """
                UPDATE dt_sessions SET
                    cash=?, equity=?, realized_pnl=?, day_return=?, complete=?,
                    bar_mode=?, fill_price_policy=?, execution_mode_id=?,
                    semantics_version=?, config_generation=?, risk_flags_json=?,
                    open_position_json=?, event_count=?, updated_at=?
                WHERE session_id=?
                """,
                (
                    result.cash,
                    result.equity,
                    result.realized_pnl,
                    result.day_return,
                    1 if result.complete else 0,
                    result.bar_mode,
                    result.fill_price_policy,
                    result.execution_mode_id,
                    result.semantics_version,
                    result.config_generation,
                    json.dumps(list(result.risk_flags)),
                    open_json,
                    len(result.events),
                    _utc_now(),
                    sid,
                ),
            )
            self._conn.commit()
        return len(new_events)

    def record_incident(
        self,
        *,
        kind: str,
        message: str,
        trader_id: str | None = None,
        severity: str = "error",
    ) -> str:
        incident_id = f"inc-{_utc_now().replace(':', '').replace('-', '')}-{kind[:12]}"
        with _LOCK:
            self._conn.execute(
                """
                INSERT INTO dt_incidents(
                    incident_id, trader_id, kind, severity, message, created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (incident_id, trader_id, kind, severity, message, _utc_now()),
            )
            self._conn.commit()
        return incident_id

    def list_incidents(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with _LOCK:
            rows = self._conn.execute(
                """
                SELECT incident_id, trader_id, kind, severity, message, created_at,
                       acknowledged, resolved
                FROM dt_incidents
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def enqueue_operator_command(self, trader_id: str, command_kind: str) -> str:
        command_id = f"cmd-{trader_id}-{command_kind}-{_utc_now()}"
        with _LOCK:
            self._conn.execute(
                """
                INSERT INTO dt_operator_commands(command_id, trader_id, command_kind, created_at)
                VALUES (?,?,?,?)
                """,
                (command_id, trader_id, command_kind, _utc_now()),
            )
            self._conn.commit()
        return command_id

    def pending_operator_commands(self, trader_id: str) -> list[dict[str, Any]]:
        with _LOCK:
            rows = self._conn.execute(
                """
                SELECT command_id, command_kind, created_at
                FROM dt_operator_commands
                WHERE trader_id=? AND consumed=0
                ORDER BY created_at ASC
                """,
                (trader_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def consume_operator_commands(self, command_ids: list[str]) -> None:
        if not command_ids:
            return
        with _LOCK:
            for cid in command_ids:
                self._conn.execute(
                    "UPDATE dt_operator_commands SET consumed=1 WHERE command_id=?",
                    (cid,),
                )
            self._conn.commit()

    def create_trader(self, cfg: SessionConfig, *, notes: str = "") -> dict[str, Any]:
        """Register a new simulated daytrader in the ledger."""
        with _LOCK:
            existing = self._conn.execute(
                "SELECT trader_id FROM dt_traders WHERE trader_id=?",
                (cfg.trader_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"trader_exists:{cfg.trader_id}")
            payload = {
                "trader_id": cfg.trader_id,
                "display_name": cfg.display_name,
                "symbol": cfg.symbol,
                "contract_id": cfg.contract_id,
                "strategy_id": cfg.strategy_id,
                "starting_equity": cfg.starting_equity,
                "quantity": cfg.quantity,
                "or_minutes": cfg.or_minutes,
                "no_new_entry_after": cfg.no_new_entry_after,
                "force_flat_time": cfg.force_flat_time,
                "rth_start": cfg.rth_start,
                "rth_end": cfg.rth_end,
                "timezone": cfg.timezone,
                "max_daily_loss_r": cfg.max_daily_loss_r,
                "tick_size": cfg.tick_size,
                "point_value": cfg.point_value,
                "commission_per_side": cfg.commission_per_side,
                "config_generation": cfg.config_generation,
                "notes": notes,
            }
            self._conn.execute(
                """
                INSERT INTO dt_traders(
                    trader_id, display_name, symbol, contract_id, strategy_id,
                    config_generation, starting_equity, enabled, status, config_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    cfg.trader_id,
                    cfg.display_name,
                    cfg.symbol,
                    cfg.contract_id,
                    cfg.strategy_id,
                    cfg.config_generation,
                    cfg.starting_equity,
                    1,
                    "ready",
                    json.dumps(payload, sort_keys=True),
                ),
            )
            self._conn.commit()
        return self.trader_card(cfg.trader_id)

    def get_session_config(self, trader_id: str) -> SessionConfig:
        with _LOCK:
            row = self._conn.execute(
                "SELECT config_json, enabled FROM dt_traders WHERE trader_id=?",
                (trader_id,),
            ).fetchone()
        if row is None:
            raise KeyError(trader_id)
        if not row["enabled"]:
            raise KeyError(f"trader_disabled:{trader_id}")
        payload = json.loads(row["config_json"])
        return session_config_from_row(payload)

    def list_enabled_trader_ids(self) -> list[str]:
        with _LOCK:
            rows = self._conn.execute(
                "SELECT trader_id FROM dt_traders WHERE enabled=1 ORDER BY trader_id"
            ).fetchall()
        return [str(r["trader_id"]) for r in rows]

    def list_trader_cards(self) -> list[dict[str, Any]]:
        ids = self.list_enabled_trader_ids()
        return [self.trader_card(tid) for tid in ids]

    def trader_card(self, trader_id: str) -> dict[str, Any]:
        """One simulated trader summary card (directory entry)."""
        with _LOCK:
            t = self._conn.execute(
                "SELECT * FROM dt_traders WHERE trader_id=?",
                (trader_id,),
            ).fetchone()
            if t is None:
                raise KeyError(trader_id)
            s = self._conn.execute(
                """
                SELECT * FROM dt_sessions
                WHERE trader_id=?
                ORDER BY trading_date DESC
                LIMIT 1
                """,
                (trader_id,),
            ).fetchone()
        open_pos = None
        if s and s["open_position_json"]:
            open_pos = json.loads(s["open_position_json"])
        config = json.loads(t["config_json"])
        return {
            "trader_id": t["trader_id"],
            "display_name": t["display_name"],
            "symbol": t["symbol"],
            "contract_id": t["contract_id"],
            "strategy_id": t["strategy_id"],
            "status": (
                t["status"]
                if s is None
                else ("live" if not s["complete"] else "flat")
            ),
            "equity": s["equity"] if s else t["starting_equity"],
            "cash": s["cash"] if s else t["starting_equity"],
            "starting_equity": t["starting_equity"],
            "day_return": s["day_return"] if s else 0.0,
            "realized_pnl": s["realized_pnl"] if s else 0.0,
            "positions_count": 1 if open_pos else 0,
            "open_position": open_pos,
            "trading_date": s["trading_date"] if s else None,
            "execution_mode_id": s["execution_mode_id"] if s else "1m_close",
            "semantics_version": s["semantics_version"]
            if s
            else "daytrade-live-1m-close-v1",
            "mtm_quality": "ledger" if s else "bootstrap",
            "live_scale_label": "1m_close（穩定 live paper）— 唔可比對 1s_worst 回測",
            "notes": config.get("notes") or "",
            # Session / risk params (card surface + detail drawer)
            "quantity": int(config.get("quantity") or 1),
            "or_minutes": int(config.get("or_minutes") or 5),
            "no_new_entry_after": str(config.get("no_new_entry_after") or "14:30"),
            "force_flat_time": str(config.get("force_flat_time") or "14:45"),
            "rth_start": str(config.get("rth_start") or "08:30"),
            "rth_end": str(config.get("rth_end") or "15:00"),
            "timezone": str(config.get("timezone") or "America/Chicago"),
            "max_daily_loss_r": float(config.get("max_daily_loss_r") or 4.0),
            "profile_path": f"/daytrade/traders/{t['trader_id']}",
        }

    # Back-compat name
    def personal_card(self, trader_id: str) -> dict[str, Any]:
        return self.trader_card(trader_id)

    def scorecard(self, trader_id: str, trading_date: date | None = None) -> dict[str, Any]:
        """成績表：由 ledger 事件重播，唔另計 P&L（對齊 stats v2 配對邏輯）。"""
        from futures_research.daytrade.stats import pair_trades_from_events

        live = self.trader_live(trader_id, trading_date)
        cfg = self.get_session_config(trader_id)
        events = live.get("events_today") or []
        opens = [e for e in events if e.get("kind") == "OPEN"]
        trades = pair_trades_from_events(events, point_value=cfg.point_value)
        wins = sum(1 for t in trades if t["won"])
        losses = sum(1 for t in trades if float(t["pnl"]) < 0)
        decided = wins + losses
        total_pnl = sum(float(t["pnl"]) for t in trades)
        return {
            "trader_id": trader_id,
            "display_name": live["display_name"],
            "symbol": live["symbol"],
            "trading_date": live["trading_date"],
            "equity": live["equity"],
            "cash": live["cash"],
            "starting_hint": live.get("config", {}).get("starting_equity"),
            "day_return": live["day_return"],
            "realized_pnl": live["realized_pnl"],
            "open_count": len(opens) - len(trades),
            "closed_trade_count": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / decided) if decided else None,
            "total_closed_pnl": total_pnl,
            "flat_compliance": live.get("open_position") is None,
            "open_position": live.get("open_position"),
            "trades": trades,
            "formula_version": "daytrade-scorecard-v2",
            "source": "ledger_events",
            "live_scale_label": live["live_scale_label"],
        }

    def trader_live(self, trader_id: str, trading_date: date | None = None) -> dict[str, Any]:
        with _LOCK:
            t = self._conn.execute(
                "SELECT * FROM dt_traders WHERE trader_id=?", (trader_id,)
            ).fetchone()
            if t is None:
                raise KeyError(trader_id)
            if trading_date is None:
                row = self._conn.execute(
                    """
                    SELECT trading_date FROM dt_sessions
                    WHERE trader_id=? ORDER BY trading_date DESC LIMIT 1
                    """,
                    (trader_id,),
                ).fetchone()
                trading_date_s = row["trading_date"] if row else date.today().isoformat()
            else:
                trading_date_s = trading_date.isoformat()
            sid = f"{trader_id}:{trading_date_s}"
            session = self._conn.execute(
                "SELECT * FROM dt_sessions WHERE session_id=?", (sid,)
            ).fetchone()
            events = self._conn.execute(
                """
                SELECT ordinal, kind, ts, payload_json FROM dt_events
                WHERE session_id=? ORDER BY ordinal ASC
                """,
                (sid,),
            ).fetchall()
        event_list = []
        for e in events:
            payload = json.loads(e["payload_json"])
            event_list.append(payload)
        open_pos = None
        if session and session["open_position_json"]:
            open_pos = json.loads(session["open_position_json"])
        return {
            "trader_id": trader_id,
            "display_name": t["display_name"],
            "symbol": t["symbol"],
            "contract_id": t["contract_id"],
            "strategy_id": t["strategy_id"],
            "config": json.loads(t["config_json"]),
            "trading_date": trading_date_s,
            "session": dict(session) if session else None,
            "open_position": open_pos,
            "positions": [open_pos] if open_pos else [],
            "events_today": event_list,
            "equity": session["equity"] if session else t["starting_equity"],
            "cash": session["cash"] if session else t["starting_equity"],
            "day_return": session["day_return"] if session else 0.0,
            "realized_pnl": session["realized_pnl"] if session else 0.0,
            "execution_mode_id": session["execution_mode_id"] if session else "1m_close",
            "semantics_version": session["semantics_version"]
            if session
            else "daytrade-live-1m-close-v1",
            "live_scale_label": "成交尺：1m_close（穩定 live paper）— 唔可比對 1s_worst 回測",
            "risk_flags": json.loads(session["risk_flags_json"]) if session else [],
            "block_reasons": _block_reasons(session, open_pos),
        }

    def positions_report(self, trader_id: str, trading_date: date | None = None) -> dict[str, Any]:
        live = self.trader_live(trader_id, trading_date)
        # Rebuild position truth from events only (no display-layer P&L formula)
        return {
            "trader_id": trader_id,
            "trading_date": live["trading_date"],
            "source": "ledger_events",
            "positions": live["positions"],
            "equity": live["equity"],
            "cash": live["cash"],
            "realized_pnl": live["realized_pnl"],
            "day_return": live["day_return"],
            "event_count": len(live["events_today"]),
            "live_scale_label": live["live_scale_label"],
            "formula_version": "daytrade-positions-ledger-v1",
        }

    def market_chart(
        self,
        trader_id: str,
        trading_date: date | None = None,
        *,
        bar_mode: str = "1m",
    ) -> dict[str, Any]:
        """OHLCV bars + trade markers + OR levels for the trader's symbol."""
        from futures_research.daytrade.stats import (
            LIVE_SCALE_LABEL,
            market_reference_from_bars,
        )

        live = self.trader_live(trader_id, trading_date)
        cfg = self.get_session_config(trader_id)
        td = date.fromisoformat(str(live["trading_date"]))
        raw_bars = self.load_bars(symbol=cfg.symbol, trading_date=td, bar_mode=bar_mode)
        bars = [
            {
                "ts": b.ts.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in raw_bars
        ]
        markers: list[dict[str, Any]] = []
        or_high = or_low = None
        for ev in live.get("events_today") or []:
            kind = str(ev.get("kind") or "")
            if kind == "OR_READY":
                # note: "OR high=x low=y" or reference=high
                note = str(ev.get("note") or "")
                if "high=" in note and "low=" in note:
                    try:
                        part_h = note.split("high=", 1)[1]
                        or_high = float(part_h.split()[0])
                        part_l = note.split("low=", 1)[1]
                        or_low = float(part_l.split()[0])
                    except (IndexError, ValueError):
                        pass
                if or_high is None and ev.get("reference") is not None:
                    or_high = float(ev["reference"])
            if kind in {"OPEN", "STOP", "TARGET", "FORCE_FLAT", "CLOSE"}:
                markers.append(
                    {
                        "ts": ev.get("ts"),
                        "kind": kind,
                        "side": ev.get("side"),
                        "price": ev.get("price"),
                        "ordinal": ev.get("ordinal"),
                        "note": ev.get("note"),
                    }
                )
        open_pos = live.get("open_position")
        if open_pos and or_high is None and open_pos.get("entry_reference") is not None:
            # Best-effort: long breakout ref is or_high
            if open_pos.get("side") == "long":
                or_high = float(open_pos["entry_reference"])
                if open_pos.get("stop") is not None:
                    or_low = float(open_pos["stop"])
            elif open_pos.get("side") == "short":
                or_low = float(open_pos["entry_reference"])
                if open_pos.get("stop") is not None:
                    or_high = float(open_pos["stop"])

        ref = market_reference_from_bars(
            bars,
            or_minutes=cfg.or_minutes,
            point_value=cfg.point_value,
            open_position=open_pos if isinstance(open_pos, dict) else None,
        )
        # Prefer event OR when present
        if or_high is not None:
            ref["or_high"] = or_high
        if or_low is not None:
            ref["or_low"] = or_low
        if or_high is not None and or_low is not None:
            ref["or_width"] = float(or_high) - float(or_low)
            ref["or_width_dollars"] = ref["or_width"] * cfg.point_value

        levels = {
            "or_high": ref.get("or_high"),
            "or_low": ref.get("or_low"),
            "stop": open_pos.get("stop") if isinstance(open_pos, dict) else None,
            "target": open_pos.get("target") if isinstance(open_pos, dict) else None,
            "entry": open_pos.get("entry_price") if isinstance(open_pos, dict) else None,
        }
        return {
            "schema": "daytrade_market_chart.v1",
            "trader_id": trader_id,
            "display_name": live["display_name"],
            "symbol": cfg.symbol,
            "contract_id": cfg.contract_id,
            "bar_mode": bar_mode,
            "trading_date": live["trading_date"],
            "bars": bars,
            "markers": markers,
            "levels": levels,
            "reference": ref,
            "point_value": cfg.point_value,
            "tick_size": cfg.tick_size,
            "open_position": open_pos,
            "live_scale_label": LIVE_SCALE_LABEL,
            "formula_version": "daytrade-market-chart-v1",
            "limitations": [
                "bars_from_daytrade_ledger_only",
                "live_scale_is_1m_close_not_comparable_to_1s_worst",
            ],
        }

    def equity_series(
        self,
        trader_id: str,
        trading_date: date | None = None,
    ) -> dict[str, Any]:
        """Equity / realized path from ledger events (authority)."""
        from futures_research.daytrade.stats import (
            LIVE_SCALE_LABEL,
            max_drawdown_from_equity,
        )

        live = self.trader_live(trader_id, trading_date)
        cfg = self.get_session_config(trader_id)
        points: list[dict[str, Any]] = []
        equities: list[float] = []
        # Seed with session start
        start_eq = float(cfg.starting_equity)
        if live.get("session"):
            start_eq = float(live["session"].get("starting_equity") or start_eq)
        points.append(
            {
                "ts": None,
                "equity": start_eq,
                "realized_pnl": 0.0,
                "kind": "SEED",
                "ordinal": 0,
            }
        )
        equities.append(start_eq)
        for ev in live.get("events_today") or []:
            eq = float(ev.get("equity") or start_eq)
            points.append(
                {
                    "ts": ev.get("ts"),
                    "equity": eq,
                    "realized_pnl": float(ev.get("realized_pnl") or 0),
                    "kind": ev.get("kind"),
                    "ordinal": ev.get("ordinal"),
                }
            )
            equities.append(eq)
        peak = max(equities) if equities else start_eq
        end_eq = equities[-1] if equities else start_eq
        return {
            "schema": "daytrade_equity_series.v1",
            "trader_id": trader_id,
            "trading_date": live["trading_date"],
            "points": points,
            "starting_equity": start_eq,
            "ending_equity": end_eq,
            "peak_equity": peak,
            "max_drawdown": max_drawdown_from_equity(equities),
            "day_return": live["day_return"],
            "realized_pnl": live["realized_pnl"],
            "live_scale_label": LIVE_SCALE_LABEL,
            "formula_version": "daytrade-equity-series-v1",
            "source": "ledger_events",
        }

    def stats(
        self,
        trader_id: str,
        *,
        window: str = "today",
        trading_date: date | None = None,
    ) -> dict[str, Any]:
        """Logical stats from ledger events. window: today | 7d | 30d | all."""
        from futures_research.daytrade.stats import (
            LIVE_SCALE_LABEL,
            STATS_FORMULA_VERSION,
            pair_trades_from_events,
            summarize_trades,
        )

        cfg = self.get_session_config(trader_id)
        win = (window or "today").strip().lower()
        if win not in {"today", "7d", "30d", "all"}:
            win = "today"

        with _LOCK:
            t = self._conn.execute(
                "SELECT * FROM dt_traders WHERE trader_id=?", (trader_id,)
            ).fetchone()
            if t is None:
                raise KeyError(trader_id)
            sessions = self._conn.execute(
                """
                SELECT * FROM dt_sessions
                WHERE trader_id=?
                ORDER BY trading_date ASC
                """,
                (trader_id,),
            ).fetchall()

        if trading_date is not None:
            target = trading_date.isoformat()
            sessions = [s for s in sessions if s["trading_date"] == target]
        elif win == "today":
            live = self.trader_live(trader_id)
            target = str(live["trading_date"])
            sessions = [s for s in sessions if s["trading_date"] == target]
        elif win in {"7d", "30d"}:
            from datetime import timedelta

            days = 7 if win == "7d" else 30
            cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
            sessions = [s for s in sessions if s["trading_date"] >= cutoff]

        all_events: list[dict[str, Any]] = []
        equities: list[float] = []
        trading_days = 0
        active_days = 0
        flat_ok_days = 0
        chain_start: float | None = None
        chain_end: float | None = None

        for s in sessions:
            trading_days += 1
            sid = s["session_id"]
            with _LOCK:
                rows = self._conn.execute(
                    """
                    SELECT payload_json FROM dt_events
                    WHERE session_id=? ORDER BY ordinal ASC
                    """,
                    (sid,),
                ).fetchall()
            day_events = [json.loads(r["payload_json"]) for r in rows]
            all_events.extend(day_events)
            start_eq = float(s["starting_equity"])
            if chain_start is None:
                chain_start = start_eq
            equities.append(start_eq)
            for ev in day_events:
                equities.append(float(ev.get("equity") or start_eq))
            end_eq = float(s["equity"])
            chain_end = end_eq
            if any(e.get("kind") == "OPEN" for e in day_events):
                active_days += 1
            open_pos = None
            if s["open_position_json"]:
                open_pos = json.loads(s["open_position_json"])
            if open_pos is None:
                flat_ok_days += 1

        trades = pair_trades_from_events(all_events, point_value=cfg.point_value)
        summary = summarize_trades(
            trades,
            equities=equities,
            starting_equity=chain_start if chain_start is not None else cfg.starting_equity,
            ending_equity=chain_end
            if chain_end is not None
            else (equities[-1] if equities else cfg.starting_equity),
        )
        summary.update(
            {
                "schema": "daytrade_stats.v2",
                "trader_id": trader_id,
                "display_name": t["display_name"],
                "symbol": t["symbol"],
                "window": win,
                "trading_days": trading_days,
                "active_days": active_days,
                "flat_compliance_days": flat_ok_days,
                "flat_compliance_rate": (flat_ok_days / trading_days)
                if trading_days
                else None,
                "open_count": sum(1 for e in all_events if e.get("kind") == "OPEN"),
                "live_scale_label": LIVE_SCALE_LABEL,
                "formula_version": STATS_FORMULA_VERSION,
                "source": "ledger_events",
            }
        )
        return summary


def _block_reasons(session: sqlite3.Row | None, open_pos: dict[str, Any] | None) -> list[str]:
    reasons: list[str] = []
    if session is None:
        return reasons
    flags = json.loads(session["risk_flags_json"] or "[]")
    for f in flags:
        reasons.append(str(f))
    if session["complete"] and open_pos:
        reasons.append("OVERNIGHT_BREACH")
    return reasons


def _event_from_payload(payload: dict[str, Any]) -> SessionEvent:
    ts_raw = payload["ts"]
    ts = datetime.fromisoformat(ts_raw)
    return SessionEvent(
        ordinal=int(payload["ordinal"]),
        kind=payload["kind"],
        ts=ts,
        symbol=payload["symbol"],
        side=payload.get("side"),
        qty=int(payload.get("qty") or 0),
        price=payload.get("price"),
        reference=payload.get("reference"),
        stop=payload.get("stop"),
        target=payload.get("target"),
        realized_pnl=float(payload["realized_pnl"]),
        cash=float(payload["cash"]),
        equity=float(payload["equity"]),
        note=str(payload.get("note") or ""),
        fill_policy=payload.get("fill_policy"),
        bar_side_used=payload.get("bar_side_used"),
    )


# silence unused import for type checkers that want OpenPosition used
_ = OpenPosition
