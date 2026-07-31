"""IBKR Gateway read-only market-data transport for the local paper runtime."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.paper.market_data import (
    FormingBarUpdate,
    MarketCallback,
    MarketTopic,
    ProviderProbe,
    ProviderSession,
)
from futures_research.paper.models import ClosedMarketInput, canonical_utc

IbProviderDataType = Literal["live", "frozen", "delayed", "delayed_frozen"]


@dataclass(frozen=True, slots=True)
class IbGatewaySettings:
    host: str = "127.0.0.1"
    port: int = 7498
    client_id: int = 7
    timeout_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1":
            raise ValueError("IB Gateway host must be exact 127.0.0.1")
        if self.port != 7498:
            raise ValueError("IB Gateway port must be exact 7498")
        if self.client_id != 7:
            raise ValueError("IB Gateway client ID must be exact 7")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 30:
            raise ValueError("IB Gateway timeout must be bounded")

    @classmethod
    def from_env(cls) -> IbGatewaySettings:
        try:
            port = int(os.environ.get("IB_PORT", "7498"))
            client_id = int(os.environ.get("IB_CLIENT_ID", "7"))
        except ValueError as exc:
            raise ValueError("IB Gateway port/client ID must be integers") from exc
        return cls(
            host=os.environ.get("IB_HOST", "127.0.0.1"),
            port=port,
            client_id=client_id,
        )


@dataclass(frozen=True, slots=True)
class IbTransportProbe:
    local_symbol: str
    exchange: str
    currency: str
    con_id: int
    provider_data_type: IbProviderDataType
    market_callback_seen: bool


@dataclass(frozen=True, slots=True)
class IbTransportBar:
    start_at: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    complete: bool


class IbMarketDataTransport(Protocol):
    def connect(
        self,
        host: str,
        port: int,
        client_id: int,
        timeout: float,
    ) -> None: ...

    def disconnect(self) -> None: ...

    def probe_contract(
        self,
        *,
        request_id: int,
        local_symbol: str,
        exchange: str,
        currency: str,
        requested_mode: str,
        timeout: float,
    ) -> IbTransportProbe: ...

    def subscribe_bars(
        self,
        *,
        request_id: int,
        local_symbol: str,
        exchange: str,
        currency: str,
        requested_mode: str,
        on_bar: Callable[[IbTransportBar], None],
        on_mode: Callable[[str], None],
    ) -> None: ...

    def unsubscribe_bars(self, request_id: int) -> None: ...

    def backfill_gap(
        self,
        *,
        request_id: int,
        local_symbol: str,
        exchange: str,
        currency: str,
        start_at: str,
        end_at: str,
        timeout: float,
    ) -> tuple[IbTransportBar, ...]: ...


@dataclass(frozen=True, slots=True)
class IbTransportAudit:
    market_requests: int
    order_requests: Literal[0] = 0


class IbMarketDataAdapter:
    """Capability-limited Gateway adapter exposing the six data operations only."""

    def __init__(
        self,
        *,
        settings: IbGatewaySettings,
        registry: ContractRegistry,
        transport: IbMarketDataTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        default_requested_mode: Literal["live", "test_delayed"] = "test_delayed",
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._transport = transport or _OfficialIbMarketDataTransport(clock=clock)
        self._clock = clock
        self._default_requested_mode = default_requested_mode
        self._session: ProviderSession | None = None
        self._next_request_id = 1_000
        self._subscriptions: dict[str, tuple[int, MarketTopic]] = {}
        self._reported_modes: dict[int, IbProviderDataType] = {}
        self._market_requests = 0

    @property
    def transport_audit(self) -> IbTransportAudit:
        return IbTransportAudit(market_requests=self._market_requests)

    def connect(self) -> ProviderSession:
        if self._session is not None:
            return self._session
        self._transport.connect(
            self._settings.host,
            self._settings.port,
            self._settings.client_id,
            self._settings.timeout_seconds,
        )
        connected_at = canonical_utc(self._clock())
        self._session = ProviderSession(
            session_id=f"ib-session-{uuid4().hex}",
            host=self._settings.host,
            port=self._settings.port,
            client_id=self._settings.client_id,
            connected_at=connected_at,
            read_only=True,
        )
        return self._session

    def disconnect(self) -> None:
        for subscription_id in tuple(self._subscriptions):
            self.unsubscribe_bars(subscription_id)
        self._transport.disconnect()
        self._session = None
        self._reported_modes.clear()

    def probe(self, contract_id: str) -> ProviderProbe:
        self._require_session()
        contract = self._contract(contract_id)
        request_id = self._allocate_request_id()
        self._market_requests += 1
        result = self._transport.probe_contract(
            request_id=request_id,
            local_symbol=contract.ib_local_symbol,
            exchange=contract.ib_exchange,
            currency=contract.currency,
            requested_mode=self._default_requested_mode,
            timeout=self._settings.timeout_seconds,
        )
        self._require_contract_match(contract, result)
        mode = _public_mode(result.provider_data_type)
        return ProviderProbe(
            contract_id=contract.contract_id,
            provider_contract=f"{result.local_symbol}@{result.exchange}",
            mode=mode,
            market_data_capable=result.market_callback_seen,
            checked_at=canonical_utc(self._clock()),
            provider_reported_type=result.provider_data_type,
            market_callback_seen=result.market_callback_seen,
        )

    def subscribe_bars(
        self,
        topic: MarketTopic,
        callback: MarketCallback,
    ) -> str:
        session = self._require_session()
        if topic.timeframe != "1m":
            raise ValueError("unsupported_timeframe: IB adapter supports enabled 1m")
        if topic.mode == "replay_test":
            raise ValueError("replay_test cannot subscribe to Gateway")
        contract = self._contract(topic.contract_id)
        request_id = self._allocate_request_id()
        subscription_id = f"ib-bars-{request_id}"
        self._subscriptions[subscription_id] = (request_id, topic)
        self._market_requests += 1

        def on_mode(value: str) -> None:
            self._reported_modes[request_id] = _provider_type(value)

        def on_bar(value: IbTransportBar) -> None:
            provider_type = self._reported_modes.get(request_id)
            if provider_type is None:
                return
            mode = _public_mode(provider_type)
            received_at = canonical_utc(self._clock())
            close_at = canonical_utc(
                _parse_utc(value.start_at) + timedelta(minutes=1)
            )
            if value.complete:
                callback(
                    ClosedMarketInput.create(
                        provider_session_id=session.session_id,
                        contract_id=contract.contract_id,
                        timeframe=topic.timeframe,
                        mode=mode,
                        event_at=close_at,
                        received_at=received_at,
                        open_price=value.open_price,
                        high_price=value.high_price,
                        low_price=value.low_price,
                        close_price=value.close_price,
                        volume=value.volume,
                        source_kind="live",
                    )
                )
            else:
                callback(
                    FormingBarUpdate(
                        provider_session_id=session.session_id,
                        contract_id=contract.contract_id,
                        timeframe=topic.timeframe,
                        mode=mode,
                        event_at=close_at,
                        received_at=received_at,
                        open_price=value.open_price,
                        high_price=value.high_price,
                        low_price=value.low_price,
                        close_price=value.close_price,
                        volume=value.volume,
                    )
                )

        self._transport.subscribe_bars(
            request_id=request_id,
            local_symbol=contract.ib_local_symbol,
            exchange=contract.ib_exchange,
            currency=contract.currency,
            requested_mode=topic.mode,
            on_bar=on_bar,
            on_mode=on_mode,
        )
        return subscription_id

    def unsubscribe_bars(self, subscription_id: str) -> None:
        try:
            request_id, _topic = self._subscriptions.pop(subscription_id)
        except KeyError as exc:
            raise KeyError(f"unknown IB bar subscription: {subscription_id}") from exc
        self._transport.unsubscribe_bars(request_id)
        self._reported_modes.pop(request_id, None)

    def backfill_gap(
        self,
        topic: MarketTopic,
        start_at: str,
        end_at: str,
    ) -> tuple[ClosedMarketInput, ...]:
        session = self._require_session()
        if topic.timeframe != "1m" or topic.mode == "replay_test":
            raise ValueError("unsupported Gateway backfill topic")
        contract = self._contract(topic.contract_id)
        request_id = self._allocate_request_id()
        self._market_requests += 1
        bars = self._transport.backfill_gap(
            request_id=request_id,
            local_symbol=contract.ib_local_symbol,
            exchange=contract.ib_exchange,
            currency=contract.currency,
            start_at=start_at,
            end_at=end_at,
            timeout=self._settings.timeout_seconds,
        )
        received_at = canonical_utc(self._clock())
        return tuple(
            ClosedMarketInput.create(
                provider_session_id=session.session_id,
                contract_id=contract.contract_id,
                timeframe=topic.timeframe,
                mode=topic.mode,
                event_at=canonical_utc(
                    _parse_utc(bar.start_at) + timedelta(minutes=1)
                ),
                received_at=received_at,
                open_price=bar.open_price,
                high_price=bar.high_price,
                low_price=bar.low_price,
                close_price=bar.close_price,
                volume=bar.volume,
                source_kind="recovered",
            )
            for bar in bars
            if bar.complete
        )

    def _require_session(self) -> ProviderSession:
        if self._session is None:
            raise RuntimeError("IB Gateway market-data session is not connected")
        return self._session

    def _contract(self, contract_id: str) -> ContractSpec:
        for contract in self._registry.contracts.values():
            if contract.contract_id == contract_id:
                return contract
        raise KeyError(f"unknown configured contract: {contract_id}")

    def _allocate_request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    @staticmethod
    def _require_contract_match(
        contract: ContractSpec,
        probe: IbTransportProbe,
    ) -> None:
        if (
            probe.local_symbol != contract.ib_local_symbol
            or probe.exchange != contract.ib_exchange
            or probe.currency != contract.currency
            or probe.con_id <= 0
        ):
            raise RuntimeError("IB contract identity mismatch")


class _OfficialIbCallbacks:
    """State holder installed onto the official API wrapper callbacks."""

    def __init__(self, *, clock: Callable[[], datetime]) -> None:
        self.clock = clock
        self.connected = threading.Event()
        self.contract_events: dict[int, threading.Event] = {}
        self.contract_results: dict[int, list[Any]] = {}
        self.mode_events: dict[int, threading.Event] = {}
        self.modes: dict[int, int] = {}
        self.market_callbacks: set[int] = set()
        self.bar_callbacks: dict[int, Callable[[IbTransportBar], None]] = {}
        self.mode_callbacks: dict[int, Callable[[str], None]] = {}
        self.backfill_events: dict[int, threading.Event] = {}
        self.backfill_rows: dict[int, list[IbTransportBar]] = {}
        self.errors: dict[int, str] = {}


class _OfficialIbMarketDataTransport:
    """Small composition wrapper over the official API's data requests."""

    def __init__(self, *, clock: Callable[[], datetime]) -> None:
        self._state = _OfficialIbCallbacks(clock=clock)
        self._client: Any | None = None
        self._thread: threading.Thread | None = None

    def connect(
        self,
        host: str,
        port: int,
        client_id: int,
        timeout: float,
    ) -> None:
        from ibapi.client import EClient  # type: ignore[import-untyped]
        from ibapi.wrapper import EWrapper  # type: ignore[import-untyped]

        state = self._state

        class Callbacks(EWrapper):  # type: ignore[misc]
            def nextValidId(self, value: int) -> None:  # noqa: N802
                del value
                state.connected.set()

            def error(self, reqId: int, *args: object) -> None:  # noqa: N802
                errorCode, errorString = _parse_official_error(args)
                if errorCode in {
                    2104,
                    2106,
                    2107,
                    2108,
                    2158,
                }:
                    return
                state.errors[reqId] = f"{errorCode}: {errorString}"
                event = state.contract_events.get(reqId)
                if event is not None:
                    event.set()
                event = state.mode_events.get(reqId)
                if event is not None:
                    event.set()
                event = state.backfill_events.get(reqId)
                if event is not None:
                    event.set()

            def contractDetails(self, reqId: int, details: Any) -> None:  # noqa: N802
                state.contract_results.setdefault(reqId, []).append(details)

            def contractDetailsEnd(self, reqId: int) -> None:  # noqa: N802
                state.contract_events[reqId].set()

            def marketDataType(self, reqId: int, marketDataType: int) -> None:  # noqa: N802
                state.modes[reqId] = marketDataType
                state.market_callbacks.add(reqId)
                callback = state.mode_callbacks.get(reqId)
                if callback is not None:
                    callback(_provider_type_from_code(marketDataType))
                event = state.mode_events.get(reqId)
                if event is not None:
                    event.set()

            def tickPrice(  # noqa: N802
                self,
                reqId: int,
                tickType: int,
                price: float,
                attrib: object,
            ) -> None:
                del tickType, price, attrib
                state.market_callbacks.add(reqId)

            def tickSize(self, reqId: int, tickType: int, size: Decimal) -> None:  # noqa: N802
                del tickType, size
                state.market_callbacks.add(reqId)

            def historicalData(self, reqId: int, bar: Any) -> None:  # noqa: N802
                value = _transport_bar_from_official(
                    bar,
                    now=state.clock(),
                )
                callback = state.bar_callbacks.get(reqId)
                if callback is not None:
                    callback(value)
                if reqId in state.backfill_rows:
                    state.backfill_rows[reqId].append(value)

            def historicalDataUpdate(self, reqId: int, bar: Any) -> None:  # noqa: N802
                callback = state.bar_callbacks.get(reqId)
                if callback is not None:
                    callback(
                        _transport_bar_from_official(
                            bar,
                            now=state.clock(),
                        )
                    )

            def historicalDataEnd(  # noqa: N802
                self,
                reqId: int,
                start: str,
                end: str,
            ) -> None:
                del start, end
                event = state.backfill_events.get(reqId)
                if event is not None:
                    event.set()

        wrapper = Callbacks()
        client = EClient(wrapper)
        self._client = client
        client.connect(host, port, client_id)
        self._thread = threading.Thread(
            target=client.run,
            name=f"paper-ib-market-{client_id}",
            daemon=True,
        )
        self._thread.start()
        if not state.connected.wait(timeout):
            client.disconnect()
            raise TimeoutError("IB Gateway handshake timed out")

    def disconnect(self) -> None:
        client = self._require_client()
        client.disconnect()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._client = None
        self._thread = None

    def probe_contract(
        self,
        *,
        request_id: int,
        local_symbol: str,
        exchange: str,
        currency: str,
        requested_mode: str,
        timeout: float,
    ) -> IbTransportProbe:
        client = self._require_client()
        state = self._state
        contract = _official_contract(local_symbol, exchange, currency)
        state.contract_events[request_id] = threading.Event()
        state.contract_results[request_id] = []
        client.reqContractDetails(request_id, contract)
        if not state.contract_events[request_id].wait(timeout):
            raise TimeoutError("IB contract resolution timed out")
        _raise_request_error(state, request_id)
        results = state.contract_results[request_id]
        if len(results) != 1:
            raise RuntimeError("IB contract resolution was not exact")
        resolved = results[0].contract
        state.mode_events[request_id] = threading.Event()
        client.reqMarketDataType(1 if requested_mode == "live" else 3)
        client.reqMktData(request_id, resolved, "", False, False, [])
        if not state.mode_events[request_id].wait(timeout):
            client.cancelMktData(request_id)
            raise TimeoutError("IB provider data mode callback timed out")
        client.cancelMktData(request_id)
        _raise_request_error(state, request_id)
        return IbTransportProbe(
            local_symbol=str(resolved.localSymbol),
            exchange=str(resolved.exchange),
            currency=str(resolved.currency),
            con_id=int(resolved.conId),
            provider_data_type=_provider_type_from_code(state.modes[request_id]),
            market_callback_seen=request_id in state.market_callbacks,
        )

    def subscribe_bars(
        self,
        *,
        request_id: int,
        local_symbol: str,
        exchange: str,
        currency: str,
        requested_mode: str,
        on_bar: Callable[[IbTransportBar], None],
        on_mode: Callable[[str], None],
    ) -> None:
        client = self._require_client()
        state = self._state
        state.bar_callbacks[request_id] = on_bar
        state.mode_callbacks[request_id] = on_mode
        client.reqMarketDataType(1 if requested_mode == "live" else 3)
        on_mode("live" if requested_mode == "live" else "delayed")
        client.reqHistoricalData(
            request_id,
            _official_contract(local_symbol, exchange, currency),
            "",
            "2 D",
            "1 min",
            "TRADES",
            0,
            2,
            True,
            [],
        )

    def unsubscribe_bars(self, request_id: int) -> None:
        client = self._require_client()
        client.cancelHistoricalData(request_id)
        self._state.bar_callbacks.pop(request_id, None)
        self._state.mode_callbacks.pop(request_id, None)

    def backfill_gap(
        self,
        *,
        request_id: int,
        local_symbol: str,
        exchange: str,
        currency: str,
        start_at: str,
        end_at: str,
        timeout: float,
    ) -> tuple[IbTransportBar, ...]:
        del start_at
        client = self._require_client()
        state = self._state
        state.backfill_events[request_id] = threading.Event()
        state.backfill_rows[request_id] = []
        end = _parse_utc(end_at)
        client.reqHistoricalData(
            request_id,
            _official_contract(local_symbol, exchange, currency),
            end.strftime("%Y%m%d-%H:%M:%S"),
            "600 S",
            "1 min",
            "TRADES",
            0,
            2,
            False,
            [],
        )
        if not state.backfill_events[request_id].wait(timeout):
            client.cancelHistoricalData(request_id)
            raise TimeoutError("IB market gap backfill timed out")
        _raise_request_error(state, request_id)
        return tuple(state.backfill_rows.pop(request_id))

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("IB market-data transport is not connected")
        return self._client


