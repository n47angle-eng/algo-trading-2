import { isPaperReviewSnapshotId } from "../lib/paper/reviewDownload";
import {
  isPaperReviewRequestId,
  isSafePaperTraderId,
} from "../lib/paper/reviewState";
import {
  getCachedGet,
  invalidateGetCache,
  setCachedGet,
} from "../lib/httpCache";
// Every request reports its outcome so the toast + offline layers know whether
// the backend is actually answering. Behaviour is otherwise identical to fetch.
import { trackedFetch } from "../lib/net/transport";
import type {
  ChartSeriesResponse,
  ChartShadowCompareResponse,
  ChartTimeframe,
  NarrativeResponse,
} from "./chartTypes";
import type {
  ComputeErrorList,
  ComputeErrorRecord,
  ComputeStatus,
} from "./computeStatus";
import type {
  BatchListResponse,
  EventsResponse,
  ResultDocument,
  RunListResponse,
  TradesResponse,
} from "./types";
import type { P4StandardRequest } from "../lib/backtest/liveContract";
import type { BacktestPreviewRequest } from "../lib/preview/contract";

export type { ComputeErrorList, ComputeErrorRecord, ComputeStatus };

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

/**
 * Hot GET paths cached in-memory (short TTL).
 * Prefix match — keep list conservative; mutations must call invalidateGetCache.
 */
const JSON_GET_CACHE_ALLOW = [
  "/api/v1/system/",
  "/api/v1/data/coverage",
  "/api/v1/runs",
  "/api/v1/batches",
  "/api/v1/strategies",
  "/api/v1/sketches",
  "/api/v1/paper/readiness",
  "/api/v1/paper/provisioning-readiness",
  "/api/v1/paper/contracts",
  "/api/v1/paper/baselines",
  "/api/v1/paper/traders",
];

/** Catalog / status probes stay a bit longer to cut tunnel round-trips. */
const LONG_TTL_PREFIXES = [
  "/api/v1/system/",
  "/api/v1/data/coverage",
  "/api/v1/paper/contracts",
  "/api/v1/paper/baselines",
];

function cacheTtlFor(path: string, override?: number): number {
  if (override !== undefined) return override;
  if (LONG_TTL_PREFIXES.some((p) => path === p || path.startsWith(p))) {
    return 30_000;
  }
  return 12_000;
}

async function getJson<T>(path: string, ttlMs?: number): Promise<T> {
  const mayCache = JSON_GET_CACHE_ALLOW.some(
    (p) => path === p || path.startsWith(p),
  );
  if (mayCache) {
    const cached = getCachedGet(path);
    if (cached && cached.status >= 200 && cached.status < 300) {
      return cached.body as T;
    }
  }
  const response = await trackedFetch(`${API_BASE}${path}`);
  const rawText = await response.text();
  if (!response.ok) {
    throw new Error(`${response.status} ${path}: ${rawText || response.statusText}`);
  }
  let body: unknown = null;
  try {
    body = JSON.parse(rawText) as unknown;
  } catch {
    throw new Error(`${path}: response is not JSON`);
  }
  if (mayCache) {
    setCachedGet(
      path,
      { status: response.status, body, rawText },
      cacheTtlFor(path, ttlMs),
    );
  }
  return body as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await trackedFetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${path}: ${detail || response.statusText}`);
  }
  return (await response.json()) as T;
}

export function fetchRuns(): Promise<RunListResponse> {
  return getJson<RunListResponse>("/api/v1/runs");
}

export function fetchBatches(): Promise<BatchListResponse> {
  return getJson<BatchListResponse>("/api/v1/batches");
}

export function fetchBatchRuns(batchId: string): Promise<RunListResponse> {
  return getJson<RunListResponse>(`/api/v1/batches/${encodeURIComponent(batchId)}/runs`);
}

export function fetchRun(runId: string): Promise<ResultDocument> {
  return getJson<ResultDocument>(`/api/v1/runs/${encodeURIComponent(runId)}`);
}

export function fetchRunTrades(runId: string): Promise<TradesResponse> {
  return getJson<TradesResponse>(`/api/v1/runs/${encodeURIComponent(runId)}/trades`);
}

export function fetchRunEvents(runId: string): Promise<EventsResponse> {
  return getJson<EventsResponse>(`/api/v1/runs/${encodeURIComponent(runId)}/events`);
}

export function fetchRunChart(
  runId: string,
  tf: ChartTimeframe,
  lookbackDays?: number,
): Promise<ChartSeriesResponse> {
  const params = new URLSearchParams({ tf });
  if (lookbackDays !== undefined) {
    params.set("lookback_days", String(lookbackDays));
  }
  return getJson<ChartSeriesResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/chart?${params.toString()}`,
  );
}

/** Phase F: same-window Python MTF vs Rust shadow diff report. */
export function fetchRunChartShadowCompare(
  runId: string,
  tf: ChartTimeframe,
  lookbackDays?: number,
): Promise<ChartShadowCompareResponse> {
  const params = new URLSearchParams({ tf });
  if (lookbackDays !== undefined) {
    params.set("lookback_days", String(lookbackDays));
  }
  return getJson<ChartShadowCompareResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/chart/shadow-compare?${params.toString()}`,
  );
}

export function fetchRunNarrative(runId: string): Promise<NarrativeResponse> {
  return getJson<NarrativeResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/narrative`,
  );
}

// --- P5 strict normal read seam ---

export interface P5HttpResult {
  path: string;
  status: number;
  ok: boolean;
  rawText: string;
  jsonParsed: boolean;
  body: unknown;
}

export interface P5ExportHttpResult {
  path: string;
  status: number;
  ok: boolean;
  contentType: string | null;
  contentDisposition: string | null;
  arrayBuffer: ArrayBuffer;
  bytes: Uint8Array;
  rawText: string;
}

export type P5PromotionDecision = "use" | "return" | "abandon";

export interface P5PromotionDecisionRequest {
  schema: "promotion_decision_request.v1";
  request_id: string;
  decision: P5PromotionDecision;
  reason: string;
}

export class P5TransportError extends Error {
  readonly path: string;
  readonly transportCause: unknown;

  constructor(path: string, cause: unknown) {
    super(
      `${path}: ${cause instanceof Error ? cause.message : String(cause)}`,
    );
    this.name = "P5TransportError";
    this.path = path;
    this.transportCause = cause;
  }
}

function isAbortError(error: unknown): boolean {
  return (
    error !== null &&
    typeof error === "object" &&
    "name" in error &&
    error.name === "AbortError"
  );
}

