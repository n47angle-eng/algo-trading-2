"""Backend chart series + narrative for P5 viewer (WO-006 / 6-3).

Candles and EMA come from precomputed MTF / native daily paths — never leave
indicator math to the browser (D18).

WO-006 / 6-3b: chart series are written as sidecars under ``chart/`` (same
artifact family as events/trades). Endpoints prefer pure file reads; first miss
materializes the sidecar (lazy). New runs can also materialize at export time.

WO-006 / 6-5: each sidecar records the run's ``data_fingerprint`` digest. A run
is immutable but the chart is derived from canonical 1m bars, so backfilling a
missing bar would otherwise leave a stale chart served forever. On read, a
missing or mismatched digest invalidates the sidecar and forces a recompute.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Final, Literal
from uuid import uuid4

from futures_research.api.results_catalog import ResultsCatalog
from futures_research.backtest.mtf import MtfPrecomputer, MtfSeries, Timeframe
from futures_research.backtest.native_daily_mtf import build_native_daily_mtf_series
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT
from futures_research.platform.backend_policy import (
    BackendMode,
    requested_backend_from_env,
)
from futures_research.platform.chart_series_backend import (
    build_product_series_via_rust,
    product_chart_should_try_rust,
    python_mtf_provenance,
)
from futures_research.platform.chart_shadow_compare import compare_chart_series_payloads

ChartTf = Literal["5m", "1H", "D"]

_TF_MAP: dict[str, Timeframe] = {
    "5m": Timeframe.M5,
    "1H": Timeframe.H1,
    "D": Timeframe.D1,
}

# Default lookback of 1m history before the visible window (for EMA warm-up + view).
_DEFAULT_LOOKBACK_DAYS: dict[str, int] = {
    "5m": 10,
    "1H": 30,
    "D": 200,  # daily uses native daily store; days of daily bars
}

_DEFAULT_MATERIALIZE_TFS: tuple[str, ...] = ("5m", "1H", "D")

# Process-local L1 after sidecar load (workers each have their own; file is L0 truth).
_MEMORY_LOCK = Lock()
_MEMORY_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}

_LOGGER: Final = logging.getLogger(__name__)


def resolve_contract(contract_id: str) -> ContractSpec:
    """Look up ContractSpec by contract_id from contracts.yaml."""
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    for contract in registry.contracts.values():
        if contract.contract_id == contract_id:
            return contract
    msg = f"unknown contract_id: {contract_id}"
    raise LookupError(msg)


def chart_sidecar_path(
    results_root: Path,
    run_id: str,
    timeframe: str,
    lookback_days: int,
) -> Path:
    """Return ``chart/<run_id>-<tf>-lb<N>.json`` under the results root."""
    safe_tf = timeframe.replace("/", "_")
    return results_root / "chart" / f"{run_id}-{safe_tf}-lb{lookback_days}.json"


def resolve_lookback_days(timeframe: str, lookback_days: int | None) -> int:
    """Resolve the effective lookback used for cache keys and computation."""
    if timeframe not in _TF_MAP:
        msg = f"unsupported timeframe '{timeframe}' (use 5m|1H|D)"
        raise ValueError(msg)
    if lookback_days is not None:
        return lookback_days
    return _DEFAULT_LOOKBACK_DAYS[timeframe]


def run_data_fingerprint(catalog: ResultsCatalog, run_id: str) -> str | None:
    """Return the run's canonical-bars SHA-256 digest, or ``None`` if absent."""
    document = catalog.get_result(run_id)
    run_raw = document.get("run")
    run_obj: dict[str, Any] = run_raw if isinstance(run_raw, dict) else {}
    manifest_raw = run_obj.get("manifest")
    manifest: dict[str, Any] = manifest_raw if isinstance(manifest_raw, dict) else {}
    fingerprint_raw = manifest.get("data_fingerprint")
    if not isinstance(fingerprint_raw, dict):
        return None
    digest = fingerprint_raw.get("digest")
    return str(digest) if isinstance(digest, str) and digest else None


