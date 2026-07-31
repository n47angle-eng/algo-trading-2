/**
 * Delete UX timing (constraints #19–#21).
 *
 * `DELETE_GRACE_MS` is the single product truth for the undo window; the real
 * archive delete now happens server-side (`DELETE /api/v1/strategies/{id}`,
 * backend `e5dd179`) once that window closes.
 *
 * ⚠️ The tombstone helpers below are **no longer part of the product path**.
 * Local tombstones would hide versions the backend still holds, so LibraryTab
 * stopped reading or writing them. They are retained only because the
 * owner-review storage-isolation probe uses `DELETE_TOMBSTONE_KEY` as a
 * sentinel key and their unit test still pins the storage shape. Do not
 * reintroduce them as delete truth.
 */

export const DELETE_TOMBSTONE_KEY = "futures-research.strategy-tombstones.v1";
export const DELETE_GRACE_MS = 5000;

export interface TombstoneRecord {
  strategyId: string;
  deletedAt: string;
  /** Snapshot for potential restore UI / audit note. */
  name: string;
}

interface TombstoneStore {
  schema: "strategy_tombstones.v1";
  items: TombstoneRecord[];
}

function readStore(storage: Storage = localStorage): TombstoneStore {
  try {
    const raw = storage.getItem(DELETE_TOMBSTONE_KEY);
    if (!raw) {
      return { schema: "strategy_tombstones.v1", items: [] };
    }
    const parsed = JSON.parse(raw) as TombstoneStore;
    if (parsed.schema !== "strategy_tombstones.v1" || !Array.isArray(parsed.items)) {
      return { schema: "strategy_tombstones.v1", items: [] };
    }
    return parsed;
  } catch {
    return { schema: "strategy_tombstones.v1", items: [] };
  }
}

function writeStore(store: TombstoneStore, storage: Storage = localStorage): void {
  storage.setItem(DELETE_TOMBSTONE_KEY, JSON.stringify(store));
}

export function listTombstones(storage: Storage = localStorage): TombstoneRecord[] {
  return readStore(storage).items;
}

export function isTombstoned(
  strategyId: string,
  storage: Storage = localStorage,
): boolean {
  return readStore(storage).items.some((t) => t.strategyId === strategyId);
}

/** Commit delete after grace — irreversible in stage B UI. */
export function commitTombstone(
  strategyId: string,
  name: string,
  storage: Storage = localStorage,
  now: Date = new Date(),
): void {
  const store = readStore(storage);
  if (store.items.some((t) => t.strategyId === strategyId)) {
    return;
  }
  store.items.push({
    strategyId,
    name,
    deletedAt: now.toISOString(),
  });
  writeStore(store, storage);
}

/** Pending deletes live only in React state; leaving page commits them. */
export interface PendingDelete {
  strategyId: string;
  name: string;
  startedAt: number;
  /** Real timer handle — not a fake. */
  timerId: ReturnType<typeof setTimeout>;
}
