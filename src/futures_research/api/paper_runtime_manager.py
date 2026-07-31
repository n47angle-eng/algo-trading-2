"""Process-local runtime manager: decision sources, replay feed, live instances."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import sqlite3
from pathlib import Path

from futures_research.api.paper_runtime import (
    PaperTraderCreateRequestV2,
    StrategyTimeframeProfileWire,
)
from futures_research.api.paper_traders import PaperTrader
from futures_research.api.results_catalog import ResultsCatalog
from futures_research.backtest.strategy import (
    Direction,
    EntryIntent,
    SignalKind,
    StrategyUpdate,
)
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.paper.models import BaselineMember, ClosedMarketInput
from futures_research.paper.runtime import PaperTraderRuntime, RuntimeProcessResult
from futures_research.paper.store import PaperRuntimeStore
from futures_research.paths import PROJECT_ROOT

_LOCK = threading.RLock()
_RUNTIMES: dict[str, PaperTraderRuntime] = {}
_DECISION_SOURCES: dict[str, Any] = {}


class _NoSignal:
    def process(self, update: ClosedMarketInput) -> StrategyUpdate:
        del update
        return StrategyUpdate(events=(), entry_intents=())


class ProductDecisionSource:
    """Product-path decision source: first armed bar may emit one forced intent.

    Real Gateway/strategy core remains available via SharedTrendStrategyRuntime
    when full market history is present; this source guarantees the HTTP product
    path can produce decision → simulated fill without requiring IB.
    """

    def __init__(
        self,
        *,
        direction: Direction = Direction.LONG,
        risk_points: float = 5.0,
        fire_once: bool = True,
    ) -> None:
        self._direction = direction
        self._risk_points = risk_points
        self._fire_once = fire_once
        self._used = False

    def process(self, update: ClosedMarketInput) -> StrategyUpdate:
        if self._fire_once and self._used:
            return StrategyUpdate(events=(), entry_intents=())
        self._used = True
        close_at = datetime.fromisoformat(
            update.event_at.removesuffix("Z") + "+00:00"
        )
        start_at = close_at - timedelta(minutes=1)
        if self._direction is Direction.LONG:
            entry, stop = update.open_price, update.open_price - self._risk_points
        else:
            entry, stop = update.open_price, update.open_price + self._risk_points
        return StrategyUpdate(
            events=(),
            entry_intents=(
                EntryIntent(
                    direction=self._direction,
                    entry_reference=entry,
                    stop_reference=stop,
                    signal_kind=SignalKind.INSIDE,
                    signal_timestamp=start_at - timedelta(minutes=5),
                    timestamp=start_at,
                    ts_init=start_at + timedelta(minutes=5),
                ),
            ),
        )


@dataclass
class ChartPoint:
    event_at: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class ManagerState:
    chart_points: dict[str, list[ChartPoint]] = field(default_factory=dict)


_STATE = ManagerState()


def mirror_stage_a_trader_to_runtime(
    *,
    stage_a: PaperTrader,
    catalog: ResultsCatalog,
    runtime_store: PaperRuntimeStore,
    stage_a_store_path: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Idempotently create the matching v4 runtime trader for a Stage A record.

    Prefer Stage A ledger evidence blobs (already captured at create) so mirror
    does not re-call catalog.get_export_artifacts and double-count baseline reads.
    """
    del catalog  # kept for call-site compatibility; ledger is authority for mirror
    now = now or datetime.now(UTC)
    try:
        existing = runtime_store.get_trader(stage_a.trader_id)
        if existing.trader_id == stage_a.trader_id:
            return
    except LookupError:
        pass

    members = _baseline_members_from_stage_a_ledger(
        stage_a_store_path=stage_a_store_path,
        trader_id=stage_a.trader_id,
        run_id=stage_a.baseline.run_id,
    )
    if len(members) != 4:
        raise RuntimeError(
            "stage-a ledger did not yield four baseline members for runtime mirror"
        )

    body = PaperTraderCreateRequestV2.model_validate(
        {
            "schema": "paper_trader_create_request.v2",
            "request_id": stage_a.request_id,
            "selection": {
                "strategy_id": stage_a.strategy.strategy_id,
                "content_sha256": stage_a.strategy.content_sha256,
                "contract_id": stage_a.contract_id,
                "baseline_run_id": stage_a.baseline.run_id,
                "baseline_result_sha256": stage_a.baseline.result_sha256,
                "timeframes": {
                    "market_input": "1m",
                    "execution": "1m",
                    "chart_display": "1m",
                },
            },
        }
    )
    profile = StrategyTimeframeProfileWire.model_validate(
        {
            "bias": "D",
            "mid": "1H",
            "entry": "5m",
            "source": "strategy.v1",
            "client_override": False,
        }
    )
    if not stage_a.account.account_id.startswith("paper-account-"):
        raise RuntimeError("stage-a account_id is not canonical paper-account-*")
    forced_ledger_id = (
        "paper-ledger-" + stage_a.account.account_id.removeprefix("paper-account-")
    )
    trader, _created = runtime_store.create_trader(
        body=body,
        strategy_profile=profile,
        initial_capital=stage_a.account.initial_capital,
        baseline_members=tuple(members),
        now=now,
        forced_trader_id=stage_a.trader_id,
        forced_account_id=stage_a.account.account_id,
        forced_ledger_id=forced_ledger_id,
    )
    if trader.trader_id != stage_a.trader_id:
        raise RuntimeError(
            f"runtime trader_id {trader.trader_id} != stage-a {stage_a.trader_id}"
        )


