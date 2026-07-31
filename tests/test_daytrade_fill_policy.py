"""worst_side fill invariants — long entry must not use low."""

from __future__ import annotations

import pytest

from futures_research.daytrade.fill_policy import (
    BarOHLC,
    action_from_position,
    resolve_fill,
    resolve_worst_side_fill,
)


def test_long_entry_uses_high_not_low() -> None:
    bar = BarOHLC(open=100, high=101, low=99, close=100.5)
    q = resolve_worst_side_fill(action="buy", reference=100.0, bar=bar)
    assert q.price == 101.0
    assert q.bar_side_used == "high"
    assert q.price != bar.low


def test_long_exit_uses_low() -> None:
    bar = BarOHLC(open=100, high=101, low=98, close=99)
    q = resolve_worst_side_fill(action="sell", reference=99.0, bar=bar)
    assert q.price == 98.0
    assert q.bar_side_used == "low"


def test_short_entry_uses_low() -> None:
    bar = BarOHLC(open=100, high=101, low=99, close=99.5)
    q = resolve_worst_side_fill(action="sell", reference=100.0, bar=bar)
    assert q.price == 99.0


def test_short_exit_uses_high() -> None:
    bar = BarOHLC(open=100, high=102, low=99, close=101)
    q = resolve_worst_side_fill(action="buy", reference=100.0, bar=bar)
    assert q.price == 102.0


def test_clamp_buy_not_better_than_reference() -> None:
    # bar high below reference → clamp to reference (not optimistic low)
    bar = BarOHLC(open=100, high=100.5, low=99, close=100)
    q = resolve_worst_side_fill(action="buy", reference=101.0, bar=bar)
    assert q.price == 101.0
    assert q.clamped is True


def test_clamp_sell_not_better_than_reference() -> None:
    bar = BarOHLC(open=100, high=101, low=99.5, close=100)
    q = resolve_worst_side_fill(action="sell", reference=99.0, bar=bar)
    assert q.price == 99.0
    assert q.clamped is True


def test_missing_bar_fail_open_reference() -> None:
    q = resolve_worst_side_fill(action="buy", reference=50.0, bar=None)
    assert q.price == 50.0
    assert q.missing_ohlc is True


def test_action_mapping() -> None:
    assert action_from_position(position_side="long", is_open=True) == "buy"
    assert action_from_position(position_side="long", is_open=False) == "sell"
    assert action_from_position(position_side="short", is_open=True) == "sell"
    assert action_from_position(position_side="short", is_open=False) == "buy"


def test_resolve_fill_close_policy() -> None:
    bar = BarOHLC(open=1, high=3, low=0.5, close=2.0)
    q = resolve_fill(policy="close", action="buy", reference=1.0, bar=bar)
    assert q.price == 2.0
    assert q.policy == "close"


def test_long_entry_must_not_silently_use_low_regression() -> None:
    """Regression: residual 'long always low' would make entry optimistic."""
    bar = BarOHLC(open=100, high=105, low=95, close=102)
    buy = resolve_worst_side_fill(action="buy", reference=100.0, bar=bar)
    assert buy.price == 105.0
    with pytest.raises(AssertionError):
        assert buy.price == 95.0
