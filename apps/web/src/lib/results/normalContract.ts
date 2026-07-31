import JSZip from "jszip";

import type { NarrativeStep } from "../../api/chartTypes";
import type {
  P5ExportHttpResult,
  P5HttpResult,
  P5PromotionDecision,
  P5PromotionDecisionRequest,
} from "../../api/client";
import {
  humanDetail,
  scorecardLabel,
  scorecardStatusLabel,
} from "./scorecardHuman";
import type {
  ChartPaneData,
  NearMiss,
  ResultDetail,
  ResultListItem,
  ScorecardRow,
  TradeCausal,
} from "./types";

export type P5ParseResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

export const P5_CHART_CACHE_STATES = [
  "memory",
  "sidecar",
  "write",
  "write_stale_fingerprint",
  "miss_no_write",
] as const;

export type P5ChartCacheState = (typeof P5_CHART_CACHE_STATES)[number];
export type P5ChartTimeframe = "D" | "1H" | "5m";

export function isP5GenerationCurrent(
  currentGeneration: number,
  expectedGeneration: number,
): boolean {
  return currentGeneration === expectedGeneration;
}

export const P5_EVIDENCE_JUDGMENT =
  "系統證據顯示：以上紀錄被列明條件截住。 " +
  "系統未提供「邏輯正常／定義有分歧」判斷，需 Owner 判斷。";

const COMPACT_METRIC_KEYS = [
  "trade_count",
  "gross_pnl",
  "net_pnl",
  "net_r",
  "win_rate",
  "profit_factor",
  "expectancy_r",
  "max_drawdown_pnl",
] as const;

const ENRICHED_METRIC_EXTRA_KEYS = [
  "max_drawdown_r",
  "payoff_ratio",
  "max_losing_streak",
  "dd_duration_trades",
  "calmar_r",
  "param_count",
  "trades_per_param",
  "rule_count",
  "skew",
  "kurtosis",
  "tail_ratio",
  "var95_r",
  "cvar95_r",
  "psr",
  "sharpe_per_trade",
  "profit_concentration",
  "cost_scenarios",
  "period_cuts",
] as const;

const ENRICHED_METRIC_KEYS = [
  ...COMPACT_METRIC_KEYS,
  ...ENRICHED_METRIC_EXTRA_KEYS,
] as const;

const RUN_LIST_ROW_KEYS = [
  "run_id",
  "strategy_version",
  "contract_id",
  "session_name",
  "range_start",
  "range_end",
  "validation_run",
  "trade_count",
  "net_r",
  "net_pnl",
  "win_rate",
  "profit_factor",
  "max_drawdown_pnl",
  "max_drawdown_r",
  "expectancy_r",
  "scorecard_statuses",
  "funnel_status",
  "funnel_fills",
  "has_scorecard",
  "has_funnel",
  "result_file",
] as const;

const TRADE_KEYS = [
  "trade_id",
  "contract_id",
  "direction",
  "signal_kind",
  "quantity",
  "signal_timestamp",
  "entry_timestamp",
  "entry_ts_init",
  "exit_timestamp",
  "exit_ts_init",
  "entry_reference",
  "entry_price",
  "stop_price",
  "target_price",
  "exit_price",
  "exit_reason",
  "gross_points",
  "gross_pnl",
  "total_commission",
  "net_pnl",
  "entry_slippage_ticks",
  "exit_slippage_ticks",
  "tags",
  "decision_evidence",
] as const;

const TRADE_TAG_KEYS = [
  "signal_kind",
  "daily_regime",
  "regime_strength",
  "entry_session",
  "entry_local_time",
  "entry_layers",
  "inside_count",
  "multiple_inside",
  "has_sweep_bonus",
  "lmr_step1_leg_atr",
  "lmr_step2_leg_atr",
  "atr_expansion_ratio",
  "mfe_r",
  "mae_r",
  "gap_through_target",
  "volatility_owner_view",
  "volatility_system_daily_atr_percentile",
  "volatility_system_range_ratio",
  "volatility_actual_daily_range",
] as const;

const EVENT_KEYS = [
  "sequence",
  "timestamp",
  "ts_init",
  "phase",
  "machine",
  "event_type",
  "from_state",
  "to_state",
  "direction",
  "price",
  "details",
] as const;

const REJECTION_KEYS = [
  "evidence_id",
  "timestamp",
  "ts_init",
  "trading_date",
  "direction",
  "evaluation_sequence",
  "reached_layers",
  "condition_facts",
  "blocking_condition_ids",
  "context",
  "source_event_sequences",
] as const;

const CONDITION_FACT_KEYS = [
  "condition_id",
  "layer_id",
  "observed_at",
  "status",
  "actual",
  "operator",
  "required",
  "unit",
  "source_sequences",
] as const;

const CHART_KEYS = [
  "schema",
  "run_id",
  "timeframe",
  "contract_id",
  "session_name",
  "data_fingerprint",
  "lookback_days",
  "visible_start",
  "visible_end",
  "candles",
  "ema18",
  "ema50",
  "ema90",
  "markers",
  "levels",
  "source",
  "sidecar_relpath",
  "cache",
] as const;

/** Phase C/F optional fields on chart_series.v1 (older sidecars omit them). */
const CHART_OPTIONAL_KEYS = ["compute_provenance"] as const;

const LAYER_DEPTH: Record<string, number> = {
  daily: 0,
  mid: 1,
  entry: 2,
  execution: 3,
};

class P5ContractError extends Error {
  readonly resource: string;
  readonly path: string;

  constructor(
    resource: string,
    path: string,
    message: string,
  ) {
    super(`${resource}:${path}: ${message}`);
    this.name = "P5ContractError";
    this.resource = resource;
    this.path = path;
  }
}

function attempt<T>(resource: string, parse: () => T): P5ParseResult<T> {
  try {
    return { ok: true, value: parse() };
  } catch (error) {
    if (error instanceof P5ContractError) {
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
    throw new P5ContractError(resource, path, message);
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value)
  ) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasOwn(
  object: Record<string, unknown>,
  key: string,
): boolean {
  return Object.prototype.hasOwnProperty.call(object, key);
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

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function nonNegativeInteger(value: unknown): value is number {
  return finiteNumber(value) && Number.isInteger(value) && value >= 0;
}

function positiveInteger(value: unknown): value is number {
  return finiteNumber(value) && Number.isInteger(value) && value > 0;
}

function nonBlankExactString(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value === value.trim()
  );
}

function nullableExactString(value: unknown): value is string | null {
  return value === null || nonBlankExactString(value);
}

function finiteOrNull(value: unknown): value is number | null {
  return value === null || finiteNumber(value);
}

function primitive(value: unknown): value is string | number | boolean | null {
  return (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    finiteNumber(value)
  );
}

function jsonValue(value: unknown): boolean {
  if (primitive(value)) {
    return true;
  }
  if (Array.isArray(value)) {
    return value.every(jsonValue);
  }
  if (isPlainObject(value)) {
    return Object.values(value).every(jsonValue);
  }
  return false;
}

function jsonEqual(left: unknown, right: unknown): boolean {
  if (left === right) {
    return true;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => jsonEqual(value, right[index]))
    );
  }
  if (isPlainObject(left) || isPlainObject(right)) {
    if (!isPlainObject(left) || !isPlainObject(right)) {
      return false;
    }
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return (
      leftKeys.length === rightKeys.length &&
      leftKeys.every(
        (key, index) =>
          key === rightKeys[index] && jsonEqual(left[key], right[key]),
      )
    );
  }
  return false;
}

function lowercaseSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

const OFFSET_INSTANT =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/;
const DATE_LABEL = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;

