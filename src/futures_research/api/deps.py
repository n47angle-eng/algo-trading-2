"""FastAPI dependency helpers for read-only result access."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from futures_research.api.paper_provisioning import (
    DisabledPaperProvisioningAuthorizationPolicy,
    PaperProvisioningAuthorizationPolicy,
)
from futures_research.api.paper_review import PaperReviewService
from futures_research.api.paper_traders import (
    DefaultDenyPaperActivationPolicy,
    DefaultPaperExternalReadinessProvider,
    PaperActivationPolicy,
    PaperExternalReadinessProvider,
    PaperTraderStore,
)
from futures_research.api.promotion_decisions import PromotionDecisionStore
from futures_research.api.results_catalog import ResultsCatalog
from futures_research.data.contracts import ContractRegistry
from futures_research.paths import PROJECT_ROOT


@lru_cache(maxsize=1)
def get_results_catalog() -> ResultsCatalog:
    """Default catalog rooted at ``data/backtests/results`` under the project."""
    root = PROJECT_ROOT / "data" / "backtests" / "results"
    return ResultsCatalog(results_root=root)


def catalog_from_root(results_root: Path) -> ResultsCatalog:
    """Build a catalog for tests with an isolated temp results directory."""
    return ResultsCatalog(results_root=results_root)


@lru_cache(maxsize=1)
def get_promotion_decision_store() -> PromotionDecisionStore:
    """Default append-only decision store, separate from immutable run tables."""
    path = PROJECT_ROOT / "data" / "backtests" / "promotion-decisions.sqlite3"
    return PromotionDecisionStore(path)


@lru_cache(maxsize=1)
def get_paper_trader_store() -> PaperTraderStore:
    """Return the default P6 handle without opening or creating its database."""
    path = PROJECT_ROOT / "data" / "paper" / "paper-traders.sqlite3"
    return PaperTraderStore(path)


@lru_cache(maxsize=1)
def get_paper_review_service() -> PaperReviewService:
    """Share one process-local review service without opening its default store."""
    return PaperReviewService(get_paper_trader_store())


def get_paper_review_artifact_root() -> Path:
    """Return the semantic default root without creating any directory."""
    return PROJECT_ROOT / "data" / "paper" / "review-artifacts"


@lru_cache(maxsize=1)
def get_paper_activation_policy() -> PaperActivationPolicy:
    """Keep normal Stage A paper-trader creation explicitly disabled."""
    return DefaultDenyPaperActivationPolicy()


@lru_cache(maxsize=1)
def get_paper_provisioning_authorization_policy(
) -> PaperProvisioningAuthorizationPolicy:
    """Keep normal one-off paper provisioning explicitly disabled."""
    return DisabledPaperProvisioningAuthorizationPolicy()


@lru_cache(maxsize=1)
def get_paper_external_readiness_provider() -> PaperExternalReadinessProvider:
    """Report only truthful unknown external readiness without network probes."""
    return DefaultPaperExternalReadinessProvider()


@lru_cache(maxsize=1)
def get_paper_contract_registry() -> ContractRegistry:
    """Load immutable contract truth used to validate P6 baseline provenance."""
    return ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")


@lru_cache(maxsize=1)
def get_paper_runtime_store_path() -> Path:
    """Default v4 runtime store path (created lazily on first write)."""
    return PROJECT_ROOT / "data" / "paper" / "runtime.sqlite3"


_RUNTIME_STORE_SINGLETON: "PaperRuntimeStore | None" = None


def open_paper_runtime_store(*, create: bool = True) -> "PaperRuntimeStore":
    """Open the v4 runtime store under data/paper/**.

    ``create=False`` never mkdirs or creates a DB — used by pure GET surfaces
    so default ``data/paper`` stays absent until an Owner write path runs.
    """
    global _RUNTIME_STORE_SINGLETON
    from futures_research.paper.store import PaperRuntimeStore

    path = get_paper_runtime_store_path()
    if not create and not path.is_file():
        raise FileNotFoundError(f"paper runtime store not present: {path}")
    if _RUNTIME_STORE_SINGLETON is not None and _RUNTIME_STORE_SINGLETON.path == path:
        if path.is_file() or create:
            return _RUNTIME_STORE_SINGLETON
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    store = PaperRuntimeStore(path)
    store.initialize()
    _RUNTIME_STORE_SINGLETON = store
    return store


def clear_paper_runtime_store_singleton() -> None:
    """Test helper — drop the process-local runtime store handle."""
    global _RUNTIME_STORE_SINGLETON
    _RUNTIME_STORE_SINGLETON = None
