/**
 * Web Push client helpers.
 *
 * Production path:
 * 1. GET /api/v1/push/vapid-public-key
 * 2. pushManager.subscribe({ userVisibleOnly, applicationServerKey })
 * 3. POST /api/v1/push/subscribe with the PushSubscription JSON
 *
 * Server stores subscriptions and can POST web-push payloads when
 * domain events fire. Local/dev may return 503 when VAPID is unset —
 * client falls back to in-app SW notifications while the page/SW is alive.
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export interface PushSubscribeResult {
  ok: boolean;
  reason:
    | "subscribed"
    | "already"
    | "no_sw"
    | "no_push_manager"
    | "permission_denied"
    | "vapid_unavailable"
    | "subscribe_failed"
    | "server_rejected";
  endpoint?: string;
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) {
    output[i] = raw.charCodeAt(i);
  }
  return output;
}

export async function fetchVapidPublicKey(): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/push/vapid-public-key`);
    if (!res.ok) {
      return null;
    }
    const body = (await res.json()) as { publicKey?: string };
    return typeof body.publicKey === "string" && body.publicKey.length > 0
      ? body.publicKey
      : null;
  } catch {
    return null;
  }
}

export async function subscribeWebPush(): Promise<PushSubscribeResult> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) {
    return { ok: false, reason: "no_sw" };
  }
  if (!("PushManager" in window)) {
    return { ok: false, reason: "no_push_manager" };
  }
  if (typeof Notification !== "undefined" && Notification.permission !== "granted") {
    return { ok: false, reason: "permission_denied" };
  }

  const publicKey = await fetchVapidPublicKey();
  if (!publicKey) {
    return { ok: false, reason: "vapid_unavailable" };
  }

  try {
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
      });
    }
    const json = sub.toJSON();
    const res = await fetch(`${API_BASE}/api/v1/push/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        endpoint: json.endpoint,
        keys: json.keys,
        expirationTime: json.expirationTime ?? null,
      }),
    });
    if (!res.ok) {
      return { ok: false, reason: "server_rejected", endpoint: json.endpoint };
    }
    return { ok: true, reason: "subscribed", endpoint: json.endpoint };
  } catch {
    return { ok: false, reason: "subscribe_failed" };
  }
}

export async function unsubscribeWebPush(): Promise<boolean> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) {
    return false;
  }
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (!sub) {
      return true;
    }
    const endpoint = sub.endpoint;
    await sub.unsubscribe();
    try {
      await fetch(`${API_BASE}/api/v1/push/unsubscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint }),
      });
    } catch {
      /* local unsubscribe is enough */
    }
    return true;
  } catch {
    return false;
  }
}
