"""Unit tests for daytrade stats pairing + drawdown."""

from __future__ import annotations

from futures_research.daytrade.stats import (
    max_drawdown_from_equity,
    pair_trades_from_events,
    summarize_trades,
)


def test_pair_trades_and_r_multiple() -> None:
    events = [
        {
            "kind": "OPEN",
            "ordinal": 1,
            "side": "long",
            "qty": 1,
            "price": 100.0,
            "stop": 99.0,
            "target": 101.0,
            "realized_pnl": 0.0,
            "ts": "2026-07-30T09:00:00-05:00",
        },
        {
            "kind": "TARGET",
            "ordinal": 2,
            "price": 101.0,
            "realized_pnl": 20.0,  # 1 point * 20 NQ
            "ts": "2026-07-30T09:10:00-05:00",
        },
    ]
    trades = pair_trades_from_events(events, point_value=20.0)
    assert len(trades) == 1
    assert trades[0]["pnl"] == 20.0
    assert trades[0]["won"] is True
    assert trades[0]["one_r_dollars"] == 20.0  # 1 pt * 20
    assert abs(float(trades[0]["r_multiple"]) - 1.0) < 1e-9

    summary = summarize_trades(
        trades,
        equities=[100000, 100020],
        starting_equity=100000,
        ending_equity=100020,
    )
    assert summary["wins"] == 1
    assert summary["win_rate"] == 1.0
    assert summary["expectancy"] == 20.0
    assert abs(float(summary["expectancy_r"]) - 1.0) < 1e-9


def test_max_drawdown_exact() -> None:
    dd = max_drawdown_from_equity([100.0, 110.0, 99.0, 105.0])
    assert abs(dd - (11.0 / 110.0)) < 1e-9
