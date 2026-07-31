/**
 * P4 normal-mode wire contract.
 *
 * Everything in this module is runtime checked.  A TypeScript cast must never
 * be the reason an Owner can submit work, display progress, or re-run history.
 */

import type { AssumptionSnapshot } from "./types";

export type AdmissionStatus = "pass" | "warn" | "block" | "unknown";
export type LiveJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";
export type LiveBatchStatus =
  | LiveJobStatus
  | "partial";

export interface P4SlippageTicks {
  breakout_entry: number;
  stop_exit: number;
  target_exit: number;
  day_end_exit: number;
}

export interface P4ExecutionAssumptionsRequest {
  initial_capital_usd: number;
  commission_per_side_by_symbol: Record<string, number>;
  slippage_ticks: P4SlippageTicks;
}

export interface P4DuplicateAcknowledgement {
  strategy_version: string;
  symbol: string;
  range_start: string;
  range_end: string;
}

export interface P4StandardRequest {
  strategy_versions: string[];
  symbols: string[];
  range_start: string;
  range_end: string;
  execution_assumptions: P4ExecutionAssumptionsRequest;
  duplicate_acknowledgements: P4DuplicateAcknowledgement[];
}

export interface P4CoverageCheck {
  status: AdmissionStatus;
  requested_trading_date_count: number;
  admitted_trading_date_count: number;
  complete_trading_dates: string[];
  owner_trusted_problem_trading_dates: string[];
  owner_excluded_trading_dates: string[];
  roll_blackout_trading_dates: string[];
  excluded_trading_dates: string[];
  blocking_problem_trading_dates: string[];
  missing_native_daily_trading_dates: string[];
  reason_codes: string[];
}

export interface P4WarmupCheck {
  status: AdmissionStatus;
  required_prior_trading_date_count: number | null;
  available_prior_trading_date_count: number | null;
  evaluable_trading_date_count: number | null;
  first_evaluable_trading_date: string | null;
  suggested_range_start: string | null;
  reason_codes: string[];
}

export interface P4DuplicateCheck {
  status: AdmissionStatus;
  count_known: boolean;
  exact_match_count: number | null;
  prior_run_ids: string[];
  unindexed_candidate_count: number | null;
  acknowledged: boolean;
  reason_codes: string[];
}

export interface P4PrecheckUnit {
  strategy_version: string;
  symbol: string;
  contract_id: string;
  session_name: "eth" | "rth" | null;
  range_start: string;
  range_end: string;
  status: AdmissionStatus;
  reason_codes: string[];
  coverage: P4CoverageCheck;
  warmup: P4WarmupCheck;
  duplicate: P4DuplicateCheck;
}

export interface P4Precheck {
  schema: "backtest_precheck.v1";
  checked_at: string;
  overall_status: AdmissionStatus;
  can_submit: boolean;
  unit_count: number;
  units: P4PrecheckUnit[];
}

export interface P4JobProgress {
  current_trading_date: string | null;
  processed_trading_date_count: number;
  total_trading_date_count: number;
  trade_count: number;
  realized_net_pnl_usd: number;
  realized_net_r: number;
  reported_at: string;
}

export interface P4PublicAssumptions {
  initial_capital_usd: number;
  commission_per_side: number;
  slippage_ticks: P4SlippageTicks;
  target_requires_through: boolean;
  fill_model: "conservative";
  bar_precision: "1m";
}

export interface P4InternalAssumptions {
  initial_capital_usd: number;
  commission_per_side: number;
  slippage_ticks: P4SlippageTicks;
  target_requires_through: boolean;
  fill_model: "conservative";
  simulation_precision: "one_minute";
  quantity: number;
}

export interface P4BatchJob {
  job_id: string;
  run_id: string;
  symbol: string;
  strategy_version: string;
  status: LiveJobStatus;
  message: string;
  started_at: string | null;
  finished_at: string | null;
  result_path: string | null;
  strategy_source: string | null;
  warnings: string[];
  progress: P4JobProgress | null;
  error_summary: string | null;
  error_full: string | null;
  cancelled_at: string | null;
  assumptions: P4PublicAssumptions | null;
  execution_assumptions?: P4InternalAssumptions;
}

export interface P4BatchSummary {
  total: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export interface P4Batch {
  schema: "batch_job.v2";
  batch_id: string;
  status: LiveBatchStatus;
  created_at: string;
  updated_at: string;
  request: Record<string, unknown>;
  jobs: P4BatchJob[];
  summary: P4BatchSummary;
}

export interface P4BatchList {
  schema: "batch_job_list.v2";
  count: number;
  batches: P4Batch[];
}

const REQUEST_KEYS = [
  "strategy_versions",
  "symbols",
  "range_start",
  "range_end",
  "execution_assumptions",
  "duplicate_acknowledgements",
] as const;
const REQUEST_ASSUMPTION_KEYS = [
  "initial_capital_usd",
  "commission_per_side_by_symbol",
  "slippage_ticks",
] as const;
const SLIPPAGE_KEYS = [
  "breakout_entry",
  "stop_exit",
  "target_exit",
  "day_end_exit",
] as const;
const ACK_KEYS = [
  "strategy_version",
  "symbol",
  "range_start",
  "range_end",
] as const;
const PRECHECK_KEYS = [
  "schema",
  "checked_at",
  "overall_status",
  "can_submit",
  "unit_count",
  "units",
] as const;
const PRECHECK_UNIT_KEYS = [
  "strategy_version",
  "symbol",
  "contract_id",
  "session_name",
  "range_start",
  "range_end",
  "status",
  "reason_codes",
  "coverage",
  "warmup",
  "duplicate",
] as const;
const COVERAGE_KEYS = [
  "status",
  "requested_trading_date_count",
  "admitted_trading_date_count",
  "complete_trading_dates",
  "owner_trusted_problem_trading_dates",
  "owner_excluded_trading_dates",
  "roll_blackout_trading_dates",
  "excluded_trading_dates",
  "blocking_problem_trading_dates",
  "missing_native_daily_trading_dates",
  "reason_codes",
] as const;
const WARMUP_KEYS = [
  "status",
  "required_prior_trading_date_count",
  "available_prior_trading_date_count",
  "evaluable_trading_date_count",
  "first_evaluable_trading_date",
  "suggested_range_start",
  "reason_codes",
] as const;
const DUPLICATE_KEYS = [
  "status",
  "count_known",
  "exact_match_count",
  "prior_run_ids",
  "unindexed_candidate_count",
  "acknowledged",
  "reason_codes",
] as const;
const BATCH_KEYS = [
  "schema",
  "batch_id",
  "status",
  "created_at",
  "updated_at",
  "request",
  "jobs",
  "summary",
] as const;
const LIST_KEYS = ["schema", "count", "batches"] as const;
const JOB_REQUIRED_KEYS = [
  "job_id",
  "run_id",
  "symbol",
  "strategy_version",
  "status",
  "message",
  "started_at",
  "finished_at",
  "result_path",
  "strategy_source",
  "warnings",
  "progress",
  "error_summary",
  "error_full",
  "cancelled_at",
  "assumptions",
] as const;
const SUMMARY_KEYS = [
  "total",
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
] as const;
const PROGRESS_KEYS = [
  "current_trading_date",
  "processed_trading_date_count",
  "total_trading_date_count",
  "trade_count",
  "realized_net_pnl_usd",
  "realized_net_r",
  "reported_at",
] as const;
const PUBLIC_ASSUMPTION_KEYS = [
  "initial_capital_usd",
  "commission_per_side",
  "slippage_ticks",
  "target_requires_through",
  "fill_model",
  "bar_precision",
] as const;
const INTERNAL_ASSUMPTION_KEYS = [
  "initial_capital_usd",
  "commission_per_side",
  "slippage_ticks",
  "target_requires_through",
  "fill_model",
  "simulation_precision",
  "quantity",
] as const;

const STRATEGY_ID = /^strategy-[0-9]{4,}$/;
const SYMBOL = /^[A-Z][A-Z0-9]{0,15}$/;
const DATE_LABEL = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/;
const CANONICAL_UTC =
  /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{6}))?Z$/;