function timezoneInstant(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value === value.trim() &&
    OFFSET_INSTANT.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function realDateLabel(value: unknown): value is string {
  if (typeof value !== "string" || !DATE_LABEL.test(value)) {
    return false;
  }
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

function isEnvelope(value: unknown): value is P5HttpResult {
  return (
    isPlainObject(value) &&
    hasOwn(value, "path") &&
    hasOwn(value, "status") &&
    hasOwn(value, "ok") &&
    hasOwn(value, "rawText") &&
    hasOwn(value, "jsonParsed") &&
    hasOwn(value, "body")
  );
}

function bodyOf(input: unknown, resource: string): unknown {
  if (!isEnvelope(input)) {
    return input;
  }
  check(
    typeof input.path === "string",
    resource,
    "$http.path",
    "必須係字串",
  );
  check(
    input.status === 200 && input.ok === true,
    resource,
    "$http.status",
    `HTTP ${String(input.status)} 唔係成功response`,
  );
  check(
    input.jsonParsed === true,
    resource,
    "$http.body",
    "response唔係有效JSON",
  );
  return input.body;
}

function requireObject(
  value: unknown,
  resource: string,
  path: string,
): Record<string, unknown> {
  check(isPlainObject(value), resource, path, "必須係plain object");
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
    `key set唔一致（收到 ${Object.keys(object).sort().join(",")}）`,
  );
}

function requireKeysWithOptional(
  object: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
  resource: string,
  path: string,
): void {
  const actual = Object.keys(object);
  const requiredSet = new Set(required);
  const allowed = new Set([...required, ...optional]);
  for (const key of required) {
    check(key in object, resource, path, `缺少必需 key: ${key}`);
  }
  for (const key of actual) {
    check(
      allowed.has(key),
      resource,
      path,
      `唔允許嘅 key: ${key}（收到 ${actual.sort().join(",")}）`,
    );
  }
  // Keep length sanity: no duplicates (objects can't), required all present.
  check(
    actual.every((key) => allowed.has(key)) &&
      required.every((key) => requiredSet.has(key) && key in object),
    resource,
    path,
    `key set唔一致（收到 ${actual.sort().join(",")}）`,
  );
}

function requireUniqueStrings(
  value: unknown,
  resource: string,
  path: string,
  options: { nonEmpty?: boolean } = {},
): string[] {
  check(Array.isArray(value), resource, path, "必須係array");
  check(
    !options.nonEmpty || value.length > 0,
    resource,
    path,
    "唔可以係空array",
  );
  check(
    value.every(nonBlankExactString),
    resource,
    path,
    "每項必須係非空、無首尾空白字串",
  );
  const strings = value as string[];
  check(
    new Set(strings).size === strings.length,
    resource,
    path,
    "唔可以有duplicate",
  );
  return strings;
}

function requirePositiveSequenceArray(
  value: unknown,
  resource: string,
  path: string,
  nonEmpty: boolean,
): number[] {
  check(Array.isArray(value), resource, path, "必須係array");
  check(!nonEmpty || value.length > 0, resource, path, "唔可以係空array");
  check(
    value.every(positiveInteger),
    resource,
    path,
    "每項必須係正整數",
  );
  const numbers = value as number[];
  check(
    new Set(numbers).size === numbers.length,
    resource,
    path,
    "唔可以有duplicate sequence",
  );
  return numbers;
}

export interface P5RunRow {
  run_id: string;
  strategy_version: string | null;
  contract_id: string | null;
  session_name: string | null;
  range_start: string | null;
  range_end: string | null;
  validation_run: boolean;
  trade_count: number | null;
  net_r: number | null;
  net_pnl: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  max_drawdown_pnl: number | null;
  max_drawdown_r: number | null;
  expectancy_r: number | null;
  scorecard_statuses: Array<{ dim: string | null; status: string | null }>;
  funnel_status: string | null;
  funnel_fills: number | null;
  has_scorecard: boolean;
  has_funnel: boolean;
  result_file: string;
}

export interface P5RunList {
  schema: "run_list.v1";
  count: number;
  runs: P5RunRow[];
}

function parseRunRow(
  value: unknown,
  index: number,
  resource: string,
): P5RunRow {
  const path = `$.runs[${index}]`;
  const row = requireObject(value, resource, path);
  requireExactKeys(row, RUN_LIST_ROW_KEYS, resource, path);
  check(nonBlankExactString(row.run_id), resource, `${path}.run_id`, "無效");
  for (const key of [
    "strategy_version",
    "contract_id",
    "session_name",
    "funnel_status",
  ] as const) {
    check(
      nullableExactString(row[key]),
      resource,
      `${path}.${key}`,
      "必須係非空字串或null",
    );
  }
  for (const key of ["range_start", "range_end"] as const) {
    check(
      row[key] === null || timezoneInstant(row[key]),
      resource,
      `${path}.${key}`,
      "必須係有時區instant或null",
    );
  }
  check(
    typeof row.validation_run === "boolean",
    resource,
    `${path}.validation_run`,
    "必須係boolean",
  );
  for (const key of ["trade_count", "funnel_fills"] as const) {
    check(
      row[key] === null || nonNegativeInteger(row[key]),
      resource,
      `${path}.${key}`,
      "必須係非負整數或null",
    );
  }
  for (const key of [
    "net_r",
    "net_pnl",
    "win_rate",
    "profit_factor",
    "max_drawdown_pnl",
    "max_drawdown_r",
    "expectancy_r",
  ] as const) {
    check(
      finiteOrNull(row[key]),
      resource,
      `${path}.${key}`,
      "必須係finite number或null",
    );
  }
  check(
    typeof row.has_scorecard === "boolean",
    resource,
    `${path}.has_scorecard`,
    "必須係boolean",
  );
  check(
    typeof row.has_funnel === "boolean",
    resource,
    `${path}.has_funnel`,
    "必須係boolean",
  );
  check(
    nonBlankExactString(row.result_file),
    resource,
    `${path}.result_file`,
    "無效",
  );
  check(
    Array.isArray(row.scorecard_statuses),
    resource,
    `${path}.scorecard_statuses`,
    "必須係array",
  );
  const chips = row.scorecard_statuses.map((chip, chipIndex) => {
    const chipPath = `${path}.scorecard_statuses[${chipIndex}]`;
    const object = requireObject(chip, resource, chipPath);
    requireExactKeys(object, ["dim", "status"], resource, chipPath);
    check(
      object.dim === null || typeof object.dim === "string",
      resource,
      `${chipPath}.dim`,
      "必須係string或null",
    );
    check(
      object.status === null || typeof object.status === "string",
      resource,
      `${chipPath}.status`,
      "必須係string或null",
    );
    return {
      dim: object.dim as string | null,
      status: object.status as string | null,
    };
  });
  return {
    run_id: row.run_id,
    strategy_version: row.strategy_version as string | null,
    contract_id: row.contract_id as string | null,
    session_name: row.session_name as string | null,
    range_start: row.range_start as string | null,
    range_end: row.range_end as string | null,
    validation_run: row.validation_run,
    trade_count: row.trade_count as number | null,
    net_r: row.net_r as number | null,
    net_pnl: row.net_pnl as number | null,
    win_rate: row.win_rate as number | null,
    profit_factor: row.profit_factor as number | null,
    max_drawdown_pnl: row.max_drawdown_pnl as number | null,
    max_drawdown_r: row.max_drawdown_r as number | null,
    expectancy_r: row.expectancy_r as number | null,
    scorecard_statuses: chips,
    funnel_status: row.funnel_status as string | null,
    funnel_fills: row.funnel_fills as number | null,
    has_scorecard: row.has_scorecard,
    has_funnel: row.has_funnel,
    result_file: row.result_file,
  };
}

export function parseP5RunList(input: unknown): P5ParseResult<P5RunList> {
  return attempt("run-list", () => {
    const body = requireObject(bodyOf(input, "run-list"), "run-list", "$");
    requireExactKeys(body, ["schema", "count", "runs"], "run-list", "$");
    check(
      body.schema === "run_list.v1",
      "run-list",
      "$.schema",
      "必須係run_list.v1",
    );
    check(Array.isArray(body.runs), "run-list", "$.runs", "必須係array");
    check(
      nonNegativeInteger(body.count) && body.count === body.runs.length,
      "run-list",
      "$.count",
      "必須exact等於runs.length",
    );
    const runs = body.runs.map((row, index) =>
      parseRunRow(row, index, "run-list"),
    );
    const ids = runs.map((run) => run.run_id);
    check(
      new Set(ids).size === ids.length,
      "run-list",
      "$.runs",
      "run_id唔可以duplicate",
    );
    return { schema: "run_list.v1", count: runs.length, runs };
  });
}

export function mapP5RunListItem(
  run: P5RunRow,
  strategyLabel: string,
  unread: boolean,
): ResultListItem {
  const strategyVersion = run.strategy_version ?? "";
  const zero = run.trade_count === 0;
  const unknown = run.trade_count === null;
  return {
    runId: run.run_id,
    strategyVersion,
    strategyLabel: strategyLabel || strategyVersion,
    contractId: run.contract_id ?? "—",
    tradeCount: run.trade_count,
    winRate: run.win_rate,
    netR: run.net_r,
    netUsd: run.net_pnl,
    maxDrawdownUsd: run.max_drawdown_pnl,
    whyLine: zero
      ? "0 成交 · 點入去睇阻擋條件"
      : unknown
        ? "成交筆數未提供 · 點入去睇詳情"
        : `淨 ${run.net_r === null ? "未提供" : run.net_r.toFixed(2)}R · ${String(
            run.trade_count,
          )} 筆`,
    bottleneck: zero ? null : unknown ? "成交筆數未提供" : null,
    unread,
    rangeStart: run.range_start,
    rangeEnd: run.range_end,
    tradingDays: null,
  };
}

export interface P5Funnel {
  schema: "funnel.v1";
  status: "ok" | "insufficient_sample";
  daily_trend_days: number;
  evaluations_passing_daily_gate: number;
  evaluations_passing_mid_gate: number;
  signals_created: number;
  fills: number;
  reject_reasons: Record<string, number>;
  notes: string;
  units: Record<
    | "daily_trend_days"
    | "evaluations_passing_daily_gate"
    | "evaluations_passing_mid_gate"
    | "signals_created"
    | "fills",
    string
  >;
}

export interface P5MainMetrics {
  trade_count: number;
  gross_pnl: number;
  net_pnl: number;
  net_r: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy_r: number | null;
  max_drawdown_pnl: number;
  max_drawdown_r: number | null;
  raw: Record<string, unknown>;
}

export interface P5StrategyBindingOverride {
  path: string;
  spec_value: string;
  applied_value: string;
}

export interface P5StrategyBinding {
  schema: "strategy_binding.v1";
  source: "strategy_file";
  strategy_id: string;
  strategy_name: string | null;
  content_sha256: string;
  universe_contracts: string[];
  universe_authorized: boolean;
  overrides: P5StrategyBindingOverride[];
}

export interface P5ResultMain {
  profile: "legacy-compact" | "legacy-enriched" | "current-complete";
  runId: string;
  strategyVersion: string;
  contractId: string;
  sessionName: string;
  rangeStart: string;
  rangeEnd: string;
  tradingDays: number | null;
  metrics: P5MainMetrics;
  scorecard: ScorecardRow[];
  funnel: P5Funnel | null;
  warnings: string[];
  refs: { trades: string; equity: string; events: string };
  strategyBinding: P5StrategyBinding | null;
  rawScorecard: unknown[];
  raw: Record<string, unknown>;
}

function parseCountMap(
  value: unknown,
  resource: string,
  path: string,
): Record<string, number> {
  const object = requireObject(value, resource, path);
  const result: Record<string, number> = {};
  for (const [key, count] of Object.entries(object)) {
    check(nonBlankExactString(key), resource, path, "key無效");
    check(
      nonNegativeInteger(count),
      resource,
      `${path}.${key}`,
      "必須係非負整數",
    );
    result[key] = count;
  }
  return result;
}

function parseFunnel(
  value: unknown,
  resource: string,
  path: string,
): P5Funnel {
  const object = requireObject(value, resource, path);
  const countKeys = [
    "daily_trend_days",
    "evaluations_passing_daily_gate",
    "evaluations_passing_mid_gate",
    "signals_created",
    "fills",
  ] as const;
  requireExactKeys(
    object,
    [
      "schema",
      "status",
      ...countKeys,
      "reject_reasons",
      "notes",
      "units",
    ],
    resource,
    path,
  );
  check(object.schema === "funnel.v1", resource, `${path}.schema`, "無效");
  check(
    object.status === "ok" || object.status === "insufficient_sample",
    resource,
    `${path}.status`,
    "無效",
  );
  for (const key of countKeys) {
    check(
      nonNegativeInteger(object[key]),
      resource,
      `${path}.${key}`,
      "必須係非負整數",
    );
  }
  check(typeof object.notes === "string", resource, `${path}.notes`, "無效");
  const units = requireObject(object.units, resource, `${path}.units`);
  requireExactKeys(units, countKeys, resource, `${path}.units`);
  for (const key of countKeys) {
    check(
      nonBlankExactString(units[key]),
      resource,
      `${path}.units.${key}`,
      "必須係非空字串",
    );
  }
  return {
    schema: "funnel.v1",
    status: object.status,
    daily_trend_days: object.daily_trend_days as number,
    evaluations_passing_daily_gate:
      object.evaluations_passing_daily_gate as number,
    evaluations_passing_mid_gate:
      object.evaluations_passing_mid_gate as number,
    signals_created: object.signals_created as number,
    fills: object.fills as number,
    reject_reasons: parseCountMap(
      object.reject_reasons,
      resource,
      `${path}.reject_reasons`,
    ),
    notes: object.notes,
    units: units as P5Funnel["units"],
  };
}

function parseMetrics(
  value: unknown,
  resource: string,
  path: string,
): P5MainMetrics {
  const metrics = requireObject(value, resource, path);
  const compact = exactKeys(metrics, COMPACT_METRIC_KEYS);
  const enriched = exactKeys(metrics, ENRICHED_METRIC_KEYS);
  check(
    compact || enriched,
    resource,
    path,
    "只接受compact-8或enriched-26 exact key set",
  );
  check(
    nonNegativeInteger(metrics.trade_count),
    resource,
    `${path}.trade_count`,
    "必須係非負整數",
  );
  for (const key of [
    "gross_pnl",
    "net_pnl",
    "net_r",
    "max_drawdown_pnl",
  ] as const) {
    check(finiteNumber(metrics[key]), resource, `${path}.${key}`, "必須finite");
  }
  for (const key of ["win_rate", "profit_factor", "expectancy_r"] as const) {
    check(
      finiteOrNull(metrics[key]),
      resource,
      `${path}.${key}`,
      "必須finite或null",
    );
  }
  if (enriched) {
    for (const key of ["max_losing_streak", "dd_duration_trades"] as const) {
      check(
        nonNegativeInteger(metrics[key]) &&
          (metrics[key] as number) <= (metrics.trade_count as number),
        resource,
        `${path}.${key}`,
        "必須係不大過trade_count嘅非負整數",
      );
    }
    for (const key of ["param_count", "rule_count"] as const) {
      check(
        positiveInteger(metrics[key]),
        resource,
        `${path}.${key}`,
        "必須係正整數",
      );
    }
    for (const key of [
      "max_drawdown_r",
      "payoff_ratio",
      "calmar_r",
      "trades_per_param",
      "skew",
      "kurtosis",
      "tail_ratio",
      "var95_r",
      "cvar95_r",
      "psr",
      "sharpe_per_trade",
    ] as const) {
      check(
        finiteOrNull(metrics[key]),
        resource,
        `${path}.${key}`,
        "必須finite或null",
      );
    }
    check(
      metrics.trade_count === 0
        ? metrics.trades_per_param === null
        : finiteNumber(metrics.trades_per_param),
      resource,
      `${path}.trades_per_param`,
      "同trade_count唔一致",
    );
    const concentration = requireObject(
      metrics.profit_concentration,
      resource,
      `${path}.profit_concentration`,
    );
    requireExactKeys(
      concentration,
      ["top5_removed_net_r", "top10_removed_net_r"],
      resource,
      `${path}.profit_concentration`,
    );
    for (const key of [
      "top5_removed_net_r",
      "top10_removed_net_r",
    ] as const) {
      check(
        finiteOrNull(concentration[key]),
        resource,
        `${path}.profit_concentration.${key}`,
        "必須finite或null",
      );
    }
    const scenarios = requireObject(
      metrics.cost_scenarios,
      resource,
      `${path}.cost_scenarios`,
    );
    requireExactKeys(
      scenarios,
      ["x1", "x1.5", "x2"],
      resource,
      `${path}.cost_scenarios`,
    );
    for (const [name, multiplier] of [
      ["x1", 1],
      ["x1.5", 1.5],
      ["x2", 2],
    ] as const) {
      const scenario = requireObject(
        scenarios[name],
        resource,
        `${path}.cost_scenarios.${name}`,
      );
      requireExactKeys(
        scenario,
        ["multiplier", "net_pnl", "net_r", "expectancy_r", "trade_count"],
        resource,
        `${path}.cost_scenarios.${name}`,
      );
      check(
        scenario.multiplier === multiplier,
        resource,
        `${path}.cost_scenarios.${name}.multiplier`,
        "無效",
      );
      check(
        finiteNumber(scenario.net_pnl) && finiteNumber(scenario.net_r),
        resource,
        `${path}.cost_scenarios.${name}`,
        "PnL/R必須finite",
      );
      check(
        scenario.trade_count === metrics.trade_count,
        resource,
        `${path}.cost_scenarios.${name}.trade_count`,
        "同top-level唔一致",
      );
      check(
        metrics.trade_count === 0
          ? scenario.expectancy_r === null
          : finiteNumber(scenario.expectancy_r),
        resource,
        `${path}.cost_scenarios.${name}.expectancy_r`,
        "同trade_count唔一致",
      );
    }
    const x1 = scenarios.x1 as Record<string, unknown>;
    check(
      x1.net_pnl === metrics.net_pnl &&
        x1.net_r === metrics.net_r &&
        x1.expectancy_r === metrics.expectancy_r,
      resource,
      `${path}.cost_scenarios.x1`,
      "必須同top-level compact metrics一致",
    );
    parsePeriodCuts(
      metrics.period_cuts,
      metrics.trade_count as number,
      resource,
      `${path}.period_cuts`,
    );
  }
  return {
    trade_count: metrics.trade_count as number,
    gross_pnl: metrics.gross_pnl as number,
    net_pnl: metrics.net_pnl as number,
    net_r: metrics.net_r as number,
    win_rate: metrics.win_rate as number | null,
    profit_factor: metrics.profit_factor as number | null,
    expectancy_r: metrics.expectancy_r as number | null,
    max_drawdown_pnl: metrics.max_drawdown_pnl as number,
    max_drawdown_r: enriched
      ? (metrics.max_drawdown_r as number | null)
      : null,
    raw: metrics,
  };
}

function parsePeriodCuts(
  value: unknown,
  tradeCount: number,
  resource: string,
  path: string,
): void {
  const cuts = requireObject(value, resource, path);
  requireExactKeys(cuts, ["by_year", "by_month"], resource, path);
  for (const [bucketName, pattern] of [
    ["by_year", /^(?:[1-9]\d{3})$/],
    ["by_month", /^(?:[1-9]\d{3})-(?:0[1-9]|1[0-2])$/],
  ] as const) {
    const buckets = requireObject(cuts[bucketName], resource, `${path}.${bucketName}`);
    let total = 0;
    for (const [label, rawBucket] of Object.entries(buckets)) {
      check(pattern.test(label), resource, `${path}.${bucketName}.${label}`, "label無效");
      const bucket = requireObject(
        rawBucket,
        resource,
        `${path}.${bucketName}.${label}`,
      );
      requireExactKeys(
        bucket,
        ["trade_count", "net_r", "net_pnl", "expectancy_r"],
        resource,
        `${path}.${bucketName}.${label}`,
      );
      check(
        positiveInteger(bucket.trade_count),
        resource,
        `${path}.${bucketName}.${label}.trade_count`,
        "必須係正整數",
      );
      check(
        finiteNumber(bucket.net_r) &&
          finiteNumber(bucket.net_pnl) &&
          finiteNumber(bucket.expectancy_r),
        resource,
        `${path}.${bucketName}.${label}`,
        "metric必須finite",
      );
      total += bucket.trade_count;
    }
    check(
      total === tradeCount,
      resource,
      `${path}.${bucketName}`,
      "bucket trade_count總和唔一致",
    );
  }
}

function parseScorecard(
  value: unknown,
  resource: string,
  path: string,
): ScorecardRow[] {
  check(Array.isArray(value), resource, path, "必須係array");
  const seen = new Set<string>();
  return value.map((item, index) => {
    const itemPath = `${path}[${index}]`;
    const object = requireObject(item, resource, itemPath);
    requireExactKeys(object, ["dim", "status", "detail"], resource, itemPath);
    check(nonBlankExactString(object.dim), resource, `${itemPath}.dim`, "無效");
    check(
      !seen.has(object.dim),
      resource,
      `${itemPath}.dim`,
      "duplicate",
    );
    seen.add(object.dim);
    check(
      nonBlankExactString(object.status),
      resource,
      `${itemPath}.status`,
      "無效",
    );
    const detail = requireObject(object.detail, resource, `${itemPath}.detail`);
    check(jsonValue(detail), resource, `${itemPath}.detail`, "唔係finite JSON");
    return {
      dim: object.dim,
      label: scorecardLabel(object.dim),
      status: object.status,
      statusLabel: scorecardStatusLabel(object.status),
      detail: humanDetail(detail),
    };
  });
}

function parseStrategyBinding(
  value: unknown,
  expectedStrategyId: string,
): P5StrategyBinding {
  const resource = "result-main";
  const path = "$.run.manifest.strategy_binding";
  const object = requireObject(value, resource, path);
  requireExactKeys(
    object,
    [
      "schema",
      "source",
      "strategy_id",
      "strategy_name",
      "content_sha256",
      "universe_contracts",
      "universe_authorized",
      "overrides",
    ],
    resource,
    path,
  );
  check(
    object.schema === "strategy_binding.v1",
    resource,
    `${path}.schema`,
    "無效",
  );
  check(
    object.source === "strategy_file",
    resource,
    `${path}.source`,
    "必須係 strategy_file",
  );
  check(
    nonBlankExactString(object.strategy_id) &&
      object.strategy_id === expectedStrategyId,
    resource,
    `${path}.strategy_id`,
    "同 run strategy version 唔一致",
  );
  check(
    object.strategy_name === null ||
      typeof object.strategy_name === "string",
    resource,
    `${path}.strategy_name`,
    "必須係字串或 null",
  );
  check(
    lowercaseSha256(object.content_sha256),
    resource,
    `${path}.content_sha256`,
    "必須係 lowercase SHA-256",
  );
  check(
    Array.isArray(object.universe_contracts) &&
      object.universe_contracts.every(
        (contract) => typeof contract === "string",
      ),
    resource,
    `${path}.universe_contracts`,
    "必須係 string array",
  );
  const universeContracts = [
    ...(object.universe_contracts as string[]),
  ];
  check(
    typeof object.universe_authorized === "boolean",
    resource,
    `${path}.universe_authorized`,
    "必須係 boolean",
  );
  check(
    object.universe_authorized === true,
    resource,
    `${path}.universe_authorized`,
    "normal standard result 必須已獲 universe 授權",
  );
  check(
    Array.isArray(object.overrides),
    resource,
    `${path}.overrides`,
    "必須係 array",
  );
  const overrides = object.overrides.map((item, index) => {
    const itemPath = `${path}.overrides[${index}]`;
    const override = requireObject(item, resource, itemPath);
    requireExactKeys(
      override,
      ["path", "spec_value", "applied_value"],
      resource,
      itemPath,
    );
    for (const key of ["path", "spec_value", "applied_value"] as const) {
      check(
        nonBlankExactString(override[key]),
        resource,
        `${itemPath}.${key}`,
        "必須係非空、無首尾空白字串",
      );
    }
    return {
      path: override.path as string,
      spec_value: override.spec_value as string,
      applied_value: override.applied_value as string,
    };
  });
  check(
    overrides.length === 0,
    resource,
    `${path}.overrides`,
    "normal standard result 唔接受 override",
  );
  return {
    schema: "strategy_binding.v1",
    source: "strategy_file",
    strategy_id: object.strategy_id,
    strategy_name: object.strategy_name as string | null,
    content_sha256: object.content_sha256,
    universe_contracts: universeContracts,
    universe_authorized: true,
    overrides,
  };
}

export function parseP5ResultMain(
  input: unknown,
  expectedRunId: string,
): P5ParseResult<P5ResultMain> {
  return attempt("result-main", () => {
    const body = requireObject(bodyOf(input, "result-main"), "result-main", "$");
    const baseKeys = [
      "schema",
      "run",
      "metrics",
      "scorecard",
      "warnings",
      "owner_action",
      "trades_ref",
      "equity_curve_ref",
      "events_ref",
    ] as const;
    let profile: P5ResultMain["profile"];
    if (exactKeys(body, baseKeys)) {
      profile = "legacy-compact";
    } else if (exactKeys(body, [...baseKeys, "funnel"])) {
      profile = "legacy-enriched";
    } else if (
      exactKeys(body, [...baseKeys, "funnel", "decision_evidence_complete"])
    ) {
      profile = "current-complete";
    } else {
      throw new P5ContractError(
        "result-main",
        "$",
        "top-level唔係三個approved exact profile",
      );
    }
    check(body.schema === "result.v1", "result-main", "$.schema", "無效");
    if (profile === "current-complete") {
      check(
        body.decision_evidence_complete === true,
        "result-main",
        "$.decision_evidence_complete",
        "必須exact true",
      );
    }
    const run = requireObject(body.run, "result-main", "$.run");
    requireExactKeys(
      run,
      ["run_id", "strategy_version", "manifest", "engine"],
      "result-main",
      "$.run",
    );
    check(
      nonBlankExactString(run.run_id) && run.run_id === expectedRunId,
      "result-main",
      "$.run.run_id",
      "同route run id唔一致",
    );
    check(
      nonBlankExactString(run.strategy_version),
      "result-main",
      "$.run.strategy_version",
      "無效",
    );
    const manifest = requireObject(
      run.manifest,
      "result-main",
      "$.run.manifest",
    );
    for (const key of [
      "schema",
      "run_id",
      "strategy_version",
      "contract_id",
      "session_name",
      "range_start",
      "range_end",
    ]) {
      check(
        hasOwn(manifest, key),
        "result-main",
        `$.run.manifest.${key}`,
        "missing",
      );
    }
    check(
      manifest.schema === "run_manifest.v1",
      "result-main",
      "$.run.manifest.schema",
      "無效",
    );
    check(
      manifest.run_id === run.run_id,
      "result-main",
      "$.run.manifest.run_id",
      "同main run id唔一致",
    );
    check(
      manifest.strategy_version === run.strategy_version,
      "result-main",
      "$.run.manifest.strategy_version",
      "同main strategy version唔一致",
    );
    const strategyBinding =
      profile === "current-complete"
        ? parseStrategyBinding(
            manifest.strategy_binding,
            run.strategy_version,
          )
        : null;
    check(
      nonBlankExactString(manifest.contract_id),
      "result-main",
      "$.run.manifest.contract_id",
      "無效",
    );
    check(
      nonBlankExactString(manifest.session_name),
      "result-main",
      "$.run.manifest.session_name",
      "無效",
    );
    check(
      timezoneInstant(manifest.range_start),
      "result-main",
      "$.run.manifest.range_start",
      "必須係有時區instant",
    );
    check(
      timezoneInstant(manifest.range_end) &&
        Date.parse(manifest.range_end) > Date.parse(manifest.range_start),
      "result-main",
      "$.run.manifest.range_end",
      "必須晚過range_start",
    );
    if (hasOwn(manifest, "validation_run")) {
      check(
        typeof manifest.validation_run === "boolean",
        "result-main",
        "$.run.manifest.validation_run",
        "必須係boolean",
      );
      check(
        manifest.validation_run === false,
        "result-main",
        "$.run.manifest.validation_run",
        "工程驗證結果唔可以喺normal詳情顯示",
      );
    }
    let tradingDays: number | null = null;
    if (hasOwn(manifest, "trading_days")) {
      check(
        manifest.trading_days === null ||
          nonNegativeInteger(manifest.trading_days),
        "result-main",
        "$.run.manifest.trading_days",
        "必須係非負整數或null",
      );
      tradingDays = manifest.trading_days as number | null;
    }
    const engine = requireObject(run.engine, "result-main", "$.run.engine");
    check(jsonValue(engine), "result-main", "$.run.engine", "唔係finite JSON");
    const metrics = parseMetrics(body.metrics, "result-main", "$.metrics");
    const scorecard = parseScorecard(
      body.scorecard,
      "result-main",
      "$.scorecard",
    );
    const rawScorecard = structuredClone(body.scorecard as unknown[]);
    check(
      Array.isArray(body.warnings) &&
        body.warnings.every((warning) => typeof warning === "string"),
      "result-main",
      "$.warnings",
      "必須係string array",
    );
    check(
      jsonValue(body.owner_action),
      "result-main",
      "$.owner_action",
      "唔係finite JSON",
    );
    for (const key of [
      "trades_ref",
      "equity_curve_ref",
      "events_ref",
    ] as const) {
      check(
        nonBlankExactString(body[key]),
        "result-main",
        `$.${key}`,
        "無效",
      );
    }
    const funnel =
      profile === "legacy-compact"
        ? null
        : parseFunnel(body.funnel, "result-main", "$.funnel");
    if (funnel) {
      check(
        funnel.fills === metrics.trade_count,
        "result-main",
        "$.funnel.fills",
        "同metrics.trade_count唔一致",
      );
    }
    return {
      profile,
      runId: run.run_id,
      strategyVersion: run.strategy_version,
      contractId: manifest.contract_id,
      sessionName: manifest.session_name,
      rangeStart: manifest.range_start,
      rangeEnd: manifest.range_end,
      tradingDays,
      metrics,
      scorecard,
      funnel,
      warnings: body.warnings as string[],
      refs: {
        trades: body.trades_ref as string,
        equity: body.equity_curve_ref as string,
        events: body.events_ref as string,
      },
      strategyBinding,
      rawScorecard,
      raw: body,
    };
  });
}

function formatLocalInstant(value: string): string {
  const date = new Date(value);
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(date);
  } catch {
    return value;
  }
}