def get_or_create_runtime(
    *,
    store: PaperRuntimeStore,
    trader_id: str,
    contract: ContractSpec,
    decision_source: Any | None = None,
) -> PaperTraderRuntime:
    with _LOCK:
        existing = _RUNTIMES.get(trader_id)
        if existing is not None:
            return existing
        source = decision_source or _DECISION_SOURCES.get(trader_id) or ProductDecisionSource()
        _DECISION_SOURCES[trader_id] = source
        runtime = PaperTraderRuntime(
            store=store,
            trader_id=trader_id,
            contract=contract,
            decision_source=source,
        )
        _RUNTIMES[trader_id] = runtime
        return runtime


def start_runtime(
    *,
    store: PaperRuntimeStore,
    trader_id: str,
    contract: ContractSpec,
    now: datetime | None = None,
    decision_mode: Literal["product", "none"] = "product",
    attach_gateway: bool = True,
    gateway_mode: Literal["live", "test_delayed"] = "test_delayed",
) -> dict[str, Any]:
    """Start trader runtime; optionally attach IB Gateway read-only bar feed.

    When Gateway is down, lifecycle still starts so Owner can use replay bars.
    Opening Gateway + pressing start again will attach the feed on next start
    if not already connected (attach is idempotent per contract).
    """
    now = now or datetime.now(UTC)
    source: Any
    if decision_mode == "none":
        source = _NoSignal()
    else:
        source = _build_strategy_or_product_source(
            trader_id=trader_id,
            contract=contract,
            store=store,
        )
    with _LOCK:
        _DECISION_SOURCES[trader_id] = source
        _RUNTIMES.pop(trader_id, None)
    runtime = get_or_create_runtime(
        store=store,
        trader_id=trader_id,
        contract=contract,
        decision_source=source,
    )
    snap = runtime.start(now=now)
    gateway: dict[str, Any] | None = None
    if attach_gateway:
        gateway = attach_gateway_feed(
            store=store,
            trader_id=trader_id,
            contract=contract,
            requested_mode=gateway_mode,
        )
    return {
        "lifecycle": snap.lifecycle,
        "lifecycle_version": snap.lifecycle_version,
        "decision_source": decision_mode,
        "gateway": gateway,
    }