def get_chart_series(
    catalog: ResultsCatalog,
    run_id: str,
    *,
    timeframe: str = "5m",
    lookback_days: int | None = None,
    market_root: Path | None = None,
    daily_root: Path | None = None,
    use_cache: bool = True,
    force_backend: BackendMode | None = None,
) -> dict[str, Any]:
    """Return chart series, preferring sidecar (+ optional process memory) cache.

    Cache key = ``(run_id, timeframe, lookback_days)``, additionally validated
    against the run's ``data_fingerprint``. On miss or stale digest, compute
    once, atomically write ``chart/<run_id>-<tf>-lbN.json``, then serve.

    ``force_backend`` is for tests / ops probes; normal requests use env policy.
    When ``force_backend`` is set, process/file cache is bypassed so provenance
    reflects the requested compute path.
    """
    lookback = resolve_lookback_days(timeframe, lookback_days)
    cache_key = (run_id, timeframe, lookback)
    expected_fingerprint = run_data_fingerprint(catalog, run_id)
    stale = False
    # Backend probes must not return a prior sidecar computed under another backend.
    allow_cache = use_cache and force_backend is None

    if allow_cache:
        with _MEMORY_LOCK:
            cached = _MEMORY_CACHE.get(cache_key)
        if cached is not None:
            if _fingerprint_matches(cached, expected_fingerprint, run_id=run_id, source="memory"):
                payload = dict(cached)
                payload["cache"] = "memory"
                return payload
            stale = True
            with _MEMORY_LOCK:
                _MEMORY_CACHE.pop(cache_key, None)

        sidecar = chart_sidecar_path(catalog.results_root, run_id, timeframe, lookback)
        if sidecar.is_file():
            loaded = _read_json_object(sidecar)
            if _fingerprint_matches(loaded, expected_fingerprint, run_id=run_id, source="sidecar"):
                with _MEMORY_LOCK:
                    _MEMORY_CACHE[cache_key] = loaded
                payload = dict(loaded)
                payload["cache"] = "sidecar"
                return payload
            stale = True

    computed = build_chart_series(
        catalog,
        run_id,
        timeframe=timeframe,
        lookback_days=lookback,
        market_root=market_root,
        daily_root=daily_root,
        force_backend=force_backend,
    )
    # Persist durable sidecar (L0); also fill process memory (L1).
    # Do not poison the default cache with force_backend probe results.
    if allow_cache:
        sidecar = chart_sidecar_path(catalog.results_root, run_id, timeframe, lookback)
        try:
            _write_json_atomic(sidecar, computed)
            with _MEMORY_LOCK:
                _MEMORY_CACHE[cache_key] = dict(computed)
            computed = dict(computed)
            computed["cache"] = "write_stale_fingerprint" if stale else "write"
        except OSError:
            computed = dict(computed)
            computed["cache"] = "miss_no_write"
    else:
        computed = dict(computed)
        computed["cache"] = "bypass" if use_cache else "bypass"
        if force_backend is not None:
            computed["cache"] = "force_backend_bypass"
    return computed


def _fingerprint_matches(
    payload: Mapping[str, Any],
    expected: str | None,
    *,
    run_id: str,
    source: str,
) -> bool:
    """Report whether a cached chart payload was built from the run's current data.

    A sidecar written before 6-5 carries no digest at all; that counts as stale
    rather than trusted, because it cannot be shown to predate a data backfill.
    Every rejection is logged — a cache that silently rebuilds is fine, a cache
    that silently serves the wrong picture is not.
    """
    if expected is None:
        # The run itself has no digest to compare against; nothing to validate.
        return True
    actual = payload.get("data_fingerprint")
    if actual == expected:
        return True
    _LOGGER.warning(
        "chart %s cache for run %s is stale (%s); recomputing from canonical bars",
        source,
        run_id,
        "no data_fingerprint recorded" if actual is None else f"digest {actual!r} != {expected!r}",
    )
    return False


def materialize_chart_sidecars(
    catalog: ResultsCatalog,
    run_id: str,
    *,
    timeframes: tuple[str, ...] = _DEFAULT_MATERIALIZE_TFS,
    market_root: Path | None = None,
    daily_root: Path | None = None,
) -> list[Path]:
    """Compute and write default-TF chart sidecars for one run (export-time hook)."""
    written: list[Path] = []
    for timeframe in timeframes:
        lookback = resolve_lookback_days(timeframe, None)
        path = chart_sidecar_path(catalog.results_root, run_id, timeframe, lookback)
        payload = build_chart_series(
            catalog,
            run_id,
            timeframe=timeframe,
            lookback_days=lookback,
            market_root=market_root,
            daily_root=daily_root,
        )
        _write_json_atomic(path, payload)
        with _MEMORY_LOCK:
            _MEMORY_CACHE[(run_id, timeframe, lookback)] = dict(payload)
        written.append(path)
    return written


