/**
 * P6 Stage B pure URL and request-state helpers.
 *
 * Authority: approved bridge design §15.1 (URL reload recovery) and §15.2
 * (explicit new export), plus work order
 * docs/work-orders/AGENT_Y_V55_P6_STAGE_B_LEDGER_CARD_REQUEST_RECOVERY.md §7.
 *
 * Pure functions only: no fetch, no storage, no DOM, no clock. The URL is the
 * single recovery authority — there is deliberately no localStorage or
 * sessionStorage copy to disagree with it.
 */

import { isCanonicalUuid4 } from "./normalContract";
import { isSafePaperTraderId } from "./reviewContract";

export const PAPER_TRADER_QUERY_KEY = "paper_trader";
export const PAPER_REVIEW_REQUEST_QUERY_KEY = "review_request";

export interface PaperReviewUrlIdentity {
  readonly trader_id: string;
  readonly request_id: string;
}

export { isSafePaperTraderId };

/** §9.1: the request identity is always a canonical lowercase UUID4. */
export function isPaperReviewRequestId(value: unknown): value is string {
  return isCanonicalUuid4(value);
}

function readSingle(
  params: URLSearchParams,
  key: string,
): string | null | undefined {
  const all = params.getAll(key);
  if (all.length === 0) {
    return undefined;
  }
  if (all.length > 1) {
    // Two authorities for one identity is a fail-closed condition.
    return null;
  }
  return all[0];
}

/**
 * §15.1: both halves must be present exactly once and strictly valid.
 * A missing half, a duplicated key or an invalid identity yields `null` —
 * never a partially trusted identity and never a repaired one.
 */
export function readPaperReviewUrlIdentity(
  search: URLSearchParams | string,
): PaperReviewUrlIdentity | null {
  const params =
    typeof search === "string" ? new URLSearchParams(search) : search;
  const traderId = readSingle(params, PAPER_TRADER_QUERY_KEY);
  const requestId = readSingle(params, PAPER_REVIEW_REQUEST_QUERY_KEY);
  if (traderId === undefined && requestId === undefined) {
    return null;
  }
  if (
    traderId === null ||
    requestId === null ||
    traderId === undefined ||
    requestId === undefined
  ) {
    return null;
  }
  if (!isSafePaperTraderId(traderId) || !isPaperReviewRequestId(requestId)) {
    return null;
  }
  return { trader_id: traderId, request_id: requestId };
}

/** True when exactly one half is present: an explicit fail-closed condition. */
export function hasPartialPaperReviewUrlIdentity(
  search: URLSearchParams | string,
): boolean {
  const params =
    typeof search === "string" ? new URLSearchParams(search) : search;
  const hasTrader = params.getAll(PAPER_TRADER_QUERY_KEY).length > 0;
  const hasRequest = params.getAll(PAPER_REVIEW_REQUEST_QUERY_KEY).length > 0;
  return hasTrader !== hasRequest;
}

/**
 * §7.1: write both halves in one navigation operation, keeping every other
 * query key. Each identity key ends up with exactly one authority.
 */
export function writePaperReviewUrlIdentity(
  search: URLSearchParams | string,
  identity: PaperReviewUrlIdentity,
): URLSearchParams | null {
  if (
    !isSafePaperTraderId(identity.trader_id) ||
    !isPaperReviewRequestId(identity.request_id)
  ) {
    return null;
  }
  const params = new URLSearchParams(
    typeof search === "string" ? search : search.toString(),
  );
  params.delete(PAPER_TRADER_QUERY_KEY);
  params.delete(PAPER_REVIEW_REQUEST_QUERY_KEY);
  params.set(PAPER_TRADER_QUERY_KEY, identity.trader_id);
  params.set(PAPER_REVIEW_REQUEST_QUERY_KEY, identity.request_id);
  return params;
}

/** True when the URL already carries this exact identity, both halves. */
export function paperReviewUrlIdentityMatches(
  search: URLSearchParams | string,
  identity: PaperReviewUrlIdentity,
): boolean {
  const current = readPaperReviewUrlIdentity(search);
  return (
    current !== null &&
    current.trader_id === identity.trader_id &&
    current.request_id === identity.request_id
  );
}

/** Phases during which a request has no known terminal outcome (§7.2). */
export const PAPER_REVIEW_LOCKED_PHASES = [
  "submitting",
  "preparing",
  "recovering",
] as const;

export type PaperReviewPhase =
  | "idle"
  | "submitting"
  | "preparing"
  | "recovering"
  | "ready"
  | "failed"
  | "unknown";

export function isPaperReviewLocked(phase: PaperReviewPhase): boolean {
  return (PAPER_REVIEW_LOCKED_PHASES as readonly string[]).includes(phase);
}
