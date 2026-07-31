"""Chart series + narrative HTTP routes (WO-006 / 6-3)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from futures_research.api.chart_series import (
    build_narrative,
    get_chart_series,
    shadow_compare_run_chart,
)
from futures_research.api.results_catalog import (
    ResultArtifactIntegrityError,
    ResultNotFoundError,
    ResultsCatalog,
)

router = APIRouter(prefix="/api/v1", tags=["chart"])


def _catalog(request: Request) -> ResultsCatalog:
    override = getattr(request.app.state, "results_catalog", None)
    if isinstance(override, ResultsCatalog):
        return override
    from futures_research.api.deps import get_results_catalog

    return get_results_catalog()


CatalogDep = Annotated[ResultsCatalog, Depends(_catalog)]


@router.get("/runs/{run_id}/chart")
def get_run_chart(
    run_id: str,
    catalog: CatalogDep,
    tf: str = Query(default="5m", pattern="^(5m|1H|D)$"),
    lookback_days: int | None = Query(default=None, ge=1, le=400),
) -> dict[str, Any]:
    """Candles + EMA + markers; prefers chart/ sidecar (6-3b), materializes on miss."""
    try:
        return get_chart_series(
            catalog,
            run_id,
            timeframe=tf,
            lookback_days=lookback_days,
        )
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}/chart/shadow-compare")
def get_run_chart_shadow_compare(
    run_id: str,
    catalog: CatalogDep,
    tf: str = Query(default="5m", pattern="^(5m|1H|D)$"),
    lookback_days: int | None = Query(default=None, ge=1, le=400),
    abs_eps: float = Query(default=1e-6, gt=0.0, le=10.0),
) -> dict[str, Any]:
    """Phase F: same-window Python MTF reference vs Rust candidate diff report."""
    try:
        return shadow_compare_run_chart(
            catalog,
            run_id,
            timeframe=tf,
            lookback_days=lookback_days,
            abs_eps=abs_eps,
        )
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}/narrative")
def get_run_narrative(
    run_id: str,
    catalog: CatalogDep,
    limit: int = Query(default=40, ge=1, le=200),
) -> dict[str, Any]:
    """Deterministic narrative steps from the events sidecar."""
    try:
        return build_narrative(catalog, run_id, limit=limit)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
