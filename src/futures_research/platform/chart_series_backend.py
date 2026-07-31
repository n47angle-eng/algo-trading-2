"""Phase C: product chart candles/EMA via chart_compute seam (prefer Rust).

Python product fallback is the legacy MTF path in ``api/chart_series.py`` —
this module only builds candle + multi-EMA series from frozen bars and never
writes authority stores.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from futures_research.contracts.chart_compute import ChartBar, ChartComputeRequest
from futures_research.data.models import CanonicalBar
from futures_research.platform.backend_policy import (
    BackendMode,
    requested_backend_from_env,
    resolve_compute_backend,
)
from futures_research.platform.chart_compute_seam import chart_compute_v1

_EMA_PERIODS: tuple[int, int, int] = (18, 50, 90)

TimeframeMinutes = Literal[1, 5, 30, 60, 1440]


def product_chart_should_try_rust(*, force_backend: BackendMode | None = None) -> bool:
    """True when product chart materialize should attempt the Rust seam first."""
    requested = force_backend or requested_backend_from_env()
    if requested == "stable":
        return False
    # Probe availability the same way the seam does (without computing).
    from futures_research.platform.chart_compute_seam import _try_load_rust

    rust_ok, _reason = _try_load_rust()
    effective, _fallback = resolve_compute_backend(
        feature="chart_series_product",
        requested=requested,
        rust_available=rust_ok,
        rust_qualified=rust_ok,
        authority_path=False,
    )
    return effective == "rust"


def canonical_to_chart_bars(bars: list[CanonicalBar] | tuple[CanonicalBar, ...]) -> list[ChartBar]:
    """Map canonical 1m (or daily) bars into chart_compute wire bars."""
    out: list[ChartBar] = []
    for bar in bars:
        ts = bar.timestamp.astimezone(UTC)
        out.append(
            ChartBar(
                t=ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                o=float(bar.open),
                h=float(bar.high),
                l=float(bar.low),
                c=float(bar.close),
                v=float(bar.volume),
            )
        )
    return out


def build_product_series_via_rust(
    bars: list[CanonicalBar] | tuple[CanonicalBar, ...],
    *,
    timeframe_minutes: int,
    visible_start: datetime,
    range_end: datetime,
    force_backend: BackendMode | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Build candles + ema18/50/90 using Rust via the chart_compute seam.

    Raises on failure so the caller can full-job fall back to the Python MTF path.
    """
    if timeframe_minutes < 1 or timeframe_minutes > 1440:
        raise ValueError(f"unsupported timeframe_minutes={timeframe_minutes}")
    if not bars:
        empty_prov = {
            "schema": "compute_provenance.v1",
            "feature": "chart_series_product",
            "requested_backend": force_backend or requested_backend_from_env(),
            "effective_backend": "rust",
            "fallback_reason": None,
            "writes_authority": False,
        }
        return [], [], [], [], empty_prov

    chart_bars = canonical_to_chart_bars(bars)
    requested: BackendMode = force_backend or requested_backend_from_env()
    # Force accelerated so the seam prefers Rust; caller already gated on rust.
    drafts = []
    for period in _EMA_PERIODS:
        req = ChartComputeRequest.model_validate(
            {
                "schema": "chart_compute_request.v1",
                "timeframe_minutes": timeframe_minutes,
                "ema_period": period,
                "bars": [b.model_dump(mode="json") for b in chart_bars],
                "requested_backend": "accelerated",
            }
        )
        draft = chart_compute_v1(req, force_backend="accelerated")
        if draft.provenance.get("effective_backend") != "rust":
            raise RuntimeError(
                f"rust chart path unavailable: {draft.provenance.get('fallback_reason')}"
            )
        drafts.append(draft)

    draft_18, draft_50, draft_90 = drafts
    # OHLC from first draft (same resample for all periods).
    visible_start_utc = visible_start.astimezone(UTC)
    range_end_utc = range_end.astimezone(UTC)

    candles: list[dict[str, Any]] = []
    ema18: list[dict[str, Any]] = []
    ema50: list[dict[str, Any]] = []
    ema90: list[dict[str, Any]] = []

    points_18 = draft_18.points
    points_50 = draft_50.points
    points_90 = draft_90.points
    if not (len(points_18) == len(points_50) == len(points_90)):
        raise RuntimeError("rust multi-EMA drafts have mismatched point counts")

    for idx, point in enumerate(points_18):
        ts = _parse_z(point.t)
        # Match legacy chart window: open time inside (visible_start, range_end].
        if ts <= visible_start_utc or ts > range_end_utc:
            continue
        time_unix = int(ts.timestamp())
        candles.append(
            {
                "time": time_unix,
                "open": point.o,
                "high": point.h,
                "low": point.l,
                "close": point.c,
            }
        )
        # Ready after enough resampled bars for each period (Nautilus-style warm-up).
        if idx + 1 >= _EMA_PERIODS[0]:
            ema18.append({"time": time_unix, "value": point.ema})
        if idx + 1 >= _EMA_PERIODS[1]:
            ema50.append({"time": time_unix, "value": points_50[idx].ema})
        if idx + 1 >= _EMA_PERIODS[2]:
            ema90.append({"time": time_unix, "value": points_90[idx].ema})

    provenance = dict(draft_18.provenance)
    provenance["feature"] = "chart_series_product"
    provenance["requested_backend"] = requested
    provenance["effective_backend"] = "rust"
    provenance["writes_authority"] = False
    provenance["ema_periods"] = list(_EMA_PERIODS)
    provenance["point_count"] = len(points_18)
    return candles, ema18, ema50, ema90, provenance


def python_mtf_provenance(
    *,
    requested: BackendMode | str,
    fallback_reason: str | None,
) -> dict[str, Any]:
    """Provenance when product chart used the legacy Python MTF path."""
    return {
        "schema": "compute_provenance.v1",
        "feature": "chart_series_product",
        "requested_backend": requested,
        "effective_backend": "python",
        "fallback_reason": fallback_reason,
        "writes_authority": False,
        "source": "backend_mtf_precompute",
    }


def _parse_z(value: str) -> datetime:
    text = value.removesuffix("Z") + "+00:00"
    return datetime.fromisoformat(text).astimezone(UTC)