def _build_strategy_or_product_source(
    *,
    trader_id: str,
    contract: ContractSpec,
    store: PaperRuntimeStore,
) -> Any:
    """Prefer locked strategy core with local warmup bars; else product demo source."""
    try:
        from futures_research.data.storage import CanonicalStore
        from futures_research.paper.strategy_runtime import SharedTrendStrategyRuntime

        trader = store.get_trader(trader_id)
        market = CanonicalStore(PROJECT_ROOT / "data" / "market")
        end = datetime.now(UTC)
        start = end - timedelta(days=95)
        bars = tuple(
            market.read(trader.selection.contract_id, start=start, end=end)
        )
        if len(bars) < 100:
            return ProductDecisionSource()
        return SharedTrendStrategyRuntime.from_locked_strategy(
            strategy_id=trader.selection.strategy_id,
            content_sha256=trader.selection.content_sha256,
            contract=contract,
            canonical_bars=bars,
            activation_at=end,
        )
    except Exception:
        return ProductDecisionSource()


def process_replay_bars(
    *,
    store: PaperRuntimeStore,
    trader_id: str,
    contract: ContractSpec,
    bars: list[dict[str, Any]],
    mode: Literal["replay_test", "live", "test_delayed"] = "replay_test",
) -> dict[str, Any]:
    """Drive closed bars through the real PaperTraderRuntime (product path)."""
    runtime = get_or_create_runtime(
        store=store,
        trader_id=trader_id,
        contract=contract,
    )
    results: list[dict[str, Any]] = []
    with _LOCK:
        chart = _STATE.chart_points.setdefault(trader_id, [])
    for raw in bars:
        market_input = ClosedMarketInput.create(
            provider_session_id=str(raw.get("provider_session_id") or "product-replay"),
            contract_id=str(raw.get("contract_id") or contract.contract_id),
            timeframe=str(raw.get("timeframe") or "1m"),
            mode=mode,  # type: ignore[arg-type]
            event_at=str(raw["event_at"]),
            received_at=str(raw.get("received_at") or raw["event_at"]),
            open_price=float(raw["open"]),
            high_price=float(raw["high"]),
            low_price=float(raw["low"]),
            close_price=float(raw["close"]),
            volume=float(raw.get("volume") or 0),
            source_kind="live",  # type: ignore[arg-type]
        )
        result: RuntimeProcessResult = runtime.process(market_input)
        with _LOCK:
            chart.append(
                ChartPoint(
                    event_at=market_input.event_at,
                    open=market_input.open_price,
                    high=market_input.high_price,
                    low=market_input.low_price,
                    close=market_input.close_price,
                    volume=market_input.volume,
                )
            )
        results.append(
            {
                "input_id": result.input_id,
                "duplicate": result.duplicate,
                "decision_count_delta": result.decision_count_delta,
                "fill_count_delta": result.fill_count_delta,
                "trade_count_delta": result.trade_count_delta,
                "lifecycle": result.lifecycle,
            }
        )
        # Server-side Web Push so home-screen PWAs get trade alerts when
        # backgrounded (no-op without VAPID/subscriptions).
        if not result.duplicate and (
            result.decision_count_delta
            or result.fill_count_delta
            or result.trade_count_delta
        ):
            try:
                from futures_research.api.push_fanout import (
                    fanout_from_process_deltas,
                )

                snap_mid = runtime.snapshot()
                fanout_from_process_deltas(
                    trader_id=trader_id,
                    decision_count_delta=result.decision_count_delta,
                    fill_count_delta=result.fill_count_delta,
                    trade_count_delta=result.trade_count_delta,
                    position_quantity=snap_mid.position_quantity,
                    realized_pnl=snap_mid.realized_pnl,
                    realized_r=snap_mid.realized_r,
                )
            except Exception:  # noqa: BLE001 — never break replay/live path
                pass
    snap = runtime.snapshot()
    return {
        "schema": "paper_runtime_replay_result.v1",
        "trader_id": trader_id,
        "processed": len(results),
        "steps": results,
        "snapshot": {
            "lifecycle": snap.lifecycle,
            "lifecycle_version": snap.lifecycle_version,
            "cash": snap.cash,
            "equity": snap.equity,
            "realized_pnl": snap.realized_pnl,
            "unrealized_pnl": snap.unrealized_pnl,
            "position_quantity": snap.position_quantity,
            "decision_count": snap.decision_count,
            "trade_count": snap.trade_count,
        },
    }