const ADMISSION_STATUSES = new Set<AdmissionStatus>([
  "pass",
  "warn",
  "block",
  "unknown",
]);
const JOB_STATUSES = new Set<LiveJobStatus>([
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
]);
const BATCH_STATUSES = new Set<LiveBatchStatus>([
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
  "partial",
]);
const COVERAGE_REASONS = new Set([
  "coverage_complete",
  "coverage_owner_trusted",
  "coverage_owner_excluded",
  "coverage_roll_blackout",
  "coverage_pending_problem",
  "coverage_minute_missing",
  "coverage_native_daily_missing",
  "coverage_all_dates_excluded",
  "coverage_unknown",
]);
const WARMUP_REASONS = new Set([
  "warmup_sufficient",
  "warmup_short",
  "warmup_unknown",
  "warmup_no_evaluable_dates",
]);
const DUPLICATE_REASONS = new Set([
  "duplicate_none",
  "duplicate_exact_match",
  "duplicate_acknowledged",
  "duplicate_acknowledgement_stale",
  "duplicate_index_unavailable",
  "duplicate_identity_unproven",
  "duplicate_catalog_integrity_error",
]);
const UNIT_ONLY_REASONS = new Set([
  "strategy_unavailable",
  "strategy_symbol_not_authorized",
]);
const ALL_REASONS = new Set([
  ...COVERAGE_REASONS,
  ...WARMUP_REASONS,
  ...DUPLICATE_REASONS,
  ...UNIT_ONLY_REASONS,
]);

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

function hasAllowedKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
): boolean {
  const keys = Object.keys(value);
  const allowed = new Set([...required, ...optional]);
  return (
    required.every((key) => Object.prototype.hasOwnProperty.call(value, key)) &&
    keys.every((key) => allowed.has(key))
  );
}

