"""Thin closed-input adapter around the shared conservative execution core."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from futures_research.backtest.execution import (
    ConservativeExecution,
    ExecutionPosition,
    ExecutionTrade,
    ExecutionUpdate,
)
from futures_research.backtest.strategy import EntryIntent, StrategyEvent
from futures_research.data.contracts import ContractSpec, ExecutionCostSpec
from futures_research.data.models import CanonicalBar
from futures_research.paper.market_data import FormingBarUpdate
from futures_research.paper.models import ClosedMarketInput
from futures_research.paper.timeframes import timeframe_spec

_MVP_EXECUTION_TIMEFRAME: Final = "1m"


class ConservativeExecutionRuntime:
    """Runtime boundary that keeps the shared fill/OCO/cost implementation authoritative."""

    def __init__(
        self,
        contract: ContractSpec,
        *,
        quantity: int,
        costs: ExecutionCostSpec | None = None,
    ) -> None:
        self._core = ConservativeExecution(
            contract,
            quantity=quantity,
            costs=costs,
        )

    @property
    def core_type(self) -> type[ConservativeExecution]:
        return type(self._core)

    @property
    def pending_intent(self) -> EntryIntent | None:
        return self._core.pending_intent

    @property
    def position(self) -> ExecutionPosition | None:
        return self._core.position

    @property
    def event_log(self) -> tuple[StrategyEvent, ...]:
        return self._core.event_log

    @property
    def trades(self) -> tuple[ExecutionTrade, ...]:
        return self._core.trades

    def arm(self, intent: EntryIntent) -> None:
        self._core.arm(intent)

    def process(
        self,
        update: ClosedMarketInput | FormingBarUpdate,
        *,
        invalidation_reason: str | None = None,
    ) -> ExecutionUpdate:
        if isinstance(update, FormingBarUpdate):
            raise ValueError("forming_bar_not_executable")
        if update.timeframe != _MVP_EXECUTION_TIMEFRAME:
            raise ValueError("unsupported_timeframe: execution requires enabled 1m")
        return self._core.process_minute(
            canonical_bar_from_closed_input(update),
            invalidation_reason=invalidation_reason,
        )

    def flatten_at_trusted_input(
        self,
        market_input: ClosedMarketInput,
    ) -> ExecutionUpdate:
        """Use the shared session-close path for an explicit trusted-price flatten."""
        bar = canonical_bar_from_closed_input(market_input)
        if self._core.event_log:
            try:
                return self._core.end_session(bar)
            except ValueError:
                self._core.process_minute(bar)
                return self._core.end_session(bar)
        self._core.process_minute(bar)
        return self._core.end_session(bar)


def canonical_bar_from_closed_input(market_input: ClosedMarketInput) -> CanonicalBar:
    """Map a close-labelled provider input onto the start-labelled shared bar."""
    duration = timeframe_spec(market_input.timeframe).duration_seconds
    close_at = _parse_utc(market_input.event_at)
    received_at = _parse_utc(market_input.received_at)
    if not market_input.volume.is_integer():
        raise ValueError("canonical futures volume must be an integer")
    return CanonicalBar(
        timestamp=close_at - timedelta(seconds=duration),
        open=market_input.open_price,
        high=market_input.high_price,
        low=market_input.low_price,
        close=market_input.close_price,
        volume=int(market_input.volume),
        contract_id=market_input.contract_id,
        source="IB",
        source_request_id=market_input.provider_session_id,
        ingested_at=received_at,
    )


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
