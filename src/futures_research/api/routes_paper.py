"""Isolated P6 Stage A paper-trader provisioning API."""

from __future__ import annotations

import sqlite3
from pathlib import Path as FileSystemPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse

from futures_research.api.paper_provisioning import (
    PaperProvisioningAuthorizationPolicy,
    PaperProvisioningNotAuthorizedError,
    PaperProvisioningReadiness,
    PaperProvisioningReadinessRequest,
    PaperProvisioningRequestConflictError,
    PaperProvisioningSelectionMismatchError,
    evaluate_paper_provisioning_readiness,
)
from futures_research.api.paper_review import (
    PaperLedgerOrigin,
    PaperReviewCreateRequest,
    PaperReviewDomainError,
    PaperReviewErrorPayload,
    PaperReviewService,
    PaperReviewStatus,
    PaperReviewTerminalOpener,
    paper_review_error_http_status,
)
from futures_research.api.paper_review_artifact import (
    PaperReviewArtifactBuilder,
    PaperReviewArtifactReader,
)
from futures_research.api.paper_traders import (
    PaperActivationNotAuthorizedError,
    PaperActivationPolicy,
    PaperBaselineIntegrityError,
    PaperBaselineList,
    PaperBaselineNotFoundError,
    PaperBaselineSelection,
    PaperContractList,
    PaperContractSelection,
    PaperEligibilityRequiredError,
    PaperExternalReadinessInvalidError,
    PaperExternalReadinessProvider,
    PaperReadiness,
    PaperReadinessBlockedError,
    PaperReadinessRequest,
    PaperRequestConflictError,
    PaperRequestNotFoundError,
    PaperSelectionIdentityMismatchError,
    PaperTrader,
    PaperTraderCreateRequest,
    PaperTraderList,
    PaperTraderRequestStatus,
    PaperTraderStore,
    PaperTraderStoreIntegrityError,
    create_paper_trader,
    evaluate_paper_readiness,
    list_paper_baselines,
    list_paper_contracts,
)
from futures_research.api.promotion_decisions import (
    PromotionDecisionIntegrityError,
    PromotionDecisionStore,
)
from futures_research.api.results_catalog import ResultsCatalog
from futures_research.data.contracts import ContractRegistry

router = APIRouter(prefix="/api/v1/paper", tags=["paper"])

_CANONICAL_UUID4 = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CANONICAL_TRADER_ID = r"^trader-[0-9a-f]{32}$"
_CANONICAL_SNAPSHOT_ID = r"^paper-review-[0-9a-f]{32}$"


def _catalog(request: Request) -> ResultsCatalog:
    override = getattr(request.app.state, "results_catalog", None)
    if isinstance(override, ResultsCatalog):
        return override
    from futures_research.api.deps import get_results_catalog

    return get_results_catalog()


def _eligibility_store(request: Request) -> PromotionDecisionStore:
    override = getattr(request.app.state, "promotion_decision_store", None)
    if isinstance(override, PromotionDecisionStore):
        return override
    from futures_research.api.deps import get_promotion_decision_store

    return get_promotion_decision_store()


def _trader_store(request: Request) -> PaperTraderStore:
    override = getattr(request.app.state, "paper_trader_store", None)
    if isinstance(override, PaperTraderStore):
        return override
    from futures_research.api.deps import get_paper_trader_store

    return get_paper_trader_store()


def _activation_policy(request: Request) -> PaperActivationPolicy:
    override = getattr(request.app.state, "paper_activation_policy", None)
    if isinstance(override, PaperActivationPolicy):
        return override
    from futures_research.api.deps import get_paper_activation_policy

    return get_paper_activation_policy()


def _provisioning_authorization_policy(
    request: Request,
) -> PaperProvisioningAuthorizationPolicy:
    override = getattr(
        request.app.state,
        "paper_provisioning_authorization_policy",
        None,
    )
    if isinstance(override, PaperProvisioningAuthorizationPolicy):
        return override
    from futures_research.api.deps import (
        get_paper_provisioning_authorization_policy,
    )

    return get_paper_provisioning_authorization_policy()


def _readiness_provider(request: Request) -> PaperExternalReadinessProvider:
    override = getattr(request.app.state, "paper_external_readiness_provider", None)
    if isinstance(override, PaperExternalReadinessProvider):
        return override
    from futures_research.api.deps import get_paper_external_readiness_provider

    return get_paper_external_readiness_provider()


def _contract_registry(request: Request) -> ContractRegistry:
    override = getattr(request.app.state, "paper_contract_registry", None)
    if isinstance(override, ContractRegistry):
        return override
    from futures_research.api.deps import get_paper_contract_registry

    return get_paper_contract_registry()


