"""P2 strategy library API — validate/import/confirm plus additive version lifecycle.

The validation response is the app's only return channel to the terminal AI
(D2: no AI API). Every issue is emitted twice: structured, for the UI to lay
out, and as ``report_text`` — the exact ``format_report()`` string the Owner
pastes back unchanged.
"""

from __future__ import annotations

import logging
from math import isfinite
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from futures_research.api.routes_run_references import run_reference_catalog_for_request
from futures_research.backtest.persistence import RunIndexIntegrityError
from futures_research.backtest.run_reference_catalog import RunReferenceMigrationRequired
from futures_research.strategy.errors import StrategyValidationError
from futures_research.strategy.parser import parse_strategy_document
from futures_research.strategy.store import (
    NumericPatch,
    StrategyArchiveCollisionError,
    StrategyDerivationError,
    StrategyStorageError,
    StrategyStorageIntegrityError,
    StrategyStore,
    StrategyVersionNotFoundError,
    default_strategy_store,
)

router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])
LOGGER = logging.getLogger(__name__)


class StrategySourceBody(BaseModel):
    """Raw ``strategy.v1`` YAML as written by the terminal AI."""

    source_text: str = Field(min_length=1)
    filename: str | None = None


class StrategyNumericPatchBody(BaseModel):
    """One client-supplied number; identity and provenance remain server-owned."""

    model_config = ConfigDict(extra="forbid")

    path: str
    value: StrictInt | StrictFloat

    @field_validator("path")
    @classmethod
    def require_exact_path(cls, value: str) -> str:
        if not value or value != value.strip():
            msg = "patch path must be non-empty and have no surrounding whitespace"
            raise ValueError(msg)
        return value

    @field_validator("value")
    @classmethod
    def require_finite_number(cls, value: int | float) -> int | float:
        if type(value) not in (int, float):
            msg = "patch value must be a JSON number, not a boolean or other type"
            raise ValueError(msg)
        if isinstance(value, float) and not isfinite(value):
            msg = "patch value must be finite"
            raise ValueError(msg)
        return value


class StrategyDeriveBody(BaseModel):
    """Strict additive derive request; replacement documents are never accepted."""

    model_config = ConfigDict(extra="forbid")

    patches: list[StrategyNumericPatchBody] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_paths(self) -> StrategyDeriveBody:
        paths = [patch.path for patch in self.patches]
        if len(paths) != len(set(paths)):
            msg = "patch paths must be unique"
            raise ValueError(msg)
        return self


def _validation_payload(exc: StrategyValidationError) -> dict[str, Any]:
    return {
        "schema": "strategy_validation.v1",
        "valid": False,
        "issue_count": len(exc.issues),
        "issues": [
            {
                "path": issue.path,
                "message": issue.message,
                "fix": issue.fix,
                "layer": issue.layer,
                "line": issue.format_line(),
            }
            for issue in exc.issues
        ],
        # Paste-back payload: plain text, no markup, no UI decoration.
        "report_text": exc.format_report(),
    }


def _strategy_store(request: Request) -> StrategyStore:
    override = getattr(request.app.state, "strategy_store", None)
    if isinstance(override, StrategyStore):
        return override
    return default_strategy_store()


@router.post("/validate")
def validate_strategy(body: StrategySourceBody) -> dict[str, Any]:
    """Run the four validation layers without storing anything."""
    try:
        parsed = parse_strategy_document(body.source_text)
    except StrategyValidationError as exc:
        return _validation_payload(exc)
    return {
        "schema": "strategy_validation.v1",
        "valid": True,
        "issue_count": 0,
        "issues": [],
        "report_text": "",
        "name": parsed.document.meta.name,
        "universe": {
            "primary_instrument": parsed.document.universe.primary_instrument,
            "asset_class": parsed.document.universe.asset_class,
            "contracts": list(parsed.document.universe.contracts),
            "expansion_rationale": dict(parsed.document.universe.expansion_rationale),
            "session": parsed.document.universe.session,
        },
    }


@router.post("/import")
def import_strategy(body: StrategySourceBody, request: Request) -> dict[str, Any]:
    """Validate and store a new ``draft`` StrategyVersion (or return the twin)."""
    store = _strategy_store(request)
    try:
        outcome = store.import_document(body.source_text)
    except StrategyValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_payload(exc)) from exc
    return {
        "schema": "strategy_import.v1",
        "deduplicated": outcome.deduplicated,
        "message": (
            "內容同已存在版本 byte 完全相同，冇開新版本"
            if outcome.deduplicated
            else "已匯入為 draft，等 Owner 確認"
        ),
        "version": outcome.record,
    }