async function p5Get(
  path: string,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  try {
    const response = await trackedFetch(`${API_BASE}${path}`, {
      method: "GET",
      signal,
    });
    const rawText = await response.text();
    let body: unknown = null;
    let jsonParsed = false;
    try {
      body = JSON.parse(rawText) as unknown;
      jsonParsed = true;
    } catch {
      // Preserve the complete raw body and let the strict resource parser fail.
    }
    return {
      path,
      status: response.status,
      ok: response.ok,
      rawText,
      jsonParsed,
      body,
    };
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new P5TransportError(path, error);
  }
}

async function p5PostJson(
  path: string,
  body: P5PromotionDecisionRequest,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  try {
    const response = await trackedFetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    const rawText = await response.text();
    let parsedBody: unknown = null;
    let jsonParsed = false;
    try {
      parsedBody = JSON.parse(rawText) as unknown;
      jsonParsed = true;
    } catch {
      // Preserve the complete raw body for strict caller-side handling.
    }
    return {
      path,
      status: response.status,
      ok: response.ok,
      rawText,
      jsonParsed,
      body: parsedBody,
    };
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new P5TransportError(path, error);
  }
}

async function p5GetBinary(
  path: string,
  signal?: AbortSignal,
): Promise<P5ExportHttpResult> {
  try {
    const response = await trackedFetch(`${API_BASE}${path}`, {
      method: "GET",
      signal,
    });
    const arrayBuffer = await response.arrayBuffer();
    const bytes = new Uint8Array(arrayBuffer);
    return {
      path,
      status: response.status,
      ok: response.ok,
      contentType: response.headers.get("Content-Type"),
      contentDisposition: response.headers.get("Content-Disposition"),
      arrayBuffer,
      bytes,
      rawText: response.ok ? "" : new TextDecoder().decode(bytes),
    };
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new P5TransportError(path, error);
  }
}

export function fetchP5Runs(signal?: AbortSignal): Promise<P5HttpResult> {
  return p5Get("/api/v1/runs", signal);
}

export function fetchP5ConfirmedStrategies(
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  return p5Get("/api/v1/strategies?status=confirmed", signal);
}

export function fetchP5Run(
  runId: string,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  return p5Get(`/api/v1/runs/${encodeURIComponent(runId)}`, signal);
}

export function fetchP5RunTrades(
  runId: string,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  return p5Get(
    `/api/v1/runs/${encodeURIComponent(runId)}/trades`,
    signal,
  );
}

export function fetchP5RunEvents(
  runId: string,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  return p5Get(
    `/api/v1/runs/${encodeURIComponent(runId)}/events`,
    signal,
  );
}

export function fetchP5RunChart(
  runId: string,
  tf: ChartTimeframe,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  return p5Get(
    `/api/v1/runs/${encodeURIComponent(runId)}/chart?tf=${encodeURIComponent(tf)}`,
    signal,
  );
}

/** Phase F value-gate report (same window Python MTF vs Rust). */
export function fetchP5RunChartShadowCompare(
  runId: string,
  tf: ChartTimeframe,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  return p5Get(
    `/api/v1/runs/${encodeURIComponent(runId)}/chart/shadow-compare?tf=${encodeURIComponent(tf)}`,
    signal,
  );
}

export function fetchP5RunNarrative(
  runId: string,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  return p5Get(
    `/api/v1/runs/${encodeURIComponent(runId)}/narrative`,
    signal,
  );
}

export function fetchP5RunExport(
  runId: string,
  signal?: AbortSignal,
): Promise<P5ExportHttpResult> {
  return p5GetBinary(
    `/api/v1/runs/${encodeURIComponent(runId)}/export`,
    signal,
  );
}

export function fetchP5RunPromotionDecisions(
  runId: string,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  return p5Get(
    `/api/v1/runs/${encodeURIComponent(runId)}/promotion-decisions`,
    signal,
  );
}

export function postP5RunPromotionDecision(
  runId: string,
  body: P5PromotionDecisionRequest,
  signal?: AbortSignal,
): Promise<P5HttpResult> {
  return p5PostJson(
    `/api/v1/runs/${encodeURIComponent(runId)}/promotion-decisions`,
    body,
    signal,
  );
}

// --- P2 normal live insight repository ---

export type InsightHttpMethod = "GET" | "POST" | "DELETE";

export interface InsightHttpResult {
  path: string;
  method: InsightHttpMethod;
  status: number;
  ok: boolean;
  rawText: string;
  jsonParsed: boolean;
  body: unknown;
}

export class InsightTransportError extends Error {
  readonly path: string;
  readonly method: InsightHttpMethod;
  readonly transportCause: unknown;

  constructor(path: string, method: InsightHttpMethod, cause: unknown) {
    super(
      `${method} ${path}: ${
        cause instanceof Error ? cause.message : String(cause)
      }`,
    );
    this.name = "InsightTransportError";
    this.path = path;
    this.method = method;
    this.transportCause = cause;
  }
}

async function insightRequest(
  path: string,
  method: InsightHttpMethod,
  requestBody: { source_text: string } | undefined,
  signal?: AbortSignal,
): Promise<InsightHttpResult> {
  const init: RequestInit = { method, signal };
  if (requestBody !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(requestBody);
  }
  try {
    const response = await trackedFetch(`${API_BASE}${path}`, init);
    const rawText = await response.text();
    let body: unknown = null;
    let jsonParsed = false;
    try {
      body = JSON.parse(rawText) as unknown;
      jsonParsed = true;
    } catch {
      // Preserve raw bytes-as-text; strict adapters reject the unknown body.
    }
    return {
      path,
      method,
      status: response.status,
      ok: response.ok,
      rawText,
      jsonParsed,
      body,
    };
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new InsightTransportError(path, method, error);
  }
}

export function fetchLiveInsights(
  signal?: AbortSignal,
): Promise<InsightHttpResult> {
  return insightRequest("/api/v1/insights", "GET", undefined, signal);
}

export function fetchLiveInsightDetail(
  origin: string,
  insightId: string,
  signal?: AbortSignal,
): Promise<InsightHttpResult> {
  return insightRequest(
    `/api/v1/insights/${encodeURIComponent(origin)}/${encodeURIComponent(
      insightId,
    )}`,
    "GET",
    undefined,
    signal,
  );
}

export function importLiveInsight(
  sourceText: string,
  signal?: AbortSignal,
): Promise<InsightHttpResult> {
  return insightRequest(
    "/api/v1/insights/import",
    "POST",
    { source_text: sourceText },
    signal,
  );
}

export function fetchLiveInsightArchives(
  signal?: AbortSignal,
): Promise<InsightHttpResult> {
  return insightRequest("/api/v1/insights/archives", "GET", undefined, signal);
}

