"""End-to-end smoke: real ASGI app exercises the closed-loop read surfaces."""

from __future__ import annotations

from fastapi.testclient import TestClient

from futures_research.api.main import app


def test_e2e_core_read_surface_does_not_500() -> None:
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    # System + data catalog (fast path)
    compute = client.get("/api/v1/system/compute-status")
    assert compute.status_code == 200
    compute_body = compute.json()
    assert compute_body["schema"] == "compute_status.v1"
    assert compute_body["writes_authority"] is False
    assert compute_body["production_default_backend"] == "auto"
    assert compute_body["authority_owners"]["paper_ledger"] == "python"
    assert compute_body["authority_owners"]["ib_transport"] == "python"

    ib = client.get("/api/v1/system/ib-status")
    assert ib.status_code in (200, 503)

    catalog = client.get("/api/v1/data/coverage?view=catalog")
    assert catalog.status_code in (200, 422, 503)

    # Paper surfaces — pure GETs must not crash or invent default paper root
    for path in (
        "/api/v1/paper/runtime-capabilities",
        "/api/v1/paper/runtime/gateway-status",
        "/api/v1/paper/fleet-overview",
        "/api/v1/paper/traders",
        "/api/v1/promotion-decisions/eligible-strategies",
        "/api/v1/strategies",
        "/api/v1/runs",
        "/api/v1/batches/jobs",
    ):
        response = client.get(path)
        assert response.status_code < 500, f"{path} -> {response.status_code}"


def test_e2e_gateway_status_zero_order_paths() -> None:
    client = TestClient(app)
    body = client.get("/api/v1/paper/runtime/gateway-status").json()
    assert body["schema"] == "paper_gateway_status.v1"
    assert body["order_paths"] == 0


def test_e2e_fleet_overview_schema() -> None:
    client = TestClient(app)
    body = client.get("/api/v1/paper/fleet-overview").json()
    assert body["schema"] == "paper_fleet_overview.v1"
    assert "traders" in body
    assert "gateway" in body
    assert "closed_loop" in body
