"""Daytrade API — multi simulated traders, each with personal profile pages."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from futures_research.daytrade.backtest_gate import estimate_backtest, run_daytrade_backtest
from futures_research.daytrade.ledger import DaytradeLedger
from futures_research.daytrade.models import DayBar, DaytradeDataError
from futures_research.daytrade.modes import (
    BACKTEST_DEFAULT_MODE_ID,
    LIVE_DEFAULT_MODE_ID,
    list_execution_modes,
)
from futures_research.daytrade.presets import build_create_config, supported_symbols
from futures_research.daytrade.runner import DaytradeRunner, health_payload, ingest_bars

router = APIRouter(prefix="/api/v1/daytrade", tags=["daytrade"])

_ledger: DaytradeLedger | None = None
_runner: DaytradeRunner | None = None


def _get_ledger() -> DaytradeLedger:
    global _ledger
    if _ledger is None:
        _ledger = DaytradeLedger()
        _ledger.bootstrap_traders_from_presets()
    return _ledger


def _get_runner() -> DaytradeRunner:
    global _runner
    if _runner is None:
        _runner = DaytradeRunner(_get_ledger())
    return _runner


class RunnerBody(BaseModel):
    enabled: bool


class OperatorCommandBody(BaseModel):
    command: Literal["pause", "resume", "force_flat"]


class CreateTraderBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    symbol: Literal["NQ", "YM", "GC"]
    strategy_id: str = "opening_range_breakout_v1"
    starting_equity: float = Field(default=100_000.0, gt=0)
    quantity: int = Field(default=1, ge=1, le=20)
    or_minutes: int = Field(default=5, ge=1, le=60)
    no_new_entry_after: str = Field(default="14:30", pattern=r"^\d{2}:\d{2}$")
    force_flat_time: str = Field(default="14:45", pattern=r"^\d{2}:\d{2}$")
    max_daily_loss_r: float = Field(default=4.0, gt=0, le=20)
    notes: str = ""
    trader_id: str | None = None


class BarIn(BaseModel):
    symbol: str
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    bar_mode: Literal["1m", "1s"] = "1m"


class IngestBarsBody(BaseModel):
    trading_date: str
    bars: list[BarIn]


class StepBody(BaseModel):
    trading_date: str | None = None
    trader_id: str | None = None
    complete: bool = False


class BacktestRunBody(BaseModel):
    trader_id: str
    start: str
    end: str
    execution_mode_id: str = BACKTEST_DEFAULT_MODE_ID


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid date: {value}") from exc


def _parse_ts(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid ts: {value}") from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("America/Chicago"))
    return ts


def _require_trader(trader_id: str) -> None:
    try:
        _get_ledger().get_session_config(trader_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc


@router.get("/health")
def daytrade_health() -> dict[str, Any]:
    return health_payload(_get_ledger())


@router.get("/modes")
def daytrade_modes() -> dict[str, Any]:
    modes = [
        {
            "mode_id": m.mode_id,
            "bar_mode": m.bar_mode,
            "fill_price_policy": m.fill_price_policy,
            "semantics_version": m.semantics_version,
            "purpose": m.purpose,
        }
        for m in list_execution_modes()
    ]
    return {
        "live_default": LIVE_DEFAULT_MODE_ID,
        "backtest_default": BACKTEST_DEFAULT_MODE_ID,
        "modes": modes,
        "supported_symbols": supported_symbols(),
        "note": "每位模擬交易員有獨立個人頁；無 team 合併 P&L",
    }


@router.get("/traders")
def daytrade_list_traders() -> dict[str, Any]:
    ledger = _get_ledger()
    cards = ledger.list_trader_cards()
    return {
        "traders": cards,
        "count": len(cards),
        "runner_enabled": ledger.get_runner_enabled(),
        "supported_symbols": supported_symbols(),
        "live_scale_label": "1m_close（穩定 live paper）— 唔可比對 1s_worst 回測",
        "formula_version": "daytrade-traders-list-v1",
    }


@router.post("/traders", status_code=201)
def daytrade_create_trader(body: CreateTraderBody) -> dict[str, Any]:
    try:
        cfg = build_create_config(
            display_name=body.display_name,
            symbol=body.symbol,
            strategy_id=body.strategy_id,
            starting_equity=body.starting_equity,
            quantity=body.quantity,
            or_minutes=body.or_minutes,
            no_new_entry_after=body.no_new_entry_after,
            force_flat_time=body.force_flat_time,
            max_daily_loss_r=body.max_daily_loss_r,
            trader_id=body.trader_id,
            notes=body.notes,
        )
        card = _get_ledger().create_trader(cfg, notes=body.notes)
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("trader_exists"):
            raise HTTPException(status_code=409, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    return {
        "trader": card,
        "message": "模擬交易員已建立",
        "entry": card["profile_path"],
    }


@router.get("/traders/{trader_id}")
def daytrade_trader_profile(trader_id: str) -> dict[str, Any]:
    ledger = _get_ledger()
    try:
        card = ledger.trader_card(trader_id)
        cfg = ledger.get_session_config(trader_id)
        live = ledger.trader_live(trader_id)
        score = ledger.scorecard(trader_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc
    return {
        "profile": {
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
            "notes": card.get("notes") or "",
            "config_generation": cfg.config_generation,
        },
        "summary": card,
        "live": live,
        "scorecard": score,
        "links": {
            "live": f"/daytrade/traders/{trader_id}?tab=live",
            "positions": f"/daytrade/traders/{trader_id}?tab=positions",
            "scorecard": f"/daytrade/traders/{trader_id}?tab=scorecard",
            "activity": f"/daytrade/traders/{trader_id}?tab=activity",
        },
        "live_scale_label": card["live_scale_label"],
    }


@router.get("/traders/{trader_id}/live")
def daytrade_trader_live(trader_id: str, trading_date: str | None = None) -> dict[str, Any]:
    try:
        d = _parse_date(trading_date) if trading_date else None
        return _get_ledger().trader_live(trader_id, d)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc


@router.get("/traders/{trader_id}/positions")
def daytrade_trader_positions(
    trader_id: str, trading_date: str | None = None
) -> dict[str, Any]:
    try:
        d = _parse_date(trading_date) if trading_date else None
        return _get_ledger().positions_report(trader_id, d)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc


@router.get("/traders/{trader_id}/scorecard")
def daytrade_trader_scorecard(
    trader_id: str, trading_date: str | None = None
) -> dict[str, Any]:
    try:
        d = _parse_date(trading_date) if trading_date else None
        return _get_ledger().scorecard(trader_id, d)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc


@router.get("/traders/{trader_id}/market-chart")
def daytrade_trader_market_chart(
    trader_id: str,
    trading_date: str | None = None,
    bar_mode: str = "1m",
) -> dict[str, Any]:
    if bar_mode not in {"1m", "1s"}:
        raise HTTPException(status_code=400, detail="bar_mode must be 1m or 1s")
    try:
        d = _parse_date(trading_date) if trading_date else None
        return _get_ledger().market_chart(trader_id, d, bar_mode=bar_mode)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc


@router.get("/traders/{trader_id}/equity-series")
def daytrade_trader_equity_series(
    trader_id: str, trading_date: str | None = None
) -> dict[str, Any]:
    try:
        d = _parse_date(trading_date) if trading_date else None
        return _get_ledger().equity_series(trader_id, d)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc


@router.get("/traders/{trader_id}/stats")
def daytrade_trader_stats(
    trader_id: str,
    window: str = "today",
    trading_date: str | None = None,
) -> dict[str, Any]:
    try:
        d = _parse_date(trading_date) if trading_date else None
        return _get_ledger().stats(trader_id, window=window, trading_date=d)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc


@router.get("/traders/{trader_id}/analytics")
def daytrade_trader_analytics(
    trader_id: str, window: str = "today"
) -> dict[str, Any]:
    """Back-compat alias → stats v2 summary fields."""
    try:
        score = _get_ledger().stats(trader_id, window=window)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc
    return {
        "trader_id": trader_id,
        "window": score.get("window"),
        "trade_count": score["closed_trade_count"],
        "open_count": score.get("open_count"),
        "equity": score.get("ending_equity"),
        "cash": None,
        "day_return": score.get("total_return"),
        "realized_pnl": score.get("total_closed_pnl"),
        "win_rate": score["win_rate"],
        "wins": score["wins"],
        "losses": score["losses"],
        "expectancy": score.get("expectancy"),
        "expectancy_r": score.get("expectancy_r"),
        "profit_factor": score.get("profit_factor"),
        "max_drawdown": score.get("max_drawdown"),
        "flat_compliance_rate": score.get("flat_compliance_rate"),
        "sample_sufficient": score.get("sample_sufficient"),
        "formula_version": score["formula_version"],
        "live_scale_label": score["live_scale_label"],
    }


@router.get("/traders/{trader_id}/activity")
def daytrade_trader_activity(trader_id: str, view: str = "summary") -> dict[str, Any]:
    try:
        live = _get_ledger().trader_live(trader_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc
    name = str(live.get("display_name") or trader_id)
    bubbles: list[dict[str, Any]] = []
    for ev in live.get("events_today") or []:
        bubbles.append(
            {
                "trader_id": trader_id,
                "display_name": name,
                "ts": ev.get("ts"),
                "kind": ev.get("kind"),
                "text": _event_to_speech(ev),
                "equity": ev.get("equity"),
                "price": ev.get("price"),
            }
        )
    if view == "summary":
        bubbles = bubbles[-40:]
    return {
        "trader_id": trader_id,
        "view": view,
        "bubbles": bubbles,
        "source": "ledger_events_only",
        "live_scale_label": live["live_scale_label"],
    }


@router.post("/traders/{trader_id}/operator-command")
def daytrade_operator_command(
    trader_id: str, body: OperatorCommandBody
) -> dict[str, Any]:
    _require_trader(trader_id)
    command_id = _get_ledger().enqueue_operator_command(trader_id, body.command)
    return {"command_id": command_id, "trader_id": trader_id, "command": body.command}


@router.post("/runner")
def daytrade_runner_enable(body: RunnerBody) -> dict[str, Any]:
    ledger = _get_ledger()
    ledger.set_runner_enabled(body.enabled)
    return {
        "runner_enabled": ledger.get_runner_enabled(),
        "message": "enabled" if body.enabled else "disabled (fail-closed)",
    }


@router.post("/bars/ingest")
def daytrade_ingest_bars(body: IngestBarsBody) -> dict[str, Any]:
    trading_date = _parse_date(body.trading_date)
    bars: list[DayBar] = []
    for b in body.bars:
        bars.append(
            DayBar(
                symbol=b.symbol,
                ts=_parse_ts(b.ts),
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                bar_mode=b.bar_mode,
            )
        )
    n = ingest_bars(bars, trading_date, ledger=_get_ledger())
    return {
        "inserted": n,
        "trading_date": trading_date.isoformat(),
        "received": len(bars),
    }


@router.post("/step")
def daytrade_step(body: StepBody) -> dict[str, Any]:
    runner = _get_runner()
    trading_date = _parse_date(body.trading_date) if body.trading_date else date.today()
    if body.trader_id:
        _require_trader(body.trader_id)
        report = runner.step_trader(
            body.trader_id, trading_date, complete=body.complete
        )
        return {"reports": [asdict(report)]}
    reports = runner.step_all(trading_date, complete=body.complete)
    return {"reports": [asdict(r) for r in reports]}


@router.get("/incidents")
def daytrade_incidents() -> dict[str, Any]:
    return {"incidents": _get_ledger().list_incidents()}


@router.get("/bt/estimate")
def daytrade_bt_estimate(
    trader_id: str,
    start: str,
    end: str,
    execution_mode_id: str = BACKTEST_DEFAULT_MODE_ID,
) -> dict[str, Any]:
    cfg = _get_ledger().get_session_config(trader_id)
    est = estimate_backtest(
        trader_id=trader_id,
        start=_parse_date(start),
        end=_parse_date(end),
        execution_mode_id=execution_mode_id,
        bars_by_day={},
        config=cfg,
    )
    return {
        **est.__dict__,
        "trader_id": trader_id,
        "banner": (
            "成交模式：1s_worst（1 秒 K 線 × 最差邊："
            "買取高、賣取低，另加摩擦成本）— 現實悲觀版"
            if execution_mode_id == "1s_worst"
            else f"成交模式：{execution_mode_id}"
        ),
    }


@router.post("/bt/run")
def daytrade_bt_run(body: BacktestRunBody) -> dict[str, Any]:
    try:
        cfg = _get_ledger().get_session_config(body.trader_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="trader_not_found") from exc
    try:
        return run_daytrade_backtest(
            trader_id=body.trader_id,
            start=_parse_date(body.start),
            end=_parse_date(body.end),
            execution_mode_id=body.execution_mode_id,
            bars_by_day={},
            config=cfg,
        )
    except DaytradeDataError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "daytrade_backtest_fail_closed",
                "message": str(exc),
                "banner": (
                    "成交模式：1s_worst（1 秒 K 線 × 最差邊："
                    "買取高、賣取低，另加摩擦成本）— 現實悲觀版"
                ),
            },
        ) from exc


def _event_to_speech(ev: dict[str, Any]) -> str:
    kind = ev.get("kind")
    price = ev.get("price")
    side = ev.get("side")
    if kind == "OPEN":
        return f"開咗 {side} 倉，成交價 {price}。"
    if kind == "STOP":
        return f"止蝕離場，成交價 {price}。"
    if kind == "TARGET":
        return f"止賺離場，成交價 {price}。"
    if kind == "FORCE_FLAT":
        return f"強制平倉，成交價 {price}。"
    if kind == "OR_READY":
        return "開盤區間就緒。"
    if kind == "OVERNIGHT_BREACH":
        return "日終未平倉 — 事故。"
    if kind == "DAILY_LOSS_HALT":
        return "觸及日損停機。"
    if kind == "SESSION_OPEN":
        return "今日日內 session 開始。"
    return f"事件 {kind}。"