export function archiveLiveInsight(
  origin: string,
  insightId: string,
  signal?: AbortSignal,
): Promise<InsightHttpResult> {
  return insightRequest(
    `/api/v1/insights/${encodeURIComponent(origin)}/${encodeURIComponent(
      insightId,
    )}`,
    "DELETE",
    undefined,
    signal,
  );
}

export function restoreLiveInsight(
  origin: string,
  insightId: string,
  archiveId: string,
  signal?: AbortSignal,
): Promise<InsightHttpResult> {
  return insightRequest(
    `/api/v1/insights/archives/${encodeURIComponent(
      origin,
    )}/${encodeURIComponent(insightId)}/${encodeURIComponent(
      archiveId,
    )}/restore`,
    "POST",
    undefined,
    signal,
  );
}

// --- P3 data ---

export interface CoverageTradingDayFacts {
  schema?: string;
  status: "known" | "unknown" | "error" | string;
  session_name?: string | null;
  first_trading_date?: string | null;
  last_trading_date?: string | null;
  trading_date_count?: number | null;
  complete_trading_date_count?: number | null;
  problem_trading_date_count?: number | null;
  pending_problem_trading_date_count?: number | null;
  owner_trusted_problem_trading_date_count?: number | null;
  owner_excluded_trading_date_count?: number | null;
  complete_trading_dates?: string[] | null;
  problem_trading_dates?: string[] | null;
  pending_problem_trading_dates?: string[] | null;
  owner_trusted_problem_trading_dates?: string[] | null;
  owner_excluded_trading_dates?: string[] | null;
  longest_complete_segment?: {
    start_trading_date?: string;
    end_trading_date?: string;
    start?: string;
    end?: string;
    trading_date_count: number;
  } | null;
  error_code?: string | null;
}

export interface CoverageNativeDailyFacts {
  schema?: string;
  status?: string;
  available_trading_date_count?: number | null;
  missing_trading_date_count?: number | null;
  [key: string]: unknown;
}

export interface CoverageContract {
  symbol: string;
  contract_id: string;
  display_name?: string;
  asset_class?: string;
  currency?: string;
  sessions_available?: string[];
  partition_count: number;
  bar_count: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  roll_blackout_dates: string[];
  owner_excluded_dates: string[];
  quality: {
    report_count: number;
    latest_error_count: number | null;
    latest_report_id: string | null;
  };
  /** Nested P3-A facts; absent on catalog view or when backend fails closed. */
  trading_day_coverage?: CoverageTradingDayFacts | null;
  native_daily_coverage?: CoverageNativeDailyFacts | null;
}

export function fetchDataCoverage(): Promise<{
  schema: string;
  count: number;
  contracts: CoverageContract[];
}> {
  return getJson("/api/v1/data/coverage");
}

/** P2 identity catalog fast path; never pay the P3 default/full scan cost. */
export function fetchInstrumentCatalog(): Promise<unknown> {
  return getJson<unknown>("/api/v1/data/coverage?view=catalog");
}

// --- P2 normal live preview ---

export interface PreviewHttpResult {
  status: number;
  body: unknown;
}

/**
 * Preview owns a complete HTTP 422 envelope, so it must not pass through
 * `postJson` (which converts every non-2xx response into a text-only error).
 */
