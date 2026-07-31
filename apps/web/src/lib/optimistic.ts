/**
 * Optimistic / stale-while-revalidate helpers — keep previous UI on screen
 * while network work runs; never blank the page on soft refresh.
 */

/** True when we already have something to show (skip full-page loading flash). */
export function hasPriorData<T>(value: T | null | undefined): value is T {
  if (value === null || value === undefined) {
    return false;
  }
  if (Array.isArray(value)) {
    return true; // empty array still counts as "loaded once"
  }
  return true;
}

/**
 * Apply a local patch immediately; return a restore function for rollback.
 */
export function optimisticPatch<T>(
  current: T,
  patch: (prev: T) => T,
  apply: (next: T) => void,
): { next: T; rollback: () => void } {
  const snapshot = current;
  const next = patch(current);
  apply(next);
  return {
    next,
    rollback: () => {
      apply(snapshot);
    },
  };
}

/** Map paper runtime command → optimistic lifecycle label. */
export function optimisticLifecycleAfter(
  command: "start" | "pause" | "resume" | "permanent-stop",
  current: string,
): string {
  switch (command) {
    case "start":
    case "resume":
      return "running";
    case "pause":
      return current === "running" ? "pausing" : "paused";
    case "permanent-stop":
      return current === "running" || current === "pausing"
        ? "stopping"
        : "permanently_stopped";
    default:
      return current;
  }
}
