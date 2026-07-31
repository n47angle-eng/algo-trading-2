/**
 * P6 Stage B strict wire contracts.
 *
 * Authority:
 * - docs/superpowers/specs/2026-07-29-p6-stage-b-ledger-review-bridge-design.md
 *   §3.1 shared scalar boundary, §8 ledger origin, §9 review lifecycle,
 *   §10 error contract, §11 provisioned profile, §12 closest three;
 * - docs/work-orders/AGENT_Y_V55_P6_STAGE_B_WIRE_CONTRACTS.md.
 *
 * These are pure functions over already-decoded JSON bodies: no fetch, no URL,
 * no storage, no DOM and no clock. HTTP status handling belongs to the later
 * consumer batch. Stage A contracts in `normalContract.ts` are untouched; the
 * one thing shared with them is the proven canonical UTC predicate, so Stage B
 * cannot drift into a second, looser timestamp parser.
 */

import { isCanonicalUtcInstant, isCanonicalUuid4 } from "./normalContract";
import {
  PAPER_CHECK_STATUSES,
  PAPER_CLOSEST_ALGORITHM,
  PAPER_CLOSEST_REF_LIMIT,
  PAPER_EVALUATION_REASON,
  PAPER_EVALUATION_STATUS,
  PAPER_LEDGER_ENGINE_STATUS,
  PAPER_LEDGER_HIGH_WATER_MARKS,
  PAPER_LEDGER_LIFECYCLE_STATUS,
  PAPER_LEDGER_SAFETY_STATE,
  PAPER_MARKET_SESSIONS,
  PAPER_OVERALL_STATES,
  PAPER_READINESS_CHECK_KEYS,
  PAPER_REVIEW_ERROR_HTTP,
  PAPER_REVIEW_FIXED_MEMBER_PATHS,
  PAPER_REVIEW_ISSUE_KINDS,
  PAPER_REVIEW_MEMBER_COUNT,
  PAPER_REVIEW_RETRYABLE_CODES,
  PAPER_REVIEW_STATUSES,
  PAPER_REVIEW_TOTAL_PARTS,
  PAPER_STAGE_A_SAFEGUARDS,
  type PaperEvidenceMember,
  type PaperLedgerOrigin,
  type PaperReadinessCheck,
  type PaperReadinessCheckKey,
  type PaperReadinessSnapshot,
  type PaperReviewCreateRequest,
  type PaperReviewError,
  type PaperReviewErrorCode,
  type PaperReviewErrorEnvelope,
  type PaperReviewIssue,
  type PaperReviewIssueKind,
  type PaperReviewProgress,
  type PaperReviewReady,
  type PaperReviewStatus,
  type PaperReviewStatusValue,
  type PaperReviewTerminalOpenerClaim,
  type PaperSupportingEvidenceRef,
} from "./types";

export type PaperReviewParseResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

class StageBContractError extends Error {
  constructor(resource: string, path: string, message: string) {
    super(`${resource}:${path}: ${message}`);
    this.name = "StageBContractError";
  }
}

function attempt<T>(
  resource: string,
  parse: () => T,
): PaperReviewParseResult<T> {
  try {
    return { ok: true, value: parse() };
  } catch (error) {
    if (error instanceof StageBContractError) {
      return { ok: false, error: error.message };
    }
    return {
      ok: false,
      error: `${resource}:$: ${
        error instanceof Error ? error.message : String(error)
      }`,
    };
  }
}

function check(
  condition: unknown,
  resource: string,
  path: string,
  message: string,
): asserts condition {
  if (!condition) {
    throw new StageBContractError(resource, path, message);
  }
}

// --- shared scalar boundary (§3.1) -----------------------------------------

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value) as object | null;
  return prototype === Object.prototype || prototype === null;
}

function requireObject(
  value: unknown,
  resource: string,
  path: string,
): Record<string, unknown> {
  check(isPlainObject(value), resource, path, "必須係 plain object");
  return value;
}

function requireExactKeys(
  object: Record<string, unknown>,
  keys: readonly string[],
  resource: string,
  path: string,
): void {
  const actual = Object.keys(object).sort();
  const wanted = [...keys].sort();
  check(
    actual.length === wanted.length &&
      actual.every((key, index) => key === wanted[index]),
    resource,
    path,
    `key set 唔一致（收到 ${actual.join(",")}）`,
  );
}

function requireArray(
  value: unknown,
  resource: string,
  path: string,
): unknown[] {
  check(Array.isArray(value), resource, path, "必須係 array");
  return value;
}

/** Machine identity or path: non-empty, no NUL, no surrounding whitespace. */
function requireExactString(
  value: unknown,
  resource: string,
  path: string,
): string {
  check(
    typeof value === "string" &&
      value.length > 0 &&
      value === value.trim() &&
      !value.includes("\u0000"),
    resource,
    path,
    "必須係非空、無首尾空白、無 NUL 嘅字串",
  );
  return value;
}

/**
 * Human text. Deliberately not trim-guarded: the persisted Terminal opener
 * ends with exactly one LF, so trimming would reject the real payload.
 */
function requireHumanText(
  value: unknown,
  resource: string,
  path: string,
): string {
  check(
    typeof value === "string" &&
      value.length > 0 &&
      !value.includes("\u0000"),
    resource,
    path,
    "必須係非空、無 NUL 嘅文字",
  );
  return value;
}

function requireLiteral<T extends string>(
  value: unknown,
  expected: T,
  resource: string,
  path: string,
): T {
  check(value === expected, resource, path, `必須 exact 等於 ${expected}`);
  return expected;
}

function requireEnum<T extends string>(
  value: unknown,
  allowed: readonly T[],
  resource: string,
  path: string,
): T {
  check(
    typeof value === "string" && (allowed as readonly string[]).includes(value),
    resource,
    path,
    `唔係已批准嘅值（只接受 ${allowed.join("／")}）`,
  );
  return value as T;
}

/** JSON integer in `0..Number.MAX_SAFE_INTEGER`; booleans rejected. */
function requireCount(
  value: unknown,
  resource: string,
  path: string,
): number {
  check(
    typeof value === "number" &&
      Number.isSafeInteger(value) &&
      value >= 0 &&
      !Object.is(value, -0),
    resource,
    path,
    "必須係 0 至 Number.MAX_SAFE_INTEGER 之間嘅安全整數",
  );
  return value;
}

