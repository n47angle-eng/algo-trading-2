"""Web Push subscription storage and VAPID key exposure.

Production readiness:
- Set env ``FR_VAPID_PUBLIC_KEY`` and ``FR_VAPID_PRIVATE_KEY`` (and optional
  ``FR_VAPID_SUBJECT`` mailto: or https: contact).
- Optional ``pywebpush`` for actual send; without it, subscribe still works
  and ``POST /api/v1/push/notify`` returns 503 with a clear message.

Subscriptions are stored in a local SQLite table under the paper data root so
the personal platform can fan-out trader/backtest alerts to installed PWAs.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from futures_research.paths import PROJECT_ROOT

router = APIRouter(prefix="/api/v1/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeBody(BaseModel):
    endpoint: str = Field(min_length=8, max_length=2048)
    keys: PushKeys
    expirationTime: float | None = None


class PushUnsubscribeBody(BaseModel):
    endpoint: str = Field(min_length=8, max_length=2048)


class PushNotifyBody(BaseModel):
    """Internal/local notify helper — title/body/tag for SW display."""

    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=500)
    tag: str | None = Field(default=None, max_length=120)
    data: dict[str, Any] | None = None


def _db_path() -> Path:
    root = PROJECT_ROOT / "data" / "paper"
    root.mkdir(parents=True, exist_ok=True)
    return root / "push_subscriptions.sqlite3"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            endpoint TEXT PRIMARY KEY,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            expiration_time REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return conn


def _vapid_public() -> str | None:
    key = os.environ.get("FR_VAPID_PUBLIC_KEY", "").strip()
    return key or None


def _vapid_private() -> str | None:
    key = os.environ.get("FR_VAPID_PRIVATE_KEY", "").strip()
    return key or None


def _vapid_subject() -> str:
    return os.environ.get("FR_VAPID_SUBJECT", "mailto:owner@localhost").strip()


@router.get("/vapid-public-key")
def vapid_public_key() -> dict[str, str]:
    public = _vapid_public()
    if not public:
        raise HTTPException(
            status_code=503,
            detail="VAPID public key not configured (set FR_VAPID_PUBLIC_KEY)",
        )
    return {"publicKey": public}


@router.post("/subscribe")
def subscribe(body: PushSubscribeBody) -> dict[str, str]:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO push_subscriptions (endpoint, p256dh, auth, expiration_time)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                p256dh=excluded.p256dh,
                auth=excluded.auth,
                expiration_time=excluded.expiration_time
            """,
            (
                body.endpoint,
                body.keys.p256dh,
                body.keys.auth,
                body.expirationTime,
            ),
        )
        conn.commit()
    return {"status": "ok"}


@router.post("/unsubscribe")
def unsubscribe(body: PushUnsubscribeBody) -> dict[str, str]:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?",
            (body.endpoint,),
        )
        conn.commit()
    return {"status": "ok"}


@router.get("/subscriptions")
def list_subscriptions() -> dict[str, Any]:
    """Local debug: count stored endpoints (no secrets)."""
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM push_subscriptions").fetchone()
    return {"count": int(row["n"]) if row else 0}


def _send_web_push(payload: dict[str, Any]) -> dict[str, Any]:
    public = _vapid_public()
    private = _vapid_private()
    if not public or not private:
        raise HTTPException(
            status_code=503,
            detail="VAPID keys not configured (FR_VAPID_PUBLIC_KEY / FR_VAPID_PRIVATE_KEY)",
        )
    try:
        from pywebpush import WebPushException, webpush  # type: ignore[import-not-found]
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="pywebpush not installed; pip install pywebpush for server push",
        ) from exc

    body = json.dumps(payload, ensure_ascii=False)
    sent = 0
    failed = 0
    removed: list[str] = []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions"
        ).fetchall()
        for row in rows:
            subscription_info = {
                "endpoint": row["endpoint"],
                "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
            }
            try:
                webpush(
                    subscription_info=subscription_info,
                    data=body,
                    vapid_private_key=private,
                    vapid_claims={"sub": _vapid_subject()},
                )
                sent += 1
            except WebPushException as exc:
                failed += 1
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in {404, 410}:
                    conn.execute(
                        "DELETE FROM push_subscriptions WHERE endpoint = ?",
                        (row["endpoint"],),
                    )
                    removed.append(row["endpoint"])
        conn.commit()
    return {"sent": sent, "failed": failed, "removed": len(removed)}


@router.post("/notify")
def notify(body: PushNotifyBody) -> dict[str, Any]:
    """Broadcast a notification payload to all stored subscriptions."""
    payload: dict[str, Any] = {
        "title": body.title,
        "body": body.body,
        "tag": body.tag,
        "data": body.data or {},
        "icon": "/icons/icon-192.png",
        "badge": "/icons/icon-192.png",
    }
    return _send_web_push(payload)