CatalogDep = Annotated[ResultsCatalog, Depends(_catalog)]
EligibilityStoreDep = Annotated[
    PromotionDecisionStore,
    Depends(_eligibility_store),
]
TraderStoreDep = Annotated[PaperTraderStore, Depends(_trader_store)]
ActivationPolicyDep = Annotated[PaperActivationPolicy, Depends(_activation_policy)]
ProvisioningAuthorizationPolicyDep = Annotated[
    PaperProvisioningAuthorizationPolicy,
    Depends(_provisioning_authorization_policy),
]
ReadinessProviderDep = Annotated[
    PaperExternalReadinessProvider,
    Depends(_readiness_provider),
]
ContractRegistryDep = Annotated[ContractRegistry, Depends(_contract_registry)]
BaselineSelectionDep = Annotated[PaperBaselineSelection, Query()]
ContractSelectionDep = Annotated[PaperContractSelection, Query()]


def _review_service(
    request: Request,
    trader_store: TraderStoreDep,
) -> PaperReviewService:
    override = getattr(request.app.state, "paper_review_service", None)
    if isinstance(override, PaperReviewService):
        if override._store is not trader_store:
            raise _review_dependency_error(
                "Injected paper review service does not own the injected store."
            )
        return override
    if getattr(request.app.state, "paper_trader_store", None) is trader_store:
        return PaperReviewService(trader_store)
    from futures_research.api.deps import get_paper_review_service

    service = get_paper_review_service()
    if service._store is not trader_store:
        raise _review_dependency_error(
            "Default paper review service does not own the default store."
        )
    return service


def _review_artifact_root(request: Request) -> FileSystemPath:
    override = getattr(request.app.state, "paper_review_artifact_root", None)
    if override is None:
        from futures_research.api.deps import get_paper_review_artifact_root

        root = get_paper_review_artifact_root()
    elif isinstance(override, FileSystemPath):
        root = override
    else:
        raise _review_dependency_error(
            "Injected paper review artifact root is not an explicit path."
        )
    if not root.is_absolute():
        raise _review_dependency_error(
            "Paper review artifact root is not absolute."
        )
    return root


ReviewServiceDep = Annotated[PaperReviewService, Depends(_review_service)]
ReviewArtifactRootDep = Annotated[
    FileSystemPath,
    Depends(_review_artifact_root),
]


def _review_artifact_builder(
    request: Request,
    service: ReviewServiceDep,
    artifact_root: ReviewArtifactRootDep,
) -> PaperReviewArtifactBuilder:
    override = getattr(request.app.state, "paper_review_artifact_builder", None)
    if isinstance(override, PaperReviewArtifactBuilder):
        if (
            override._service is not service
            or override._artifact_root != artifact_root
        ):
            raise _review_dependency_error(
                "Injected paper review builder has a different authority."
            )
        return override
    return PaperReviewArtifactBuilder(
        service,
        artifact_root=artifact_root,
    )


ReviewArtifactBuilderDep = Annotated[
    PaperReviewArtifactBuilder,
    Depends(_review_artifact_builder),
]


def _review_dependency_error(message: str) -> HTTPException:
    payload = PaperReviewErrorPayload(
        schema="paper_review_error.v1",
        code="snapshot_integrity_failed",
        message=message,
        retryable=False,
        request_id=None,
        snapshot_id=None,
        progress=None,
        issues=(),
    )
    return _review_http_error(payload)


def _review_http_error(
    error: PaperReviewErrorPayload,
) -> HTTPException:
    return HTTPException(
        status_code=paper_review_error_http_status(error.code),
        detail=error.model_dump(by_alias=True, mode="json"),
    )


def _translate_review_error(exc: PaperReviewDomainError) -> HTTPException:
    return _review_http_error(exc.payload)


def _review_status_error(status: PaperReviewStatus) -> HTTPException:
    if status.status != "failed" or status.error is None:
        raise ValueError("only a failed review status can become an HTTP error")
    return _review_http_error(status.error)


def _business_error(
    *,
    status_code: int,
    code: str,
    message: str,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "schema": "paper_api_error.v1",
            "code": code,
            "message": message,
            "retryable": False,
        },
    )