def _official_contract(local_symbol: str, exchange: str, currency: str) -> Any:
    from ibapi.contract import Contract  # type: ignore[import-untyped]

    contract = Contract()
    contract.secType = "FUT"
    contract.localSymbol = local_symbol
    contract.exchange = exchange
    contract.currency = currency
    return contract


def _transport_bar_from_official(bar: Any, *, now: datetime) -> IbTransportBar:
    start = _official_bar_start(str(bar.date))
    complete = start + timedelta(minutes=1, seconds=1) <= now.astimezone(UTC)
    return IbTransportBar(
        start_at=canonical_utc(start),
        open_price=float(bar.open),
        high_price=float(bar.high),
        low_price=float(bar.low),
        close_price=float(bar.close),
        volume=float(bar.volume),
        complete=complete,
    )


def _official_bar_start(value: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except ValueError:
        parsed = datetime.strptime(value.strip(), "%Y%m%d  %H:%M:%S")
        return parsed.replace(tzinfo=UTC)


def _provider_type(value: str) -> IbProviderDataType:
    if value not in ("live", "frozen", "delayed", "delayed_frozen"):
        raise RuntimeError(f"unknown IB provider data type: {value}")
    return cast(IbProviderDataType, value)


def _provider_type_from_code(value: int) -> IbProviderDataType:
    mapping: dict[int, IbProviderDataType] = {
        1: "live",
        2: "frozen",
        3: "delayed",
        4: "delayed_frozen",
    }
    try:
        return mapping[value]
    except KeyError as exc:
        raise RuntimeError(f"unknown IB provider data type code: {value}") from exc


def _public_mode(
    value: IbProviderDataType,
) -> Literal["live", "test_delayed"]:
    return "live" if value == "live" else "test_delayed"


def _raise_request_error(state: _OfficialIbCallbacks, request_id: int) -> None:
    error = state.errors.pop(request_id, None)
    if error is not None:
        raise RuntimeError(f"IB market-data request failed: {error}")


def _parse_official_error(args: tuple[object, ...]) -> tuple[int, str]:
    """Accept both legacy and current official API error callback signatures."""
    if (
        len(args) >= 3
        and isinstance(args[0], int)
        and isinstance(args[1], int)
        and isinstance(args[2], str)
    ):
        return args[1], args[2]
    if len(args) >= 2 and isinstance(args[0], int) and isinstance(args[1], str):
        return args[0], args[1]
    raise RuntimeError("IB API returned an unknown error callback signature")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
