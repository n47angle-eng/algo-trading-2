"""Live IB market path must fan-out push on process deltas (not replay-only)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from futures_research.api import paper_runtime_manager as manager
from futures_research.paper.models import ClosedMarketInput


def _closed_bar(contract_id: str = "NQ-202609-CME") -> ClosedMarketInput:
    return ClosedMarketInput.create(
        provider_session_id="live-test",
        contract_id=contract_id,
        timeframe="1m",
        mode="live",
        event_at="2026-07-31T14:00:00Z",
        received_at="2026-07-31T14:00:01Z",
        open_price=21000.0,
        high_price=21010.0,
        low_price=20990.0,
        close_price=21005.0,
        volume=100.0,
        source_kind="live",
    )


def test_on_market_update_fans_out_push_on_trade_deltas() -> None:
    """_on_market_update must call fanout_from_process_deltas for live bars."""
    fanout_calls: list[dict[str, Any]] = []

    def capture_fanout(**kwargs: Any) -> list[dict[str, Any]]:
        fanout_calls.append(kwargs)
        return [{"sent": 1}]

    process_result = SimpleNamespace(
        duplicate=False,
        decision_count_delta=0,
        fill_count_delta=1,
        trade_count_delta=0,
        lifecycle="running",
    )
    snap = SimpleNamespace(
        lifecycle="running",
        position_quantity=1,
        realized_pnl=0.0,
        realized_r=0.0,
    )
    runtime = MagicMock()
    runtime.snapshot.side_effect = [snap, snap]  # lifecycle check + post-process
    runtime.process.return_value = process_result
    runtime._store.runtime_projection.return_value = {
        "selection_json": '{"contract_id":"NQ-202609-CME"}',
    }

    # Isolate process-local runtime map
    with manager._LOCK:  # noqa: SLF001
        prior = dict(manager._RUNTIMES)
        manager._RUNTIMES.clear()
        manager._RUNTIMES["trader-live-1"] = runtime

    try:
        with patch(
            "futures_research.api.push_fanout.fanout_from_process_deltas",
            side_effect=capture_fanout,
        ):
            # Patch at import site inside the function body via module-level
            with patch.object(
                manager,
                "_RUNTIMES",
                {"trader-live-1": runtime},
            ):
                # Call the real shipped live handler
                manager._on_market_update("NQ-202609-CME", _closed_bar())
    finally:
        with manager._LOCK:  # noqa: SLF001
            manager._RUNTIMES.clear()
            manager._RUNTIMES.update(prior)

    runtime.process.assert_called_once()
    assert len(fanout_calls) == 1
    assert fanout_calls[0]["trader_id"] == "trader-live-1"
    assert fanout_calls[0]["fill_count_delta"] == 1
    assert fanout_calls[0]["decision_count_delta"] == 0


def test_on_market_update_skips_fanout_on_duplicate_or_zero_delta() -> None:
    fanout_calls: list[Any] = []

    process_result = SimpleNamespace(
        duplicate=True,
        decision_count_delta=1,
        fill_count_delta=0,
        trade_count_delta=0,
        lifecycle="running",
    )
    snap = SimpleNamespace(lifecycle="running")
    runtime = MagicMock()
    runtime.snapshot.return_value = snap
    runtime.process.return_value = process_result
    runtime._store.runtime_projection.return_value = {
        "selection_json": '{"contract_id":"NQ-202609-CME"}',
    }

    with manager._LOCK:  # noqa: SLF001
        prior = dict(manager._RUNTIMES)
        manager._RUNTIMES.clear()
        manager._RUNTIMES["trader-dup"] = runtime
    try:
        with patch(
            "futures_research.api.push_fanout.fanout_from_process_deltas",
            side_effect=lambda **kw: fanout_calls.append(kw) or [],
        ):
            manager._on_market_update("NQ-202609-CME", _closed_bar())
    finally:
        with manager._LOCK:  # noqa: SLF001
            manager._RUNTIMES.clear()
            manager._RUNTIMES.update(prior)

    assert fanout_calls == []