function isCanonicalString(value: unknown, allowEmpty = false): value is string {
  return (
    typeof value === "string" &&
    (allowEmpty || value.length > 0) &&
    (allowEmpty || value.trim() === value)
  );
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNonNegativeInt(value: unknown): value is number {
  return Number.isInteger(value) && (value as number) >= 0;
}

function isPositiveInt(value: unknown): value is number {
  return Number.isInteger(value) && (value as number) > 0;
}

export function isTradingDateLabel(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const match = DATE_LABEL.exec(value);
  if (!match) {
    return false;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

export function isCanonicalUtc(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const match = CANONICAL_UTC.exec(value);
  if (!match) {
    return false;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return false;
  }
  return (
    parsed.getUTCFullYear() === Number(match[1]) &&
    parsed.getUTCMonth() + 1 === Number(match[2]) &&
    parsed.getUTCDate() === Number(match[3]) &&
    parsed.getUTCHours() === Number(match[4]) &&
    parsed.getUTCMinutes() === Number(match[5]) &&
    parsed.getUTCSeconds() === Number(match[6])
  );
}

function stringArray(
  value: unknown,
  item: (entry: unknown) => entry is string,
  allowEmpty = true,
): string[] | null {
  if (
    !Array.isArray(value) ||
    (!allowEmpty && value.length === 0) ||
    !value.every(item)
  ) {
    return null;
  }
  const result = value as string[];
  return new Set(result).size === result.length ? [...result] : null;
}

function dateArray(value: unknown): string[] | null {
  const parsed = stringArray(value, isTradingDateLabel);
  if (!parsed) {
    return null;
  }
  return parsed.every((entry, index) => index === 0 || parsed[index - 1] < entry)
    ? parsed
    : null;
}

function reasonArray(
  value: unknown,
  allowed: ReadonlySet<string>,
  allowEmpty = true,
): string[] | null {
  const parsed = stringArray(
    value,
    (entry): entry is string =>
      typeof entry === "string" && allowed.has(entry),
    allowEmpty,
  );
  return parsed;
}

function parseSlippage(value: unknown): P4SlippageTicks | null {
  if (!isObject(value) || !hasExactKeys(value, SLIPPAGE_KEYS)) {
    return null;
  }
  for (const key of SLIPPAGE_KEYS) {
    if (!isNonNegativeInt(value[key])) {
      return null;
    }
  }
  return {
    breakout_entry: value.breakout_entry as number,
    stop_exit: value.stop_exit as number,
    target_exit: value.target_exit as number,
    day_end_exit: value.day_end_exit as number,
  };
}

function parseRequestAssumptions(
  value: unknown,
  symbols: readonly string[],
): P4ExecutionAssumptionsRequest | null {
  if (!isObject(value) || !hasExactKeys(value, REQUEST_ASSUMPTION_KEYS)) {
    return null;
  }
  if (
    !isFiniteNumber(value.initial_capital_usd) ||
    value.initial_capital_usd <= 0 ||
    !isObject(value.commission_per_side_by_symbol)
  ) {
    return null;
  }
  const commissions = value.commission_per_side_by_symbol;
  const commissionKeys = Object.keys(commissions);
  if (
    commissionKeys.length !== symbols.length ||
    !symbols.every((symbol) =>
      Object.prototype.hasOwnProperty.call(commissions, symbol),
    )
  ) {
    return null;
  }
  const checkedCommissions: Record<string, number> = {};
  for (const symbol of symbols) {
    const amount = commissions[symbol];
    if (!isFiniteNumber(amount) || amount < 0) {
      return null;
    }
    checkedCommissions[symbol] = amount;
  }
  const slippage = parseSlippage(value.slippage_ticks);
  if (!slippage) {
    return null;
  }
  return {
    initial_capital_usd: value.initial_capital_usd,
    commission_per_side_by_symbol: checkedCommissions,
    slippage_ticks: slippage,
  };
}

function parseAcknowledgement(
  value: unknown,
): P4DuplicateAcknowledgement | null {
  if (!isObject(value) || !hasExactKeys(value, ACK_KEYS)) {
    return null;
  }
  if (
    typeof value.strategy_version !== "string" ||
    !STRATEGY_ID.test(value.strategy_version) ||
    typeof value.symbol !== "string" ||
    !SYMBOL.test(value.symbol) ||
    !isCanonicalUtc(value.range_start) ||
    !isCanonicalUtc(value.range_end) ||
    value.range_start >= value.range_end
  ) {
    return null;
  }
  return {
    strategy_version: value.strategy_version,
    symbol: value.symbol,
    range_start: value.range_start,
    range_end: value.range_end,
  };
}

export function duplicateAcknowledgementKey(
  acknowledgement: P4DuplicateAcknowledgement,
): string {
  return [
    acknowledgement.strategy_version,
    acknowledgement.symbol,
    acknowledgement.range_start,
    acknowledgement.range_end,
  ].join("\u0000");
}

export function parseP4StandardRequest(
  value: unknown,
): P4StandardRequest | null {
  if (!isObject(value) || !hasExactKeys(value, REQUEST_KEYS)) {
    return null;
  }
  const strategyVersions = stringArray(
    value.strategy_versions,
    (entry): entry is string =>
      typeof entry === "string" && STRATEGY_ID.test(entry),
    false,
  );
  const symbols = stringArray(
    value.symbols,
    (entry): entry is string =>
      typeof entry === "string" && SYMBOL.test(entry),
    false,
  );
  if (
    !strategyVersions ||
    !symbols ||
    !isCanonicalUtc(value.range_start) ||
    !isCanonicalUtc(value.range_end) ||
    value.range_start >= value.range_end
  ) {
    return null;
  }
  const assumptions = parseRequestAssumptions(
    value.execution_assumptions,
    symbols,
  );
  if (!assumptions || !Array.isArray(value.duplicate_acknowledgements)) {
    return null;
  }
  const acknowledgements: P4DuplicateAcknowledgement[] = [];
  const matrix = new Set(
    strategyVersions.flatMap((strategy) =>
      symbols.map((symbol) =>
        [strategy, symbol, value.range_start, value.range_end].join("\u0000"),
      ),
    ),
  );
  const seen = new Set<string>();
  for (const raw of value.duplicate_acknowledgements) {
    const acknowledgement = parseAcknowledgement(raw);
    if (!acknowledgement) {
      return null;
    }
    const key = duplicateAcknowledgementKey(acknowledgement);
    if (seen.has(key) || !matrix.has(key)) {
      return null;
    }
    seen.add(key);
    acknowledgements.push(acknowledgement);
  }
  return {
    strategy_versions: strategyVersions,
    symbols,
    range_start: value.range_start,
    range_end: value.range_end,
    execution_assumptions: assumptions,
    duplicate_acknowledgements: acknowledgements,
  };
}

export function buildP4StandardRequest(input: {
  strategyVersions: readonly string[];
  symbols: readonly string[];
  rangeStartUtc: string;
  rangeEndUtc: string;
  assumptions: AssumptionSnapshot;
  duplicateAcknowledgements?: readonly P4DuplicateAcknowledgement[];
}): P4StandardRequest | null {
  const commissions: Record<string, number> = {};
  for (const symbol of input.symbols) {
    commissions[symbol] = input.assumptions.fees[symbol];
  }
  return parseP4StandardRequest({
    strategy_versions: [...input.strategyVersions],
    symbols: [...input.symbols],
    range_start: input.rangeStartUtc,
    range_end: input.rangeEndUtc,
    execution_assumptions: {
      initial_capital_usd: input.assumptions.initialCapital,
      commission_per_side_by_symbol: commissions,
      slippage_ticks: {
        breakout_entry: input.assumptions.slippageTicks.breakout,
        stop_exit: input.assumptions.slippageTicks.stop,
        target_exit: input.assumptions.slippageTicks.target,
        day_end_exit: input.assumptions.slippageTicks.dayEnd,
      },
    },
    duplicate_acknowledgements: [
      ...(input.duplicateAcknowledgements ?? []),
    ],
  });
}

function stableRequestIdentity(
  request: P4StandardRequest,
  includeAcknowledgements: boolean,
): string {
  const strategies = [...request.strategy_versions].sort();
  const symbols = [...request.symbols].sort();
  const commissions = symbols.map((symbol) => [
    symbol,
    request.execution_assumptions.commission_per_side_by_symbol[symbol],
  ]);
  const acknowledgements = includeAcknowledgements
    ? [...request.duplicate_acknowledgements]
        .map(duplicateAcknowledgementKey)
        .sort()
    : [];
  return JSON.stringify({
    strategies,
    symbols,
    rangeStart: request.range_start,
    rangeEnd: request.range_end,
    capital: request.execution_assumptions.initial_capital_usd,
    commissions,
    slippage: request.execution_assumptions.slippage_ticks,
    acknowledgements,
  });
}

/** Form identity: includes every editable assumption, but not a derived ack. */
export function p4FormIdentity(request: P4StandardRequest): string {
  return stableRequestIdentity(request, false);
}

/** Wire identity: distinguishes the recheck after an exact duplicate ack. */
export function p4RequestIdentity(request: P4StandardRequest): string {
  return stableRequestIdentity(request, true);
}

function parseAdmissionStatus(value: unknown): AdmissionStatus | null {
  return typeof value === "string" &&
    ADMISSION_STATUSES.has(value as AdmissionStatus)
    ? (value as AdmissionStatus)
    : null;
}

function aggregateAdmission(statuses: readonly AdmissionStatus[]): AdmissionStatus {
  if (statuses.includes("unknown")) {
    return "unknown";
  }
  if (statuses.includes("block")) {
    return "block";
  }
  if (statuses.includes("warn")) {
    return "warn";
  }
  return "pass";
}

function parseCoverage(value: unknown): P4CoverageCheck | null {
  if (!isObject(value) || !hasExactKeys(value, COVERAGE_KEYS)) {
    return null;
  }
  const status = parseAdmissionStatus(value.status);
  const complete = dateArray(value.complete_trading_dates);
  const trusted = dateArray(value.owner_trusted_problem_trading_dates);
  const ownerExcluded = dateArray(value.owner_excluded_trading_dates);
  const roll = dateArray(value.roll_blackout_trading_dates);
  const excluded = dateArray(value.excluded_trading_dates);
  const blocking = dateArray(value.blocking_problem_trading_dates);
  const missingNative = dateArray(value.missing_native_daily_trading_dates);
  const reasons = reasonArray(value.reason_codes, COVERAGE_REASONS, false);
  if (
    !status ||
    !isNonNegativeInt(value.requested_trading_date_count) ||
    !isNonNegativeInt(value.admitted_trading_date_count) ||
    !complete ||
    !trusted ||
    !ownerExcluded ||
    !roll ||
    !excluded ||
    !blocking ||
    !missingNative ||
    !reasons
  ) {
    return null;
  }
  const exactExcluded = [...new Set([...ownerExcluded, ...roll])].sort();
  const admittedDates = [...complete, ...trusted];
  const requestedDates = new Set([
    ...admittedDates,
    ...excluded,
    ...blocking,
  ]);
  const disjointPartition =
    requestedDates.size ===
    admittedDates.length + excluded.length + blocking.length;
  const reasonSet = new Set(reasons);
  const unknown = reasonSet.has("coverage_unknown");
  const expectedStatus: AdmissionStatus = unknown
    ? "unknown"
    : blocking.length > 0 || admittedDates.length === 0
      ? "block"
      : trusted.length > 0 || excluded.length > 0
        ? "warn"
        : "pass";
  if (
    value.admitted_trading_date_count !== complete.length + trusted.length ||
    value.requested_trading_date_count !== requestedDates.size ||
    JSON.stringify(excluded) !== JSON.stringify(exactExcluded) ||
    !disjointPartition ||
    !missingNative.every((date) => blocking.includes(date)) ||
    status !== expectedStatus ||
    (unknown &&
      (reasons.length !== 1 ||
        admittedDates.length !== 0 ||
        missingNative.length !== 0)) ||
    (!unknown &&
      (reasonSet.has("coverage_unknown") ||
        reasonSet.has("coverage_complete") !== (complete.length > 0) ||
        reasonSet.has("coverage_owner_trusted") !== (trusted.length > 0) ||
        reasonSet.has("coverage_owner_excluded") !==
          (ownerExcluded.length > 0) ||
        reasonSet.has("coverage_roll_blackout") !== (roll.length > 0) ||
        reasonSet.has("coverage_native_daily_missing") !==
          (missingNative.length > 0) ||
        reasonSet.has("coverage_all_dates_excluded") !==
          (admittedDates.length === 0 && blocking.length === 0) ||
        ((reasonSet.has("coverage_pending_problem") ||
          reasonSet.has("coverage_minute_missing")) !==
          blocking.some((date) => !missingNative.includes(date)))))
  ) {
    return null;
  }
  return {
    status,
    requested_trading_date_count: value.requested_trading_date_count,
    admitted_trading_date_count: value.admitted_trading_date_count,
    complete_trading_dates: complete,
    owner_trusted_problem_trading_dates: trusted,
    owner_excluded_trading_dates: ownerExcluded,
    roll_blackout_trading_dates: roll,
    excluded_trading_dates: excluded,
    blocking_problem_trading_dates: blocking,
    missing_native_daily_trading_dates: missingNative,
    reason_codes: reasons,
  };
}

function nullableNonNegativeInt(value: unknown): number | null | undefined {
  if (value === null) {
    return null;
  }
  return isNonNegativeInt(value) ? value : undefined;
}

function parseWarmup(value: unknown): P4WarmupCheck | null {
  if (!isObject(value) || !hasExactKeys(value, WARMUP_KEYS)) {
    return null;
  }
  const status = parseAdmissionStatus(value.status);
  const required = nullableNonNegativeInt(
    value.required_prior_trading_date_count,
  );
  const available = nullableNonNegativeInt(
    value.available_prior_trading_date_count,
  );
  const evaluable = nullableNonNegativeInt(value.evaluable_trading_date_count);
  const first =
    value.first_evaluable_trading_date === null
      ? null
      : isTradingDateLabel(value.first_evaluable_trading_date)
        ? value.first_evaluable_trading_date
        : undefined;
  const suggestion =
    value.suggested_range_start === null
      ? null
      : isCanonicalUtc(value.suggested_range_start)
        ? value.suggested_range_start
        : undefined;
  const reasons = reasonArray(value.reason_codes, WARMUP_REASONS, false);
  if (
    !status ||
    required === undefined ||
    available === undefined ||
    evaluable === undefined ||
    first === undefined ||
    suggestion === undefined ||
    !reasons
  ) {
    return null;
  }
  if ((evaluable === 0 || evaluable === null) && first !== null) {
    return null;
  }
  if (typeof evaluable === "number" && evaluable > 0 && first === null) {
    return null;
  }
  const exactReasons = (...expected: string[]) =>
    reasons.length === expected.length &&
    expected.every((reason, index) => reasons[index] === reason);
  const semanticMatch =
    status === "unknown"
      ? exactReasons("warmup_unknown") &&
        available === null &&
        evaluable === null &&
        first === null &&
        suggestion === null
      : status === "pass"
        ? exactReasons("warmup_sufficient") &&
          required !== null &&
          available !== null &&
          available >= required &&
          evaluable !== null &&
          evaluable > 0 &&
          first !== null &&
          suggestion === null
        : status === "warn" &&
          (exactReasons("warmup_short")
            ? required !== null &&
              available !== null &&
              available < required &&
              evaluable === 0 &&
              first === null
            : exactReasons(
                  "warmup_sufficient",
                  "warmup_no_evaluable_dates",
                ) &&
              required !== null &&
              available !== null &&
              available >= required &&
              evaluable === 0 &&
              first === null &&
              suggestion === null);
  if (!semanticMatch) {
    return null;
  }
  return {
    status,
    required_prior_trading_date_count: required,
    available_prior_trading_date_count: available,
    evaluable_trading_date_count: evaluable,
    first_evaluable_trading_date: first,
    suggested_range_start: suggestion,
    reason_codes: reasons,
  };
}

function parseDuplicate(value: unknown): P4DuplicateCheck | null {
  if (!isObject(value) || !hasExactKeys(value, DUPLICATE_KEYS)) {
    return null;
  }
  const status = parseAdmissionStatus(value.status);
  const exact = nullableNonNegativeInt(value.exact_match_count);
  const unindexed = nullableNonNegativeInt(value.unindexed_candidate_count);
  const runIds = stringArray(
    value.prior_run_ids,
    (entry): entry is string => isCanonicalString(entry),
  );
  const reasons = reasonArray(value.reason_codes, DUPLICATE_REASONS, false);
  if (
    !status ||
    typeof value.count_known !== "boolean" ||
    exact === undefined ||
    unindexed === undefined ||
    !runIds ||
    typeof value.acknowledged !== "boolean" ||
    !reasons
  ) {
    return null;
  }
  if (
    (exact !== null && exact !== runIds.length) ||
    (value.count_known &&
      (exact === null || unindexed !== 0))
  ) {
    return null;
  }
  const onlyReason = reasons.length === 1 ? reasons[0] : null;
  const semanticMatch =
    status === "unknown"
      ? !value.count_known &&
        [
          "duplicate_index_unavailable",
          "duplicate_identity_unproven",
          "duplicate_catalog_integrity_error",
        ].includes(onlyReason ?? "") &&
        (onlyReason === "duplicate_identity_unproven"
          ? unindexed !== null && unindexed > 0
          : exact === null &&
            runIds.length === 0 &&
            unindexed === null)
      : status === "pass"
        ? onlyReason === "duplicate_none" &&
          value.count_known &&
          exact === 0 &&
          runIds.length === 0 &&
          unindexed === 0 &&
          !value.acknowledged
        : status === "warn"
          ? onlyReason === "duplicate_acknowledged" &&
            value.count_known &&
            exact !== null &&
            exact > 0 &&
            unindexed === 0 &&
            value.acknowledged
          : status === "block" &&
            value.count_known &&
            unindexed === 0 &&
            (onlyReason === "duplicate_exact_match"
              ? exact !== null && exact > 0 && !value.acknowledged
              : onlyReason === "duplicate_acknowledgement_stale" &&
                exact === 0 &&
                runIds.length === 0 &&
                value.acknowledged);
  if (!semanticMatch) {
    return null;
  }
  return {
    status,
    count_known: value.count_known,
    exact_match_count: exact,
    prior_run_ids: runIds,
    unindexed_candidate_count: unindexed,
    acknowledged: value.acknowledged,
    reason_codes: reasons,
  };
}

function parsePrecheckUnit(value: unknown): P4PrecheckUnit | null {
  if (!isObject(value) || !hasExactKeys(value, PRECHECK_UNIT_KEYS)) {
    return null;
  }
  const status = parseAdmissionStatus(value.status);
  const sessionName =
    value.session_name === null ||
    value.session_name === "eth" ||
    value.session_name === "rth"
      ? value.session_name
      : undefined;
  const reasons = reasonArray(value.reason_codes, ALL_REASONS);
  const coverage = parseCoverage(value.coverage);
  const warmup = parseWarmup(value.warmup);
  const duplicate = parseDuplicate(value.duplicate);
  if (
    typeof value.strategy_version !== "string" ||
    !STRATEGY_ID.test(value.strategy_version) ||
    typeof value.symbol !== "string" ||
    !SYMBOL.test(value.symbol) ||
    !isCanonicalString(value.contract_id) ||
    sessionName === undefined ||
    !isCanonicalUtc(value.range_start) ||
    !isCanonicalUtc(value.range_end) ||
    (warmup !== null &&
      warmup.suggested_range_start !== null &&
      Date.parse(warmup.suggested_range_start) <=
        Date.parse(value.range_start as string)) ||
    !status ||
    !reasons ||
    !coverage ||
    !warmup ||
    !duplicate
  ) {
    return null;
  }
  const unitOnlyBlock = reasons.some((reason) => UNIT_ONLY_REASONS.has(reason));
  const expectedReasons = [
    ...reasons.filter((reason) => UNIT_ONLY_REASONS.has(reason)),
    ...coverage.reason_codes.filter((reason) => reason !== "coverage_complete"),
    ...warmup.reason_codes.filter((reason) => reason !== "warmup_sufficient"),
    ...duplicate.reason_codes.filter((reason) => reason !== "duplicate_none"),
  ].filter((reason, index, all) => all.indexOf(reason) === index);
  const expected = aggregateAdmission([
    coverage.status,
    warmup.status,
    duplicate.status,
    ...(unitOnlyBlock ? (["block"] as const) : []),
  ]);
  if (
    status !== expected ||
    (sessionName === null) !== reasons.includes("strategy_unavailable") ||
    JSON.stringify(reasons) !== JSON.stringify(expectedReasons)
  ) {
    return null;
  }
  return {
    strategy_version: value.strategy_version,
    symbol: value.symbol,
    contract_id: value.contract_id,
    session_name: sessionName,
    range_start: value.range_start,
    range_end: value.range_end,
    status,
    reason_codes: reasons,
    coverage,
    warmup,
    duplicate,
  };
}

export function parseP4Precheck(
  value: unknown,
  expectedRequest: P4StandardRequest,
): P4Precheck | null {
  if (!isObject(value) || !hasExactKeys(value, PRECHECK_KEYS)) {
    return null;
  }
  const overall = parseAdmissionStatus(value.overall_status);
  if (
    value.schema !== "backtest_precheck.v1" ||
    !isCanonicalUtc(value.checked_at) ||
    !overall ||
    typeof value.can_submit !== "boolean" ||
    !isNonNegativeInt(value.unit_count) ||
    !Array.isArray(value.units) ||
    value.unit_count !== value.units.length
  ) {
    return null;
  }
  const units: P4PrecheckUnit[] = [];
  for (const raw of value.units) {
    const unit = parsePrecheckUnit(raw);
    if (!unit) {
      return null;
    }
    units.push(unit);
  }
  const expectedCells = new Set(
    expectedRequest.strategy_versions.flatMap((strategy) =>
      expectedRequest.symbols.map((symbol) =>
        [strategy, symbol].join("\u0000"),
      ),
    ),
  );
  const seenCells = new Set<string>();
  const acknowledgementKeys = new Set(
    expectedRequest.duplicate_acknowledgements.map(
      duplicateAcknowledgementKey,
    ),
  );
  for (const unit of units) {
    const cell = [unit.strategy_version, unit.symbol].join("\u0000");
    const acknowledgement = duplicateAcknowledgementKey({
      strategy_version: unit.strategy_version,
      symbol: unit.symbol,
      range_start: unit.range_start,
      range_end: unit.range_end,
    });
    if (
      !expectedCells.has(cell) ||
      seenCells.has(cell) ||
      unit.range_start !== expectedRequest.range_start ||
      unit.range_end !== expectedRequest.range_end ||
      unit.duplicate.acknowledged !== acknowledgementKeys.has(acknowledgement)
    ) {
      return null;
    }
    seenCells.add(cell);
  }
  const calculated = aggregateAdmission(units.map((unit) => unit.status));
  if (
    seenCells.size !== expectedCells.size ||
    overall !== calculated ||
    value.can_submit !== (overall === "pass" || overall === "warn")
  ) {
    return null;
  }
  return {
    schema: "backtest_precheck.v1",
    checked_at: value.checked_at,
    overall_status: overall,
    can_submit: value.can_submit,
    unit_count: value.unit_count,
    units,
  };
}

export function exactDuplicateAcknowledgements(
  precheck: P4Precheck,
): P4DuplicateAcknowledgement[] {
  return precheck.units
    .filter(
      (unit) =>
        unit.duplicate.count_known &&
        (unit.duplicate.exact_match_count ?? 0) > 0,
    )
    .map((unit) => ({
      strategy_version: unit.strategy_version,
      symbol: unit.symbol,
      range_start: unit.range_start,
      range_end: unit.range_end,
    }));
}

function parseProgress(value: unknown): P4JobProgress | null {
  if (!isObject(value) || !hasExactKeys(value, PROGRESS_KEYS)) {
    return null;
  }
  const current =
    value.current_trading_date === null
      ? null
      : isTradingDateLabel(value.current_trading_date)
        ? value.current_trading_date
        : undefined;
  if (
    current === undefined ||
    !isNonNegativeInt(value.processed_trading_date_count) ||
    !isPositiveInt(value.total_trading_date_count) ||
    value.processed_trading_date_count > value.total_trading_date_count ||
    !isNonNegativeInt(value.trade_count) ||
    !isFiniteNumber(value.realized_net_pnl_usd) ||
    !isFiniteNumber(value.realized_net_r) ||
    !isCanonicalUtc(value.reported_at)
  ) {
    return null;
  }
  if (
    (current === null) !== (value.processed_trading_date_count === 0) ||
    (value.processed_trading_date_count === 0 &&
      (value.trade_count !== 0 ||
        value.realized_net_pnl_usd !== 0 ||
        value.realized_net_r !== 0))
  ) {
    return null;
  }
  return {
    current_trading_date: current,
    processed_trading_date_count: value.processed_trading_date_count,
    total_trading_date_count: value.total_trading_date_count,
    trade_count: value.trade_count,
    realized_net_pnl_usd: value.realized_net_pnl_usd,
    realized_net_r: value.realized_net_r,
    reported_at: value.reported_at,
  };
}

function parsePublicAssumptions(value: unknown): P4PublicAssumptions | null {
  if (!isObject(value) || !hasExactKeys(value, PUBLIC_ASSUMPTION_KEYS)) {
    return null;
  }
  const slippage = parseSlippage(value.slippage_ticks);
  if (
    !isFiniteNumber(value.initial_capital_usd) ||
    value.initial_capital_usd <= 0 ||
    !isFiniteNumber(value.commission_per_side) ||
    value.commission_per_side < 0 ||
    !slippage ||
    typeof value.target_requires_through !== "boolean" ||
    value.fill_model !== "conservative" ||
    value.bar_precision !== "1m"
  ) {
    return null;
  }
  return {
    initial_capital_usd: value.initial_capital_usd,
    commission_per_side: value.commission_per_side,
    slippage_ticks: slippage,
    target_requires_through: value.target_requires_through,
    fill_model: "conservative",
    bar_precision: "1m",
  };
}

function parseInternalAssumptions(value: unknown): P4InternalAssumptions | null {
  if (!isObject(value) || !hasExactKeys(value, INTERNAL_ASSUMPTION_KEYS)) {
    return null;
  }
  const slippage = parseSlippage(value.slippage_ticks);
  if (
    !isFiniteNumber(value.initial_capital_usd) ||
    value.initial_capital_usd <= 0 ||
    !isFiniteNumber(value.commission_per_side) ||
    value.commission_per_side < 0 ||
    !slippage ||
    typeof value.target_requires_through !== "boolean" ||
    value.fill_model !== "conservative" ||
    value.simulation_precision !== "one_minute" ||
    !isPositiveInt(value.quantity)
  ) {
    return null;
  }
  return {
    initial_capital_usd: value.initial_capital_usd,
    commission_per_side: value.commission_per_side,
    slippage_ticks: slippage,
    target_requires_through: value.target_requires_through,
    fill_model: "conservative",
    simulation_precision: "one_minute",
    quantity: value.quantity,
  };
}

function publicMatchesInternal(
  publicValue: P4PublicAssumptions,
  internal: P4InternalAssumptions,
): boolean {
  return (
    publicValue.initial_capital_usd === internal.initial_capital_usd &&
    publicValue.commission_per_side === internal.commission_per_side &&
    JSON.stringify(publicValue.slippage_ticks) ===
      JSON.stringify(internal.slippage_ticks) &&
    publicValue.target_requires_through === internal.target_requires_through
  );
}

function parseNullableCanonicalString(value: unknown): string | null | undefined {
  if (value === null) {
    return null;
  }
  return isCanonicalString(value) ? value : undefined;
}

function parseNullableUtc(value: unknown): string | null | undefined {
  if (value === null) {
    return null;
  }
  return isCanonicalUtc(value) ? value : undefined;
}

function parseJob(value: unknown): P4BatchJob | null {
  if (
    !isObject(value) ||
    !hasAllowedKeys(value, JOB_REQUIRED_KEYS, ["execution_assumptions"])
  ) {
    return null;
  }
  const status =
    typeof value.status === "string" &&
    JOB_STATUSES.has(value.status as LiveJobStatus)
      ? (value.status as LiveJobStatus)
      : null;
  const started = parseNullableUtc(value.started_at);
  const finished = parseNullableUtc(value.finished_at);
  const cancelled = parseNullableUtc(value.cancelled_at);
  const resultPath = parseNullableCanonicalString(value.result_path);
  const strategySource = parseNullableCanonicalString(value.strategy_source);
  const errorSummary = parseNullableCanonicalString(value.error_summary);
  const errorFull = parseNullableCanonicalString(value.error_full);
  const warnings = stringArray(
    value.warnings,
    (entry): entry is string => isCanonicalString(entry),
  );
  const progress =
    value.progress === null ? null : parseProgress(value.progress) ?? undefined;
  const publicAssumptions =
    value.assumptions === null
      ? null
      : parsePublicAssumptions(value.assumptions) ?? undefined;
  const hasInternal = Object.prototype.hasOwnProperty.call(
    value,
    "execution_assumptions",
  );
  const internal = hasInternal
    ? parseInternalAssumptions(value.execution_assumptions)
    : null;
  if (
    !isCanonicalString(value.job_id) ||
    !isCanonicalString(value.run_id) ||
    !isCanonicalString(value.symbol) ||
    !isCanonicalString(value.strategy_version) ||
    !status ||
    !isCanonicalString(value.message, true) ||
    started === undefined ||
    finished === undefined ||
    cancelled === undefined ||
    resultPath === undefined ||
    strategySource === undefined ||
    errorSummary === undefined ||
    errorFull === undefined ||
    !warnings ||
    progress === undefined ||
    publicAssumptions === undefined ||
    (hasInternal && !internal) ||
    (internal !== null &&
      (publicAssumptions === null ||
        !publicMatchesInternal(publicAssumptions, internal))) ||
    (internal === null && publicAssumptions !== null)
  ) {
    return null;
  }
  if (
    (status === "queued" &&
      [
        started,
        finished,
        progress,
        errorSummary,
        errorFull,
        cancelled,
      ].some((entry) => entry !== null)) ||
    (status === "running" &&
      (started === null ||
        finished !== null ||
        errorSummary !== null ||
        errorFull !== null ||
        cancelled !== null)) ||
    (status === "cancelled" &&
      (started !== null ||
        finished === null ||
        cancelled === null ||
        finished !== cancelled ||
        progress !== null ||
        errorSummary !== null ||
        errorFull !== null)) ||
    (status === "failed" &&
      (started === null ||
        finished === null ||
        cancelled !== null ||
        (errorSummary === null) !== (errorFull === null))) ||
    (status === "completed" &&
      (started === null ||
        finished === null ||
        cancelled !== null ||
        errorSummary !== null ||
        errorFull !== null ||
        (progress !== null &&
          progress.processed_trading_date_count !==
            progress.total_trading_date_count)))
  ) {
    return null;
  }
  return {
    job_id: value.job_id,
    run_id: value.run_id,
    symbol: value.symbol,
    strategy_version: value.strategy_version,
    status,
    message: value.message,
    started_at: started,
    finished_at: finished,
    result_path: resultPath,
    strategy_source: strategySource,
    warnings,
    progress,
    error_summary: errorSummary,
    error_full: errorFull,
    cancelled_at: cancelled,
    assumptions: publicAssumptions,
    ...(internal ? { execution_assumptions: internal } : {}),
  };
}

function reduceBatchStatus(jobs: readonly P4BatchJob[]): LiveBatchStatus | null {
  if (jobs.length === 0) {
    return null;
  }
  const statuses = new Set(jobs.map((job) => job.status));
  if (statuses.size === 1 && statuses.has("queued")) {
    return "queued";
  }
  if (statuses.has("queued") || statuses.has("running")) {
    return "running";
  }
  if (statuses.size === 1 && statuses.has("completed")) {
    return "completed";
  }
  if (statuses.size === 1 && statuses.has("failed")) {
    return "failed";
  }
  if (statuses.size === 1 && statuses.has("cancelled")) {
    return "cancelled";
  }
  return "partial";
}

function calculateSummary(jobs: readonly P4BatchJob[]): P4BatchSummary {
  const summary: P4BatchSummary = {
    total: jobs.length,
    queued: 0,
    running: 0,
    completed: 0,
    failed: 0,
    cancelled: 0,
  };
  for (const job of jobs) {
    summary[job.status] += 1;
  }
  return summary;
}

function sameSlippage(a: P4SlippageTicks, b: P4SlippageTicks): boolean {
  return SLIPPAGE_KEYS.every((key) => a[key] === b[key]);
}

function validateStandardJobs(
  jobs: readonly P4BatchJob[],
  request: P4StandardRequest,
): boolean {
  const expected = new Set(
    request.strategy_versions.flatMap((strategy) =>
      request.symbols.map((symbol) => [strategy, symbol].join("\u0000")),
    ),
  );
  const seen = new Set<string>();
  for (const job of jobs) {
    const cell = [job.strategy_version, job.symbol].join("\u0000");
    const internal = job.execution_assumptions;
    const requested = request.execution_assumptions;
    if (
      !expected.has(cell) ||
      seen.has(cell) ||
      !internal ||
      internal.initial_capital_usd !== requested.initial_capital_usd ||
      internal.commission_per_side !==
        requested.commission_per_side_by_symbol[job.symbol] ||
      !sameSlippage(internal.slippage_ticks, requested.slippage_ticks) ||
      ((job.status === "running" || job.status === "completed") &&
        job.progress === null) ||
      (job.status === "failed" &&
        (job.error_summary === null || job.error_full === null))
    ) {
      return false;
    }
    seen.add(cell);
  }
  return seen.size === expected.size;
}

function validateTimeline(
  batch: {
    created_at: string;
    updated_at: string;
    jobs: readonly P4BatchJob[];
  },
): boolean {
  const created = Date.parse(batch.created_at);
  const updated = Date.parse(batch.updated_at);
  if (updated < created) {
    return false;
  }
  for (const job of batch.jobs) {
    const started = job.started_at ? Date.parse(job.started_at) : null;
    const finished = job.finished_at ? Date.parse(job.finished_at) : null;
    const reported = job.progress ? Date.parse(job.progress.reported_at) : null;
    if (
      (started !== null && (started < created || started > updated)) ||
      (finished !== null && (finished < created || finished > updated)) ||
      (started !== null && finished !== null && finished < started) ||
      (reported !== null && (reported < created || reported > updated)) ||
      (started !== null && reported !== null && reported < started)
    ) {
      return false;
    }
  }
  return true;
}

export function parseP4Batch(
  value: unknown,
  expectedBatchId?: string,
): P4Batch | null {
  if (!isObject(value) || !hasExactKeys(value, BATCH_KEYS)) {
    return null;
  }
  const status =
    typeof value.status === "string" &&
    BATCH_STATUSES.has(value.status as LiveBatchStatus)
      ? (value.status as LiveBatchStatus)
      : null;
  if (
    value.schema !== "batch_job.v2" ||
    !isCanonicalString(value.batch_id) ||
    (expectedBatchId !== undefined && value.batch_id !== expectedBatchId) ||
    !status ||
    !isCanonicalUtc(value.created_at) ||
    !isCanonicalUtc(value.updated_at) ||
    !isObject(value.request) ||
    !Array.isArray(value.jobs) ||
    value.jobs.length === 0 ||
    !isObject(value.summary) ||
    !hasExactKeys(value.summary, SUMMARY_KEYS)
  ) {
    return null;
  }
  const jobs: P4BatchJob[] = [];
  for (const raw of value.jobs) {
    const job = parseJob(raw);
    if (!job) {
      return null;
    }
    jobs.push(job);
  }
  if (
    new Set(jobs.map((job) => job.job_id)).size !== jobs.length ||
    new Set(jobs.map((job) => job.run_id)).size !== jobs.length
  ) {
    return null;
  }
  const calculated = calculateSummary(jobs);
  for (const key of SUMMARY_KEYS) {
    if (!isNonNegativeInt(value.summary[key]) || value.summary[key] !== calculated[key]) {
      return null;
    }
  }
  const calculatedStatus = reduceBatchStatus(jobs);
  if (calculatedStatus !== status) {
    return null;
  }
  const standardRequest = parseP4StandardRequest(value.request);
  if (standardRequest && !validateStandardJobs(jobs, standardRequest)) {
    return null;
  }
  const batch: P4Batch = {
    schema: "batch_job.v2",
    batch_id: value.batch_id,
    status,
    created_at: value.created_at,
    updated_at: value.updated_at,
    request: value.request,
    jobs,
    summary: calculated,
  };
  return validateTimeline(batch) ? batch : null;
}

export function parseP4BatchList(value: unknown): P4BatchList | null {
  if (
    !isObject(value) ||
    !hasExactKeys(value, LIST_KEYS) ||
    value.schema !== "batch_job_list.v2" ||
    !isNonNegativeInt(value.count) ||
    !Array.isArray(value.batches) ||
    value.count !== value.batches.length
  ) {
    return null;
  }
  const batches: P4Batch[] = [];
  for (const raw of value.batches) {
    const batch = parseP4Batch(raw);
    if (!batch) {
      return null;
    }
    batches.push(batch);
  }
  if (new Set(batches.map((batch) => batch.batch_id)).size !== batches.length) {
    return null;
  }
  return {
    schema: "batch_job_list.v2",
    count: value.count,
    batches,
  };
}

export function isP4BatchTerminal(status: LiveBatchStatus): boolean {
  return ["completed", "failed", "cancelled", "partial"].includes(status);
}

function immutableJobIdentity(job: P4BatchJob): string {
  return JSON.stringify({
    jobId: job.job_id,
    runId: job.run_id,
    symbol: job.symbol,
    strategy: job.strategy_version,
    assumptions: job.assumptions,
    internal: job.execution_assumptions ?? null,
  });
}

const NEXT_JOB_STATUSES: Record<LiveJobStatus, ReadonlySet<LiveJobStatus>> = {
  queued: new Set([
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
  ]),
  running: new Set(["running", "completed", "failed"]),
  completed: new Set(["completed"]),
  failed: new Set(["failed"]),
  cancelled: new Set(["cancelled"]),
};

/**
 * Reject stale/regressive operational truth.  For a cancel response, every
 * non-queued cell must remain byte-for-byte unchanged.
 */
export function isMonotonicP4BatchUpdate(
  previous: P4Batch,
  next: P4Batch,
  source: "poll" | "cancel",
): boolean {
  if (
    previous.batch_id !== next.batch_id ||
    previous.created_at !== next.created_at ||
    JSON.stringify(previous.request) !== JSON.stringify(next.request) ||
    Date.parse(next.updated_at) < Date.parse(previous.updated_at) ||
    previous.jobs.length !== next.jobs.length
  ) {
    return false;
  }
  const nextById = new Map(next.jobs.map((job) => [job.job_id, job]));
  for (const oldJob of previous.jobs) {
    const newJob = nextById.get(oldJob.job_id);
    if (
      !newJob ||
      immutableJobIdentity(oldJob) !== immutableJobIdentity(newJob) ||
      !NEXT_JOB_STATUSES[oldJob.status].has(newJob.status)
    ) {
      return false;
    }
    if (source === "cancel") {
      if (
        (oldJob.status !== "queued" &&
          JSON.stringify(oldJob) !== JSON.stringify(newJob)) ||
        (oldJob.status === "queued" &&
          ((
            newJob.status !== "queued" &&
            newJob.status !== "cancelled"
          ) ||
            oldJob.result_path !== newJob.result_path ||
            oldJob.strategy_source !== newJob.strategy_source ||
            JSON.stringify(oldJob.warnings) !==
              JSON.stringify(newJob.warnings)))
      ) {
        return false;
      }
    }
    if (
      (oldJob.progress !== null && newJob.progress === null) ||
      (oldJob.progress !== null &&
        newJob.progress !== null &&
        (oldJob.progress.total_trading_date_count !==
          newJob.progress.total_trading_date_count ||
          newJob.progress.processed_trading_date_count <
            oldJob.progress.processed_trading_date_count ||
          newJob.progress.trade_count < oldJob.progress.trade_count ||
          (newJob.progress.processed_trading_date_count ===
            oldJob.progress.processed_trading_date_count &&
            JSON.stringify(newJob.progress) !==
              JSON.stringify(oldJob.progress)) ||
          (newJob.progress.processed_trading_date_count >
            oldJob.progress.processed_trading_date_count &&
            oldJob.progress.current_trading_date !== null &&
            newJob.progress.current_trading_date !== null &&
            newJob.progress.current_trading_date <=
              oldJob.progress.current_trading_date) ||
          Date.parse(newJob.progress.reported_at) <
            Date.parse(oldJob.progress.reported_at)))
    ) {
      return false;
    }
  }
  return true;
}

export function requestToAssumptionSnapshot(
  request: P4StandardRequest,
): AssumptionSnapshot {
  return {
    initialCapital: request.execution_assumptions.initial_capital_usd,
    fees: {
      ...request.execution_assumptions.commission_per_side_by_symbol,
    },
    slippageTicks: {
      breakout: request.execution_assumptions.slippage_ticks.breakout_entry,
      stop: request.execution_assumptions.slippage_ticks.stop_exit,
      target: request.execution_assumptions.slippage_ticks.target_exit,
      dayEnd: request.execution_assumptions.slippage_ticks.day_end_exit,
    },
  };
}
