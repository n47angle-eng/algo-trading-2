/**
 * P6 Stage A strict consumer.
 *
 * Every parser below is exact: exact top-level key set, exact nested key set,
 * closed enums, exact identity echo, exact counts, and no coercion. Unknown or
 * malformed responses can never become success or empty — they fail closed.
 *
 * Authority: docs/superpowers/specs/2026-07-29-p6-isolated-creation-seam-design.md
 * (§6.1–§6.5, §7) plus the approved contract-source correction
 * docs/superpowers/specs/2026-07-29-p6-contract-candidate-source-correction-design.md.
 */

import {
  PAPER_CHECK_STATUSES,
  PAPER_CONTRACT_SELECTION_KEYS,
  PAPER_ERROR_STATUS,
  PAPER_LIFECYCLE_STATUSES,
  PAPER_MARKET_SESSIONS,
  PAPER_OVERALL_STATES,
  PAPER_PROVISIONING_PERMIT_MINUTES,
  PAPER_PROVISIONING_STATES,
  PAPER_READINESS_CHECK_KEYS,
  PAPER_REQUEST_STATUSES,
  PAPER_SELECTION_KEYS,
  PAPER_STAGE_A_LIFECYCLE_REASON,
  PAPER_STAGE_A_SAFEGUARDS,
  PAPER_STRATEGY_SELECTION_KEYS,
  type PaperApiError,
  type PaperBaseline,
  type PaperBaselineList,
  type PaperContractList,
  type PaperContractRow,
  type PaperContractSelection,
  type PaperEligibleStrategy,
  type PaperEligibleStrategyList,
  type PaperErrorCode,
  type PaperLifecycleReason,
  type PaperOverallState,
  type PaperProvisioningAuthorization,
  type PaperProvisioningReadiness,
  type PaperProvisioningReadinessRequest,
  type PaperReadiness,
  type PaperReadinessCheck,
  type PaperReadinessCheckKey,
  type PaperReadinessRequest,
  type PaperReadinessSnapshot,
  type PaperSelection,
  type PaperStrategySelection,
  type PaperSupportingDecision,
  type PaperTrader,
  type PaperTraderCreateRequest,
  type PaperTraderList,
  type PaperTraderRequestStatusRecord,
} from "./types";
import type { PaperHttpResult } from "../../api/client";

export type PaperParseResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

class PaperContractError extends Error {
  readonly resource: string;
  readonly path: string;

  constructor(resource: string, path: string, message: string) {
    super(`${resource}:${path}: ${message}`);
    this.name = "PaperContractError";
    this.resource = resource;
    this.path = path;
  }
}

