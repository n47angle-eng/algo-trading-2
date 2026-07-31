"""Read-only HTTP routes for result.v1 artifacts (WO-006 / 6-2)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from futures_research.api.result_export import build_result_export
from futures_research.api.results_catalog import (
    ResultArtifactIntegrityError,
    ResultNotFoundError,
    ResultsCatalog,
)

router = APIRouter(prefix="/api/v1", tags=["results"])


def _catalog(request: Request) -> ResultsCatalog:
    """Prefer app.state.results_catalog (tests); else project default."""
    override = getattr(request.app.state, "results_catalog", None)
    if isinstance(override, ResultsCatalog):
        return override
    from futures_research.api.deps import get_results_catalog

    return get_results_catalog()


CatalogDep = Annotated[ResultsCatalog, Depends(_catalog)]


@router.get("/runs")
def list_runs(catalog: CatalogDep) -> dict[str, Any]:
    """List compact run summaries for the P5 compare table."""
    try:
        rows = catalog.list_run_summaries()
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"schema": "run_list.v1", "count": len(rows), "runs": rows}


@router.get("/runs/{run_id}")
def get_run(run_id: str, catalog: CatalogDep) -> dict[str, Any]:
    """Return one result.v1 main document (metrics, scorecard, funnel, refs)."""
    try:
        return catalog.get_result(run_id)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/runs/{run_id}/export")
def export_run(run_id: str, catalog: CatalogDep) -> Response:
    """Return one self-contained ZIP made only from captured immutable artifact bytes."""
    try:
        payload = build_result_export(catalog, run_id)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"unable to build complete result export for {run_id}",
        ) from exc
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="result-{run_id}.zip"',
        },
    )


@router.get("/runs/{run_id}/trades")
def get_run_trades(run_id: str, catalog: CatalogDep) -> dict[str, Any]:
    """Return the trades sidecar for one run."""
    try:
        return catalog.get_trades(run_id)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/runs/{run_id}/events")
def get_run_events(run_id: str, catalog: CatalogDep) -> dict[str, Any]:
    """Return the events sidecar for one run."""
    try:
        return catalog.get_events(run_id)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/batches")
def list_batches(catalog: CatalogDep) -> dict[str, Any]:
    """List synthetic batch groups over local result files."""
    try:
        batches = catalog.list_batches()
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"schema": "batch_list.v1", "count": len(batches), "batches": batches}


@router.get("/batches/{batch_id}/runs")
def list_batch_runs(batch_id: str, catalog: CatalogDep) -> dict[str, Any]:
    """List run summaries in one batch (for P5 batch entry)."""
    try:
        rows = catalog.list_batch_runs(batch_id)
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResultArtifactIntegrityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "schema": "run_list.v1",
        "batch_id": batch_id,
        "count": len(rows),
        "runs": rows,
    }