export async function postBacktestPreview(
  body: BacktestPreviewRequest,
  signal?: AbortSignal,
): Promise<PreviewHttpResult> {
  const response = await trackedFetch(`${API_BASE}/api/v1/backtests/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  let responseBody: unknown = null;
  try {
    responseBody = await response.json();
  } catch {
    // null is passed to the runtime parser and therefore fails closed.
  }
  return { status: response.status, body: responseBody };
}

export function fetchQualityReports(contractId?: string): Promise<{
  schema: string;
  count: number;
  reports: Array<{
    report_id: string;
    contract_id: string;
    checked_at: string | null;
    total_bars: number | null;
    issue_count: number;
    error_count: number;
    path: string;
  }>;
}> {
  const q = contractId
    ? `?contract_id=${encodeURIComponent(contractId)}`
    : "";
  return getJson(`/api/v1/data/quality-reports${q}`);
}

export function postBlacklist(body: {
  contract_id: string;
  trading_date: string;
  decision: "exclude" | "trust";
  note?: string;
}): Promise<{ schema: string; entries: unknown[] }> {
  return postJson("/api/v1/data/blacklist", body);
}

export function postDownload(body: {
  symbol: string;
  start: string;
  end: string;
}): Promise<{ job_id: string; status: string; message?: string }> {
  return postJson("/api/v1/data/download", body);
}

// --- P4 batch jobs ---

export interface BatchJobRecord {
  schema: string;
  batch_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  request: Record<string, unknown>;
  jobs: Array<{
    job_id: string;
    run_id: string;
    symbol: string;
    strategy_version: string;
    status: string;
    message: string;
    started_at: string | null;
    finished_at: string | null;
    result_path: string | null;
    /** `strategy_file` once a confirmed A2 version drove the replay (6-5). */
    strategy_source: string | null;
    /** Non-fatal problems that must still surface (e.g. chart sidecar). */
    warnings: string[];
  }>;
  summary: {
    total: number;
    queued: number;
    running: number;
    completed: number;
    failed: number;
  };
}

export function submitBatch(body: {
  symbols: string[];
  strategy_versions: string[];
  range_start: string;
  range_end: string;
  session_name?: string;
  validation_run?: boolean;
  initial_capital?: number;
  /**
   * Override requests, not inputs: omit to use the strategy document's value.
   * Only a validation run may apply them (channel [083] Q1). Standard runs
   * must not send overrides (P4 design).
   */
  pullback_ema_period?: number;
  regime_separation_percentile?: number;
  regime_slope_percentile?: number;
  skip_nautilus_replay?: boolean;
}): Promise<BatchJobRecord> {
  return postJson("/api/v1/batches/submit", body);
}

/**
 * P4 normal-mode callers must inspect both HTTP status and runtime-validated
 * JSON.  In particular, submit 409 carries a precheck document rather than a
 * conventional error envelope.
 */
export interface P4HttpResult {
  status: number;
  ok: boolean;
  body: unknown;
}

async function p4JsonRequest(
  path: string,
  init: RequestInit,
): Promise<P4HttpResult> {
  const response = await trackedFetch(`${API_BASE}${path}`, init);
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // A non-JSON body is deliberately returned as null for fail-closed parsing.
  }
  return { status: response.status, ok: response.ok, body };
}

export function precheckP4Batch(
  body: P4StandardRequest,
  signal?: AbortSignal,
): Promise<P4HttpResult> {
  return p4JsonRequest("/api/v1/batches/precheck", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
}

export function submitP4Batch(
  body: P4StandardRequest,
  signal?: AbortSignal,
): Promise<P4HttpResult> {
  return p4JsonRequest("/api/v1/batches/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
}

export function fetchP4Batch(
  batchId: string,
  signal?: AbortSignal,
): Promise<P4HttpResult> {
  return p4JsonRequest(
    `/api/v1/batches/jobs/${encodeURIComponent(batchId)}`,
    { method: "GET", signal },
  );
}

export function fetchP4Batches(
  signal?: AbortSignal,
): Promise<P4HttpResult> {
  return p4JsonRequest("/api/v1/batches/jobs", {
    method: "GET",
    signal,
  });
}

export function cancelP4Queued(
  batchId: string,
  signal?: AbortSignal,
): Promise<P4HttpResult> {
  return p4JsonRequest(
    `/api/v1/batches/jobs/${encodeURIComponent(batchId)}/cancel-queued`,
    { method: "POST", signal },
  );
}

// --- P2 strategy library ---

export interface StrategyValidationIssue {
  path: string;
  message: string;
  fix: string;
  layer: string;
  line: string;
}

export interface StrategyValidationResponse {
  schema: string;
  valid: boolean;
  issue_count: number;
  issues: StrategyValidationIssue[];
  /** Verbatim `format_report()` text — what the Owner pastes back to the AI. */
  report_text: string;
  name?: string;
  universe?: { contracts: string[]; session: string };
}

export interface StrategyParameterRow {
  label: string;
  value: string;
  path: string | null;
  source: string | null;
  note: string | null;
  kind: "numeric" | "structure";
}

export interface StrategyVersion {
  schema: string;
  strategy_id: string;
  status: "draft" | "confirmed";
  name: string;
  created: string;
  imported_at: string;
  confirmed_at: string | null;
  content_sha256: string;
  source_text: string;
  spec_ref: string | null;
  based_on: string | null;
  based_on_sketch: string | null;
  /** Additive composite lineage (docs/05 §1.1a); null/omit for pre-v1.3 versions. */
  based_on_sketch_origin?: "workshop" | "journal-app" | null;
  based_on_insights: string[];
  rationale: string;
  unquantified_notes: Array<{ note: string; action_needed: string | null }>;
  universe: { contracts: string[]; session: string };
  parameters: StrategyParameterRow[];
  spec: {
    universe_session: string;
    regime_separation_percentile: number;
    regime_slope_percentile: number;
    pullback_ema_period: number;
    entry_layers: number;
    signal_bars: string[];
    target_r_multiple: number;
    stop_offset_ticks: number;
  };
}

// --- sketch.v1 origin-aware read (docs/05 §3.5 + backend StoredSketch.detail) ---

const SKETCH_ROLES = new Set(["bias", "mid", "auxiliary", "entry"]);
const CHART_SOURCES = new Set(["rendered", "screenshot"]);
const EXPECTED_CHART_FILES = [
  "chart-D.png",
  "chart-1H.png",
  "chart-30m.png",
  "chart-5m.png",
] as const;
const DATE_LABEL = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
const SKETCH_ID_RE = /^sketch-([0-9]{8})-([0-9]{2})$/;
const PERIOD_INDICATOR = /^(?:ema|sma|atr|rsi)[1-9][0-9]*$/;
const SINGLE_INDICATOR = /^(?:vwap|volume|macd)$/;
const BOLLINGER_INDICATOR = /^bb[1-9][0-9]*_([0-9]+(?:\.[0-9]+)?)$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;

export type SketchDetailRole = "bias" | "mid" | "auxiliary" | "entry";

export interface SketchDetailChart {
  file: string;
  timeframe: string;
  /** Optional in sketch.v1 — omitted is legal; null is not. */
  role?: SketchDetailRole;
  range?: [string, string];
  indicators_shown: string[];
  indicators_other?: string[];
  drawings?: Array<Record<string, unknown>>;
  owner_view: string;
}

export interface SketchDetailMeta {
  schema: "sketch.v1";
  sketch_id: string;
  kind: "strategy" | "insight";
  origin: "workshop" | "journal-app";
  chart_source: "rendered" | "screenshot";
  instructions_template: "instructions.v1";
  /**
   * Root product symbol. Omitted only for legacy both-omitted packages.
   * Current packages: non-blank string with no surrounding whitespace (exact).
   */
  instrument?: string;
  /**
   * Controlled catalog class. Omitted only for legacy both-omitted packages.
   * Current packages: exact enum equity_index_futures | commodity_futures (no trim).
   */
  asset_class?: string;
  created: string;
  title: string;
  rationale?: string;
  charts: SketchDetailChart[];
}

const SKETCH_ASSET_CLASSES = new Set([
  "equity_index_futures",
  "commodity_futures",
]);

export interface SketchDetailImage {
  file: string;
  url: string;
  content_type: string;
  byte_count: number;
  sha256: string;
}

export interface SketchDetailResponse {
  schema: "sketch_detail.v1";
  meta: SketchDetailMeta;
  instructions_markdown: string;
  images: SketchDetailImage[];
}

/** Fail-closed: HTTP or invalid payload. status 0 = invalid body. */
export class SketchFetchError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "SketchFetchError";
    this.status = status;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Optional means omitted — explicit null is always illegal. */
function hasExplicitNull(obj: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(obj, key) && obj[key] === null;
}

function isRealDateLabel(value: unknown): value is string {
  if (typeof value !== "string" || !DATE_LABEL.test(value)) {
    return false;
  }
  const [ys, ms, ds] = value.split("-");
  const y = Number(ys);
  const m = Number(ms);
  const d = Number(ds);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return (
    dt.getUTCFullYear() === y &&
    dt.getUTCMonth() === m - 1 &&
    dt.getUTCDate() === d
  );
}

function isCanonicalSketchId(value: unknown): value is string {
  if (typeof value !== "string" || value !== value.trim()) {
    return false;
  }
  const match = SKETCH_ID_RE.exec(value);
  if (!match) {
    return false;
  }
  const day = match[1];
  return isRealDateLabel(
    `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6, 8)}`,
  );
}

/** docs/05 / backend vocabulary — not the UI chip subset. */
export function isCanonicalIndicatorToken(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  if (PERIOD_INDICATOR.test(value) || SINGLE_INDICATOR.test(value)) {
    return true;
  }
  const bb = BOLLINGER_INDICATOR.exec(value);
  return bb != null && Number(bb[1]) > 0;
}

function parseChart(
  chart: unknown,
): { file: string } | null {
  if (!isPlainObject(chart)) {
    return null;
  }
  for (const key of [
    "role",
    "range",
    "indicators_other",
    "drawings",
  ] as const) {
    if (hasExplicitNull(chart, key)) {
      return null;
    }
  }
  if (typeof chart.file !== "string") {
    return null;
  }
  if (
    typeof chart.timeframe !== "string" ||
    chart.timeframe.trim() === ""
  ) {
    return null;
  }
  if (typeof chart.owner_view !== "string" || chart.owner_view.trim() === "") {
    return null;
  }
  if (!Array.isArray(chart.indicators_shown)) {
    return null;
  }
  const tokens = chart.indicators_shown;
  if (!tokens.every((t) => typeof t === "string")) {
    return null;
  }
  if (new Set(tokens).size !== tokens.length) {
    return null;
  }
  if (!tokens.every((t) => isCanonicalIndicatorToken(t))) {
    return null;
  }
  if (chart.role !== undefined) {
    if (typeof chart.role !== "string" || !SKETCH_ROLES.has(chart.role)) {
      return null;
    }
  }
  if (chart.range !== undefined) {
    if (
      !Array.isArray(chart.range) ||
      chart.range.length !== 2 ||
      !isRealDateLabel(chart.range[0]) ||
      !isRealDateLabel(chart.range[1]) ||
      chart.range[0] > chart.range[1]
    ) {
      return null;
    }
  }
  if (chart.indicators_other !== undefined) {
    if (
      !Array.isArray(chart.indicators_other) ||
      chart.indicators_other.length === 0 ||
      !chart.indicators_other.every(
        (t) => typeof t === "string" && t.trim() !== "",
      )
    ) {
      return null;
    }
  }
  if (chart.drawings !== undefined) {
    if (
      !Array.isArray(chart.drawings) ||
      !chart.drawings.every((d) => isPlainObject(d))
    ) {
      return null;
    }
  }
  return { file: chart.file };
}

function parseImage(img: unknown): { file: string } | null {
  if (!isPlainObject(img)) {
    return null;
  }
  if (
    typeof img.file !== "string" ||
    typeof img.url !== "string" ||
    img.url.trim() === "" ||
    typeof img.content_type !== "string" ||
    img.content_type !== "image/png" ||
    typeof img.byte_count !== "number" ||
    !Number.isInteger(img.byte_count) ||
    img.byte_count < 0 ||
    typeof img.sha256 !== "string" ||
    !SHA256_HEX.test(img.sha256)
  ) {
    return null;
  }
  return { file: img.file };
}

/**
 * Fail-closed parse of sketch_detail.v1 against docs/05 §3.5 + backend truth.
 * Response composite identity must exactly equal the request.
 * Does not normalize or rewrite the payload.
 */
export function parseSketchDetailResponse(
  body: unknown,
  expectedOrigin: string,
  expectedSketchId: string,
): SketchDetailResponse | null {
  if (!isPlainObject(body)) {
    return null;
  }
  if (body.schema !== "sketch_detail.v1") {
    return null;
  }
  if (typeof body.instructions_markdown !== "string") {
    return null;
  }
  if (!Array.isArray(body.images)) {
    return null;
  }
  if (!isPlainObject(body.meta)) {
    return null;
  }
  const m = body.meta;
  if (
    hasExplicitNull(m, "rationale") ||
    hasExplicitNull(m, "instrument") ||
    hasExplicitNull(m, "asset_class")
  ) {
    return null;
  }
  if (m.schema !== "sketch.v1") {
    return null;
  }
  if (typeof m.origin !== "string" || typeof m.sketch_id !== "string") {
    return null;
  }
  if (m.origin !== expectedOrigin || m.sketch_id !== expectedSketchId) {
    return null;
  }
  if (m.origin !== "workshop" && m.origin !== "journal-app") {
    return null;
  }
  if (!isCanonicalSketchId(m.sketch_id)) {
    return null;
  }
  if (m.kind !== "strategy" && m.kind !== "insight") {
    return null;
  }
  if (
    typeof m.chart_source !== "string" ||
    !CHART_SOURCES.has(m.chart_source)
  ) {
    return null;
  }
  if (m.instructions_template !== "instructions.v1") {
    return null;
  }
  // instrument + asset_class pair (D16/D21):
  // both omitted → legacy (do NOT insert keys or empty strings);
  // both present → exact non-blank, value===trim(value), enum exact (no normalize);
  // only one present / blank / surrounding whitespace / unknown → invalid.
  // Parser never mutates caller payload.
  const instKey = Object.prototype.hasOwnProperty.call(m, "instrument");
  const classKey = Object.prototype.hasOwnProperty.call(m, "asset_class");
  if (!instKey && !classKey) {
    // legacy: leave meta untouched
  } else if (instKey && classKey) {
    if (typeof m.instrument !== "string" || m.instrument === "") {
      return null;
    }
    if (typeof m.asset_class !== "string" || m.asset_class === "") {
      return null;
    }
    // surrounding whitespace is invalid — never trim-and-accept
    if (m.instrument !== m.instrument.trim()) {
      return null;
    }
    if (m.asset_class !== m.asset_class.trim()) {
      return null;
    }
    if (!SKETCH_ASSET_CLASSES.has(m.asset_class)) {
      return null;
    }
  } else {
    return null;
  }
  if (!isRealDateLabel(m.created)) {
    return null;
  }
  if (typeof m.title !== "string" || m.title.trim() === "") {
    return null;
  }
  if (m.kind === "strategy") {
    if (typeof m.rationale !== "string" || m.rationale.trim() === "") {
      return null;
    }
  } else if (m.rationale !== undefined) {
    if (typeof m.rationale !== "string" || m.rationale.trim() === "") {
      return null;
    }
  }
  if (!Array.isArray(m.charts) || m.charts.length !== 4) {
    return null;
  }
  const chartFiles: string[] = [];
  for (const chart of m.charts) {
    const parsed = parseChart(chart);
    if (!parsed) {
      return null;
    }
    chartFiles.push(parsed.file);
  }
  if (
    new Set(chartFiles).size !== 4 ||
    !EXPECTED_CHART_FILES.every((f) => chartFiles.includes(f))
  ) {
    return null;
  }
  const imageFiles: string[] = [];
  for (const img of body.images) {
    const parsed = parseImage(img);
    if (!parsed) {
      return null;
    }
    if (imageFiles.includes(parsed.file)) {
      return null;
    }
    imageFiles.push(parsed.file);
  }
  if (imageFiles.length !== 4) {
    return null;
  }
  const chartSet = new Set(chartFiles);
  for (const f of imageFiles) {
    if (!chartSet.has(f)) {
      return null;
    }
  }
  return body as unknown as SketchDetailResponse;
}

/** Prefix relative API paths with VITE_API_BASE; leave absolute / data URLs. */
export function resolveApiUrl(url: string): string {
  if (
    url.startsWith("http://") ||
    url.startsWith("https://") ||
    url.startsWith("data:") ||
    url.startsWith("blob:")
  ) {
    return url;
  }
  return `${API_BASE}${url}`;
}

/**
 * GET /api/v1/sketches/{origin}/{sketch_id}
 * Origin + id each encodeURIComponent. Does not POST.
 */
export async function fetchSketchDetail(
  origin: string,
  sketchId: string,
): Promise<SketchDetailResponse> {
  const path =
    `/api/v1/sketches/${encodeURIComponent(origin)}/` +
    `${encodeURIComponent(sketchId)}`;
  const response = await trackedFetch(`${API_BASE}${path}`);
  if (!response.ok) {
    const detail = await response.text();
    throw new SketchFetchError(
      response.status,
      detail || response.statusText,
    );
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new SketchFetchError(0, "sketch_detail response is not JSON");
  }
  const parsed = parseSketchDetailResponse(body, origin, sketchId);
  if (!parsed) {
    throw new SketchFetchError(0, "invalid sketch_detail.v1 payload");
  }
  return parsed;
}

export function validateStrategy(
  sourceText: string,
): Promise<StrategyValidationResponse> {
  return postJson("/api/v1/strategies/validate", { source_text: sourceText });
}

export interface StrategyImportResponse {
  schema: string;
  deduplicated: boolean;
  message: string;
  version: StrategyVersion;
}

export async function importStrategy(
  sourceText: string,
  filename?: string,
): Promise<StrategyImportResponse> {
  const response = await trackedFetch(`${API_BASE}/api/v1/strategies/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_text: sourceText, filename }),
  });
  if (response.status === 422) {
    // Validation failure carries the full paste-back report in `detail`.
    const body = (await response.json()) as {
      detail: StrategyValidationResponse;
    };
    throw new StrategyImportValidationError(body.detail);
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} import: ${detail || response.statusText}`);
  }
  return (await response.json()) as StrategyImportResponse;
}

/** Thrown when import is rejected by the four-layer validator. */
export class StrategyImportValidationError extends Error {
  readonly report: StrategyValidationResponse;

  constructor(report: StrategyValidationResponse) {
    super(`strategy validation failed with ${report.issue_count} issue(s)`);
    this.name = "StrategyImportValidationError";
    this.report = report;
  }
}

// --- P2 tab ③ version lifecycle (backend e5dd179) ---

/**
 * Same status + parsed-body envelope as the frozen P4 seam.  These operations
 * must inspect HTTP status and validated JSON separately: delete 409 carries a
 * structured block document and derive 422 carries the paste-back report.
 */
const lifecycleRequest = p4JsonRequest;

export function fetchStrategyRunReferences(
  strategyVersion: string,
  signal?: AbortSignal,
): Promise<P4HttpResult> {
  const params = new URLSearchParams({
    mode: "strategy",
    strategy_version: strategyVersion,
  });
  return lifecycleRequest(`/api/v1/run-references?${params.toString()}`, {
    method: "GET",
    signal,
  });
}

export interface StrategyNumericPatch {
  path: string;
  value: number;
}

export function deriveStrategyVersion(
  parentStrategyId: string,
  patches: StrategyNumericPatch[],
  signal?: AbortSignal,
): Promise<P4HttpResult> {
  return lifecycleRequest(
    `/api/v1/strategies/${encodeURIComponent(parentStrategyId)}/derive`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patches }),
      signal,
    },
  );
}

/**
 * Final delete after the frontend grace period. `keepalive` lets an unmount or
 * page-close flush still reach the server — leaving the page counts as confirm
 * (P2 #21), so silently dropping the request would be a lie.
 */
export function deleteStrategyVersion(
  strategyId: string,
  keepalive = false,
): Promise<P4HttpResult> {
  return lifecycleRequest(
    `/api/v1/strategies/${encodeURIComponent(strategyId)}`,
    { method: "DELETE", keepalive },
  );
}

export function fetchStrategies(
  status?: "draft" | "confirmed",
): Promise<{ schema: string; count: number; versions: StrategyVersion[] }> {
  const query = status ? `?status=${status}` : "";
  return getJson(`/api/v1/strategies${query}`);
}

export function fetchStrategy(strategyId: string): Promise<StrategyVersion> {
  return getJson(`/api/v1/strategies/${encodeURIComponent(strategyId)}`);
}

export function confirmStrategy(strategyId: string): Promise<StrategyVersion> {
  return postJson(
    `/api/v1/strategies/${encodeURIComponent(strategyId)}/confirm`,
    {},
  );
}

// --- P1 overview ---

export type IbStatusState =
  | "unconfigured"
  | "port_unreachable"
  | "port_reachable_unverified";

export interface IbStatus {
  schema: string;
  state: IbStatusState;
  host: string | null;
  port: number | null;
  detail: string;
  probe_timeout_seconds: number;
  checked_at: string;
}

export function fetchIbStatus(): Promise<IbStatus> {
  return getJson("/api/v1/system/ib-status");
}

export function fetchComputeStatus(): Promise<ComputeStatus> {
  return getJson<ComputeStatus>("/api/v1/system/compute-status");
}

export function fetchComputeErrors(limit = 20): Promise<ComputeErrorList> {
  return getJson<ComputeErrorList>(
    `/api/v1/system/compute-errors?limit=${encodeURIComponent(String(limit))}`,
  );
}

export function fetchComputeErrorDetail(
  errorId: string,
): Promise<ComputeErrorRecord> {
  return getJson<ComputeErrorRecord>(
    `/api/v1/system/compute-errors/${encodeURIComponent(errorId)}`,
  );
}

export function fetchBatchJob(batchId: string): Promise<BatchJobRecord> {
  return getJson(`/api/v1/batches/jobs/${encodeURIComponent(batchId)}`);
}

export function fetchBatchJobs(): Promise<{
  schema: string;
  count: number;
  batches: BatchJobRecord[];
}> {
  return getJson("/api/v1/batches/jobs");
}

// --- P6 Stage A isolated trader creation ---

export type PaperHttpMethod = "GET" | "POST";

export interface PaperHttpResult {
  path: string;
  method: PaperHttpMethod;
  status: number;
  ok: boolean;
  rawText: string;
  jsonParsed: boolean;
  body: unknown;
}

export class PaperTransportError extends Error {
  readonly path: string;
  readonly method: PaperHttpMethod;
  readonly transportCause: unknown;

  constructor(path: string, method: PaperHttpMethod, cause: unknown) {
    super(
      `${method} ${path}: ${
        cause instanceof Error ? cause.message : String(cause)
      }`,
    );
    this.name = "PaperTransportError";
    this.path = path;
    this.method = method;
    this.transportCause = cause;
  }
}

/** Only cache stable, high-churn-safe probes — never identity-sensitive GETs. */
const PAPER_GET_CACHE_ALLOW = [
  "/api/v1/paper/runtime-capabilities",
  "/api/v1/paper/runtime/gateway-status",
  "/api/v1/paper/fleet-overview",
];

function paperGetCacheable(path: string): boolean {
  return PAPER_GET_CACHE_ALLOW.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}?`),
  );
}

