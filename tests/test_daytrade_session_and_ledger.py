"""Daytrade run_session + ledger append + prefix fail-closed."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from futures_research.daytrade.backtest_gate import estimate_backtest, run_daytrade_backtest
from futures_research.daytrade.ledger import DaytradeLedger
from futures_research.daytrade.models import DayBar, DaytradeDataError, PrefixMismatchError
from futures_research.daytrade.presets import get_trader_config
from futures_research.daytrade.runner import DaytradeRunner
from futures_research.daytrade.session_engine import run_session

TZ = ZoneInfo("America/Chicago")
TRADING_DATE = date(2026, 7, 30)


def _bar(minute_offset: int, o: float, h: float, low: float, c: float) -> DayBar:
    # RTH open 08:30
    base = datetime(2026, 7, 30, 8, 30, tzinfo=TZ)
    return DayBar(
        symbol="NQ",
        ts=base + timedelta(minutes=minute_offset),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=100.0,
        bar_mode="1m",
    )


def _or_breakout_bars() -> list[DayBar]:
    """5m OR then long breakout, then target hit."""
    bars: list[DayBar] = []
    # OR window minutes 0-4: range 100-101
    for i in range(5):
        bars.append(_bar(i, 100.0, 101.0, 100.0, 100.5))
    # minute 5: OR ready bar (still inside)
    bars.append(_bar(5, 100.5, 100.8, 100.2, 100.6))
    # minute 6: breakout high through 101
    bars.append(_bar(6, 100.6, 102.0, 100.5, 101.8))
    # later: hit target (entry worst/close ~101.8 or close, stop=100, risk~1.8 → target ~103.6)
    # With close policy entry at close 101.8, stop 100, risk 1.8, target 103.6
    bars.append(_bar(20, 102.0, 104.0, 101.9, 103.8))
    # force flat region bars near 14:45
    bars.append(
        DayBar(
            symbol="NQ",
            ts=datetime(2026, 7, 30, 14, 45, tzinfo=TZ),
            open=103.0,
            high=103.5,
            low=102.5,
            close=103.0,
            volume=10,
            bar_mode="1m",
        )
    )
    return bars


def test_run_session_or_breakout_long_close_policy() -> None:
    cfg = get_trader_config("dt-nq-or-1")
    result = run_session(
        cfg,
        trading_date=TRADING_DATE,
        bars=_or_breakout_bars(),
        starting_equity=cfg.starting_equity,
        complete=True,
        execution_mode_id="1m_close",
    )
    kinds = [e.kind for e in result.events]
    assert "SESSION_OPEN" in kinds
    assert "OR_READY" in kinds
    assert "OPEN" in kinds
    assert result.prefix_matched is True
    assert result.execution_mode_id == "1m_close"
    closed = any(k in kinds for k in ("FORCE_FLAT", "TARGET", "STOP"))
    assert result.open_position is None or closed


def test_worst_side_long_entry_not_low() -> None:
    cfg = get_trader_config("dt-nq-or-1")
    bars = _or_breakout_bars()
    result = run_session(
        cfg,
        trading_date=TRADING_DATE,
        bars=bars,
        starting_equity=cfg.starting_equity,
        complete=False,
        execution_mode_id="1m_worst",
    )
    opens = [e for e in result.events if e.kind == "OPEN"]
    if opens:
        assert opens[0].bar_side_used == "high"
        assert opens[0].price is not None
        # breakout bar low is 100.5 — must not fill there on long open
        assert opens[0].price >= 101.0


def test_prefix_mismatch_raises() -> None:
    cfg = get_trader_config("dt-nq-or-1")
    bars = _or_breakout_bars()
    first = run_session(
        cfg,
        trading_date=TRADING_DATE,
        bars=bars[:6],
        starting_equity=cfg.starting_equity,
        complete=False,
        execution_mode_id="1m_close",
    )
    # Tamper committed fingerprint
    bad = list(first.events)
    if not bad:
        pytest.skip("no events")
    from futures_research.daytrade.models import SessionEvent

    e0 = bad[0]
    bad[0] = SessionEvent(
        ordinal=e0.ordinal,
        kind=e0.kind,
        ts=e0.ts,
        symbol=e0.symbol,
        side=e0.side,
        qty=e0.qty,
        price=e0.price,
        reference=e0.reference,
        stop=e0.stop,
        target=e0.target,
        realized_pnl=e0.realized_pnl,
        cash=999999.0,  # diverge
        equity=e0.equity,
        note=e0.note,
        fill_policy=e0.fill_policy,
        bar_side_used=e0.bar_side_used,
    )
    with pytest.raises(PrefixMismatchError):
        run_session(
            cfg,
            trading_date=TRADING_DATE,
            bars=bars,
            starting_equity=cfg.starting_equity,
            complete=False,
            execution_mode_id="1m_close",
            committed_events=bad,
        )


def test_ledger_append_and_personal_card(tmp_path: Path) -> None:
    db = tmp_path / "dt.db"
    ledger = DaytradeLedger(db)
    ledger.bootstrap_traders_from_presets()
    bars = _or_breakout_bars()
    ledger.insert_bars(bars, TRADING_DATE)
    runner = DaytradeRunner(ledger)
    ledger.set_runner_enabled(True)
    # Multi-trader presets: always step the NQ fixture trader (bars are NQ-only)
    report = runner.step_trader("dt-nq-or-1", TRADING_DATE, complete=True)
    assert report.skipped_reason is None
    assert report.events_appended >= 1
    card = ledger.trader_card("dt-nq-or-1")
    assert card["trader_id"] == "dt-nq-or-1"
    assert card["profile_path"] == "/daytrade/traders/dt-nq-or-1"
    live = ledger.trader_live("dt-nq-or-1", TRADING_DATE)
    pos = ledger.positions_report("dt-nq-or-1", TRADING_DATE)
    assert pos["equity"] == live["equity"]
    assert pos["source"] == "ledger_events"
    report2 = runner.step_trader("dt-nq-or-1", TRADING_DATE, complete=True)
    assert report2.skipped_reason != "prefix_mismatch"


def test_runner_disabled_fail_closed(tmp_path: Path) -> None:
    ledger = DaytradeLedger(tmp_path / "dt2.db")
    ledger.bootstrap_traders_from_presets()
    ledger.set_runner_enabled(False)
    runner = DaytradeRunner(ledger)
    report = runner.step_trader("dt-nq-or-1", TRADING_DATE)
    assert report.skipped_reason == "runner_disabled"


def test_1s_worst_backtest_fail_closed_without_data() -> None:
    est = estimate_backtest(
        trader_id="dt-nq-or-1",
        start=TRADING_DATE,
        end=TRADING_DATE,
        execution_mode_id="1s_worst",
        bars_by_day={},
    )
    assert est.can_run is False
    assert est.degrade_reason == "missing_1s_bars_fail_closed"
    with pytest.raises(DaytradeDataError):
        run_daytrade_backtest(
            trader_id="dt-nq-or-1",
            start=TRADING_DATE,
            end=TRADING_DATE,
            execution_mode_id="1s_worst",
            bars_by_day={},
        )


def test_force_flat_command_clears_position() -> None:
    cfg = get_trader_config("dt-nq-or-1")
    # Only OR + breakout, no target — force_flat_command closes
    bars = []
    for i in range(5):
        bars.append(_bar(i, 100.0, 101.0, 100.0, 100.5))
    bars.append(_bar(5, 100.5, 100.8, 100.2, 100.6))
    bars.append(_bar(6, 100.6, 102.0, 100.5, 101.8))
    bars.append(_bar(10, 101.8, 102.2, 101.5, 102.0))
    result = run_session(
        cfg,
        trading_date=TRADING_DATE,
        bars=bars,
        starting_equity=cfg.starting_equity,
        complete=False,
        execution_mode_id="1m_close",
        force_flat_command=True,
    )
    assert any(e.kind == "FORCE_FLAT" for e in result.events) or result.open_position is None
