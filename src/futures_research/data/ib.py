"""NautilusTrader Interactive Brokers historical-client boundary.

This module deliberately owns only transport and normalization. Canonical storage, quality checks,
and roll policy remain independent of IB so another data provider can later replace it.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from futures_research.data.daily_download import (
    DAILY_SOURCE,
    DailyHistoricalDownloadRequest,
    map_daily_event_to_session_start,
)
from futures_research.data.download import HistoricalDownloadRequest
from futures_research.data.models import CanonicalBar
from futures_research.paths import PROJECT_ROOT


class IbConnectionConfig(BaseModel):
    """Non-secret TWS or IB Gateway connection settings."""

    model_config = ConfigDict(frozen=True)

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    client_id: int = Field(ge=0)
    request_timeout_seconds: int = Field(default=120, ge=1)
    stabilization_seconds: float = Field(default=2.0, ge=0)

    @classmethod
    def from_environment(cls, *, env_file: Path | None = None) -> IbConnectionConfig:
        """Load required, owner-managed IB settings from a root-anchored ``.env`` file.

        Connection details are intentionally required rather than supplied with source-code
        defaults. This prevents a checked-in port (especially a live-vs-paper port) from becoming
        an accidental execution target.
        """
        load_dotenv(dotenv_path=env_file or PROJECT_ROOT / ".env", override=False)
        values = {name: os.environ.get(name, "").strip() for name in _REQUIRED_IB_ENVIRONMENT}
        missing = [name for name, value in values.items() if not value]
        if missing:
            msg = f"missing required IB environment variables: {', '.join(missing)}"
            raise ValueError(msg)
        try:
            return cls(
                host=values["IB_HOST"],
                port=int(values["IB_PORT"]),
                client_id=int(values["IB_CLIENT_ID"]),
            )
        except ValueError as exc:
            msg = "IB_PORT and IB_CLIENT_ID must be valid integers"
            raise ValueError(msg) from exc


class NautilusIbHistoricalClient:
    """Adapt NautilusTrader's historical IB client to canonical one-minute bars."""

    def __init__(self, config: IbConnectionConfig) -> None:
        self._config = config
        self._client: Any | None = None
        self._connected = False
        self._active_no_data_event: asyncio.Event | None = None
        self._active_historical_error: Exception | None = None
        self._active_request_has_no_data = False

    @property
    def is_connected(self) -> bool:
        """Expose local connection state for the owner-managed session lifecycle."""
        return self._connected

    async def connect(self) -> None:
        """Connect to an already-running TWS or Gateway instance."""
        if self._connected:
            return
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        self._client = HistoricInteractiveBrokersClient(
            host=self._config.host,
            port=self._config.port,
            client_id=self._config.client_id,
        )
        await self._client.connect()
        self._install_no_data_observer(self._client)
        if self._config.stabilization_seconds:
            await asyncio.sleep(self._config.stabilization_seconds)
        self._connected = True

    async def disconnect(self) -> None:
        """Close the TWS/Gateway session when a download batch finishes or fails."""
        try:
            if self._client is not None and self._connected:
                await self._disconnect_client(self._client)
                await self._await_scheduled_shutdown(self._client)
        finally:
            self._connected = False

    async def fetch_bars(self, request: HistoricalDownloadRequest) -> list[CanonicalBar]:
        """Request one segment through the official Nautilus IB historical client."""
        if not self._connected or self._client is None:
            msg = "IB historical client is not connected"
            raise RuntimeError(msg)

        from nautilus_trader.adapters.interactive_brokers.common import IBContract

        local_zone = ZoneInfo(request.contract.timezone)
        ib_contract = IBContract(
            secType="FUT",
            symbol=request.contract.symbol,
            exchange=request.contract.ib_exchange,
            localSymbol=request.contract.ib_local_symbol,
            currency=request.contract.currency,
            lastTradeDateOrContractMonth=request.contract.expiry.strftime("%Y%m"),
            includeExpired=True,
        )
        raw_bars = await self._request_bars_or_empty_at_historical_boundary(
            bar_specifications=["1-MINUTE-LAST"],
            start_date_time=self._to_local_naive(request.start, local_zone),
            end_date_time=self._to_local_naive(request.end, local_zone),
            tz_name=request.contract.timezone,
            contracts=[ib_contract],
            use_rth=request.use_rth,
            timeout=self._config.request_timeout_seconds,
        )
        return [
            bar
            for raw_bar in raw_bars
            if (
                bar := self._to_canonical_bar(
                    raw_bar,
                    contract_id=request.contract.contract_id,
                    request_id=request.request_id,
                )
            ).timestamp
            >= request.start
            and bar.timestamp < request.end
        ]

    async def fetch_daily_bars(self, request: DailyHistoricalDownloadRequest) -> list[CanonicalBar]:
        """Request one native daily segment and map each bar to session-open ``ts_event``.

        Additive WO-003b path: uses ``1-DAY-LAST`` only. Existing one-minute ``fetch_bars`` is
        unchanged. Canonical daily timestamps are bar-start (session open) per channel [044] Q2.
        """
        if not self._connected or self._client is None:
            msg = "IB historical client is not connected"
            raise RuntimeError(msg)

        from nautilus_trader.adapters.interactive_brokers.common import IBContract

        local_zone = ZoneInfo(request.contract.timezone)
        ib_contract = IBContract(
            secType="FUT",
            symbol=request.contract.symbol,
            exchange=request.contract.ib_exchange,
            localSymbol=request.contract.ib_local_symbol,
            currency=request.contract.currency,
            lastTradeDateOrContractMonth=request.contract.expiry.strftime("%Y%m"),
            includeExpired=True,
        )
        raw_bars = await self._request_bars_or_empty_at_historical_boundary(
            bar_specifications=["1-DAY-LAST"],
            start_date_time=self._to_local_naive(request.start, local_zone),
            end_date_time=self._to_local_naive(request.end, local_zone),
            tz_name=request.contract.timezone,
            contracts=[ib_contract],
            use_rth=request.use_rth,
            timeout=self._config.request_timeout_seconds,
        )
        bars: list[CanonicalBar] = []
        for raw_bar in raw_bars:
            bar = self._to_daily_canonical_bar(
                raw_bar,
                contract=request.contract,
                request_id=request.request_id,
                session_name=request.session_name,
            )
            if request.start <= bar.timestamp < request.end:
                bars.append(bar)
        # Existing-wins storage keys on ts_event; collapse any provider duplicate dates here.
        unique: dict[datetime, CanonicalBar] = {}
        for bar in sorted(bars, key=lambda item: item.timestamp):
            unique.setdefault(bar.timestamp, bar)
        return list(unique.values())

    async def _request_bars_or_empty_at_historical_boundary(self, **kwargs: Any) -> list[Any]:
        """Convert IB's explicit no-data response into the downloader's empty boundary.

        Nautilus 1.230 removes the internal historical request on IB error 162 but leaves its
        awaitable unresolved.  The adapter sees that response directly and returns an empty
        batch, while every other response keeps the configured request timeout and error path.
        The downloader then requires five consecutive exchange sessions before accepting the
        boundary as real provider depth.
        """
        if self._client is None:
            msg = "IB historical client is not connected"
            raise RuntimeError(msg)
        if self._active_no_data_event is not None:
            msg = "IB historical adapter does not support concurrent requests"
            raise RuntimeError(msg)

        no_data_event = asyncio.Event()
        self._active_no_data_event = no_data_event
        request_task = asyncio.create_task(self._client.request_bars(**kwargs))
        no_data_task = asyncio.create_task(no_data_event.wait())
        try:
            await asyncio.wait(
                (request_task, no_data_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if no_data_event.is_set():
                request_task.cancel()
                with suppress(asyncio.CancelledError):
                    await request_task
                if self._active_request_has_no_data:
                    return []
                if self._active_historical_error is not None:
                    raise self._active_historical_error
                msg = "IB historical request ended without a classified response"
                raise RuntimeError(msg)
            return cast(list[Any], request_task.result())
        finally:
            self._active_no_data_event = None
            self._active_historical_error = None
            self._active_request_has_no_data = False
            no_data_task.cancel()
            with suppress(asyncio.CancelledError):
                await no_data_task

    def _install_no_data_observer(self, historical_client: Any) -> None:
        """Observe the one IB error that marks a historical-availability boundary.

        This is intentionally a narrow compatibility seam around Nautilus 1.230's private
        transport: only error 162 whose provider text says there is no data is intercepted.
        Authentication, entitlement, pacing, and all other failures retain Nautilus' ordinary
        handling rather than becoming a false historical boundary.
        """
        transport = getattr(historical_client, "_client", None)
        original_handler = getattr(transport, "process_error", None)
        if transport is None or not callable(original_handler):
            msg = "Nautilus historical client exposes no IB error observer"
            raise RuntimeError(msg)

        async def observe_error(**error: Any) -> None:
            self._record_historical_error(
                error_code=error.get("error_code"),
                error_string=error.get("error_string"),
            )
            result = original_handler(**error)
            if inspect.isawaitable(result):
                await result

        transport.process_error = observe_error

    def _record_no_data_error(self, *, error_code: Any, error_string: Any) -> None:
        """Compatibility shim for the explicit no-data portion of error classification."""
        self._record_historical_error(error_code=error_code, error_string=error_string)

    def _record_historical_error(self, *, error_code: Any, error_string: Any) -> None:
        """Classify only active-request IB 162 responses before Nautilus drops their future."""
        if error_code != 162 or not isinstance(error_string, str):
            return
        if self._active_no_data_event is None:
            return
        if "no data" in error_string.lower():
            self._active_request_has_no_data = True
        else:
            self._active_historical_error = ConnectionError(
                f"IB historical request failed (162): {error_string}"
            )
        self._active_no_data_event.set()

    @staticmethod
    def _to_local_naive(value: datetime, zone: ZoneInfo) -> datetime:
        return value.astimezone(zone).replace(tzinfo=None)

    @staticmethod
    def _to_canonical_bar(raw_bar: Any, *, contract_id: str, request_id: str) -> CanonicalBar:
        return CanonicalBar(
            timestamp=NautilusIbHistoricalClient._start_timestamp(raw_bar.ts_event),
            open=NautilusIbHistoricalClient._as_float(raw_bar.open),
            high=NautilusIbHistoricalClient._as_float(raw_bar.high),
            low=NautilusIbHistoricalClient._as_float(raw_bar.low),
            close=NautilusIbHistoricalClient._as_float(raw_bar.close),
            volume=NautilusIbHistoricalClient._as_int(raw_bar.volume),
            contract_id=contract_id,
            source="IB",
            source_request_id=request_id,
        )

    @staticmethod
    def _to_daily_canonical_bar(
        raw_bar: Any,
        *,
        contract: Any,
        request_id: str,
        session_name: str,
    ) -> CanonicalBar:
        """Normalize a native daily bar onto session-open ``ts_event`` with daily source tag."""
        from futures_research.data.contracts import ContractSpec

        if not isinstance(contract, ContractSpec):
            msg = "daily canonical mapping requires a ContractSpec"
            raise TypeError(msg)
        event = NautilusIbHistoricalClient._event_timestamp(raw_bar.ts_event)
        session_start = map_daily_event_to_session_start(
            contract,
            event,
            session_name=session_name,
        )
        return CanonicalBar(
            timestamp=session_start,
            open=NautilusIbHistoricalClient._as_float(raw_bar.open),
            high=NautilusIbHistoricalClient._as_float(raw_bar.high),
            low=NautilusIbHistoricalClient._as_float(raw_bar.low),
            close=NautilusIbHistoricalClient._as_float(raw_bar.close),
            volume=NautilusIbHistoricalClient._as_int(raw_bar.volume),
            contract_id=contract.contract_id,
            source=DAILY_SOURCE,
            source_request_id=request_id,
        )

    @staticmethod
    def _event_timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                msg = "NautilusTrader returned a timezone-naive event timestamp"
                raise ValueError(msg)
            return value.astimezone(UTC)
        return datetime.fromtimestamp(int(value) / 1_000_000_000, tz=UTC)

    @staticmethod
    def _start_timestamp(value: Any) -> datetime:
        """Normalize Nautilus 1.230 historical ``ts_event`` into canonical bar-start UTC.

        The pinned Nautilus IB adapter documents its historical ``ts_event`` as the bar start and
        its ``ts_init`` as the bar end. Only ``ts_event`` therefore crosses this provider boundary;
        subtracting one minute here would incorrectly shift IB's start-labelled one-minute bars.
        """
        timestamp = NautilusIbHistoricalClient._event_timestamp(value)
        if timestamp.second or timestamp.microsecond:
            msg = "Nautilus historical one-minute ts_event is not aligned to a minute start"
            raise ValueError(msg)
        return timestamp

    @staticmethod
    def _as_float(value: Any) -> float:
        return float(str(value))

    @staticmethod
    def _as_int(value: Any) -> int:
        return int(float(str(value)))

    @staticmethod
    async def _disconnect_client(client: Any) -> None:
        """Disconnect across NautilusTrader stable and future public-client APIs.

        NautilusTrader 1.230.0 exposes only the adapter's internal disconnect coroutine. The
        preferred public ``disconnect`` path is checked first so this compatibility seam can be
        removed when a stable release supplies it.
        """
        public_disconnect = getattr(client, "disconnect", None)
        if callable(public_disconnect):
            result = public_disconnect()
            if inspect.isawaitable(result):
                await result
            return

        data_client = getattr(client, "_data_client", None)
        stable_disconnect = getattr(data_client, "_disconnect", None)
        if callable(stable_disconnect):
            result = stable_disconnect()
            if inspect.isawaitable(result):
                await result
            return

        transport = getattr(client, "_client", None)
        stop = getattr(transport, "stop", None)
        if callable(stop):
            stop()
            return

        msg = "NautilusTrader IB historical client exposes no supported disconnect path"
        raise RuntimeError(msg)

    @staticmethod
    async def _await_scheduled_shutdown(client: Any) -> None:
        """Give Nautilus 1.230's scheduled private shutdown task a bounded chance to finish.

        Its only available stable-era disconnect path schedules ``_stop_async`` instead of awaiting
        it. Waiting for the transport socket avoids ``asyncio.run`` cancelling that task at CLI
        teardown and leaves the Gateway session cleanly closed.
        """
        transport = getattr(client, "_client", None)
        socket_client = getattr(transport, "_eclient", None)
        is_connected = getattr(socket_client, "isConnected", None)
        if not callable(is_connected):
            return
        for _ in range(20):
            with suppress(Exception):
                if not is_connected():
                    await asyncio.sleep(0)
                    return
            await asyncio.sleep(0.05)


_REQUIRED_IB_ENVIRONMENT = ("IB_HOST", "IB_PORT", "IB_CLIENT_ID")