export function mapP5ResultDetail(
  main: P5ResultMain,
  strategyLabel: string,
): ResultDetail {
  return {
    runId: main.runId,
    strategyVersion: main.strategyVersion,
    strategyLabel: strategyLabel || main.strategyVersion,
    contractId: main.contractId,
    rangeStartLocal: formatLocalInstant(main.rangeStart),
    rangeEndLocal: formatLocalInstant(main.rangeEnd),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "本地時區",
    tradingDays: main.tradingDays,
    tradeCount: main.metrics.trade_count,
    winRate: main.metrics.win_rate,
    netR: main.metrics.net_r,
    netUsd: main.metrics.net_pnl,
    maxDrawdownUsd: main.metrics.max_drawdown_pnl,
    scorecard: main.scorecard,
    funnel: main.funnel
      ? {
          dailyPass: main.funnel.daily_trend_days,
          dailyTotal: null,
          evalPass: main.funnel.evaluations_passing_mid_gate,
          fills: main.funnel.fills,
          judgment: P5_EVIDENCE_JUDGMENT,
        }
      : null,
    nearMisses: [],
    trades: [],
    charts: [],
    warnings: main.warnings,
    whyZero: null,
  };
}

export interface P5ConditionFact {
  condition_id: string;
  layer_id: string;
  observed_at: string;
  status: "passed" | "failed" | "not_evaluated";
  actual: string | number | boolean | null;
  operator: string;
  required: string | number | boolean | null;
  unit: string;
  source_sequences: number[];
}

interface P5EntryEvidence {
  signal_kind: string;
  signal_timestamp: string;
  entry_timestamp: string;
  condition_facts: P5ConditionFact[];
  entry_reference: number;
  fill_price: number;
}

interface P5StopEvidence {
  reference_type: string;
  reference_price: number;
  offset_ticks: number;
  final_stop_price: number;
  condition_facts: P5ConditionFact[];
}

interface P5ExitCandidate {
  reason: string;
  timestamp: string;
  price: number;
  source_sequences: number[];
}

interface P5ExitEvidence {
  actual_reason: string;
  trigger_timestamp: string;
  trigger_price: number;
  candidates: P5ExitCandidate[];
  selected_candidate: string;
  resolution: string;
}

interface P5ConservativeAssumption {
  code: string;
  applied: boolean;
  effects: string[];
  source_sequences: number[];
}

