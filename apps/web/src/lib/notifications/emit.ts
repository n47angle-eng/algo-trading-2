/**
 * Preference-gated notification emission.
 * Separates pure decision (shouldNotify) from browser I/O (showNotification).
 */

import { isNotificationAllowed, loadNotificationPreferences } from "./preferences";
import { buildNotificationPayload } from "./payloads";
import type {
  DomainNotificationEvent,
  NotificationPayload,
  NotificationPreferences,
} from "./types";

export interface NotifyDecision {
  allowed: boolean;
  payload: NotificationPayload | null;
  reason:
    | "ok"
    | "prefs_disabled"
    | "type_disabled"
    | "permission_denied"
    | "unsupported";
}

/**
 * Decide whether a domain event should produce a notification under prefs.
 * Does not touch the Notification API.
 */
export function decideNotification(
  event: DomainNotificationEvent,
  prefs: NotificationPreferences,
): NotifyDecision {
  const payload = buildNotificationPayload(event);
  if (!prefs.enabled) {
    return { allowed: false, payload, reason: "prefs_disabled" };
  }
  if (!isNotificationAllowed(prefs, payload.type)) {
    return { allowed: false, payload, reason: "type_disabled" };
  }
  return { allowed: true, payload, reason: "ok" };
}

export type NotificationPermissionState =
  | "granted"
  | "denied"
  | "default"
  | "unsupported";

export function getNotificationPermission(): NotificationPermissionState {
  if (typeof window === "undefined" || typeof Notification === "undefined") {
    return "unsupported";
  }
  return Notification.permission as NotificationPermissionState;
}

export async function requestNotificationPermission(): Promise<NotificationPermissionState> {
  if (typeof window === "undefined" || typeof Notification === "undefined") {
    return "unsupported";
  }
  if (Notification.permission === "granted") {
    return "granted";
  }
  if (Notification.permission === "denied") {
    return "denied";
  }
  try {
    const result = await Notification.requestPermission();
    return result as NotificationPermissionState;
  } catch {
    return "unsupported";
  }
}

export interface ShowNotificationOptions {
  /** Prefer service worker when available (installed PWA path). */
  preferServiceWorker?: boolean;
}

/**
 * Show a notification via SW registration or the page Notification constructor.
 * Call only after decideNotification.allowed === true and permission granted.
 */
export async function showNotificationPayload(
  payload: NotificationPayload,
  options: ShowNotificationOptions = {},
): Promise<"sw" | "page" | "skipped"> {
  if (typeof window === "undefined" || typeof Notification === "undefined") {
    return "skipped";
  }
  if (Notification.permission !== "granted") {
    return "skipped";
  }

  const opts: NotificationOptions & { renotify?: boolean } = {
    body: payload.body,
    tag: payload.tag,
    data: payload.data,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    // Renotify so repeated tags still surface on some platforms.
    renotify: true,
  };

  if (options.preferServiceWorker !== false && "serviceWorker" in navigator) {
    try {
      const reg = await navigator.serviceWorker.ready;
      await reg.showNotification(payload.title, opts);
      return "sw";
    } catch {
      /* fall through to page Notification */
    }
  }

  try {
    // Page-level fallback when SW not ready (dev / first paint).
    new Notification(payload.title, opts);
    return "page";
  } catch {
    return "skipped";
  }
}

/**
 * Full pipeline: load prefs → decide → show (if allowed + permission).
 * Safe to call from domain observers; never throws.
 */
export async function emitDomainNotification(
  event: DomainNotificationEvent,
  prefsOverride?: NotificationPreferences,
): Promise<NotifyDecision & { shown: "sw" | "page" | "skipped" | "blocked" }> {
  const prefs = prefsOverride ?? loadNotificationPreferences();
  const decision = decideNotification(event, prefs);
  if (!decision.allowed || !decision.payload) {
    return { ...decision, shown: "blocked" };
  }
  const permission = getNotificationPermission();
  if (permission === "unsupported") {
    return { ...decision, allowed: false, reason: "unsupported", shown: "blocked" };
  }
  if (permission !== "granted") {
    return {
      ...decision,
      allowed: false,
      reason: "permission_denied",
      shown: "blocked",
    };
  }
  const shown = await showNotificationPayload(decision.payload);
  return { ...decision, shown };
}
