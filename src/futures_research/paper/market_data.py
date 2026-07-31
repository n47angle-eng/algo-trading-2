"""Provider-neutral read-only market-data bus, journal, and gap policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import uuid4

from futures_research.paper.models import (
    ClosedMarketInput,
    PaperMarketMode,
    finite_number,
    require_canonical_utc,
)
from futures_research.paper.store import PaperRuntimeStore
from futures_research.paper.timeframes import timeframe_spec


class MarketDataError(RuntimeError):
    """Base market-data truth error."""


class MarketDataOrderError(MarketDataError):
    """A closed input arrived before or at an already consumed cursor."""


class MarketDataGapError(MarketDataError):
    """A blind interval cannot be proven complete and short."""


@dataclass(frozen=True, slots=True)
class ProviderSession:
    session_id: str
    host: str
    port: int
    client_id: int
    connected_at: str
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class ProviderProbe:
    contract_id: str
    provider_contract: str
    mode: Literal["live", "test_delayed"]
    market_data_capable: bool
    checked_at: str
    provider_reported_type: str = "unknown"
    market_callback_seen: bool = False


@dataclass(frozen=True, slots=True)
class MarketTopic:
    contract_id: str
    timeframe: str
    mode: PaperMarketMode

    def __post_init__(self) -> None:
        if not self.contract_id or self.contract_id != self.contract_id.strip():
            raise ValueError("contract_id must be exact non-empty text")
        timeframe_spec(self.timeframe)


@dataclass(frozen=True, slots=True)
class FormingBarUpdate:
    """Display-only update; it can never be converted implicitly to a closed input."""

    provider_session_id: str
    contract_id: str
    timeframe: str
    mode: PaperMarketMode
    event_at: str
    received_at: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    closed: Literal[False] = False

    def __post_init__(self) -> None:
        require_canonical_utc(self.event_at)
        require_canonical_utc(self.received_at)
        timeframe_spec(self.timeframe)
        for field_name in (
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
        ):
            value = finite_number(getattr(self, field_name), field_name=field_name)
            object.__setattr__(self, field_name, value)
        if self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.low_price > min(
            self.open_price,
            self.high_price,
            self.close_price,
        ):
            raise ValueError("forming low exceeds another OHLC value")
        if self.high_price < max(
            self.open_price,
            self.low_price,
            self.close_price,
        ):
            raise ValueError("forming high is below another OHLC value")


MarketUpdate = FormingBarUpdate | ClosedMarketInput
MarketCallback = Callable[[MarketUpdate], None]


class MarketDataAdapter(Protocol):
    """The complete broker-facing surface; intentionally no order/account methods."""

    def connect(self) -> ProviderSession: ...

    def disconnect(self) -> None: ...

    def probe(self, contract_id: str) -> ProviderProbe: ...

    def subscribe_bars(
        self,
        topic: MarketTopic,
        callback: MarketCallback,
    ) -> str: ...

    def unsubscribe_bars(self, subscription_id: str) -> None: ...

    def backfill_gap(
        self,
        topic: MarketTopic,
        start_at: str,
        end_at: str,
    ) -> tuple[ClosedMarketInput, ...]: ...


@dataclass(slots=True)
class _TopicSubscription:
    upstream_id: str
    callbacks: dict[str, MarketCallback]


class MarketDataBus:
    """One upstream subscription per immutable market topic."""

    def __init__(self, adapter: MarketDataAdapter) -> None:
        self._adapter = adapter
        self._topics: dict[MarketTopic, _TopicSubscription] = {}
        self._tokens: dict[str, MarketTopic] = {}
        self._failure_count = 0

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def subscribe(self, topic: MarketTopic, callback: MarketCallback) -> str:
        token = f"paper-subscriber-{uuid4().hex}"
        subscription = self._topics.get(topic)
        if subscription is None:
            upstream_id = self._adapter.subscribe_bars(
                topic,
                lambda update: self._publish(topic, update),
            )
            subscription = _TopicSubscription(upstream_id=upstream_id, callbacks={})
            self._topics[topic] = subscription
        subscription.callbacks[token] = callback
        self._tokens[token] = topic
        return token

    def unsubscribe(self, token: str) -> None:
        try:
            topic = self._tokens.pop(token)
        except KeyError as exc:
            raise KeyError(f"unknown market subscriber: {token}") from exc
        subscription = self._topics[topic]
        subscription.callbacks.pop(token)
        if not subscription.callbacks:
            self._adapter.unsubscribe_bars(subscription.upstream_id)
            self._topics.pop(topic)

    def _publish(self, topic: MarketTopic, update: MarketUpdate) -> None:
        subscription = self._topics.get(topic)
        if subscription is None:
            return
        for callback in tuple(subscription.callbacks.values()):
            try:
                callback(update)
            except Exception:
                self._failure_count += 1


@dataclass(frozen=True, slots=True)
class GapAssessment:
    missing_count: int
    auto_recoverable: bool


@dataclass(frozen=True, slots=True)
class JournalAcceptance:
    input_sequence: int
    inserted: bool
    recovered_count: int = 0


class MarketInputJournal:
    """Ordered closed-input admission with exact duplicate and blind-gap policy."""

    def __init__(self, store: PaperRuntimeStore, *, blind_minutes: int = 5) -> None:
        if blind_minutes <= 0:
            raise ValueError("blind_minutes must be positive")
        self._store = store
        self._blind_minutes = blind_minutes
        self._last_by_topic: dict[MarketTopic, ClosedMarketInput] = {}

    def classify_next(self, market_input: ClosedMarketInput) -> GapAssessment:
        topic = _topic_for(market_input)
        previous = self._last_by_topic.get(topic)
        if previous is None:
            return GapAssessment(missing_count=0, auto_recoverable=True)
        previous_at = _parse_utc(previous.event_at)
        current_at = _parse_utc(market_input.event_at)
        duration = timeframe_spec(market_input.timeframe).duration_seconds
        delta = int((current_at - previous_at).total_seconds())
        if delta < 0:
            raise MarketDataOrderError("out_of_order market input")
        if delta == 0:
            if market_input.input_id != previous.input_id:
                raise MarketDataOrderError("same timestamp has identity drift")
            return GapAssessment(missing_count=0, auto_recoverable=True)
        if delta % duration != 0:
            raise MarketDataGapError("gap_not_aligned_to_timeframe")
        missing = delta // duration - 1
        return GapAssessment(
            missing_count=missing,
            auto_recoverable=missing * duration <= self._blind_minutes * 60,
        )

    def accept_closed(self, market_input: ClosedMarketInput) -> JournalAcceptance:
        assessment = self.classify_next(market_input)
        if assessment.missing_count:
            raise MarketDataGapError("gap_recovery_required")
        sequence, inserted = self._store.append_market_input(market_input)
        self._last_by_topic[_topic_for(market_input)] = market_input
        return JournalAcceptance(sequence, inserted)

    def recover_complete_gap(
        self,
        *,
        next_input: ClosedMarketInput,
        recovered: tuple[ClosedMarketInput, ...],
    ) -> JournalAcceptance:
        topic = _topic_for(next_input)
        previous = self._last_by_topic.get(topic)
        if previous is None:
            raise MarketDataGapError("no gap cursor exists")
        assessment = self.classify_next(next_input)
        if not assessment.auto_recoverable:
            raise MarketDataGapError("blind_interval_exceeded")
        if assessment.missing_count != len(recovered):
            raise MarketDataGapError("incomplete_gap")
        duration = timeframe_spec(next_input.timeframe).duration_seconds
        expected_at = _parse_utc(previous.event_at) + timedelta(seconds=duration)
        for item in recovered:
            if (
                _topic_for(item) != topic
                or item.provider_session_id != next_input.provider_session_id
                or item.source_kind != "recovered"
                or _parse_utc(item.event_at) != expected_at
            ):
                raise MarketDataGapError("incomplete_gap_identity")
            expected_at += timedelta(seconds=duration)
        if expected_at != _parse_utc(next_input.event_at):
            raise MarketDataGapError("incomplete_gap")
        results = self._store.append_market_inputs_atomic((*recovered, next_input))
        self._last_by_topic[topic] = next_input
        final_sequence, final_inserted = results[-1]
        return JournalAcceptance(
            input_sequence=final_sequence,
            inserted=final_inserted,
            recovered_count=len(recovered),
        )


def _topic_for(market_input: ClosedMarketInput) -> MarketTopic:
    return MarketTopic(
        market_input.contract_id,
        market_input.timeframe,
        market_input.mode,
    )


def _parse_utc(value: str) -> datetime:
    require_canonical_utc(value)
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