/** Byte counts that the contract requires to be positive. */
function requirePositiveBytes(
  value: unknown,
  resource: string,
  path: string,
): number {
  const count = requireCount(value, resource, path);
  check(count > 0, resource, path, "必須大過 0");
  return count;
}

/** Finite JSON number; NaN, Infinity and negative zero rejected. */
function requireFinite(
  value: unknown,
  resource: string,
  path: string,
): number {
  check(
    typeof value === "number" && Number.isFinite(value) && !Object.is(value, -0),
    resource,
    path,
    "必須係 finite number（拒 NaN／Infinity／負零／boolean／字串）",
  );
  return value;
}

function requireExactNumber(
  value: unknown,
  expected: number,
  resource: string,
  path: string,
): number {
  const actual = requireFinite(value, resource, path);
  check(
    actual === expected && !Object.is(actual, -0),
    resource,
    path,
    `必須 exact 等於 ${expected}`,
  );
  return expected;
}

function requireBoolean(
  value: unknown,
  resource: string,
  path: string,
): boolean {
  check(typeof value === "boolean", resource, path, "必須係 boolean");
  return value;
}

const LOWER_SHA256 = /^[0-9a-f]{64}$/;
const TRADER_ID = /^trader-[0-9a-f]{32}$/;
const ACCOUNT_ID = /^paper-account-[0-9a-f]{32}$/;
const LEDGER_ORIGIN_ID = /^paper-ledger-[0-9a-f]{32}$/;
const SNAPSHOT_ID = /^paper-review-[0-9a-f]{32}$/;
const CANONICAL_UTC =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{6}))?Z$/;

function requirePattern(
  value: unknown,
  pattern: RegExp,
  resource: string,
  path: string,
  message: string,
): string {
  const text = requireExactString(value, resource, path);
  check(pattern.test(text), resource, path, message);
  return text;
}

function requireSha256(
  value: unknown,
  resource: string,
  path: string,
): string {
  return requirePattern(
    value,
    LOWER_SHA256,
    resource,
    path,
    "必須係 64 位小寫 hex SHA-256",
  );
}

function requireUuid4(value: unknown, resource: string, path: string): string {
  check(
    isCanonicalUuid4(value),
    resource,
    path,
    "必須係 canonical 小寫 UUID4",
  );
  return value;
}

/** Reuses the proven Stage A producer set; no second timestamp parser. */
function requireUtc(value: unknown, resource: string, path: string): string {
  check(
    isCanonicalUtcInstant(value),
    resource,
    path,
    "必須係 canonical UTC instant（無 fraction 或 exact 六位微秒、大寫 Z、真實日曆日）",
  );
  return value;
}

/**
 * Same safe identity boundary as the backend `ResultsCatalog._RUN_ID_SAFE`.
 * A run id reaches artifact member paths, so anything outside this set — a
 * separator, a leading dot, a drive letter, whitespace or a NUL — must never
 * be interpolated, sanitised or trimmed into a path.
 */
const SAFE_RUN_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;

export function isSafePaperRunId(value: unknown): value is string {
  return typeof value === "string" && SAFE_RUN_ID.test(value);
}

/** §3.1: the prefixed trader identity that ends up inside the ZIP filename. */
export function isSafePaperTraderId(value: unknown): value is string {
  return typeof value === "string" && TRADER_ID.test(value);
}

function requireSafeRunId(
  value: unknown,
  resource: string,
  path: string,
): string {
  check(
    isSafePaperRunId(value),
    resource,
    path,
    "必須符合 ^[A-Za-z0-9][A-Za-z0-9_.-]*$（唔准分隔符、開頭點、空白或 NUL）",
  );
  return value;
}

/**
 * §11.1: `YYYYMMDDTHHMMSSffffffZ`, always six microsecond digits, derived from
 * the persisted `captured_at` — never re-read from a clock.
 */
export function paperReviewCompactUtc(capturedAt: string): string | null {
  if (!isCanonicalUtcInstant(capturedAt)) {
    return null;
  }
  const parts = CANONICAL_UTC.exec(capturedAt);
  if (parts === null) {
    return null;
  }
  const [, year, month, day, hour, minute, second, fraction] = parts;
  return `${year}${month}${day}T${hour}${minute}${second}${fraction ?? "000000"}Z`;
}

function buildReviewMemberPaths(runId: string): readonly string[] {
  return [
    ...PAPER_REVIEW_FIXED_MEMBER_PATHS,
    `baseline/trades/${runId}.json`,
    `baseline/equity/${runId}.json`,
    `baseline/events/${runId}.json`,
  ];
}

function buildLedgerMemberPaths(runId: string): readonly string[] {
  return [
    "baseline/result.json",
    `baseline/trades/${runId}.json`,
    `baseline/equity/${runId}.json`,
    `baseline/events/${runId}.json`,
  ];
}

/**
 * §11.1: the ten ordered ZIP member paths for one baseline run.
 * An unsafe run id yields `null` — never a sanitised path, a trimmed value,
 * a basename, an empty array or a thrown error.
 */
export function paperReviewMemberPaths(
  runId: string,
): readonly string[] | null {
  return isSafePaperRunId(runId) ? buildReviewMemberPaths(runId) : null;
}

/** §8: the four ordered baseline member paths captured at create time. */
export function paperLedgerMemberPaths(
  runId: string,
): readonly string[] | null {
  return isSafePaperRunId(runId) ? buildLedgerMemberPaths(runId) : null;
}

export function paperReviewErrorHttpStatus(code: PaperReviewErrorCode): number {
  return PAPER_REVIEW_ERROR_HTTP[code];
}

export function paperReviewErrorRetryable(code: PaperReviewErrorCode): boolean {
  return (PAPER_REVIEW_RETRYABLE_CODES as readonly string[]).includes(code);
}