def _translate_business_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PaperEligibilityRequiredError):
        return _business_error(
            status_code=409,
            code="eligible_strategy_required",
            message=str(exc),
        )
    if isinstance(exc, PaperBaselineNotFoundError):
        return _business_error(
            status_code=404,
            code="baseline_not_found",
            message=str(exc),
        )
    if isinstance(exc, PaperSelectionIdentityMismatchError):
        return _business_error(
            status_code=409,
            code="selection_identity_mismatch",
            message=str(exc),
        )
    if isinstance(exc, PaperBaselineIntegrityError):
        return _business_error(
            status_code=503,
            code="baseline_integrity_failed",
            message=str(exc),
        )
    if isinstance(exc, PaperReadinessBlockedError):
        return _business_error(
            status_code=409,
            code="readiness_blocked",
            message=str(exc),
        )
    if isinstance(exc, PaperExternalReadinessInvalidError):
        return _business_error(
            status_code=503,
            code="external_readiness_invalid",
            message=str(exc),
        )
    if isinstance(exc, PaperProvisioningSelectionMismatchError):
        return _business_error(
            status_code=409,
            code="provisioning_selection_mismatch",
            message=str(exc),
        )
    if isinstance(exc, PaperProvisioningNotAuthorizedError):
        return _business_error(
            status_code=503,
            code="provisioning_not_authorized",
            message=str(exc),
        )
    if isinstance(exc, PaperProvisioningRequestConflictError):
        return _business_error(
            status_code=409,
            code="provisioning_request_conflict",
            message=str(exc),
        )
    if isinstance(exc, PaperActivationNotAuthorizedError):
        return _business_error(
            status_code=503,
            code="activation_not_authorized",
            message=str(exc),
        )
    if isinstance(exc, PaperRequestConflictError):
        return _business_error(
            status_code=409,
            code="request_id_conflict",
            message=str(exc),
        )
    if isinstance(exc, PaperRequestNotFoundError):
        return _business_error(
            status_code=404,
            code="request_not_found",
            message=str(exc),
        )
    return _business_error(
        status_code=503,
        code="store_unavailable",
        message=str(exc) or "paper trader store is unavailable",
    )


@router.get("/baselines", response_model=PaperBaselineList)
def get_paper_baselines(
    selection: BaselineSelectionDep,
    catalog: CatalogDep,
    registry: ContractRegistryDep,
    eligibility_store: EligibilityStoreDep,
) -> PaperBaselineList:
    """List verified non-validation baselines for one eligible strategy version."""
    try:
        return list_paper_baselines(
            catalog=catalog,
            registry=registry,
            eligibility_store=eligibility_store,
            selection=selection,
        )
    except (
        PaperEligibilityRequiredError,
        PaperBaselineIntegrityError,
        PaperBaselineNotFoundError,
        PromotionDecisionIntegrityError,
        OSError,
        sqlite3.Error,
    ) as exc:
        raise _translate_business_error(exc) from exc


@router.get("/contracts", response_model=PaperContractList)
def get_paper_contracts(
    selection: ContractSelectionDep,
    catalog: CatalogDep,
    registry: ContractRegistryDep,
    eligibility_store: EligibilityStoreDep,
) -> PaperContractList:
    """List only contracts with a verified baseline for an eligible strategy."""
    try:
        return list_paper_contracts(
            catalog=catalog,
            registry=registry,
            eligibility_store=eligibility_store,
            selection=selection,
        )
    except (
        PaperEligibilityRequiredError,
        PaperBaselineIntegrityError,
        PaperBaselineNotFoundError,
        PromotionDecisionIntegrityError,
        OSError,
        sqlite3.Error,
    ) as exc:
        raise _translate_business_error(exc) from exc


@router.post("/readiness", response_model=PaperReadiness)
def get_paper_readiness(
    body: PaperReadinessRequest,
    catalog: CatalogDep,
    registry: ContractRegistryDep,
    eligibility_store: EligibilityStoreDep,
    readiness_provider: ReadinessProviderDep,
) -> PaperReadiness:
    """Freshly evaluate the exact four Stage A readiness checks."""
    try:
        readiness, _baseline = evaluate_paper_readiness(
            catalog=catalog,
            registry=registry,
            eligibility_store=eligibility_store,
            provider=readiness_provider,
            selection=body.selection,
        )
        return readiness
    except (
        PaperEligibilityRequiredError,
        PaperBaselineNotFoundError,
        PaperSelectionIdentityMismatchError,
        PaperBaselineIntegrityError,
        PaperReadinessBlockedError,
        PromotionDecisionIntegrityError,
        OSError,
        sqlite3.Error,
    ) as exc:
        raise _translate_business_error(exc) from exc


@router.post(
    "/provisioning-readiness",
    response_model=PaperProvisioningReadiness,
)
def get_paper_provisioning_readiness(
    body: PaperProvisioningReadinessRequest,
    catalog: CatalogDep,
    registry: ContractRegistryDep,
    eligibility_store: EligibilityStoreDep,
    readiness_provider: ReadinessProviderDep,
    policy: ProvisioningAuthorizationPolicyDep,
) -> PaperProvisioningReadiness:
    """Project one-off provisioning authority without claiming or writing."""
    try:
        return evaluate_paper_provisioning_readiness(
            catalog=catalog,
            registry=registry,
            eligibility_store=eligibility_store,
            provider=readiness_provider,
            policy=policy,
            selection=body.selection,
        )
    except (
        PaperEligibilityRequiredError,
        PaperBaselineNotFoundError,
        PaperSelectionIdentityMismatchError,
        PaperBaselineIntegrityError,
        PaperExternalReadinessInvalidError,
        PromotionDecisionIntegrityError,
        OSError,
        sqlite3.Error,
    ) as exc:
        raise _translate_business_error(exc) from exc