function attempt<T>(resource: string, parse: () => T): PaperParseResult<T> {
  try {
    return { ok: true, value: parse() };
  } catch (error) {
    if (error instanceof PaperContractError) {
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
    throw new PaperContractError(resource, path, message);
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value) as object | null;
  return prototype === Object.prototype || prototype === null;
}

function exactKeys(
  object: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(object).sort();
  const wanted = [...expected].sort();
  return (
    actual.length === wanted.length &&
    actual.every((key, index) => key === wanted[index])
  );
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
  check(
    exactKeys(object, keys),
    resource,
    path,
    `key set 唔一致（收到 ${Object.keys(object).sort().join(",")}）`,
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

/** Non-empty string with no leading or trailing whitespace; never trimmed. */
function isExactString(value: unknown): value is string {
  return (
    typeof value === "string" && value.length > 0 && value === value.trim()
  );
}

function requireExactString(
  value: unknown,
  resource: string,
  path: string,
): string {
  check(
    isExactString(value),
    resource,
    path,
    "必須係非空、無首尾空白嘅字串",
  );
  return value;
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function requireFiniteNumber(
  value: unknown,
  resource: string,
  path: string,
): number {
  check(
    finiteNumber(value),
    resource,
    path,
    "必須係 finite number（唔接受字串、boolean、NaN、Infinity）",
  );
  return value;
}

function requireBoolean(
  value: unknown,
  resource: string,
  path: string,
): boolean {
  check(typeof value === "boolean", resource, path, "必須係 boolean");
  return value as boolean;
}

const LOWER_SHA256 = /^[0-9a-f]{64}$/;
const CANONICAL_UUID4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const TRADER_ID = /^trader-[0-9a-f]{32}$/;
const ACCOUNT_ID = /^paper-account-[0-9a-f]{32}$/;
/*
 * Producer set: `datetime.astimezone(UTC).isoformat().replace("+00:00", "Z")`
 * emits either no fraction or exactly six microsecond digits, always with an
 * uppercase `Z`. Anything else (1–5 or 7–9 digits, an offset, a lowercase z)
 * is outside the contract, and the date must be a real calendar instant.
 */
const UTC_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{6}))?Z$/;

export function isCanonicalUuid4(value: unknown): value is string {
  return typeof value === "string" && CANONICAL_UUID4.test(value);
}

function requireSha256(
  value: unknown,
  resource: string,
  path: string,
): string {
  check(
    typeof value === "string" && LOWER_SHA256.test(value),
    resource,
    path,
    "必須係 64 位小寫 hex SHA-256",
  );
  return value as string;
}

/** Canonical producer text for a real calendar instant; never normalised. */
export function isCanonicalUtcInstant(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const parts = UTC_INSTANT.exec(value);
  if (parts === null) {
    return false;
  }
  const fraction = parts[7];
  // `isoformat()` drops the fraction entirely when microsecond is 0, so an
  // all-zero six-digit fraction has no producer and must be rejected.
  if (fraction !== undefined && /^0{6}$/.test(fraction)) {
    return false;
  }
  const year = Number(parts[1]);
  const month = Number(parts[2]);
  const day = Number(parts[3]);
  const hour = Number(parts[4]);
  const minute = Number(parts[5]);
  const second = Number(parts[6]);
  // Python `datetime` years are 1..9999, so year 0000 has no producer.
  if (year < 1) {
    return false;
  }
  /*
   * `Date.UTC(1, ...)` silently means 1901, which would reject the producer's
   * legal year 0001..0099. Build the instant explicitly instead, then compare
   * every component back so 2026-02-30 still cannot normalise through.
   */
  const instant = new Date(0);
  instant.setUTCFullYear(year, month - 1, day);
  instant.setUTCHours(hour, minute, second, 0);
  return (
    instant.getUTCFullYear() === year &&
    instant.getUTCMonth() === month - 1 &&
    instant.getUTCDate() === day &&
    instant.getUTCHours() === hour &&
    instant.getUTCMinutes() === minute &&
    instant.getUTCSeconds() === second
  );
}

function requireUtcInstant(
  value: unknown,
  resource: string,
  path: string,
): string {
  check(
    isCanonicalUtcInstant(value),
    resource,
    path,
    "必須係 canonical UTC instant（無 fraction 或 exact 六位微秒、大寫 Z、真實日曆日）",
  );
  return value;
}

function requirePattern(
  value: unknown,
  pattern: RegExp,
  resource: string,
  path: string,
  message: string,
): string {
  check(
    typeof value === "string" && pattern.test(value),
    resource,
    path,
    message,
  );
  return value as string;
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

/**
 * Only the exact listed statuses are success. Any other status, a non-JSON
 * body, or a transport-level envelope drift fails closed.
 */
function successBody(
  response: PaperHttpResult,
  resource: string,
  allowedStatus: readonly number[],
): unknown {
  check(
    allowedStatus.includes(response.status),
    resource,
    "$http.status",
    `HTTP ${String(response.status)} 唔係已批准嘅成功 status`,
  );
  check(
    response.ok === true,
    resource,
    "$http.ok",
    "response 唔係成功",
  );
  check(
    response.jsonParsed === true,
    resource,
    "$http.body",
    "response 唔係有效 JSON",
  );
  return response.body;
}

// --- identity helpers -------------------------------------------------------

export function paperStrategySelectionEquals(
  left: PaperStrategySelection,
  right: PaperStrategySelection,
): boolean {
  return (
    left.strategy_id === right.strategy_id &&
    left.content_sha256 === right.content_sha256
  );
}

export function paperContractSelectionEquals(
  left: PaperContractSelection,
  right: PaperContractSelection,
): boolean {
  return (
    paperStrategySelectionEquals(left, right) &&
    left.contract_id === right.contract_id
  );
}

export function paperSelectionEquals(
  left: PaperSelection,
  right: PaperSelection,
): boolean {
  return (
    paperContractSelectionEquals(left, right) &&
    left.baseline_run_id === right.baseline_run_id &&
    left.baseline_result_sha256 === right.baseline_result_sha256
  );
}

/** Late-response guard: a stale generation must never reach state. */
export function isPaperGenerationCurrent(
  currentGeneration: number,
  expectedGeneration: number,
): boolean {
  return currentGeneration === expectedGeneration;
}

function parseStrategySelection(
  value: unknown,
  expected: PaperStrategySelection,
  resource: string,
  path: string,
): PaperStrategySelection {
  const object = requireObject(value, resource, path);
  requireExactKeys(object, PAPER_STRATEGY_SELECTION_KEYS, resource, path);
  const selection: PaperStrategySelection = {
    strategy_id: requireExactString(
      object.strategy_id,
      resource,
      `${path}.strategy_id`,
    ),
    content_sha256: requireSha256(
      object.content_sha256,
      resource,
      `${path}.content_sha256`,
    ),
  };
  check(
    paperStrategySelectionEquals(selection, expected),
    resource,
    path,
    "同今次請求嘅策略身份唔一致",
  );
  return selection;
}

function parseContractSelection(
  value: unknown,
  expected: PaperContractSelection,
  resource: string,
  path: string,
): PaperContractSelection {
  const object = requireObject(value, resource, path);
  requireExactKeys(object, PAPER_CONTRACT_SELECTION_KEYS, resource, path);
  const selection: PaperContractSelection = {
    strategy_id: requireExactString(
      object.strategy_id,
      resource,
      `${path}.strategy_id`,
    ),
    content_sha256: requireSha256(
      object.content_sha256,
      resource,
      `${path}.content_sha256`,
    ),
    contract_id: requireExactString(
      object.contract_id,
      resource,
      `${path}.contract_id`,
    ),
  };
  check(
    paperContractSelectionEquals(selection, expected),
    resource,
    path,
    "同今次請求嘅策略／合約身份唔一致",
  );
  return selection;
}

function parseFullSelection(
  value: unknown,
  expected: PaperSelection,
  resource: string,
  path: string,
): PaperSelection {
  const object = requireObject(value, resource, path);
  requireExactKeys(object, PAPER_SELECTION_KEYS, resource, path);
  const selection: PaperSelection = {
    strategy_id: requireExactString(
      object.strategy_id,
      resource,
      `${path}.strategy_id`,
    ),
    content_sha256: requireSha256(
      object.content_sha256,
      resource,
      `${path}.content_sha256`,
    ),
    contract_id: requireExactString(
      object.contract_id,
      resource,
      `${path}.contract_id`,
    ),
    baseline_run_id: requireExactString(
      object.baseline_run_id,
      resource,
      `${path}.baseline_run_id`,
    ),
    baseline_result_sha256: requireSha256(
      object.baseline_result_sha256,
      resource,
      `${path}.baseline_result_sha256`,
    ),
  };
  check(
    paperSelectionEquals(selection, expected),
    resource,
    path,
    "同今次請求嘅完整選擇身份唔一致",
  );
  return selection;
}

// --- §6.1 eligible strategies ----------------------------------------------

const RESOURCE_ELIGIBLE = "eligible-strategies";

export function parsePaperEligibleStrategyList(
  response: PaperHttpResult,
): PaperParseResult<PaperEligibleStrategyList> {
  return attempt(RESOURCE_ELIGIBLE, () => {
    const body = requireObject(
      successBody(response, RESOURCE_ELIGIBLE, [200]),
      RESOURCE_ELIGIBLE,
      "$",
    );
    requireExactKeys(
      body,
      ["schema", "count", "strategies"],
      RESOURCE_ELIGIBLE,
      "$",
    );
    check(
      body.schema === "eligible_strategy_list.v1",
      RESOURCE_ELIGIBLE,
      "$.schema",
      "唔係已批准嘅 schema",
    );
    const rows = requireArray(
      body.strategies,
      RESOURCE_ELIGIBLE,
      "$.strategies",
    );
    check(
      body.count === rows.length,
      RESOURCE_ELIGIBLE,
      "$.count",
      `count 同 strategies 長度唔一致（${String(body.count)} vs ${rows.length}）`,
    );
    const seen = new Set<string>();
    const strategies = rows.map((row, index) => {
      const path = `$.strategies[${index}]`;
      const object = requireObject(row, RESOURCE_ELIGIBLE, path);
      requireExactKeys(
        object,
        ["strategy_id", "content_sha256", "supporting_decisions"],
        RESOURCE_ELIGIBLE,
        path,
      );
      const strategyId = requireExactString(
        object.strategy_id,
        RESOURCE_ELIGIBLE,
        `${path}.strategy_id`,
      );
      const contentSha = requireSha256(
        object.content_sha256,
        RESOURCE_ELIGIBLE,
        `${path}.content_sha256`,
      );
      const identity = `${strategyId}@${contentSha}`;
      check(
        !seen.has(identity),
        RESOURCE_ELIGIBLE,
        `${path}.strategy_id`,
        "重複策略身份",
      );
      seen.add(identity);
      const decisionRows = requireArray(
        object.supporting_decisions,
        RESOURCE_ELIGIBLE,
        `${path}.supporting_decisions`,
      );
      check(
        decisionRows.length > 0,
        RESOURCE_ELIGIBLE,
        `${path}.supporting_decisions`,
        "冇任何不可變晉升決定支持",
      );
      const supporting: PaperSupportingDecision[] = decisionRows.map(
        (decisionRow, decisionIndex) => {
          const decisionPath = `${path}.supporting_decisions[${decisionIndex}]`;
          const decision = requireObject(
            decisionRow,
            RESOURCE_ELIGIBLE,
            decisionPath,
          );
          requireExactKeys(
            decision,
            ["decision_id", "run_id"],
            RESOURCE_ELIGIBLE,
            decisionPath,
          );
          return {
            decision_id: requireExactString(
              decision.decision_id,
              RESOURCE_ELIGIBLE,
              `${decisionPath}.decision_id`,
            ),
            run_id: requireExactString(
              decision.run_id,
              RESOURCE_ELIGIBLE,
              `${decisionPath}.run_id`,
            ),
          };
        },
      );
      const strategy: PaperEligibleStrategy = {
        strategy_id: strategyId,
        content_sha256: contentSha,
        supporting_decisions: supporting,
      };
      return strategy;
    });
    return {
      schema: "eligible_strategy_list.v1",
      count: body.count,
      strategies,
    };
  });
}

// --- §6.1a contract candidates ---------------------------------------------

const RESOURCE_CONTRACTS = "paper-contracts";

export function parsePaperContractList(
  response: PaperHttpResult,
  expected: PaperStrategySelection,
): PaperParseResult<PaperContractList> {
  return attempt(RESOURCE_CONTRACTS, () => {
    const body = requireObject(
      successBody(response, RESOURCE_CONTRACTS, [200]),
      RESOURCE_CONTRACTS,
      "$",
    );
    requireExactKeys(
      body,
      ["schema", "selection", "count", "contracts"],
      RESOURCE_CONTRACTS,
      "$",
    );
    check(
      body.schema === "paper_contract_list.v1",
      RESOURCE_CONTRACTS,
      "$.schema",
      "唔係已批准嘅 schema",
    );
    const selection = parseStrategySelection(
      body.selection,
      expected,
      RESOURCE_CONTRACTS,
      "$.selection",
    );
    const rows = requireArray(
      body.contracts,
      RESOURCE_CONTRACTS,
      "$.contracts",
    );
    check(
      body.count === rows.length,
      RESOURCE_CONTRACTS,
      "$.count",
      `count 同 contracts 長度唔一致（${String(body.count)} vs ${rows.length}）`,
    );
    let previousId: string | null = null;
    const contracts: PaperContractRow[] = rows.map((row, index) => {
      const path = `$.contracts[${index}]`;
      const object = requireObject(row, RESOURCE_CONTRACTS, path);
      requireExactKeys(
        object,
        ["contract_id", "symbol", "display_name"],
        RESOURCE_CONTRACTS,
        path,
      );
      const contractId = requireExactString(
        object.contract_id,
        RESOURCE_CONTRACTS,
        `${path}.contract_id`,
      );
      // Same root symbol may legitimately have several expiry contracts, so
      // uniqueness and ordering are on contract_id only.
      if (previousId !== null) {
        check(
          previousId < contractId,
          RESOURCE_CONTRACTS,
          `${path}.contract_id`,
          "唔係 contract_id 遞增次序，或者有重複合約",
        );
      }
      previousId = contractId;
      return {
        contract_id: contractId,
        symbol: requireExactString(
          object.symbol,
          RESOURCE_CONTRACTS,
          `${path}.symbol`,
        ),
        display_name: requireExactString(
          object.display_name,
          RESOURCE_CONTRACTS,
          `${path}.display_name`,
        ),
      };
    });
    return {
      schema: "paper_contract_list.v1",
      selection,
      count: body.count,
      contracts,
    };
  });
}

// --- §6.2 baseline candidates ----------------------------------------------

const RESOURCE_BASELINES = "paper-baselines";

function parseBaselineRow(
  value: unknown,
  resource: string,
  path: string,
): PaperBaseline {
  const object = requireObject(value, resource, path);
  requireExactKeys(
    object,
    [
      "run_id",
      "result_sha256",
      "range_start",
      "range_end",
      "currency",
      "initial_capital",
      "trade_count",
      "net_r",
      "validation_run",
      "integrity",
    ],
    resource,
    path,
  );
  const initialCapital = requireFiniteNumber(
    object.initial_capital,
    resource,
    `${path}.initial_capital`,
  );
  check(
    initialCapital > 0,
    resource,
    `${path}.initial_capital`,
    "初始資金必須大過 0",
  );
  const tradeCount = requireFiniteNumber(
    object.trade_count,
    resource,
    `${path}.trade_count`,
  );
  check(
    Number.isInteger(tradeCount) && tradeCount >= 0,
    resource,
    `${path}.trade_count`,
    "成交數必須係 0 或以上嘅整數",
  );
  const validationRun = requireBoolean(
    object.validation_run,
    resource,
    `${path}.validation_run`,
  );
  check(
    validationRun === false,
    resource,
    `${path}.validation_run`,
    "工程驗證用嘅回測唔可以做對照基準",
  );
  return {
    run_id: requireExactString(object.run_id, resource, `${path}.run_id`),
    result_sha256: requireSha256(
      object.result_sha256,
      resource,
      `${path}.result_sha256`,
    ),
    range_start: requireUtcInstant(
      object.range_start,
      resource,
      `${path}.range_start`,
    ),
    range_end: requireUtcInstant(
      object.range_end,
      resource,
      `${path}.range_end`,
    ),
    currency: requireExactString(
      object.currency,
      resource,
      `${path}.currency`,
    ),
    initial_capital: initialCapital,
    trade_count: tradeCount,
    net_r: requireFiniteNumber(object.net_r, resource, `${path}.net_r`),
    validation_run: false,
    integrity: requireEnum(
      object.integrity,
      ["verified"],
      resource,
      `${path}.integrity`,
    ),
  };
}

export function parsePaperBaselineList(
  response: PaperHttpResult,
  expected: PaperContractSelection,
): PaperParseResult<PaperBaselineList> {
  return attempt(RESOURCE_BASELINES, () => {
    const body = requireObject(
      successBody(response, RESOURCE_BASELINES, [200]),
      RESOURCE_BASELINES,
      "$",
    );
    requireExactKeys(
      body,
      ["schema", "selection", "count", "baselines"],
      RESOURCE_BASELINES,
      "$",
    );
    check(
      body.schema === "paper_baseline_list.v1",
      RESOURCE_BASELINES,
      "$.schema",
      "唔係已批准嘅 schema",
    );
    const selection = parseContractSelection(
      body.selection,
      expected,
      RESOURCE_BASELINES,
      "$.selection",
    );
    const rows = requireArray(
      body.baselines,
      RESOURCE_BASELINES,
      "$.baselines",
    );
    check(
      body.count === rows.length,
      RESOURCE_BASELINES,
      "$.count",
      `count 同 baselines 長度唔一致（${String(body.count)} vs ${rows.length}）`,
    );
    const seen = new Set<string>();
    const baselines = rows.map((row, index) => {
      const path = `$.baselines[${index}]`;
      const baseline = parseBaselineRow(row, RESOURCE_BASELINES, path);
      check(
        !seen.has(baseline.run_id),
        RESOURCE_BASELINES,
        `${path}.run_id`,
        "重複回測身份",
      );
      seen.add(baseline.run_id);
      return baseline;
    });
    return {
      schema: "paper_baseline_list.v1",
      selection,
      count: body.count,
      baselines,
    };
  });
}

// --- §6.3 readiness ---------------------------------------------------------

const RESOURCE_READINESS = "paper-readiness";

export function buildPaperReadinessRequest(
  selection: PaperSelection,
): PaperReadinessRequest {
  return {
    schema: "paper_readiness_request.v1",
    selection: { ...selection },
  };
}

function parseReadinessChecks(
  value: unknown,
  resource: string,
  path: string,
  expectedCheckedAt: string,
): PaperReadinessCheck[] {
  const rows = requireArray(value, resource, path);
  check(
    rows.length === PAPER_READINESS_CHECK_KEYS.length,
    resource,
    path,
    `必須 exact ${PAPER_READINESS_CHECK_KEYS.length} 項檢查（收到 ${rows.length}）`,
  );
  return rows.map((row, index) => {
    const rowPath = `${path}[${index}]`;
    const object = requireObject(row, resource, rowPath);
    requireExactKeys(
      object,
      ["key", "status", "reason", "checked_at"],
      resource,
      rowPath,
    );
    const expectedKey: PaperReadinessCheckKey =
      PAPER_READINESS_CHECK_KEYS[index];
    check(
      object.key === expectedKey,
      resource,
      `${rowPath}.key`,
      `次序唔啱，第 ${index + 1} 項必須係 ${expectedKey}`,
    );
    return {
      key: expectedKey,
      status: requireEnum(
        object.status,
        PAPER_CHECK_STATUSES,
        resource,
        `${rowPath}.status`,
      ),
      reason: requireExactString(
        object.reason,
        resource,
        `${rowPath}.reason`,
      ),
      checked_at: requireCheckedAt(
        object.checked_at,
        expectedCheckedAt,
        resource,
        `${rowPath}.checked_at`,
      ),
    };
  });
}

/** Every child check is stamped from the same atomic readiness evaluation. */
function requireCheckedAt(
  value: unknown,
  expected: string,
  resource: string,
  path: string,
): string {
  const instant = requireUtcInstant(value, resource, path);
  check(
    instant === expected,
    resource,
    path,
    "必須 exact 等於同一次檢查嘅 checked_at",
  );
  return instant;
}

/** §6.3: every check ready ⇒ overall ready; any blocked/unknown ⇒ blocked. */
export function derivePaperOverall(
  checks: readonly PaperReadinessCheck[],
): PaperOverallState {
  return checks.every((item) => item.status === "ready") ? "ready" : "blocked";
}

function requireConsistentOverall(
  overall: PaperOverallState,
  checks: readonly PaperReadinessCheck[],
  resource: string,
  path: string,
): void {
  check(
    overall === derivePaperOverall(checks),
    resource,
    path,
    "overall 同四項檢查狀態唔一致",
  );
}

/**
 * The readiness object itself, with no HTTP envelope. The provisioning
 * response embeds exactly this shape, so both callers must share one parser:
 * a second copy could drift and silently accept two different truths.
 */
function parseReadinessObject(
  value: unknown,
  expected: PaperSelection,
  resource: string,
  path: string,
): PaperReadiness {
  const body = requireObject(value, resource, path);
  requireExactKeys(
    body,
    ["schema", "selection", "overall", "market_session", "checked_at", "checks"],
    resource,
    path,
  );
  check(
    body.schema === "paper_readiness.v1",
    resource,
    `${path}.schema`,
    "唔係已批准嘅 schema",
  );
  const selection = parseFullSelection(
    body.selection,
    expected,
    resource,
    `${path}.selection`,
  );
  const checkedAt = requireUtcInstant(
    body.checked_at,
    resource,
    `${path}.checked_at`,
  );
  const checks = parseReadinessChecks(
    body.checks,
    resource,
    `${path}.checks`,
    checkedAt,
  );
  const overall = requireEnum(
    body.overall,
    PAPER_OVERALL_STATES,
    resource,
    `${path}.overall`,
  );
  requireConsistentOverall(overall, checks, resource, `${path}.overall`);
  return {
    schema: "paper_readiness.v1",
    selection,
    overall,
    market_session: requireEnum(
      body.market_session,
      PAPER_MARKET_SESSIONS,
      resource,
      `${path}.market_session`,
    ),
    checked_at: checkedAt,
    checks,
  };
}

export function parsePaperReadiness(
  response: PaperHttpResult,
  expected: PaperSelection,
): PaperParseResult<PaperReadiness> {
  return attempt(RESOURCE_READINESS, () =>
    parseReadinessObject(
      successBody(response, RESOURCE_READINESS, [200]),
      expected,
      RESOURCE_READINESS,
      "$",
    ),
  );
}

// --- provisioning authorization (Option A §7) -------------------------------

const RESOURCE_PROVISIONING = "paper-provisioning-readiness";

const PROVISION_OPERATION_ID = /^paper-provision-[0-9a-f]{32}$/;

const MILLISECONDS_PER_MINUTE = 60_000;

export function buildPaperProvisioningRequest(
  selection: PaperSelection,
): PaperProvisioningReadinessRequest {
  return {
    schema: "paper_provisioning_readiness_request.v1",
    selection: { ...selection },
  };
}

/**
 * §7.2: the wire discriminator is the public `schema` key only. `schema_version`
 * is an internal backend field; a body carrying it — alone or beside `schema` —
 * has no approved producer and is refused rather than read through an alias.
 */
function requireCanonicalSchemaKey(
  object: Record<string, unknown>,
  resource: string,
  path: string,
): void {
  check(
    !Object.hasOwn(object, "schema_version"),
    resource,
    `${path}.schema_version`,
    "internal 版本欄位唔可以出現喺 wire body；public discriminator 只可以係 schema",
  );
}

function requireNullableUtcInstant(
  value: unknown,
  resource: string,
  path: string,
): string | null {
  return value === null ? null : requireUtcInstant(value, resource, path);
}

/**
 * §7.2: the permit lasts exactly thirty minutes. The comparison keeps the
 * microsecond text identical and then checks the millisecond distance, so a
 * fraction cannot hide a drift the parser would otherwise round away.
 */
function requireExactPermitWindow(
  authorizedAt: string,
  expiresAt: string,
  resource: string,
  path: string,
): void {
  const authorizedFraction = authorizedAt.slice(19, -1);
  const expiresFraction = expiresAt.slice(19, -1);
  check(
    authorizedFraction === expiresFraction,
    resource,
    path,
    "expires_at 嘅微秒必須同 authorized_at 一致",
  );
  check(
    Date.parse(expiresAt) - Date.parse(authorizedAt) ===
      PAPER_PROVISIONING_PERMIT_MINUTES * MILLISECONDS_PER_MINUTE,
    resource,
    path,
    `expires_at 必須 exact 係 authorized_at 之後 ${PAPER_PROVISIONING_PERMIT_MINUTES} 分鐘`,
  );
}

function parseProvisioningAuthorization(
  value: unknown,
  resource: string,
  path: string,
): PaperProvisioningAuthorization {
  const object = requireObject(value, resource, path);
  requireCanonicalSchemaKey(object, resource, path);
  requireExactKeys(
    object,
    [
      "schema",
      "state",
      "operation_id",
      "authorized_at",
      "expires_at",
      "reason",
    ],
    resource,
    path,
  );
  check(
    object.schema === "paper_provisioning_authorization.v1",
    resource,
    `${path}.schema`,
    "唔係已批准嘅 schema",
  );
  const state = requireEnum(
    object.state,
    PAPER_PROVISIONING_STATES,
    resource,
    `${path}.state`,
  );
  const operationId =
    object.operation_id === null
      ? null
      : requirePattern(
          object.operation_id,
          PROVISION_OPERATION_ID,
          resource,
          `${path}.operation_id`,
          "必須係 paper-provision- 加 32 位小寫 hex",
        );
  const authorizedAt = requireNullableUtcInstant(
    object.authorized_at,
    resource,
    `${path}.authorized_at`,
  );
  const expiresAt = requireNullableUtcInstant(
    object.expires_at,
    resource,
    `${path}.expires_at`,
  );
  /*
   * §7.2: `disabled` is also the request-relative answer for a selection that
   * no permit covers, so it must not leak another operation's identity. Every
   * other state describes a real permit and carries all three fields.
   */
  if (state === "disabled") {
    check(
      operationId === null && authorizedAt === null && expiresAt === null,
      resource,
      path,
      "disabled 唔可以帶 operation 身份或時間",
    );
  } else {
    check(
      operationId !== null && authorizedAt !== null && expiresAt !== null,
      resource,
      path,
      "已存在嘅授權必須帶 operation 身份同兩個時間",
    );
    requireExactPermitWindow(
      authorizedAt,
      expiresAt,
      resource,
      `${path}.expires_at`,
    );
  }
  return {
    schema: "paper_provisioning_authorization.v1",
    state,
    operation_id: operationId,
    authorized_at: authorizedAt,
    expires_at: expiresAt,
    // Never trimmed, never rendered as the Owner-facing reason.
    reason: requireExactString(object.reason, resource, `${path}.reason`),
  };
}

/** §7.3: the decoded body only; the HTTP envelope stays the caller's problem. */
export function parsePaperProvisioningReadiness(
  body: unknown,
  expected: PaperSelection,
): PaperParseResult<PaperProvisioningReadiness> {
  return attempt(RESOURCE_PROVISIONING, () => {
    const root = requireObject(body, RESOURCE_PROVISIONING, "$");
    requireCanonicalSchemaKey(root, RESOURCE_PROVISIONING, "$");
    requireExactKeys(
      root,
      [
        "schema",
        "selection",
        "can_provision",
        "authorization",
        "runtime_readiness",
      ],
      RESOURCE_PROVISIONING,
      "$",
    );
    check(
      root.schema === "paper_provisioning_readiness.v1",
      RESOURCE_PROVISIONING,
      "$.schema",
      "唔係已批准嘅 schema",
    );
    const selection = parseFullSelection(
      root.selection,
      expected,
      RESOURCE_PROVISIONING,
      "$.selection",
    );
    const canProvision = requireBoolean(
      root.can_provision,
      RESOURCE_PROVISIONING,
      "$.can_provision",
    );
    const authorization = parseProvisioningAuthorization(
      root.authorization,
      RESOURCE_PROVISIONING,
      "$.authorization",
    );
    const runtime = parseReadinessObject(
      root.runtime_readiness,
      expected,
      RESOURCE_PROVISIONING,
      "$.runtime_readiness",
    );
    // Both selections were checked against the caller's exact selection; this
    // states the invariant directly so the response cannot describe two.
    check(
      paperSelectionEquals(selection, runtime.selection),
      RESOURCE_PROVISIONING,
      "$.runtime_readiness.selection",
      "同頂層 selection 唔一致",
    );
    const baselineIntegrity = runtime.checks.find(
      (item) => item.key === "baseline_integrity",
    );
    /*
     * §7.3 is an equality, not an implication: `can_provision` is true exactly
     * when the permit is armed and the baseline package is verified. Checking
     * one direction would accept `false` beside an armed, verified body — a
     * combination the approved producer cannot emit. Immutable Owner
     * eligibility is gated by the producer and has no field here, so the
     * frontend never invents one.
     */
    const derivedCanProvision =
      authorization.state === "armed" && baselineIntegrity?.status === "ready";
    check(
      canProvision === derivedCanProvision,
      RESOURCE_PROVISIONING,
      "$.can_provision",
      "必須 exact 等於「armed 授權而且對照基準完整性 ready」",
    );
    return {
      schema: "paper_provisioning_readiness.v1",
      selection,
      can_provision: canProvision,
      authorization,
      runtime_readiness: runtime,
    };
  });
}

// --- §6.4 / §6.5 trader record ---------------------------------------------

const RESOURCE_CREATE = "paper-trader-create";
const RESOURCE_REQUEST = "paper-trader-request";
const RESOURCE_LIST = "paper-trader-list";
const RESOURCE_DETAIL = "paper-trader-detail";

export function buildPaperCreateRequest(
  requestId: string,
  selection: PaperSelection,
): PaperTraderCreateRequest {
  return {
    schema: "paper_trader_create_request.v1",
    request_id: requestId,
    selection: { ...selection },
  };
}

function parseReadinessSnapshot(
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
  check(
    object.schema === "paper_readiness_snapshot.v1",
    resource,
    `${path}.schema`,
    "唔係已批准嘅 schema",
  );
  const checkedAt = requireUtcInstant(
    object.checked_at,
    resource,
    `${path}.checked_at`,
  );
  const checks = parseReadinessChecks(
    object.checks,
    resource,
    `${path}.checks`,
    checkedAt,
  );
  const overall = requireEnum(
    object.overall,
    PAPER_OVERALL_STATES,
    resource,
    `${path}.overall`,
  );
  /*
   * Option A: a provision-only trader is saved with the truthful snapshot of
   * the moment, which may be `blocked` while IB, the calendar or Telegram are
   * unverified. `overall` still has to equal the derived value, so a snapshot
   * can never claim more than its four checks say.
   */
  requireConsistentOverall(overall, checks, resource, `${path}.overall`);
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

function parseTraderRecord(
  value: unknown,
  resource: string,
  path: string,
): PaperTrader {
  const object = requireObject(value, resource, path);
  requireExactKeys(
    object,
    [
      "schema",
      "trader_id",
      "request_id",
      "strategy",
      "contract_id",
      "baseline",
      "account",
      "safeguards",
      "lifecycle",
      "readiness_snapshot",
      "created_at",
    ],
    resource,
    path,
  );
  check(
    object.schema === "paper_trader.v1",
    resource,
    `${path}.schema`,
    "唔係已批准嘅 schema",
  );

  const strategy = requireObject(object.strategy, resource, `${path}.strategy`);
  requireExactKeys(
    strategy,
    ["strategy_id", "content_sha256"],
    resource,
    `${path}.strategy`,
  );

  const baseline = requireObject(object.baseline, resource, `${path}.baseline`);
  requireExactKeys(
    baseline,
    ["run_id", "result_sha256", "range_start", "range_end"],
    resource,
    `${path}.baseline`,
  );

  const account = requireObject(object.account, resource, `${path}.account`);
  requireExactKeys(
    account,
    ["account_id", "currency", "initial_capital"],
    resource,
    `${path}.account`,
  );
  const initialCapital = requireFiniteNumber(
    account.initial_capital,
    resource,
    `${path}.account.initial_capital`,
  );
  check(
    initialCapital > 0,
    resource,
    `${path}.account.initial_capital`,
    "獨立帳戶初始資金必須大過 0",
  );

  const safeguards = requireObject(
    object.safeguards,
    resource,
    `${path}.safeguards`,
  );
  requireExactKeys(
    safeguards,
    ["max_drawdown_r", "max_losing_streak", "blind_minutes"],
    resource,
    `${path}.safeguards`,
  );
  // Stage A safeguards are closed literals, not "any positive number".
  for (const key of [
    "max_drawdown_r",
    "max_losing_streak",
    "blind_minutes",
  ] as const) {
    const actual = requireFiniteNumber(
      safeguards[key],
      resource,
      `${path}.safeguards.${key}`,
    );
    check(
      actual === PAPER_STAGE_A_SAFEGUARDS[key],
      resource,
      `${path}.safeguards.${key}`,
      `Stage A 必須 exact 等於 ${PAPER_STAGE_A_SAFEGUARDS[key]}`,
    );
  }

  const lifecycle = requireObject(
    object.lifecycle,
    resource,
    `${path}.lifecycle`,
  );
  requireExactKeys(
    lifecycle,
    ["status", "reason", "as_of"],
    resource,
    `${path}.lifecycle`,
  );

  return {
    schema: "paper_trader.v1",
    trader_id: requirePattern(
      object.trader_id,
      TRADER_ID,
      resource,
      `${path}.trader_id`,
      "必須係 trader- 加 32 位小寫 hex",
    ),
    request_id: requirePattern(
      object.request_id,
      CANONICAL_UUID4,
      resource,
      `${path}.request_id`,
      "必須係 canonical 小寫 UUID4",
    ),
    strategy: {
      strategy_id: requireExactString(
        strategy.strategy_id,
        resource,
        `${path}.strategy.strategy_id`,
      ),
      content_sha256: requireSha256(
        strategy.content_sha256,
        resource,
        `${path}.strategy.content_sha256`,
      ),
    },
    contract_id: requireExactString(
      object.contract_id,
      resource,
      `${path}.contract_id`,
    ),
    baseline: {
      run_id: requireExactString(
        baseline.run_id,
        resource,
        `${path}.baseline.run_id`,
      ),
      result_sha256: requireSha256(
        baseline.result_sha256,
        resource,
        `${path}.baseline.result_sha256`,
      ),
      range_start: requireUtcInstant(
        baseline.range_start,
        resource,
        `${path}.baseline.range_start`,
      ),
      range_end: requireUtcInstant(
        baseline.range_end,
        resource,
        `${path}.baseline.range_end`,
      ),
    },
    account: {
      account_id: requirePattern(
        account.account_id,
        ACCOUNT_ID,
        resource,
        `${path}.account.account_id`,
        "必須係 paper-account- 加 32 位小寫 hex",
      ),
      currency: requireExactString(
        account.currency,
        resource,
        `${path}.account.currency`,
      ),
      initial_capital: initialCapital,
    },
    safeguards: { ...PAPER_STAGE_A_SAFEGUARDS },
    lifecycle: {
      status: requireEnum(
        lifecycle.status,
        PAPER_LIFECYCLE_STATUSES,
        resource,
        `${path}.lifecycle.status`,
      ),
      reason: requireLifecycleReason(
        lifecycle.reason,
        resource,
        `${path}.lifecycle.reason`,
      ),
      as_of: requireUtcInstant(
        lifecycle.as_of,
        resource,
        `${path}.lifecycle.as_of`,
      ),
    },
    readiness_snapshot: parseReadinessSnapshot(
      object.readiness_snapshot,
      resource,
      `${path}.readiness_snapshot`,
    ),
    created_at: requireUtcInstant(
      object.created_at,
      resource,
      `${path}.created_at`,
    ),
  };
}

/** Stage A declares one exact lifecycle reason; anything else is drift. */
function requireLifecycleReason(
  value: unknown,
  resource: string,
  path: string,
): PaperLifecycleReason {
  check(
    value === PAPER_STAGE_A_LIFECYCLE_REASON,
    resource,
    path,
    "唔係 Stage A exact lifecycle reason",
  );
  return PAPER_STAGE_A_LIFECYCLE_REASON;
}

function requireTraderMatchesRequest(
  trader: PaperTrader,
  request: PaperTraderCreateRequest,
  resource: string,
  path: string,
): void {
  check(
    trader.request_id === request.request_id,
    resource,
    `${path}.request_id`,
    "同今次建立請求身份唔一致",
  );
  check(
    trader.strategy.strategy_id === request.selection.strategy_id &&
      trader.strategy.content_sha256 === request.selection.content_sha256,
    resource,
    `${path}.strategy`,
    "同今次選擇嘅策略版本唔一致",
  );
  check(
    trader.contract_id === request.selection.contract_id,
    resource,
    `${path}.contract_id`,
    "同今次選擇嘅合約唔一致",
  );
  check(
    trader.baseline.run_id === request.selection.baseline_run_id &&
      trader.baseline.result_sha256 ===
        request.selection.baseline_result_sha256,
    resource,
    `${path}.baseline`,
    "同今次選擇嘅對照基準唔一致",
  );
}

export interface PaperCreateOutcome {
  trader: PaperTrader;
  /** true when the backend replayed an existing record (HTTP 200). */
  replay: boolean;
}

export function parsePaperCreatedTrader(
  response: PaperHttpResult,
  request: PaperTraderCreateRequest,
): PaperParseResult<PaperCreateOutcome> {
  return attempt(RESOURCE_CREATE, () => {
    const body = successBody(response, RESOURCE_CREATE, [201, 200]);
    const trader = parseTraderRecord(body, RESOURCE_CREATE, "$");
    requireTraderMatchesRequest(trader, request, RESOURCE_CREATE, "$");
    return { trader, replay: response.status === 200 };
  });
}

export function parsePaperTraderRequestStatus(
  response: PaperHttpResult,
  request: PaperTraderCreateRequest,
): PaperParseResult<PaperTraderRequestStatusRecord> {
  return attempt(RESOURCE_REQUEST, () => {
    const body = requireObject(
      successBody(response, RESOURCE_REQUEST, [200]),
      RESOURCE_REQUEST,
      "$",
    );
    requireExactKeys(
      body,
      ["schema", "request_id", "status", "trader"],
      RESOURCE_REQUEST,
      "$",
    );
    check(
      body.schema === "paper_trader_request_status.v1",
      RESOURCE_REQUEST,
      "$.schema",
      "唔係已批准嘅 schema",
    );
    const requestId = requirePattern(
      body.request_id,
      CANONICAL_UUID4,
      RESOURCE_REQUEST,
      "$.request_id",
      "必須係 canonical 小寫 UUID4",
    );
    check(
      requestId === request.request_id,
      RESOURCE_REQUEST,
      "$.request_id",
      "同今次建立請求身份唔一致",
    );
    const trader = parseTraderRecord(body.trader, RESOURCE_REQUEST, "$.trader");
    requireTraderMatchesRequest(trader, request, RESOURCE_REQUEST, "$.trader");
    return {
      schema: "paper_trader_request_status.v1",
      request_id: requestId,
      status: requireEnum(
        body.status,
        PAPER_REQUEST_STATUSES,
        RESOURCE_REQUEST,
        "$.status",
      ),
      trader,
    };
  });
}

export function parsePaperTraderList(
  response: PaperHttpResult,
): PaperParseResult<PaperTraderList> {
  return attempt(RESOURCE_LIST, () => {
    const body = requireObject(
      successBody(response, RESOURCE_LIST, [200]),
      RESOURCE_LIST,
      "$",
    );
    requireExactKeys(
      body,
      ["schema", "count", "traders"],
      RESOURCE_LIST,
      "$",
    );
    check(
      body.schema === "paper_trader_list.v1",
      RESOURCE_LIST,
      "$.schema",
      "唔係已批准嘅 schema",
    );
    const rows = requireArray(body.traders, RESOURCE_LIST, "$.traders");
    check(
      body.count === rows.length,
      RESOURCE_LIST,
      "$.count",
      `count 同 traders 長度唔一致（${String(body.count)} vs ${rows.length}）`,
    );
    const seen = new Set<string>();
    const traders = rows.map((row, index) => {
      const path = `$.traders[${index}]`;
      const trader = parseTraderRecord(row, RESOURCE_LIST, path);
      check(
        !seen.has(trader.trader_id),
        RESOURCE_LIST,
        `${path}.trader_id`,
        "重複交易員身份",
      );
      seen.add(trader.trader_id);
      return trader;
    });
    return { schema: "paper_trader_list.v1", count: body.count, traders };
  });
}

export function parsePaperTraderDetail(
  response: PaperHttpResult,
  expectedTraderId: string,
): PaperParseResult<PaperTrader> {
  return attempt(RESOURCE_DETAIL, () => {
    const trader = parseTraderRecord(
      successBody(response, RESOURCE_DETAIL, [200]),
      RESOURCE_DETAIL,
      "$",
    );
    check(
      trader.trader_id === expectedTraderId,
      RESOURCE_DETAIL,
      "$.trader_id",
      "同要求嘅交易員身份唔一致",
    );
    return trader;
  });
}

/** Deep equality used to prove tab / list / detail carry one same record. */
export function paperTraderEquals(left: PaperTrader, right: PaperTrader): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

// --- §7 error contract ------------------------------------------------------

const RESOURCE_ERROR = "paper-api-error";

export function parsePaperApiError(
  response: PaperHttpResult,
): PaperParseResult<PaperApiError> {
  return attempt(RESOURCE_ERROR, () => {
    check(
      response.ok === false,
      RESOURCE_ERROR,
      "$http.ok",
      "成功 response 唔可以當錯誤處理",
    );
    check(
      response.jsonParsed === true,
      RESOURCE_ERROR,
      "$http.body",
      "response 唔係有效 JSON",
    );
    const body = requireObject(response.body, RESOURCE_ERROR, "$");
    requireExactKeys(body, ["detail"], RESOURCE_ERROR, "$");
    const detail = requireObject(body.detail, RESOURCE_ERROR, "$.detail");
    requireExactKeys(
      detail,
      ["schema", "code", "message", "retryable"],
      RESOURCE_ERROR,
      "$.detail",
    );
    check(
      detail.schema === "paper_api_error.v1",
      RESOURCE_ERROR,
      "$.detail.schema",
      "唔係已批准嘅 schema",
    );
    const code = requireEnum(
      detail.code,
      Object.keys(PAPER_ERROR_STATUS) as PaperErrorCode[],
      RESOURCE_ERROR,
      "$.detail.code",
    );
    check(
      PAPER_ERROR_STATUS[code] === response.status,
      RESOURCE_ERROR,
      "$.detail.code",
      `已知錯誤碼出現喺唔對應嘅 HTTP ${String(response.status)}`,
    );
    return {
      schema: "paper_api_error.v1",
      code,
      message: requireExactString(
        detail.message,
        RESOURCE_ERROR,
        "$.detail.message",
      ),
      retryable: requireNonRetryable(
        detail.retryable,
        RESOURCE_ERROR,
        "$.detail.retryable",
      ),
    };
  });
}

/** `paper_api_error.v1` is always non-retryable; `true` is not a v1 state. */
function requireNonRetryable(
  value: unknown,
  resource: string,
  path: string,
): false {
  check(value === false, resource, path, "v1 業務錯誤必須 exact 係 false");
  return false;
}

/**
 * Owner-facing copy per known code. Raw codes and raw backend strings never
 * reach the screen (banned-vocabulary rule, p4 稿 constraint #16).
 */
const PAPER_ERROR_COPY: Record<PaperErrorCode, string> = {
  eligible_strategy_required:
    "後端暫時冇可用策略或對照基準，所以唔可以喺呢度建立交易員。",
  baseline_not_found:
    "搵唔到相符嘅對照基準。請確認揀咗同一個策略版本同合約，或者先完成一次相符嘅回測。",
  selection_identity_mismatch:
    "你揀嘅內容同系統核對唔上，已經停低。請重新揀策略版本、合約同對照基準。",
  baseline_integrity_failed:
    "對照基準嘅完整性核對唔通過，所以唔可以建立交易員。",
  readiness_blocked:
    "開始前檢查未全部通過，所以唔可以建立交易員。",
  activation_not_authorized: "模擬盤建立功能尚未啟用。",
  request_id_conflict:
    "同一個建立請求對應咗唔同內容，已經停低；系統唔會建立第二個交易員。",
  request_not_found: "系統確認今次請求未曾建立過任何交易員。",
  store_unavailable: "模擬盤記錄暫時讀寫唔到，所以唔可以建立交易員。",
  external_readiness_invalid:
    "實際啟動條件嘅回覆讀唔到，所以而家唔可以建立交易員。",
  provisioning_not_authorized:
    "而家未有建立授權，所以唔可以建立交易員。",
  provisioning_selection_mismatch:
    "你揀嘅內容唔喺今次建立授權範圍之內，所以唔可以建立交易員。",
  provisioning_request_conflict:
    "今次建立授權已經俾另一個請求用咗，所以唔會建立第二個交易員。",
};

export function paperErrorCopy(code: PaperErrorCode): string {
  return PAPER_ERROR_COPY[code];
}