function parseEvidenceMembers(
  value: unknown,
  expectedPaths: readonly string[],
  resource: string,
  path: string,
): PaperEvidenceMember[] {
  const rows = requireArray(value, resource, path);
  check(
    rows.length === expectedPaths.length,
    resource,
    path,
    `必須 exact ${expectedPaths.length} 個 member（收到 ${rows.length}）`,
  );
  return rows.map((row, index) => {
    const rowPath = `${path}[${index}]`;
    const object = requireObject(row, resource, rowPath);
    requireExactKeys(object, ["path", "bytes", "sha256"], resource, rowPath);
    const memberPath = requireExactString(
      object.path,
      resource,
      `${rowPath}.path`,
    );
    check(
      memberPath === expectedPaths[index],
      resource,
      `${rowPath}.path`,
      `次序或路徑唔啱，第 ${index + 1} 個必須係 ${expectedPaths[index]}`,
    );
    return {
      path: memberPath,
      bytes: requirePositiveBytes(object.bytes, resource, `${rowPath}.bytes`),
      sha256: requireSha256(object.sha256, resource, `${rowPath}.sha256`),
    };
  });
}

/**
 * §8: the ledger readiness snapshot must be semantically identical to the one
 * frozen inside `paper_trader.v1`. It is re-implemented here rather than
 * imported because Stage A parsers are frozen for this batch and their
 * internal helper is not exported; both share the same timestamp predicate.
 */
function parseReadinessSnapshotObject(
  value: unknown,
  resource: string,
  path: string,
): PaperReadinessSnapshot {
  const object = requireObject(value, resource, path);
  requireExactKeys(
    object,
    ["schema", "overall", "market_session", "checked_at", "checks"],
    resource,
    path,
  );
  requireLiteral(
    object.schema,
    "paper_readiness_snapshot.v1",
    resource,
    `${path}.schema`,
  );
  const checkedAt = requireUtc(
    object.checked_at,
    resource,
    `${path}.checked_at`,
  );
  const rows = requireArray(object.checks, resource, `${path}.checks`);
  check(
    rows.length === PAPER_READINESS_CHECK_KEYS.length,
    resource,
    `${path}.checks`,
    `必須 exact ${PAPER_READINESS_CHECK_KEYS.length} 項檢查`,
  );
  const checks: PaperReadinessCheck[] = rows.map((row, index) => {
    const rowPath = `${path}.checks[${index}]`;
    const item = requireObject(row, resource, rowPath);
    requireExactKeys(
      item,
      ["key", "status", "reason", "checked_at"],
      resource,
      rowPath,
    );
    const expectedKey: PaperReadinessCheckKey =
      PAPER_READINESS_CHECK_KEYS[index];
    check(
      item.key === expectedKey,
      resource,
      `${rowPath}.key`,
      `次序唔啱，第 ${index + 1} 項必須係 ${expectedKey}`,
    );
    const rowCheckedAt = requireUtc(
      item.checked_at,
      resource,
      `${rowPath}.checked_at`,
    );
    check(
      rowCheckedAt === checkedAt,
      resource,
      `${rowPath}.checked_at`,
      "必須 exact 等於同一次檢查嘅 checked_at",
    );
    return {
      key: expectedKey,
      status: requireEnum(
        item.status,
        PAPER_CHECK_STATUSES,
        resource,
        `${rowPath}.status`,
      ),
      reason: requireHumanText(item.reason, resource, `${rowPath}.reason`),
      checked_at: rowCheckedAt,
    };
  });
  const overall = requireEnum(
    object.overall,
    PAPER_OVERALL_STATES,
    resource,
    `${path}.overall`,
  );
  const derived = checks.every((item) => item.status === "ready")
    ? "ready"
    : "blocked";
  /*
   * Option A: the persisted snapshot may be `blocked` — a provision-only
   * trader is allowed to exist while the external providers are unverified.
   * The derived equality still holds, so nothing can be painted better than
   * its four checks.
   */
  check(
    overall === derived,
    resource,
    `${path}.overall`,
    "overall 同四項檢查狀態唔一致",
  );
  return {
    schema: "paper_readiness_snapshot.v1",
    overall,
    market_session: requireEnum(
      object.market_session,
      PAPER_MARKET_SESSIONS,
      resource,
      `${path}.market_session`,
    ),
    checked_at: checkedAt,
    checks,
  };
}

// --- §8 paper_ledger_origin.v1 ---------------------------------------------

const RESOURCE_LEDGER = "paper-ledger-origin";

export interface PaperLedgerExpectation {
  readonly trader_id: string;
}

