"""Strict, read-only validation for result.v1 mains used by irreversible exits."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal

from pydantic import ValidationError

from futures_research.backtest.records import (
    EngineMetadata,
    RunManifest,
    RunMetrics,
)

ScorecardStatus = Literal[
    "pass",
    "warn",
    "insufficient_sample",
    "not_available_p2",
]

_SCORECARD_FIELDS = {"dim", "status", "detail"}
_SCORECARD_STATUSES: frozenset[str] = frozenset(
    {"pass", "warn", "insufficient_sample", "not_available_p2"}
)
_COMPACT_METRICS_FIELDS = frozenset(RunMetrics.model_fields)
_ENRICHED_ONLY_METRICS_FIELDS = frozenset(
    {
        "max_drawdown_r",
        "payoff_ratio",
        "max_losing_streak",
        "dd_duration_trades",
        "calmar_r",
        "param_count",
        "trades_per_param",
        "rule_count",
        "skew",
        "kurtosis",
        "tail_ratio",
        "var95_r",
        "cvar95_r",
        "psr",
        "sharpe_per_trade",
        "profit_concentration",
        "cost_scenarios",
        "period_cuts",
    }
)
_ENRICHED_METRICS_FIELDS = _COMPACT_METRICS_FIELDS | _ENRICHED_ONLY_METRICS_FIELDS
_NULLABLE_ENRICHED_NUMBER_FIELDS = frozenset(
    {
        "max_drawdown_r",
        "payoff_ratio",
        "calmar_r",
        "trades_per_param",
        "skew",
        "kurtosis",
        "tail_ratio",
        "var95_r",
        "cvar95_r",
        "psr",
        "sharpe_per_trade",
    }
)
_NONNEGATIVE_ENRICHED_INTEGER_FIELDS = frozenset(
    {"max_losing_streak", "dd_duration_trades"}
)
_POSITIVE_ENRICHED_INTEGER_FIELDS = frozenset({"param_count", "rule_count"})
_PROFIT_CONCENTRATION_FIELDS = frozenset(
    {"top5_removed_net_r", "top10_removed_net_r"}
)
_COST_SCENARIO_MULTIPLIERS = {"x1": 1.0, "x1.5": 1.5, "x2": 2.0}
_COST_SCENARIO_FIELDS = frozenset(
    {"multiplier", "net_pnl", "net_r", "expectancy_r", "trade_count"}
)
_PERIOD_CUT_FIELDS = frozenset({"by_year", "by_month"})
_PERIOD_BUCKET_FIELDS = frozenset(
    {"trade_count", "net_r", "net_pnl", "expectancy_r"}
)
_YEAR_BUCKET = re.compile(r"^[0-9]{4}$")
_MONTH_BUCKET = re.compile(r"^([0-9]{4})-([0-9]{2})$")
_ENGINE_FIELDS = frozenset(EngineMetadata.model_fields)


class ResultMainValidationError(ValueError):
    """A result main is unsafe to export or capture in a decision."""


@dataclass(frozen=True, slots=True)
class ValidatedResultMain:
    """Strict facts shared by result export and PromotionDecision."""

    manifest: RunManifest
    strategy_id: str
    strategy_content_sha256: str
    engine: EngineMetadata
    metrics: RunMetrics
    scorecard: list[dict[str, object]]


def validate_result_main(
    document: dict[str, Any],
    *,
    expected_run_id: str,
) -> ValidatedResultMain:
    """Validate one persisted result.v1 main without normalizing or rewriting it."""
    if document.get("schema") != "result.v1":
        raise ResultMainValidationError("result main requires schema result.v1")

    run = document.get("run")
    if not isinstance(run, dict):
        raise ResultMainValidationError("result main requires a run object")
    if run.get("run_id") != expected_run_id:
        raise ResultMainValidationError(
            "result main run.run_id must match the requested immutable run"
        )

    manifest_raw = run.get("manifest")
    if not isinstance(manifest_raw, dict):
        raise ResultMainValidationError("result main requires its locked run manifest")
    try:
        manifest_json = json.dumps(
            manifest_raw,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        manifest = RunManifest.model_validate_json(manifest_json, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ResultMainValidationError(
            f"result {expected_run_id} has an invalid locked run manifest: {exc}"
        ) from exc

    raw_manifest_run_id = manifest_raw.get("run_id")
    raw_manifest_strategy = _require_untrimmed_text(
        manifest_raw.get("strategy_version"),
        context=f"result {expected_run_id} manifest.strategy_version",
    )
    raw_manifest_contract = _require_untrimmed_text(
        manifest_raw.get("contract_id"),
        context=f"result {expected_run_id} manifest.contract_id",
    )
    raw_manifest_session = _require_untrimmed_text(
        manifest_raw.get("session_name"),
        context=f"result {expected_run_id} manifest.session_name",
    )
    raw_run_strategy = _require_untrimmed_text(
        run.get("strategy_version"),
        context=f"result {expected_run_id} run.strategy_version",
    )
    if (
        raw_manifest_run_id != expected_run_id
        or manifest.run_id != raw_manifest_run_id
        or raw_run_strategy != raw_manifest_strategy
        or manifest.strategy_version != raw_manifest_strategy
        or manifest.contract_id != raw_manifest_contract
        or manifest.session_name != raw_manifest_session
    ):
        raise ResultMainValidationError(
            f"result {expected_run_id} run identity does not exactly match "
            "its locked manifest"
        )

    binding = manifest.strategy_binding
    if binding is None:
        raise ResultMainValidationError(
            f"result {expected_run_id} lacks one exact strategy_file binding "
            "matching its locked strategy version"
        )
    strategy_id = _require_untrimmed_text(
        binding.strategy_id,
        context=f"result {expected_run_id} strategy_binding.strategy_id",
    )
    strategy_content_sha256 = binding.content_sha256
    if (
        binding.source != "strategy_file"
        or strategy_id != raw_manifest_strategy
        or strategy_content_sha256 is None
    ):
        raise ResultMainValidationError(
            f"result {expected_run_id} lacks one exact strategy_file binding "
            "matching its locked strategy version"
        )

    engine_raw = run.get("engine")
    if not isinstance(engine_raw, dict) or set(engine_raw) != _ENGINE_FIELDS:
        raise ResultMainValidationError(
            f"result {expected_run_id} engine must contain exactly "
            f"{sorted(_ENGINE_FIELDS)}"
        )
    for field in sorted(_ENGINE_FIELDS):
        _require_untrimmed_text(
            engine_raw.get(field),
            context=f"result {expected_run_id} engine.{field}",
        )
    try:
        engine = EngineMetadata.model_validate(engine_raw, strict=True)
    except ValidationError as exc:
        raise ResultMainValidationError(
            f"result {expected_run_id} has invalid engine metadata: {exc}"
        ) from exc

    metrics = _validate_metrics(document.get("metrics"), expected_run_id=expected_run_id)

    scorecard = validate_scorecard(
        document.get("scorecard"),
        context=f"result {expected_run_id} scorecard",
    )
    return ValidatedResultMain(
        manifest=manifest,
        strategy_id=strategy_id,
        strategy_content_sha256=strategy_content_sha256,
        engine=engine,
        metrics=metrics,
        scorecard=scorecard,
    )


def _validate_metrics(value: object, *, expected_run_id: str) -> RunMetrics:
    context = f"result {expected_run_id} metrics"
    if not isinstance(value, dict):
        raise ResultMainValidationError(f"{context} must be an object")

    fields = frozenset(value)
    if fields == _COMPACT_METRICS_FIELDS:
        compact = value
        enriched = False
    elif fields == _ENRICHED_METRICS_FIELDS:
        compact = {field: value[field] for field in _COMPACT_METRICS_FIELDS}
        enriched = True
    else:
        raise ResultMainValidationError(
            f"{context} keys must equal exactly compact "
            f"{sorted(_COMPACT_METRICS_FIELDS)} or enriched "
            f"{sorted(_ENRICHED_METRICS_FIELDS)}"
        )

    try:
        metrics = RunMetrics.model_validate(compact, strict=True)
    except ValidationError as exc:
        raise ResultMainValidationError(
            f"result {expected_run_id} has invalid compact metrics: {exc}"
        ) from exc

    if enriched:
        _validate_enriched_metrics(value, compact=metrics, context=context)
    return metrics


def _validate_enriched_metrics(
    value: dict[object, object],
    *,
    compact: RunMetrics,
    context: str,
) -> None:
    integer_values = {
        field: _require_json_integer(
            value[field],
            context=f"{context}.{field}",
            minimum=0,
        )
        for field in _NONNEGATIVE_ENRICHED_INTEGER_FIELDS
    }
    for field, number in integer_values.items():
        if number > compact.trade_count:
            raise ResultMainValidationError(
                f"{context}.{field} must not exceed metrics.trade_count"
            )

    for field in _POSITIVE_ENRICHED_INTEGER_FIELDS:
        _require_json_integer(
            value[field],
            context=f"{context}.{field}",
            minimum=1,
        )

    for field in _NULLABLE_ENRICHED_NUMBER_FIELDS:
        _require_nullable_finite_number(value[field], context=f"{context}.{field}")
    trades_per_param = value["trades_per_param"]
    if compact.trade_count == 0 and trades_per_param is not None:
        raise ResultMainValidationError(
            f"{context}.trades_per_param must be null when trade_count is zero"
        )
    if compact.trade_count > 0 and trades_per_param is None:
        raise ResultMainValidationError(
            f"{context}.trades_per_param must be finite when trade_count is nonzero"
        )

    _validate_profit_concentration(
        value["profit_concentration"],
        context=f"{context}.profit_concentration",
    )
    _validate_cost_scenarios(
        value["cost_scenarios"],
        compact=compact,
        context=f"{context}.cost_scenarios",
    )
    _validate_period_cuts(
        value["period_cuts"],
        trade_count=compact.trade_count,
        context=f"{context}.period_cuts",
    )


def _validate_profit_concentration(value: object, *, context: str) -> None:
    concentration = _require_exact_object(
        value,
        fields=_PROFIT_CONCENTRATION_FIELDS,
        context=context,
    )
    for field in _PROFIT_CONCENTRATION_FIELDS:
        _require_nullable_finite_number(
            concentration[field],
            context=f"{context}.{field}",
        )


def _validate_cost_scenarios(
    value: object,
    *,
    compact: RunMetrics,
    context: str,
) -> None:
    scenarios = _require_exact_object(
        value,
        fields=frozenset(_COST_SCENARIO_MULTIPLIERS),
        context=context,
    )
    for scenario_name, expected_multiplier in _COST_SCENARIO_MULTIPLIERS.items():
        scenario_context = f"{context}.{scenario_name}"
        scenario = _require_exact_object(
            scenarios[scenario_name],
            fields=_COST_SCENARIO_FIELDS,
            context=scenario_context,
        )
        multiplier = _require_finite_number(
            scenario["multiplier"],
            context=f"{scenario_context}.multiplier",
        )
        if multiplier != expected_multiplier:
            raise ResultMainValidationError(
                f"{scenario_context}.multiplier must exactly equal "
                f"{expected_multiplier:g}"
            )
        net_pnl = _require_finite_number(
            scenario["net_pnl"],
            context=f"{scenario_context}.net_pnl",
        )
        net_r = _require_finite_number(
            scenario["net_r"],
            context=f"{scenario_context}.net_r",
        )
        expectancy = scenario["expectancy_r"]
        if compact.trade_count == 0:
            if expectancy is not None:
                raise ResultMainValidationError(
                    f"{scenario_context}.expectancy_r must be null when "
                    "trade_count is zero"
                )
        else:
            _require_finite_number(
                expectancy,
                context=f"{scenario_context}.expectancy_r",
            )
        scenario_trade_count = _require_json_integer(
            scenario["trade_count"],
            context=f"{scenario_context}.trade_count",
            minimum=0,
        )
        if scenario_trade_count != compact.trade_count:
            raise ResultMainValidationError(
                f"{scenario_context}.trade_count must exactly equal "
                "metrics.trade_count"
            )
        if scenario_name == "x1":
            _require_equal(
                net_pnl,
                compact.net_pnl,
                context=f"{scenario_context}.net_pnl",
                expected="metrics.net_pnl",
            )
            _require_equal(
                net_r,
                compact.net_r,
                context=f"{scenario_context}.net_r",
                expected="metrics.net_r",
            )
            _require_equal(
                expectancy,
                compact.expectancy_r,
                context=f"{scenario_context}.expectancy_r",
                expected="metrics.expectancy_r",
            )


def _validate_period_cuts(
    value: object,
    *,
    trade_count: int,
    context: str,
) -> None:
    cuts = _require_exact_object(value, fields=_PERIOD_CUT_FIELDS, context=context)
    by_year = _require_object(cuts["by_year"], context=f"{context}.by_year")
    by_month = _require_object(cuts["by_month"], context=f"{context}.by_month")
    if trade_count == 0:
        if by_year or by_month:
            raise ResultMainValidationError(
                f"{context}.by_year and by_month must both be empty when "
                "trade_count is zero"
            )
        return

    year_count = _validate_period_buckets(
        by_year,
        grain="year",
        context=f"{context}.by_year",
    )
    month_count = _validate_period_buckets(
        by_month,
        grain="month",
        context=f"{context}.by_month",
    )
    if year_count != trade_count:
        raise ResultMainValidationError(
            f"{context}.by_year trade_count sum must exactly equal "
            "metrics.trade_count"
        )
    if month_count != trade_count:
        raise ResultMainValidationError(
            f"{context}.by_month trade_count sum must exactly equal "
            "metrics.trade_count"
        )


def _validate_period_buckets(
    buckets: dict[object, object],
    *,
    grain: Literal["year", "month"],
    context: str,
) -> int:
    total = 0
    for label, value in buckets.items():
        if not isinstance(label, str) or not _valid_period_label(label, grain=grain):
            expected = "YYYY" if grain == "year" else "YYYY-MM"
            raise ResultMainValidationError(
                f"{context} key {label!r} must be an exact valid {expected}"
            )
        bucket_context = f"{context}.{label}"
        bucket = _require_exact_object(
            value,
            fields=_PERIOD_BUCKET_FIELDS,
            context=bucket_context,
        )
        count = _require_json_integer(
            bucket["trade_count"],
            context=f"{bucket_context}.trade_count",
            minimum=1,
        )
        total += count
        for field in ("net_r", "net_pnl", "expectancy_r"):
            _require_finite_number(
                bucket[field],
                context=f"{bucket_context}.{field}",
            )
    return total


def _valid_period_label(value: str, *, grain: Literal["year", "month"]) -> bool:
    if grain == "year":
        return _YEAR_BUCKET.fullmatch(value) is not None and int(value) >= 1
    match = _MONTH_BUCKET.fullmatch(value)
    if match is None:
        return False
    year, month = match.groups()
    return int(year) >= 1 and 1 <= int(month) <= 12


def _require_object(value: object, *, context: str) -> dict[object, object]:
    if not isinstance(value, dict):
        raise ResultMainValidationError(f"{context} must be an object")
    return value


def _require_exact_object(
    value: object,
    *,
    fields: frozenset[str],
    context: str,
) -> dict[object, object]:
    result = _require_object(value, context=context)
    if frozenset(result) != fields:
        raise ResultMainValidationError(
            f"{context} must contain exactly {sorted(fields)}"
        )
    return result


def _require_json_integer(
    value: object,
    *,
    context: str,
    minimum: int,
) -> int:
    if type(value) is not int or value < minimum:
        qualifier = "non-negative" if minimum == 0 else "positive"
        raise ResultMainValidationError(
            f"{context} must be a {qualifier} JSON integer"
        )
    return value


def _require_finite_number(value: object, *, context: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResultMainValidationError(f"{context} must be a finite JSON number")
    if isinstance(value, float) and not isfinite(value):
        raise ResultMainValidationError(f"{context} must be a finite JSON number")
    return value


def _require_nullable_finite_number(value: object, *, context: str) -> None:
    if value is not None:
        _require_finite_number(value, context=context)


def _require_equal(
    actual: object,
    expected_value: object,
    *,
    context: str,
    expected: str,
) -> None:
    if actual != expected_value:
        raise ResultMainValidationError(f"{context} must exactly equal {expected}")


def _require_untrimmed_text(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ResultMainValidationError(f"{context} must be non-blank untrimmed text")
    return value


def validate_scorecard(
    value: object,
    *,
    context: str,
) -> list[dict[str, object]]:
    """Return a defensive copy of one exact, finite result.v1 scorecard."""
    if not isinstance(value, list) or not value:
        raise ResultMainValidationError(f"{context} must be a non-empty list")

    dimensions: set[str] = set()
    validated: list[dict[str, object]] = []
    for ordinal, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) != _SCORECARD_FIELDS:
            raise ResultMainValidationError(
                f"{context} item {ordinal} must contain exactly dim/status/detail"
            )

        dim = item.get("dim")
        if not isinstance(dim, str) or not dim or dim != dim.strip():
            raise ResultMainValidationError(
                f"{context} item {ordinal} dim must be non-blank untrimmed text"
            )
        if dim in dimensions:
            raise ResultMainValidationError(f"{context} has duplicate dim {dim!r}")
        dimensions.add(dim)

        status = item.get("status")
        if not isinstance(status, str) or status not in _SCORECARD_STATUSES:
            raise ResultMainValidationError(
                f"{context} item {ordinal} has an unknown status"
            )

        detail = item.get("detail")
        if not isinstance(detail, dict):
            raise ResultMainValidationError(
                f"{context} item {ordinal} detail must be an object"
            )
        _assert_finite_json(detail, path=f"{context}[{ordinal}].detail")
        validated.append(deepcopy(item))
    return validated


def _assert_finite_json(value: object, *, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ResultMainValidationError(f"{path} must contain finite JSON numbers")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite_json(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ResultMainValidationError(f"{path} keys must be JSON strings")
            _assert_finite_json(item, path=f"{path}.{key}")
        return
    raise ResultMainValidationError(f"{path} must contain only JSON values")
