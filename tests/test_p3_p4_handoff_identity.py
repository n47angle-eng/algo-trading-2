"""Journey identity: coverage usable dates handoff fields stay intact."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from futures_research.data.coverage import compute_trading_day_coverage
from tests.test_coverage_facts import _minute_bars, _write_tiny_contract_config


def test_coverage_complete_dates_are_trading_day_labels_not_instants(
    tmp_path: Path,
) -> None:
    """Backend usable dates must be YYYY-MM-DD labels (no timezone shift)."""
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    bars = [
        *_minute_bars(contract, date(2026, 5, 5)),
        *_minute_bars(contract, date(2026, 5, 6)),
    ]
    projection = compute_trading_day_coverage(
        contract,
        bars,
        owner_entries=[],
    )
    document = projection.document
    assert document["status"] == "known"
    assert document["complete_trading_dates"] == ["2026-05-05", "2026-05-06"]
    for label in document["complete_trading_dates"]:
        assert len(label) == 10
        assert label[4] == "-" and label[7] == "-"
    segment = document["longest_complete_segment"]
    assert segment is not None
    assert segment["start_trading_date"] == "2026-05-05"
    assert segment["end_trading_date"] == "2026-05-06"
