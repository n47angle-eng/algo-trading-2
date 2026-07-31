/** Strict P4 normal-history snapshot helpers. */

import {
  parseP4StandardRequest,
  requestToAssumptionSnapshot,
} from "./liveContract";
import type { AssumptionSnapshot } from "./types";

export interface CompleteRerunSnapshot {
  strategyVersions: string[];
  symbols: string[];
  rangeStartUtc: string;
  rangeEndUtc: string;
  assumptions: AssumptionSnapshot;
}

export const INCOMPLETE_RERUN_HINT =
  "當時快照唔完整，暫時唔可以精確重跑";

/** Standard history means the whole D1 request parses exactly. */
export function isStandardHistoryRequest(
  request: unknown,
): boolean {
  return parseP4StandardRequest(request) !== null;
}

/** Never fills a missing field from current defaults, jobs, or catalog. */
export function parseCompleteRerunSnapshot(
  request: unknown,
): CompleteRerunSnapshot | null {
  const parsed = parseP4StandardRequest(request);
  if (!parsed) {
    return null;
  }
  return {
    strategyVersions: [...parsed.strategy_versions],
    symbols: [...parsed.symbols],
    rangeStartUtc: parsed.range_start,
    rangeEndUtc: parsed.range_end,
    assumptions: requestToAssumptionSnapshot(parsed),
  };
}

export function isLiveExactRerunAvailable(
  request: unknown,
): boolean {
  return parseP4StandardRequest(request) !== null;
}

export function liveRerunDisabledReason(): string {
  return INCOMPLETE_RERUN_HINT;
}
