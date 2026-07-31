"""Phase F: same-window shadow compare of Rust chart vs Python MTF reference.

Does not write authority stores. Reports are pure analytics for value-gating.
"""

from __future__ import annotations

from typing import Any, Literal

ShadowVerdict = Literal["PASS", "DIFF", "SKIPPED", "INCOMPARABLE"]

# Absolute price tolerance for OHLC / EMA (futures-scale; NQ ~ 0.25 tick).
_DEFAULT_ABS_EPS = 1e-6
# Relative soft band used only for summary ratios (not pass/fail alone).
_DEFAULT_REL_EPS = 1e-9


def compare_chart_series_payloads(
    *,
    reference: dict[str, Any],
    candidate: dict[str, Any],
    abs_eps: float = _DEFAULT_ABS_EPS,
    rel_eps: float = _DEFAULT_REL_EPS,
) -> dict[str, Any]:
    """Diff two ``chart_series.v1`` payloads over the shared visible window.

    ``reference`` is the Python MTF product path; ``candidate`` is Rust (or other).
    """
    ref_candles = _index_candles(reference.get("candles"))
    cand_candles = _index_candles(candidate.get("candles"))
    shared_times = sorted(set(ref_candles) & set(cand_candles))
    only_ref = sorted(set(ref_candles) - set(cand_candles))
    only_cand = sorted(set(cand_candles) - set(ref_candles))

    ohlc = _diff_fields(
        shared_times,
        ref_candles,
        cand_candles,
        fields=("open", "high", "low", "close"),
        abs_eps=abs_eps,
        rel_eps=rel_eps,
    )

    ema_reports: dict[str, Any] = {}
    for key in ("ema18", "ema50", "ema90"):
        ref_line = _index_line(reference.get(key))
        cand_line = _index_line(candidate.get(key))
        shared_ema = sorted(set(ref_line) & set(cand_line))
        ema_reports[key] = {
            "ref_count": len(ref_line),
            "candidate_count": len(cand_line),
            "shared_count": len(shared_ema),
            "only_in_reference": len(set(ref_line) - set(cand_line)),
            "only_in_candidate": len(set(cand_line) - set(ref_line)),
            **_diff_scalar_maps(
                shared_ema,
                ref_line,
                cand_line,
                abs_eps=abs_eps,
                rel_eps=rel_eps,
            ),
        }

    max_ohlc = ohlc["max_abs_diff"]
    max_ema = max(
        (float(ema_reports[k]["max_abs_diff"]) for k in ema_reports),
        default=0.0,
    )
    timeline_mismatch = bool(only_ref or only_cand)

    if not shared_times and (ref_candles or cand_candles):
        verdict: ShadowVerdict = "INCOMPARABLE"
    elif timeline_mismatch or max_ohlc > abs_eps or max_ema > abs_eps:
        verdict = "DIFF"
    else:
        verdict = "PASS"

    return {
        "schema": "chart_shadow_compare.v1",
        "verdict": verdict,
        "abs_eps": abs_eps,
        "rel_eps": rel_eps,
        "timeframe": reference.get("timeframe") or candidate.get("timeframe"),
        "run_id": reference.get("run_id") or candidate.get("run_id"),
        "reference": {
            "source": reference.get("source"),
            "compute_provenance": reference.get("compute_provenance"),
            "candle_count": len(ref_candles),
        },
        "candidate": {
            "source": candidate.get("source"),
            "compute_provenance": candidate.get("compute_provenance"),
            "candle_count": len(cand_candles),
        },
        "timeline": {
            "shared_count": len(shared_times),
            "only_in_reference_count": len(only_ref),
            "only_in_candidate_count": len(only_cand),
            "only_in_reference_sample": only_ref[:8],
            "only_in_candidate_sample": only_cand[:8],
        },
        "ohlc": ohlc,
        "ema": ema_reports,
        "summary": {
            "max_abs_ohlc_diff": max_ohlc,
            "max_abs_ema_diff": max_ema,
            "timeline_mismatch": timeline_mismatch,
            "pass_exact_within_eps": verdict == "PASS",
        },
    }


def _index_candles(raw: object) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            t = int(item["time"])
            out[t] = {
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _index_line(raw: object) -> dict[int, float]:
    out: dict[int, float] = {}
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out[int(item["time"])] = float(item["value"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _diff_fields(
    times: list[int],
    ref: dict[int, dict[str, float]],
    cand: dict[int, dict[str, float]],
    *,
    fields: tuple[str, ...],
    abs_eps: float,
    rel_eps: float,
) -> dict[str, Any]:
    max_abs = 0.0
    sum_abs = 0.0
    n = 0
    worst: dict[str, Any] | None = None
    within = 0
    for t in times:
        for field in fields:
            a = ref[t][field]
            b = cand[t][field]
            diff = abs(a - b)
            max_abs = max(max_abs, diff)
            sum_abs += diff
            n += 1
            if diff <= abs_eps or (
                abs(a) > 0 and diff / abs(a) <= rel_eps
            ):
                within += 1
            if worst is None or diff > float(worst["abs_diff"]):
                worst = {
                    "time": t,
                    "field": field,
                    "reference": a,
                    "candidate": b,
                    "abs_diff": diff,
                }
    return {
        "compared_values": n,
        "max_abs_diff": max_abs,
        "mean_abs_diff": (sum_abs / n) if n else 0.0,
        "within_eps_count": within,
        "within_eps_ratio": (within / n) if n else 1.0,
        "worst": worst,
    }


def _diff_scalar_maps(
    times: list[int],
    ref: dict[int, float],
    cand: dict[int, float],
    *,
    abs_eps: float,
    rel_eps: float,
) -> dict[str, Any]:
    max_abs = 0.0
    sum_abs = 0.0
    n = 0
    within = 0
    worst: dict[str, Any] | None = None
    for t in times:
        a = ref[t]
        b = cand[t]
        diff = abs(a - b)
        max_abs = max(max_abs, diff)
        sum_abs += diff
        n += 1
        if diff <= abs_eps or (abs(a) > 0 and diff / abs(a) <= rel_eps):
            within += 1
        if worst is None or diff > float(worst["abs_diff"]):
            worst = {
                "time": t,
                "reference": a,
                "candidate": b,
                "abs_diff": diff,
            }
    return {
        "compared_values": n,
        "max_abs_diff": max_abs,
        "mean_abs_diff": (sum_abs / n) if n else 0.0,
        "within_eps_count": within,
        "within_eps_ratio": (within / n) if n else 1.0,
        "worst": worst,
    }