def clear_chart_memory_cache() -> None:
    """Test helper: drop process-local L1 chart cache."""
    with _MEMORY_LOCK:
        _MEMORY_CACHE.clear()


def shadow_compare_run_chart(
    catalog: ResultsCatalog,
    run_id: str,
    *,
    timeframe: str = "5m",
    lookback_days: int | None = None,
    market_root: Path | None = None,
    daily_root: Path | None = None,
    abs_eps: float = 1e-6,
) -> dict[str, Any]:
    """Same-window Python MTF vs Rust product chart diff (Phase F value gate).

    Always rebuilds both paths with ``force_backend`` (no sidecar cache) so the
    report reflects live compute, not a previously sealed sidecar.
    """
    lookback = resolve_lookback_days(timeframe, lookback_days)
    reference = build_chart_series(
        catalog,
        run_id,
        timeframe=timeframe,
        lookback_days=lookback,
        market_root=market_root,
        daily_root=daily_root,
        force_backend="stable",
    )
    try:
        candidate = build_chart_series(
            catalog,
            run_id,
            timeframe=timeframe,
            lookback_days=lookback,
            market_root=market_root,
            daily_root=daily_root,
            force_backend="accelerated",
        )
    except Exception as exc:  # noqa: BLE001 — report, never 500 for gate tooling
        return {
            "schema": "chart_shadow_compare.v1",
            "verdict": "SKIPPED",
            "reason": f"candidate_build_failed:{type(exc).__name__}:{exc}",
            "timeframe": timeframe,
            "run_id": run_id,
            "lookback_days": lookback,
            "reference": {
                "source": reference.get("source"),
                "compute_provenance": reference.get("compute_provenance"),
                "candle_count": len(reference.get("candles") or []),
            },
            "candidate": None,
            "abs_eps": abs_eps,
        }

    cand_prov = candidate.get("compute_provenance")
    cand_backend = (
        cand_prov.get("effective_backend")
        if isinstance(cand_prov, dict)
        else None
    )
    if cand_backend != "rust":
        return {
            "schema": "chart_shadow_compare.v1",
            "verdict": "SKIPPED",
            "reason": "rust_not_effective",
            "timeframe": timeframe,
            "run_id": run_id,
            "lookback_days": lookback,
            "reference": {
                "source": reference.get("source"),
                "compute_provenance": reference.get("compute_provenance"),
                "candle_count": len(reference.get("candles") or []),
            },
            "candidate": {
                "source": candidate.get("source"),
                "compute_provenance": candidate.get("compute_provenance"),
                "candle_count": len(candidate.get("candles") or []),
            },
            "abs_eps": abs_eps,
            "note": (
                "Candidate did not use Rust (dylib missing, kill-switch, or "
                "admission failure). Diff not meaningful for value gate."
            ),
        }

    report = compare_chart_series_payloads(
        reference=reference,
        candidate=candidate,
        abs_eps=abs_eps,
    )
    report["lookback_days"] = lookback
    report["visible_start"] = reference.get("visible_start")
    report["visible_end"] = reference.get("visible_end")
    return report