@router.post("/traders", response_model=PaperTrader)
def post_paper_trader(
    body: PaperTraderCreateRequest,
    request: Request,
    catalog: CatalogDep,
    registry: ContractRegistryDep,
    eligibility_store: EligibilityStoreDep,
    readiness_provider: ReadinessProviderDep,
    authorization_policy: ProvisioningAuthorizationPolicyDep,
    trader_store: TraderStoreDep,
) -> JSONResponse:
    """Create once or safely recover the same immutable provisioning record.

    Also mirrors into the v4 PaperRuntimeStore under the same trader_id so
    runtime/timeline/review-v2 routes share product identity with Stage A.
    """
    try:
        record, created = create_paper_trader(
            body=body,
            catalog=catalog,
            registry=registry,
            eligibility_store=eligibility_store,
            readiness_provider=readiness_provider,
            authorization_policy=authorization_policy,
            trader_store=trader_store,
        )
    except (
        PaperEligibilityRequiredError,
        PaperBaselineNotFoundError,
        PaperSelectionIdentityMismatchError,
        PaperBaselineIntegrityError,
        PaperExternalReadinessInvalidError,
        PaperReadinessBlockedError,
        PaperActivationNotAuthorizedError,
        PaperProvisioningSelectionMismatchError,
        PaperProvisioningNotAuthorizedError,
        PaperProvisioningRequestConflictError,
        PaperRequestConflictError,
        PaperTraderStoreIntegrityError,
        PromotionDecisionIntegrityError,
        OSError,
        sqlite3.Error,
    ) as exc:
        raise _translate_business_error(exc) from exc
    try:
        from futures_research.api.paper_runtime_manager import (
            mirror_stage_a_trader_to_runtime,
        )

        mirror_stage_a_trader_to_runtime(
            stage_a=record,
            catalog=catalog,
            runtime_store=_runtime_store(request),
            stage_a_store_path=trader_store.path,
        )
    except Exception as exc:
        # Fail closed: Stage A row must not claim a runtime that was not mirrored.
        raise HTTPException(
            status_code=503,
            detail={
                "schema": "paper_runtime_error.v1",
                "code": "runtime_mirror_failed",
                "message": (
                    "trader was provisioned but runtime store mirror failed; "
                    f"{exc}"
                ),
                "retryable": True,
            },
        ) from exc
    return JSONResponse(
        status_code=201 if created else 200,
        content=record.model_dump(by_alias=True, mode="json"),
    )


@router.get(
    "/trader-requests/{request_id}",
    response_model=PaperTraderRequestStatus,
)
def get_paper_trader_request(
    request_id: Annotated[str, Path(pattern=_CANONICAL_UUID4)],
    trader_store: TraderStoreDep,
) -> PaperTraderRequestStatus:
    """Recover an immutable create outcome without repeating the POST."""
    try:
        result = trader_store.request_status(request_id)
        if result is None:
            raise PaperRequestNotFoundError(
                f"paper trader request not found: {request_id}"
            )
        return result
    except (
        PaperRequestNotFoundError,
        PaperTraderStoreIntegrityError,
        OSError,
        sqlite3.Error,
    ) as exc:
        raise _translate_business_error(exc) from exc


@router.get("/traders", response_model=PaperTraderList)
def list_paper_traders(trader_store: TraderStoreDep) -> PaperTraderList:
    """List complete immutable provisioning records without lazy creation."""
    try:
        return trader_store.list()
    except (PaperTraderStoreIntegrityError, OSError, sqlite3.Error) as exc:
        raise _translate_business_error(exc) from exc


@router.get(
    "/traders/{trader_id}/ledger-origin",
    response_model=PaperLedgerOrigin,
)
def get_paper_ledger_origin(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    service: ReviewServiceDep,
) -> PaperLedgerOrigin:
    """Project the exact immutable Stage B ledger origin without writing."""
    try:
        return service.ledger_origin(trader_id)
    except PaperReviewDomainError as exc:
        raise _translate_review_error(exc) from exc


