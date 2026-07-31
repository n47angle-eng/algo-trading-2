"""Python oracle for chart_compute_v1 — pure bars → resample + EMA.

Rust must match this algorithm bit-for-bit on finite IEEE floats with
round-half-even serialization via shared wire helpers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from futures_research.contracts.chart_compute import (
    ChartBar,
    ChartComputeDraft,
    ChartComputeRequest,
    ChartPoint,
)


def compute_chart_python(request: ChartComputeRequest) -> ChartComputeDraft:
    points = _resample_and_ema(
        bars=request.bars,
        timeframe_minutes=request.timeframe_minutes,
        ema_period=request.ema_period,
    )
    payload = {
        "schema": "chart_compute_draft.v1",
        "timeframe_minutes": request.timeframe_minutes,
        "ema_period": request.ema_period,
        "points": [p.model_dump(mode="json") for p in points],
    }
    artifact = _canonical_json_bytes(payload)
    digest = sha256(artifact).hexdigest()
    return ChartComputeDraft.model_validate(
        {
            "schema": "chart_compute_draft.v1",
            "timeframe_minutes": request.timeframe_minutes,
            "ema_period": request.ema_period,
            "points": [p.model_dump(mode="json") for p in points],
            "artifact_sha256": digest,
            "provenance": {
                "schema": "compute_provenance.v1",
                "feature": "chart_compute_v1",
                "requested_backend": request.requested_backend,
                "effective_backend": "python",
                "fallback_reason": None,
                "artifact_sha256": digest,
                "writes_authority": False,
            },
        }
    )


def bars_from_dicts(rows: list[dict[str, object]]) -> list[ChartBar]:
    return [ChartBar.model_validate(row) for row in rows]


def _resample_and_ema(
    *,
    bars: list[ChartBar] | tuple[ChartBar, ...],
    timeframe_minutes: int,
    ema_period: int,
) -> list[ChartPoint]:
    if not bars:
        return []
    bucket_seconds = timeframe_minutes * 60
    buckets: dict[int, list[ChartBar]] = {}
    for bar in bars:
        ts = _parse_utc(bar.t)
        epoch = int(ts.timestamp())
        bucket = epoch - (epoch % bucket_seconds)
        buckets.setdefault(bucket, []).append(bar)

    points: list[ChartPoint] = []
    ema: float | None = None
    alpha = 2.0 / (ema_period + 1.0)
    for bucket in sorted(buckets):
        group = buckets[bucket]
        o = group[0].o
        h = max(b.h for b in group)
        l = min(b.l for b in group)
        c = group[-1].c
        v = sum(b.v for b in group)
        if ema is None:
            ema = c
        else:
            ema = c * alpha + ema * (1.0 - alpha)
        t = datetime.fromtimestamp(bucket, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        points.append(
            ChartPoint(t=t, o=o, h=h, l=l, c=c, v=v, ema=float(ema))
        )
    return points


def _parse_utc(value: str) -> datetime:
    text = value.removesuffix("Z") + "+00:00"
    return datetime.fromisoformat(text)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