def build_chart_series(
    catalog: ResultsCatalog,
    run_id: str,
    *,
    timeframe: str = "5m",
    lookback_days: int | None = None,
    market_root: Path | None = None,
    daily_root: Path | None = None,
    force_backend: BackendMode | None = None,
) -> dict[str, Any]:
    """Compute candles + EMA overlays + markers/levels (no cache I/O).

    Phase C/F production: default ``FR_COMPUTE_BACKEND=auto`` prefers Rust via
    the chart_compute seam when the dylib is admitted. On any failure, full-job
    fall back to Python MTF. Force ``stable`` to pin pure Python.
    """
    if timeframe not in _TF_MAP:
        msg = f"unsupported timeframe '{timeframe}' (use 5m|1H|D)"
        raise ValueError(msg)
    document = catalog.get_result(run_id)
    run_raw = document.get("run")
    run_obj: dict[str, Any] = run_raw if isinstance(run_raw, dict) else {}
    manifest_raw = run_obj.get("manifest")
    manifest: dict[str, Any] = manifest_raw if isinstance(manifest_raw, dict) else {}
    contract_id = str(manifest.get("contract_id") or "")
    session_name = str(manifest.get("session_name") or "eth")
    fingerprint_raw = manifest.get("data_fingerprint")
    data_fingerprint = fingerprint_raw.get("digest") if isinstance(fingerprint_raw, dict) else None
    if not contract_id:
        msg = "result missing run.manifest.contract_id"
        raise ValueError(msg)
    range_start = _parse_ts(manifest.get("range_start"))
    range_end = _parse_ts(manifest.get("range_end"))
    if range_start is None or range_end is None:
        msg = "result missing range_start/range_end"
        raise ValueError(msg)

    contract = resolve_contract(contract_id)
    lookback = lookback_days if lookback_days is not None else _DEFAULT_LOOKBACK_DAYS[timeframe]
    visible_start = max(range_start, range_end - timedelta(days=lookback))
    # Warm-up before visible window so EMA is initialized.
    warm_start = visible_start - timedelta(days=max(lookback, 90))

    market = CanonicalStore(market_root or (PROJECT_ROOT / "data" / "market"))
    daily = CanonicalStore(daily_root or (PROJECT_ROOT / "data" / "market-daily"))
    requested = force_backend or requested_backend_from_env()

    candles: list[dict[str, Any]]
    ema18: list[dict[str, Any]]
    ema50: list[dict[str, Any]]
    ema90: list[dict[str, Any]]
    compute_provenance: dict[str, Any]
    source: str

    candles, ema18, ema50, ema90, compute_provenance, source = _series_with_phase_c(
        contract=contract,
        session_name=session_name,
        timeframe=timeframe,
        market=market,
        daily=daily,
        warm_start=warm_start,
        visible_start=visible_start,
        range_end=range_end,
        force_backend=force_backend,
        requested=requested,
    )

    events_payload = catalog.get_events(run_id)
    trades_payload = catalog.get_trades(run_id)
    events_raw = events_payload.get("events")
    trades_raw = trades_payload.get("trades")
    events: list[Any] = events_raw if isinstance(events_raw, list) else []
    trades: list[Any] = trades_raw if isinstance(trades_raw, list) else []

    lookback = resolve_lookback_days(timeframe, lookback_days)
    return {
        "schema": "chart_series.v1",
        "run_id": run_id,
        "timeframe": timeframe,
        "contract_id": contract_id,
        "session_name": session_name,
        # Cache-validation key: the chart is derived from canonical bars, so a
        # backfill must invalidate it even though the run itself is immutable.
        "data_fingerprint": data_fingerprint,
        "lookback_days": lookback,
        "visible_start": visible_start.isoformat().replace("+00:00", "Z"),
        "visible_end": range_end.isoformat().replace("+00:00", "Z"),
        "candles": candles,
        "ema18": ema18,
        "ema50": ema50,
        "ema90": ema90,
        "markers": _markers_from_events(events, trades),
        "levels": _levels_from_events(events, trades),
        "source": source,
        "compute_provenance": compute_provenance,
        "sidecar_relpath": (
            Path("chart") / f"{run_id}-{timeframe.replace('/', '_')}-lb{lookback}.json"
        ).as_posix(),
    }


_TF_MINUTES: dict[str, int] = {
    "5m": 5,
    "1H": 60,
    "D": 1,  # daily bars are already one bar per session day
}