export function parsePaperLedgerOrigin(
  body: unknown,
  expected: PaperLedgerExpectation,
): PaperReviewParseResult<PaperLedgerOrigin> {
  return attempt(RESOURCE_LEDGER, () => {
    const root = requireObject(body, RESOURCE_LEDGER, "$");
    requireExactKeys(
      root,
      [
        "schema",
        "ledger_origin_id",
        "trader_id",
        "origin_at",
        "lifecycle",
        "strategy",
        "contract",
        "baseline",
        "account",
        "balances",
        "high_water_marks",
        "positions",
        "orders",
        "safety",
        "readiness_snapshot",
        "interpretation",
      ],
      RESOURCE_LEDGER,
      "$",
    );
    requireLiteral(
      root.schema,
      "paper_ledger_origin.v1",
      RESOURCE_LEDGER,
      "$.schema",
    );
    const traderId = requirePattern(
      root.trader_id,
      TRADER_ID,
      RESOURCE_LEDGER,
      "$.trader_id",
      "必須係 trader- 加 32 位小寫 hex",
    );
    check(
      traderId === expected.trader_id,
      RESOURCE_LEDGER,
      "$.trader_id",
      "同要求嘅交易員身份唔一致",
    );

    const lifecycle = requireObject(
      root.lifecycle,
      RESOURCE_LEDGER,
      "$.lifecycle",
    );
    requireExactKeys(
      lifecycle,
      ["status", "engine_status"],
      RESOURCE_LEDGER,
      "$.lifecycle",
    );
    requireLiteral(
      lifecycle.status,
      PAPER_LEDGER_LIFECYCLE_STATUS,
      RESOURCE_LEDGER,
      "$.lifecycle.status",
    );
    requireLiteral(
      lifecycle.engine_status,
      PAPER_LEDGER_ENGINE_STATUS,
      RESOURCE_LEDGER,
      "$.lifecycle.engine_status",
    );

    const strategy = requireObject(
      root.strategy,
      RESOURCE_LEDGER,
      "$.strategy",
    );
    requireExactKeys(
      strategy,
      ["strategy_id", "name", "content_sha256"],
      RESOURCE_LEDGER,
      "$.strategy",
    );

    const contract = requireObject(
      root.contract,
      RESOURCE_LEDGER,
      "$.contract",
    );
    requireExactKeys(
      contract,
      ["contract_id", "exchange", "timezone"],
      RESOURCE_LEDGER,
      "$.contract",
    );

    const baseline = requireObject(
      root.baseline,
      RESOURCE_LEDGER,
      "$.baseline",
    );
    requireExactKeys(
      baseline,
      [
        "run_id",
        "result_sha256",
        "range_start",
        "range_end",
        "rejection_count",
        "closest_algorithm",
        "closest_rejection_refs",
        "members",
      ],
      RESOURCE_LEDGER,
      "$.baseline",
    );
    // Guarded before any member path is derived, so a traversal run id is
    // rejected even when every derived path in the body agrees with it.
    const runId = requireSafeRunId(
      baseline.run_id,
      RESOURCE_LEDGER,
      "$.baseline.run_id",
    );
    requireLiteral(
      baseline.closest_algorithm,
      PAPER_CLOSEST_ALGORITHM,
      RESOURCE_LEDGER,
      "$.baseline.closest_algorithm",
    );
    const rejectionCount = requireCount(
      baseline.rejection_count,
      RESOURCE_LEDGER,
      "$.baseline.rejection_count",
    );
    const refRows = requireArray(
      baseline.closest_rejection_refs,
      RESOURCE_LEDGER,
      "$.baseline.closest_rejection_refs",
    );
    check(
      refRows.length === Math.min(PAPER_CLOSEST_REF_LIMIT, rejectionCount),
      RESOURCE_LEDGER,
      "$.baseline.closest_rejection_refs",
      "數量必須等於 min(3, rejection_count)",
    );
    const closestRefs = refRows.map((row, index) =>
      requireExactString(
        row,
        RESOURCE_LEDGER,
        `$.baseline.closest_rejection_refs[${index}]`,
      ),
    );
    check(
      new Set(closestRefs).size === closestRefs.length,
      RESOURCE_LEDGER,
      "$.baseline.closest_rejection_refs",
      "唔可以有重複 ref",
    );
    const members = parseEvidenceMembers(
      baseline.members,
      buildLedgerMemberPaths(runId),
      RESOURCE_LEDGER,
      "$.baseline.members",
    );
    const baselineResultSha = requireSha256(
      baseline.result_sha256,
      RESOURCE_LEDGER,
      "$.baseline.result_sha256",
    );
    check(
      members[0].sha256 === baselineResultSha,
      RESOURCE_LEDGER,
      "$.baseline.members[0].sha256",
      "同 baseline result_sha256 唔一致",
    );

    const account = requireObject(root.account, RESOURCE_LEDGER, "$.account");
    requireExactKeys(
      account,
      ["account_id", "currency", "initial_capital", "independent_account"],
      RESOURCE_LEDGER,
      "$.account",
    );
    const initialCapital = requireFinite(
      account.initial_capital,
      RESOURCE_LEDGER,
      "$.account.initial_capital",
    );
    check(
      initialCapital > 0,
      RESOURCE_LEDGER,
      "$.account.initial_capital",
      "必須大過 0",
    );
    check(
      account.independent_account === true,
      RESOURCE_LEDGER,
      "$.account.independent_account",
      "必須 exact 係 true",
    );

    const balances = requireObject(
      root.balances,
      RESOURCE_LEDGER,
      "$.balances",
    );
    requireExactKeys(
      balances,
      ["cash", "equity", "realized_pnl", "unrealized_pnl"],
      RESOURCE_LEDGER,
      "$.balances",
    );
    // A zero-trade origin is defined by these equalities; the frontend never
    // fills them in, it only refuses anything that is not an origin.
    const cash = requireExactNumber(
      balances.cash,
      initialCapital,
      RESOURCE_LEDGER,
      "$.balances.cash",
    );
    const equity = requireExactNumber(
      balances.equity,
      initialCapital,
      RESOURCE_LEDGER,
      "$.balances.equity",
    );
    const realized = requireExactNumber(
      balances.realized_pnl,
      0,
      RESOURCE_LEDGER,
      "$.balances.realized_pnl",
    );
    const unrealized = requireExactNumber(
      balances.unrealized_pnl,
      0,
      RESOURCE_LEDGER,
      "$.balances.unrealized_pnl",
    );

    const hwm = requireObject(
      root.high_water_marks,
      RESOURCE_LEDGER,
      "$.high_water_marks",
    );
    requireExactKeys(
      hwm,
      Object.keys(PAPER_LEDGER_HIGH_WATER_MARKS),
      RESOURCE_LEDGER,
      "$.high_water_marks",
    );
    for (const key of Object.keys(
      PAPER_LEDGER_HIGH_WATER_MARKS,
    ) as (keyof typeof PAPER_LEDGER_HIGH_WATER_MARKS)[]) {
      const actual = requireCount(
        hwm[key],
        RESOURCE_LEDGER,
        `$.high_water_marks.${key}`,
      );
      check(
        actual === PAPER_LEDGER_HIGH_WATER_MARKS[key],
        RESOURCE_LEDGER,
        `$.high_water_marks.${key}`,
        `provisioned profile 必須 exact 等於 ${PAPER_LEDGER_HIGH_WATER_MARKS[key]}`,
      );
    }

    for (const key of ["positions", "orders"] as const) {
      const rows = requireArray(root[key], RESOURCE_LEDGER, `$.${key}`);
      check(
        rows.length === PAPER_LEDGER_HIGH_WATER_MARKS[key],
        RESOURCE_LEDGER,
        `$.${key}`,
        "引擎未啟用，必須係空 array",
      );
    }

    const safety = requireObject(root.safety, RESOURCE_LEDGER, "$.safety");
    requireExactKeys(
      safety,
      [
        "state",
        "drawdown_r",
        "loss_streak",
        "max_drawdown_r",
        "max_losing_streak",
        "blind_minutes",
      ],
      RESOURCE_LEDGER,
      "$.safety",
    );
    requireLiteral(
      safety.state,
      PAPER_LEDGER_SAFETY_STATE,
      RESOURCE_LEDGER,
      "$.safety.state",
    );
    requireExactNumber(safety.drawdown_r, 0, RESOURCE_LEDGER, "$.safety.drawdown_r");
    requireExactNumber(
      safety.loss_streak,
      0,
      RESOURCE_LEDGER,
      "$.safety.loss_streak",
    );
    for (const key of [
      "max_drawdown_r",
      "max_losing_streak",
      "blind_minutes",
    ] as const) {
      requireExactNumber(
        safety[key],
        PAPER_STAGE_A_SAFEGUARDS[key],
        RESOURCE_LEDGER,
        `$.safety.${key}`,
      );
    }

    const readinessSnapshot = parseReadinessSnapshotObject(
      root.readiness_snapshot,
      RESOURCE_LEDGER,
      "$.readiness_snapshot",
    );

    const interpretation = requireObject(
      root.interpretation,
      RESOURCE_LEDGER,
      "$.interpretation",
    );
    requireExactKeys(
      interpretation,
      [
        "evaluation_status",
        "reason",
        "owner_view",
        "categories",
        "supporting_evidence_refs",
      ],
      RESOURCE_LEDGER,
      "$.interpretation",
    );
    requireLiteral(
      interpretation.evaluation_status,
      PAPER_EVALUATION_STATUS,
      RESOURCE_LEDGER,
      "$.interpretation.evaluation_status",
    );
    requireLiteral(
      interpretation.reason,
      PAPER_EVALUATION_REASON,
      RESOURCE_LEDGER,
      "$.interpretation.reason",
    );
    const categories = requireArray(
      interpretation.categories,
      RESOURCE_LEDGER,
      "$.interpretation.categories",
    );
    check(
      categories.length === 1 && categories[0] === "unknown",
      RESOURCE_LEDGER,
      "$.interpretation.categories",
      "provisioned profile 必須 exact 係 [\"unknown\"]",
    );
    const supportRows = requireArray(
      interpretation.supporting_evidence_refs,
      RESOURCE_LEDGER,
      "$.interpretation.supporting_evidence_refs",
    );
    check(
      supportRows.length === closestRefs.length,
      RESOURCE_LEDGER,
      "$.interpretation.supporting_evidence_refs",
      "數量必須同 closest_rejection_refs 一致",
    );
    const eventsMemberPath = members[3].path;
    const supporting: PaperSupportingEvidenceRef[] = supportRows.map(
      (row, index) => {
        const rowPath = `$.interpretation.supporting_evidence_refs[${index}]`;
        const item = requireObject(row, RESOURCE_LEDGER, rowPath);
        requireExactKeys(item, ["path", "evidence_id"], RESOURCE_LEDGER, rowPath);
        const refPath = requireExactString(
          item.path,
          RESOURCE_LEDGER,
          `${rowPath}.path`,
        );
        check(
          refPath === eventsMemberPath,
          RESOURCE_LEDGER,
          `${rowPath}.path`,
          "必須指向已捕獲嘅 baseline events member",
        );
        const evidenceId = requireExactString(
          item.evidence_id,
          RESOURCE_LEDGER,
          `${rowPath}.evidence_id`,
        );
        check(
          evidenceId === closestRefs[index],
          RESOURCE_LEDGER,
          `${rowPath}.evidence_id`,
          "同 closest_rejection_refs 唔一致",
        );
        return { path: refPath, evidence_id: evidenceId };
      },
    );

    return {
      schema: "paper_ledger_origin.v1",
      ledger_origin_id: requirePattern(
        root.ledger_origin_id,
        LEDGER_ORIGIN_ID,
        RESOURCE_LEDGER,
        "$.ledger_origin_id",
        "必須係 paper-ledger- 加 32 位小寫 hex",
      ),
      trader_id: traderId,
      origin_at: requireUtc(root.origin_at, RESOURCE_LEDGER, "$.origin_at"),
      lifecycle: {
        status: PAPER_LEDGER_LIFECYCLE_STATUS,
        engine_status: PAPER_LEDGER_ENGINE_STATUS,
      },
      strategy: {
        strategy_id: requireExactString(
          strategy.strategy_id,
          RESOURCE_LEDGER,
          "$.strategy.strategy_id",
        ),
        name: requireHumanText(
          strategy.name,
          RESOURCE_LEDGER,
          "$.strategy.name",
        ),
        content_sha256: requireSha256(
          strategy.content_sha256,
          RESOURCE_LEDGER,
          "$.strategy.content_sha256",
        ),
      },
      contract: {
        contract_id: requireExactString(
          contract.contract_id,
          RESOURCE_LEDGER,
          "$.contract.contract_id",
        ),
        exchange: requireExactString(
          contract.exchange,
          RESOURCE_LEDGER,
          "$.contract.exchange",
        ),
        timezone: requireExactString(
          contract.timezone,
          RESOURCE_LEDGER,
          "$.contract.timezone",
        ),
      },
      baseline: {
        run_id: runId,
        result_sha256: baselineResultSha,
        range_start: requireUtc(
          baseline.range_start,
          RESOURCE_LEDGER,
          "$.baseline.range_start",
        ),
        range_end: requireUtc(
          baseline.range_end,
          RESOURCE_LEDGER,
          "$.baseline.range_end",
        ),
        rejection_count: rejectionCount,
        closest_algorithm: PAPER_CLOSEST_ALGORITHM,
        closest_rejection_refs: closestRefs,
        members,
      },
      account: {
        account_id: requirePattern(
          account.account_id,
          ACCOUNT_ID,
          RESOURCE_LEDGER,
          "$.account.account_id",
          "必須係 paper-account- 加 32 位小寫 hex",
        ),
        currency: requireExactString(
          account.currency,
          RESOURCE_LEDGER,
          "$.account.currency",
        ),
        initial_capital: initialCapital,
        independent_account: true,
      },
      balances: {
        cash,
        equity,
        realized_pnl: realized,
        unrealized_pnl: unrealized,
      },
      high_water_marks: PAPER_LEDGER_HIGH_WATER_MARKS,
      positions: [],
      orders: [],
      safety: {
        state: PAPER_LEDGER_SAFETY_STATE,
        drawdown_r: 0,
        loss_streak: 0,
        max_drawdown_r: PAPER_STAGE_A_SAFEGUARDS.max_drawdown_r,
        max_losing_streak: PAPER_STAGE_A_SAFEGUARDS.max_losing_streak,
        blind_minutes: PAPER_STAGE_A_SAFEGUARDS.blind_minutes,
      },
      readiness_snapshot: readinessSnapshot,
      interpretation: {
        evaluation_status: PAPER_EVALUATION_STATUS,
        reason: PAPER_EVALUATION_REASON,
        owner_view: requireHumanText(
          interpretation.owner_view,
          RESOURCE_LEDGER,
          "$.interpretation.owner_view",
        ),
        categories: ["unknown"],
        supporting_evidence_refs: supporting,
      },
    };
  });
}