export interface P5TradeWire {
  trade_id: string;
  contract_id: string;
  direction: "long" | "short";
  signal_kind: string;
  quantity: number;
  signal_timestamp: string;
  entry_timestamp: string;
  entry_ts_init: string;
  exit_timestamp: string;
  exit_ts_init: string;
  entry_reference: number;
  entry_price: number;
  stop_price: number;
  target_price: number;
  exit_price: number;
  exit_reason: string;
  gross_points: number;
  gross_pnl: number;
  total_commission: number;
  net_pnl: number;
  entry_slippage_ticks: number;
  exit_slippage_ticks: number;
  tags: Record<string, unknown>;
  decision_evidence: {
    trade_id: string;
    ordinal: number;
    entry: P5EntryEvidence;
    stop: P5StopEvidence;
    exit: P5ExitEvidence;
    conservative_assumptions: P5ConservativeAssumption[];
  };
}

export type P5Trades =
  | { kind: "complete"; trades: P5TradeWire[] }
  | { kind: "unavailable"; trades: [] };

function parseConditionFact(
  value: unknown,
  resource: string,
  path: string,
  eventSequences?: ReadonlySet<number>,
): P5ConditionFact {
  const fact = requireObject(value, resource, path);
  requireExactKeys(fact, CONDITION_FACT_KEYS, resource, path);
  check(
    nonBlankExactString(fact.condition_id),
    resource,
    `${path}.condition_id`,
    "無效",
  );
  check(
    nonBlankExactString(fact.layer_id),
    resource,
    `${path}.layer_id`,
    "無效",
  );
  check(
    timezoneInstant(fact.observed_at),
    resource,
    `${path}.observed_at`,
    "無效",
  );
  check(
    fact.status === "passed" ||
      fact.status === "failed" ||
      fact.status === "not_evaluated",
    resource,
    `${path}.status`,
    "無效",
  );
  check(primitive(fact.actual), resource, `${path}.actual`, "無效primitive");
  check(
    nonBlankExactString(fact.operator),
    resource,
    `${path}.operator`,
    "無效",
  );
  check(
    primitive(fact.required),
    resource,
    `${path}.required`,
    "無效primitive",
  );
  check(typeof fact.unit === "string", resource, `${path}.unit`, "無效");
  const sequences = requirePositiveSequenceArray(
    fact.source_sequences,
    resource,
    `${path}.source_sequences`,
    fact.status !== "not_evaluated",
  );
  check(
    fact.status !== "not_evaluated" || sequences.length === 0,
    resource,
    `${path}.source_sequences`,
    "not_evaluated必須係空",
  );
  if (eventSequences) {
    check(
      sequences.every((sequence) => eventSequences.has(sequence)),
      resource,
      `${path}.source_sequences`,
      "引用不存在event sequence",
    );
  }
  return {
    condition_id: fact.condition_id,
    layer_id: fact.layer_id,
    observed_at: fact.observed_at,
    status: fact.status,
    actual: fact.actual,
    operator: fact.operator,
    required: fact.required,
    unit: fact.unit,
    source_sequences: sequences,
  };
}

function parseConditionFacts(
  value: unknown,
  resource: string,
  path: string,
  eventSequences?: ReadonlySet<number>,
): P5ConditionFact[] {
  check(
    Array.isArray(value) && value.length > 0,
    resource,
    path,
    "必須係非空array",
  );
  const facts = value.map((fact, index) =>
    parseConditionFact(
      fact,
      resource,
      `${path}[${index}]`,
      eventSequences,
    ),
  );
  const ids = facts.map((fact) => fact.condition_id);
  check(new Set(ids).size === ids.length, resource, path, "condition id duplicate");
  return facts;
}

function parseEntryEvidence(
  value: unknown,
  resource: string,
  path: string,
): P5EntryEvidence {
  const entry = requireObject(value, resource, path);
  requireExactKeys(
    entry,
    [
      "signal_kind",
      "signal_timestamp",
      "entry_timestamp",
      "condition_facts",
      "entry_reference",
      "fill_price",
    ],
    resource,
    path,
  );
  check(nonBlankExactString(entry.signal_kind), resource, `${path}.signal_kind`, "無效");
  check(timezoneInstant(entry.signal_timestamp), resource, `${path}.signal_timestamp`, "無效");
  check(timezoneInstant(entry.entry_timestamp), resource, `${path}.entry_timestamp`, "無效");
  check(finiteNumber(entry.entry_reference), resource, `${path}.entry_reference`, "無效");
  check(finiteNumber(entry.fill_price), resource, `${path}.fill_price`, "無效");
  return {
    signal_kind: entry.signal_kind,
    signal_timestamp: entry.signal_timestamp,
    entry_timestamp: entry.entry_timestamp,
    condition_facts: parseConditionFacts(
      entry.condition_facts,
      resource,
      `${path}.condition_facts`,
    ),
    entry_reference: entry.entry_reference,
    fill_price: entry.fill_price,
  };
}

function parseStopEvidence(
  value: unknown,
  resource: string,
  path: string,
): P5StopEvidence {
  const stop = requireObject(value, resource, path);
  requireExactKeys(
    stop,
    [
      "reference_type",
      "reference_price",
      "offset_ticks",
      "final_stop_price",
      "condition_facts",
    ],
    resource,
    path,
  );
  check(nonBlankExactString(stop.reference_type), resource, `${path}.reference_type`, "無效");
  for (const key of ["reference_price", "offset_ticks", "final_stop_price"] as const) {
    check(finiteNumber(stop[key]), resource, `${path}.${key}`, "必須finite");
  }
  return {
    reference_type: stop.reference_type,
    reference_price: stop.reference_price as number,
    offset_ticks: stop.offset_ticks as number,
    final_stop_price: stop.final_stop_price as number,
    condition_facts: parseConditionFacts(
      stop.condition_facts,
      resource,
      `${path}.condition_facts`,
    ),
  };
}

function parseExitEvidence(
  value: unknown,
  resource: string,
  path: string,
): P5ExitEvidence {
  const exit = requireObject(value, resource, path);
  requireExactKeys(
    exit,
    [
      "actual_reason",
      "trigger_timestamp",
      "trigger_price",
      "candidates",
      "selected_candidate",
      "resolution",
    ],
    resource,
    path,
  );
  for (const key of ["actual_reason", "selected_candidate", "resolution"] as const) {
    check(nonBlankExactString(exit[key]), resource, `${path}.${key}`, "無效");
  }
  check(timezoneInstant(exit.trigger_timestamp), resource, `${path}.trigger_timestamp`, "無效");
  check(finiteNumber(exit.trigger_price), resource, `${path}.trigger_price`, "無效");
  check(Array.isArray(exit.candidates) && exit.candidates.length > 0, resource, `${path}.candidates`, "必須係非空array");
  const candidates = exit.candidates.map((candidate, index) => {
    const candidatePath = `${path}.candidates[${index}]`;
    const object = requireObject(candidate, resource, candidatePath);
    requireExactKeys(
      object,
      ["reason", "timestamp", "price", "source_sequences"],
      resource,
      candidatePath,
    );
    check(nonBlankExactString(object.reason), resource, `${candidatePath}.reason`, "無效");
    check(timezoneInstant(object.timestamp), resource, `${candidatePath}.timestamp`, "無效");
    check(finiteNumber(object.price), resource, `${candidatePath}.price`, "無效");
    return {
      reason: object.reason,
      timestamp: object.timestamp,
      price: object.price,
      source_sequences: requirePositiveSequenceArray(
        object.source_sequences,
        resource,
        `${candidatePath}.source_sequences`,
        true,
      ),
    };
  });
  check(
    candidates.some((candidate) => candidate.reason === exit.selected_candidate),
    resource,
    `${path}.selected_candidate`,
    "必須指向一個candidate reason",
  );
  return {
    actual_reason: exit.actual_reason as string,
    trigger_timestamp: exit.trigger_timestamp as string,
    trigger_price: exit.trigger_price,
    candidates,
    selected_candidate: exit.selected_candidate as string,
    resolution: exit.resolution as string,
  };
}

function parseAssumptions(
  value: unknown,
  resource: string,
  path: string,
): P5ConservativeAssumption[] {
  check(Array.isArray(value), resource, path, "必須係array");
  const seen = new Set<string>();
  return value.map((assumption, index) => {
    const itemPath = `${path}[${index}]`;
    const object = requireObject(assumption, resource, itemPath);
    requireExactKeys(
      object,
      ["code", "applied", "effects", "source_sequences"],
      resource,
      itemPath,
    );
    check(nonBlankExactString(object.code), resource, `${itemPath}.code`, "無效");
    check(!seen.has(object.code), resource, `${itemPath}.code`, "duplicate");
    seen.add(object.code);
    check(typeof object.applied === "boolean", resource, `${itemPath}.applied`, "無效");
    const effects = requireUniqueStrings(
      object.effects,
      resource,
      `${itemPath}.effects`,
    );
    return {
      code: object.code,
      applied: object.applied,
      effects,
      source_sequences: requirePositiveSequenceArray(
        object.source_sequences,
        resource,
        `${itemPath}.source_sequences`,
        object.applied,
      ),
    };
  });
}

function parseTrade(
  value: unknown,
  index: number,
  expectedContractId: string,
): P5TradeWire {
  const resource = "trades";
  const path = `$.trades[${index}]`;
  const trade = requireObject(value, resource, path);
  requireExactKeys(trade, TRADE_KEYS, resource, path);
  check(nonBlankExactString(trade.trade_id), resource, `${path}.trade_id`, "無效");
  check(
    trade.contract_id === expectedContractId,
    resource,
    `${path}.contract_id`,
    "同validated main contract唔一致",
  );
  check(
    trade.direction === "long" || trade.direction === "short",
    resource,
    `${path}.direction`,
    "無效",
  );
  check(nonBlankExactString(trade.signal_kind), resource, `${path}.signal_kind`, "無效");
  check(finiteNumber(trade.quantity) && trade.quantity > 0, resource, `${path}.quantity`, "必須係正finite number");
  for (const key of [
    "signal_timestamp",
    "entry_timestamp",
    "entry_ts_init",
    "exit_timestamp",
    "exit_ts_init",
  ] as const) {
    check(timezoneInstant(trade[key]), resource, `${path}.${key}`, "無效instant");
  }
  check(
    Date.parse(trade.entry_ts_init as string) >=
      Date.parse(trade.entry_timestamp as string),
    resource,
    `${path}.entry_ts_init`,
    "早過entry_timestamp",
  );
  check(
    Date.parse(trade.exit_ts_init as string) >=
      Date.parse(trade.exit_timestamp as string),
    resource,
    `${path}.exit_ts_init`,
    "早過exit_timestamp",
  );
  for (const key of [
    "entry_reference",
    "entry_price",
    "stop_price",
    "target_price",
    "exit_price",
    "gross_points",
    "gross_pnl",
    "total_commission",
    "net_pnl",
    "entry_slippage_ticks",
    "exit_slippage_ticks",
  ] as const) {
    check(finiteNumber(trade[key]), resource, `${path}.${key}`, "必須finite");
  }
  check(nonBlankExactString(trade.exit_reason), resource, `${path}.exit_reason`, "無效");
  const tags = requireObject(trade.tags, resource, `${path}.tags`);
  requireExactKeys(tags, TRADE_TAG_KEYS, resource, `${path}.tags`);
  check(jsonValue(tags), resource, `${path}.tags`, "唔係finite JSON");
  const evidence = requireObject(
    trade.decision_evidence,
    resource,
    `${path}.decision_evidence`,
  );
  requireExactKeys(
    evidence,
    [
      "trade_id",
      "ordinal",
      "entry",
      "stop",
      "exit",
      "conservative_assumptions",
    ],
    resource,
    `${path}.decision_evidence`,
  );
  check(
    evidence.trade_id === trade.trade_id,
    resource,
    `${path}.decision_evidence.trade_id`,
    "同execution trade_id唔一致",
  );
  check(
    positiveInteger(evidence.ordinal),
    resource,
    `${path}.decision_evidence.ordinal`,
    "必須係正整數",
  );
  const entry = parseEntryEvidence(
    evidence.entry,
    resource,
    `${path}.decision_evidence.entry`,
  );
  const stop = parseStopEvidence(
    evidence.stop,
    resource,
    `${path}.decision_evidence.stop`,
  );
  const exit = parseExitEvidence(
    evidence.exit,
    resource,
    `${path}.decision_evidence.exit`,
  );
  check(
    entry.signal_kind === trade.signal_kind &&
      entry.signal_timestamp === trade.signal_timestamp &&
      entry.entry_timestamp === trade.entry_timestamp &&
      entry.entry_reference === trade.entry_reference &&
      entry.fill_price === trade.entry_price,
    resource,
    `${path}.decision_evidence.entry`,
    "同execution identity/value唔一致",
  );
  check(
    stop.final_stop_price === trade.stop_price,
    resource,
    `${path}.decision_evidence.stop.final_stop_price`,
    "同execution stop_price唔一致",
  );
  check(
    exit.actual_reason === trade.exit_reason &&
      exit.trigger_timestamp === trade.exit_timestamp &&
      exit.trigger_price === trade.exit_price,
    resource,
    `${path}.decision_evidence.exit`,
    "同execution exit唔一致",
  );
  return {
    ...(trade as unknown as Omit<P5TradeWire, "decision_evidence">),
    decision_evidence: {
      trade_id: evidence.trade_id as string,
      ordinal: evidence.ordinal as number,
      entry,
      stop,
      exit,
      conservative_assumptions: parseAssumptions(
        evidence.conservative_assumptions,
        resource,
        `${path}.decision_evidence.conservative_assumptions`,
      ),
    },
  };
}