async function paperRequest(
  path: string,
  method: PaperHttpMethod,
  requestBody: unknown,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (method === "GET" && !signal && paperGetCacheable(path)) {
    const cached = getCachedGet(path);
    if (cached) {
      return {
        path,
        method,
        status: cached.status,
        ok: cached.status >= 200 && cached.status < 300,
        rawText: cached.rawText,
        jsonParsed: true,
        body: cached.body,
      };
    }
  }
  const init: RequestInit = { method, signal };
  if (requestBody !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(requestBody);
  }
  try {
    const response = await trackedFetch(`${API_BASE}${path}`, init);
    const rawText = await response.text();
    let body: unknown = null;
    let jsonParsed = false;
    try {
      body = JSON.parse(rawText) as unknown;
      jsonParsed = true;
    } catch {
      // Keep the complete raw body; the strict parser rejects the unknown one.
    }
    if (method === "GET" && paperGetCacheable(path)) {
      setCachedGet(path, {
        status: response.status,
        body,
        rawText,
      });
    } else if (method !== "GET") {
      // Mutations invalidate fleet/gateway probe caches.
      invalidateGetCache("/api/v1/paper/fleet-overview");
      invalidateGetCache("/api/v1/paper/runtime/gateway-status");
    }
    return {
      path,
      method,
      status: response.status,
      ok: response.ok,
      rawText,
      jsonParsed,
      body,
    };
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new PaperTransportError(path, method, error);
  }
}

