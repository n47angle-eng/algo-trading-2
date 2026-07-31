import type {
  StrategyValidationIssue,
  StrategyValidationResponse,
} from "../../api/client";
import type { InstrumentCatalogRow } from "../catalog/types";
import type { ChartPaneData } from "../results/types";
import type { StrategyUniverse } from "../strategy/universe";
import { localDatetimeToUtcIso } from "../backtest/time";

export const PREVIEW_TIMEFRAMES = ["D", "1H", "30m", "5m"] as const;

export type PreviewTimeframe = (typeof PREVIEW_TIMEFRAMES)[number];

export interface PreviewAssumptions {
  initial_capital_usd: number;
  commission_per_side: number;
  slippage_ticks: number;
}

export interface BacktestPreviewRequest {
  schema: "backtest_preview_request.v1";
  source_text: string;
  filename: string | null;
  contract_id: string;
  range_start: string;
  range_end: string;
  assumptions: PreviewAssumptions;
}

export interface PreviewRequestInput {
  sourceText: string;
  filename: string | null;
  contractId: string;
  rangeStartLocal: string;
  rangeEndLocal: string;
  commissionText: string;
  slippageText: string;
}

export type PreviewRequestResult =
  | { ok: true; request: BacktestPreviewRequest }
  | { ok: false; reason: string };

export interface PreviewFunnel {
  schema: "funnel.v1";
  status: "ok" | "insufficient_sample";
  daily_trend_days: number;
  evaluations_passing_daily_gate: number;
  evaluations_passing_mid_gate: number;
  signals_created: number;
  fills: number;
  reject_reasons: Record<string, number>;
  notes: string;
  units: {
    daily_trend_days: string;
    evaluations_passing_daily_gate: string;
    evaluations_passing_mid_gate: string;
    signals_created: string;
    fills: string;
  };
}

export interface PreviewEvidenceSummary {
  availability: "available" | "unavailable";
  complete: boolean;
  evaluation_count: number;
  rejection_count: number;
  layer_reached_counts: Record<string, number>;
  blocking_condition_counts: Record<string, number>;
  deepest_layer: string | null;
  trade_count: number;
}

export interface PreviewRejectionEvidence {
  evidenceId: string;
  timestamp: string;
  reachedLayers: string[];
  blockingConditionIds: string[];
  raw: Record<string, unknown>;
}

export interface PreviewChartSeries {
  schema: "chart_series.v1";
  timeframe: PreviewTimeframe;
  contractId: string;
  sessionName: string;
  dataFingerprint: string;
  lookbackDays: number;
  visibleStart: string;
  visibleEnd: string;
  candles: Array<{
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
  }>;
  ema18: Array<{ time: number; value: number }>;
  ema50: Array<{ time: number; value: number }>;
  ema90: Array<{ time: number; value: number }>;
  markers: Array<{
    time: number;
    position: "aboveBar" | "belowBar" | "inBar";
    shape: "arrowUp" | "arrowDown" | "circle" | "square";
    token: string;
    text: string;
  }>;
  levels: Array<{
    kind: string;
    price: number;
    time: number | null;
    token: string;
  }>;
  source: string;
  persisted: false;
}

interface PreviewEnvelopeBase {
  schema: "backtest_preview.v1";
  runScope: "dry_run";
  requestFingerprint: {
    algorithm: "sha256";
    digest: string;
  };
  validation: StrategyValidationResponse;
  evidenceSummary: PreviewEvidenceSummary;
  persisted: false;
  warnings: string[];
  errors: string[];
}

export interface PreviewSuccess extends PreviewEnvelopeBase {
  kind: "success";
  validation: StrategyValidationResponse & { valid: true };
  funnel: PreviewFunnel;
  decisionEvidence: Record<string, unknown>[];
  rejectionEvidence: PreviewRejectionEvidence[];
  charts: Record<PreviewTimeframe, PreviewChartSeries>;
}

export interface PreviewFailure extends PreviewEnvelopeBase {
  kind: "failure";
  funnel: null;
  decisionEvidence: [];
  rejectionEvidence: [];
  charts: Record<string, never>;
}

export type PreviewEnvelope = PreviewSuccess | PreviewFailure;

export type PreviewEnvelopeResult =
  | { ok: true; value: PreviewEnvelope }
  | { ok: false; reason: string };

