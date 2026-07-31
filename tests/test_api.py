"""Smoke tests for the initial API application skeleton."""

import httpx
import pytest

from futures_research.api.main import app


@pytest.mark.asyncio
async def test_health_endpoint_is_ready() -> None:
    """The service skeleton should be runnable before UI work begins."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "futures-research"}


@pytest.mark.asyncio
async def test_browser_preflight_covers_every_exposed_method() -> None:
    """A method the API exposes but CORS omits is unreachable from the browser.

    The P2 version library deletes a strategy with a real cross-origin DELETE,
    so the preflight — not just the route — has to allow it.  An unexposed
    method must still be refused, otherwise this assertion would pass on any
    permissive configuration.
    """
    transport = httpx.ASGITransport(app=app)
    origin = "http://localhost:5173"
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        allowed = await client.options(
            "/api/v1/strategies/strategy-0001",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "DELETE",
            },
        )
        refused = await client.options(
            "/api/v1/strategies/strategy-0001",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PATCH",
            },
        )

    assert allowed.status_code == 200
    methods = {
        method.strip()
        for method in allowed.headers["access-control-allow-methods"].split(",")
    }
    assert "DELETE" in methods
    assert methods == {"GET", "POST", "DELETE", "OPTIONS"}
    assert refused.status_code == 400