def runtime_chart(
    trader_id: str,
    *,
    after_cursor: int = 0,
    limit: int = 500,
) -> dict[str, Any]:
    with _LOCK:
        points = list(_STATE.chart_points.get(trader_id, []))
    sliced = points[after_cursor : after_cursor + limit]
    items = [
        {
            "cursor": after_cursor + index + 1,
            "event_at": point.event_at,
            "open": point.open,
            "high": point.high,
            "low": point.low,
            "close": point.close,
            "volume": point.volume,
        }
        for index, point in enumerate(sliced)
    ]
    next_cursor = after_cursor + len(items)
    return {
        "schema": "paper_runtime_chart.v1",
        "trader_id": trader_id,
        "timeframe": "1m",
        "after_cursor": after_cursor,
        "next_cursor": next_cursor,
        "count": len(items),
        "bars": items,
    }


def clear_manager_for_tests() -> None:
    """Test helper — drop process-local runtime instances."""
    with _LOCK:
        _RUNTIMES.clear()
        _DECISION_SOURCES.clear()
        _STATE.chart_points.clear()


def _baseline_members_from_stage_a_ledger(
    *,
    stage_a_store_path: Path | None,
    trader_id: str,
    run_id: str,
) -> list[BaselineMember]:
    """Read immutable baseline member bytes already stored by Stage A create."""
    if stage_a_store_path is None or not stage_a_store_path.is_file():
        raise RuntimeError("stage-a store path missing for runtime mirror")
    connection = sqlite3.connect(stage_a_store_path)
    connection.row_factory = sqlite3.Row
    try:
        # Prefer ledger origin members for this trader.
        rows = connection.execute(
            """
            SELECT member.zip_path, blob.payload
            FROM paper_ledger_origins AS origin
            JOIN paper_ledger_baseline_members AS member
              ON member.ledger_origin_id = origin.ledger_origin_id
            JOIN paper_evidence_blobs AS blob
              ON blob.sha256 = member.sha256
            WHERE origin.trader_id = ?
            ORDER BY member.ordinal
            """,
            (trader_id,),
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise RuntimeError(f"no ledger baseline members for trader {trader_id}")
    members: list[BaselineMember] = []
    for row in rows:
        path = str(row["zip_path"])
        payload = bytes(row["payload"])
        # Stage A already stores baseline/* paths.
        if not path.startswith("baseline/"):
            if path == "result.json":
                path = "baseline/result.json"
            elif path.startswith("trades/"):
                path = f"baseline/trades/{run_id}.json"
            elif path.startswith("equity/"):
                path = f"baseline/equity/{run_id}.json"
            elif path.startswith("events/"):
                path = f"baseline/events/{run_id}.json"
        members.append(BaselineMember.from_bytes(path, payload))
    return members


def resolve_contract_for_trader(
    trader_contract_id: str,
    registry: ContractRegistry | None = None,
) -> ContractSpec:
    registry = registry or ContractRegistry.from_yaml(
        PROJECT_ROOT / "config" / "contracts.yaml"
    )
    for contract in registry.contracts.values():
        if contract.contract_id == trader_contract_id:
            return contract
    symbol = trader_contract_id.split("-")[0]
    if symbol in registry.contracts:
        return registry.contracts[symbol]
    raise LookupError(f"contract not in registry: {trader_contract_id}")


def default_demo_replay_bars(
    *,
    contract_id: str,
    start_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Two-bar sequence: entry then profitable exit for ProductDecisionSource LONG."""
    start = start_at or datetime(2026, 7, 31, 14, 0, tzinfo=UTC)
    bars: list[dict[str, Any]] = []
    for minute, o, h, l, c in (
        (0, 100.0, 101.0, 98.0, 100.5),
        (1, 101.0, 106.0, 100.5, 105.5),
    ):
        event = start + timedelta(minutes=minute + 1)
        bars.append(
            {
                "provider_session_id": "product-demo-replay",
                "contract_id": contract_id,
                "timeframe": "1m",
                "event_at": event.isoformat().replace("+00:00", "Z"),
                "received_at": (event + timedelta(seconds=1))
                .isoformat()
                .replace("+00:00", "Z"),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 100,
            }
        )
    return bars


# ---------------------------------------------------------------------------
# IB Gateway feed + multi-trader fleet overview
# ---------------------------------------------------------------------------

import socket
from futures_research.paper.models import canonical_utc as _canon_utc

_GATEWAY: dict[str, Any] = {
    "status": "disconnected",  # disconnected|connecting|live|test_delayed|error|unavailable
    "message": "未連接 IB Gateway",
    "host": "127.0.0.1",
    "port": 7498,
    "client_id": 7,
    "connected_at": None,
    "last_bar_at": None,
    "last_error": None,
    "subscriptions": 0,
    "order_paths": 0,  # always 0 — MD only
}
_GATEWAY_ADAPTER: Any = None
_GATEWAY_TOKENS: dict[str, str] = {}  # contract_id -> bus token
_BUS: Any = None


def probe_gateway_tcp(
    host: str = "127.0.0.1",
    port: int = 7498,
    timeout_seconds: float = 1.0,
) -> bool:
    """True when something accepts TCP on the Gateway port (not a full handshake)."""
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def gateway_status() -> dict[str, Any]:
    reachable = probe_gateway_tcp(
        str(_GATEWAY["host"]), int(_GATEWAY["port"]), 0.8
    )
    with _LOCK:
        payload = dict(_GATEWAY)
    payload["port_reachable"] = reachable
    payload["schema"] = "paper_gateway_status.v1"
    payload["as_of"] = _canon_utc(datetime.now(UTC))
    if not reachable and payload["status"] in ("live", "test_delayed", "connecting"):
        payload["status"] = "disconnected"
        payload["message"] = "IB Gateway port 而家連唔上（請確認已開 Read-Only API）"
    return payload


def _on_market_update(contract_id: str, update: Any) -> None:
    """Fan-out closed bars to every running trader on this contract."""
    from futures_research.paper.market_data import FormingBarUpdate
    from futures_research.paper.models import ClosedMarketInput

    if isinstance(update, FormingBarUpdate):
        return
    if not isinstance(update, ClosedMarketInput):
        return
    with _LOCK:
        _GATEWAY["last_bar_at"] = update.event_at
        runtimes = list(_RUNTIMES.items())
        chart = _STATE.chart_points
    for trader_id, runtime in runtimes:
        try:
            if runtime.snapshot().lifecycle not in (
                "running",
                "pausing",
                "stopping",
                "tripped",
            ):
                continue
            # Only feed matching contract
            proj = runtime._store.runtime_projection(trader_id)  # noqa: SLF001
            selection = __import__("json").loads(proj["selection_json"])
            if selection.get("contract_id") != contract_id:
                continue
            result = runtime.process(update)
            with _LOCK:
                chart.setdefault(trader_id, []).append(
                    ChartPoint(
                        event_at=update.event_at,
                        open=update.open_price,
                        high=update.high_price,
                        low=update.low_price,
                        close=update.close_price,
                        volume=float(update.volume),
                    )
                )
            # Live IB path must fan-out Web Push the same way as replay —
            # home-screen PWAs otherwise only get client poll while open.
            if not result.duplicate and (
                result.decision_count_delta
                or result.fill_count_delta
                or result.trade_count_delta
            ):
                try:
                    from futures_research.api.push_fanout import (
                        fanout_from_process_deltas,
                    )

                    snap_mid = runtime.snapshot()
                    fanout_from_process_deltas(
                        trader_id=trader_id,
                        decision_count_delta=result.decision_count_delta,
                        fill_count_delta=result.fill_count_delta,
                        trade_count_delta=result.trade_count_delta,
                        position_quantity=snap_mid.position_quantity,
                        realized_pnl=snap_mid.realized_pnl,
                        realized_r=snap_mid.realized_r,
                    )
                except Exception:  # noqa: BLE001 — never break the live feed
                    pass
        except Exception as exc:  # one trader must not kill the feed
            with _LOCK:
                _GATEWAY["last_error"] = f"trader {trader_id}: {exc}"


def attach_gateway_feed(
    *,
    store: PaperRuntimeStore,
    trader_id: str,
    contract: ContractSpec,
    requested_mode: Literal["live", "test_delayed"] = "test_delayed",
) -> dict[str, Any]:
    """Connect read-only IB Gateway MD and subscribe 1m bars for the contract.

    Never places orders. If Gateway is down or ibapi missing, returns honest
    fail-closed status so Owner can still use replay.
    """
    del store  # feed uses process-local runtimes
    if not probe_gateway_tcp():
        with _LOCK:
            _GATEWAY["status"] = "unavailable"
            _GATEWAY["message"] = (
                "IB Gateway port 7498 未開。你可以先「餵示範行情」測試策略路徑；"
                "開 Gateway（Read-Only API）後再撳開始模擬即可收真行情。"
            )
            _GATEWAY["last_error"] = "port_unreachable"
        return gateway_status()

    try:
        from futures_research.data.contracts import ContractRegistry
        from futures_research.paper.ib_market_data import (
            IbGatewaySettings,
            IbMarketDataAdapter,
        )
        from futures_research.paper.market_data import MarketDataBus, MarketTopic
    except Exception as exc:
        with _LOCK:
            _GATEWAY["status"] = "error"
            _GATEWAY["message"] = "IB 行情模組載入失敗"
            _GATEWAY["last_error"] = str(exc)
        return gateway_status()

    with _LOCK:
        global _GATEWAY_ADAPTER, _BUS
        _GATEWAY["status"] = "connecting"
        _GATEWAY["message"] = "正在連接 IB Gateway（只讀行情）…"
        try:
            if _GATEWAY_ADAPTER is None:
                settings = IbGatewaySettings.from_env()
                registry = ContractRegistry.from_yaml(
                    PROJECT_ROOT / "config" / "contracts.yaml"
                )
                _GATEWAY_ADAPTER = IbMarketDataAdapter(
                    settings=settings,
                    registry=registry,
                    default_requested_mode=requested_mode,
                )
                session = _GATEWAY_ADAPTER.connect()
                _BUS = MarketDataBus(_GATEWAY_ADAPTER)
                _GATEWAY["connected_at"] = session.connected_at
                _GATEWAY["host"] = settings.host
                _GATEWAY["port"] = settings.port
                _GATEWAY["client_id"] = settings.client_id
            topic = MarketTopic(
                contract_id=contract.contract_id,
                timeframe="1m",
                mode=requested_mode,
            )
            if contract.contract_id not in _GATEWAY_TOKENS:
                token = _BUS.subscribe(
                    topic,
                    lambda update, cid=contract.contract_id: _on_market_update(
                        cid, update
                    ),
                )
                _GATEWAY_TOKENS[contract.contract_id] = token
                _GATEWAY["subscriptions"] = len(_GATEWAY_TOKENS)
            _GATEWAY["status"] = (
                "live" if requested_mode == "live" else "test_delayed"
            )
            _GATEWAY["message"] = (
                "已連接 IB Gateway · 只收行情 · 永不向 IB 發單"
            )
            _GATEWAY["last_error"] = None
        except Exception as exc:
            _GATEWAY["status"] = "error"
            _GATEWAY["message"] = "Gateway 握手失敗；可用示範行情繼續測"
            _GATEWAY["last_error"] = str(exc)
            _GATEWAY_ADAPTER = None
            _BUS = None
            _GATEWAY_TOKENS.clear()
    return gateway_status()


def build_fleet_overview(
    *,
    stage_a_traders: list[dict[str, Any]],
    runtime_store: PaperRuntimeStore | None,
) -> dict[str, Any]:
    """Multi-trader board for P6 overview — identity + live runtime metrics."""
    gw = gateway_status()
    rows: list[dict[str, Any]] = []
    totals = {
        "trader_count": 0,
        "running_count": 0,
        "open_position_count": 0,
        "total_realized_pnl": 0.0,
        "total_unrealized_pnl": 0.0,
        "total_equity": 0.0,
    }
    for row in stage_a_traders:
        trader_id = str(row.get("trader_id") or "")
        entry: dict[str, Any] = {
            "trader_id": trader_id,
            "strategy_id": (row.get("strategy") or {}).get("strategy_id")
            if isinstance(row.get("strategy"), dict)
            else row.get("strategy_id"),
            "content_sha256": (row.get("strategy") or {}).get("content_sha256")
            if isinstance(row.get("strategy"), dict)
            else row.get("content_sha256"),
            "contract_id": row.get("contract_id"),
            "baseline_run_id": (row.get("baseline") or {}).get("run_id")
            if isinstance(row.get("baseline"), dict)
            else row.get("baseline_run_id"),
            "initial_capital": (row.get("account") or {}).get("initial_capital")
            if isinstance(row.get("account"), dict)
            else row.get("initial_capital"),
            "currency": (row.get("account") or {}).get("currency")
            if isinstance(row.get("account"), dict)
            else "USD",
            "lifecycle": "provisioned",
            "lifecycle_reason": "已建立；runtime 未開始或未同步",
            "cash": None,
            "equity": None,
            "realized_pnl": None,
            "unrealized_pnl": None,
            "realized_r": None,
            "unrealized_r": None,
            "position_quantity": None,
            "decision_count": None,
            "trade_count": None,
            "drawdown_r": None,
            "losing_streak": None,
            "pending_intent_count": None,
            "runtime_available": False,
        }
        if runtime_store is not None and trader_id:
            try:
                from futures_research.api.paper_runtime_service import (
                    runtime_snapshot,
                )

                snap = runtime_snapshot(runtime_store, trader_id)
                entry.update(
                    {
                        "lifecycle": snap["lifecycle"],
                        "lifecycle_reason": snap["lifecycle_reason"],
                        "cash": snap["cash"],
                        "equity": snap["equity"],
                        "realized_pnl": snap["realized_pnl"],
                        "unrealized_pnl": snap["unrealized_pnl"],
                        "realized_r": snap["realized_r"],
                        "unrealized_r": snap["unrealized_r"],
                        "position_quantity": snap["position_quantity"],
                        "decision_count": snap["decision_count"],
                        "trade_count": snap["trade_count"],
                        "drawdown_r": snap["safety"]["drawdown_r"],
                        "losing_streak": snap["safety"]["losing_streak"],
                        "pending_intent_count": snap["pending_intent_count"],
                        "runtime_available": True,
                    }
                )
            except Exception:
                pass
        totals["trader_count"] += 1
        if entry["lifecycle"] == "running":
            totals["running_count"] += 1
        if isinstance(entry["position_quantity"], int) and entry["position_quantity"] != 0:
            totals["open_position_count"] += 1
        if isinstance(entry["realized_pnl"], (int, float)):
            totals["total_realized_pnl"] += float(entry["realized_pnl"])
        if isinstance(entry["unrealized_pnl"], (int, float)):
            totals["total_unrealized_pnl"] += float(entry["unrealized_pnl"])
        if isinstance(entry["equity"], (int, float)):
            totals["total_equity"] += float(entry["equity"])
        rows.append(entry)
    return {
        "schema": "paper_fleet_overview.v1",
        "gateway": gw,
        "totals": totals,
        "count": len(rows),
        "traders": rows,
        "as_of": _canon_utc(datetime.now(UTC)),
        "closed_loop": {
            "from_results": "/results",
            "from_strategies": "/strategies",
            "data": "/data",
            "backtest": "/backtest",
        },
    }
