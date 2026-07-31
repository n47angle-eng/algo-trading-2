"""P3 data API routes (WO-006 / 6-4)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from futures_research.api import data_catalog

router = APIRouter(prefix="/api/v1/data", tags=["data"])


class BlacklistBody(BaseModel):
    contract_id: str
    trading_date: str = Field(description="YYYY-MM-DD")
    decision: Literal["exclude", "trust"]
    note: str = ""


class DownloadBody(BaseModel):
    symbol: str
    start: str
    end: str


@router.get("/coverage")
def get_coverage(
    view: Literal["catalog", "full"] = "full",
) -> dict[str, Any]:
    """Canonical 1m coverage table for configured contracts."""
    try:
        return data_catalog.list_coverage(view=view)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="contract catalog is invalid; coverage was not partially generated",
        ) from exc


@router.get("/quality-reports")
def get_quality_reports(
    contract_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    return data_catalog.list_quality_reports(contract_id=contract_id, limit=limit)


@router.get("/quality-reports/{report_id:path}")
def get_quality_report(report_id: str) -> dict[str, Any]:
    try:
        return data_catalog.get_quality_report(report_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/blacklist")
def get_blacklist() -> dict[str, Any]:
    return data_catalog.list_owner_blacklist()


@router.post("/blacklist")
def post_blacklist(body: BlacklistBody) -> dict[str, Any]:
    try:
        return data_catalog.upsert_owner_blacklist_entry(
            contract_id=body.contract_id,
            trading_date=body.trading_date,
            decision=body.decision,
            note=body.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/download")
def post_download(body: DownloadBody) -> dict[str, Any]:
    try:
        return data_catalog.enqueue_download_job(
            symbol=body.symbol.upper(),
            start=body.start,
            end=body.end,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/download-jobs")
def get_download_jobs(limit: int = 20) -> dict[str, Any]:
    return data_catalog.list_download_jobs(limit=limit)