@router.post(
    "/traders/{trader_id}/review-snapshots",
    response_model=PaperReviewStatus,
)
def post_paper_review_snapshot(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    body: PaperReviewCreateRequest,
    service: ReviewServiceDep,
    builder: ReviewArtifactBuilderDep,
) -> JSONResponse:
    """Accept once and synchronously drive one exact artifact builder."""
    try:
        status, created = service.accept_review_snapshot(
            trader_id=trader_id,
            body=body,
        )
    except PaperReviewDomainError as exc:
        raise _translate_review_error(exc) from exc
    if created:
        try:
            status = builder.build(status.request_id)
        except Exception:
            try:
                status = service.review_request_status(body.request_id)
            except PaperReviewDomainError as exc:
                raise _translate_review_error(exc) from None
            if status.status != "failed":
                fallback = PaperReviewDomainError(
                    "artifact_build_failed",
                    "Review artifact build failed without a stable ready outcome.",
                    request_id=status.request_id,
                    snapshot_id=status.snapshot_id,
                    progress=status.progress,
                )
                raise _translate_review_error(fallback) from None
    if status.status == "failed":
        raise _review_status_error(status)
    if status.status == "preparing":
        status_code = 202
    elif created:
        status_code = 201
    else:
        status_code = 200
    return JSONResponse(
        status_code=status_code,
        content=status.model_dump(by_alias=True, mode="json"),
    )


@router.get(
    "/review-requests/{request_id}",
    response_model=PaperReviewStatus,
)
def get_paper_review_request(
    request_id: Annotated[str, Path(pattern=_CANONICAL_UUID4)],
    service: ReviewServiceDep,
) -> PaperReviewStatus:
    """Return preparing, ready, persisted failed, or effective interrupted state."""
    try:
        return service.review_request_status(request_id)
    except PaperReviewDomainError as exc:
        raise _translate_review_error(exc) from exc


@router.get("/review-snapshots/{snapshot_id}/download")
def get_paper_review_download(
    snapshot_id: Annotated[str, Path(pattern=_CANONICAL_SNAPSHOT_ID)],
    service: ReviewServiceDep,
    artifact_root: ReviewArtifactRootDep,
) -> Response:
    """Return only fully captured bytes that match the persisted ready claim."""
    try:
        download = PaperReviewArtifactReader(
            service,
            artifact_root=artifact_root,
        ).read(snapshot_id)
    except PaperReviewDomainError as exc:
        raise _translate_review_error(exc) from exc
    return Response(
        content=download.content,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{download.display_filename}"'
            )
        },
    )


@router.get(
    "/review-snapshots/{snapshot_id}/terminal-opener",
    response_model=PaperReviewTerminalOpener,
)
def get_paper_review_terminal_opener(
    snapshot_id: Annotated[str, Path(pattern=_CANONICAL_SNAPSHOT_ID)],
    service: ReviewServiceDep,
) -> PaperReviewTerminalOpener:
    """Return the persisted exact terminal opener; never regenerate it."""
    try:
        return service.terminal_opener(snapshot_id)
    except PaperReviewDomainError as exc:
        raise _translate_review_error(exc) from exc


@router.get("/traders/{trader_id}", response_model=PaperTrader)
def get_paper_trader(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    trader_store: TraderStoreDep,
) -> PaperTrader:
    """Read one complete immutable provisioning record."""
    try:
        result = trader_store.get(trader_id)
        if result is None:
            raise PaperRequestNotFoundError(f"paper trader not found: {trader_id}")
        return result
    except (
        PaperRequestNotFoundError,
        PaperTraderStoreIntegrityError,
        OSError,
        sqlite3.Error,
    ) as exc:
        raise _translate_business_error(exc) from exc


# ---------------------------------------------------------------------------
# P6 runtime surface (v4 store) — capabilities, snapshot, timeline, commands,
# paper-review.v2. IB is market-data only; no order transport is registered.
# ---------------------------------------------------------------------------


class _NoSignalDecisionSource:
    """Lifecycle-only decision source (no new entries on command paths)."""

    def process(self, update: object) -> object:
        from futures_research.backtest.strategy import StrategyUpdate

        del update
        return StrategyUpdate(events=(), entry_intents=())


def _runtime_store(request: Request | None = None, *, create: bool = True):
    """Resolve v4 runtime store — tests may inject app.state.paper_runtime_store."""
    from pathlib import Path

    from futures_research.api.deps import open_paper_runtime_store
    from futures_research.paper.store import PaperRuntimeStore

    if request is not None:
        override = getattr(request.app.state, "paper_runtime_store", None)
        if override is not None:
            return override
        path = getattr(request.app.state, "paper_runtime_store_path", None)
        if path is not None:
            path = Path(path)
            if not create and not path.is_file():
                raise FileNotFoundError(str(path))
            if create:
                path.parent.mkdir(parents=True, exist_ok=True)
            store = PaperRuntimeStore(path)
            store.initialize()
            request.app.state.paper_runtime_store = store
            return store
    return open_paper_runtime_store(create=create)


