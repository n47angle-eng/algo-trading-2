"""FastAPI application — results, chart, data (P3), batches (P4), strategies (P2)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from futures_research.api.routes_backtest_preview import router as backtest_preview_router
from futures_research.api.routes_batch import router as batch_router
from futures_research.api.routes_chart import router as chart_router
from futures_research.api.routes_data import router as data_router
from futures_research.api.routes_daytrade import router as daytrade_router
from futures_research.api.routes_insights import router as insights_router
from futures_research.api.routes_paper import router as paper_router
from futures_research.api.routes_promotion_decisions import (
    router as promotion_decisions_router,
)
from futures_research.api.routes_push import router as push_router
from futures_research.api.routes_results import router as results_router
from futures_research.api.routes_run_references import router as run_references_router
from futures_research.api.routes_sketches import router as sketches_router
from futures_research.api.routes_strategies import router as strategies_router
from futures_research.api.routes_system import router as system_router

app = FastAPI(
    title="Futures Research Platform",
    version="0.1.0",
    description="Personal futures research platform; no live-order execution.",
)

# Local Vite dev server. Production can tighten origins later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(results_router)
app.include_router(promotion_decisions_router)
app.include_router(paper_router)
app.include_router(daytrade_router)
app.include_router(run_references_router)
app.include_router(chart_router)
app.include_router(data_router)
app.include_router(batch_router)
app.include_router(backtest_preview_router)
app.include_router(strategies_router)
app.include_router(sketches_router)
app.include_router(insights_router)
app.include_router(system_router)
app.include_router(push_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Return a dependency-free readiness response for the initial service skeleton."""
    return {"status": "ok", "service": "futures-research"}