// --- §9.1 paper_review_create_request.v1 -----------------------------------

const RESOURCE_CREATE = "paper-review-create-request";

/**
 * §9.1: the body carries an identity and nothing else — no trader, snapshot,
 * strategy, baseline, cutoff or zero values.
 */
export function buildPaperReviewCreateRequest(
  requestId: string,
): PaperReviewParseResult<PaperReviewCreateRequest> {
  return attempt(RESOURCE_CREATE, () => {
    requireUuid4(requestId, RESOURCE_CREATE, "$.request_id");
    return {
      schema: "paper_review_create_request.v1",
      request_id: requestId,
    };
  });
}

// --- §10 paper_review_error.v1 ---------------------------------------------

const RESOURCE_ERROR = "paper-review-error";

/**
 * §10 has exactly three closed shapes for an error body, and the caller always
 * knows which one it may accept. A single optional-field record could express
 * impossible states (an accepted failure with no progress, a pre-acceptance
 * failure carrying a snapshot), so the expectation is discriminated instead.
 *
 * `accepted_assigned` exists because the browser cannot know the snapshot id
 * the backend minted for a request that failed on its very first POST: the id
 * is only ever seen in that response.
 */
export type PaperReviewErrorExpectation =
  | {
      readonly mode: "pre_acceptance";
      /** Exact echo; `null` when the route never carried a request id. */
      readonly request_id: string | null;
    }
  | {
      readonly mode: "accepted_known";
      readonly request_id: string;
      readonly snapshot_id: string;
    }
  | {
      readonly mode: "accepted_assigned";
      readonly request_id: string;
    };

