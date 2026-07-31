/** In-memory Storage for owner-review isolation (never touches localStorage). */

export function createMemoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear() {
      map.clear();
    },
    getItem(key: string) {
      return map.has(key) ? (map.get(key) as string) : null;
    },
    key(index: number) {
      return [...map.keys()][index] ?? null;
    },
    removeItem(key: string) {
      map.delete(key);
    },
    setItem(key: string, value: string) {
      map.set(key, value);
    },
  };
}

/** Snapshot of localStorage keys used by P2 business state. */
export function readP2LocalStorageBytes(
  storage: Storage = localStorage,
): Record<string, string | null> {
  return {
    "futures-research.sketches.v1": storage.getItem(
      "futures-research.sketches.v1",
    ),
    "futures-research.insights.v1": storage.getItem(
      "futures-research.insights.v1",
    ),
  };
}