interface ExpectedPreviewIdentity {
  contractId: string;
  sessionName: string;
}

const SHA256_HEX = /^[0-9a-f]{64}$/;
const UTC_ISO =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const DATE_LABEL = /^\d{4}-\d{2}-\d{2}$/;
const DATETIME_LOCAL =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;
const MARKER_POSITIONS = new Set(["aboveBar", "belowBar", "inBar"]);
const MARKER_SHAPES = new Set(["arrowUp", "arrowDown", "circle", "square"]);

function invalid(reason: string): { ok: false; reason: string } {
  return { ok: false, reason };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0
  );
}

function isPositiveInteger(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value > 0
  );
}

function isNonBlankString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isCanonicalUtc(value: unknown): value is string {
  return (
    typeof value === "string" &&
    UTC_ISO.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function isRealDateLabel(value: unknown): value is string {
  if (typeof value !== "string" || !DATE_LABEL.test(value)) {
    return false;
  }
  const [yearText, monthText, dayText] = value.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

function isPrimitive(value: unknown): boolean {
  return (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    isFiniteNumber(value)
  );
}

function isJsonValue(value: unknown): boolean {
  if (isPrimitive(value)) {
    return true;
  }
  if (Array.isArray(value)) {
    return value.every(isJsonValue);
  }
  if (isRecord(value)) {
    return Object.values(value).every(isJsonValue);
  }
  return false;
}

function containsKeyDeep(value: unknown, wanted: string): boolean {
  if (Array.isArray(value)) {
    return value.some((item) => containsKeyDeep(item, wanted));
  }
  if (!isRecord(value)) {
    return false;
  }
  return (
    Object.prototype.hasOwnProperty.call(value, wanted) ||
    Object.values(value).some((item) => containsKeyDeep(item, wanted))
  );
}

function dryRunIdentityError(body: Record<string, unknown>): string | null {
  if (body.persisted !== false) {
    return "preview response persisted 必須 exact false";
  }
  if (containsKeyDeep(body, "run_id")) {
    return "preview response 任意深度都唔准有 run_id";
  }
  return null;
}

function parseStringArray(
  value: unknown,
  path: string,
  options: { nonEmpty?: boolean; unique?: boolean } = {},
): string[] | string {
  if (
    !Array.isArray(value) ||
    !value.every((item) => isNonBlankString(item))
  ) {
    return `${path} 必須係非空字串陣列`;
  }
  const values = value as string[];
  if (options.nonEmpty && values.length === 0) {
    return `${path} 唔可以係空陣列`;
  }
  if (options.unique && new Set(values).size !== values.length) {
    return `${path} 唔可以有重複值`;
  }
  return [...values];
}

function parseCountMap(
  value: unknown,
  path: string,
): Record<string, number> | string {
  if (!isRecord(value)) {
    return `${path} 必須係 count mapping`;
  }
  const parsed: Record<string, number> = {};
  for (const [key, count] of Object.entries(value)) {
    if (!isNonBlankString(key) || !isNonNegativeInteger(count)) {
      return `${path} 每個 id 要配非負整數`;
    }
    parsed[key] = count;
  }
  return parsed;
}

function parseValidation(
  value: unknown,
): StrategyValidationResponse | string {
  if (!isRecord(value) || value.schema !== "strategy_validation.v1") {
    return "validation 必須係 strategy_validation.v1";
  }
  if (
    typeof value.valid !== "boolean" ||
    !isNonNegativeInteger(value.issue_count) ||
    !Array.isArray(value.issues) ||
    typeof value.report_text !== "string"
  ) {
    return "validation 基本欄位 shape 無效";
  }
  const issues: StrategyValidationIssue[] = [];
  for (const item of value.issues) {
    if (
      !isRecord(item) ||
      typeof item.path !== "string" ||
      typeof item.message !== "string" ||
      typeof item.fix !== "string" ||
      typeof item.layer !== "string" ||
      typeof item.line !== "string"
    ) {
      return "validation.issues shape 無效";
    }
    issues.push({
      path: item.path,
      message: item.message,
      fix: item.fix,
      layer: item.layer,
      line: item.line,
    });
  }
  if (issues.length !== value.issue_count) {
    return "validation.issue_count 同 issues 數量唔一致";
  }
  if (value.valid !== (issues.length === 0)) {
    return "validation.valid 同 issues 互相矛盾";
  }

  let universe: StrategyValidationResponse["universe"];
  if (value.universe !== undefined) {
    if (!isRecord(value.universe)) {
      return "validation.universe shape 無效";
    }
    const contracts = parseStringArray(
      value.universe.contracts,
      "validation.universe.contracts",
    );
    if (
      typeof contracts === "string" ||
      !isNonBlankString(value.universe.session)
    ) {
      return "validation.universe contracts/session 無效";
    }
    universe = { contracts, session: value.universe.session };
  }
  if (value.name !== undefined && typeof value.name !== "string") {
    return "validation.name 必須係字串";
  }
  return {
    schema: value.schema,
    valid: value.valid,
    issue_count: value.issue_count,
    issues,
    report_text: value.report_text,
    ...(typeof value.name === "string" ? { name: value.name } : {}),
    ...(universe ? { universe } : {}),
  };
}

function parseFingerprint(
  value: unknown,
): PreviewEnvelopeBase["requestFingerprint"] | string {
  if (
    !isRecord(value) ||
    value.algorithm !== "sha256" ||
    typeof value.digest !== "string" ||
    !SHA256_HEX.test(value.digest)
  ) {
    return "request_fingerprint 必須係 sha256＋64位小寫 hex";
  }
  return { algorithm: "sha256", digest: value.digest };
}

function parseStringList(value: unknown, path: string): string[] | string {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    return `${path} 必須係字串陣列`;
  }
  return [...value] as string[];
}

function parseFunnel(value: unknown): PreviewFunnel | string {
  if (
    !isRecord(value) ||
    value.schema !== "funnel.v1" ||
    (value.status !== "ok" && value.status !== "insufficient_sample")
  ) {
    return "funnel 必須係 funnel.v1";
  }
  const countKeys = [
    "daily_trend_days",
    "evaluations_passing_daily_gate",
    "evaluations_passing_mid_gate",
    "signals_created",
    "fills",
  ] as const;
  for (const key of countKeys) {
    if (!isNonNegativeInteger(value[key])) {
      return `funnel.${key} 必須係非負整數`;
    }
  }
  const reasons = parseCountMap(value.reject_reasons, "funnel.reject_reasons");
  if (typeof reasons === "string") {
    return reasons;
  }
  if (typeof value.notes !== "string" || !isRecord(value.units)) {
    return "funnel notes/units shape 無效";
  }
  const units = value.units;
  for (const key of countKeys) {
    if (!isNonBlankString(units[key])) {
      return `funnel.units.${key} 必須係非空字串`;
    }
  }
  return {
    schema: "funnel.v1",
    status: value.status,
    daily_trend_days: value.daily_trend_days as number,
    evaluations_passing_daily_gate:
      value.evaluations_passing_daily_gate as number,
    evaluations_passing_mid_gate:
      value.evaluations_passing_mid_gate as number,
    signals_created: value.signals_created as number,
    fills: value.fills as number,
    reject_reasons: reasons,
    notes: value.notes,
    units: {
      daily_trend_days: units.daily_trend_days as string,
      evaluations_passing_daily_gate:
        units.evaluations_passing_daily_gate as string,
      evaluations_passing_mid_gate:
        units.evaluations_passing_mid_gate as string,
      signals_created: units.signals_created as string,
      fills: units.fills as string,
    },
  };
}

function parseEvidenceSummary(
  value: unknown,
): PreviewEvidenceSummary | string {
  if (
    !isRecord(value) ||
    (value.availability !== "available" &&
      value.availability !== "unavailable") ||
    typeof value.complete !== "boolean"
  ) {
    return "evidence_summary availability/complete 無效";
  }
  for (const key of [
    "evaluation_count",
    "rejection_count",
    "trade_count",
  ] as const) {
    if (!isNonNegativeInteger(value[key])) {
      return `evidence_summary.${key} 必須係非負整數`;
    }
  }
  if ((value.rejection_count as number) > (value.evaluation_count as number)) {
    return "evidence_summary rejection_count 大過 evaluation_count";
  }
  const layerCounts = parseCountMap(
    value.layer_reached_counts,
    "evidence_summary.layer_reached_counts",
  );
  const blockingCounts = parseCountMap(
    value.blocking_condition_counts,
    "evidence_summary.blocking_condition_counts",
  );
  if (typeof layerCounts === "string") {
    return layerCounts;
  }
  if (typeof blockingCounts === "string") {
    return blockingCounts;
  }
  if (
    value.deepest_layer !== null &&
    !isNonBlankString(value.deepest_layer)
  ) {
    return "evidence_summary.deepest_layer 必須係字串或 null";
  }
  return {
    availability: value.availability,
    complete: value.complete,
    evaluation_count: value.evaluation_count as number,
    rejection_count: value.rejection_count as number,
    layer_reached_counts: layerCounts,
    blocking_condition_counts: blockingCounts,
    deepest_layer: value.deepest_layer as string | null,
    trade_count: value.trade_count as number,
  };
}

function parseConditionFact(value: unknown): boolean {
  if (
    !isRecord(value) ||
    !isNonBlankString(value.condition_id) ||
    !isNonBlankString(value.layer_id) ||
    !isCanonicalUtc(value.observed_at) ||
    !["passed", "failed", "not_evaluated"].includes(
      String(value.status),
    ) ||
    !isPrimitive(value.actual) ||
    !isNonBlankString(value.operator) ||
    !isPrimitive(value.required) ||
    typeof value.unit !== "string" ||
    !Array.isArray(value.source_sequences) ||
    !value.source_sequences.every(isPositiveInteger)
  ) {
    return false;
  }
  if (
    value.status === "not_evaluated" &&
    value.source_sequences.length !== 0
  ) {
    return false;
  }
  return (
    value.status === "not_evaluated" ||
    value.source_sequences.length > 0
  );
}

function parseRejection(
  value: unknown,
): PreviewRejectionEvidence | string {
  if (
    !isRecord(value) ||
    !isNonBlankString(value.evidence_id) ||
    !isCanonicalUtc(value.timestamp) ||
    !isCanonicalUtc(value.ts_init) ||
    Date.parse(value.ts_init) < Date.parse(value.timestamp) ||
    !isRealDateLabel(value.trading_date) ||
    !isNonBlankString(value.direction) ||
    !isPositiveInteger(value.evaluation_sequence)
  ) {
    return "rejection_evidence identity/timestamp shape 無效";
  }
  const reached = parseStringArray(
    value.reached_layers,
    "rejection_evidence.reached_layers",
    { nonEmpty: true, unique: true },
  );
  const blocking = parseStringArray(
    value.blocking_condition_ids,
    "rejection_evidence.blocking_condition_ids",
    { nonEmpty: true, unique: true },
  );
  if (typeof reached === "string") {
    return reached;
  }
  if (typeof blocking === "string") {
    return blocking;
  }
  if (
    !Array.isArray(value.condition_facts) ||
    value.condition_facts.length === 0 ||
    !value.condition_facts.every(parseConditionFact)
  ) {
    return "rejection_evidence.condition_facts shape 無效";
  }
  const facts = value.condition_facts as Array<Record<string, unknown>>;
  const factsById = new Map(facts.map((fact) => [fact.condition_id, fact]));
  if (
    blocking.some((id) => {
      const fact = factsById.get(id);
      return !fact || fact.status !== "failed";
    })
  ) {
    return "blocking_condition_ids 必須指向 failed condition facts";
  }
  if (
    !isRecord(value.context) ||
    !Array.isArray(value.context.candidate_signal_kinds) ||
    value.context.candidate_signal_kinds.length === 0 ||
    !value.context.candidate_signal_kinds.every(isNonBlankString) ||
    !(
      value.context.inside_count === null ||
      isPositiveInteger(value.context.inside_count)
    ) ||
    !isNonBlankString(value.context.entry_pullback_state) ||
    !isNonBlankString(value.context.mid_pullback_state) ||
    !(
      value.context.daily_regime === null ||
      isNonBlankString(value.context.daily_regime)
    )
  ) {
    return "rejection_evidence.context shape 無效";
  }
  if (
    !Array.isArray(value.source_event_sequences) ||
    value.source_event_sequences.length === 0 ||
    !value.source_event_sequences.every(isPositiveInteger)
  ) {
    return "rejection_evidence.source_event_sequences shape 無效";
  }
  return {
    evidenceId: value.evidence_id,
    timestamp: value.timestamp,
    reachedLayers: reached,
    blockingConditionIds: blocking,
    raw: value,
  };
}

function parseTimeValue(
  value: unknown,
  path: string,
): number | string {
  if (!isNonNegativeInteger(value)) {
    return `${path}.time 必須係非負 unix 秒整數`;
  }
  return value;
}

function parseChart(
  value: unknown,
  timeframe: PreviewTimeframe,
  expected: ExpectedPreviewIdentity,
): PreviewChartSeries | string {
  if (
    !isRecord(value) ||
    value.schema !== "chart_series.v1" ||
    value.timeframe !== timeframe ||
    value.contract_id !== expected.contractId ||
    value.session_name !== expected.sessionName ||
    typeof value.data_fingerprint !== "string" ||
    !SHA256_HEX.test(value.data_fingerprint) ||
    !isPositiveInteger(value.lookback_days) ||
    !isCanonicalUtc(value.visible_start) ||
    !isCanonicalUtc(value.visible_end) ||
    value.visible_start >= value.visible_end ||
    !isNonBlankString(value.source) ||
    value.persisted !== false ||
    Object.prototype.hasOwnProperty.call(value, "sidecar_relpath")
  ) {
    return `charts.${timeframe} identity/range/persistence shape 無效`;
  }
  if (!Array.isArray(value.candles)) {
    return `charts.${timeframe}.candles 必須係陣列`;
  }
  const candles: PreviewChartSeries["candles"] = [];
  for (const candle of value.candles) {
    if (!isRecord(candle)) {
      return `charts.${timeframe}.candles shape 無效`;
    }
    const time = parseTimeValue(candle.time, `charts.${timeframe}.candles`);
    if (
      typeof time === "string" ||
      !isFiniteNumber(candle.open) ||
      !isFiniteNumber(candle.high) ||
      !isFiniteNumber(candle.low) ||
      !isFiniteNumber(candle.close) ||
      candle.high < Math.max(candle.open, candle.close, candle.low) ||
      candle.low > Math.min(candle.open, candle.close, candle.high)
    ) {
      return `charts.${timeframe}.candles OHLC/time 無效`;
    }
    candles.push({
      time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    });
  }

  const parseLine = (
    raw: unknown,
    key: string,
  ): Array<{ time: number; value: number }> | string => {
    if (!Array.isArray(raw)) {
      return `charts.${timeframe}.${key} 必須係陣列`;
    }
    const points: Array<{ time: number; value: number }> = [];
    for (const point of raw) {
      if (!isRecord(point)) {
        return `charts.${timeframe}.${key} shape 無效`;
      }
      const time = parseTimeValue(point.time, `charts.${timeframe}.${key}`);
      if (typeof time === "string" || !isFiniteNumber(point.value)) {
        return `charts.${timeframe}.${key} time/value 無效`;
      }
      points.push({ time, value: point.value });
    }
    return points;
  };
  const ema18 = parseLine(value.ema18, "ema18");
  const ema50 = parseLine(value.ema50, "ema50");
  const ema90 = parseLine(value.ema90, "ema90");
  if (typeof ema18 === "string") return ema18;
  if (typeof ema50 === "string") return ema50;
  if (typeof ema90 === "string") return ema90;

  if (!Array.isArray(value.markers)) {
    return `charts.${timeframe}.markers 必須係陣列`;
  }
  const markers: PreviewChartSeries["markers"] = [];
  for (const marker of value.markers) {
    if (!isRecord(marker)) {
      return `charts.${timeframe}.markers shape 無效`;
    }
    const time = parseTimeValue(marker.time, `charts.${timeframe}.markers`);
    if (
      typeof time === "string" ||
      typeof marker.position !== "string" ||
      !MARKER_POSITIONS.has(marker.position) ||
      typeof marker.shape !== "string" ||
      !MARKER_SHAPES.has(marker.shape) ||
      !isNonBlankString(marker.token) ||
      typeof marker.text !== "string"
    ) {
      return `charts.${timeframe}.markers 欄位無效`;
    }
    markers.push({
      time,
      position: marker.position as PreviewChartSeries["markers"][number]["position"],
      shape: marker.shape as PreviewChartSeries["markers"][number]["shape"],
      token: marker.token,
      text: marker.text,
    });
  }

  if (!Array.isArray(value.levels)) {
    return `charts.${timeframe}.levels 必須係陣列`;
  }
  const levels: PreviewChartSeries["levels"] = [];
  for (const level of value.levels) {
    if (
      !isRecord(level) ||
      !isNonBlankString(level.kind) ||
      !isFiniteNumber(level.price) ||
      !(
        level.time === null ||
        isNonNegativeInteger(level.time)
      ) ||
      !isNonBlankString(level.token)
    ) {
      return `charts.${timeframe}.levels 欄位無效`;
    }
    levels.push({
      kind: level.kind,
      price: level.price,
      time: level.time,
      token: level.token,
    });
  }
  return {
    schema: "chart_series.v1",
    timeframe,
    contractId: value.contract_id,
    sessionName: value.session_name,
    dataFingerprint: value.data_fingerprint,
    lookbackDays: value.lookback_days,
    visibleStart: value.visible_start,
    visibleEnd: value.visible_end,
    candles,
    ema18,
    ema50,
    ema90,
    markers,
    levels,
    source: value.source,
    persisted: false,
  };
}

function parseBase(
  body: Record<string, unknown>,
): PreviewEnvelopeBase | string {
  if (
    body.schema !== "backtest_preview.v1" ||
    body.run_scope !== "dry_run"
  ) {
    return "response 必須係 backtest_preview.v1 dry_run";
  }
  const identityError = dryRunIdentityError(body);
  if (identityError) {
    return identityError;
  }
  const fingerprint = parseFingerprint(body.request_fingerprint);
  const validation = parseValidation(body.validation);
  const summary = parseEvidenceSummary(body.evidence_summary);
  const warnings = parseStringList(body.warnings, "warnings");
  const errors = parseStringList(body.errors, "errors");
  if (typeof fingerprint === "string") return fingerprint;
  if (typeof validation === "string") return validation;
  if (typeof summary === "string") return summary;
  if (typeof warnings === "string") return warnings;
  if (typeof errors === "string") return errors;
  return {
    schema: "backtest_preview.v1",
    runScope: "dry_run",
    requestFingerprint: fingerprint,
    validation,
    evidenceSummary: summary,
    persisted: false,
    warnings,
    errors,
  };
}

export function parsePreviewEnvelope(
  status: number,
  body: unknown,
  expected: ExpectedPreviewIdentity,
): PreviewEnvelopeResult {
  if (status !== 200 && status !== 422) {
    return invalid(`HTTP ${status} 唔係 preview 合約成功／完整 422`);
  }
  if (!isRecord(body)) {
    return invalid("preview response 唔係 JSON object");
  }
  const base = parseBase(body);
  if (typeof base === "string") {
    return invalid(base);
  }

  if (status === 422) {
    if (
      body.funnel !== null ||
      !Array.isArray(body.decision_evidence) ||
      body.decision_evidence.length !== 0 ||
      !Array.isArray(body.rejection_evidence) ||
      body.rejection_evidence.length !== 0 ||
      !isRecord(body.charts) ||
      Object.keys(body.charts).length !== 0 ||
      base.errors.length === 0 ||
      base.evidenceSummary.availability !== "unavailable" ||
      base.evidenceSummary.complete !== false ||
      base.evidenceSummary.evaluation_count !== 0 ||
      base.evidenceSummary.rejection_count !== 0 ||
      base.evidenceSummary.trade_count !== 0 ||
      Object.keys(base.evidenceSummary.layer_reached_counts).length !== 0 ||
      Object.keys(base.evidenceSummary.blocking_condition_counts).length !== 0 ||
      base.evidenceSummary.deepest_layer !== null
    ) {
      return invalid("422 必須保留完整 fail-closed preview envelope");
    }
    return {
      ok: true,
      value: {
        ...base,
        kind: "failure",
        funnel: null,
        decisionEvidence: [],
        rejectionEvidence: [],
        charts: {},
      },
    };
  }

  if (
    !base.validation.valid ||
    base.errors.length !== 0 ||
    base.evidenceSummary.availability !== "available" ||
    base.evidenceSummary.complete !== true
  ) {
    return invalid("HTTP 200 必須係 valid、complete、無 errors 嘅 dry-run");
  }
  const funnel = parseFunnel(body.funnel);
  if (typeof funnel === "string") {
    return invalid(funnel);
  }
  if (!Array.isArray(body.decision_evidence)) {
    return invalid("decision_evidence 必須係陣列");
  }
  const decisions: Record<string, unknown>[] = [];
  for (const decision of body.decision_evidence) {
    if (!isRecord(decision) || !isJsonValue(decision)) {
      return invalid("decision_evidence shape／finite value 無效");
    }
    decisions.push(decision);
  }
  if (!Array.isArray(body.rejection_evidence)) {
    return invalid("rejection_evidence 必須係陣列");
  }
  const rejections: PreviewRejectionEvidence[] = [];
  for (const item of body.rejection_evidence) {
    const parsed = parseRejection(item);
    if (typeof parsed === "string") {
      return invalid(parsed);
    }
    rejections.push(parsed);
  }
  if (base.evidenceSummary.rejection_count !== rejections.length) {
    return invalid("complete evidence summary 同 rejection records 數量唔一致");
  }
  if (base.evidenceSummary.trade_count !== funnel.fills) {
    return invalid("evidence trade_count 同 funnel fills 唔一致");
  }
  if (!isRecord(body.charts)) {
    return invalid("charts 必須係 object");
  }
  const chartKeys = Object.keys(body.charts).sort();
  const expectedKeys = [...PREVIEW_TIMEFRAMES].sort();
  if (
    chartKeys.length !== expectedKeys.length ||
    chartKeys.some((key, index) => key !== expectedKeys[index])
  ) {
    return invalid("charts 必須 exact D／1H／30m／5m 四格");
  }
  const charts = {} as Record<PreviewTimeframe, PreviewChartSeries>;
  for (const timeframe of PREVIEW_TIMEFRAMES) {
    const chart = parseChart(body.charts[timeframe], timeframe, expected);
    if (typeof chart === "string") {
      return invalid(chart);
    }
    charts[timeframe] = chart;
  }
  return {
    ok: true,
    value: {
      ...base,
      kind: "success",
      validation: base.validation as StrategyValidationResponse & {
        valid: true;
      },
      funnel,
      decisionEvidence: decisions,
      rejectionEvidence: rejections,
      charts,
    },
  };
}

function isValidLocalDatetime(value: string): boolean {
  const match = DATETIME_LOCAL.exec(value);
  if (!match) {
    return false;
  }
  const [, yearText, monthText, dayText, hourText, minuteText, secondText] =
    match;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return false;
  }
  return (
    parsed.getFullYear() === Number(yearText) &&
    parsed.getMonth() + 1 === Number(monthText) &&
    parsed.getDate() === Number(dayText) &&
    parsed.getHours() === Number(hourText) &&
    parsed.getMinutes() === Number(minuteText) &&
    parsed.getSeconds() === Number(secondText ?? "0")
  );
}

export function buildPreviewRequest(
  input: PreviewRequestInput,
): PreviewRequestResult {
  if (!input.sourceText.trim()) {
    return invalid("策略原文未填");
  }
  if (!input.contractId) {
    return invalid("要揀一個 exact contract");
  }
  if (
    !isValidLocalDatetime(input.rangeStartLocal) ||
    !isValidLocalDatetime(input.rangeEndLocal)
  ) {
    return invalid("開始／結束時間要由 Owner 明確揀");
  }
  const rangeStart = localDatetimeToUtcIso(input.rangeStartLocal);
  const rangeEnd = localDatetimeToUtcIso(input.rangeEndLocal);
  if (!rangeStart || !rangeEnd || rangeStart >= rangeEnd) {
    return invalid("開始時間必須早過結束時間");
  }
  if (!input.commissionText.trim()) {
    return invalid("每邊佣金要由 Owner 明確填");
  }
  const commission = Number(input.commissionText);
  if (!Number.isFinite(commission) || commission < 0) {
    return invalid("每邊佣金必須係有限、非負數");
  }
  if (!input.slippageText.trim()) {
    return invalid("滑點 ticks 要由 Owner 明確填");
  }
  const slippage = Number(input.slippageText);
  if (!Number.isInteger(slippage) || slippage < 0) {
    return invalid("滑點 ticks 必須係非負整數");
  }
  return {
    ok: true,
    request: {
      schema: "backtest_preview_request.v1",
      source_text: input.sourceText,
      filename: input.filename,
      contract_id: input.contractId,
      range_start: rangeStart,
      range_end: rangeEnd,
      assumptions: {
        initial_capital_usd: 100_000,
        commission_per_side: commission,
        slippage_ticks: slippage,
      },
    },
  };
}

export function eligiblePreviewContracts(
  universe: StrategyUniverse | null,
  rows: InstrumentCatalogRow[] | null,
): InstrumentCatalogRow[] {
  if (!universe || !rows) {
    return [];
  }
  const members = new Set(universe.contracts);
  return rows.filter(
    (row) =>
      members.has(row.symbol) &&
      row.assetClass === universe.assetClass &&
      row.currency === "USD" &&
      row.sessionsAvailable.includes(universe.session),
  );
}

export function primaryPreviewContractId(
  universe: StrategyUniverse | null,
  eligible: InstrumentCatalogRow[],
): string {
  if (!universe) {
    return "";
  }
  const primary = eligible.filter(
    (row) => row.symbol === universe.primaryInstrument,
  );
  return primary.length === 1 ? primary[0].contractId : "";
}

function funnelFactsAreConsistent(funnel: PreviewFunnel): boolean {
  const {
    daily_trend_days: daily,
    evaluations_passing_daily_gate: dailyEvaluations,
    evaluations_passing_mid_gate: midEvaluations,
    signals_created: signals,
    fills,
  } = funnel;
  if (
    midEvaluations > dailyEvaluations ||
    signals > midEvaluations ||
    fills > signals
  ) {
    return false;
  }
  if (
    daily === 0 &&
    (dailyEvaluations > 0 || midEvaluations > 0 || signals > 0 || fills > 0)
  ) {
    return false;
  }
  return true;
}

export function interpretPreviewFunnel(funnel: PreviewFunnel): string {
  if (!funnelFactsAreConsistent(funnel)) {
    return "暫時解讀唔到：漏斗數字互相矛盾，系統唔會猜。";
  }
  if (funnel.daily_trend_days === 0) {
    return "Daily 市況閘係最早可見阻位：今次 0 個交易日通過。";
  }
  if (funnel.evaluations_passing_mid_gate === 0) {
    return "Daily 有通過日，但中層閘未有 5m 評估通過。";
  }
  if (funnel.signals_created === 0) {
    return "中層閘有評估通過，但入市訊號層未形成訊號。";
  }
  if (funnel.fills === 0) {
    return `已形成 ${funnel.signals_created} 次入市訊號，但成交／執行層未形成成交。`;
  }
  return `今次形成 ${funnel.fills} 筆成交。`;
}

function rejectionTimeframe(
  rejection: PreviewRejectionEvidence,
): PreviewTimeframe | null {
  const ids = rejection.blockingConditionIds;
  if (ids.some((id) => id.startsWith("daily_"))) return "D";
  if (ids.some((id) => id.startsWith("mid_"))) return "1H";
  if (
    ids.some(
      (id) =>
        id.startsWith("entry_") ||
        id.startsWith("signal_") ||
        id.startsWith("execution_"),
    )
  ) {
    return "5m";
  }
  for (const layer of [...rejection.reachedLayers].reverse()) {
    if (layer === "daily") return "D";
    if (layer === "mid") return "1H";
    if (layer === "entry" || layer === "execution") return "5m";
  }
  return null;
}

const ROLE_LABELS: Record<PreviewTimeframe, string> = {
  D: "大框架",
  "1H": "中框架",
  "30m": "輔助",
  "5m": "入市",
};

export function previewToChartPanes(
  preview: PreviewSuccess,
): ChartPaneData[] {
  const annotations: Record<
    PreviewTimeframe,
    Array<{ time: number; label: string }>
  > = { D: [], "1H": [], "30m": [], "5m": [] };
  for (const rejection of preview.rejectionEvidence) {
    const timeframe = rejectionTimeframe(rejection);
    if (!timeframe) {
      continue;
    }
    annotations[timeframe].push({
      time: Math.floor(Date.parse(rejection.timestamp) / 1000),
      label: rejection.blockingConditionIds.join("、"),
    });
  }

  return PREVIEW_TIMEFRAMES.map((timeframe) => {
    const chart = preview.charts[timeframe];
    const rejects = annotations[timeframe];
    return {
      timeframe,
      roleLabel: ROLE_LABELS[timeframe],
      available: chart.candles.length > 0,
      ...(chart.candles.length === 0
        ? { unavailableReason: "Backend 呢格冇 candle；系統冇畫假資料。" }
        : {}),
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
      verticalAnnotations: rejects,
      rejectMarkers: rejects.map((item) => ({
        time: item.time,
        text: item.label,
      })),
    };
  });
}