function parseNullableSha(
  value: unknown,
  resource: string,
  path: string,
): string | null {
  if (value === null) {
    return null;
  }
  return requireSha256(value, resource, path);
}

function parseNullableExactString(
  value: unknown,
  resource: string,
  path: string,
): string | null {
  if (value === null) {
    return null;
  }
  return requireExactString(value, resource, path);
}

function parseProgressObject(
  value: unknown,
  resource: string,
  path: string,
  allowedParts: readonly string[],
  requireNullCurrentPart: boolean,
): PaperReviewProgress {
  const object = requireObject(value, resource, path);
  requireExactKeys(
    object,
    ["completed_parts", "total_parts", "current_part"],
    resource,
    path,
  );
  const completed = requireCount(
    object.completed_parts,
    resource,
    `${path}.completed_parts`,
  );
  requireExactNumber(
    object.total_parts,
    PAPER_REVIEW_TOTAL_PARTS,
    resource,
    `${path}.total_parts`,
  );
  check(
    completed <= PAPER_REVIEW_TOTAL_PARTS,
    resource,
    `${path}.completed_parts`,
    `唔可以大過 ${PAPER_REVIEW_TOTAL_PARTS}`,
  );
  let currentPart: string | null = null;
  if (object.current_part !== null) {
    check(
      !requireNullCurrentPart,
      resource,
      `${path}.current_part`,
      "terminal 狀態必須係 null",
    );
    currentPart = requireExactString(
      object.current_part,
      resource,
      `${path}.current_part`,
    );
    check(
      allowedParts.includes(currentPart),
      resource,
      `${path}.current_part`,
      "必須係十個已批准 ZIP member path 之一",
    );
  }
  return {
    completed_parts: completed,
    total_parts: PAPER_REVIEW_TOTAL_PARTS,
    current_part: currentPart,
  };
}

function parseIssues(
  value: unknown,
  resource: string,
  path: string,
): PaperReviewIssue[] {
  const rows = requireArray(value, resource, path);
  return rows.map((row, index) => {
    const rowPath = `${path}[${index}]`;
    const object = requireObject(row, resource, rowPath);
    requireExactKeys(
      object,
      [
        "kind",
        "path",
        "source_ref",
        "expected_sha256",
        "actual_sha256",
        "ref_chain",
      ],
      resource,
      rowPath,
    );
    const chainRows = requireArray(
      object.ref_chain,
      resource,
      `${rowPath}.ref_chain`,
    );
    return {
      kind: requireEnum<PaperReviewIssueKind>(
        object.kind,
        PAPER_REVIEW_ISSUE_KINDS,
        resource,
        `${rowPath}.kind`,
      ),
      path: parseNullableExactString(object.path, resource, `${rowPath}.path`),
      source_ref: parseNullableExactString(
        object.source_ref,
        resource,
        `${rowPath}.source_ref`,
      ),
      expected_sha256: parseNullableSha(
        object.expected_sha256,
        resource,
        `${rowPath}.expected_sha256`,
      ),
      actual_sha256: parseNullableSha(
        object.actual_sha256,
        resource,
        `${rowPath}.actual_sha256`,
      ),
      ref_chain: chainRows.map((entry, entryIndex) =>
        requireExactString(
          entry,
          resource,
          `${rowPath}.ref_chain[${entryIndex}]`,
        ),
      ),
    };
  });
}