def _series_with_phase_c(
    *,
    contract: ContractSpec,
    session_name: str,
    timeframe: str,
    market: CanonicalStore,
    daily: CanonicalStore,
    warm_start: datetime,
    visible_start: datetime,
    range_end: datetime,
    force_backend: BackendMode | None,
    requested: BackendMode | str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    str,
]:
    """Prefer Rust product path when policy allows; else Python MTF."""
    fallback_reason: str | None = None
    if product_chart_should_try_rust(force_backend=force_backend):
        try:
            return _series_via_rust(
                contract=contract,
                session_name=session_name,
                timeframe=timeframe,
                market=market,
                daily=daily,
                warm_start=warm_start,
                visible_start=visible_start,
                range_end=range_end,
                force_backend=force_backend,
            )
        except Exception as exc:  # FULL_JOB_PYTHON_FALLBACK — discard partial
            fallback_reason = f"rust_product_failed:{type(exc).__name__}:{exc}"
            _LOGGER.warning(
                "chart_series Phase C rust path failed; falling back to Python MTF (%s)",
                fallback_reason,
            )

    if timeframe == "D":
        candles, ema18, ema50, ema90 = _daily_series(
            contract,
            daily,
            session_name=session_name,
            warm_start=warm_start,
            visible_start=visible_start,
            range_end=range_end,
        )
        source = "backend_native_daily_mtf"
    else:
        candles, ema18, ema50, ema90 = _intraday_series(
            contract,
            market,
            session_name=session_name,
            timeframe=_TF_MAP[timeframe],
            warm_start=warm_start,
            visible_start=visible_start,
            range_end=range_end,
        )
        source = "backend_mtf_precompute"

    if fallback_reason is None and requested in {"auto", "accelerated"}:
        # Policy wanted acceleration but rust was not selected.
        fallback_reason = "rust_not_selected_use_python_mtf"

    provenance = python_mtf_provenance(
        requested=requested,
        fallback_reason=fallback_reason,
    )
    provenance["source"] = source
    return candles, ema18, ema50, ema90, provenance, source


def _series_via_rust(
    *,
    contract: ContractSpec,
    session_name: str,
    timeframe: str,
    market: CanonicalStore,
    daily: CanonicalStore,
    warm_start: datetime,
    visible_start: datetime,
    range_end: datetime,
    force_backend: BackendMode | None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    str,
]:
    """Load bars and materialize candles/EMA entirely through the Rust seam."""
    del session_name  # wall-clock resample for Phase C; session MTF remains Python fallback
    if timeframe == "D":
        bars: list[CanonicalBar] = list(
            daily.read(contract.contract_id, start=warm_start, end=range_end)
        )
        tf_minutes = 1
        source = "backend_rust_chart_compute_daily"
    else:
        bars = list(market.read(contract.contract_id, start=warm_start, end=range_end))
        tf_minutes = _TF_MINUTES[timeframe]
        source = "backend_rust_chart_compute"
    if not bars:
        prov = {
            "schema": "compute_provenance.v1",
            "feature": "chart_series_product",
            "requested_backend": force_backend or requested_backend_from_env(),
            "effective_backend": "rust",
            "fallback_reason": "empty_bars",
            "writes_authority": False,
        }
        return [], [], [], [], prov, source

    candles, ema18, ema50, ema90, provenance = build_product_series_via_rust(
        bars,
        timeframe_minutes=tf_minutes,
        visible_start=visible_start,
        range_end=range_end,
        force_backend=force_backend,
    )
    return candles, ema18, ema50, ema90, provenance, source


