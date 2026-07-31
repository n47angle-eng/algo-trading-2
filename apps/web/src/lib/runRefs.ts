import { fetchRuns } from "../api/client";
import type { RunSummary } from "../api/types";

/**
 * Count standard (non-validation) runs that bind a strategy version.
 * Constraint #19: validation runs do not block delete.
 */
export function countStandardRunsForStrategy(
  runs: RunSummary[],
  strategyId: string,
): number {
  return runs.filter(
    (r) =>
      r.strategy_version === strategyId && r.validation_run === false,
  ).length;
}

export interface StandardRunCountResult {
  counts: Record<string, number>;
  /**
   * false when the runs API failed — delete must fail-closed (D4).
   * "I could not check" is not the same as "zero references".
   */
  known: boolean;
}

export async function loadStandardRunCounts(
  strategyIds: string[],
): Promise<StandardRunCountResult> {
  const counts: Record<string, number> = {};
  for (const id of strategyIds) {
    counts[id] = 0;
  }
  try {
    const list = await fetchRuns();
    for (const id of strategyIds) {
      counts[id] = countStandardRunsForStrategy(list.runs, id);
    }
    return { counts, known: true };
  } catch {
    return { counts, known: false };
  }
}