function parseErrorObject(
  value: unknown,
  expected: PaperReviewErrorExpectation,
  resource: string,
  path: string,
  allowedParts: readonly string[],
): PaperReviewError {
  const object = requireObject(value, resource, path);
  requireExactKeys(
    object,
    [
      "schema",
      "code",
      "message",
      "retryable",
      "request_id",
      "snapshot_id",
      "progress",
      "issues",
    ],
    resource,
    path,
  );
  requireLiteral(
    object.schema,
    "paper_review_error.v1",
    resource,
    `${path}.schema`,
  );
  const code = requireEnum<PaperReviewErrorCode>(
    object.code,
    Object.keys(PAPER_REVIEW_ERROR_HTTP) as PaperReviewErrorCode[],
    resource,
    `${path}.code`,
  );
  const retryable = requireBoolean(
    object.retryable,
    resource,
    `${path}.retryable`,
  );
  check(
    retryable === paperReviewErrorRetryable(code),
    resource,
    `${path}.retryable`,
    "同已批准 code 對應嘅 retryable 唔一致",
  );

  const requestId =
    object.request_id === null
      ? null
      : requireUuid4(object.request_id, resource, `${path}.request_id`);
  check(
    requestId === expected.request_id,
    resource,
    `${path}.request_id`,
    "同要求嘅 request 身份唔一致",
  );
  const snapshotId =
    object.snapshot_id === null
      ? null
      : requirePattern(
          object.snapshot_id,
          SNAPSHOT_ID,
          resource,
          `${path}.snapshot_id`,
          "必須係 paper-review- 加 32 位小寫 hex",
        );
  if (expected.mode === "pre_acceptance") {
    check(
      snapshotId === null,
      resource,
      `${path}.snapshot_id`,
      "未接受嘅 request 唔可以帶 snapshot 身份",
    );
  } else if (expected.mode === "accepted_known") {
    check(
      snapshotId === expected.snapshot_id,
      resource,
      `${path}.snapshot_id`,
      "同要求嘅 snapshot 身份唔一致",
    );
  } else {
    // accepted_assigned: the backend is the only source of this identity, so
    // it must still be a canonical, present one — never null, never guessed.
    check(
      snapshotId !== null,
      resource,
      `${path}.snapshot_id`,
      "已接受嘅 request 必須帶 backend 分配嘅 snapshot 身份",
    );
  }

  let progress: PaperReviewProgress | null = null;
  if (expected.mode === "pre_acceptance") {
    check(
      object.progress === null,
      resource,
      `${path}.progress`,
      "未接受嘅 request 必須係 null",
    );
  } else {
    check(
      object.progress !== null,
      resource,
      `${path}.progress`,
      "已接受嘅 request 必須帶最後已驗證進度",
    );
    progress = parseProgressObject(
      object.progress,
      resource,
      `${path}.progress`,
      allowedParts,
      true,
    );
  }

  return {
    schema: "paper_review_error.v1",
    code,
    message: requireHumanText(object.message, resource, `${path}.message`),
    retryable,
    request_id: requestId,
    snapshot_id: snapshotId,
    progress,
    issues: parseIssues(object.issues, resource, `${path}.issues`),
  };
}

/** §10: the inner object, as embedded in `paper_review_status.v1`. */
export function parsePaperReviewErrorObject(
  body: unknown,
  expected: PaperReviewErrorExpectation,
  allowedParts: readonly string[] = [],
): PaperReviewParseResult<PaperReviewError> {
  return attempt(RESOURCE_ERROR, () =>
    parseErrorObject(body, expected, RESOURCE_ERROR, "$", allowedParts),
  );
}

/** §10: the HTTP body, whose only top-level key is `detail`. */
export function parsePaperReviewErrorEnvelope(
  body: unknown,
  expected: PaperReviewErrorExpectation,
  allowedParts: readonly string[] = [],
): PaperReviewParseResult<PaperReviewErrorEnvelope> {
  return attempt(RESOURCE_ERROR, () => {
    const root = requireObject(body, RESOURCE_ERROR, "$");
    requireExactKeys(root, ["detail"], RESOURCE_ERROR, "$");
    return {
      detail: parseErrorObject(
        root.detail,
        expected,
        RESOURCE_ERROR,
        "$.detail",
        allowedParts,
      ),
    };
  });
}

// --- §9.2 paper_review_ready.v1 --------------------------------------------

const RESOURCE_READY = "paper-review-ready";

export interface PaperReviewReadyExpectation {
  readonly trader_id: string;
  readonly captured_at: string;
  readonly baseline_run_id: string;
}

function parseReadyObject(
  value: unknown,
  expected: PaperReviewReadyExpectation,
  resource: string,
  path: string,
): PaperReviewReady {
  const object = requireObject(value, resource, path);
  requireExactKeys(
    object,
    [
      "schema",
      "display_filename",
      "artifact_bytes",
      "artifact_sha256",
      "member_count",
      "members",
      "terminal_opener",
      "ready_at",
    ],
    resource,
    path,
  );
  requireLiteral(
    object.schema,
    "paper_review_ready.v1",
    resource,
    `${path}.schema`,
  );
  // The caller expectation is itself untrusted input: it produces the ten
  // member paths and the download filename.
  check(
    isSafePaperTraderId(expected.trader_id),
    resource,
    `${path}.display_filename`,
    "要求嘅 trader 身份唔係 trader- 加 32 位小寫 hex",
  );
  check(
    isSafePaperRunId(expected.baseline_run_id),
    resource,
    `${path}.members`,
    "要求嘅 baseline run 身份唔安全，唔可以砌成 member path",
  );
  const compact = paperReviewCompactUtc(expected.captured_at);
  check(
    compact !== null,
    resource,
    `${path}.display_filename`,
    "要求嘅 captured_at 唔係 canonical UTC",
  );
  const expectedFilename = `paper-review-${expected.trader_id}-${compact}.zip`;
  const filename = requireExactString(
    object.display_filename,
    resource,
    `${path}.display_filename`,
  );
  check(
    filename === expectedFilename,
    resource,
    `${path}.display_filename`,
    "必須 exact 等於 trader 身份加已保存 captured_at 嘅 compact 形式",
  );
  requireExactNumber(
    object.member_count,
    PAPER_REVIEW_MEMBER_COUNT,
    resource,
    `${path}.member_count`,
  );
  const members = parseEvidenceMembers(
    object.members,
    buildReviewMemberPaths(expected.baseline_run_id),
    resource,
    `${path}.members`,
  );
  const opener = requireObject(
    object.terminal_opener,
    resource,
    `${path}.terminal_opener`,
  );
  requireExactKeys(
    opener,
    ["bytes", "sha256"],
    resource,
    `${path}.terminal_opener`,
  );
  return {
    schema: "paper_review_ready.v1",
    display_filename: filename,
    artifact_bytes: requirePositiveBytes(
      object.artifact_bytes,
      resource,
      `${path}.artifact_bytes`,
    ),
    artifact_sha256: requireSha256(
      object.artifact_sha256,
      resource,
      `${path}.artifact_sha256`,
    ),
    member_count: PAPER_REVIEW_MEMBER_COUNT,
    members,
    terminal_opener: {
      bytes: requirePositiveBytes(
        opener.bytes,
        resource,
        `${path}.terminal_opener.bytes`,
      ),
      sha256: requireSha256(
        opener.sha256,
        resource,
        `${path}.terminal_opener.sha256`,
      ),
    },
    ready_at: requireUtc(object.ready_at, resource, `${path}.ready_at`),
  };
}

