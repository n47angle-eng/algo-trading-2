"""P2 tab ④ market-insight repository — import, reads, and recoverable archive.

Insights are a notebook (P2 constraints #22/#23): this router deliberately
offers no strategy or run linkage, no status transition, and no permanent
delete. Every version remains immutable while its whole identity is archived.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from futures_research.insight.store import (
    InsightConflictError,
    InsightNotFoundError,
    InsightStorageError,
    InsightStore,
    InsightValidationError,
    InsightVersionCollisionError,
    default_insight_store,
)

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])
LOGGER = logging.getLogger(__name__)


class InsightSourceBody(BaseModel):
    """Raw ``insight.v1`` YAML as written by the terminal AI."""

    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1)


def _validation_payload(exc: InsightValidationError) -> dict[str, Any]:
    return {
        "schema": "insight_validation.v1",
        "valid": False,
        "issue_count": len(exc.issues),
        "issues": [
            {
                "path": issue.path,
                "message": issue.message,
                "fix": issue.fix,
                "line": issue.format_line(),
            }
            for issue in exc.issues
        ],
        # Paste-back payload: plain text, no markup, no UI decoration.
        "report_text": exc.format_report(),
    }


def _insight_store(request: Request) -> InsightStore:
    override = getattr(request.app.state, "insight_store", None)
    if isinstance(override, InsightStore):
        return override
    return default_insight_store()


@router.post("/import")
def import_insight(body: InsightSourceBody, request: Request) -> dict[str, Any]:
    """Validate and store one immutable ``insight.v1`` version."""
    store = _insight_store(request)
    try:
        outcome = store.import_document(body.source_text)
    except InsightValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_payload(exc)) from exc
    except InsightConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "schema": "insight_import_conflict.v1",
                "message": (
                    "insight identity is archived; restore it before importing. "
                    "Active and archive storage are unchanged."
                ),
            },
        ) from exc
    except InsightVersionCollisionError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "schema": "insight_import_conflict.v1",
                "message": "同一個洞察版本已存在而且內容唔同；已保留原版本，冇覆蓋",
            },
        ) from exc
    except InsightStorageError as exc:
        LOGGER.exception("insight storage failure during import")
        raise HTTPException(
            status_code=503,
            detail="洞察儲存暫時不可用；未有寫入任何檔案",
        ) from exc
    return {
        "schema": "insight_import.v1",
        "deduplicated": outcome.deduplicated,
        "message": (
            "內容同已存在版本 byte 完全相同，冇開新版本"
            if outcome.deduplicated
            else "已存入洞察庫"
        ),
        "insight": outcome.record,
    }


@router.delete("/{origin}/{insight_id}")
def archive_insight(
    origin: str,
    insight_id: str,
    request: Request,
) -> dict[str, Any]:
    """Move every immutable version into one recoverable archive."""
    store = _insight_store(request)
    try:
        outcome = store.archive(origin, insight_id)
    except InsightNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                "Active insight was not found; active and archive storage "
                "are unchanged."
            ),
        ) from exc
    except InsightConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Archive target changed or already exists; active and archive "
                "storage were not overwritten."
            ),
        ) from exc
    except InsightStorageError as exc:
        LOGGER.exception("insight storage failure during archive")
        raise HTTPException(
            status_code=503,
            detail=(
                "Archive state could not be proven; no success was reported and "
                "related reads fail closed."
            ),
        ) from exc
    archived_to = (
        "data/insights/_deleted/"
        f"{outcome.origin}/{outcome.insight_id}/{outcome.archive_id}"
    )
    return {
        "schema": "insight_archive.v1",
        "origin": outcome.origin,
        "insight_id": outcome.insight_id,
        "archive_id": outcome.archive_id,
        "deleted_at": outcome.deleted_at,
        "version_count": outcome.version_count,
        "archived_to": archived_to,
    }


@router.get("/archives")
def list_archives(request: Request) -> dict[str, Any]:
    """List proven archives without exposing archived document contents."""
    store = _insight_store(request)
    try:
        archives = store.list_archives()
    except InsightStorageError as exc:
        LOGGER.exception("insight storage failure during archive list")
        raise HTTPException(
            status_code=503,
            detail=(
                "Archive storage could not be proven; active and archive storage "
                "are unchanged and related reads fail closed."
            ),
        ) from exc
    return {
        "schema": "insight_archive_list.v1",
        "count": len(archives),
        "archives": archives,
    }


@router.post("/archives/{origin}/{insight_id}/{archive_id}/restore")
def restore_insight(
    origin: str,
    insight_id: str,
    archive_id: str,
    request: Request,
) -> dict[str, Any]:
    """Restore a whole archive only when the active identity is absent."""
    store = _insight_store(request)
    try:
        outcome = store.restore(origin, insight_id, archive_id)
    except InsightNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                "Insight archive was not found or its path is invalid; active "
                "and archive storage are unchanged."
            ),
        ) from exc
    except InsightConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Active restore target exists or changed; active and archive "
                "storage are unchanged and nothing was overwritten."
            ),
        ) from exc
    except InsightStorageError as exc:
        LOGGER.exception("insight storage failure during restore")
        raise HTTPException(
            status_code=503,
            detail=(
                "Restore state could not be proven; no success was reported and "
                "related reads fail closed."
            ),
        ) from exc
    return {
        "schema": "insight_restore.v1",
        "origin": outcome.origin,
        "insight_id": outcome.insight_id,
        "archive_id": outcome.archive_id,
        "restored_at": outcome.restored_at,
        "version_count": outcome.version_count,
        "restored_to": f"data/insights/{outcome.origin}/{outcome.insight_id}",
    }


@router.get("")
def list_insights(request: Request) -> dict[str, Any]:
    """List one summary per composite identity, with every stored version number."""
    store = _insight_store(request)
    try:
        insights = store.list_insights()
    except InsightStorageError as exc:
        LOGGER.exception("insight storage failure during list")
        raise HTTPException(
            status_code=503,
            detail="洞察儲存暫時不可用；未能列出洞察庫",
        ) from exc
    return {
        "schema": "insight_list.v1",
        "count": len(insights),
        "insights": insights,
    }


@router.get("/{origin}/{insight_id}")
def get_insight(origin: str, insight_id: str, request: Request) -> dict[str, Any]:
    """Return every stored version of one insight, including its original YAML."""
    store = _insight_store(request)
    try:
        return store.get(origin, insight_id)
    except InsightNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsightStorageError as exc:
        LOGGER.exception("insight storage failure during detail read")
        raise HTTPException(
            status_code=503,
            detail="洞察儲存暫時不可用；未能讀取呢個洞察",
        ) from exc