def build_preview_chart_series(
    *,
    contract: ContractSpec,
    session_name: str,
    range_start: datetime,
    range_end: datetime,
    data_fingerprint: str,
    events: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    market_store: CanonicalStore,
    daily_store: CanonicalStore | None,
    timeframe: Literal["D", "1H", "30m", "5m"],
) -> dict[str, object]:
    """Build one preview chart directly from the in-memory replay, never a sidecar.

    The field semantics intentionally mirror ``chart_series.v1``.  There is no
    ``run_id`` or ``sidecar_relpath`` because a preview must not look like a
    persisted P5 result.
    """
    preview_lookbacks = {"D": 200, "1H": 30, "30m": 20, "5m": 10}
    timeframe_map = {
        "D": Timeframe.D1,
        "1H": Timeframe.H1,
        "30m": Timeframe.M30,
        "5m": Timeframe.M5,
    }
    start = range_start.astimezone(UTC)
    end = range_end.astimezone(UTC)
    lookback = preview_lookbacks[timeframe]
    visible_start = max(start, end - timedelta(days=lookback))
    warm_start = visible_start - timedelta(days=max(lookback, 90))
    if timeframe == "D":
        if daily_store is None:
            candles: list[dict[str, Any]] = []
            ema18: list[dict[str, Any]] = []
            ema50: list[dict[str, Any]] = []
            ema90: list[dict[str, Any]] = []
        else:
            candles, ema18, ema50, ema90 = _daily_series(
                contract,
                daily_store,
                session_name=session_name,
                warm_start=warm_start,
                visible_start=visible_start,
                range_end=end,
            )
    else:
        candles, ema18, ema50, ema90 = _intraday_series(
            contract,
            market_store,
            session_name=session_name,
            timeframe=timeframe_map[timeframe],
            warm_start=warm_start,
            visible_start=visible_start,
            range_end=end,
        )
    return {
        "schema": "chart_series.v1",
        "timeframe": timeframe,
        "contract_id": contract.contract_id,
        "session_name": session_name,
        "data_fingerprint": data_fingerprint,
        "lookback_days": lookback,
        "visible_start": visible_start.isoformat().replace("+00:00", "Z"),
        "visible_end": end.isoformat().replace("+00:00", "Z"),
        "candles": candles,
        "ema18": ema18,
        "ema50": ema50,
        "ema90": ema90,
        "markers": _markers_from_events(events, trades),
        "levels": _levels_from_events(events, trades),
        "source": "backend_mtf_precompute" if timeframe != "D" else "backend_native_daily_mtf",
        "persisted": False,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"chart sidecar must be a JSON object: {path}"
        raise ValueError(msg)
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any] | dict[str, Any]) -> None:
    """Atomically write one chart sidecar (same family as result exporter)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def build_narrative(
    catalog: ResultsCatalog,
    run_id: str,
    *,
    limit: int = 40,
) -> dict[str, Any]:
    """Deterministic event → human steps (templates; no AI)."""
    events_payload = catalog.get_events(run_id)
    trades_payload = catalog.get_trades(run_id)
    events_raw = events_payload.get("events")
    trades_raw = trades_payload.get("trades")
    events: list[Any] = events_raw if isinstance(events_raw, list) else []
    trades: list[Any] = trades_raw if isinstance(trades_raw, list) else []
    steps = _narrative_steps(events, trades, limit=limit)
    return {
        "schema": "narrative.v1",
        "run_id": run_id,
        "count": len(steps),
        "steps": steps,
        "note": "deterministic templates from events sidecar; same source as golden tests",
    }


def _daily_series(
    contract: ContractSpec,
    daily_store: CanonicalStore,
    *,
    session_name: str,
    warm_start: datetime,
    visible_start: datetime,
    range_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    bars = daily_store.read(contract.contract_id, start=warm_start, end=range_end)
    series = build_native_daily_mtf_series(contract, bars, session_name=session_name)
    return _series_to_points(series, visible_start=visible_start, range_end=range_end)


def _intraday_series(
    contract: ContractSpec,
    market_store: CanonicalStore,
    *,
    session_name: str,
    timeframe: Timeframe,
    warm_start: datetime,
    visible_start: datetime,
    range_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    bars = market_store.read(contract.contract_id, start=warm_start, end=range_end)
    if not bars:
        return [], [], [], []
    precomp = MtfPrecomputer(contract, session_name=session_name).precompute(bars)
    series = precomp.series[timeframe]
    return _series_to_points(series, visible_start=visible_start, range_end=range_end)


def _series_to_points(
    series: MtfSeries,
    *,
    visible_start: datetime,
    range_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candles: list[dict[str, Any]] = []
    ema18: list[dict[str, Any]] = []
    ema50: list[dict[str, Any]] = []
    ema90: list[dict[str, Any]] = []
    for snapshot in series.snapshots:
        bar = snapshot.bar
        # Use bar open time as LWC candle time; filter visible window by close delivery.
        if bar.ts_init <= visible_start or bar.ts_init > range_end:
            continue
        time_unix = int(bar.timestamp.timestamp())
        candles.append(
            {
                "time": time_unix,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            }
        )
        if snapshot.is_ready:
            ema18.append({"time": time_unix, "value": snapshot.ema_18})
            ema50.append({"time": time_unix, "value": snapshot.ema_50})
            ema90.append({"time": time_unix, "value": snapshot.ema_90})
    return candles, ema18, ema50, ema90


def _markers_from_events(
    events: list[Any],
    trades: list[Any],
) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        ts = _parse_ts(event.get("timestamp"))
        if ts is None:
            continue
        time_unix = int(ts.timestamp())
        if event_type == "signal_created":
            markers.append(
                {
                    "time": time_unix,
                    "position": "belowBar",
                    "shape": "arrowUp",
                    "token": "chart-marker-entry",
                    "text": "signal",
                }
            )
        elif event_type in {"entry_filled", "entry_intent_created"}:
            markers.append(
                {
                    "time": time_unix,
                    "position": "belowBar",
                    "shape": "arrowUp",
                    "token": "chart-marker-entry",
                    "text": "entry",
                }
            )
        elif event_type in {"position_closed", "position_forced_closed"}:
            markers.append(
                {
                    "time": time_unix,
                    "position": "aboveBar",
                    "shape": "arrowDown",
                    "token": "chart-marker-exit",
                    "text": "exit",
                }
            )
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        entry_ts = _parse_ts(trade.get("entry_timestamp"))
        exit_ts = _parse_ts(trade.get("exit_timestamp"))
        if entry_ts is not None:
            markers.append(
                {
                    "time": int(entry_ts.timestamp()),
                    "position": "belowBar",
                    "shape": "arrowUp",
                    "token": "chart-marker-entry",
                    "text": "fill",
                }
            )
        if exit_ts is not None:
            markers.append(
                {
                    "time": int(exit_ts.timestamp()),
                    "position": "aboveBar",
                    "shape": "arrowDown",
                    "token": "chart-marker-exit",
                    "text": "flat",
                }
            )
    # Deduplicate by (time, text)
    seen: set[tuple[int, str]] = set()
    unique: list[dict[str, Any]] = []
    for marker in markers:
        key = (int(marker["time"]), str(marker["text"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(marker)
    return unique


def _levels_from_events(
    events: list[Any],
    trades: list[Any],
) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") != "signal_created":
            continue
        details_raw = event.get("details")
        details: dict[str, Any] = details_raw if isinstance(details_raw, dict) else {}
        price = event.get("price")
        stop = details.get("stop_reference")
        ts = _parse_ts(event.get("timestamp"))
        time_unix = int(ts.timestamp()) if ts is not None else None
        if isinstance(price, (int, float)):
            levels.append(
                {
                    "kind": "entry_ref",
                    "price": float(price),
                    "time": time_unix,
                    "token": "chart-marker-entry",
                }
            )
        if isinstance(stop, (int, float)):
            levels.append(
                {
                    "kind": "stop",
                    "price": float(stop),
                    "time": time_unix,
                    "token": "color-negative",
                }
            )
            if isinstance(price, (int, float)):
                # 1R target mirror for display when no fill (P1 risk default).
                risk = abs(float(price) - float(stop))
                direction = str(event.get("direction") or "long")
                target = float(price) + risk if direction == "long" else float(price) - risk
                levels.append(
                    {
                        "kind": "target",
                        "price": target,
                        "time": time_unix,
                        "token": "color-positive",
                    }
                )
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        for kind, key, token in (
            ("entry", "entry_price", "chart-marker-entry"),
            ("stop", "stop_price", "color-negative"),
            ("target", "target_price", "color-positive"),
            ("exit", "exit_price", "chart-marker-exit"),
        ):
            value = trade.get(key)
            if isinstance(value, (int, float)):
                levels.append({"kind": kind, "price": float(value), "time": None, "token": token})
    return levels


def _narrative_steps(
    events: list[Any],
    trades: list[Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        step = _template_event(event)
        if step is not None:
            steps.append(step)
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        steps.extend(_template_trade(trade))
    # Prefer structural / fill events over high-volume pullback noise.
    priority = {
        "Daily 閘": 0,
        "1H 層": 1,
        "5m 層": 2,
        "入市": 3,
        "離場": 4,
        "作廢": 5,
    }
    steps.sort(
        key=lambda item: (
            str(item.get("time") or ""),
            priority.get(str(item.get("layer") or ""), 9),
        )
    )
    if len(steps) > limit:
        # Keep first regime + last (limit-1) steps for recency bias on long runs.
        head = [steps[0]] if steps else []
        tail = steps[-(limit - 1) :] if limit > 1 else []
        merged = head + [item for item in tail if item not in head]
        steps = merged[:limit]
    return steps


def _template_event(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("event_type") or "")
    ts = _parse_ts(event.get("timestamp"))
    time_label = ts.strftime("%m-%d %H:%M") if ts is not None else "—"
    time_iso = ts.isoformat().replace("+00:00", "Z") if ts is not None else None
    direction = str(event.get("direction") or "")
    details_raw = event.get("details")
    details: dict[str, Any] = details_raw if isinstance(details_raw, dict) else {}
    price = event.get("price")
    machine = str(event.get("machine") or "")

    if event_type == "daily_regime_changed":
        to_state = str(event.get("to_state") or "—")
        return {
            "time": time_iso,
            "time_label": time_label,
            "layer": "Daily 閘",
            "tone": "ok" if to_state == "trend" else "neutral",
            "text": (
                f"Regime → {to_state}"
                + (f" · direction context: {direction}" if direction else "")
                + "（closed Daily bar 判定）"
            ),
        }
    if event_type == "mid_direction_changed":
        to_state = str(event.get("to_state") or "—")
        return {
            "time": time_iso,
            "time_label": time_label,
            "layer": "1H 層",
            "tone": "ok" if to_state in {"long", "short"} else "neutral",
            "text": f"中層方向 → {to_state}（18×90 cross state）",
        }
    if event_type == "pullback_touched" and machine in {"mid_pullback", "mid_direction", ""}:
        reason = str(details.get("reason") or "touch")
        return {
            "time": time_iso,
            "time_label": time_label,
            "layer": "1H 層",
            "tone": "ok",
            "text": f"第一次回踩 touch（{reason}）→ mid 合資格 gate",
        }
    if event_type == "pullback_touched" and "entry" in machine:
        reason = str(details.get("reason") or "touch")
        return {
            "time": time_iso,
            "time_label": time_label,
            "layer": "5m 層",
            "tone": "ok",
            "text": f"入市層回踩 touch（{reason}）",
        }
    if event_type == "signal_created":
        kind = str(details.get("signal_kind") or "signal")
        stop = details.get("stop_reference")
        price_txt = f" @ {price}" if isinstance(price, (int, float)) else ""
        stop_txt = f" · stop_ref {stop}" if isinstance(stop, (int, float)) else ""
        return {
            "time": time_iso,
            "time_label": time_label,
            "layer": "5m 層",
            "tone": "ok",
            "text": f"Signal created（{kind}{price_txt}{stop_txt}）· dir {direction or '—'}",
        }
    if event_type == "signal_cancelled":
        reason = str(details.get("reason") or event.get("to_state") or "cancelled")
        return {
            "time": time_iso,
            "time_label": time_label,
            "layer": "作廢",
            "tone": "warn",
            "text": f"Signal cancelled：{reason}",
        }
    if event_type == "signal_rejected":
        reason = str(details.get("reason") or "rejected")
        # Skip bulk daily_regime_range spam — only surface mid-layer / rare reasons.
        if reason.startswith("daily_regime"):
            return None
        return {
            "time": time_iso,
            "time_label": time_label,
            "layer": "5m 層",
            "tone": "warn",
            "text": f"Signal rejected：{reason}",
        }
    if event_type == "entry_filled":
        return {
            "time": time_iso,
            "time_label": time_label,
            "layer": "入市",
            "tone": "ok",
            "text": f"Entry filled{f' @ {price}' if price is not None else ''} · {direction}",
        }
    if event_type in {"position_closed", "position_forced_closed"}:
        return {
            "time": time_iso,
            "time_label": time_label,
            "layer": "離場",
            "tone": "ok",
            "text": f"{event_type}{f' @ {price}' if price is not None else ''}",
        }
    return None


def _template_trade(trade: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    entry_ts = _parse_ts(trade.get("entry_timestamp"))
    exit_ts = _parse_ts(trade.get("exit_timestamp"))
    direction = str(trade.get("direction") or "")
    entry_price = trade.get("entry_price")
    exit_price = trade.get("exit_price")
    stop = trade.get("stop_price")
    target = trade.get("target_price")
    net = trade.get("net_pnl")
    if entry_ts is not None:
        steps.append(
            {
                "time": entry_ts.isoformat().replace("+00:00", "Z"),
                "time_label": entry_ts.strftime("%m-%d %H:%M"),
                "layer": "入市",
                "tone": "ok",
                "text": (f"成交 {direction} @ {entry_price} · stop {stop} · target {target}"),
            }
        )
    if exit_ts is not None:
        steps.append(
            {
                "time": exit_ts.isoformat().replace("+00:00", "Z"),
                "time_label": exit_ts.strftime("%m-%d %H:%M"),
                "layer": "離場",
                "tone": "ok",
                "text": (
                    f"平倉 @ {exit_price} · net_pnl {net} · reason {trade.get('exit_reason')}"
                ),
            }
        )
    return steps


def _parse_ts(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:
        return None
