"""Push subscription API — VAPID readiness + subscribe store."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from futures_research.api.main import app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Isolate push DB under tmp paper root via PROJECT_ROOT override is hard;
    # instead redirect db path by monkeypatching the module helper.
    db = tmp_path / "push_subscriptions.sqlite3"

    def _db_path() -> Path:
        return db

    monkeypatch.setattr(
        "futures_research.api.routes_push._db_path",
        _db_path,
    )
    return TestClient(app)


def test_vapid_public_key_unavailable_without_env(client: TestClient) -> None:
    # Ensure unset
    os.environ.pop("FR_VAPID_PUBLIC_KEY", None)
    res = client.get("/api/v1/push/vapid-public-key")
    assert res.status_code == 503


def test_vapid_public_key_returns_when_set(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FR_VAPID_PUBLIC_KEY", "BFakePublicKeyForUnitTestOnly==")
    res = client.get("/api/v1/push/vapid-public-key")
    assert res.status_code == 200
    body = res.json()
    assert body["publicKey"] == "BFakePublicKeyForUnitTestOnly=="


def test_subscribe_and_list(client: TestClient) -> None:
    res = client.post(
        "/api/v1/push/subscribe",
        json={
            "endpoint": "https://push.example/endpoint/abc",
            "keys": {"p256dh": "pk", "auth": "ak"},
            "expirationTime": None,
        },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    listed = client.get("/api/v1/push/subscriptions")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    # Upsert same endpoint
    res2 = client.post(
        "/api/v1/push/subscribe",
        json={
            "endpoint": "https://push.example/endpoint/abc",
            "keys": {"p256dh": "pk2", "auth": "ak2"},
        },
    )
    assert res2.status_code == 200
    assert client.get("/api/v1/push/subscriptions").json()["count"] == 1


def test_unsubscribe(client: TestClient) -> None:
    client.post(
        "/api/v1/push/subscribe",
        json={
            "endpoint": "https://push.example/endpoint/xyz",
            "keys": {"p256dh": "pk", "auth": "ak"},
        },
    )
    res = client.post(
        "/api/v1/push/unsubscribe",
        json={"endpoint": "https://push.example/endpoint/xyz"},
    )
    assert res.status_code == 200
    assert client.get("/api/v1/push/subscriptions").json()["count"] == 0


def test_notify_without_vapid_is_503(client: TestClient) -> None:
    os.environ.pop("FR_VAPID_PUBLIC_KEY", None)
    os.environ.pop("FR_VAPID_PRIVATE_KEY", None)
    res = client.post(
        "/api/v1/push/notify",
        json={"title": "t", "body": "b"},
    )
    assert res.status_code == 503
