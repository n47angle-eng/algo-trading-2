"""Python oracle for backtest_loop_v1 — dual-EMA closed-bar long-only loop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from futures_research.contracts.backtest_loop import (
    BacktestLoopDraft,
    BacktestLoopRequest,
    LoopBar,
    LoopEquityPoint,
    LoopTrade,
)


def compute_backtest_loop_python(request: BacktestLoopRequest) -> BacktestLoopDraft:
    points = _resample(request.bars, request.timeframe_minutes)
    trades, equity, net = _run_loop(
        points,
        ema_fast=request.ema_fast,
        ema_slow=request.ema_slow,
        quantity=request.quantity,
        point_value=request.point_value,
        commission_per_side=request.commission_per_side,
    )
    payload = {
        "schema": "backtest_loop_draft.v1",
        "timeframe_minutes": request.timeframe_minutes,
        "ema_fast": request.ema_fast,
        "ema_slow": request.ema_slow,
        "trades": [t.model_dump(mode="json") for t in trades],
        "equity_curve": [e.model_dump(mode="json") for e in equity],
        "trade_count": len(trades),
        "net_pnl": net,
    }
    digest = sha256(_canonical_json_bytes(payload)).hexdigest()
    return BacktestLoopDraft.model_validate(
        {
            **payload,
            "artifact_sha256": digest,
            "provenance": {
                "schema": "compute_provenance.v1",
                "feature": "backtest_loop_v1",
                "requested_backend": request.requested_backend,
                "effective_backend": "python",
                "fallback_reason": None,
                "artifact_sha256": digest,
                "writes_authority": False,
            },
        }
    )


def _resample(bars: list[LoopBar], timeframe_minutes: int) -> list[tuple[str, float]]:
    if not bars:
        return []
    bucket_seconds = timeframe_minutes * 60
    buckets: dict[int, list[LoopBar]] = {}
    for bar in bars:
        epoch = int(_parse_utc(bar.t).timestamp())
        bucket = epoch - (epoch % bucket_seconds)
        buckets.setdefault(bucket, []).append(bar)
    out: list[tuple[str, float]] = []
    for bucket in sorted(buckets):
        group = buckets[bucket]
        t = datetime.fromtimestamp(bucket, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append((t, group[-1].c))
    return out


def _run_loop(
    points: list[tuple[str, float]],
    *,
    ema_fast: int,
    ema_slow: int,
    quantity: int,
    point_value: float,
    commission_per_side: float,
) -> tuple[list[LoopTrade], list[LoopEquityPoint], float]:
    if not points:
        return [], [], 0.0
    af = 2.0 / (ema_fast + 1.0)
    as_ = 2.0 / (ema_slow + 1.0)
    fast: float | None = None
    slow: float | None = None
    position: tuple[str, float] | None = None
    trades: list[LoopTrade] = []
    equity_curve: list[LoopEquityPoint] = []
    realized = 0.0
    prev_cross: int | None = None

    for idx, (t, c) in enumerate(points):
        fast = c if fast is None else c * af + fast * (1.0 - af)
        slow = c if slow is None else c * as_ + slow * (1.0 - as_)
        assert fast is not None and slow is not None

        if idx + 1 < ema_slow:
            equity_curve.append(LoopEquityPoint(t=t, equity=realized))
            continue

        if fast > slow:
            cross = 1
        elif fast < slow:
            cross = -1
        else:
            cross = prev_cross if prev_cross is not None else 0

        if prev_cross is not None:
            if prev_cross <= 0 and cross > 0 and position is None:
                position = (t, c)
            elif prev_cross >= 0 and cross < 0 and position is not None:
                entry_t, entry_px = position
                position = None
                gross = c - entry_px
                net = gross * point_value * quantity - 2.0 * commission_per_side * quantity
                realized += net
                trades.append(
                    LoopTrade(
                        entry_t=entry_t,
                        exit_t=t,
                        direction="long",
                        entry_price=entry_px,
                        exit_price=c,
                        quantity=quantity,
                        gross_points=gross,
                        net_pnl=net,
                    )
                )
        prev_cross = cross
        equity_curve.append(LoopEquityPoint(t=t, equity=realized))

    if position is not None and points:
        entry_t, entry_px = position
        t, c = points[-1]
        gross = c - entry_px
        net = gross * point_value * quantity - 2.0 * commission_per_side * quantity
        realized += net
        trades.append(
            LoopTrade(
                entry_t=entry_t,
                exit_t=t,
                direction="long",
                entry_price=entry_px,
                exit_price=c,
                quantity=quantity,
                gross_points=gross,
                net_pnl=net,
            )
        )
        if equity_curve:
            equity_curve[-1] = LoopEquityPoint(t=equity_curve[-1].t, equity=realized)

    return trades, equity_curve, realized


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