def _runtime_http_error(exc: Exception) -> HTTPException:
    from futures_research.api.paper_runtime_service import PaperRuntimeServiceError
    from futures_research.paper.review_v2 import (
        PaperReviewV2Error,
        PaperReviewV2MissingMemberError,
    )

    if isinstance(exc, PaperRuntimeServiceError):
        status = 404 if exc.code == "trader_not_found" else 409
        if exc.code in {"invalid_request_id", "invalid_cursor"}:
            status = 422
        if exc.code in {"stale_lifecycle_version", "selection_fingerprint_mismatch"}:
            status = 409
        return HTTPException(
            status_code=status,
            detail={
                "schema": "paper_runtime_error.v1",
                "code": exc.code,
                "message": exc.message,
                "retryable": False,
            },
        )
    if isinstance(exc, PaperReviewV2MissingMemberError):
        return HTTPException(
            status_code=422,
            detail={
                "schema": "paper_runtime_error.v1",
                "code": "review_v2_missing_member",
                "message": str(exc),
                "retryable": False,
            },
        )
    if isinstance(exc, PaperReviewV2Error):
        return HTTPException(
            status_code=404,
            detail={
                "schema": "paper_runtime_error.v1",
                "code": "review_v2_failed",
                "message": str(exc),
                "retryable": False,
            },
        )
    return HTTPException(
        status_code=503,
        detail={
            "schema": "paper_runtime_error.v1",
            "code": "runtime_unavailable",
            "message": str(exc) or "paper runtime unavailable",
            "retryable": True,
        },
    )


@router.get("/runtime-capabilities")
def get_paper_runtime_capabilities() -> dict:
    """Public timeframe/safety/lifecycle capability registry."""
    from futures_research.api.paper_runtime_service import runtime_capabilities

    return runtime_capabilities()


@router.get("/runtime/gateway-status")
def get_paper_gateway_status() -> dict:
    """IB Gateway port + session status (market-data only; zero order paths)."""
    from futures_research.api.paper_runtime_manager import gateway_status

    return gateway_status()


@router.get("/fleet-overview")
def get_paper_fleet_overview(
    request: Request,
    trader_store: TraderStoreDep,
) -> dict:
    """Multi-trader board: Stage A identity + runtime PnL/position/safety."""
    from futures_research.api.paper_runtime_manager import build_fleet_overview

    try:
        listing = trader_store.list()
        stage_rows = [
            t.model_dump(by_alias=True, mode="json") for t in listing.traders
        ]
    except (PaperTraderStoreIntegrityError, OSError, sqlite3.Error) as exc:
        raise _translate_business_error(exc) from exc
    runtime = None
    try:
        # Never lazy-create data/paper on a pure overview GET.
        runtime = _runtime_store(request, create=False)
    except FileNotFoundError:
        runtime = None
    except Exception:
        runtime = None
    return build_fleet_overview(
        stage_a_traders=stage_rows,
        runtime_store=runtime,
    )


@router.get("/traders/{trader_id}/runtime")
def get_paper_trader_runtime(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    request: Request,
) -> dict:
    """Bounded runtime snapshot for polling consumers."""
    from futures_research.api.paper_runtime_service import (
        PaperRuntimeServiceError,
        runtime_snapshot,
    )

    try:
        store = _runtime_store(request, create=False)
        return runtime_snapshot(store, trader_id)
    except FileNotFoundError as exc:
        raise _runtime_http_error(
            PaperRuntimeServiceError(
                "trader_not_found", f"paper trader not found: {trader_id}"
            )
        ) from exc
    except PaperRuntimeServiceError as exc:
        raise _runtime_http_error(exc) from exc
    except (OSError, sqlite3.Error) as exc:
        raise _runtime_http_error(exc) from exc


