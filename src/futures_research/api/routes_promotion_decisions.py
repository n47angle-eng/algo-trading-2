"""Additive APIs for immutable Owner PromotionDecision records."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from futures_research.api.promotion_decisions import (
    PromotionDecisionConflictError,
    PromotionDecisionIntegrityError,
    PromotionDecisionStore,
    PromotionDecisionValue,
    request_payload_sha256,
    source_from_result_snapshot,
)
from futures_research.api.results_catalog import (
    ResultArtifactIntegrityError,
    ResultNotFoundError,
    ResultsCatalog,
)

router = APIRouter(prefix="/api/v1", tags=["promotion-decisions"])


class PromotionDecisionRequest(BaseModel):
    """Only Owner intent crosses the API; provenance is always server-derived."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        populate_by_name=True,
    )

    schema_version: Literal["promotion_decision_request.v1"] = Field(alias="schema")
    request_id: str = Field(min_length=1, max_length=256)
    decision: PromotionDecisionValue
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("request_id")
    @classmethod
    def reject_blank_request_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("request_id must not be blank")
        return value

    @field_validator("reason")
    @classmethod
    def reject_blank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must contain the Owner's explanation")
        return value


def _catalog(request: Request) -> ResultsCatalog:
    override = getattr(request.app.state, "results_catalog", None)
    if isinstance(override, ResultsCatalog):
        return override
    from futures_research.api.deps import get_results_catalog

    return get_results_catalog()


def _store(request: Request) -> PromotionDecisionStore:
    override = getattr(request.app.state, "promotion_decision_store", None)
    if isinstance(override, PromotionDecisionStore):
        return override
    from futures_research.api.deps import get_promotion_decision_store

    return get_promotion_decision_store()


CatalogDep = Annotated[ResultsCatalog, Depends(_catalog)]
StoreDep = Annotated[PromotionDecisionStore, Depends(_store)]


@router.post("/runs/{run_id}/promotion-decisions")
def create_promotion_decision(
    run_id: str,
    body: PromotionDecisionRequest,
    catalog: CatalogDep,
    store: StoreDep,
) -> dict[str, object]:
    """Append one human decision, safely replaying an identical request id."""
    fingerprint = request_payload_sha256(
        run_id=run_id,
        request_id=body.request_id,
        decision=body.decision,
        reason=body.reason,
    )
    try:
        existing = store.replay(
            request_id=body.request_id,
            request_payload_sha256=fingerprint,
        )
        if existing is not None:
            return existing.to_dict()
        snapshot = catalog.get_verified_snapshot(run_id)
        source = source_from_result_snapshot(snapshot, expected_run_id=run_id)
        return store.append(
            request_id=body.request_id,
            request_payload_sha256=fingerprint,
            decision=body.decision,
            reason=body.reason,
            source=source,
        ).to_dict()
    except ResultNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PromotionDecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ResultArtifactIntegrityError, PromotionDecisionIntegrityError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(
            status_code=503,
            detail="promotion decision store is unavailable; no decision was appended",
        ) from exc


@router.get("/runs/{run_id}/promotion-decisions")
def list_run_promotion_decisions(
    run_id: str,
    store: StoreDep,
) -> dict[str, object]:
    """List one run's immutable decision history."""
    try:
        records = store.list(run_id=run_id)
    except (OSError, sqlite3.Error, PromotionDecisionIntegrityError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "schema": "promotion_decision_list.v1",
        "run_id": run_id,
        "count": len(records),
        "decisions": [record.to_dict() for record in records],
    }


@router.get("/promotion-decisions")
def list_promotion_decisions(
    store: StoreDep,
    run_id: str | None = None,
    strategy_id: str | None = None,
    decision: Literal["use", "return", "abandon"] | None = None,
) -> dict[str, object]:
    """Read the global immutable history with additive exact-match filters."""
    try:
        records = store.list(
            run_id=run_id,
            strategy_id=strategy_id,
            decision=decision,
        )
    except (OSError, sqlite3.Error, PromotionDecisionIntegrityError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "schema": "promotion_decision_list.v1",
        "count": len(records),
        "decisions": [record.to_dict() for record in records],
    }


@router.get("/promotion-decisions/eligible-strategies")
def list_eligible_strategies(store: StoreDep) -> dict[str, object]:
    """List strategies that ever received a historical ``use`` decision.

    Page independence (docs/10): this is Owner intent history only. Paper UI
    selects confirmed strategies from the strategy store and does not gate on
    this list. Kept for audit / filters / older consumers.
    """
    try:
        strategies = store.eligible_strategies()
    except (OSError, sqlite3.Error, PromotionDecisionIntegrityError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "schema": "eligible_strategy_list.v1",
        "count": len(strategies),
        "strategies": list(strategies),
    }
