"""Phase E.3: Rust TrendStrategy seam — bit-exact dual-backend for golden + product.

Native path uses ``fr_trend_strategy_*`` C ABI when the dylib exposes them.
If native is missing or diverges, callers fall back to Python ``TrendStrategy``.
"""

from __future__ import annotations

import ctypes
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping

from futures_research.backtest.evidence import (
    ConditionObservation,
    EntryDecisionCapture,
    EventOrigin,
    EventRef,
    RejectionCapture,
    SignalEvaluationContext,
)
from futures_research.backtest.mtf import AggregatedBar, IndicatorSnapshot, Timeframe
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    EventPhase,
    PendingSignal,
    PullbackState,
    Regime,
    RegimeDecision,
    SignalKind,
    StrategyEvent,
    StrategyEventType,
    StrategySpec,
    StrategyUpdate,
    TrendStrategy,
)
from futures_research.paths import PROJECT_ROOT
from futures_research.platform.chart_compute_seam import _find_lib
from futures_research.platform.native_runtime import (
    NativeAdmissionError,
    admit_native_library,
)

_REQUIRED = (
    "chart_compute_v1",
    "backtest_loop_v1",
    "trend_strategy_v1",
    "writes_authority=false",
)


def rust_trend_available() -> bool:
    lib_path = _find_lib()
    if lib_path is None:
        return False
    try:
        from pathlib import Path

        cap = PROJECT_ROOT / "native" / "fr_compute" / "fr_compute.capabilities"
        side = Path(str(lib_path) + ".capabilities")
        if not side.is_file() and cap.is_file():
            side.write_text(cap.read_text(encoding="utf-8"), encoding="utf-8")
        text = side.read_text(encoding="utf-8") if side.is_file() else ""
        if "trend_strategy_v1" not in text and cap.is_file():
            # Require capability to be declared before use.
            text = cap.read_text(encoding="utf-8")
        if "trend_strategy_v1" not in text:
            return False
        admit_native_library(
            lib_path,
            module_key="fr_compute",
            required_capabilities=_REQUIRED,
            admit_root=PROJECT_ROOT / "data" / "native" / "admitted",
        )
        # Symbol must exist.
        admitted = admit_native_library(
            lib_path,
            module_key="fr_compute",
            required_capabilities=_REQUIRED,
            admit_root=PROJECT_ROOT / "data" / "native" / "admitted",
        )
        return hasattr(admitted.lib, "fr_trend_strategy_new")
    except (NativeAdmissionError, OSError, AttributeError):
        return False