@router.get("/traders/{trader_id}/timeline")
def get_paper_trader_timeline(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    request: Request,
    after_cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """Cursor-ordered lifecycle timeline (control states only — not trades)."""
    from futures_research.api.paper_runtime_service import (
        PaperRuntimeServiceError,
        runtime_timeline,
    )

    try:
        return runtime_timeline(
            _runtime_store(request),
            trader_id,
            after_cursor=after_cursor,
            limit=limit,
        )
    except PaperRuntimeServiceError as exc:
        raise _runtime_http_error(exc) from exc
    except (OSError, sqlite3.Error) as exc:
        raise _runtime_http_error(exc) from exc


@router.get("/traders/{trader_id}/activity")
def get_paper_trader_activity(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    request: Request,
    after_cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """Trade activity stream: buy_opportunity / entry / exit for notifications."""
    from futures_research.api.paper_runtime_service import (
        PaperRuntimeServiceError,
        runtime_activity,
    )

    try:
        return runtime_activity(
            _runtime_store(request),
            trader_id,
            after_cursor=after_cursor,
            limit=limit,
        )
    except PaperRuntimeServiceError as exc:
        raise _runtime_http_error(exc) from exc
    except (OSError, sqlite3.Error) as exc:
        raise _runtime_http_error(exc) from exc


@router.post("/traders/{trader_id}/runtime-preflight")
def post_paper_runtime_preflight(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    body: dict,
    request: Request,
) -> dict:
    """Read-only preflight — zero state-changing writes."""
    from futures_research.api.paper_runtime_service import (
        PaperRuntimeServiceError,
        preflight_runtime,
    )

    try:
        return preflight_runtime(
            _runtime_store(request),
            trader_id=trader_id,
            request_id=str(body.get("request_id", "")),
            expected_lifecycle_version=int(body.get("expected_lifecycle_version", -1)),
            selection_fingerprint=str(body.get("selection_fingerprint", "")),
            requested_market_mode=body.get("requested_market_mode", "test_delayed"),  # type: ignore[arg-type]
            gateway_reachable=None,
        )
    except PaperRuntimeServiceError as exc:
        raise _runtime_http_error(exc) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "schema": "paper_runtime_error.v1",
                "code": "invalid_preflight_body",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc


def _resolve_runtime_contract(
    trader_id: str,
    request: Request | None = None,
    *,
    create: bool = True,
):
    from futures_research.api.paper_runtime_manager import resolve_contract_for_trader
    from futures_research.api.paper_runtime_service import PaperRuntimeServiceError

    try:
        store = _runtime_store(request, create=create)
    except FileNotFoundError as exc:
        raise _runtime_http_error(
            PaperRuntimeServiceError(
                "trader_not_found", f"paper trader not found: {trader_id}"
            )
        ) from exc
    try:
        trader = store.get_trader(trader_id)
    except LookupError as exc:
        raise _runtime_http_error(
            PaperRuntimeServiceError("trader_not_found", str(exc))
        ) from exc
    try:
        contract = resolve_contract_for_trader(trader.selection.contract_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "schema": "paper_runtime_error.v1",
                "code": "contract_not_in_registry",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    return store, trader, contract


def _lifecycle_command(
    trader_id: str,
    command: str,
    body: dict,
    request: Request | None = None,
) -> dict:
    from datetime import UTC, datetime
    from uuid import UUID

    from futures_research.api.paper_runtime_manager import (
        get_or_create_runtime,
        start_runtime,
    )
    from futures_research.api.paper_runtime_service import PaperRuntimeServiceError
    from futures_research.paper.models import canonical_utc
    from futures_research.paper.store import PaperStoreLifecycleConflictError

    store, trader, contract = _resolve_runtime_contract(trader_id, request)
    request_id = str(body.get("request_id", ""))
    try:
        parsed = UUID(request_id)
        if parsed.version != 4 or str(parsed) != request_id:
            raise ValueError("bad uuid")
    except (ValueError, AttributeError) as exc:
        raise _runtime_http_error(
            PaperRuntimeServiceError(
                "invalid_request_id", "request_id must be canonical UUID4"
            )
        ) from exc
    expected_version = int(body.get("expected_lifecycle_version", -1))
    fingerprint = str(body.get("selection_fingerprint", ""))
    if trader.lifecycle_version != expected_version:
        raise _runtime_http_error(
            PaperRuntimeServiceError(
                "stale_lifecycle_version",
                "lifecycle version does not match; zero write performed",
            )
        )
    if trader.selection_fingerprint != fingerprint:
        raise _runtime_http_error(
            PaperRuntimeServiceError(
                "selection_fingerprint_mismatch",
                "selection fingerprint does not match locked trader",
            )
        )
    now = datetime.now(UTC)
    try:
        if command == "start":
            started = start_runtime(
                store=store,
                trader_id=trader_id,
                contract=contract,
                now=now,
                decision_mode="product",
                attach_gateway=True,
                gateway_mode=str(body.get("gateway_mode") or "test_delayed"),  # type: ignore[arg-type]
            )
            return {
                "schema": "paper_runtime_command.v1",
                "request_id": request_id,
                "trader_id": trader_id,
                "command": "start",
                "lifecycle": started["lifecycle"],
                "lifecycle_version": started["lifecycle_version"],
                "decision_source": started["decision_source"],
                "gateway": started.get("gateway"),
                "accepted_at": canonical_utc(now),
            }
        runtime = get_or_create_runtime(
            store=store,
            trader_id=trader_id,
            contract=contract,
        )
        if command == "pause":
            snap = runtime.request_pause(now=now)
        elif command == "resume":
            current = store.get_trader(trader_id)
            if current.lifecycle != "paused":
                raise PaperRuntimeServiceError(
                    "invalid_lifecycle_transition",
                    f"cannot resume from {current.lifecycle}",
                )
            snap = runtime.start(now=now)
        elif command == "permanent_stop":
            snap = runtime.request_permanent_stop(now=now)
        else:
            raise PaperRuntimeServiceError("unknown_command", command)
        return {
            "schema": "paper_runtime_command.v1",
            "request_id": request_id,
            "trader_id": trader_id,
            "command": command,
            "lifecycle": snap.lifecycle,
            "lifecycle_version": snap.lifecycle_version,
            "accepted_at": canonical_utc(now),
        }
    except PaperRuntimeServiceError as exc:
        raise _runtime_http_error(exc) from exc
    except PaperStoreLifecycleConflictError as exc:
        raise _runtime_http_error(
            PaperRuntimeServiceError("lifecycle_conflict", str(exc))
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise _runtime_http_error(exc) from exc


@router.post("/traders/{trader_id}/runtime/start")
def post_paper_runtime_start(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    body: dict,
    request: Request,
) -> dict:
    return _lifecycle_command(trader_id, "start", body, request)


@router.post("/traders/{trader_id}/runtime/pause")
def post_paper_runtime_pause(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    body: dict,
    request: Request,
) -> dict:
    return _lifecycle_command(trader_id, "pause", body, request)


@router.post("/traders/{trader_id}/runtime/resume")
def post_paper_runtime_resume(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    body: dict,
    request: Request,
) -> dict:
    return _lifecycle_command(trader_id, "resume", body, request)


@router.post("/traders/{trader_id}/runtime/permanent-stop")
def post_paper_runtime_permanent_stop(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    body: dict,
    request: Request,
) -> dict:
    return _lifecycle_command(trader_id, "permanent_stop", body, request)


@router.post("/traders/{trader_id}/runtime/replay")
def post_paper_runtime_replay(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    body: dict,
    request: Request,
) -> dict:
    """Product MD/replay ingest: drive closed bars through real decision/execution.

    When IB Gateway is unavailable, Owner/tests POST closed bars (or request
    demo bars) so simulated decisions/fills stay on the registered HTTP path.
    """
    from futures_research.api.paper_runtime_manager import (
        default_demo_replay_bars,
        process_replay_bars,
    )
    from futures_research.api.paper_runtime_service import PaperRuntimeServiceError

    store, trader, contract = _resolve_runtime_contract(trader_id, request)
    bars = body.get("bars")
    if bars is None and body.get("use_demo_bars") is True:
        bars = default_demo_replay_bars(contract_id=trader.selection.contract_id)
    if not isinstance(bars, list) or not bars:
        raise HTTPException(
            status_code=422,
            detail={
                "schema": "paper_runtime_error.v1",
                "code": "invalid_replay_body",
                "message": "bars must be a non-empty list, or use_demo_bars=true",
                "retryable": False,
            },
        )
    try:
        return process_replay_bars(
            store=store,
            trader_id=trader_id,
            contract=contract,
            bars=bars,
            mode=str(body.get("mode") or "replay_test"),  # type: ignore[arg-type]
        )
    except (ValueError, RuntimeError) as exc:
        raise _runtime_http_error(
            PaperRuntimeServiceError("replay_failed", str(exc))
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise _runtime_http_error(exc) from exc


@router.get("/traders/{trader_id}/chart")
def get_paper_trader_chart(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    request: Request,
    after_cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """Runtime chart bars accumulated from product replay/MD ingest."""
    from futures_research.api.paper_runtime_manager import runtime_chart
    from futures_research.api.paper_runtime_service import PaperRuntimeServiceError

    store = _runtime_store(request)
    try:
        store.get_trader(trader_id)
    except LookupError as exc:
        raise _runtime_http_error(
            PaperRuntimeServiceError("trader_not_found", str(exc))
        ) from exc
    return runtime_chart(trader_id, after_cursor=after_cursor, limit=limit)


@router.post("/traders/{trader_id}/review-v2")
def post_paper_review_v2(
    trader_id: Annotated[str, Path(pattern=_CANONICAL_TRADER_ID)],
    body: dict,
    request: Request,
) -> Response:
    """Build immutable paper-review.v2 zip from committed runtime evidence."""
    from futures_research.paper.review_v2 import (
        PaperReviewV2Error,
        build_paper_review_v2,
    )

    request_id = str(body.get("request_id", ""))
    try:
        package = build_paper_review_v2(
            _runtime_store(request),
            trader_id=trader_id,
            request_id=request_id,
        )
    except PaperReviewV2Error as exc:
        raise _runtime_http_error(exc) from exc
    except (OSError, sqlite3.Error) as exc:
        raise _runtime_http_error(exc) from exc
    return Response(
        content=package.zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{package.snapshot_id}.zip"'
            ),
            "X-Paper-Review-Schema": "paper-review.v2",
            "X-Paper-Review-Sha256": package.package_sha256,
        },
    )
