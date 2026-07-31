"""Exchange-timezone session templates used by the data-quality completeness check."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from futures_research.data.contracts import ContractSpec


class SessionDateMapper:
    """Exact timestamp↔trading-date mapper with per-date bounds cached for one scan."""

    __slots__ = ("_bounds_cache", "_contract", "_hours", "_session_name", "_zone")

    def __init__(self, contract: ContractSpec, *, session_name: str) -> None:
        try:
            hours = contract.sessions[session_name]
        except KeyError as exc:
            msg = f"unknown session '{session_name}' for {contract.contract_id}"
            raise KeyError(msg) from exc
        self._contract = contract
        self._session_name = session_name
        self._hours = hours
        self._zone = ZoneInfo(contract.timezone)
        self._bounds_cache: dict[date, tuple[datetime, datetime] | None] = {}

    def bounds_for_trading_date(
        self,
        trading_date: date,
    ) -> tuple[datetime, datetime] | None:
        """Return cached UTC bounds for one exchange-local session-end label."""
        if trading_date not in self._bounds_cache:
            self._bounds_cache[trading_date] = _session_bounds(
                trading_date,
                self._hours.start,
                self._hours.end,
                self._zone,
            )
        return self._bounds_cache[trading_date]

    def trading_date_for_timestamp(self, event: datetime) -> date:
        """Map one aware instant to exactly one exchange-local session-end label."""
        event_utc = _as_utc(event)
        local_date = event_utc.astimezone(self._zone).date()
        matches: set[date] = set()
        for candidate in (
            local_date - timedelta(days=1),
            local_date,
            local_date + timedelta(days=1),
        ):
            bounds = self.bounds_for_trading_date(candidate)
            if bounds is not None and bounds[0] <= event_utc < bounds[1]:
                matches.add(candidate)
        if len(matches) != 1:
            msg = (
                f"timestamp must map to exactly one {self._session_name} trading date for "
                f"{self._contract.contract_id}"
            )
            raise ValueError(msg)
        return next(iter(matches))

    def trading_dates_for_timestamps(
        self,
        events: list[datetime],
    ) -> tuple[date, ...]:
        """Map a scan efficiently while preserving input order and exact uniqueness."""
        if not events:
            return ()
        normalized = [_as_utc(event) for event in events]
        earliest = min(normalized).date() - timedelta(days=2)
        latest = max(normalized).date() + timedelta(days=2)
        windows: list[tuple[datetime, datetime, date]] = []
        candidate = earliest
        while candidate <= latest:
            bounds = self.bounds_for_trading_date(candidate)
            if bounds is not None:
                windows.append((bounds[0], bounds[1], candidate))
            candidate += timedelta(days=1)
        windows.sort(key=lambda item: item[0])

        mapped: list[date | None] = [None] * len(events)
        active: list[tuple[datetime, datetime, date]] = []
        window_index = 0
        for event_index, event_utc in sorted(
            enumerate(normalized),
            key=lambda item: item[1],
        ):
            while (
                window_index < len(windows)
                and windows[window_index][0] <= event_utc
            ):
                active.append(windows[window_index])
                window_index += 1
            active = [window for window in active if event_utc < window[1]]
            if len(active) != 1:
                msg = (
                    f"timestamp must map to exactly one {self._session_name} trading date for "
                    f"{self._contract.contract_id}"
                )
                raise ValueError(msg)
            mapped[event_index] = active[0][2]
        if any(value is None for value in mapped):
            msg = "session timestamp mapping did not produce one label per input"
            raise RuntimeError(msg)
        return tuple(value for value in mapped if value is not None)

    def trading_dates_for_sorted_epoch_nanoseconds(
        self,
        events: Sequence[int],
    ) -> tuple[date, ...]:
        """Map sorted Arrow timestamp integers without materializing Python datetimes."""
        projected = self.project_trading_dates_for_sorted_epoch_nanoseconds(events)
        if any(value is None for value in projected):
            msg = (
                f"timestamp must map to exactly one {self._session_name} trading date for "
                f"{self._contract.contract_id}"
            )
            raise ValueError(msg)
        return tuple(value for value in projected if value is not None)

    def project_trading_dates_for_sorted_epoch_nanoseconds(
        self,
        events: Sequence[int],
    ) -> tuple[date | None, ...]:
        """Project a wider physical session, marking out-of-session rows as ``None``."""
        if not events:
            return ()
        if any(previous > current for previous, current in zip(events, events[1:], strict=False)):
            msg = "epoch nanosecond timestamps must be sorted"
            raise ValueError(msg)

        earliest = datetime.fromtimestamp(events[0] // 1_000_000_000, tz=UTC).date()
        latest = datetime.fromtimestamp(events[-1] // 1_000_000_000, tz=UTC).date()
        windows: list[tuple[int, int, date]] = []
        candidate = earliest - timedelta(days=2)
        last_candidate = latest + timedelta(days=2)
        while candidate <= last_candidate:
            bounds = self.bounds_for_trading_date(candidate)
            if bounds is not None:
                windows.append(
                    (
                        _epoch_nanoseconds(bounds[0]),
                        _epoch_nanoseconds(bounds[1]),
                        candidate,
                    )
                )
            candidate += timedelta(days=1)
        windows.sort(key=lambda item: item[0])

        mapped: list[date | None] = []
        window_index = 0
        for event in events:
            while window_index < len(windows) and event >= windows[window_index][1]:
                window_index += 1
            if (
                window_index >= len(windows)
                or event < windows[window_index][0]
                or event >= windows[window_index][1]
            ):
                mapped.append(None)
                continue
            if (
                window_index + 1 < len(windows)
                and windows[window_index + 1][0] <= event < windows[window_index + 1][1]
            ):
                msg = (
                    f"timestamp must map to exactly one {self._session_name} trading date for "
                    f"{self._contract.contract_id}"
                )
                raise ValueError(msg)
            mapped.append(windows[window_index][2])
        return tuple(mapped)

    def trading_date_for_session_start(self, event: datetime) -> date:
        """Reverse-map an exact canonical session-start instant to its date label."""
        event_utc = _as_utc(event)
        trading_date = self.trading_date_for_timestamp(event_utc)
        bounds = self.bounds_for_trading_date(trading_date)
        if bounds is None or event_utc != bounds[0]:
            msg = (
                f"timestamp is not the exact {self._session_name} session start for "
                f"{self._contract.contract_id}"
            )
            raise ValueError(msg)
        return trading_date


def expected_minute_timestamps(
    contract: ContractSpec,
    start: datetime,
    end: datetime,
    *,
    session_name: str,
) -> set[datetime]:
    """Build UTC expected 1-minute start times from a configured weekday session template.

    The template is deliberately conservative: it understands the configured RTH/ETH local hours
    and DST, but not exchange holidays or ad-hoc early closes. Those become quality findings for
    Owner review until explicit calendar overrides are supplied.
    """
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if start_utc >= end_utc:
        msg = "start must be earlier than end"
        raise ValueError(msg)
    try:
        hours = contract.sessions[session_name]
    except KeyError as exc:
        msg = f"unknown session '{session_name}' for {contract.contract_id}"
        raise KeyError(msg) from exc

    zone = ZoneInfo(contract.timezone)
    local_start_date = start_utc.astimezone(zone).date()
    local_end_date = end_utc.astimezone(zone).date()
    expected: set[datetime] = set()

    # Include one neighboring local date because ETH begins on the previous calendar day.
    candidate = local_start_date - timedelta(days=1)
    last_candidate = local_end_date + timedelta(days=1)
    while candidate <= last_candidate:
        session_bounds = _session_bounds(candidate, hours.start, hours.end, zone)
        if session_bounds is not None:
            session_start, session_end = session_bounds
            cursor = max(session_start, start_utc)
            limit = min(session_end, end_utc)
            while cursor < limit:
                expected.add(cursor)
                cursor += timedelta(minutes=1)
        candidate += timedelta(days=1)
    return expected


def session_bounds_for_trading_date(
    contract: ContractSpec,
    trading_date: date,
    *,
    session_name: str,
) -> tuple[datetime, datetime] | None:
    """Return one configured session's UTC bounds, or ``None`` on a weekend.

    ``trading_date`` means the exchange-local date on which the session ends.
    For CME ETH this intentionally maps Monday's trading date to the Sunday
    17:00 CT open through Monday 16:00 CT close.
    """
    try:
        hours = contract.sessions[session_name]
    except KeyError as exc:
        msg = f"unknown session '{session_name}' for {contract.contract_id}"
        raise KeyError(msg) from exc
    return _session_bounds(trading_date, hours.start, hours.end, ZoneInfo(contract.timezone))


def trading_date_for_session_timestamp(
    contract: ContractSpec,
    event: datetime,
    *,
    session_name: str,
) -> date:
    """Map one aware instant to exactly one exchange-local session-end date label.

    The returned date is a label, not an instant.  Callers must not derive it with
    ``timestamp.date()`` or a fixed day offset because overnight sessions and DST
    make both approaches incorrect.
    """
    return SessionDateMapper(
        contract,
        session_name=session_name,
    ).trading_date_for_timestamp(event)


def trading_date_for_session_start(
    contract: ContractSpec,
    event: datetime,
    *,
    session_name: str,
) -> date:
    """Reverse-map an exact canonical session-start instant to its trading-date label."""
    return SessionDateMapper(
        contract,
        session_name=session_name,
    ).trading_date_for_session_start(event)


def latest_completed_trading_date(
    contract: ContractSpec,
    as_of: datetime,
    *,
    session_name: str,
) -> date:
    """Find the latest configured session that had fully closed by ``as_of``."""
    as_of_utc = _as_utc(as_of)
    zone = ZoneInfo(contract.timezone)
    candidate = as_of_utc.astimezone(zone).date() + timedelta(days=1)
    while True:
        bounds = session_bounds_for_trading_date(
            contract,
            candidate,
            session_name=session_name,
        )
        if bounds is not None and bounds[1] <= as_of_utc:
            return candidate
        candidate -= timedelta(days=1)


def _session_bounds(
    session_date: date,
    start_time: time,
    end_time: time,
    zone: ZoneInfo,
) -> tuple[datetime, datetime] | None:
    """Return UTC session bounds for a weekday, treating an overnight session by end date."""
    if session_date.weekday() > 4:
        return None
    if start_time <= end_time:
        local_start = datetime.combine(session_date, start_time, tzinfo=zone)
        local_end = datetime.combine(session_date, end_time, tzinfo=zone)
    else:
        local_start = datetime.combine(session_date - timedelta(days=1), start_time, tzinfo=zone)
        local_end = datetime.combine(session_date, end_time, tzinfo=zone)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize an aware boundary to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "session boundaries must include a timezone"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _epoch_nanoseconds(value: datetime) -> int:
    normalized = _as_utc(value)
    delta = normalized - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )
