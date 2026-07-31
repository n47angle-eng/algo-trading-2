"""P4 batch submit + progress API (WO-006 / 6-4)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from futures_research.api.backtest_admission import (
    AdmissionEvaluation,
    AdmissionRequestError,
    AdmissionServiceError,
    BacktestAdmissionService,
    P4BatchRequest,
    default_backtest_admission_service,
)
from futures_research.api.batch_queue import (
    BatchOperationalStateError,
    BatchPersistenceError,
    get_batch_queue,
)

_LOGGER = logging.getLogger(__name__)
_ADMISSION_UNAVAILABLE_DETAIL = "backtest admission truth is unavailable"
_BATCH_STATE_UNAVAILABLE_DETAIL = "batch operational state is unavailable"

router = APIRouter(prefix="/api/v1", tags=["batches"])


class BatchSubmitBody(BaseModel):
    """Multi-strategy × multi-contract batch request.

    The three numeric knobs are **override requests**, not inputs: ``None`` means
    "use the strategy document's value" (D9), and a value is only applied on a
    validation run, where it is recorded in the manifest (channel [083] Q1).
    ``session_name`` likewise comes from ``universe.session`` unless explicitly
    supplied, in which case a mismatch is rejected rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1)
    strategy_versions: list[str] = Field(default_factory=lambda: ["trend-v0"])
    session_name: str | None = None
    range_start: str
    range_end: str
    validation_run: bool = True
    pullback_ema_period: int | None = None
    regime_separation_percentile: float | None = None
    regime_slope_percentile: float | None = None
    initial_capital: float = 100_000.0
    quantity: int = 1
    skip_nautilus_replay: bool = True


def _admission_service(request: Request) -> BacktestAdmissionService:
    override = getattr(request.app.state, "backtest_admission_service", None)
    if isinstance(override, BacktestAdmissionService):
        return override
    return default_backtest_admission_service()


def _evaluate_admission(
    body: P4BatchRequest,
    request: Request,
) -> tuple[BacktestAdmissionService, AdmissionEvaluation]:
    """Resolve and evaluate endpoint-wide truth behind one sanitized boundary."""
    try:
        service = _admission_service(request)
        return service, service.evaluate(body)
    except AdmissionRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AdmissionServiceError as exc:
        _LOGGER.exception("backtest admission truth service failed")
        raise HTTPException(
            status_code=503,
            detail=_ADMISSION_UNAVAILABLE_DETAIL,
        ) from exc
    except Exception as exc:  # noqa: BLE001 — endpoint dependency boundary
        _LOGGER.exception("unexpected backtest admission truth failure")
        raise HTTPException(
            status_code=503,
            detail=_ADMISSION_UNAVAILABLE_DETAIL,
        ) from exc


@router.post("/batches/precheck")
def precheck_batch(body: P4BatchRequest, request: Request) -> dict[str, Any]:
    """Evaluate a complete P4 standard matrix without performing any writes."""
    _, evaluation = _evaluate_admission(body, request)
    return evaluation.document


@router.post("/batches/submit", response_model=None)
def submit_batch(
    body: P4BatchRequest | BatchSubmitBody,
    request: Request,
) -> dict[str, Any] | JSONResponse:
    """Submit strict standard P4 work or retain the engineering validation path."""
    queue = get_batch_queue()
    if isinstance(body, P4BatchRequest):
        service, evaluation = _evaluate_admission(body, request)
        if evaluation.document["can_submit"] is not True:
            return JSONResponse(status_code=409, content=evaluation.document)
        try:
            record = queue.submit_standard(
                body,
                evaluation,
                admission_service=service,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail="standard batch could not be published atomically",
            ) from exc
        return record.to_dict()

    try:
        record = queue.submit(body.model_dump())
    except (BatchOperationalStateError, BatchPersistenceError) as exc:
        _LOGGER.exception("engineering batch operational state could not be published")
        raise HTTPException(
            status_code=503,
            detail=_BATCH_STATE_UNAVAILABLE_DETAIL,
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.to_dict()


@router.get("/batches/jobs")
def list_batch_jobs() -> dict[str, Any]:
    """List submitted UI batches (distinct from synthetic result-file batches)."""
    queue = get_batch_queue()
    try:
        batches = queue.list_batches()
    except BatchOperationalStateError as exc:
        _LOGGER.exception("persisted batch operational state failed validation")
        raise HTTPException(
            status_code=503,
            detail=_BATCH_STATE_UNAVAILABLE_DETAIL,
        ) from exc
    return {"schema": "batch_job_list.v2", "count": len(batches), "batches": batches}


@router.get("/batches/jobs/{batch_id}")
def get_batch_job(batch_id: str) -> dict[str, Any]:
    queue = get_batch_queue()
    try:
        return queue.get(batch_id).to_dict()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BatchOperationalStateError as exc:
        _LOGGER.exception("persisted batch operational state failed validation")
        raise HTTPException(
            status_code=503,
            detail=_BATCH_STATE_UNAVAILABLE_DETAIL,
        ) from exc


@router.post("/batches/jobs/{batch_id}/cancel-queued")
def cancel_queued_batch_jobs(batch_id: str) -> dict[str, Any]:
    """Cancel only cells that remain queued at this endpoint's lock boundary."""
    queue = get_batch_queue()
    try:
        return queue.cancel_queued(batch_id).to_dict()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (BatchOperationalStateError, BatchPersistenceError) as exc:
        _LOGGER.exception("queued batch cancellation could not be persisted")
        raise HTTPException(
            status_code=503,
            detail=_BATCH_STATE_UNAVAILABLE_DETAIL,
        ) from exc
