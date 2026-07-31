"""Additive sketch.v1 package upload and read APIs (batch 1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from futures_research.sketch.store import (
    SketchAlreadyExistsError,
    SketchNotFoundError,
    SketchValidationError,
    default_sketch_store,
)

router = APIRouter(prefix="/api/v1/sketches", tags=["sketches"])


def _validation_payload(exc: SketchValidationError) -> dict[str, Any]:
    return {
        "schema": "sketch_validation.v1",
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
        "report_text": exc.format_report(),
    }


@router.post("", status_code=201)
async def import_sketch(request: Request) -> dict[str, Any]:
    """Accept one ZIP and publish the complete package only after full validation."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/zip":
        raise HTTPException(
            status_code=415,
            detail="send the sketch.v1 bundle as Content-Type: application/zip",
        )
    bundle = await request.body()
    store = default_sketch_store()
    try:
        return store.import_zip(bundle).detail()
    except SketchValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_payload(exc)) from exc
    except SketchAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="unable to store sketch package; no partial package was published",
        ) from exc


@router.get("")
def list_sketches() -> dict[str, Any]:
    store = default_sketch_store()
    sketches = [package.summary() for package in store.list()]
    return {
        "schema": "sketch_list.v1",
        "count": len(sketches),
        "sketches": sketches,
    }


@router.get("/{origin}/{sketch_id}/images/{filename}")
def get_sketch_image(origin: str, sketch_id: str, filename: str) -> FileResponse:
    store = default_sketch_store()
    try:
        path = store.image_path(origin, sketch_id, filename)
    except SketchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


@router.get("/{origin}/{sketch_id}")
def get_sketch(origin: str, sketch_id: str) -> dict[str, Any]:
    store = default_sketch_store()
    try:
        return store.get(origin, sketch_id).detail()
    except SketchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
