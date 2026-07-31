"""Bind a submitted strategy version to the spec that actually drives a run.

Before WO-006 / 6-5 the API passed ``strategy_version`` as a bare label and the
replay always used engine-default knobs, so picking a different version changed
nothing. This module is the fix: a confirmed A2 StrategyVersion is parsed back
into a ``StrategySpec`` and injected, and every way a run may deviate from its
document is recorded on the manifest rather than applied silently.

Policy (channel [083] Q1/Q3):

* the strategy file wins (D9 — the document is the single source of truth);
* validation runs may still override numeric knobs, but each override is
  recorded with its original and applied value;
* every P4/new-run contract must be explicitly listed in
  ``universe.contracts``; validation status never widens that authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, cast

from futures_research.backtest.records import StrategyBinding, StrategyParamOverride
from futures_research.backtest.strategy import StrategySpec
from futures_research.strategy.errors import StrategyValidationError
from futures_research.strategy.parser import parse_strategy_document
from futures_research.strategy.store import StrategyStore, default_strategy_store

#: Ids the store mints. Anything else is treated as a free-form legacy label.
_MANAGED_ID = re.compile(r"^strategy-[0-9]{4,}$")

#: Pre-6-5 validation preset, kept so unmanaged labels behave exactly as before.
LEGACY_SEPARATION_PERCENTILE = 50.0
LEGACY_SLOPE_PERCENTILE = 50.0
LEGACY_PULLBACK_EMA_PERIOD = 18


class StrategyResolutionError(ValueError):
    """Raised when a submission cannot be bound to a runnable strategy."""


@dataclass(frozen=True, slots=True)
class ResolvedStrategy:
    """Everything one run needs to replay a specific strategy version."""

    spec: StrategySpec | None
    binding: StrategyBinding
    session_name: Literal["eth", "rth"]
    regime_separation_percentile: float
    regime_slope_percentile: float
    pullback_ema_period: Literal[18, 90]


def resolve_strategy(
    *,
    strategy_version: str,
    symbol: str,
    validation_run: bool,
    requested_session: str | None = None,
    override_separation_percentile: float | None = None,
    override_slope_percentile: float | None = None,
    override_pullback_ema_period: int | None = None,
    allow_unauthorized_symbol: bool = False,
    store: StrategyStore | None = None,
) -> ResolvedStrategy:
    """Resolve one (strategy version × contract) cell of a batch matrix."""
    resolved_store = store or default_strategy_store()
    record = _load_record(resolved_store, strategy_version)

    if record is None:
        return _resolve_engine_default(
            strategy_version=strategy_version,
            requested_session=requested_session,
            override_separation_percentile=override_separation_percentile,
            override_slope_percentile=override_slope_percentile,
            override_pullback_ema_period=override_pullback_ema_period,
        )

    if record.get("status") != "confirmed":
        msg = (
            f"策略 {strategy_version} 仲係 draft，未經 Owner 確認 — "
            "請喺 P2 策略庫確認咗佢先至可以回測（journey S2c gate）"
        )
        raise StrategyResolutionError(msg)

    source_text = str(record.get("source_text") or "")
    if not source_text:
        msg = f"策略 {strategy_version} 冇儲存 YAML 原文，無法重建 spec — 請重新匯入"
        raise StrategyResolutionError(msg)
    try:
        parsed = parse_strategy_document(source_text)
    except StrategyValidationError as exc:  # pragma: no cover — stored text was validated
        msg = f"策略 {strategy_version} 嘅儲存內容而家驗證唔過：\n{exc.format_report()}"
        raise StrategyResolutionError(msg) from exc

    session = parsed.document.universe.session
    if requested_session is not None and requested_session != session:
        msg = (
            f"策略 {strategy_version} 嘅 universe.session 係 '{session}'，"
            f"但提交要求 '{requested_session}' — session 由策略文件決定，唔喺 UI 改"
        )
        raise StrategyResolutionError(msg)

    authorized_contracts = tuple(parsed.document.universe.contracts)
    universe_authorized = symbol in authorized_contracts
    if not universe_authorized and not allow_unauthorized_symbol:
        msg = (
            f"策略 {strategy_version} 只授權 {', '.join(authorized_contracts)}，"
            f"唔包 {symbol.upper()}。要做跨市場驗證，請喺 terminal 出一個 "
            f"universe.contracts 包含 {symbol.upper()} 嘅新版本"
            "（唔好喺 UI 擴大授權範圍）。"
        )
        raise StrategyResolutionError(msg)

    spec = parsed.spec
    overrides: list[StrategyParamOverride] = []
    requested_overrides = (
        (
            "regime.sep_mult.value",
            override_separation_percentile,
            spec.regime.separation_percentile,
        ),
        ("regime.flat_mult.value", override_slope_percentile, spec.regime.slope_percentile),
        (
            "structures.pullback_lifecycle.touch.period",
            override_pullback_ema_period,
            spec.entry.pullback_ema_period,
        ),
    )
    for path, requested, spec_value in requested_overrides:
        if requested is None or float(requested) == float(spec_value):
            continue
        if not validation_run:
            msg = (
                f"{path} 嘅 override（{spec_value} → {requested}）只准喺 validation run 用。"
                " 策略文件係唯一真相來源（D9）；要改參數請喺 terminal 出新版本。"
            )
            raise StrategyResolutionError(msg)
        overrides.append(
            StrategyParamOverride(
                path=path,
                spec_value=_format_value(spec_value),
                applied_value=_format_value(requested),
            )
        )

    separation = _pick(override_separation_percentile, spec.regime.separation_percentile)
    slope = _pick(override_slope_percentile, spec.regime.slope_percentile)
    pullback = int(_pick(override_pullback_ema_period, spec.entry.pullback_ema_period))
    if pullback not in (18, 90):
        msg = f"pullback EMA override 必須係 18 或者 90（TRADING_SPEC §12.2），收到 {pullback}"
        raise StrategyResolutionError(msg)
    if overrides:
        spec = spec.model_copy(
            update={
                "regime": spec.regime.model_copy(
                    update={
                        "separation_percentile": separation,
                        "slope_percentile": slope,
                    }
                ),
                "entry": spec.entry.model_copy(update={"pullback_ema_period": pullback}),
            }
        )

    binding = StrategyBinding(
        source="strategy_file",
        strategy_id=str(record.get("strategy_id")),
        strategy_name=str(record.get("name") or "") or None,
        content_sha256=str(record.get("content_sha256") or "") or None,
        universe_contracts=authorized_contracts,
        universe_authorized=universe_authorized,
        overrides=tuple(overrides),
    )
    return ResolvedStrategy(
        spec=spec,
        binding=binding,
        session_name=session,
        regime_separation_percentile=separation,
        regime_slope_percentile=slope,
        pullback_ema_period=cast(Literal[18, 90], pullback),
    )


def _resolve_engine_default(
    *,
    strategy_version: str,
    requested_session: str | None,
    override_separation_percentile: float | None,
    override_slope_percentile: float | None,
    override_pullback_ema_period: int | None,
) -> ResolvedStrategy:
    """Legacy path: a free-form label with no stored document behind it."""
    session = requested_session or "eth"
    if session not in ("eth", "rth"):
        msg = "session_name must be eth|rth"
        raise StrategyResolutionError(msg)
    pullback = int(
        _pick(override_pullback_ema_period, LEGACY_PULLBACK_EMA_PERIOD),
    )
    if pullback not in (18, 90):
        msg = f"pullback EMA 必須係 18 或者 90（TRADING_SPEC §12.2），收到 {pullback}"
        raise StrategyResolutionError(msg)
    del strategy_version
    return ResolvedStrategy(
        spec=None,
        binding=StrategyBinding(source="engine_default"),
        session_name=cast(Literal["eth", "rth"], session),
        regime_separation_percentile=float(
            _pick(override_separation_percentile, LEGACY_SEPARATION_PERCENTILE)
        ),
        regime_slope_percentile=float(_pick(override_slope_percentile, LEGACY_SLOPE_PERCENTILE)),
        pullback_ema_period=cast(Literal[18, 90], pullback),
    )


def _load_record(store: StrategyStore, strategy_version: str) -> dict[str, Any] | None:
    """Return the stored version, or ``None`` for an unmanaged legacy label."""
    if not _MANAGED_ID.fullmatch(strategy_version):
        return None
    try:
        return store.get(strategy_version)
    except LookupError as exc:
        msg = (
            f"策略版本 {strategy_version} 唔存在於策略庫 — "
            "請喺 P2 匯入並確認，或者用返一個已確認版本"
        )
        raise StrategyResolutionError(msg) from exc


def _pick(requested: float | int | None, fallback: float | int) -> float:
    return float(fallback if requested is None else requested)


def _format_value(value: float | int) -> str:
    """Render a parameter value for the audit trail without float noise."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)