/**
 * @deprecated Page independence (docs/10): paper UI loads confirmed strategies
 * via ``fetchStrategies("confirmed")``. This endpoint still returns strategies
 * that ever received a PromotionDecision ``use`` — history only, not a gate.
 */
export function fetchPaperEligibleStrategies(
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest(
    "/api/v1/promotion-decisions/eligible-strategies",
    "GET",
    undefined,
    signal,
  );
}

/**
 * Approach C: the only authoritative contract producer. Normal P6 never
 * derives contracts from the coverage catalog or the general run list.
 */
export function fetchPaperContracts(
  selection: { strategy_id: string; content_sha256: string },
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  const params = new URLSearchParams({
    strategy_id: selection.strategy_id,
    content_sha256: selection.content_sha256,
  });
  return paperRequest(
    `/api/v1/paper/contracts?${params.toString()}`,
    "GET",
    undefined,
    signal,
  );
}

export function fetchPaperBaselines(
  selection: {
    strategy_id: string;
    content_sha256: string;
    contract_id: string;
  },
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  const params = new URLSearchParams({
    strategy_id: selection.strategy_id,
    content_sha256: selection.content_sha256,
    contract_id: selection.contract_id,
  });
  return paperRequest(
    `/api/v1/paper/baselines?${params.toString()}`,
    "GET",
    undefined,
    signal,
  );
}