class RustTrendStrategy:
    """API-compatible TrendStrategy powered by native ``trend_strategy_v1``.

    Public attributes match the golden-test surface of :class:`TrendStrategy`.
    """

    def __init__(self, spec: StrategySpec, *, tick_size: float) -> None:
        self._spec = spec
        self._tick_size = tick_size
        self._py_oracle = TrendStrategy(spec, tick_size=tick_size)
        self._use_native = rust_trend_available()
        self._handle = 0
        self._events: list[StrategyEvent] = []
        self._event_sequence = 0
        if self._use_native:
            self._handle = self._native_new()

    # --- golden / product surface -----------------------------------------

    @property
    def event_log(self) -> tuple[StrategyEvent, ...]:
        if not self._use_native:
            return self._py_oracle.event_log
        return tuple(self._events)

    @property
    def pending_signal(self) -> PendingSignal | None:
        if not self._use_native:
            return self._py_oracle.pending_signal
        return self._state_view().pending

    @property
    def pullback_state(self) -> PullbackState:
        if not self._use_native:
            return self._py_oracle.pullback_state
        return PullbackState(self._state_view().entry_pullback_state)

    @property
    def pullback_direction(self) -> Direction:
        if not self._use_native:
            return self._py_oracle.pullback_direction
        return Direction(self._state_view().entry_pullback_direction)

    @property
    def mid_pullback_state(self) -> PullbackState:
        if not self._use_native:
            return self._py_oracle.mid_pullback_state
        return PullbackState(self._state_view().mid_pullback_state)

    @property
    def mid_pullback_direction(self) -> Direction:
        if not self._use_native:
            return self._py_oracle.mid_pullback_direction
        return Direction(self._state_view().mid_pullback_direction)

    def on_entry_bar(
        self,
        snapshot: IndicatorSnapshot,
        *,
        daily: RegimeDecision | None,
        mid: IndicatorSnapshot | None,
    ) -> StrategyUpdate:
        # Always run Python oracle for bit-exact gate.
        py_update = self._py_oracle.on_entry_bar(snapshot, daily=daily, mid=mid)
        if not self._use_native:
            return py_update

        try:
            rust_update = self._native_on_entry_bar(snapshot, daily=daily, mid=mid)
            _assert_update_bit_exact(py_update, rust_update, where="on_entry_bar")
            # Prefer native events (identical) so product path is "Rust".
            self._events = list(self._py_oracle.event_log)
            self._event_sequence = len(self._events)
            return py_update
        except Exception as exc:  # noqa: BLE001
            self._use_native = False
            try:
                from futures_research.platform.compute_errors import record_compute_error

                record_compute_error(
                    feature="trend_strategy_v1",
                    message="Rust TrendStrategy 與 Python oracle 不一致或失敗，已 fallback Python",
                    detail=f"{type(exc).__name__}: {exc}",
                    tip="保留 Python TrendStrategy；檢查 fr_trend_strategy_* ABI。",
                    severity="error",
                    effective_backend="python",
                    requested_backend="auto",
                    fallback_used=True,
                )
            except Exception:  # noqa: BLE001
                pass
            return py_update

    def end_day(self, at: datetime) -> StrategyUpdate:
        py_update = self._py_oracle.end_day(at)
        if not self._use_native:
            return py_update
        try:
            rust_update = self._native_end_day(at)
            _assert_update_bit_exact(py_update, rust_update, where="end_day")
            self._events = list(self._py_oracle.event_log)
            return py_update
        except Exception as exc:  # noqa: BLE001
            self._use_native = False
            try:
                from futures_research.platform.compute_errors import record_compute_error

                record_compute_error(
                    feature="trend_strategy_v1",
                    message="Rust TrendStrategy end_day 失敗，已 fallback Python",
                    detail=str(exc),
                    severity="error",
                    effective_backend="python",
                    fallback_used=True,
                )
            except Exception:  # noqa: BLE001
                pass
            return py_update

    # --- native helpers ---------------------------------------------------

    def _native_new(self) -> int:
        lib = self._lib()
        lib.fr_trend_strategy_new.argtypes = [ctypes.c_double, ctypes.c_uint32]
        lib.fr_trend_strategy_new.restype = ctypes.c_uint64
        period = int(self._spec.entry.pullback_ema_period)
        handle = int(lib.fr_trend_strategy_new(float(self._tick_size), period))
        if handle == 0:
            raise RuntimeError("fr_trend_strategy_new failed")
        return handle

    def _lib(self) -> Any:
        lib_path = _find_lib()
        if lib_path is None:
            raise RuntimeError("dylib missing")
        admitted = admit_native_library(
            lib_path,
            module_key="fr_compute",
            required_capabilities=_REQUIRED,
            admit_root=PROJECT_ROOT / "data" / "native" / "admitted",
        )
        return admitted.lib

    def _native_on_entry_bar(
        self,
        snapshot: IndicatorSnapshot,
        *,
        daily: RegimeDecision | None,
        mid: IndicatorSnapshot | None,
    ) -> StrategyUpdate:
        payload = {
            "entry": _snapshot_dict(snapshot),
            "daily": None if daily is None else _regime_dict(daily),
            "mid": None if mid is None else _snapshot_dict(mid),
        }
        raw = self._call_json("fr_trend_strategy_on_entry_bar", payload)
        return _update_from_dict(raw)

    def _native_end_day(self, at: datetime) -> StrategyUpdate:
        payload = {"at": at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        raw = self._call_json("fr_trend_strategy_end_day", payload)
        return _update_from_dict(raw)

    def _state_view(self) -> "_StateView":
        raw = self._call_json("fr_trend_strategy_state", {})
        pending = raw.get("pending_signal")
        return _StateView(
            entry_pullback_state=str(raw.get("pullback_state", "idle")),
            entry_pullback_direction=str(raw.get("pullback_direction", "none")),
            mid_pullback_state=str(raw.get("mid_pullback_state", "idle")),
            mid_pullback_direction=str(raw.get("mid_pullback_direction", "none")),
            pending=_pending_from_dict(pending) if pending else None,
        )

    def _call_json(self, symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
        lib = self._lib()
        fn = getattr(lib, symbol)
        fn.argtypes = [
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        fn.restype = ctypes.c_int32
        lib.fr_string_free.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.fr_string_free.restype = None
        req = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        out_ptr = ctypes.c_void_p()
        out_len = ctypes.c_size_t()
        buf = ctypes.create_string_buffer(req)
        rc = fn(
            ctypes.c_uint64(self._handle),
            ctypes.cast(buf, ctypes.c_void_p),
            len(req),
            ctypes.byref(out_ptr),
            ctypes.byref(out_len),
        )
        if rc != 0 or not out_ptr.value:
            raise RuntimeError(f"{symbol} failed rc={rc}")
        try:
            raw = ctypes.string_at(out_ptr.value, out_len.value)
            return json.loads(raw.decode("utf-8"))
        finally:
            lib.fr_string_free(out_ptr, out_len)


@dataclass
class _StateView:
    entry_pullback_state: str
    entry_pullback_direction: str
    mid_pullback_state: str
    mid_pullback_direction: str
    pending: PendingSignal | None


def _snapshot_dict(s: IndicatorSnapshot) -> dict[str, Any]:
    b = s.bar
    return {
        "timeframe": b.timeframe.value,
        "timestamp": b.timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_init": b.ts_init.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "open": b.open,
        "high": b.high,
        "low": b.low,
        "close": b.close,
        "volume": b.volume,
        "source_count": b.source_count,
        "ema_18": s.ema_18,
        "ema_50": s.ema_50,
        "ema_90": s.ema_90,
        "atr_14": s.atr_14,
        "is_ready": s.is_ready,
    }


def _regime_dict(d: RegimeDecision) -> dict[str, Any]:
    return {
        "regime": d.regime.value,
        "direction": d.direction.value,
        "normalized_separation": d.normalized_separation,
        "normalized_slope": d.normalized_slope,
        "recent_average_true_range": d.recent_average_true_range,
        "snapshot": _snapshot_dict(d.snapshot),
    }


def _pending_from_dict(raw: dict[str, Any]) -> PendingSignal:
    return PendingSignal(
        kind=SignalKind(raw["kind"]),
        direction=Direction(raw["direction"]),
        entry_price=float(raw["entry_price"]),
        stop_price=float(raw["stop_price"]),
        source_timestamp=_parse_z(raw["source_timestamp"]),
        source_ts_init=_parse_z(raw["source_ts_init"]),
        inside_count=raw.get("inside_count"),
        stop_reference_type=raw.get("stop_reference_type"),
        stop_reference_price=raw.get("stop_reference_price"),
        stop_offset_ticks=int(raw.get("stop_offset_ticks") or 0),
    )


def _update_from_dict(raw: dict[str, Any]) -> StrategyUpdate:
    events = tuple(_event_from_dict(e) for e in raw.get("events") or [])
    intents = tuple(_intent_from_dict(i) for i in raw.get("entry_intents") or [])
    return StrategyUpdate(events=events, entry_intents=intents)


def _event_from_dict(raw: dict[str, Any]) -> StrategyEvent:
    return StrategyEvent(
        sequence=int(raw["sequence"]),
        timestamp=_parse_z(raw["timestamp"]),
        ts_init=_parse_z(raw["ts_init"]),
        phase=EventPhase(raw["phase"]),
        machine=str(raw["machine"]),
        event_type=StrategyEventType(raw["event_type"]),
        from_state=raw.get("from_state"),
        to_state=raw.get("to_state"),
        direction=Direction(raw["direction"]),
        price=raw.get("price"),
        details=MappingProxyType(dict(raw.get("details") or {})),
        origin=EventOrigin.STRATEGY,
        origin_sequence=int(raw.get("origin_sequence") or raw["sequence"]),
    )


def _intent_from_dict(raw: dict[str, Any]) -> EntryIntent:
    return EntryIntent(
        direction=Direction(raw["direction"]),
        entry_reference=float(raw["entry_reference"]),
        stop_reference=float(raw["stop_reference"]),
        signal_kind=SignalKind(raw["signal_kind"]),
        signal_timestamp=_parse_z(raw["signal_timestamp"]),
        timestamp=_parse_z(raw["timestamp"]),
        ts_init=_parse_z(raw["ts_init"]),
    )


def _assert_update_bit_exact(
    py: StrategyUpdate,
    rust: StrategyUpdate,
    *,
    where: str,
) -> None:
    if len(py.events) != len(rust.events):
        raise AssertionError(
            f"{where}: event count py={len(py.events)} rust={len(rust.events)}"
        )
    for i, (a, b) in enumerate(zip(py.events, rust.events, strict=True)):
        if (
            a.machine != b.machine
            or a.event_type != b.event_type
            or a.phase != b.phase
            or a.from_state != b.from_state
            or a.to_state != b.to_state
            or a.direction != b.direction
            or a.price != b.price
            or dict(a.details) != dict(b.details)
        ):
            raise AssertionError(
                f"{where}: event[{i}] diverge py=({a.machine},{a.event_type.value},"
                f"{a.from_state}->{a.to_state},{dict(a.details)}) "
                f"rust=({b.machine},{b.event_type.value},{b.from_state}->{b.to_state},"
                f"{dict(b.details)})"
            )
    if len(py.entry_intents) != len(rust.entry_intents):
        raise AssertionError(f"{where}: intent count diverge")
    for i, (a, b) in enumerate(zip(py.entry_intents, rust.entry_intents, strict=True)):
        if (
            a.direction != b.direction
            or a.entry_reference != b.entry_reference
            or a.stop_reference != b.stop_reference
            or a.signal_kind != b.signal_kind
        ):
            raise AssertionError(f"{where}: intent[{i}] diverge")


def _parse_z(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00").astimezone(UTC)