export function parseP5Trades(
  input: unknown,
  expectedRunId: string,
  expectedContractId: string,
  expectedTradeCount: number,
): P5ParseResult<P5Trades> {
  return attempt("trades", () => {
    const body = requireObject(bodyOf(input, "trades"), "trades", "$");
    check(body.schema === "trades.v1", "trades", "$.schema", "無效");
    check(
      body.run_id === expectedRunId,
      "trades",
      "$.run_id",
      "同route run id唔一致",
    );
    check(Array.isArray(body.trades), "trades", "$.trades", "必須係array");
    if (
      exactKeys(body, [
        "schema",
        "run_id",
        "trades",
        "decision_evidence_complete",
        "decision_evidence_availability",
      ])
    ) {
      check(
        body.decision_evidence_complete === false &&
          body.decision_evidence_availability === "unavailable",
        "trades",
        "$.decision_evidence_complete",
        "legacy unavailable marker無效",
      );
      check(
        body.trades.every(
          (trade) => isPlainObject(trade) && jsonValue(trade),
        ),
        "trades",
        "$.trades",
        "legacy rows唔係finite JSON object",
      );
      return { kind: "unavailable", trades: [] };
    }
    requireExactKeys(
      body,
      ["schema", "run_id", "trades", "decision_evidence_complete"],
      "trades",
      "$",
    );
    check(
      body.decision_evidence_complete === true,
      "trades",
      "$.decision_evidence_complete",
      "complete profile必須exact true",
    );
    check(
      body.trades.length === expectedTradeCount,
      "trades",
      "$.trades",
      "同main trade_count唔一致",
    );
    const trades = body.trades.map((trade, index) =>
      parseTrade(trade, index, expectedContractId),
    );
    const tradeIds = trades.map((trade) => trade.trade_id);
    const ordinals = trades.map(
      (trade) => trade.decision_evidence.ordinal,
    );
    check(
      new Set(tradeIds).size === tradeIds.length,
      "trades",
      "$.trades",
      "trade_id duplicate",
    );
    check(
      new Set(ordinals).size === ordinals.length &&
        [...ordinals].sort((a, b) => a - b).every(
          (ordinal, index) => ordinal === index + 1,
        ),
      "trades",
      "$.trades",
      "ordinal必須unique且由1連續",
    );
    return { kind: "complete", trades };
  });
}

function displayPrimitive(value: string | number | boolean | null): string {
  return value === null ? "null" : JSON.stringify(value);
}

function factsText(facts: P5ConditionFact[], layers: string[]): string {
  const selected = facts.filter((fact) => layers.includes(fact.layer_id));
  if (selected.length === 0) {
    return "未提供";
  }
  return selected
    .map(
      (fact) =>
        `${fact.condition_id}：${displayPrimitive(fact.actual)} ` +
        `${fact.operator} ${displayPrimitive(fact.required)} ${fact.unit}` +
        `（${fact.status}）`,
    )
    .join("；");
}

export function mapP5Trades(trades: P5TradeWire[]): TradeCausal[] {
  return [...trades]
    .sort(
      (a, b) =>
        a.decision_evidence.ordinal - b.decision_evidence.ordinal,
    )
    .map((trade) => {
      const evidence = trade.decision_evidence;
      const assumptions =
        evidence.conservative_assumptions.length === 0
          ? "未有保守假設紀錄"
          : evidence.conservative_assumptions
              .map(
                (item) =>
                  `${item.code}：${item.applied ? "已套用" : "未套用"}` +
                  `${item.effects.length > 0 ? `（${item.effects.join("、")}）` : ""}`,
              )
              .join("；");
      return {
        tradeIndex: evidence.ordinal,
        side: trade.direction,
        entryTimeLocal: formatLocalInstant(trade.entry_timestamp),
        entryPrice: trade.entry_price,
        exitTimeLocal: formatLocalInstant(trade.exit_timestamp),
        exitTimeUtc: trade.exit_timestamp,
        exitPrice: trade.exit_price,
        targetPrice: trade.target_price,
        rMultiple: null,
        pnlUsd: trade.net_pnl,
        whyEntry: {
          d: factsText(evidence.entry.condition_facts, ["daily"]),
          h1: factsText(evidence.entry.condition_facts, ["mid"]),
          m5: factsText(evidence.entry.condition_facts, [
            "entry",
            "execution",
          ]),
        },
        stop: {
          anchor: `${evidence.stop.reference_type} ${evidence.stop.reference_price}`,
          offsetTicks: evidence.stop.offset_ticks,
          finalPrice: evidence.stop.final_stop_price,
          reason: factsText(evidence.stop.condition_facts, [
            "daily",
            "mid",
            "entry",
            "execution",
          ]),
        },
        exit: {
          kind: evidence.exit.actual_reason,
          whichFirst: evidence.exit.selected_candidate,
          detail:
            `${evidence.exit.resolution}；候選：` +
            evidence.exit.candidates
              .map(
                (candidate) =>
                  `${candidate.reason}@${candidate.price} ${candidate.timestamp}`,
              )
              .join("、"),
        },
        conservative: assumptions,
        focusTimeUtc: trade.entry_timestamp,
      };
    });
}

export interface P5EventWire {
  sequence: number;
  timestamp: string;
  ts_init: string;
  phase: string;
  machine: string;
  event_type: string;
  from_state: string | null;
  to_state: string | null;
  direction: string | null;
  price: number | null;
  details: Record<string, unknown>;
}

export interface P5RejectionEvidence {
  evidence_id: string;
  timestamp: string;
  ts_init: string;
  trading_date: string;
  direction: string;
  evaluation_sequence: number;
  reached_layers: string[];
  condition_facts: P5ConditionFact[];
  blocking_condition_ids: string[];
  context: {
    candidate_signal_kinds: string[];
    inside_count: number | null;
    entry_pullback_state: string;
    mid_pullback_state: string;
    daily_regime: string | null;
  };
  source_event_sequences: number[];
}

export interface P5EvidenceSummary {
  availability: "available";
  complete: true;
  evaluation_count: number;
  rejection_count: number;
  layer_reached_counts: Record<string, number>;
  blocking_condition_counts: Record<string, number>;
  deepest_layer: string | null;
  trade_count: number;
}

export type P5Events =
  | {
      kind: "complete";
      events: P5EventWire[];
      rejections: P5RejectionEvidence[];
      summary: P5EvidenceSummary;
    }
  | { kind: "unavailable"; events: P5EventWire[] };

function parseEvent(
  value: unknown,
  index: number,
): P5EventWire {
  const resource = "events";
  const path = `$.events[${index}]`;
  const event = requireObject(value, resource, path);
  requireExactKeys(event, EVENT_KEYS, resource, path);
  check(positiveInteger(event.sequence), resource, `${path}.sequence`, "無效");
  check(timezoneInstant(event.timestamp), resource, `${path}.timestamp`, "無效");
  check(
    timezoneInstant(event.ts_init) &&
      Date.parse(event.ts_init) >= Date.parse(event.timestamp),
    resource,
    `${path}.ts_init`,
    "無效或早過timestamp",
  );
  for (const key of ["phase", "machine", "event_type"] as const) {
    check(nonBlankExactString(event[key]), resource, `${path}.${key}`, "無效");
  }
  for (const key of ["from_state", "to_state", "direction"] as const) {
    check(nullableExactString(event[key]), resource, `${path}.${key}`, "無效");
  }
  check(finiteOrNull(event.price), resource, `${path}.price`, "無效");
  const details = requireObject(event.details, resource, `${path}.details`);
  check(jsonValue(details), resource, `${path}.details`, "唔係finite JSON");
  return {
    sequence: event.sequence,
    timestamp: event.timestamp,
    ts_init: event.ts_init,
    phase: event.phase as string,
    machine: event.machine as string,
    event_type: event.event_type as string,
    from_state: event.from_state as string | null,
    to_state: event.to_state as string | null,
    direction: event.direction as string | null,
    price: event.price as number | null,
    details,
  };
}

function parseRejection(
  value: unknown,
  index: number,
  eventSequences: ReadonlySet<number>,
): P5RejectionEvidence {
  const resource = "events";
  const path = `$.rejection_evidence[${index}]`;
  const rejection = requireObject(value, resource, path);
  requireExactKeys(rejection, REJECTION_KEYS, resource, path);
  check(
    nonBlankExactString(rejection.evidence_id),
    resource,
    `${path}.evidence_id`,
    "無效",
  );
  check(
    timezoneInstant(rejection.timestamp),
    resource,
    `${path}.timestamp`,
    "無效",
  );
  check(
    timezoneInstant(rejection.ts_init) &&
      Date.parse(rejection.ts_init) >= Date.parse(rejection.timestamp),
    resource,
    `${path}.ts_init`,
    "無效或早過timestamp",
  );
  check(
    realDateLabel(rejection.trading_date),
    resource,
    `${path}.trading_date`,
    "無效",
  );
  check(
    nonBlankExactString(rejection.direction),
    resource,
    `${path}.direction`,
    "無效",
  );
  check(
    positiveInteger(rejection.evaluation_sequence),
    resource,
    `${path}.evaluation_sequence`,
    "無效",
  );
  const reached = requireUniqueStrings(
    rejection.reached_layers,
    resource,
    `${path}.reached_layers`,
    { nonEmpty: true },
  );
  const facts = parseConditionFacts(
    rejection.condition_facts,
    resource,
    `${path}.condition_facts`,
    eventSequences,
  );
  const blockers = requireUniqueStrings(
    rejection.blocking_condition_ids,
    resource,
    `${path}.blocking_condition_ids`,
    { nonEmpty: true },
  );
  const factsById = new Map(facts.map((fact) => [fact.condition_id, fact]));
  check(
    blockers.every((id) => factsById.get(id)?.status === "failed"),
    resource,
    `${path}.blocking_condition_ids`,
    "每個blocker必須指向failed fact",
  );
  const context = requireObject(rejection.context, resource, `${path}.context`);
  requireExactKeys(
    context,
    [
      "candidate_signal_kinds",
      "inside_count",
      "entry_pullback_state",
      "mid_pullback_state",
      "daily_regime",
    ],
    resource,
    `${path}.context`,
  );
  const candidateKinds = requireUniqueStrings(
    context.candidate_signal_kinds,
    resource,
    `${path}.context.candidate_signal_kinds`,
    { nonEmpty: true },
  );
  check(
    context.inside_count === null || positiveInteger(context.inside_count),
    resource,
    `${path}.context.inside_count`,
    "必須係正整數或null",
  );
  check(
    nonBlankExactString(context.entry_pullback_state),
    resource,
    `${path}.context.entry_pullback_state`,
    "無效",
  );
  check(
    nonBlankExactString(context.mid_pullback_state),
    resource,
    `${path}.context.mid_pullback_state`,
    "無效",
  );
  check(
    context.daily_regime === null ||
      nonBlankExactString(context.daily_regime),
    resource,
    `${path}.context.daily_regime`,
    "無效",
  );
  const sources = requirePositiveSequenceArray(
    rejection.source_event_sequences,
    resource,
    `${path}.source_event_sequences`,
    true,
  );
  check(
    sources.every((sequence) => eventSequences.has(sequence)),
    resource,
    `${path}.source_event_sequences`,
    "引用不存在event sequence",
  );
  return {
    evidence_id: rejection.evidence_id,
    timestamp: rejection.timestamp,
    ts_init: rejection.ts_init,
    trading_date: rejection.trading_date,
    direction: rejection.direction,
    evaluation_sequence: rejection.evaluation_sequence,
    reached_layers: reached,
    condition_facts: facts,
    blocking_condition_ids: blockers,
    context: {
      candidate_signal_kinds: candidateKinds,
      inside_count: context.inside_count as number | null,
      entry_pullback_state: context.entry_pullback_state,
      mid_pullback_state: context.mid_pullback_state,
      daily_regime: context.daily_regime as string | null,
    },
    source_event_sequences: sources,
  };
}

function sameCountMap(
  actual: Record<string, number>,
  expected: Record<string, number>,
): boolean {
  const actualKeys = Object.keys(actual).sort();
  const expectedKeys = Object.keys(expected).sort();
  return (
    actualKeys.length === expectedKeys.length &&
    actualKeys.every(
      (key, index) =>
        key === expectedKeys[index] && actual[key] === expected[key],
    )
  );
}

function deepestKnownLayer(
  rejections: P5RejectionEvidence[],
): string | null {
  let deepest: string | null = null;
  let depth = -1;
  for (const rejection of rejections) {
    for (const layer of rejection.reached_layers) {
      const candidate = LAYER_DEPTH[layer];
      if (candidate !== undefined && candidate > depth) {
        depth = candidate;
        deepest = layer;
      }
    }
  }
  return deepest;
}

