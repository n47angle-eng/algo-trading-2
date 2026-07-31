/**
 * Real-time optimistic webapp updates.
 *
 * Two channels (either one is enough):
 * 1) Service Worker updatefound → skipWaiting → controllerchange → reload
 * 2) Poll /build-meta.json (no-cache) for a new buildId → hard reload
 *
 * Default is auto-apply (optimistic): no "稍後" gate for code deploys.
 */

export const BUILD_META_PATH = "/build-meta.json";
/** How often to ask the SW / meta endpoint for a newer build. */
export const UPDATE_CHECK_MS = 15_000;
/** Brief delay so the banner paints before reload (optimistic UX). */
export const AUTO_RELOAD_PAINT_MS = 280;

export type BuildMeta = {
  buildId: string;
  builtAt?: string;
};

export function currentAppBuildId(): string {
  try {
    // Injected at build time by vite.config.ts
    if (typeof __APP_BUILD_ID__ === "string" && __APP_BUILD_ID__.length > 0) {
      return __APP_BUILD_ID__;
    }
  } catch {
    /* ignore */
  }
  return "dev";
}

export function parseBuildMeta(raw: unknown): BuildMeta | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const buildId = (raw as { buildId?: unknown }).buildId;
  if (typeof buildId !== "string" || buildId.trim().length === 0) {
    return null;
  }
  const builtAt = (raw as { builtAt?: unknown }).builtAt;
  return {
    buildId: buildId.trim(),
    builtAt: typeof builtAt === "string" ? builtAt : undefined,
  };
}

export function shouldReloadForBuild(
  currentBuildId: string,
  remoteBuildId: string,
): boolean {
  if (!remoteBuildId || !currentBuildId) {
    return false;
  }
  // Never thrash on dev placeholders
  if (currentBuildId === "dev" || remoteBuildId === "dev") {
    return false;
  }
  return currentBuildId !== remoteBuildId;
}

export async function fetchRemoteBuildMeta(
  fetchImpl: typeof fetch = fetch,
): Promise<BuildMeta | null> {
  const url = `${BUILD_META_PATH}?t=${Date.now()}`;
  const res = await fetchImpl(url, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    return null;
  }
  try {
    return parseBuildMeta(await res.json());
  } catch {
    return null;
  }
}

const RELOAD_GUARD_KEY = "fr-auto-reload-build";

/**
 * Avoid thrashing if a deploy is half-applied or meta fetch races.
 * Returns false when we already reloaded for this buildId in this tab session.
 */
export function claimReloadForBuild(buildId: string): boolean {
  if (!buildId || buildId === "dev") {
    return false;
  }
  try {
    if (sessionStorage.getItem(RELOAD_GUARD_KEY) === buildId) {
      return false;
    }
    sessionStorage.setItem(RELOAD_GUARD_KEY, buildId);
  } catch {
    /* private mode — still allow one reload */
  }
  return true;
}

/**
 * Hard navigation reload. Uses replace when possible so back-stack stays clean.
 */
export function hardReload(locationLike: Location = window.location): void {
  try {
    locationLike.reload();
  } catch {
    locationLike.href = locationLike.href;
  }
}
