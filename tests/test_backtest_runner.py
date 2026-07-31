"""End-to-end deterministic coverage for the closed-bar backtest runner."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from futures_research.backtest.persistence import ResultExporter, SqliteRunStore
from futures_research.backtest.runner import BacktestRunConfig, BacktestRunner
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import expected_minute_timestamps
from futures_research.data.storage import CanonicalStore


def test_complete_realistic_eth_session_emits_an_auditable_warmup_smoke_run(
    tmp_path: Path,
    contracts_registry: ContractRegistry,
) -> None:
    """A complete but under-warmed range must publish a zero-trade result, not look ahead."""
    contract = contracts_registry.by_symbol("NQ")
    start = datetime(2026, 7, 22, 22, tzinfo=UTC)
    end = datetime(2026, 7, 23, 21, tzinfo=UTC)
    bars = _canonical_bars(contract, start=start, end=end)
    warmup_bars = _canonical_bars(
        contract,
        start=datetime(2026, 7, 21, 22, tzinfo=UTC),
        end=datetime(2026, 7, 22, 21, tzinfo=UTC),
    )
    market_root = tmp_path / "market"
    CanonicalStore(market_root).append([*warmup_bars, *bars])
    quality_root = tmp_path / "quality-reports"
    report_path = quality_root / contract.contract_id / "fixture-quality.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "contract_id": contract.contract_id,
                "issues": [],
            }
        ),
        encoding="utf-8",
    )
    runner = BacktestRunner(
        canonical_store=CanonicalStore(market_root),
        run_store=SqliteRunStore(tmp_path / "runs.sqlite3"),
        result_exporter=ResultExporter(tmp_path / "results"),
        quality_reports_root=quality_root,
    )

    artifacts = runner.run(
        contract=contract,
        config=BacktestRunConfig(
            run_id="runner-smoke-001",
            strategy_version="trend-v0",
            session_name="eth",
            range_start=bars[0].timestamp,
            range_end=bars[-1].timestamp + timedelta(minutes=1),
            initial_capital=100_000.0,
            quantity=1,
            verify_nautilus_replay=False,
        ),
    )

    assert artifacts.raw_bar_count == 1380
    assert artifacts.admitted_bar_count == 1380
    assert artifacts.warmup_bar_count == 1380
    assert artifacts.entry_bar_count == 276
    assert artifacts.nautilus_replay_iterations is None
    assert artifacts.result.metrics.trade_count == 0
    assert artifacts.result.trade_records == ()
    assert artifacts.result.warnings == (
        "daily_regime_calibration_unavailable: no closed historical daily warm-up "
        "before range_start; no entries were eligible",
    )
    assert artifacts.manifest.data_fingerprint.quality_report_ids == (
        f"{contract.contract_id}/fixture-quality.json",
    )
    assert artifacts.manifest.calibration is not None
    assert artifacts.manifest.calibration.status == "unavailable"
    assert artifacts.manifest.calibration.daily_source == "ib_native_daily"
    # quality_reports_root set → enforce; no error reports in fixture → admitted.
    assert artifacts.manifest.quality_gate_mode == "enforce"
    assert artifacts.manifest.validation_run is False
    # No native daily store in this fixture → empty daily fingerprint / under-warm.
    assert artifacts.manifest.calibration.data_fingerprint.bar_count == 0
    assert artifacts.manifest.calibration.window_start is None
    assert artifacts.manifest.calibration.window_end == start
    assert [event.sequence for event in artifacts.result.event_log] == list(
        range(1, len(artifacts.result.event_log) + 1)
    )
    # Without native settlement dailies, no Daily regime transitions are observed; the
    # gate stays cold (no TREND context) so entries cannot complete.
    assert not any(
        event.machine == "daily_regime" and event.to_state == "trend"
        for event in artifacts.result.event_log
    )

    main = json.loads(artifacts.exported.result_path.read_text(encoding="utf-8"))
    trades = json.loads(artifacts.exported.trades_path.read_text(encoding="utf-8"))
    events = json.loads(artifacts.exported.events_path.read_text(encoding="utf-8"))
    assert main["schema"] == "result.v1"
    assert main["events_ref"] == "events/runner-smoke-001.json"
    assert main["run"]["manifest"]["calibration"]["status"] == "unavailable"
    assert main["run"]["manifest"]["calibration"]["daily_source"] == "ib_native_daily"
    assert trades["trades"] == []
    assert not any(
        event["machine"] == "daily_regime" and event["to_state"] == "trend"
        for event in events["events"]
    )
    with sqlite3.connect(tmp_path / "runs.sqlite3") as connection:
        lookup = connection.execute(
            "SELECT trading_dates_status FROM run_lookup WHERE run_id = 'runner-smoke-001'"
        ).fetchone()
        consumed_dates = connection.execute(
            "SELECT trading_date FROM run_trading_dates WHERE run_id = 'runner-smoke-001'"
        ).fetchall()
    assert lookup == ("complete",)
    assert consumed_dates == [(date(2026, 7, 23).isoformat(),)]


def _canonical_bars(
    contract: ContractSpec,
    *,
    start: datetime,
    end: datetime,
) -> list[CanonicalBar]:
    """Create a complete, tick-aligned ETH session with realistic monotonic coverage."""
    timestamps = sorted(expected_minute_timestamps(contract, start, end, session_name="eth"))
    return [
        CanonicalBar(
            timestamp=timestamp,
            open=20_000.0 + index * 0.25,
            high=20_001.0 + index * 0.25,
            low=19_999.5 + index * 0.25,
            close=20_000.5 + index * 0.25,
            volume=100 + index,
            contract_id=contract.contract_id,
            source="fixture",
        )
        for index, timestamp in enumerate(timestamps)
    ]
