/**
 * P2 tab ③ version-lifecycle wire contract.
 *
 * Backend truth for the delete guard, numeric derive and final archive delete
 * (`e5dd179`).  Everything here is runtime checked: a TypeScript cast must
 * never be the reason the Owner can delete a version or believe a new version
 * exists.  "I could not check" is never rendered as "zero references".
 */

import type { StrategyVersion } from "../../api/client";

const RUN_REFERENCE_LIST_KEYS = [
  "schema",
  "mode",
  "run_scope",
  "count_known",
  "count",
  "known_match_count",
  "runs",
  "unindexed_candidates",
] as const;
const RUN_REFERENCE_ROW_KEYS = [
  "run_id",
  "strategy_version",
  "contract_id",
  "symbol",
  "session_name",
  "range_start",
  "range_end",
] as const;
const UNINDEXED_CANDIDATE_KEYS = ["run_id", "reason"] as const;
const DERIVE_KEYS = [
  "schema",
  "parent_strategy_id",
  "changed_count",
  "changed_paths",
  "deduplicated",
  "version",
] as const;
const DELETE_KEYS = [
  "schema",
  "strategy_id",
  "deleted_at",
  "archived_status",
  "archived_to",
] as const;
const DELETE_BLOCKED_KEYS = [
  "schema",
  "message",
  "standard_run_count",
  "run_ids",
] as const;

const STRATEGY_ID = /^strategy-[0-9]{4,}$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const CANONICAL_UTC =
  /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$/;
const ARCHIVE_PREFIX = "data/strategies/_deleted/";

export interface RunReferenceRow {
  run_id: string;
  strategy_version: string;
  contract_id: string;
  symbol: string;
  session_name: string;
  range_start: string;
  range_end: string;
}

export interface UnindexedRunCandidate {
  run_id: string;
  reason: string;
}

export interface RunReferenceList {
  schema: "run_reference_list.v1";
  mode: "strategy";
  run_scope: "standard";
  count_known: boolean;
  count: number | null;
  known_match_count: number;
  runs: RunReferenceRow[];
  unindexed_candidates: UnindexedRunCandidate[];
}

export interface StrategyDeriveResponse {
  schema: "strategy_derive.v1";
  parent_strategy_id: string;
  changed_count: number;
  changed_paths: string[];
  deduplicated: boolean;
  version: StrategyVersion;
}

export interface StrategyDeleteResponse {
  schema: "strategy_delete.v1";
  strategy_id: string;
  deleted_at: string;
  archived_status: "draft" | "confirmed";
  archived_to: string;
}

export interface StrategyDeleteBlocked {
  schema: "strategy_delete_blocked.v1";
  message: string;
  standard_run_count: number;
  run_ids: string[];
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value);
  return (
    actual.length === expected.length &&
    expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))
  );
}

