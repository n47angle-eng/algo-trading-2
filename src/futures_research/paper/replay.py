"""OS-temp deterministic replay harness using saved real IB market bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from futures_research.api.paper_runtime import (
    PaperTraderCreateRequestV2,
    StrategyTimeframeProfileWire,
)
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    SignalKind,
    StrategyUpdate,
)
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CanonicalStore
from futures_research.paper.models import BaselineMember, ClosedMarketInput
from futures_research.paper.runtime import PaperTraderRuntime
from futures_research.paper.store import PaperRuntimeStore
from futures_research.paths import PROJECT_ROOT


@dataclass(frozen=True, slots=True)
class SavedReplayWindow:
    contract_id: str
    start_at: datetime
    end_at: datetime
    bars: tuple[CanonicalBar, ...]


@dataclass(frozen=True, slots=True)
class ReplayPathEvidence:
    path: str
    processed_input_count: int
    trade_count: int
    realized_pnl: float
    position_quantity: int
    lifecycle: str
    market_mode: str
    provider_session_id: str


class _ScheduledDecisionSource:
    def __init__(
        self,
        schedule: dict[str, Direction],
        *,
        risk_points: float = 5.0,
    ) -> None:
        self._schedule = schedule
        self._risk_points = risk_points

    def process(self, update: ClosedMarketInput) -> StrategyUpdate:
        direction = self._schedule.get(update.event_at)
        if direction is None:
            return StrategyUpdate(events=(), entry_intents=())
        close_at = _parse_utc(update.event_at)
        start_at = close_at - timedelta(minutes=1)
        stop = (
            update.open_price - self._risk_points
            if direction is Direction.LONG
            else update.open_price + self._risk_points
        )
        return StrategyUpdate(
            events=(),
            entry_intents=(
                EntryIntent(
                    direction=direction,
                    entry_reference=update.open_price,
                    stop_reference=stop,
                    signal_kind=SignalKind.INSIDE,
                    signal_timestamp=start_at - timedelta(minutes=5),
                    timestamp=start_at,
                    ts_init=start_at + timedelta(minutes=5),
                ),
            ),
        )


def load_saved_real_nq_window(
    *,
    end_at: datetime,
    days: int = 30,
) -> SavedReplayWindow:
    """Read a bounded 30-day window without writing protected market history."""
    if days <= 0 or days > 30:
        raise ValueError("saved real replay window must be between 1 and 30 days")
    if end_at.tzinfo is None or end_at.utcoffset() is None:
        raise ValueError("end_at must be timezone-aware")
    end = end_at.astimezone(UTC)
    start = end - timedelta(days=days)
    store = CanonicalStore(PROJECT_ROOT / "data" / "market")
    bars = tuple(store.read("NQ-202609-CME", start=start, end=end))
    if not bars:
        raise ValueError("saved real NQ replay window is empty")
    if any(bar.source != "IB" for bar in bars):
        raise ValueError("saved replay contains non-IB market data")
    return SavedReplayWindow(
        contract_id="NQ-202609-CME",
        start_at=start,
        end_at=end,
        bars=bars,
    )


class SavedRealReplayHarness:
    """Run four forced decision paths through production runtime/store/execution."""

    def __init__(self, *, window: SavedReplayWindow, temp_root: Path) -> None:
        if not temp_root.is_absolute():
            raise ValueError("replay temp root must be absolute")
        self._window = window
        self._temp_root = temp_root
        registry = ContractRegistry.from_yaml(
            PROJECT_ROOT / "config" / "contracts.yaml"
        )
        self._contract = registry.contracts["NQ"]

    def run_four_paths(self) -> tuple[ReplayPathEvidence, ...]:
        long_pair = _find_long_profit_pair(self._window.bars, self._contract)
        loss_pairs = _find_short_loss_pairs(
            self._window.bars,
            self._contract,
            count=8,
        )
        no_signal_bars = _first_consecutive(self._window.bars, count=20)
        return (
            self._run_path(
                path="long_profit",
                bars=long_pair,
                schedule={
                    _close_text(long_pair[0]): Direction.LONG,
                },
                request_id="0d97d7fb-e42b-47c7-b853-4a9cb5d96e5d",
            ),
            self._run_path(
                path="short_loss",
                bars=loss_pairs[0],
                schedule={
                    _close_text(loss_pairs[0][0]): Direction.SHORT,
                },
                request_id="1e35c607-eb98-4399-b411-baf3bff20510",
            ),
            self._run_path(
                path="no_signal",
                bars=no_signal_bars,
                schedule={},
                request_id="2222d50e-8f7c-4e07-a17a-6550d4bf4d30",
            ),
            self._run_path(
                path="safety_trigger",
                bars=tuple(bar for pair in loss_pairs for bar in pair),
                schedule={
                    _close_text(pair[0]): Direction.SHORT for pair in loss_pairs
                },
                request_id="a776e0b8-894c-4d4a-b287-833a4bdf268e",
                stop_when_tripped=True,
            ),
        )

    def _run_path(
        self,
        *,
        path: str,
        bars: tuple[CanonicalBar, ...],
        schedule: dict[str, Direction],
        request_id: str,
        stop_when_tripped: bool = False,
    ) -> ReplayPathEvidence:
        store = PaperRuntimeStore(self._temp_root / f"{path}.sqlite3")
        store.initialize()
        trader_id = _create_replay_trader(
            store=store,
            request_id=request_id,
            now=bars[0].timestamp,
        )
        runtime = PaperTraderRuntime(
            store=store,
            trader_id=trader_id,
            contract=self._contract,
            decision_source=_ScheduledDecisionSource(schedule),
        )
        runtime.start(now=bars[0].timestamp)
        processed = 0
        for bar in bars:
            if stop_when_tripped:
                before = runtime.snapshot()
                if before.lifecycle == "tripped" and before.position_quantity == 0:
                    break
            runtime.process(_closed_input(bar))
            processed += 1
        snapshot = runtime.snapshot()
        return ReplayPathEvidence(
            path=path,
            processed_input_count=processed,
            trade_count=snapshot.trade_count,
            realized_pnl=snapshot.realized_pnl,
            position_quantity=snapshot.position_quantity,
            lifecycle=snapshot.lifecycle,
            market_mode="replay_test",
            provider_session_id="saved-real-ib-replay",
        )


def _create_replay_trader(
    *,
    store: PaperRuntimeStore,
    request_id: str,
    now: datetime,
) -> str:
    baseline_result = b'{"schema":"replay_baseline.v1"}'
    baseline_sha = sha256(baseline_result).hexdigest()
    run_id = "replay-baseline"
    body = PaperTraderCreateRequestV2.model_validate(
        {
            "schema": "paper_trader_create_request.v2",
            "request_id": request_id,
            "selection": {
                "strategy_id": "strategy-0003",
                "content_sha256": (
                    "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
                ),
                "contract_id": "NQ-202609-CME",
                "baseline_run_id": run_id,
                "baseline_result_sha256": baseline_sha,
                "timeframes": {
                    "market_input": "1m",
                    "execution": "1m",
                    "chart_display": "30m",
                },
            },
        }
    )
    members = tuple(
        BaselineMember.from_bytes(member_path, content)
        for member_path, content in (
            ("baseline/result.json", baseline_result),
            (f"baseline/trades/{run_id}.json", b'{"trades":[]}'),
            (f"baseline/equity/{run_id}.json", b'{"equity":[]}'),
            (f"baseline/events/{run_id}.json", b'{"events":[]}'),
        )
    )
    trader, _ = store.create_trader(
        body=body,
        strategy_profile=StrategyTimeframeProfileWire.model_validate(
            {
                "bias": "D",
                "mid": "1H",
                "entry": "5m",
                "source": "strategy.v1",
                "client_override": False,
            }
        ),
        initial_capital=100_000,
        baseline_members=members,
        now=now,
    )
    return trader.trader_id


def _closed_input(bar: CanonicalBar) -> ClosedMarketInput:
    return ClosedMarketInput.create(
        provider_session_id="saved-real-ib-replay",
        contract_id=bar.contract_id,
        timeframe="1m",
        mode="replay_test",
        event_at=_close_text(bar),
        received_at=(bar.timestamp + timedelta(minutes=1, microseconds=1))
        .isoformat()
        .replace("+00:00", "Z"),
        open_price=bar.open,
        high_price=bar.high,
        low_price=bar.low,
        close_price=bar.close,
        volume=bar.volume,
        source_kind="live",
    )


def _find_long_profit_pair(
    bars: tuple[CanonicalBar, ...],
    contract: ContractSpec,
) -> tuple[CanonicalBar, CanonicalBar]:
    slippage = contract.execution_costs.slippage_ticks.breakout_entry * contract.tick_size
    for first, second in zip(bars, bars[1:], strict=False):
        if second.timestamp - first.timestamp != timedelta(minutes=1):
            continue
        entry = first.open + slippage
        stop = first.open - 5
        target = entry + (entry - stop)
        if (
            first.high >= first.open
            and first.low > stop
            and first.high < target
            and second.low > stop
            and second.high >= target
        ):
            return first, second
    raise ValueError("saved real window has no deterministic long-profit pair")


def _find_short_loss_pairs(
    bars: tuple[CanonicalBar, ...],
    contract: ContractSpec,
    *,
    count: int,
) -> tuple[tuple[CanonicalBar, CanonicalBar], ...]:
    slippage = contract.execution_costs.slippage_ticks.breakout_entry * contract.tick_size
    selected: list[tuple[CanonicalBar, CanonicalBar]] = []
    last_second: datetime | None = None
    for first, second in zip(bars, bars[1:], strict=False):
        if second.timestamp - first.timestamp != timedelta(minutes=1):
            continue
        if last_second is not None and first.timestamp <= last_second:
            continue
        entry = first.open - slippage
        stop = first.open + 5
        target = entry - (stop - entry)
        if (
            first.low <= first.open
            and first.high < stop
            and first.low > target
            and second.high >= stop
        ):
            selected.append((first, second))
            last_second = second.timestamp
            if len(selected) == count:
                return tuple(selected)
    raise ValueError("saved real window has too few deterministic short-loss pairs")


def _first_consecutive(
    bars: tuple[CanonicalBar, ...],
    *,
    count: int,
) -> tuple[CanonicalBar, ...]:
    for index in range(0, len(bars) - count + 1):
        candidate = bars[index : index + count]
        if all(
            right.timestamp - left.timestamp == timedelta(minutes=1)
            for left, right in zip(candidate, candidate[1:], strict=False)
        ):
            return candidate
    raise ValueError("saved real window has no sufficiently long consecutive slice")


def _close_text(bar: CanonicalBar) -> str:
    return (bar.timestamp + timedelta(minutes=1)).isoformat().replace(
        "+00:00",
        "Z",
    )


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