function parseEvidenceSummary(
  value: unknown,
  events: P5EventWire[],
  rejections: P5RejectionEvidence[],
  expectedTradeCount: number,
): P5EvidenceSummary {
  const resource = "events";
  const path = "$.evidence_summary";
  const summary = requireObject(value, resource, path);
  requireExactKeys(
    summary,
    [
      "availability",
      "complete",
      "evaluation_count",
      "rejection_count",
      "layer_reached_counts",
      "blocking_condition_counts",
      "deepest_layer",
      "trade_count",
    ],
    resource,
    path,
  );
  check(summary.availability === "available", resource, `${path}.availability`, "無效");
  check(summary.complete === true, resource, `${path}.complete`, "無效");
  for (const key of [
    "evaluation_count",
    "rejection_count",
    "trade_count",
  ] as const) {
    check(nonNegativeInteger(summary[key]), resource, `${path}.${key}`, "無效");
  }
  check(
    summary.rejection_count === rejections.length,
    resource,
    `${path}.rejection_count`,
    "同records唔一致",
  );
  const evaluationCount = events.filter(
    (event) =>
      event.event_type === "signal_created" ||
      event.event_type === "signal_rejected",
  ).length;
  check(
    summary.evaluation_count === evaluationCount,
    resource,
    `${path}.evaluation_count`,
    "同event log唔一致",
  );
  check(
    events.filter((event) => event.event_type === "signal_rejected").length ===
      rejections.length,
    resource,
    "$.events",
    "signal_rejected同records唔一致",
  );
  check(
    summary.trade_count === expectedTradeCount,
    resource,
    `${path}.trade_count`,
    "同main trade_count唔一致",
  );
  const expectedLayerCounts: Record<string, number> = {};
  const expectedBlockingCounts: Record<string, number> = {};
  for (const rejection of rejections) {
    for (const layer of new Set(rejection.reached_layers)) {
      expectedLayerCounts[layer] = (expectedLayerCounts[layer] ?? 0) + 1;
    }
    for (const blocker of rejection.blocking_condition_ids) {
      expectedBlockingCounts[blocker] =
        (expectedBlockingCounts[blocker] ?? 0) + 1;
    }
  }
  const layerCounts = parseCountMap(
    summary.layer_reached_counts,
    resource,
    `${path}.layer_reached_counts`,
  );
  const blockingCounts = parseCountMap(
    summary.blocking_condition_counts,
    resource,
    `${path}.blocking_condition_counts`,
  );
  check(
    sameCountMap(layerCounts, expectedLayerCounts),
    resource,
    `${path}.layer_reached_counts`,
    "同records重算唔一致",
  );
  check(
    sameCountMap(blockingCounts, expectedBlockingCounts),
    resource,
    `${path}.blocking_condition_counts`,
    "同records重算唔一致",
  );
  check(
    summary.deepest_layer === deepestKnownLayer(rejections),
    resource,
    `${path}.deepest_layer`,
    "同records重算唔一致",
  );
  return {
    availability: "available",
    complete: true,
    evaluation_count: summary.evaluation_count as number,
    rejection_count: summary.rejection_count as number,
    layer_reached_counts: layerCounts,
    blocking_condition_counts: blockingCounts,
    deepest_layer: summary.deepest_layer as string | null,
    trade_count: summary.trade_count as number,
  };
}

export function parseP5Events(
  input: unknown,
  expectedRunId: string,
  expectedTradeCount: number,
): P5ParseResult<P5Events> {
  return attempt("events", () => {
    const body = requireObject(bodyOf(input, "events"), "events", "$");
    check(body.schema === "events.v1", "events", "$.schema", "無效");
    check(
      body.run_id === expectedRunId,
      "events",
      "$.run_id",
      "同route run id唔一致",
    );
    check(Array.isArray(body.events), "events", "$.events", "必須係array");
    const parsedEvents = body.events.map(parseEvent);
    const sequences = parsedEvents.map((event) => event.sequence);
    check(
      new Set(sequences).size === sequences.length &&
        sequences.every(
          (sequence, index) => index === 0 || sequence > sequences[index - 1],
        ),
      "events",
      "$.events",
      "sequence必須unique且ascending",
    );
    if (
      exactKeys(body, [
        "schema",
        "run_id",
        "events",
        "evidence_complete",
        "evidence_availability",
      ])
    ) {
      check(
        body.evidence_complete === false &&
          body.evidence_availability === "unavailable",
        "events",
        "$.evidence_complete",
        "legacy unavailable marker無效",
      );
      return { kind: "unavailable", events: parsedEvents };
    }
    requireExactKeys(
      body,
      [
        "schema",
        "run_id",
        "events",
        "evidence_complete",
        "rejection_evidence",
        "evidence_summary",
      ],
      "events",
      "$",
    );
    check(
      body.evidence_complete === true,
      "events",
      "$.evidence_complete",
      "complete profile必須exact true",
    );
    check(
      Array.isArray(body.rejection_evidence),
      "events",
      "$.rejection_evidence",
      "必須係array",
    );
    const eventSet = new Set(sequences);
    const rejections = body.rejection_evidence.map((rejection, index) =>
      parseRejection(rejection, index, eventSet),
    );
    const ids = rejections.map((rejection) => rejection.evidence_id);
    const evaluationSequences = rejections.map(
      (rejection) => rejection.evaluation_sequence,
    );
    check(new Set(ids).size === ids.length, "events", "$.rejection_evidence", "evidence_id duplicate");
    check(
      new Set(evaluationSequences).size === evaluationSequences.length,
      "events",
      "$.rejection_evidence",
      "evaluation_sequence duplicate",
    );
    check(
      rejections.every(
        (rejection, index) =>
          index === 0 ||
          rejection.evaluation_sequence >
            rejections[index - 1].evaluation_sequence ||
          (rejection.evaluation_sequence ===
            rejections[index - 1].evaluation_sequence &&
            rejection.evidence_id >
              rejections[index - 1].evidence_id),
      ),
      "events",
      "$.rejection_evidence",
      "必須按evaluation_sequence/evidence_id ascending",
    );
    const summary = parseEvidenceSummary(
      body.evidence_summary,
      parsedEvents,
      rejections,
      expectedTradeCount,
    );
    return {
      kind: "complete",
      events: parsedEvents,
      rejections,
      summary,
    };
  });
}

export type P5ClosestRejections =
  | { supported: true; records: P5RejectionEvidence[] }
  | { supported: false; records: []; reason: string };

function rejectionDepth(rejection: P5RejectionEvidence): number {
  return Math.max(
    ...rejection.reached_layers.map((layer) => LAYER_DEPTH[layer]),
  );
}

export function selectClosestRejections(
  rejections: readonly P5RejectionEvidence[],
): P5ClosestRejections {
  if (
    rejections.some((rejection) =>
      rejection.reached_layers.some(
        (layer) => LAYER_DEPTH[layer] === undefined,
      ),
    )
  ) {
    return {
      supported: false,
      records: [],
      reason: "排序規則未支援此 layer",
    };
  }
  const records = [...rejections]
    .sort((a, b) => {
      const depth = rejectionDepth(b) - rejectionDepth(a);
      if (depth !== 0) return depth;
      const reached =
        new Set(b.reached_layers).size - new Set(a.reached_layers).size;
      if (reached !== 0) return reached;
      const blockers =
        a.blocking_condition_ids.length - b.blocking_condition_ids.length;
      if (blockers !== 0) return blockers;
      const sequence = b.evaluation_sequence - a.evaluation_sequence;
      if (sequence !== 0) return sequence;
      const timestamp = Date.parse(b.timestamp) - Date.parse(a.timestamp);
      if (timestamp !== 0) return timestamp;
      return b.evidence_id.localeCompare(a.evidence_id);
    })
    .slice(0, 3);
  return { supported: true, records };
}

export function mapP5NearMisses(
  rejections: readonly P5RejectionEvidence[],
): P5ClosestRejections & { nearMisses?: NearMiss[] } {
  const selected = selectClosestRejections(rejections);
  if (!selected.supported) {
    return selected;
  }
  const timezone =
    Intl.DateTimeFormat().resolvedOptions().timeZone || "本地時區";
  const nearMisses = selected.records.map((rejection, index) => {
    const byId = new Map(
      rejection.condition_facts.map((fact) => [fact.condition_id, fact]),
    );
    const blockerFacts = rejection.blocking_condition_ids
      .map((id) => byId.get(id))
      .filter((fact): fact is P5ConditionFact => Boolean(fact));
    return {
      index: index + 1,
      timeLocal: formatLocalInstant(rejection.timestamp),
      timezone,
      layerReached: rejection.reached_layers.join(" → "),
      stepsAway: rejection.blocking_condition_ids.length,
      missing: rejection.blocking_condition_ids.join("、"),
      values: blockerFacts
        .map(
          (fact) =>
            `${fact.condition_id}: ${displayPrimitive(fact.actual)} ` +
            `${fact.operator} ${displayPrimitive(fact.required)} ${fact.unit}`,
        )
        .join("；"),
      focusTimeUtc: rejection.timestamp,
    };
  });
  return { ...selected, nearMisses };
}

export interface P5Chart {
  schema: "chart_series.v1";
  run_id: string;
  timeframe: P5ChartTimeframe;
  contract_id: string;
  session_name: string;
  data_fingerprint: string;
  lookback_days: number;
  visible_start: string;
  visible_end: string;
  candles: ChartPaneData["candles"];
  ema18: Array<{ time: number; value: number }>;
  ema50: Array<{ time: number; value: number }>;
  ema90: Array<{ time: number; value: number }>;
  markers: NonNullable<ChartPaneData["markers"]>;
  levels: Array<{
    kind: string;
    price: number;
    time: number | null;
    token: string;
  }>;
  source: string;
  sidecar_relpath: string;
  cache: P5ChartCacheState;
  compute_provenance?: {
    effective_backend?: string;
    fallback_reason?: string | null;
    writes_authority?: boolean;
    requested_backend?: string;
    feature?: string;
    source?: string;
  };
}

function parseIncreasingSeries<T extends { time: number }>(
  value: unknown,
  resource: string,
  path: string,
  parseItem: (item: unknown, path: string) => T,
): T[] {
  check(Array.isArray(value), resource, path, "必須係array");
  const result = value.map((item, index) =>
    parseItem(item, `${path}[${index}]`),
  );
  check(
    result.every(
      (item, index) => index === 0 || item.time > result[index - 1].time,
    ),
    resource,
    path,
    "time必須strictly increasing且unique",
  );
  return result;
}

export function parseP5Chart(
  input: unknown,
  expectedRunId: string,
  expectedTimeframe: P5ChartTimeframe,
  expectedContractId: string,
): P5ParseResult<P5Chart> {
  return attempt(`chart-${expectedTimeframe}`, () => {
    const resource = `chart-${expectedTimeframe}`;
    const body = requireObject(bodyOf(input, resource), resource, "$");
    requireKeysWithOptional(body, CHART_KEYS, CHART_OPTIONAL_KEYS, resource, "$");
    check(body.schema === "chart_series.v1", resource, "$.schema", "無效");
    check(body.run_id === expectedRunId, resource, "$.run_id", "同route run id唔一致");
    check(
      body.timeframe === expectedTimeframe,
      resource,
      "$.timeframe",
      "同requested timeframe唔一致",
    );
    check(
      body.contract_id === expectedContractId,
      resource,
      "$.contract_id",
      "同validated main contract唔一致",
    );
    for (const key of [
      "session_name",
      "data_fingerprint",
      "source",
      "sidecar_relpath",
    ] as const) {
      check(nonBlankExactString(body[key]), resource, `$.${key}`, "無效");
    }
    check(positiveInteger(body.lookback_days), resource, "$.lookback_days", "無效");
    check(timezoneInstant(body.visible_start), resource, "$.visible_start", "無效");
    check(
      timezoneInstant(body.visible_end) &&
        Date.parse(body.visible_end) >= Date.parse(body.visible_start),
      resource,
      "$.visible_end",
      "無效",
    );
    check(
      typeof body.cache === "string" &&
        (P5_CHART_CACHE_STATES as readonly string[]).includes(body.cache),
      resource,
      "$.cache",
      "必須係external closed enum",
    );
    const candles = parseIncreasingSeries(
      body.candles,
      resource,
      "$.candles",
      (item, path) => {
        const object = requireObject(item, resource, path);
        requireExactKeys(
          object,
          ["time", "open", "high", "low", "close"],
          resource,
          path,
        );
        check(nonNegativeInteger(object.time), resource, `${path}.time`, "無效");
        for (const key of ["open", "high", "low", "close"] as const) {
          check(finiteNumber(object[key]), resource, `${path}.${key}`, "無效");
        }
        check(
          (object.low as number) <= (object.open as number) &&
            (object.open as number) <= (object.high as number) &&
            (object.low as number) <= (object.close as number) &&
            (object.close as number) <= (object.high as number),
          resource,
          path,
          "OHLC envelope無效",
        );
        return object as unknown as ChartPaneData["candles"][number];
      },
    );
    const line = (value: unknown, path: string) =>
      parseIncreasingSeries(value, resource, path, (item, itemPath) => {
        const object = requireObject(item, resource, itemPath);
        requireExactKeys(object, ["time", "value"], resource, itemPath);
        check(nonNegativeInteger(object.time), resource, `${itemPath}.time`, "無效");
        check(finiteNumber(object.value), resource, `${itemPath}.value`, "無效");
        return { time: object.time, value: object.value };
      });
    check(Array.isArray(body.markers), resource, "$.markers", "必須係array");
    const markerKeys = new Set<string>();
    const markers = body.markers.map((marker, index) => {
      const path = `$.markers[${index}]`;
      const object = requireObject(marker, resource, path);
      requireExactKeys(
        object,
        ["time", "position", "shape", "token", "text"],
        resource,
        path,
      );
      check(nonNegativeInteger(object.time), resource, `${path}.time`, "無效");
      check(
        object.position === "aboveBar" ||
          object.position === "belowBar" ||
          object.position === "inBar",
        resource,
        `${path}.position`,
        "無效",
      );
      for (const key of ["shape", "token", "text"] as const) {
        check(nonBlankExactString(object[key]), resource, `${path}.${key}`, "無效");
      }
      const identity = `${String(object.time)}\0${object.text}`;
      check(!markerKeys.has(identity), resource, path, "duplicate (time,text)");
      markerKeys.add(identity);
      return {
        time: object.time,
        position: object.position,
        shape: object.shape,
        token: object.token,
        text: object.text,
      } as NonNullable<ChartPaneData["markers"]>[number];
    });
    check(Array.isArray(body.levels), resource, "$.levels", "必須係array");
    const levels = body.levels.map((level, index) => {
      const path = `$.levels[${index}]`;
      const object = requireObject(level, resource, path);
      requireExactKeys(
        object,
        ["kind", "price", "time", "token"],
        resource,
        path,
      );
      check(nonBlankExactString(object.kind), resource, `${path}.kind`, "無效");
      check(finiteNumber(object.price), resource, `${path}.price`, "無效");
      check(
        object.time === null || nonNegativeInteger(object.time),
        resource,
        `${path}.time`,
        "無效",
      );
      check(nonBlankExactString(object.token), resource, `${path}.token`, "無效");
      return {
        kind: object.kind,
        price: object.price,
        time: object.time as number | null,
        token: object.token,
      };
    });
    let computeProvenance: P5Chart["compute_provenance"];
    if (body.compute_provenance !== undefined) {
      const prov = requireObject(
        body.compute_provenance,
        resource,
        "$.compute_provenance",
      );
      const effective = prov.effective_backend;
      check(
        effective === undefined ||
          effective === "python" ||
          effective === "rust" ||
          (typeof effective === "string" && effective.length > 0),
        resource,
        "$.compute_provenance.effective_backend",
        "無效",
      );
      if (prov.writes_authority !== undefined) {
        check(
          prov.writes_authority === false,
          resource,
          "$.compute_provenance.writes_authority",
          "必須係 false",
        );
      }
      computeProvenance = {
        effective_backend:
          typeof effective === "string" ? effective : undefined,
        fallback_reason:
          prov.fallback_reason === null ||
          typeof prov.fallback_reason === "string"
            ? (prov.fallback_reason as string | null)
            : undefined,
        writes_authority:
          prov.writes_authority === false ? false : undefined,
        requested_backend:
          typeof prov.requested_backend === "string"
            ? prov.requested_backend
            : undefined,
        feature: typeof prov.feature === "string" ? prov.feature : undefined,
        source: typeof prov.source === "string" ? prov.source : undefined,
      };
    }
    return {
      schema: "chart_series.v1",
      run_id: expectedRunId,
      timeframe: expectedTimeframe,
      contract_id: expectedContractId,
      session_name: body.session_name as string,
      data_fingerprint: body.data_fingerprint as string,
      lookback_days: body.lookback_days as number,
      visible_start: body.visible_start as string,
      visible_end: body.visible_end as string,
      candles,
      ema18: line(body.ema18, "$.ema18"),
      ema50: line(body.ema50, "$.ema50"),
      ema90: line(body.ema90, "$.ema90"),
      markers,
      levels,
      source: body.source as string,
      sidecar_relpath: body.sidecar_relpath as string,
      cache: body.cache as P5ChartCacheState,
      compute_provenance: computeProvenance,
    };
  });
}