export function postPaperReadiness(
  body: unknown,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest("/api/v1/paper/readiness", "POST", body, signal);
}

/**
 * Option A §7.1: the additive provisioning preflight. The body is exactly
 * `schema` plus `selection` — no request id, because the browser only mints a
 * create identity at final confirm. No auto retry lives here either.
 */
export function postPaperProvisioningReadiness(
  body: unknown,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest(
    "/api/v1/paper/provisioning-readiness",
    "POST",
    body,
    signal,
  );
}

export function postPaperTrader(
  body: unknown,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest("/api/v1/paper/traders", "POST", body, signal);
}

export function fetchPaperTraderRequest(
  requestId: string,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest(
    `/api/v1/paper/trader-requests/${encodeURIComponent(requestId)}`,
    "GET",
    undefined,
    signal,
  );
}

export function fetchPaperTraders(
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest("/api/v1/paper/traders", "GET", undefined, signal);
}

export function fetchPaperTrader(
  traderId: string,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest(
    `/api/v1/paper/traders/${encodeURIComponent(traderId)}`,
    "GET",
    undefined,
    signal,
  );
}

// --- P6 Stage B ledger origin and review snapshot lifecycle ---

/**
 * Stage B identities reach artifact paths and filenames, so each one is
 * checked against its canonical guard before it is ever placed in a URL.
 * An unsafe identity means no request leaves the browser at all.
 */
function paperIdentityRejection(
  path: string,
  method: PaperHttpMethod,
  message: string,
): Promise<PaperHttpResult> {
  return Promise.reject(
    new PaperTransportError(path, method, new Error(message)),
  );
}

export function fetchPaperLedgerOrigin(
  traderId: string,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isSafePaperTraderId(traderId)) {
    return paperIdentityRejection(
      "/api/v1/paper/traders/{trader_id}/ledger-origin",
      "GET",
      "unsafe trader identity",
    );
  }
  return paperRequest(
    `/api/v1/paper/traders/${encodeURIComponent(traderId)}/ledger-origin`,
    "GET",
    undefined,
    signal,
  );
}

export function postPaperReviewSnapshot(
  traderId: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isSafePaperTraderId(traderId)) {
    return paperIdentityRejection(
      "/api/v1/paper/traders/{trader_id}/review-snapshots",
      "POST",
      "unsafe trader identity",
    );
  }
  return paperRequest(
    `/api/v1/paper/traders/${encodeURIComponent(traderId)}/review-snapshots`,
    "POST",
    body,
    signal,
  );
}

export function fetchPaperReviewRequest(
  requestId: string,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isPaperReviewRequestId(requestId)) {
    return paperIdentityRejection(
      "/api/v1/paper/review-requests/{request_id}",
      "GET",
      "unsafe review request identity",
    );
  }
  return paperRequest(
    `/api/v1/paper/review-requests/${encodeURIComponent(requestId)}`,
    "GET",
    undefined,
    signal,
  );
}

/**
 * §9.3 keeps the artifact bytes untouched: the caller needs the complete
 * ArrayBuffer, both headers and, on a non-200, the raw error text — nothing is
 * decoded, coerced or discarded here.
 */
export interface PaperBinaryHttpResult {
  path: string;
  method: "GET";
  status: number;
  ok: boolean;
  contentType: string | null;
  contentDisposition: string | null;
  arrayBuffer: ArrayBuffer;
  bytes: Uint8Array;
  rawText: string;
  jsonParsed: boolean;
  body: unknown;
}

