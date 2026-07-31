/**
 * Notification preference load / save / gate.
 * Pure storage helpers + preference gating — no Notification API.
 */

import {
  DEFAULT_NOTIFICATION_PREFERENCES,
  NOTIFICATION_PREFS_STORAGE_KEY,
  NOTIFICATION_TYPES,
  type NotificationPreferences,
  type NotificationType,
} from "./types";

export function defaultNotificationPreferences(): NotificationPreferences {
  return {
    enabled: DEFAULT_NOTIFICATION_PREFERENCES.enabled,
    types: { ...DEFAULT_NOTIFICATION_PREFERENCES.types },
  };
}

export function isNotificationType(value: unknown): value is NotificationType {
  return (
    typeof value === "string" &&
    (NOTIFICATION_TYPES as readonly string[]).includes(value)
  );
}

/**
 * Parse unknown JSON into a complete preferences object.
 * Invalid / partial input falls back to defaults per-field.
 */
export function parseNotificationPreferences(
  raw: unknown,
): NotificationPreferences {
  const base = defaultNotificationPreferences();
  if (!raw || typeof raw !== "object") {
    return base;
  }
  const obj = raw as Record<string, unknown>;
  if (typeof obj.enabled === "boolean") {
    base.enabled = obj.enabled;
  }
  if (obj.types && typeof obj.types === "object") {
    const types = obj.types as Record<string, unknown>;
    for (const key of NOTIFICATION_TYPES) {
      if (typeof types[key] === "boolean") {
        base.types[key] = types[key];
      }
    }
  }
  return base;
}

export function loadNotificationPreferences(
  storage: Pick<Storage, "getItem"> | null | undefined = typeof window !==
  "undefined"
    ? window.localStorage
    : null,
): NotificationPreferences {
  if (!storage) {
    return defaultNotificationPreferences();
  }
  try {
    const raw = storage.getItem(NOTIFICATION_PREFS_STORAGE_KEY);
    if (!raw) {
      return defaultNotificationPreferences();
    }
    return parseNotificationPreferences(JSON.parse(raw) as unknown);
  } catch {
    return defaultNotificationPreferences();
  }
}

export function saveNotificationPreferences(
  prefs: NotificationPreferences,
  storage: Pick<Storage, "setItem"> | null | undefined = typeof window !==
  "undefined"
    ? window.localStorage
    : null,
): void {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(NOTIFICATION_PREFS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    /* private mode / quota */
  }
}

export function setNotificationTypeEnabled(
  prefs: NotificationPreferences,
  type: NotificationType,
  enabled: boolean,
): NotificationPreferences {
  return {
    ...prefs,
    types: {
      ...prefs.types,
      [type]: enabled,
    },
  };
}

export function setMasterNotificationEnabled(
  prefs: NotificationPreferences,
  enabled: boolean,
): NotificationPreferences {
  return { ...prefs, enabled };
}

/**
 * Gate: may this notification type fire under current preferences?
 * Master off OR type off → false.
 */
export function isNotificationAllowed(
  prefs: NotificationPreferences,
  type: NotificationType,
): boolean {
  if (!prefs.enabled) {
    return false;
  }
  return prefs.types[type] === true;
}