export function parsePaperReviewReady(
  body: unknown,
  expected: PaperReviewReadyExpectation,
): PaperReviewParseResult<PaperReviewReady> {
  return attempt(RESOURCE_READY, () =>
    parseReadyObject(body, expected, RESOURCE_READY, "$"),
  );
}

// --- §9.2 paper_review_status.v1 -------------------------------------------

const RESOURCE_STATUS = "paper-review-status";

export interface PaperReviewStatusExpectation {
  readonly request_id: string;
  readonly trader_id: string;
  readonly baseline_run_id: string;
}

export function parsePaperReviewStatus(
  body: unknown,
  expected: PaperReviewStatusExpectation,
): PaperReviewParseResult<PaperReviewStatus> {
  return attempt(RESOURCE_STATUS, () => {
    const root = requireObject(body, RESOURCE_STATUS, "$");
    requireExactKeys(
      root,
      [
        "schema",
        "request_id",
        "snapshot_id",
        "trader_id",
        "status",
        "captured_at",
        "progress",
        "ready",
        "error",
      ],
      RESOURCE_STATUS,
      "$",
    );
    requireLiteral(
      root.schema,
      "paper_review_status.v1",
      RESOURCE_STATUS,
      "$.schema",
    );
    const requestId = requireUuid4(
      root.request_id,
      RESOURCE_STATUS,
      "$.request_id",
    );
    check(
      requestId === expected.request_id,
      RESOURCE_STATUS,
      "$.request_id",
      "同要求嘅 request 身份唔一致",
    );
    const traderId = requirePattern(
      root.trader_id,
      TRADER_ID,
      RESOURCE_STATUS,
      "$.trader_id",
      "必須係 trader- 加 32 位小寫 hex",
    );
    check(
      traderId === expected.trader_id,
      RESOURCE_STATUS,
      "$.trader_id",
      "同要求嘅交易員身份唔一致",
    );
    const snapshotId = requirePattern(
      root.snapshot_id,
      SNAPSHOT_ID,
      RESOURCE_STATUS,
      "$.snapshot_id",
      "必須係 paper-review- 加 32 位小寫 hex",
    );
    const capturedAt = requireUtc(
      root.captured_at,
      RESOURCE_STATUS,
      "$.captured_at",
    );
    const status = requireEnum<PaperReviewStatusValue>(
      root.status,
      PAPER_REVIEW_STATUSES,
      RESOURCE_STATUS,
      "$.status",
    );
    // The expectation drives the allowed `current_part` values, so an unsafe
    // baseline run must fail closed even when the body agrees with it.
    check(
      isSafePaperRunId(expected.baseline_run_id),
      RESOURCE_STATUS,
      "$.progress.current_part",
      "要求嘅 baseline run 身份唔安全，唔可以砌成 member path",
    );
    const allowedParts = buildReviewMemberPaths(expected.baseline_run_id);

    const progress = parseProgressObject(
      root.progress,
      RESOURCE_STATUS,
      "$.progress",
      allowedParts,
      status !== "preparing",
    );

    let ready: PaperReviewReady | null = null;
    let error: PaperReviewError | null = null;

    if (status === "preparing") {
      check(root.ready === null, RESOURCE_STATUS, "$.ready", "必須係 null");
      check(root.error === null, RESOURCE_STATUS, "$.error", "必須係 null");
    } else if (status === "ready") {
      check(root.error === null, RESOURCE_STATUS, "$.error", "必須係 null");
      check(
        progress.completed_parts === PAPER_REVIEW_TOTAL_PARTS,
        RESOURCE_STATUS,
        "$.progress.completed_parts",
        `ready 必須 exact 等於 ${PAPER_REVIEW_TOTAL_PARTS}`,
      );
      ready = parseReadyObject(
        root.ready,
        {
          trader_id: traderId,
          captured_at: capturedAt,
          baseline_run_id: expected.baseline_run_id,
        },
        RESOURCE_STATUS,
        "$.ready",
      );
    } else {
      check(root.ready === null, RESOURCE_STATUS, "$.ready", "必須係 null");
      error = parseErrorObject(
        root.error,
        {
          mode: "accepted_known",
          request_id: requestId,
          snapshot_id: snapshotId,
        },
        RESOURCE_STATUS,
        "$.error",
        allowedParts,
      );
    }

    return {
      schema: "paper_review_status.v1",
      request_id: requestId,
      snapshot_id: snapshotId,
      trader_id: traderId,
      status,
      captured_at: capturedAt,
      progress,
      ready,
      error,
    };
  });
}

// --- §9.4 paper_review_terminal_opener.v1 ----------------------------------

const RESOURCE_OPENER = "paper-review-terminal-opener";

export interface PaperReviewOpenerExpectation {
  readonly snapshot_id: string;
}

/**
 * Shape and identity only. `bytes` and `sha256` stay unverified claims here:
 * confirming them needs async Web Crypto and belongs to the consumer batch.
 */
export function parsePaperReviewTerminalOpenerClaim(
  body: unknown,
  expected: PaperReviewOpenerExpectation,
): PaperReviewParseResult<PaperReviewTerminalOpenerClaim> {
  return attempt(RESOURCE_OPENER, () => {
    const root = requireObject(body, RESOURCE_OPENER, "$");
    requireExactKeys(
      root,
      ["schema", "snapshot_id", "text", "bytes", "sha256"],
      RESOURCE_OPENER,
      "$",
    );
    requireLiteral(
      root.schema,
      "paper_review_terminal_opener.v1",
      RESOURCE_OPENER,
      "$.schema",
    );
    const snapshotId = requirePattern(
      root.snapshot_id,
      SNAPSHOT_ID,
      RESOURCE_OPENER,
      "$.snapshot_id",
      "必須係 paper-review- 加 32 位小寫 hex",
    );
    check(
      snapshotId === expected.snapshot_id,
      RESOURCE_OPENER,
      "$.snapshot_id",
      "同要求嘅 snapshot 身份唔一致",
    );
    return {
      schema: "paper_review_terminal_opener.v1",
      snapshot_id: snapshotId,
      text: requireHumanText(root.text, RESOURCE_OPENER, "$.text"),
      bytes: requirePositiveBytes(root.bytes, RESOURCE_OPENER, "$.bytes"),
      sha256: requireSha256(root.sha256, RESOURCE_OPENER, "$.sha256"),
    };
  });
}