async function paperBinaryRequest(
  path: string,
  signal?: AbortSignal,
): Promise<PaperBinaryHttpResult> {
  try {
    const response = await trackedFetch(`${API_BASE}${path}`, {
      method: "GET",
      signal,
    });
    const arrayBuffer = await response.arrayBuffer();
    const bytes = new Uint8Array(arrayBuffer);
    const rawText = response.ok ? "" : new TextDecoder().decode(bytes);
    let body: unknown = null;
    let jsonParsed = false;
    if (!response.ok) {
      try {
        body = JSON.parse(rawText) as unknown;
        jsonParsed = true;
      } catch {
        // Keep the raw bytes; the strict error parser refuses the unknown one.
      }
    }
    return {
      path,
      method: "GET",
      status: response.status,
      ok: response.ok,
      contentType: response.headers.get("Content-Type"),
      contentDisposition: response.headers.get("Content-Disposition"),
      arrayBuffer,
      bytes,
      rawText,
      jsonParsed,
      body,
    };
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new PaperTransportError(path, "GET", error);
  }
}

export function fetchPaperReviewDownload(
  snapshotId: string,
  signal?: AbortSignal,
): Promise<PaperBinaryHttpResult> {
  if (!isPaperReviewSnapshotId(snapshotId)) {
    return Promise.reject(
      new PaperTransportError(
        "/api/v1/paper/review-snapshots/{snapshot_id}/download",
        "GET",
        new Error("unsafe review snapshot identity"),
      ),
    );
  }
  return paperBinaryRequest(
    `/api/v1/paper/review-snapshots/${encodeURIComponent(snapshotId)}/download`,
    signal,
  );
}

export function fetchPaperReviewTerminalOpener(
  snapshotId: string,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isPaperReviewSnapshotId(snapshotId)) {
    return paperIdentityRejection(
      "/api/v1/paper/review-snapshots/{snapshot_id}/terminal-opener",
      "GET",
      "unsafe review snapshot identity",
    );
  }
  return paperRequest(
    `/api/v1/paper/review-snapshots/${encodeURIComponent(
      snapshotId,
    )}/terminal-opener`,
    "GET",
    undefined,
    signal,
  );
}

// --- P6 runtime (v4 store) — real registered routes ---

export function fetchPaperRuntimeCapabilities(
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest(
    "/api/v1/paper/runtime-capabilities",
    "GET",
    undefined,
    signal,
  );
}

export function fetchPaperTraderRuntime(
  traderId: string,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isSafePaperTraderId(traderId)) {
    throw new PaperTransportError(
      "/api/v1/paper/traders/{trader_id}/runtime",
      "GET",
      new Error("invalid_trader_id"),
    );
  }
  return paperRequest(
    `/api/v1/paper/traders/${encodeURIComponent(traderId)}/runtime`,
    "GET",
    undefined,
    signal,
  );
}

export function postPaperRuntimeCommand(
  traderId: string,
  command: "start" | "pause" | "resume" | "permanent-stop",
  body: unknown,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isSafePaperTraderId(traderId)) {
    throw new PaperTransportError(
      `/api/v1/paper/traders/{trader_id}/runtime/${command}`,
      "POST",
      new Error("invalid_trader_id"),
    );
  }
  return paperRequest(
    `/api/v1/paper/traders/${encodeURIComponent(traderId)}/runtime/${command}`,
    "POST",
    body,
    signal,
  );
}

export async function postPaperReviewV2Download(
  traderId: string,
  body: { request_id: string },
  signal?: AbortSignal,
): Promise<{ ok: boolean; status: number; bytes: ArrayBuffer; schema: string | null }> {
  if (!isSafePaperTraderId(traderId)) {
    throw new PaperTransportError(
      "/api/v1/paper/traders/{trader_id}/review-v2",
      "POST",
      new Error("invalid_trader_id"),
    );
  }
  const response = await trackedFetch(
    `${API_BASE}/api/v1/paper/traders/${encodeURIComponent(traderId)}/review-v2`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    },
  );
  const bytes = await response.arrayBuffer();
  return {
    ok: response.ok,
    status: response.status,
    bytes,
    schema: response.headers.get("X-Paper-Review-Schema"),
  };
}

export function fetchPaperTraderTimeline(
  traderId: string,
  afterCursor = 0,
  limit = 50,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isSafePaperTraderId(traderId)) {
    throw new PaperTransportError(
      "/api/v1/paper/traders/{trader_id}/timeline",
      "GET",
      new Error("invalid_trader_id"),
    );
  }
  const params = new URLSearchParams({
    after_cursor: String(afterCursor),
    limit: String(limit),
  });
  return paperRequest(
    `/api/v1/paper/traders/${encodeURIComponent(traderId)}/timeline?${params}`,
    "GET",
    undefined,
    signal,
  );
}

/** Trade activity stream (intents / fills / trades) for notifications. */
export function fetchPaperTraderActivity(
  traderId: string,
  afterCursor = 0,
  limit = 50,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isSafePaperTraderId(traderId)) {
    throw new PaperTransportError(
      "/api/v1/paper/traders/{trader_id}/activity",
      "GET",
      new Error("invalid_trader_id"),
    );
  }
  const params = new URLSearchParams({
    after_cursor: String(afterCursor),
    limit: String(limit),
  });
  return paperRequest(
    `/api/v1/paper/traders/${encodeURIComponent(traderId)}/activity?${params}`,
    "GET",
    undefined,
    signal,
  );
}

export function fetchPaperTraderChart(
  traderId: string,
  afterCursor = 0,
  limit = 100,
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isSafePaperTraderId(traderId)) {
    throw new PaperTransportError(
      "/api/v1/paper/traders/{trader_id}/chart",
      "GET",
      new Error("invalid_trader_id"),
    );
  }
  const params = new URLSearchParams({
    after_cursor: String(afterCursor),
    limit: String(limit),
  });
  return paperRequest(
    `/api/v1/paper/traders/${encodeURIComponent(traderId)}/chart?${params}`,
    "GET",
    undefined,
    signal,
  );
}

export function postPaperRuntimeReplay(
  traderId: string,
  body: { use_demo_bars?: boolean; bars?: unknown[]; mode?: string },
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  if (!isSafePaperTraderId(traderId)) {
    throw new PaperTransportError(
      "/api/v1/paper/traders/{trader_id}/runtime/replay",
      "POST",
      new Error("invalid_trader_id"),
    );
  }
  return paperRequest(
    `/api/v1/paper/traders/${encodeURIComponent(traderId)}/runtime/replay`,
    "POST",
    body,
    signal,
  );
}

export function fetchPaperGatewayStatus(
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest(
    "/api/v1/paper/runtime/gateway-status",
    "GET",
    undefined,
    signal,
  );
}

export function fetchPaperFleetOverview(
  signal?: AbortSignal,
): Promise<PaperHttpResult> {
  return paperRequest(
    "/api/v1/paper/fleet-overview",
    "GET",
    undefined,
    signal,
  );
}
