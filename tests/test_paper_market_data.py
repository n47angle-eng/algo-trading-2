from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from futures_research.paper.market_data import (
    FormingBarUpdate,
    MarketDataBus,
    MarketDataGapError,
    MarketDataOrderError,
    MarketTopic,
    ProviderProbe,
    ProviderSession,
)
from futures_research.paper.models import ClosedMarketInput
from futures_research.paper.store import PaperRuntimeStore


def closed(minute: int, *, source_kind: str = "live") -> ClosedMarketInput:
    event = datetime(2026, 7, 31, 1, minute, tzinfo=UTC)
    return ClosedMarketInput.create(
        provider_session_id="session-01",
        contract_id="NQ-202609-CME",
        timeframe="1m",
        mode="replay_test",
        event_at=(event + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        received_at=(event + timedelta(minutes=1, seconds=1))
        .isoformat()
        .replace("+00:00", "Z"),
        open_price=20_000 + minute,
        high_price=20_002 + minute,
        low_price=19_999 + minute,
        close_price=20_001 + minute,
        volume=100,
        source_kind=source_kind,  # type: ignore[arg-type]
    )


class FakeAdapter:
    def __init__(self) -> None:
        self.subscribe_count = 0
        self.unsubscribe_count = 0
        self.callback: Callable[[FormingBarUpdate | ClosedMarketInput], None] | None = None

    def connect(self) -> ProviderSession:
        return ProviderSession(
            session_id="session-01",
            host="127.0.0.1",
            port=7498,
            client_id=7,
            connected_at="2026-07-31T01:00:00Z",
        )

    def disconnect(self) -> None:
        return None

    def probe(self, contract_id: str) -> ProviderProbe:
        return ProviderProbe(
            contract_id=contract_id,
            provider_contract="NQU6@CME",
            mode="test_delayed",
            market_data_capable=True,
            checked_at="2026-07-31T01:00:00Z",
        )

    def subscribe_bars(
        self,
        topic: MarketTopic,
        callback: Callable[[FormingBarUpdate | ClosedMarketInput], None],
    ) -> str:
        self.subscribe_count += 1
        self.callback = callback
        return "subscription-01"

    def unsubscribe_bars(self, subscription_id: str) -> None:
        assert subscription_id == "subscription-01"
        self.unsubscribe_count += 1

    def backfill_gap(
        self,
        topic: MarketTopic,
        start_at: str,
        end_at: str,
    ) -> tuple[ClosedMarketInput, ...]:
        del topic, start_at, end_at
        return ()


@pytest.fixture
def store(tmp_path: Path) -> PaperRuntimeStore:
    value = PaperRuntimeStore(tmp_path / "paper.sqlite3")
    value.initialize()
    return value


def test_bus_uses_one_upstream_subscription_and_isolates_subscriber_failure() -> None:
    adapter = FakeAdapter()
    bus = MarketDataBus(adapter)
    topic = MarketTopic("NQ-202609-CME", "1m", "replay_test")
    observed: list[str] = []

    first = bus.subscribe(topic, lambda update: observed.append(type(update).__name__))

    def broken(update: FormingBarUpdate | ClosedMarketInput) -> None:
        del update
        raise RuntimeError("one trader failed")

    second = bus.subscribe(topic, broken)
    assert adapter.subscribe_count == 1
    assert adapter.callback is not None

    adapter.callback(closed(1))

    assert observed == ["ClosedMarketInput"]
    assert bus.failure_count == 1
    bus.unsubscribe(first)
    assert adapter.unsubscribe_count == 0
    bus.unsubscribe(second)
    assert adapter.unsubscribe_count == 1


def test_forming_update_is_display_only_and_never_enters_closed_journal(
    store: PaperRuntimeStore,
) -> None:
    update = FormingBarUpdate(
        provider_session_id="session-01",
        contract_id="NQ-202609-CME",
        timeframe="1m",
        mode="replay_test",
        event_at="2026-07-31T01:01:00Z",
        received_at="2026-07-31T01:00:30Z",
        open_price=20_000,
        high_price=20_001,
        low_price=19_999,
        close_price=20_000,
        volume=25,
    )

    assert update.closed is False
    assert store.table_count("paper_market_inputs") == 0


def test_duplicate_is_exact_once_and_out_of_order_fails_closed(
    store: PaperRuntimeStore,
) -> None:
    from futures_research.paper.market_data import MarketInputJournal

    journal = MarketInputJournal(store)
    first = journal.accept_closed(closed(1))
    duplicate = journal.accept_closed(closed(1))

    assert first.inserted is True
    assert duplicate.inserted is False
    assert store.table_count("paper_market_inputs") == 1

    with pytest.raises(MarketDataOrderError, match="out_of_order"):
        journal.accept_closed(closed(0))

    assert store.table_count("paper_market_inputs") == 1


def test_gap_classifier_allows_only_complete_short_recovery(
    store: PaperRuntimeStore,
) -> None:
    from futures_research.paper.market_data import MarketInputJournal

    journal = MarketInputJournal(store, blind_minutes=5)
    journal.accept_closed(closed(0))
    gap = journal.classify_next(closed(4))

    assert gap.missing_count == 3
    assert gap.auto_recoverable is True

    recovered = tuple(closed(index, source_kind="recovered") for index in (1, 2, 3))
    result = journal.recover_complete_gap(next_input=closed(4), recovered=recovered)
    assert result.recovered_count == 3
    assert store.table_count("paper_market_inputs") == 5


def test_long_or_incomplete_gap_trips_without_inventing_rows(
    store: PaperRuntimeStore,
) -> None:
    from futures_research.paper.market_data import MarketInputJournal

    journal = MarketInputJournal(store, blind_minutes=5)
    journal.accept_closed(closed(0))

    with pytest.raises(MarketDataGapError, match="blind_interval_exceeded"):
        journal.recover_complete_gap(next_input=closed(7), recovered=())

    with pytest.raises(MarketDataGapError, match="incomplete_gap"):
        journal.recover_complete_gap(next_input=closed(4), recovered=(closed(1),))

    assert store.table_count("paper_market_inputs") == 1