export function mapP5Chart(chart: P5Chart): ChartPaneData {
  const prov = chart.compute_provenance;
  return {
    timeframe: chart.timeframe,
    roleLabel:
      chart.timeframe === "D"
        ? "大框架"
        : chart.timeframe === "1H"
          ? "中框架"
          : "入市",
    available: true,
    candles: chart.candles,
    indicators: [
      { id: "ema18", token: "chart-ema-18", points: chart.ema18 },
      { id: "ema50", token: "chart-ema-50", points: chart.ema50 },
      { id: "ema90", token: "chart-ema-90", points: chart.ema90 },
    ],
    markers: chart.markers,
    levels: chart.levels.map((level) => ({
      price: level.price,
      token: level.token,
      label: level.kind,
    })),
    computeBackend: prov?.effective_backend
      ? {
          effectiveBackend: prov.effective_backend,
          source: chart.source,
          fallbackReason: prov.fallback_reason ?? null,
          writesAuthority: prov.writes_authority,
        }
      : {
          effectiveBackend: "python",
          source: chart.source,
          fallbackReason: null,
        },
  };
}

function unixSeconds(instant: string): number {
  return Math.floor(Date.parse(instant) / 1000);
}

export function enrichP5ChartPanes(
  panes: readonly ChartPaneData[],
  trades: readonly TradeCausal[],
  nearMisses: readonly NearMiss[],
  activeTrade: number | null,
): ChartPaneData[] {
  const active =
    trades.find((trade) => trade.tradeIndex === activeTrade) ??
    trades[0] ??
    null;
  const tradeVerticals = trades.map((trade) => ({
    time: unixSeconds(trade.focusTimeUtc),
    label: `#${trade.tradeIndex} · ${trade.entryTimeLocal}`,
  }));
  const rejects = nearMisses.map((miss) => ({
    time: unixSeconds(miss.focusTimeUtc),
    text: `近失 #${miss.index} · ${miss.missing}`,
  }));
  return panes.map((pane) => {
    if (!pane.available) {
      return { ...pane };
    }
    if (pane.timeframe === "D" || pane.timeframe === "1H") {
      return {
        ...pane,
        verticalLines: tradeVerticals,
        verticalAnnotations: tradeVerticals,
        rejectMarkers: rejects,
      };
    }
    if (pane.timeframe !== "5m") {
      return { ...pane, rejectMarkers: rejects };
    }
    const evidenceMarkers = active
      ? [
          {
            time: unixSeconds(active.focusTimeUtc),
            position:
              active.side === "long"
                ? ("belowBar" as const)
                : ("aboveBar" as const),
            shape: active.side === "long" ? "arrowUp" : "arrowDown",
            token: "chart-marker-entry",
            text: `#${active.tradeIndex} · 入市 ${active.entryPrice}`,
          },
          ...(active.exitTimeUtc
            ? [
                {
                  time: unixSeconds(active.exitTimeUtc),
                  position:
                    active.side === "long"
                      ? ("aboveBar" as const)
                      : ("belowBar" as const),
                  shape: "circle",
                  token: "chart-marker-exit",
                  text: `#${active.tradeIndex} · 離場 ${active.exitPrice}`,
                },
              ]
            : []),
        ]
      : [];
    const evidenceLevels = active
      ? [
          {
            price: active.stop.finalPrice,
            token: "color-negative",
            label: `#${active.tradeIndex} 止損`,
          },
          ...(active.targetPrice === undefined
            ? []
            : [
                {
                  price: active.targetPrice,
                  token: "color-positive",
                  label: `#${active.tradeIndex} 目標`,
                },
              ]),
        ]
      : [];
    return {
      ...pane,
      markers: [...(pane.markers ?? []), ...evidenceMarkers],
      levels: [...(pane.levels ?? []), ...evidenceLevels],
      rejectMarkers: rejects,
    };
  });
}

export interface P5Narrative {
  schema: "narrative.v1";
  run_id: string;
  count: number;
  steps: NarrativeStep[];
  note: string;
}

export function parseP5Narrative(
  input: unknown,
  expectedRunId: string,
): P5ParseResult<P5Narrative> {
  return attempt("narrative", () => {
    const body = requireObject(bodyOf(input, "narrative"), "narrative", "$");
    requireExactKeys(
      body,
      ["schema", "run_id", "count", "steps", "note"],
      "narrative",
      "$",
    );
    check(body.schema === "narrative.v1", "narrative", "$.schema", "無效");
    check(
      body.run_id === expectedRunId,
      "narrative",
      "$.run_id",
      "同route run id唔一致",
    );
    check(Array.isArray(body.steps), "narrative", "$.steps", "必須係array");
    check(
      nonNegativeInteger(body.count) && body.count === body.steps.length,
      "narrative",
      "$.count",
      "同steps.length唔一致",
    );
    check(typeof body.note === "string", "narrative", "$.note", "必須係string");
    const steps = body.steps.map((step, index) => {
      const path = `$.steps[${index}]`;
      const object = requireObject(step, "narrative", path);
      requireExactKeys(
        object,
        ["time", "time_label", "layer", "tone", "text"],
        "narrative",
        path,
      );
      check(
        object.time === null || timezoneInstant(object.time),
        "narrative",
        `${path}.time`,
        "必須係instant或null",
      );
      for (const key of ["time_label", "layer", "tone", "text"] as const) {
        check(
          nonBlankExactString(object[key]),
          "narrative",
          `${path}.${key}`,
          "無效",
        );
      }
      return object as unknown as NarrativeStep;
    });
    return {
      schema: "narrative.v1",
      run_id: body.run_id,
      count: steps.length,
      steps,
      note: body.note,
    };
  });
}

export function parseP5StrategyLabels(
  input: unknown,
): P5ParseResult<Map<string, string>> {
  return attempt("strategy-catalog", () => {
    const body = requireObject(
      bodyOf(input, "strategy-catalog"),
      "strategy-catalog",
      "$",
    );
    requireExactKeys(
      body,
      ["schema", "count", "versions"],
      "strategy-catalog",
      "$",
    );
    check(
      body.schema === "strategy_version_list.v1",
      "strategy-catalog",
      "$.schema",
      "無效",
    );
    check(
      Array.isArray(body.versions),
      "strategy-catalog",
      "$.versions",
      "必須係array",
    );
    check(
      nonNegativeInteger(body.count) && body.count === body.versions.length,
      "strategy-catalog",
      "$.count",
      "同versions.length唔一致",
    );
    const result = new Map<string, string>();
    body.versions.forEach((version, index) => {
      const path = `$.versions[${index}]`;
      const object = requireObject(version, "strategy-catalog", path);
      check(
        nonBlankExactString(object.strategy_id),
        "strategy-catalog",
        `${path}.strategy_id`,
        "無效",
      );
      check(
        nonBlankExactString(object.name),
        "strategy-catalog",
        `${path}.name`,
        "無效",
      );
      check(
        !result.has(object.strategy_id),
        "strategy-catalog",
        `${path}.strategy_id`,
        "duplicate",
      );
      result.set(object.strategy_id, object.name);
    });
    return result;
  });
}

export interface P5PromotionDecisionRecordWire {
  schema: "promotion_decision.v1";
  decision_id: string;
  request_id: string;
  run_id: string;
  strategy: {
    strategy_id: string;
    content_sha256: string;
  };
  result_sha256: string;
  decision: P5PromotionDecision;
  reason: string;
  scorecard_snapshot: unknown[];
  created_at: string;
}

export interface P5PromotionDecisionHistory {
  schema: "promotion_decision_list.v1";
  run_id: string;
  count: number;
  decisions: P5PromotionDecisionRecordWire[];
}

export interface P5VerifiedExport {
  arrayBuffer: ArrayBuffer;
  bytes: Uint8Array;
  fileName: string;
  members: [string, string, string, string];
}

export function hasP5Stage2Capability(main: P5ResultMain): boolean {
  return (
    main.profile === "current-complete" &&
    main.strategyBinding !== null
  );
}

export function shouldCommitP5DecisionHistory(
  revisionAtStart: number,
  currentRevision: number,
): boolean {
  return revisionAtStart === currentRevision;
}

function requireStage2Main(
  main: P5ResultMain,
  resource: string,
): P5StrategyBinding {
  check(
    hasP5Stage2Capability(main),
    resource,
    "$main",
    "歷史結果缺完整不可變證據",
  );
  return main.strategyBinding as P5StrategyBinding;
}

function parseP5DecisionRecordValue(
  value: unknown,
  main: P5ResultMain,
  resource: string,
  path: string,
  expectedRequest?: P5PromotionDecisionRequest,
): P5PromotionDecisionRecordWire {
  const binding = requireStage2Main(main, resource);
  const object = requireObject(value, resource, path);
  requireExactKeys(
    object,
    [
      "schema",
      "decision_id",
      "request_id",
      "run_id",
      "strategy",
      "result_sha256",
      "decision",
      "reason",
      "scorecard_snapshot",
      "created_at",
    ],
    resource,
    path,
  );
  check(
    object.schema === "promotion_decision.v1",
    resource,
    `${path}.schema`,
    "無效",
  );
  for (const key of ["decision_id", "request_id"] as const) {
    check(
      nonBlankExactString(object[key]),
      resource,
      `${path}.${key}`,
      "必須係非空、無首尾空白字串",
    );
  }
  check(
    object.run_id === main.runId,
    resource,
    `${path}.run_id`,
    "同已驗證 run 唔一致",
  );
  const strategy = requireObject(
    object.strategy,
    resource,
    `${path}.strategy`,
  );
  requireExactKeys(
    strategy,
    ["strategy_id", "content_sha256"],
    resource,
    `${path}.strategy`,
  );
  check(
    strategy.strategy_id === binding.strategy_id,
    resource,
    `${path}.strategy.strategy_id`,
    "同已驗證 strategy binding 唔一致",
  );
  check(
    strategy.content_sha256 === binding.content_sha256,
    resource,
    `${path}.strategy.content_sha256`,
    "同已驗證 strategy hash 唔一致",
  );
  check(
    lowercaseSha256(object.result_sha256),
    resource,
    `${path}.result_sha256`,
    "必須係 lowercase SHA-256",
  );
  check(
    object.decision === "use" ||
      object.decision === "return" ||
      object.decision === "abandon",
    resource,
    `${path}.decision`,
    "唔係已批准決定",
  );
  check(
    nonBlankExactString(object.reason),
    resource,
    `${path}.reason`,
    "必須係非空、無首尾空白字串",
  );
  check(
    Array.isArray(object.scorecard_snapshot) &&
      jsonValue(object.scorecard_snapshot),
    resource,
    `${path}.scorecard_snapshot`,
    "必須係 finite JSON array",
  );
  check(
    jsonEqual(object.scorecard_snapshot, main.rawScorecard),
    resource,
    `${path}.scorecard_snapshot`,
    "同已驗證 immutable scorecard 唔一致",
  );
  check(
    timezoneInstant(object.created_at),
    resource,
    `${path}.created_at`,
    "必須係有時區 instant",
  );
  if (expectedRequest) {
    check(
      object.request_id === expectedRequest.request_id,
      resource,
      `${path}.request_id`,
      "同 immutable request id 唔一致",
    );
    check(
      object.decision === expectedRequest.decision,
      resource,
      `${path}.decision`,
      "同 immutable request decision 唔一致",
    );
    check(
      object.reason === expectedRequest.reason,
      resource,
      `${path}.reason`,
      "同 immutable request reason 唔一致",
    );
  }
  return {
    schema: "promotion_decision.v1",
    decision_id: object.decision_id as string,
    request_id: object.request_id as string,
    run_id: object.run_id as string,
    strategy: {
      strategy_id: strategy.strategy_id as string,
      content_sha256: strategy.content_sha256 as string,
    },
    result_sha256: object.result_sha256,
    decision: object.decision,
    reason: object.reason,
    scorecard_snapshot: structuredClone(
      object.scorecard_snapshot as unknown[],
    ),
    created_at: object.created_at,
  };
}