/** Non-empty and never surrounded by whitespace — identities are exact. */
function isExactText(value: unknown): value is string {
  return typeof value === "string" && value !== "" && value === value.trim();
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function parseRunReferenceRow(
  value: unknown,
  expectedStrategyVersion: string,
): RunReferenceRow | null {
  if (!isObject(value) || !hasExactKeys(value, RUN_REFERENCE_ROW_KEYS)) {
    return null;
  }
  for (const key of RUN_REFERENCE_ROW_KEYS) {
    if (!isExactText(value[key])) {
      return null;
    }
  }
  // A row for another strategy would silently inflate this version's guard.
  if (value.strategy_version !== expectedStrategyVersion) {
    return null;
  }
  return value as unknown as RunReferenceRow;
}

function parseUnindexedCandidate(value: unknown): UnindexedRunCandidate | null {
  if (!isObject(value) || !hasExactKeys(value, UNINDEXED_CANDIDATE_KEYS)) {
    return null;
  }
  if (!isExactText(value.run_id) || !isExactText(value.reason)) {
    return null;
  }
  return value as unknown as UnindexedRunCandidate;
}

/**
 * Exact `run_reference_list.v1` for one `mode=strategy` query.
 * Returns null on any drift; callers must then fail closed.
 */
export function parseRunReferenceList(
  body: unknown,
  expectedStrategyVersion: string,
): RunReferenceList | null {
  if (!isObject(body) || !hasExactKeys(body, RUN_REFERENCE_LIST_KEYS)) {
    return null;
  }
  if (body.schema !== "run_reference_list.v1") {
    return null;
  }
  // Only the strategy mode answers "is this version still in use".
  if (body.mode !== "strategy") {
    return null;
  }
  if (body.run_scope !== "standard") {
    return null;
  }
  if (typeof body.count_known !== "boolean") {
    return null;
  }
  if (!Array.isArray(body.runs) || !Array.isArray(body.unindexed_candidates)) {
    return null;
  }

  const runs: RunReferenceRow[] = [];
  const seenRunIds = new Set<string>();
  for (const row of body.runs) {
    const parsed = parseRunReferenceRow(row, expectedStrategyVersion);
    if (!parsed || seenRunIds.has(parsed.run_id)) {
      return null;
    }
    seenRunIds.add(parsed.run_id);
    runs.push(parsed);
  }

  const candidates: UnindexedRunCandidate[] = [];
  for (const row of body.unindexed_candidates) {
    const parsed = parseUnindexedCandidate(row);
    if (!parsed) {
      return null;
    }
    candidates.push(parsed);
  }

  if (!isCount(body.known_match_count) || body.known_match_count !== runs.length) {
    return null;
  }
  // The backend derives count_known from the candidate list; a payload that
  // claims certainty while carrying candidates is drift, not a known zero.
  if (body.count_known !== (candidates.length === 0)) {
    return null;
  }
  if (body.count_known) {
    if (!isCount(body.count) || body.count !== runs.length) {
      return null;
    }
  } else if (body.count !== null) {
    return null;
  }

  return body as unknown as RunReferenceList;
}

export type StrategyDeleteGuard =
  | { kind: "allowed" }
  | { kind: "in-use"; count: number; runIds: string[] }
  | { kind: "unknown" };

/**
 * Delete is only offered on a proven exact zero.  Loading, transport failure,
 * malformed payload and unindexed candidates all collapse to "unknown".
 */
export function strategyDeleteGuard(
  list: RunReferenceList | null,
): StrategyDeleteGuard {
  if (!list || !list.count_known || list.count === null) {
    return { kind: "unknown" };
  }
  if (list.count > 0) {
    return {
      kind: "in-use",
      count: list.count,
      runIds: list.runs.map((row) => row.run_id),
    };
  }
  return { kind: "allowed" };
}

function parseDerivedVersion(
  value: unknown,
  parentStrategyId: string,
): StrategyVersion | null {
  if (!isObject(value)) {
    return null;
  }
  if (value.schema !== "strategy_version.v1") {
    return null;
  }
  if (
    typeof value.strategy_id !== "string" ||
    !STRATEGY_ID.test(value.strategy_id)
  ) {
    return null;
  }
  // A "child" that is the parent itself would let the UI claim a version that
  // was never created.
  if (value.strategy_id === parentStrategyId) {
    return null;
  }
  if (value.status !== "draft" && value.status !== "confirmed") {
    return null;
  }
  // meta.based_on is server-owned and must be the direct parent (spec §4.5).
  if (value.based_on !== parentStrategyId) {
    return null;
  }
  if (
    typeof value.content_sha256 !== "string" ||
    !SHA256_HEX.test(value.content_sha256)
  ) {
    return null;
  }
  if (typeof value.source_text !== "string" || value.source_text === "") {
    return null;
  }
  if (!Array.isArray(value.parameters)) {
    return null;
  }
  return value as unknown as StrategyVersion;
}

/**
 * Exact `strategy_derive.v1`.  `requestedPaths` are the paths this client
 * actually sent, so a response that changed something else fails closed.
 */
export function parseStrategyDerive(
  body: unknown,
  parentStrategyId: string,
  requestedPaths: readonly string[],
): StrategyDeriveResponse | null {
  if (!isObject(body) || !hasExactKeys(body, DERIVE_KEYS)) {
    return null;
  }
  if (body.schema !== "strategy_derive.v1") {
    return null;
  }
  if (body.parent_strategy_id !== parentStrategyId) {
    return null;
  }
  if (typeof body.deduplicated !== "boolean") {
    return null;
  }
  if (!Array.isArray(body.changed_paths)) {
    return null;
  }
  const paths: string[] = [];
  for (const path of body.changed_paths) {
    if (!isExactText(path) || paths.includes(path)) {
      return null;
    }
    paths.push(path);
  }
  // Canonical sorted order (spec §3.1) — accepting any order would hide a
  // backend that lost the canonicalisation.
  const sorted = [...paths].sort();
  if (paths.some((path, index) => path !== sorted[index])) {
    return null;
  }
  if (!isCount(body.changed_count) || body.changed_count !== paths.length) {
    return null;
  }
  if (paths.length !== requestedPaths.length) {
    return null;
  }
  const requested = new Set(requestedPaths);
  if (paths.some((path) => !requested.has(path))) {
    return null;
  }
  const version = parseDerivedVersion(body.version, parentStrategyId);
  if (!version) {
    return null;
  }
  return body as unknown as StrategyDeriveResponse;
}

/** repo-relative POSIX archive path only — never a local absolute path. */
function isArchivePath(value: unknown): value is string {
  if (!isExactText(value)) {
    return false;
  }
  if (value.includes("\\") || value.startsWith("/") || /^[A-Za-z]:/.test(value)) {
    return false;
  }
  if (value.split("/").includes("..")) {
    return false;
  }
  return value.startsWith(ARCHIVE_PREFIX) && value.endsWith(".yaml");
}

/** Exact `strategy_delete.v1` for the id whose grace period just finished. */
export function parseStrategyDelete(
  body: unknown,
  expectedStrategyId: string,
): StrategyDeleteResponse | null {
  if (!isObject(body) || !hasExactKeys(body, DELETE_KEYS)) {
    return null;
  }
  if (body.schema !== "strategy_delete.v1") {
    return null;
  }
  if (body.strategy_id !== expectedStrategyId) {
    return null;
  }
  if (
    typeof body.deleted_at !== "string" ||
    !CANONICAL_UTC.test(body.deleted_at)
  ) {
    return null;
  }
  if (body.archived_status !== "draft" && body.archived_status !== "confirmed") {
    return null;
  }
  if (!isArchivePath(body.archived_to)) {
    return null;
  }
  return body as unknown as StrategyDeleteResponse;
}

function parseDeleteBlocked(value: unknown): StrategyDeleteBlocked | null {
  if (!isObject(value) || !hasExactKeys(value, DELETE_BLOCKED_KEYS)) {
    return null;
  }
  if (value.schema !== "strategy_delete_blocked.v1") {
    return null;
  }
  if (!isExactText(value.message)) {
    return null;
  }
  if (!isCount(value.standard_run_count) || value.standard_run_count === 0) {
    return null;
  }
  if (!Array.isArray(value.run_ids)) {
    return null;
  }
  const runIds: string[] = [];
  for (const runId of value.run_ids) {
    if (!isExactText(runId) || runIds.includes(runId)) {
      return null;
    }
    runIds.push(runId);
  }
  if (runIds.length !== value.standard_run_count) {
    return null;
  }
  return value as unknown as StrategyDeleteBlocked;
}

export interface ProblemDetail {
  /** Exact backend text — what a copy button must place on the clipboard. */
  text: string;
  /** Present only for the structured standard-run block (delete 409). */
  blocked: StrategyDeleteBlocked | null;
}

/**
 * Turn a FastAPI error body into exact plain text plus, when present, the
 * structured delete block.  No HTML, no UI prefix, no summary substitution.
 */
export function readProblemDetail(body: unknown, fallback: string): ProblemDetail {
  if (!isObject(body) || !Object.prototype.hasOwnProperty.call(body, "detail")) {
    return { text: fallback, blocked: null };
  }
  const detail = body.detail;
  if (typeof detail === "string" && detail !== "") {
    return { text: detail, blocked: null };
  }
  if (isObject(detail)) {
    const blocked = parseDeleteBlocked(detail);
    if (blocked) {
      return {
        text: [
          blocked.message,
          `standard run 數目：${blocked.standard_run_count}`,
          ...blocked.run_ids,
        ].join("\n"),
        blocked,
      };
    }
    // Four-layer validation failures carry the verbatim paste-back report.
    if (
      detail.schema === "strategy_validation.v1" &&
      typeof detail.report_text === "string" &&
      detail.report_text !== ""
    ) {
      return { text: detail.report_text, blocked: null };
    }
    return { text: JSON.stringify(detail, null, 2), blocked: null };
  }
  return { text: fallback, blocked: null };
}
