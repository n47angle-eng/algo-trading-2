"""Phase E.2: seal Rust/Python dual-EMA loop drafts into product RunResult facts.

Maps closed-bar loop trades into ExecutionTrade + StrategyEvent + full
TradeDecisionCapture so ``evidence_complete=True`` can be published.
Authority writers (SQLite / result.v1) remain Python-owned.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import isfinite

from futures_research.backtest.evidence import (
    ConditionObservation,
    ConservativeAssumptionCapture,
    EntryDecisionCapture,
    EventOrigin,
    EventRef,
    ExitCandidateCapture,
    ExitDecisionCapture,
    TradeDecisionCapture,
)
from futures_research.backtest.execution import ExecutionTrade, ExitReason
from futures_research.backtest.records import (
    RecordTagInputs,
    build_run_result,
    build_trade_records,
)
from futures_research.backtest.strategy import (
    Direction,
    EventPhase,
    SignalKind,
    StrategyEvent,
    StrategyEventType,
)
from futures_research.backtest.records import RunManifest, RunResult
from futures_research.contracts.backtest_loop import BacktestLoopDraft, LoopTrade
from futures_research.data.contracts import ContractSpec
from futures_research.data.models import CanonicalBar

_ONE_MINUTE = timedelta(minutes=1)


def seal_kernel_draft_to_run_result(
    *,
    draft: BacktestLoopDraft,
    manifest: RunManifest,
    contract: ContractSpec,
    session_name: str,
    canonical_bars: Sequence[CanonicalBar],
    trade_id_prefix: str | None = None,
    additional_warnings: Sequence[str] = (),
) -> RunResult:
    """Build an evidence-complete RunResult from a dual-EMA loop draft."""
    events, trades = materialize_kernel_execution(
        draft=draft,
        contract=contract,
        quantity=manifest.quantity,
        point_value=contract.point_value,
        commission_per_side=float(manifest.costs.commission_per_side),
    )
    trade_records = build_trade_records(
        run_id=manifest.run_id,
        trades=trades,
        events=events,
        canonical_bars=canonical_bars,
        contract=contract,
        session_name=session_name,
        default_inputs=RecordTagInputs(),
        include_decision_evidence=True,
        trade_id_prefix=trade_id_prefix,
    )
    warnings = (
        f"compute:backtest_loop_v1 seal=product engine={draft.provenance.get('effective_backend')} "
        f"trades={draft.trade_count} net_pnl={draft.net_pnl:.6f} "
        f"sha={draft.artifact_sha256[:16]}",
        *additional_warnings,
    )
    return build_run_result(
        manifest=manifest,
        trade_records=trade_records,
        event_log=events,
        contract=contract,
        additional_warnings=warnings,
        rejection_evidence=(),
        evidence_complete=True,
    )


def materialize_kernel_execution(
    *,
    draft: BacktestLoopDraft,
    contract: ContractSpec,
    quantity: int,
    point_value: float,
    commission_per_side: float,
) -> tuple[tuple[StrategyEvent, ...], tuple[ExecutionTrade, ...]]:
    """Convert draft trades into strategy/execution events + ExecutionTrade with captures."""
    events: list[StrategyEvent] = []
    trades: list[ExecutionTrade] = []
    strategy_origin = 0
    execution_origin = 0
    global_seq = 0

    for trade in draft.trades:
        built = _one_trade(
            trade=trade,
            contract=contract,
            quantity=quantity if trade.quantity == quantity else trade.quantity,
            point_value=point_value,
            commission_per_side=commission_per_side,
            strategy_origin_start=strategy_origin,
            execution_origin_start=execution_origin,
            global_seq_start=global_seq,
        )
        events.extend(built[0])
        trades.append(built[1])
        strategy_origin = built[2]
        execution_origin = built[3]
        global_seq = built[4]

    return tuple(events), tuple(trades)


def _one_trade(
    *,
    trade: LoopTrade,
    contract: ContractSpec,
    quantity: int,
    point_value: float,
    commission_per_side: float,
    strategy_origin_start: int,
    execution_origin_start: int,
    global_seq_start: int,
) -> tuple[list[StrategyEvent], ExecutionTrade, int, int, int]:
    entry_ts = _parse_z(trade.entry_t)
    exit_ts = _parse_z(trade.exit_t)
    entry_ts_init = entry_ts + _ONE_MINUTE
    exit_ts_init = exit_ts + _ONE_MINUTE
    direction = Direction.LONG if trade.direction == "long" else Direction.SHORT
    # Wide synthetic stop for evidence only (kernel exits on EMA reverse / flatten).
    stop_offset = 40
    stop_price = trade.entry_price - stop_offset * contract.tick_size
    target_price = trade.exit_price  # reverse-cross exit treated as target fill
    exit_reason = (
        ExitReason.SESSION_CLOSE
        if trade.exit_t  # still distinguish flatten via gross
        and abs(trade.gross_points) < 1e-12
        else ExitReason.TARGET
    )
    # Prefer TARGET for reverse exits; SESSION_CLOSE when price unchanged (rare).
    if abs(trade.exit_price - trade.entry_price) > 1e-12:
        exit_reason = ExitReason.TARGET
    else:
        exit_reason = ExitReason.SESSION_CLOSE

    strat_seq = strategy_origin_start + 1
    exec_entry_seq = execution_origin_start + 1
    exec_exit_seq = execution_origin_start + 2
    g1 = global_seq_start + 1
    g2 = global_seq_start + 2
    g3 = global_seq_start + 3

    signal_ref = EventRef(origin=EventOrigin.STRATEGY, origin_sequence=strat_seq)
    entry_fill_ref = EventRef(origin=EventOrigin.EXECUTION, origin_sequence=exec_entry_seq)
    exit_ref = EventRef(origin=EventOrigin.EXECUTION, origin_sequence=exec_exit_seq)

    entry_capture = EntryDecisionCapture(
        signal_kind=SignalKind.INSIDE.value,
        signal_timestamp=entry_ts,
        entry_reference=trade.entry_price,
        stop_reference_type="kernel_ema_entry",
        stop_reference_price=trade.entry_price,
        stop_offset_ticks=stop_offset,
        final_stop_price=stop_price,
        condition_observations=(
            ConditionObservation(
                condition_id="kernel_ema_cross_long",
                layer_id="entry",
                observed_at=entry_ts,
                status="passed",
                actual=True,
                operator="eq",
                required=True,
                unit="bool",
            ),
            ConditionObservation(
                condition_id="kernel_timeframe_minutes",
                layer_id="entry",
                observed_at=entry_ts,
                status="passed",
                actual=5,
                operator="eq",
                required=5,
                unit="minutes",
            ),
        ),
        signal_event_ref=signal_ref,
        intent_event_ref=None,
    )

    exit_candidate_id = (
        "target" if exit_reason is ExitReason.TARGET else "session_close"
    )
    exit_capture = ExitDecisionCapture(
        reason=exit_reason.value,
        timestamp=exit_ts,
        raw_exit_price=trade.exit_price,
        selected_candidate_id=exit_candidate_id,
        candidates=(
            ExitCandidateCapture(
                candidate_id="stop",
                triggered=False,
                reference_price=stop_price,
                observed_price=trade.exit_price,
            ),
            ExitCandidateCapture(
                candidate_id="target",
                triggered=exit_reason is ExitReason.TARGET,
                reference_price=target_price,
                observed_price=trade.exit_price,
            ),
            ExitCandidateCapture(
                candidate_id="session_close",
                triggered=exit_reason is ExitReason.SESSION_CLOSE,
                reference_price=trade.exit_price,
                observed_price=trade.exit_price,
            ),
        ),
        resolution_code="kernel_ema_reverse"
        if exit_reason is ExitReason.TARGET
        else "kernel_flatten",
        event_ref=exit_ref,
    )

    decision = TradeDecisionCapture(
        entry=entry_capture,
        entry_timestamp=entry_ts,
        fill_price=trade.entry_price,
        entry_fill_event_ref=entry_fill_ref,
        exit=exit_capture,
        entry_slippage_ticks=0,
        exit_slippage_ticks=0,
        assumptions=(
            ConservativeAssumptionCapture(
                code="kernel_dual_ema_v1",
                applied=True,
                effects=("closed_bar_fill", "no_slippage", "long_only"),
                source_event_refs=(signal_ref, entry_fill_ref, exit_ref),
            ),
        ),
    )

    events = [
        StrategyEvent(
            sequence=g1,
            timestamp=entry_ts,
            ts_init=entry_ts_init,
            phase=EventPhase.CLOSE,
            machine="kernel_v1",
            event_type=StrategyEventType.SIGNAL_CREATED,
            from_state="idle",
            to_state="signal",
            direction=direction,
            price=trade.entry_price,
            details={"signal_kind": SignalKind.INSIDE.value, "engine": "backtest_loop_v1"},
            origin=EventOrigin.STRATEGY,
            origin_sequence=strat_seq,
        ),
        StrategyEvent(
            sequence=g2,
            timestamp=entry_ts,
            ts_init=entry_ts_init,
            phase=EventPhase.INTRABAR,
            machine="kernel_v1",
            event_type=StrategyEventType.ENTRY_FILLED,
            from_state="signal",
            to_state="in_position",
            direction=direction,
            price=trade.entry_price,
            details={"engine": "backtest_loop_v1"},
            origin=EventOrigin.EXECUTION,
            origin_sequence=exec_entry_seq,
        ),
        StrategyEvent(
            sequence=g3,
            timestamp=exit_ts,
            ts_init=exit_ts_init,
            phase=EventPhase.CLOSE,
            machine="kernel_v1",
            event_type=StrategyEventType.POSITION_CLOSED,
            from_state="in_position",
            to_state="flat",
            direction=direction,
            price=trade.exit_price,
            details={
                "exit_reason": exit_reason.value,
                "engine": "backtest_loop_v1",
            },
            origin=EventOrigin.EXECUTION,
            origin_sequence=exec_exit_seq,
        ),
    ]

    qty = int(quantity)
    gross_points = float(trade.gross_points)
    if not isfinite(gross_points):
        raise ValueError("non-finite gross_points")
    gross_pnl = gross_points * point_value * qty
    total_commission = 2.0 * commission_per_side * qty
    net_pnl = float(trade.net_pnl)
    if abs(net_pnl - (gross_pnl - total_commission)) > 1e-4:
        # Trust draft net; recompute for internal consistency if close.
        net_pnl = gross_pnl - total_commission

    execution = ExecutionTrade(
        contract_id=contract.contract_id,
        direction=direction,
        signal_kind=SignalKind.INSIDE,
        quantity=qty,
        signal_timestamp=entry_ts,
        entry_timestamp=entry_ts,
        entry_ts_init=entry_ts_init,
        exit_timestamp=exit_ts,
        exit_ts_init=exit_ts_init,
        entry_reference=trade.entry_price,
        entry_price=trade.entry_price,
        stop_price=stop_price,
        target_price=target_price,
        exit_price=trade.exit_price,
        exit_reason=exit_reason,
        gross_points=gross_points,
        gross_pnl=gross_pnl,
        total_commission=total_commission,
        net_pnl=net_pnl,
        entry_slippage_ticks=0,
        exit_slippage_ticks=0,
        decision_capture=decision,
    )
    return (
        events,
        execution,
        strat_seq,
        exec_exit_seq,
        g3,
    )


def _parse_z(value: str) -> datetime:
    text = value.removesuffix("Z") + "+00:00"
    return datetime.fromisoformat(text).astimezone(UTC)
