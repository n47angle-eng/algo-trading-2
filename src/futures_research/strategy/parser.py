"""strategy.v1 YAML loader, four-layer validator, and StrategySpec mapper."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from futures_research.backtest.mtf import Timeframe
from futures_research.backtest.strategy import (
    DirectionSettings,
    EntrySettings,
    RegimeSettings,
    RiskSettings,
    SignalKind,
    StrategySpec,
    TimeframeTrio,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.paths import PROJECT_ROOT
from futures_research.sketch.identity import (
    SketchIdentityError,
    parse_sketch_id,
    parse_sketch_origin,
)
from futures_research.sketch.store import (
    SketchNotFoundError,
    SketchStore,
    SketchValidationError,
    default_sketch_store,
)
from futures_research.strategy.errors import StrategyValidationError, StrategyValidationIssue
from futures_research.strategy.models import (
    P1_INDICATOR_TYPES,
    P1_INVALIDATIONS,
    P1_STRUCTURE_TYPES,
    P1_TRIO_BIAS,
    P1_TRIO_ENTRY,
    P1_TRIO_MID,
    StrategyDocument,
)

# entry.sequence require patterns (P1 vocabulary)
_RE_CROSS_STATE = re.compile(
    r"^cross_state\((?P<fast>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
    r"(?P<slow>[A-Za-z_][A-Za-z0-9_]*)\)\s*==\s*(?P<side>golden|death)$"
)
_RE_STRUCTURE_COMPLETED = re.compile(r"^(?P<sid>[A-Za-z_][A-Za-z0-9_]*)\.completed$")
_RE_SIGNAL_BAR_IN = re.compile(r"^signal_bar\s+in\s+\[(?P<body>[^\]]+)\]$")

_PERCENTILE_MIN = 50.0
_PERCENTILE_MAX = 70.0
_EMA_PERIODS_P1 = frozenset({18, 90})
_ATR_PERIOD_P1 = 14


@dataclass(frozen=True, slots=True)
class ParsedStrategy:
    """Validated strategy.v1 document plus engine-facing StrategySpec."""

    document: StrategyDocument
    spec: StrategySpec
    source_path: Path | None = None


def load_strategy_file(
    path: Path,
    *,
    contracts_config: Path | None = None,
    sketch_store: SketchStore | None = None,
) -> ParsedStrategy:
    """Load a strategy.v1 YAML file, validate all layers, map to StrategySpec."""
    text = path.read_text(encoding="utf-8")
    return parse_strategy_document(
        text,
        source_path=path,
        contracts_config=contracts_config,
        sketch_store=sketch_store,
    )


def parse_strategy_document(
    source: str | Mapping[str, Any],
    *,
    source_path: Path | None = None,
    contracts_config: Path | None = None,
    sketch_store: SketchStore | None = None,
) -> ParsedStrategy:
    """Parse and fully validate a strategy.v1 document (string YAML or mapping)."""
    issues: list[StrategyValidationIssue] = []

    # --- Layer 1: format (YAML + pydantic) ---
    raw, format_issues = _load_raw(source)
    issues.extend(format_issues)
    if raw is None:
        raise StrategyValidationError(issues)

    try:
        document = StrategyDocument.model_validate(raw)
    except ValidationError as exc:
        issues.extend(_pydantic_to_issues(exc, layer="format"))
        raise StrategyValidationError(issues) from exc

    registry = _load_registry(contracts_config)

    # --- Layer 2: references ---
    issues.extend(
        _validate_references(
            document,
            registry,
            sketch_store=sketch_store or default_sketch_store(),
        )
    )

    # --- Layer 3: semantics (P1 bounds + vocabulary) ---
    issues.extend(_validate_semantics(document))

    # --- Layer 4: provenance coverage ---
    issues.extend(_validate_provenance(document))

    if issues:
        raise StrategyValidationError(issues)

    spec = _map_to_strategy_spec(document)
    return ParsedStrategy(document=document, spec=spec, source_path=source_path)


def required_provenance_paths(document: StrategyDocument) -> list[str]:
    """Numeric parameter paths that must appear in provenance (docs/05 §1.2)."""
    paths: list[str] = []
    for index, indicator in enumerate(document.indicators):
        paths.append(f"indicators.{indicator.id}.period")
        del index  # id-based path preferred over index
    paths.append("timeframes.entry_layers")
    paths.append("regime.sep_mult.value")
    paths.append("regime.flat_mult.value")
    paths.append("risk.stop.offset_ticks")
    paths.append("risk.target.value")
    paths.append("risk.sizing.risk_pct")
    paths.append("risk.daily_loss_limit_r")
    return paths


def _load_raw(
    source: str | Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[StrategyValidationIssue]]:
    if isinstance(source, Mapping):
        return dict(source), []
    try:
        loaded = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        return None, [
            StrategyValidationIssue(
                path="(root)",
                message=f"YAML syntax error ({exc})",
                fix="fix YAML indentation/quotes so the file parses as a mapping",
                layer="format",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            StrategyValidationIssue(
                path="(root)",
                message="strategy document must be a YAML mapping",
                fix="start with schema: strategy.v1 and top-level sections from docs/05 §1.1",
                layer="format",
            )
        ]
    return loaded, []


def _pydantic_to_issues(
    exc: ValidationError,
    *,
    layer: str,
) -> list[StrategyValidationIssue]:
    issues: list[StrategyValidationIssue] = []
    for error in exc.errors():
        loc = error.get("loc") or ()
        parts: list[str] = []
        for item in loc:
            if item == "schema_name":
                parts.append("schema")
            else:
                parts.append(str(item))
        path = ".".join(parts) if parts else "(root)"
        msg = str(error.get("msg") or "invalid value")
        issues.append(
            StrategyValidationIssue(
                path=path,
                message=msg,
                fix="correct the field type/value to match strategy.v1 (docs/05 §1.1)",
                layer=layer,
            )
        )
    return issues


def _load_registry(contracts_config: Path | None) -> ContractRegistry:
    path = contracts_config or (PROJECT_ROOT / "config" / "contracts.yaml")
    return ContractRegistry.from_yaml(path)


def _validate_references(
    document: StrategyDocument,
    registry: ContractRegistry,
    *,
    sketch_store: SketchStore,
) -> list[StrategyValidationIssue]:
    issues: list[StrategyValidationIssue] = []
    canonical_sketch_id: str | None = None
    canonical_sketch_origin: str | None = None
    try:
        canonical_sketch_id = parse_sketch_id(document.meta.based_on_sketch)
    except SketchIdentityError:
        issues.append(
            StrategyValidationIssue(
                path="meta.based_on_sketch",
                message="strategy.v1 requires a canonical sketch-YYYYMMDD-NN id",
                fix=(
                    "set meta.based_on_sketch to a canonical source id "
                    "(for example sketch-20260726-01); the complete identity "
                    "does not need to exist on this machine"
                ),
                layer="references",
            )
        )
    try:
        canonical_sketch_origin = parse_sketch_origin(document.meta.based_on_sketch_origin)
    except SketchIdentityError:
        issues.append(
            StrategyValidationIssue(
                path="meta.based_on_sketch_origin",
                message="strategy.v1 requires sketch origin workshop or journal-app",
                fix=(
                    "set meta.based_on_sketch_origin to workshop or journal-app; "
                    "do not infer an origin from the sketch id or local files"
                ),
                layer="references",
            )
        )
    indicator_ids = {item.id for item in document.indicators}
    structure_ids = {item.id for item in document.structures}
    known_symbols = set(registry.contracts.keys())

    for index, symbol in enumerate(document.universe.contracts):
        if symbol not in known_symbols:
            issues.append(
                StrategyValidationIssue(
                    path=f"universe.contracts[{index}]",
                    message=f"contract symbol '{symbol}' is not in contracts.yaml",
                    fix=(
                        f"use one of {sorted(known_symbols)} "
                        "or add the contract to config/contracts.yaml"
                    ),
                    layer="references",
                )
            )

    primary = registry.contracts.get(document.universe.primary_instrument)
    if primary is None:
        issues.append(
            StrategyValidationIssue(
                path="universe.primary_instrument",
                message=(
                    f"primary instrument {document.universe.primary_instrument!r} "
                    "is not in config/contracts.yaml"
                ),
                fix=f"use one of {sorted(known_symbols)} without changing its spelling",
                layer="references",
            )
        )
    else:
        if document.universe.primary_instrument not in document.universe.contracts:
            issues.append(
                StrategyValidationIssue(
                    path="universe.primary_instrument",
                    message="primary_instrument must also appear in universe.contracts",
                    fix="add the exact primary_instrument to universe.contracts",
                    layer="references",
                )
            )
        if document.universe.asset_class != primary.asset_class:
            issues.append(
                StrategyValidationIssue(
                    path="universe.asset_class",
                    message=(
                        f"asset_class {document.universe.asset_class!r} does not match "
                        f"catalog class {primary.asset_class!r} for "
                        f"{document.universe.primary_instrument}"
                    ),
                    fix=(
                        "copy the primary instrument's exact asset_class "
                        "from config/contracts.yaml"
                    ),
                    layer="references",
                )
            )

    for index, symbol in enumerate(document.universe.contracts):
        contract = registry.contracts.get(symbol)
        if contract is None:
            continue
        if contract.asset_class != document.universe.asset_class:
            issues.append(
                StrategyValidationIssue(
                    path=f"universe.contracts[{index}]",
                    message=(
                        f"{symbol} has catalog class {contract.asset_class!r}, not "
                        f"universe asset_class {document.universe.asset_class!r}"
                    ),
                    fix="keep every universe contract in the primary instrument's asset class",
                    layer="references",
                )
            )
        if document.universe.session not in contract.sessions:
            issues.append(
                StrategyValidationIssue(
                    path="universe.session",
                    message=f"{symbol} does not offer session {document.universe.session!r}",
                    fix="choose a session supported by every universe contract",
                    layer="references",
                )
            )
        if contract.currency != "USD":
            issues.append(
                StrategyValidationIssue(
                    path=f"universe.contracts[{index}]",
                    message=f"{symbol} currency is {contract.currency!r}; P2 MVP requires USD",
                    fix="use only USD-denominated configured contracts in this MVP universe",
                    layer="references",
                )
            )

    expected_rationale_keys = set(document.universe.contracts) - {
        document.universe.primary_instrument
    }
    actual_rationale_keys = set(document.universe.expansion_rationale)
    if actual_rationale_keys != expected_rationale_keys:
        issues.append(
            StrategyValidationIssue(
                path="universe.expansion_rationale",
                message=(
                    "expansion_rationale keys must be exactly universe.contracts minus "
                    "primary_instrument"
                ),
                fix=(
                    "use {} for a primary-only universe, otherwise give every non-primary "
                    "contract one non-empty rationale and no extra keys"
                ),
                layer="references",
            )
        )

    if canonical_sketch_id is not None and canonical_sketch_origin is not None:
        issues.extend(
            _validate_local_sketch_identity(
                document,
                sketch_id=canonical_sketch_id,
                origin=canonical_sketch_origin,
                sketch_store=sketch_store,
            )
        )

    for index, structure in enumerate(document.structures):
        data = structure.as_mapping()
        stype = structure.type
        if stype == "pullback_lifecycle":
            touch = data.get("touch")
            if touch is None:
                issues.append(
                    StrategyValidationIssue(
                        path=f"structures[{index}].touch",
                        message="pullback_lifecycle requires touch indicator id",
                        fix="set touch to an indicators[].id (e.g. ema_slow)",
                        layer="references",
                    )
                )
            elif str(touch) not in indicator_ids:
                issues.append(
                    StrategyValidationIssue(
                        path=f"structures[{index}].touch",
                        message=f"touch id '{touch}' is not defined in indicators",
                        fix="declare the indicator under indicators: or fix the touch id",
                        layer="references",
                    )
                )

    for index, step in enumerate(document.entry.sequence):
        require = step.require.strip()
        cross = _RE_CROSS_STATE.match(require)
        completed = _RE_STRUCTURE_COMPLETED.match(require)
        signal_in = _RE_SIGNAL_BAR_IN.match(require)
        if cross:
            for role, name in (("fast", cross.group("fast")), ("slow", cross.group("slow"))):
                if name not in indicator_ids:
                    issues.append(
                        StrategyValidationIssue(
                            path=f"entry.sequence[{index}].require",
                            message=f"cross_state {role} indicator '{name}' is not defined",
                            fix="use indicators[].id values inside cross_state(...)",
                            layer="references",
                        )
                    )
        elif completed:
            sid = completed.group("sid")
            if sid not in structure_ids:
                issues.append(
                    StrategyValidationIssue(
                        path=f"entry.sequence[{index}].require",
                        message=f"structure id '{sid}' is not defined in structures",
                        fix="add the structure or fix the .completed reference",
                        layer="references",
                    )
                )
        elif signal_in:
            body = signal_in.group("body")
            for raw_id in body.split(","):
                sid = raw_id.strip()
                if not sid:
                    continue
                if sid not in structure_ids:
                    issues.append(
                        StrategyValidationIssue(
                            path=f"entry.sequence[{index}].require",
                            message=f"signal_bar id '{sid}' is not defined in structures",
                            fix="list only declared signal_bar structure ids",
                            layer="references",
                        )
                    )
        else:
            issues.append(
                StrategyValidationIssue(
                    path=f"entry.sequence[{index}].require",
                    message=f"unsupported require predicate '{require}'",
                    fix=(
                        "use P1 forms: cross_state(id,id)==golden|death, "
                        "<structure_id>.completed, or signal_bar in [id, ...]"
                    ),
                    layer="references",
                )
            )

    paths = [item.path for item in document.provenance]
    if len(paths) != len(set(paths)):
        issues.append(
            StrategyValidationIssue(
                path="provenance",
                message="duplicate provenance paths",
                fix="keep one provenance entry per parameter path",
                layer="references",
            )
        )

    return issues


def _validate_local_sketch_identity(
    document: StrategyDocument,
    *,
    sketch_id: str,
    origin: str,
    sketch_store: SketchStore,
) -> list[StrategyValidationIssue]:
    """Check an available local sketch without turning remote provenance into an error.

    A complete composite identity may point at another app/machine, so absence
    is intentionally valid.  A local package that exists but predates P2 (or
    is malformed) is not equivalent to absence: it cannot prove the requested
    instrument identity and must be duplicated as a new package.
    """
    try:
        sketch = sketch_store.get(origin, sketch_id)
    except SketchNotFoundError:
        return []
    except SketchValidationError:
        return [
            StrategyValidationIssue(
                path="meta.based_on_sketch",
                message="local sketch package is incomplete or invalid for P2 identity checks",
                fix="duplicate it as a new sketch.v1 package with instrument and asset_class",
                layer="references",
            )
        ]

    meta = sketch.meta
    if meta.instrument is None or meta.asset_class is None:
        return [
            StrategyValidationIssue(
                path="meta.based_on_sketch",
                message="local legacy sketch lacks instrument and/or asset_class",
                fix="duplicate it as a new sketch.v1 package with complete catalog identity",
                layer="references",
            )
        ]

    issues: list[StrategyValidationIssue] = []
    if meta.instrument != document.universe.primary_instrument:
        issues.append(
            StrategyValidationIssue(
                path="universe.primary_instrument",
                message=(
                    f"local sketch instrument {meta.instrument!r} does not match primary "
                    f"instrument {document.universe.primary_instrument!r}"
                ),
                fix=(
                    "set primary_instrument to the local sketch instrument "
                    "or choose matching sketch"
                ),
                layer="references",
            )
        )
    if meta.asset_class != document.universe.asset_class:
        issues.append(
            StrategyValidationIssue(
                path="universe.asset_class",
                message=(
                    f"local sketch asset_class {meta.asset_class!r} does not match strategy "
                    f"asset_class {document.universe.asset_class!r}"
                ),
                fix="make the strategy universe match the local sketch catalog identity",
                layer="references",
            )
        )
    return issues


def _validate_semantics(document: StrategyDocument) -> list[StrategyValidationIssue]:
    issues: list[StrategyValidationIssue] = []

    trio = document.timeframes.trio
    if trio.bias != P1_TRIO_BIAS or trio.mid != P1_TRIO_MID or trio.entry != P1_TRIO_ENTRY:
        issues.append(
            StrategyValidationIssue(
                path="timeframes.trio",
                message=(
                    f"P1 trio must be {{{P1_TRIO_BIAS}/{P1_TRIO_MID}/{P1_TRIO_ENTRY}}}, "
                    f"got {{{trio.bias}/{trio.mid}/{trio.entry}}}"
                ),
                fix=f"set trio to bias: {P1_TRIO_BIAS}, mid: {P1_TRIO_MID}, entry: {P1_TRIO_ENTRY}",
                layer="semantics",
            )
        )

    if document.timeframes.entry_layers != 3:
        issues.append(
            StrategyValidationIssue(
                path="timeframes.entry_layers",
                message=f"P1 requires entry_layers=3, got {document.timeframes.entry_layers}",
                fix="set timeframes.entry_layers: 3 (mid-layer entry is P2)",
                layer="semantics",
            )
        )

    for index, indicator in enumerate(document.indicators):
        if indicator.type not in P1_INDICATOR_TYPES:
            issues.append(
                StrategyValidationIssue(
                    path=f"indicators[{index}].type",
                    message=f"indicator type '{indicator.type}' is not in P1 vocabulary",
                    fix=f"use one of {sorted(P1_INDICATOR_TYPES)}",
                    layer="semantics",
                )
            )
            continue
        if indicator.type == "EMA" and indicator.period not in _EMA_PERIODS_P1:
            issues.append(
                StrategyValidationIssue(
                    path=f"indicators[{index}].period",
                    message=f"P1 EMA period must be 18 or 90, got {indicator.period}",
                    fix="set period to 18 (fast) or 90 (slow) per TRADING_SPEC §12.2",
                    layer="semantics",
                )
            )
        if indicator.type == "ATR" and indicator.period != _ATR_PERIOD_P1:
            issues.append(
                StrategyValidationIssue(
                    path=f"indicators[{index}].period",
                    message=f"P1 ATR period must be {_ATR_PERIOD_P1}, got {indicator.period}",
                    fix=f"set period: {_ATR_PERIOD_P1}",
                    layer="semantics",
                )
            )

    for index, structure in enumerate(document.structures):
        if structure.type not in P1_STRUCTURE_TYPES:
            issues.append(
                StrategyValidationIssue(
                    path=f"structures[{index}].type",
                    message=(
                        f"structure type '{structure.type}' is P2 未支持 "
                        f"(not in P1 vocabulary {sorted(P1_STRUCTURE_TYPES)})"
                    ),
                    fix=(
                        "remove this structure or replace with pullback_lifecycle / "
                        "signal_bar; LMR and other P2 structures are not accepted yet"
                    ),
                    layer="semantics",
                )
            )
            continue
        data = structure.as_mapping()
        if structure.type == "pullback_lifecycle":
            layer = data.get("layer")
            if layer not in {"mid", "entry"}:
                issues.append(
                    StrategyValidationIssue(
                        path=f"structures[{index}].layer",
                        message=f"pullback_lifecycle layer must be mid|entry, got {layer!r}",
                        fix="set layer: mid or layer: entry",
                        layer="semantics",
                    )
                )
        if structure.type == "signal_bar":
            variant = data.get("variant")
            if variant not in {"inside", "magic"}:
                issues.append(
                    StrategyValidationIssue(
                        path=f"structures[{index}].variant",
                        message=f"signal_bar variant must be inside|magic, got {variant!r}",
                        fix="set variant: inside or variant: magic",
                        layer="semantics",
                    )
                )

    if not document.regime.require_trend:
        issues.append(
            StrategyValidationIssue(
                path="regime.require_trend",
                message="P1 Trend MVP requires require_trend: true",
                fix="set regime.require_trend: true",
                layer="semantics",
            )
        )
    if not document.regime.congestion_no_trade:
        issues.append(
            StrategyValidationIssue(
                path="regime.congestion_no_trade",
                message="P1 requires congestion_no_trade: true",
                fix="set regime.congestion_no_trade: true",
                layer="semantics",
            )
        )

    for field_name, calib in (
        ("sep_mult", document.regime.sep_mult),
        ("flat_mult", document.regime.flat_mult),
    ):
        value = calib.value
        if value < _PERCENTILE_MIN or value > _PERCENTILE_MAX:
            issues.append(
                StrategyValidationIssue(
                    path=f"regime.{field_name}.value",
                    message=(
                        f"percentile {value} outside P1 band [{_PERCENTILE_MIN}, {_PERCENTILE_MAX}]"
                    ),
                    fix=(
                        f"set value between {_PERCENTILE_MIN} and "
                        f"{_PERCENTILE_MAX} (matrix/spec band)"
                    ),
                    layer="semantics",
                )
            )

    if document.direction.mode != "trend_following":
        issues.append(
            StrategyValidationIssue(
                path="direction.mode",
                message=f"P1 only supports trend_following, got '{document.direction.mode}'",
                fix="set direction.mode: trend_following (reversal is P2)",
                layer="semantics",
            )
        )
    if document.direction.layer_consistency != "hard":
        issues.append(
            StrategyValidationIssue(
                path="direction.layer_consistency",
                message=(
                    f"P1 requires layer_consistency: hard, "
                    f"got '{document.direction.layer_consistency}'"
                ),
                fix="set direction.layer_consistency: hard",
                layer="semantics",
            )
        )

    if document.entry.trigger.type != "breakout":
        issues.append(
            StrategyValidationIssue(
                path="entry.trigger.type",
                message=f"P1 trigger type must be breakout, got '{document.entry.trigger.type}'",
                fix="set entry.trigger.type: breakout",
                layer="semantics",
            )
        )

    for index, name in enumerate(document.invalidations):
        if name not in P1_INVALIDATIONS:
            issues.append(
                StrategyValidationIssue(
                    path=f"invalidations[{index}]",
                    message=f"invalidation '{name}' is not in P1 set",
                    fix=f"use only {sorted(P1_INVALIDATIONS)}",
                    layer="semantics",
                )
            )

    if document.risk.stop.offset_ticks != 1:
        issues.append(
            StrategyValidationIssue(
                path="risk.stop.offset_ticks",
                message=f"P1 stop offset_ticks must be 1, got {document.risk.stop.offset_ticks}",
                fix="set risk.stop.offset_ticks: 1",
                layer="semantics",
            )
        )
    if document.risk.target.type != "r_multiple":
        issues.append(
            StrategyValidationIssue(
                path="risk.target.type",
                message=f"P1 target type must be r_multiple, got '{document.risk.target.type}'",
                fix="set risk.target.type: r_multiple",
                layer="semantics",
            )
        )

    # P1 single pullback EMA period: mid/entry touch indicators must agree.
    touch_periods: list[tuple[str, int]] = []
    indicator_period = {item.id: item.period for item in document.indicators}
    for structure in document.structures:
        if structure.type != "pullback_lifecycle":
            continue
        data = structure.as_mapping()
        touch = data.get("touch")
        if isinstance(touch, str) and touch in indicator_period:
            touch_periods.append((structure.id, indicator_period[touch]))
    if touch_periods:
        periods = {period for _sid, period in touch_periods}
        if len(periods) > 1:
            issues.append(
                StrategyValidationIssue(
                    path="structures",
                    message=(
                        "P1 requires one shared pullback EMA period; "
                        f"got conflicting touch periods {sorted(periods)}"
                    ),
                    fix=(
                        "point all pullback_lifecycle.touch at indicators "
                        "with the same period (18 or 90)"
                    ),
                    layer="semantics",
                )
            )
        elif periods and next(iter(periods)) not in _EMA_PERIODS_P1:
            issues.append(
                StrategyValidationIssue(
                    path="structures",
                    message=f"pullback touch period {next(iter(periods))} is not 18 or 90",
                    fix="use an EMA indicator with period 18 or 90 for touch",
                    layer="semantics",
                )
            )

    return issues


def _validate_provenance(document: StrategyDocument) -> list[StrategyValidationIssue]:
    issues: list[StrategyValidationIssue] = []
    required = required_provenance_paths(document)
    present = {entry.path for entry in document.provenance}
    for path in required:
        if path not in present:
            issues.append(
                StrategyValidationIssue(
                    path=f"provenance[{path}]",
                    message="missing provenance for numeric parameter",
                    fix=(
                        f"add provenance entry {{ path: {path}, "
                        f"source: owner_explicit|owner_inferred|web_researched|"
                        f"market_convention|system_default|derived }}"
                    ),
                    layer="provenance",
                )
            )
    return issues


def _map_to_strategy_spec(document: StrategyDocument) -> StrategySpec:
    """Map a fully validated document onto the internal P1 StrategySpec."""
    indicator_period = {item.id: item.period for item in document.indicators}
    pullback_period: Literal[18, 90] = 90
    for structure in document.structures:
        if structure.type != "pullback_lifecycle":
            continue
        touch = structure.as_mapping().get("touch")
        if isinstance(touch, str) and touch in indicator_period:
            period = indicator_period[touch]
            if period in (18, 90):
                pullback_period = period  # type: ignore[assignment]
            break

    signal_kinds: list[SignalKind] = []
    for structure in document.structures:
        if structure.type != "signal_bar":
            continue
        variant = structure.as_mapping().get("variant")
        if variant == "inside":
            signal_kinds.append(SignalKind.INSIDE)
        elif variant == "magic":
            signal_kinds.append(SignalKind.MAGIC)
    if not signal_kinds:
        signal_kinds = [SignalKind.INSIDE, SignalKind.MAGIC]
    # preserve declaration order, unique
    unique_signals: list[SignalKind] = []
    for kind in signal_kinds:
        if kind not in unique_signals:
            unique_signals.append(kind)

    inside_entry_ref: Literal["mother_high"] = "mother_high"
    inside_stop_ref: Literal["mother_low"] = "mother_low"
    for structure in document.structures:
        data = structure.as_mapping()
        if data.get("type") == "signal_bar" and data.get("variant") == "inside":
            entry_ref = data.get("entry_ref")
            stop_ref = data.get("stop_ref")
            if entry_ref == "mother_high":
                inside_entry_ref = "mother_high"
            if stop_ref == "mother_low":
                inside_stop_ref = "mother_low"

    return StrategySpec(
        universe_session=document.universe.session,
        timeframes=TimeframeTrio(
            bias=Timeframe.D1,
            mid=Timeframe.H1,
            entry=Timeframe.M5,
        ),
        regime=RegimeSettings(
            require_trend=True,
            congestion_no_trade=True,
            separation_percentile=float(document.regime.sep_mult.value),
            slope_percentile=float(document.regime.flat_mult.value),
        ),
        direction=DirectionSettings(
            mode="trend_following",
            layer_consistency="hard",
        ),
        entry=EntrySettings(
            entry_layers=3,
            signal_bars=tuple(unique_signals),
            pullback_ema_period=pullback_period,
            inside_entry_ref=inside_entry_ref,
            inside_stop_ref=inside_stop_ref,
        ),
        risk=RiskSettings(
            stop_offset_ticks=1,
            target_r_multiple=float(document.risk.target.value),
        ),
    )
