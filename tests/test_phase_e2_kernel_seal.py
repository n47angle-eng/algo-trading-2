"""Phase E.2: kernel draft → evidence-complete product seal + rust/python golden align."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from futures_research.backtest.backtest_kernel_python import compute_backtest_loop_python
from futures_research.backtest.kernel_seal import seal_kernel_draft_to_run_result
from futures_research.backtest.records import (
    DataFingerprint,
    RunManifest,
    StrategyBinding,
)
from futures_research.contracts.backtest_loop import BacktestLoopRequest
from futures_research.data.contracts import (
    ContractSpec,
    ExecutionCostSpec,
    SessionHours,
    SlippageTicks,
)
from futures_research.data.models import CanonicalBar
from futures_research.paths import PROJECT_ROOT
from futures_research.platform.backtest_loop_seam import (
    backtest_loop_v1,
    reset_backtest_loop_state_for_tests,
)
from futures_research.platform.native_runtime import clear_native_registry
from datetime import time


def _contract() -> ContractSpec:
    return ContractSpec(
        contract_id="NQ-202609-CME",
        symbol="NQ",
        display_name="E-mini Nasdaq-100",
        asset_class="equity_index_futures",
        exchange="CME",
        ib_exchange="CME",
        ib_local_symbol="NQU6",
        currency="USD",
        expiry=datetime(2026, 9, 18, tzinfo=UTC).date(),
        timezone="America/Chicago",
        tick_size=0.25,
        point_value=20.0,
        execution_costs=ExecutionCostSpec(
            commission_per_side=2.5,
            slippage_ticks=SlippageTicks(
                breakout_entry=1,
                stop_exit=2,
                target_exit=0,
                day_end_exit=1,
            ),
        ),
        roll_blackout_half_window_days=1,
        sessions={
            "eth": SessionHours(start=time(17, 0), end=time(16, 0)),
            "rth": SessionHours(start=time(8, 30), end=time(15, 0)),
        },
    )


def _bars(n: int = 200) -> list[CanonicalBar]:
    start = datetime(2026, 5, 20, 18, 0, tzinfo=UTC)
    out: list[CanonicalBar] = []
    for i in range(n):
        ts = start + timedelta(minutes=i)
        # Waveform that forces dual-EMA crosses for golden stability.
        phase = (i % 40) - 20
        c = 20000.0 + phase * 2.0 + i * 0.05
        out.append(
            CanonicalBar(
                timestamp=ts,
                open=c - 0.5,
                high=c + 1.0,
                low=c - 1.0,
                close=c,
                volume=100 + i,
                contract_id="NQ-202609-CME",
                source="e2_fixture",
            )
        )
    return out


def _loop_request(bars: list[CanonicalBar], backend: str = "stable") -> BacktestLoopRequest:
    return BacktestLoopRequest.model_validate(
        {
            "schema": "backtest_loop_request.v1",
            "timeframe_minutes": 5,
            "ema_fast": 3,
            "ema_slow": 8,
            "quantity": 1,
            "point_value": 20.0,
            "commission_per_side": 2.5,
            "bars": [
                {
                    "t": b.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "o": b.open,
                    "h": b.high,
                    "l": b.low,
                    "c": b.close,
                    "v": float(b.volume),
                }
                for b in bars
            ],
            "requested_backend": backend,
        }
    )


def _manifest(contract: ContractSpec) -> RunManifest:
    start = datetime(2026, 5, 20, 18, 0, tzinfo=UTC)
    end = datetime(2026, 5, 20, 21, 20, tzinfo=UTC)
    return RunManifest(
        run_id="e2-kernel-seal-001",
        strategy_version="kernel-v1-e2",
        contract_id=contract.contract_id,
        session_name="eth",
        range_start=start,
        range_end=end,
        initial_capital=100_000.0,
        quantity=1,
        costs=contract.execution_costs,
        data_fingerprint=DataFingerprint(
            digest="e" * 64,
            bar_count=200,
            first_timestamp=start,
            end_timestamp=end,
            source_partitions=(),
            quality_report_ids=(),
        ),
        strategy_binding=None,
        quality_gate_mode="disabled",
        validation_run=False,
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_backtest_loop_state_for_tests()
    clear_native_registry()
    yield
    reset_backtest_loop_state_for_tests()
    clear_native_registry()


def _ensure_caps() -> bool:
    release = PROJECT_ROOT / "native" / "fr_compute" / "target" / "release"
    lib = release / "libfr_compute.dylib"
    if not lib.is_file():
        lib = release / "libfr_compute.so"
    if not lib.is_file():
        return False
    cap = PROJECT_ROOT / "native" / "fr_compute" / "fr_compute.capabilities"
    Path(str(lib) + ".capabilities").write_text(
        cap.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return True


def test_python_kernel_seal_is_evidence_complete() -> None:
    contract = _contract()
    bars = _bars()
    draft = compute_backtest_loop_python(_loop_request(bars, "stable"))
    result = seal_kernel_draft_to_run_result(
        draft=draft,
        manifest=_manifest(contract),
        contract=contract,
        session_name="eth",
        canonical_bars=bars,
    )
    assert result.evidence_complete is True
    assert result.evidence_summary is not None
    assert len(result.trade_records) == draft.trade_count
    for record in result.trade_records:
        assert record.decision_evidence is not None
        assert record.decision_evidence.trade_id == record.trade_id
        assert record.decision_evidence.entry.signal_kind == "inside"
        assert len(record.decision_evidence.entry.condition_facts) >= 1
        assert len(record.decision_evidence.conservative_assumptions) >= 1
    # Event log covers signal + entry + exit per trade
    assert len(result.event_log) == draft.trade_count * 3
    assert any("product_seal" in w or "backtest_loop_v1" in w for w in result.warnings)


def test_rust_and_python_kernel_golden_align_then_seal() -> None:
    if not _ensure_caps():
        pytest.skip("Rust dylib not built")
    contract = _contract()
    bars = _bars()
    py = compute_backtest_loop_python(_loop_request(bars, "stable"))
    rust = backtest_loop_v1(_loop_request(bars, "accelerated"), force_backend="accelerated")
    if rust.provenance.get("effective_backend") != "rust":
        pytest.skip(f"rust not admitted: {rust.provenance.get('fallback_reason')}")

    # Golden: draft trades align bit-for-bit on key fields.
    assert rust.trade_count == py.trade_count
    assert abs(rust.net_pnl - py.net_pnl) < 1e-9
    assert len(rust.trades) == len(py.trades)
    for rt, pt in zip(rust.trades, py.trades, strict=True):
        assert rt.entry_t == pt.entry_t
        assert rt.exit_t == pt.exit_t
        assert abs(rt.entry_price - pt.entry_price) < 1e-12
        assert abs(rt.exit_price - pt.exit_price) < 1e-12
        assert abs(rt.net_pnl - pt.net_pnl) < 1e-9

    sealed_py = seal_kernel_draft_to_run_result(
        draft=py,
        manifest=_manifest(contract),
        contract=contract,
        session_name="eth",
        canonical_bars=bars,
    )
    sealed_rust = seal_kernel_draft_to_run_result(
        draft=rust,
        manifest=_manifest(contract),
        contract=contract,
        session_name="eth",
        canonical_bars=bars,
    )
    assert sealed_py.evidence_complete and sealed_rust.evidence_complete
    assert len(sealed_py.trade_records) == len(sealed_rust.trade_records)
    for a, b in zip(sealed_py.trade_records, sealed_rust.trade_records, strict=True):
        assert a.trade_id == b.trade_id
        assert abs(a.execution.net_pnl - b.execution.net_pnl) < 1e-9
        assert a.execution.entry_price == b.execution.entry_price
        assert a.execution.exit_price == b.execution.exit_price
        assert a.decision_evidence is not None and b.decision_evidence is not None
        assert a.decision_evidence.ordinal == b.decision_evidence.ordinal
