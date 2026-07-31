from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from futures_research.paper.replay import (
    SavedRealReplayHarness,
    load_saved_real_nq_window,
)


def test_saved_real_replay_covers_four_paths_in_os_temp(tmp_path: Path) -> None:
    window = load_saved_real_nq_window(
        end_at=datetime(2026, 7, 23, 21, 0, tzinfo=UTC),
        days=30,
    )

    assert window.contract_id == "NQ-202609-CME"
    assert window.start_at >= window.end_at - timedelta(days=30)
    assert len(window.bars) > 1_000
    assert {bar.source for bar in window.bars} == {"IB"}

    evidence = SavedRealReplayHarness(
        window=window,
        temp_root=tmp_path,
    ).run_four_paths()
    by_name = {item.path: item for item in evidence}

    assert set(by_name) == {
        "long_profit",
        "short_loss",
        "no_signal",
        "safety_trigger",
    }
    assert by_name["long_profit"].trade_count == 1
    assert by_name["long_profit"].realized_pnl > 0
    assert by_name["short_loss"].trade_count == 1
    assert by_name["short_loss"].realized_pnl < 0
    assert by_name["no_signal"].trade_count == 0
    assert by_name["no_signal"].position_quantity == 0
    assert by_name["safety_trigger"].lifecycle == "tripped"
    assert by_name["safety_trigger"].position_quantity == 0
    assert all(item.market_mode == "replay_test" for item in evidence)
    assert all(item.provider_session_id == "saved-real-ib-replay" for item in evidence)