export function parseP5PromotionDecisionHistory(
  input: unknown,
  main: P5ResultMain,
): P5ParseResult<P5PromotionDecisionHistory> {
  return attempt("promotion-decision-history", () => {
    requireStage2Main(main, "promotion-decision-history");
    const body = requireObject(
      bodyOf(input, "promotion-decision-history"),
      "promotion-decision-history",
      "$",
    );
    requireExactKeys(
      body,
      ["schema", "run_id", "count", "decisions"],
      "promotion-decision-history",
      "$",
    );
    check(
      body.schema === "promotion_decision_list.v1",
      "promotion-decision-history",
      "$.schema",
      "無效",
    );
    check(
      body.run_id === main.runId,
      "promotion-decision-history",
      "$.run_id",
      "同 route/main run id 唔一致",
    );
    check(
      Array.isArray(body.decisions),
      "promotion-decision-history",
      "$.decisions",
      "必須係 array",
    );
    check(
      nonNegativeInteger(body.count) &&
        body.count === body.decisions.length,
      "promotion-decision-history",
      "$.count",
      "同 decisions.length 唔一致",
    );
    const decisions = body.decisions.map((record, index) =>
      parseP5DecisionRecordValue(
        record,
        main,
        "promotion-decision-history",
        `$.decisions[${index}]`,
      ),
    );
    check(
      new Set(decisions.map((record) => record.decision_id)).size ===
        decisions.length,
      "promotion-decision-history",
      "$.decisions",
      "decision_id duplicate",
    );
    check(
      new Set(decisions.map((record) => record.request_id)).size ===
        decisions.length,
      "promotion-decision-history",
      "$.decisions",
      "request_id duplicate",
    );
    for (let index = 1; index < decisions.length; index += 1) {
      const previous = decisions[index - 1];
      const current = decisions[index];
      check(
        previous.created_at < current.created_at ||
          (previous.created_at === current.created_at &&
            previous.decision_id < current.decision_id),
        "promotion-decision-history",
        `$.decisions[${index}]`,
        "必須按 created_at、decision_id 升序",
      );
    }
    return {
      schema: "promotion_decision_list.v1",
      run_id: body.run_id as string,
      count: decisions.length,
      decisions,
    };
  });
}

export function parseP5PromotionDecisionRecord(
  input: unknown,
  main: P5ResultMain,
  expectedRequest: P5PromotionDecisionRequest,
): P5ParseResult<P5PromotionDecisionRecordWire> {
  return attempt("promotion-decision-record", () =>
    parseP5DecisionRecordValue(
      bodyOf(input, "promotion-decision-record"),
      main,
      "promotion-decision-record",
      "$",
      expectedRequest,
    ),
  );
}

function exportEnvelope(
  input: unknown,
): P5ExportHttpResult {
  check(
    isPlainObject(input),
    "result-export",
    "$http",
    "必須係 binary response envelope",
  );
  for (const key of [
    "path",
    "status",
    "ok",
    "contentType",
    "contentDisposition",
    "arrayBuffer",
    "bytes",
    "rawText",
  ]) {
    check(hasOwn(input, key), "result-export", `$http.${key}`, "missing");
  }
  check(
    typeof input.path === "string",
    "result-export",
    "$http.path",
    "必須係字串",
  );
  check(
    input.status === 200 && input.ok === true,
    "result-export",
    "$http.status",
    `HTTP ${String(input.status)} 唔係成功 response；${String(input.rawText)}`,
  );
  check(
    input.contentType === "application/zip",
    "result-export",
    "$http.content-type",
    "必須 exact application/zip",
  );
  check(
    input.arrayBuffer instanceof ArrayBuffer,
    "result-export",
    "$http.arrayBuffer",
    "必須係原 ArrayBuffer",
  );
  check(
    input.bytes instanceof Uint8Array,
    "result-export",
    "$http.bytes",
    "必須係原 Uint8Array",
  );
  const arrayBytes = new Uint8Array(input.arrayBuffer);
  check(
    input.bytes.length === arrayBytes.length &&
      input.bytes.every((value, index) => value === arrayBytes[index]),
    "result-export",
    "$http.bytes",
    "同原 ArrayBuffer bytes 唔一致",
  );
  return input as unknown as P5ExportHttpResult;
}

function orderedZipMemberNames(bytes: Uint8Array): string[] {
  const resource = "result-export";
  const view = new DataView(
    bytes.buffer,
    bytes.byteOffset,
    bytes.byteLength,
  );
  const minimumEocd = 22;
  check(
    bytes.byteLength >= minimumEocd,
    resource,
    "$zip",
    "ZIP 太短",
  );
  const searchStart = Math.max(0, bytes.byteLength - 65_557);
  let eocdOffset = -1;
  for (
    let offset = bytes.byteLength - minimumEocd;
    offset >= searchStart;
    offset -= 1
  ) {
    if (view.getUint32(offset, true) === 0x06054b50) {
      eocdOffset = offset;
      break;
    }
  }
  check(eocdOffset >= 0, resource, "$zip", "搵唔到 ZIP central directory");
  const disk = view.getUint16(eocdOffset + 4, true);
  const centralDisk = view.getUint16(eocdOffset + 6, true);
  const entriesOnDisk = view.getUint16(eocdOffset + 8, true);
  const entryCount = view.getUint16(eocdOffset + 10, true);
  const centralSize = view.getUint32(eocdOffset + 12, true);
  const centralOffset = view.getUint32(eocdOffset + 16, true);
  const commentLength = view.getUint16(eocdOffset + 20, true);
  check(
    disk === 0 &&
      centralDisk === 0 &&
      entriesOnDisk === entryCount &&
      eocdOffset + minimumEocd + commentLength === bytes.byteLength,
    resource,
    "$zip",
    "唔接受 multi-disk／ZIP64／尾隨 bytes",
  );
  check(
    centralOffset + centralSize === eocdOffset,
    resource,
    "$zip",
    "central directory offset/size 唔一致",
  );
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const names: string[] = [];
  let offset = centralOffset;
  for (let index = 0; index < entryCount; index += 1) {
    check(
      offset + 46 <= eocdOffset &&
        view.getUint32(offset, true) === 0x02014b50,
      resource,
      `$zip.entries[${index}]`,
      "central directory entry 無效",
    );
    const nameLength = view.getUint16(offset + 28, true);
    const extraLength = view.getUint16(offset + 30, true);
    const entryCommentLength = view.getUint16(offset + 32, true);
    const nameStart = offset + 46;
    const nextOffset =
      nameStart + nameLength + extraLength + entryCommentLength;
    check(
      nextOffset <= eocdOffset,
      resource,
      `$zip.entries[${index}]`,
      "entry 長度越界",
    );
    const name = decoder.decode(
      bytes.subarray(nameStart, nameStart + nameLength),
    );
    check(
      nonBlankExactString(name) && !name.endsWith("/"),
      resource,
      `$zip.entries[${index}].name`,
      "唔接受空名或 directory member",
    );
    names.push(name);
    offset = nextOffset;
  }
  check(
    offset === eocdOffset &&
      new Set(names).size === names.length,
    resource,
    "$zip.entries",
    "central directory 數量／duplicate member 無效",
  );
  return names;
}

function parseJsonMember(
  text: string,
  path: string,
): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new P5ContractError(
      "result-export",
      `$zip.${path}`,
      "唔係有效 JSON",
    );
  }
}

function parseP5Equity(
  value: unknown,
  expectedRunId: string,
): void {
  const resource = "result-export";
  const body = requireObject(value, resource, "$zip.equity");
  requireExactKeys(
    body,
    ["schema", "run_id", "points"],
    resource,
    "$zip.equity",
  );
  check(
    body.schema === "equity_curve.v1",
    resource,
    "$zip.equity.schema",
    "無效",
  );
  check(
    body.run_id === expectedRunId,
    resource,
    "$zip.equity.run_id",
    "同 route/main run id 唔一致",
  );
  check(
    Array.isArray(body.points),
    resource,
    "$zip.equity.points",
    "必須係 array",
  );
  body.points.forEach((point, index) => {
    const path = `$zip.equity.points[${index}]`;
    const object = requireObject(point, resource, path);
    requireExactKeys(
      object,
      ["timestamp", "equity", "cumulative_net_pnl"],
      resource,
      path,
    );
    check(
      timezoneInstant(object.timestamp),
      resource,
      `${path}.timestamp`,
      "必須係有時區 instant",
    );
    for (const key of ["equity", "cumulative_net_pnl"] as const) {
      check(
        finiteNumber(object[key]),
        resource,
        `${path}.${key}`,
        "必須係 finite native number",
      );
    }
  });
}

export async function parseP5ResultExport(
  input: unknown,
  expectedMain: P5ResultMain,
): Promise<P5ParseResult<P5VerifiedExport>> {
  try {
    requireStage2Main(expectedMain, "result-export");
    const envelope = exportEnvelope(input);
    const fileName = `result-${expectedMain.runId}.zip`;
    check(
      envelope.contentDisposition ===
        `attachment; filename="${fileName}"`,
      "result-export",
      "$http.content-disposition",
      "download filename 同 run id 唔一致",
    );
    const members = [
      "result.json",
      `trades/${expectedMain.runId}.json`,
      `equity/${expectedMain.runId}.json`,
      `events/${expectedMain.runId}.json`,
    ] as const;
    const actualMembers = orderedZipMemberNames(envelope.bytes);
    check(
      actualMembers.length === members.length &&
        actualMembers.every((name, index) => name === members[index]),
      "result-export",
      "$zip.entries",
      `member order/set 唔一致（收到 ${actualMembers.join(",")}）`,
    );
    const zip = await JSZip.loadAsync(envelope.bytes, {
      checkCRC32: true,
      createFolders: false,
    });
    const memberBodies = await Promise.all(
      members.map(async (path) => {
        const file = zip.file(path);
        check(
          file !== null && file.dir === false,
          "result-export",
          `$zip.${path}`,
          "member missing",
        );
        return parseJsonMember(await file.async("string"), path);
      }),
    );
    const mainResult = parseP5ResultMain(
      memberBodies[0],
      expectedMain.runId,
    );
    check(
      mainResult.ok && hasP5Stage2Capability(mainResult.value),
      "result-export",
      "$zip.result.json",
      mainResult.ok ? "唔係 current-complete" : mainResult.error,
    );
    const exportedMain = mainResult.value;
    check(
      exportedMain.refs.trades === members[1] &&
        exportedMain.refs.equity === members[2] &&
        exportedMain.refs.events === members[3],
      "result-export",
      "$zip.result.json.refs",
      "三個 member ref 唔一致",
    );
    check(
      exportedMain.strategyVersion === expectedMain.strategyVersion &&
        exportedMain.contractId === expectedMain.contractId &&
        exportedMain.strategyBinding?.content_sha256 ===
          expectedMain.strategyBinding?.content_sha256 &&
        jsonEqual(exportedMain.rawScorecard, expectedMain.rawScorecard),
      "result-export",
      "$zip.result.json",
      "同頁面已驗證 immutable main 唔一致",
    );
    const trades = parseP5Trades(
      memberBodies[1],
      expectedMain.runId,
      expectedMain.contractId,
      exportedMain.metrics.trade_count,
    );
    check(
      trades.ok,
      "result-export",
      "$zip.trades",
      trades.ok ? "" : trades.error,
    );
    parseP5Equity(memberBodies[2], expectedMain.runId);
    const events = parseP5Events(
      memberBodies[3],
      expectedMain.runId,
      exportedMain.metrics.trade_count,
    );
    check(
      events.ok,
      "result-export",
      "$zip.events",
      events.ok ? "" : events.error,
    );
    return {
      ok: true,
      value: {
        arrayBuffer: envelope.arrayBuffer,
        bytes: envelope.bytes,
        fileName,
        members: [...members],
      },
    };
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof P5ContractError
          ? error.message
          : `result-export:${
              error instanceof Error ? error.message : String(error)
            }`,
    };
  }
}
