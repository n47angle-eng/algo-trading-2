"""Additive, fail-closed reverse lookups over derived immutable-run indexes."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request

from futures_research.backtest.persistence import RunIndexIntegrityError
from futures_research.backtest.run_reference_catalog import (
    CatalogRun,
    RunReferenceCatalog,
    RunReferenceMigrationRequired,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.paths import PROJECT_ROOT

router = APIRouter(prefix="/api/v1", tags=["run-references"])

_DATE_LABEL = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_MODE_FIELDS = {
    "strategy": frozenset({"mode", "strategy_version"}),
    "trading-date": frozenset({"mode", "symbol", "trading_date"}),
    "duplicate": frozenset({"mode", "strategy_version", "symbol", "range_start", "range_end"}),
}


@lru_cache(maxsize=1)
def _default_registry() -> ContractRegistry:
    """Load the configured root-symbol registry once for default local API use."""
    return ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")


@lru_cache(maxsize=1)
def _default_audit_runs() -> tuple[CatalogRun, ...]:
    """Read the permanent audit source once; its immutable entries never need refreshing."""
    backtests_root = PROJECT_ROOT / "data" / "backtests"
    return RunReferenceCatalog.audit_runs_from_source(
        audit_database=backtests_root / "runs-3b4-audit.sqlite3",
        registry=_default_registry(),
    )


def default_run_reference_catalog() -> RunReferenceCatalog:
    """Read a fresh main snapshot while retaining only the immutable audit bootstrap."""
    backtests_root = PROJECT_ROOT / "data" / "backtests"
    return RunReferenceCatalog.from_main_with_audit_runs(
        main_database=backtests_root / "runs.sqlite3",
        audit_database=backtests_root / "runs-3b4-audit.sqlite3",
        registry=_default_registry(),
        audit_runs=_default_audit_runs(),
    )


def _catalog(request: Request) -> RunReferenceCatalog:
    override = getattr(request.app.state, "run_reference_catalog", None)
    if isinstance(override, RunReferenceCatalog):
        return override
    return default_run_reference_catalog()


def run_reference_catalog_for_request(request: Request) -> RunReferenceCatalog:
    """Expose the exact production dependency for other additive API operations."""
    return _catalog(request)


def _registry(request: Request) -> ContractRegistry:
    override = getattr(request.app.state, "run_reference_registry", None)
    if isinstance(override, ContractRegistry):
        return override
    return _default_registry()


@router.get("/run-references")
def get_run_references(request: Request) -> dict[str, object]:
    """Answer one strict standard-run reference query without scanning manifest JSON."""
    registry = _registry(request)
    try:
        mode, values = _parse_query(request, registry=registry)
        catalog = run_reference_catalog_for_request(request)
        if mode == "strategy":
            return catalog.references_for_strategy(
                strategy_version=values["strategy_version"]
            ).to_document()
        if mode == "duplicate":
            return catalog.references_for_duplicate(
                strategy_version=values["strategy_version"],
                symbol=values["symbol"],
                range_start=values["range_start"],
                range_end=values["range_end"],
            ).to_document()
        symbol = values["symbol"]
        return catalog.references_for_trading_date(
            symbol=symbol,
            trading_date=_parse_date_label(values["trading_date"], field="trading_date"),
            contract=registry.by_symbol(symbol),
        ).to_document()
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RunReferenceMigrationRequired as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RunIndexIntegrityError as exc:
        raise HTTPException(
            status_code=503,
            detail="run reference index is inconsistent; reconcile it before querying",
        ) from exc


def _parse_query(
    request: Request,
    *,
    registry: ContractRegistry,
) -> tuple[str, dict[str, str]]:
    """Require exactly one approved mode and exactly its approved query fields."""
    items = list(request.query_params.multi_items())
    names = [name for name, _ in items]
    if len(names) != len(set(names)):
        msg = "run-reference query fields must not be repeated"
        raise ValueError(msg)
    values = dict(items)
    mode = values.get("mode")
    if mode not in _MODE_FIELDS:
        msg = "mode must be exactly strategy, trading-date, or duplicate"
        raise ValueError(msg)
    expected_fields = _MODE_FIELDS[mode]
    if set(values) != expected_fields:
        msg = f"mode={mode} requires exactly {sorted(expected_fields)}"
        raise ValueError(msg)

    if mode == "strategy":
        values["strategy_version"] = _exact_text(
            values["strategy_version"], field="strategy_version"
        )
        return mode, values

    symbol = _exact_text(values["symbol"], field="symbol")
    try:
        registry.by_symbol(symbol)
    except KeyError as exc:
        msg = f"symbol must be an exact configured root symbol: {symbol!r}"
        raise ValueError(msg) from exc
    values["symbol"] = symbol
    if mode == "trading-date":
        _parse_date_label(values["trading_date"], field="trading_date")
        return mode, values

    values["strategy_version"] = _exact_text(values["strategy_version"], field="strategy_version")
    start = _parse_aware_utc(values["range_start"], field="range_start")
    end = _parse_aware_utc(values["range_end"], field="range_end")
    if start >= end:
        msg = "range_start must be earlier than range_end"
        raise ValueError(msg)
    values["range_start"] = _utc_text(start)
    values["range_end"] = _utc_text(end)
    return mode, values


def _exact_text(value: str, *, field: str) -> str:
    """Reject blank/padded query values instead of silently normalizing identities."""
    if not value or value != value.strip():
        msg = f"{field} must be a non-empty unpadded value"
        raise ValueError(msg)
    return value


def _parse_date_label(value: str, *, field: str) -> date:
    """Accept one ASCII trading-date label, never a timestamp or locale conversion."""
    if _DATE_LABEL.fullmatch(value) is None:
        msg = f"{field} must be an exact ASCII YYYY-MM-DD label"
        raise ValueError(msg)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        msg = f"{field} must contain a real calendar date"
        raise ValueError(msg) from exc


def _parse_aware_utc(value: str, *, field: str) -> datetime:
    """Normalize a query instant only after requiring an explicit UTC offset."""
    if value != value.strip():
        msg = f"{field} must be an unpadded ISO-8601 timestamp with an offset"
        raise ValueError(msg)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        msg = f"{field} must be an ISO-8601 timestamp with an offset"
        raise ValueError(msg) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = f"{field} must include an explicit UTC offset"
        raise ValueError(msg)
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    """Use the same canonical Z form as immutable run manifests and lookup rows."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