@router.get("")
def list_strategies(
    request: Request,
    status: Literal["draft", "confirmed"] | None = None,
) -> dict[str, Any]:
    """List stored strategy versions (P4 lists ``confirmed`` only)."""
    store = _strategy_store(request)
    versions = store.list_versions(status=status)
    return {
        "schema": "strategy_version_list.v1",
        "count": len(versions),
        "versions": versions,
    }


@router.get("/{strategy_id}")
def get_strategy(strategy_id: str, request: Request) -> dict[str, Any]:
    """Return one StrategyVersion including read-only parameters and YAML source."""
    store = _strategy_store(request)
    try:
        return store.get(strategy_id)
    except StrategyVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{strategy_id}/confirm")
def confirm_strategy(strategy_id: str, request: Request) -> dict[str, Any]:
    """Owner confirmation gate (journey S2c). Idempotent; content is immutable."""
    store = _strategy_store(request)
    try:
        return store.confirm(strategy_id)
    except StrategyVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StrategyValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_payload(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{parent_strategy_id}/derive")
def derive_strategy(
    parent_strategy_id: str,
    body: StrategyDeriveBody,
    request: Request,
) -> dict[str, Any]:
    """Create an immutable numeric-only direct child from one canonical parent."""
    store = _strategy_store(request)
    patches = tuple(
        NumericPatch(path=patch.path, value=patch.value)
        for patch in body.patches
    )
    try:
        outcome = store.derive(parent_strategy_id, patches)
    except StrategyVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StrategyValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_payload(exc)) from exc
    except StrategyDerivationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StrategyStorageError as exc:
        LOGGER.exception("strategy derive storage failure")
        raise HTTPException(
            status_code=503,
            detail="策略儲存暫時不可用；未有建立新版本",
        ) from exc
    return {
        "schema": "strategy_derive.v1",
        "parent_strategy_id": outcome.parent_strategy_id,
        "changed_count": len(outcome.changed_paths),
        "changed_paths": list(outcome.changed_paths),
        "deduplicated": outcome.deduplicated,
        "version": outcome.record,
    }


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: str, request: Request) -> dict[str, Any]:
    """Finalize the frontend grace period with a guarded lossless archive."""
    store = _strategy_store(request)
    try:
        store.get(strategy_id)
    except StrategyVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        LOGGER.exception("strategy active record could not be read before delete")
        raise HTTPException(
            status_code=503,
            detail="策略儲存完整性檢查失敗；未有刪除任何檔案",
        ) from exc

    try:
        catalog = run_reference_catalog_for_request(request)
        references = catalog.references_for_strategy(strategy_version=strategy_id)
    except (RunReferenceMigrationRequired, RunIndexIntegrityError, OSError) as exc:
        LOGGER.exception("run reference dependency unavailable for strategy delete")
        raise HTTPException(
            status_code=503,
            detail="run reference lookup 暫時不可用；未有刪除任何策略",
        ) from exc
    except Exception as exc:
        LOGGER.exception("unexpected run reference lookup failure for strategy delete")
        raise HTTPException(
            status_code=503,
            detail="run reference lookup 暫時不可用；未有刪除任何策略",
        ) from exc

    if references.unindexed_candidates:
        raise HTTPException(
            status_code=503,
            detail="未能證實 standard run 引用數量；請先修復 run reference index",
        )
    if references.runs:
        run_ids = sorted(reference.run_id for reference in references.runs)
        raise HTTPException(
            status_code=409,
            detail={
                "schema": "strategy_delete_blocked.v1",
                "message": "呢個策略版本仍有 standard run 引用，未能刪除",
                "standard_run_count": len(run_ids),
                "run_ids": run_ids,
            },
        )

    try:
        outcome = store.archive_delete(strategy_id)
    except StrategyVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StrategyArchiveCollisionError as exc:
        raise HTTPException(
            status_code=409,
            detail="策略歸檔已存在；未有覆蓋或刪除任何檔案",
        ) from exc
    except StrategyStorageIntegrityError as exc:
        LOGGER.exception("strategy archive integrity failure")
        raise HTTPException(
            status_code=503,
            detail="策略儲存完整性檢查失敗；未有刪除任何檔案",
        ) from exc
    except StrategyStorageError as exc:
        LOGGER.exception("strategy archive storage failure")
        raise HTTPException(
            status_code=503,
            detail="策略歸檔暫時不可用；原版本仍然保留",
        ) from exc
    return {
        "schema": "strategy_delete.v1",
        "strategy_id": outcome.strategy_id,
        "deleted_at": outcome.deleted_at,
        "archived_status": outcome.archived_status,
        "archived_to": outcome.archived_to,
    }
