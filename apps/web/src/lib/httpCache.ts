/**
 * Short-lived in-memory GET cache for hot read paths.
 * Avoids hammering the backend on tab switches / multi-component probes.
 * Never caches non-GET or mutating calls.
 */

interface CacheEntry {
  expiresAt: number;
  status: number;
  body: unknown;
  rawText: string;
}

const store = new Map<string, CacheEntry>();

/** Default TTL for catalog / status probes (ms). */
export const DEFAULT_GET_TTL_MS = 8_000;

export function cacheKey(method: string, path: string): string {
  return `${method.toUpperCase()} ${path}`;
}

export function getCachedGet(path: string): CacheEntry | null {
  const key = cacheKey("GET", path);
  const hit = store.get(key);
  if (!hit) {
    return null;
  }
  if (Date.now() > hit.expiresAt) {
    store.delete(key);
    return null;
  }
  return hit;
}

export function setCachedGet(
  path: string,
  value: { status: number; body: unknown; rawText: string },
  ttlMs: number = DEFAULT_GET_TTL_MS,
): void {
  if (value.status < 200 || value.status >= 300) {
    return;
  }
  store.set(cacheKey("GET", path), {
    expiresAt: Date.now() + ttlMs,
    status: value.status,
    body: value.body,
    rawText: value.rawText,
  });
}

export function invalidateGetCache(pathPrefix?: string): void {
  if (!pathPrefix) {
    store.clear();
    return;
  }
  for (const key of store.keys()) {
    if (key.includes(pathPrefix)) {
      store.delete(key);
    }
  }
}

/** Test helper */
export function __resetHttpCacheForTests(): void {
  store.clear();
}
