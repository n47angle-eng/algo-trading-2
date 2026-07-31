from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from futures_research.backtest.mtf import MtfPrecomputer, Timeframe
from futures_research.backtest.strategy import TrendStrategy
from futures_research.data.contracts import ContractRegistry
from futures_research.data.models import CanonicalBar
from futures_research.paper.market_data import FormingBarUpdate
from futures_research.paper.models import ClosedMarketInput
from futures_research.paper.strategy_runtime import SharedTrendStrategyRuntime
from futures_research.paths import PROJECT_ROOT

STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
START = datetime(2026, 7, 30, 13, 30, tzinfo=UTC)


def contract():
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    return registry.contracts["NQ"]


def bars(count: int = 120) -> tuple[CanonicalBar, ...]:
    result: list[CanonicalBar] = []
    for index in range(count):
        timestamp = START + timedelta(minutes=index)
        price = 20_000 + index * 0.25
        result.append(
            CanonicalBar(
                timestamp=timestamp,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + 0.25,
                volume=100,
                contract_id="NQ-202609-CME",
                ingested_at=timestamp + timedelta(minutes=1),
            )
        )
    return tuple(result)


def closed(bar: CanonicalBar) -> ClosedMarketInput:
    return ClosedMarketInput.create(
        provider_session_id="saved-ib-session",
        contract_id=bar.contract_id,
        timeframe="1m",
        mode="replay_test",
        event_at=(bar.timestamp + timedelta(minutes=1))
        .isoformat()
        .replace("+00:00", "Z"),
        received_at=bar.ingested_at.isoformat().replace("+00:00", "Z"),
        open_price=bar.open,
        high_price=bar.high,
        low_price=bar.low,
        close_price=bar.close,
        volume=bar.volume,
        source_kind="live",
    )


def test_shared_runtime_uses_exact_mtf_and_trend_strategy_authorities() -> None:
    source_bars = bars()
    runtime = SharedTrendStrategyRuntime.from_locked_strategy(
        strategy_id="strategy-0003",
        content_sha256=STRATEGY_SHA,
        contract=contract(),
        canonical_bars=source_bars,
        activation_at=START,
    )
    direct = MtfPrecomputer(contract(), session_name="eth").precompute(source_bars)
    expected = tuple(
        snapshot.bar.ts_init
        for snapshot in direct.series[Timeframe.M5].snapshots
        if snapshot.bar.ts_init > START
    )

    assert runtime.core_type is TrendStrategy
    assert runtime.strategy_timeframes == ("D", "1H", "5m")
    assert runtime.entry_snapshot_close_times == expected


def test_closed_boundary_has_no_lookahead_and_forming_never_enters_strategy() -> None:
    source_bars = bars()
    runtime = SharedTrendStrategyRuntime.from_locked_strategy(
        strategy_id="strategy-0003",
        content_sha256=STRATEGY_SHA,
        contract=contract(),
        canonical_bars=source_bars,
        activation_at=START,
    )
    fifth = source_bars[4]

    update = runtime.process(closed(fifth))

    assert runtime.last_processed_entry_close == START + timedelta(minutes=5)
    assert update.entry_intents == ()

    forming = FormingBarUpdate(
        provider_session_id="saved-ib-session",
        contract_id="NQ-202609-CME",
        timeframe="1m",
        mode="replay_test",
        event_at="2026-07-30T13:36:00Z",
        received_at="2026-07-30T13:35:30Z",
        open_price=20_001,
        high_price=20_002,
        low_price=20_000,
        close_price=20_001,
        volume=50,
    )
    with pytest.raises(ValueError, match="forming_bar_not_decidable"):
        runtime.process(forming)

    assert runtime.last_processed_entry_close == START + timedelta(minutes=5)
