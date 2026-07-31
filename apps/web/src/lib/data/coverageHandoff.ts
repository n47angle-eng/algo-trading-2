/**
 * P3 → P4 usable trading-day handoff.
 *
 * Pure helpers: parse real coverage facts, fail closed on unknown/error,
 * build Backtest query params without fabricating dates.
 */

export type TradingDayCoverageStatus = "known" | "unknown" | "error";

export interface LongestCompleteSegment {
  start_trading_date: string;
  end_trading_date: string;
  trading_date_count: number;
}

export interface TradingDayCoverageFacts {
  schema: "trading_day_coverage.v1";
  status: TradingDayCoverageStatus;
  session_name: string | null;
  first_trading_date: string | null;
  last_trading_date: string | null;
  trading_date_count: number | null;
  complete_trading_date_count: number | null;
  problem_trading_date_count: number | null;
  pending_problem_trading_date_count: number | null;
  owner_trusted_problem_trading_date_count: number | null;
  owner_excluded_trading_date_count: number | null;
  complete_trading_dates: string[] | null;
  problem_trading_dates: string[] | null;
  pending_problem_trading_dates: string[] | null;
  owner_trusted_problem_trading_dates: string[] | null;
  owner_excluded_trading_dates: string[] | null;
  longest_complete_segment: LongestCompleteSegment | null;
  error_code?: string | null;
}

export interface NativeDailyCoverageFacts {
  schema?: string;
  status?: string;
  available_trading_date_count?: number | null;
  missing_trading_date_count?: number | null;
  [key: string]: unknown;
}

export interface CoverageContractRow {
  symbol: string;
  contract_id: string;
  display_name?: string;
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
  trading_day_coverage?: TradingDayCoverageFacts | null;
  native_daily_coverage?: NativeDailyCoverageFacts | null;
}

export interface BacktestHandoff {
  symbol: string;
  /** Trading-day labels YYYY-MM-DD — never timezone-shifted. */
  rangeStartDate: string;
  rangeEndDate: string;
  source: "longest_complete_segment" | "complete_bounds";
}

const TRADING_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function isTradingDateLabel(value: unknown): value is string {
  return typeof value === "string" && TRADING_DATE_RE.test(value);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function dateArrayOrNull(value: unknown): string[] | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (!Array.isArray(value)) {
    return null;
  }
  if (!value.every(isTradingDateLabel)) {
    return null;
  }
  return value as string[];
}

function countOrNull(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    return null;
  }
  return value;
}

/**
 * Parse nested trading_day_coverage from the real coverage API.
 * Unknown / missing nested block → fail-closed "unknown" (no fake clean days).
 */
export function parseTradingDayCoverage(
  raw: unknown,
): TradingDayCoverageFacts {
  if (!isPlainObject(raw)) {
    return failClosedUnknown("missing_nested_coverage");
  }
  const status = raw.status;
  if (status === "unknown" || status === "error") {
    return {
      schema: "trading_day_coverage.v1",
      status,
      session_name:
        typeof raw.session_name === "string" ? raw.session_name : null,
      first_trading_date: null,
      last_trading_date: null,
      trading_date_count: null,
      complete_trading_date_count: null,
      problem_trading_date_count: null,
      pending_problem_trading_date_count: null,
      owner_trusted_problem_trading_date_count: null,
      owner_excluded_trading_date_count: null,
      complete_trading_dates: null,
      problem_trading_dates: null,
      pending_problem_trading_dates: null,
      owner_trusted_problem_trading_dates: null,
      owner_excluded_trading_dates: null,
      longest_complete_segment: null,
      error_code:
        typeof raw.error_code === "string" ? raw.error_code : null,
    };
  }
  if (status !== "known") {
    return failClosedUnknown("status_not_known");
  }

  const complete = dateArrayOrNull(raw.complete_trading_dates);
  const problem = dateArrayOrNull(raw.problem_trading_dates);
  const pending = dateArrayOrNull(raw.pending_problem_trading_dates);
  const trusted = dateArrayOrNull(raw.owner_trusted_problem_trading_dates);
  const excluded = dateArrayOrNull(raw.owner_excluded_trading_dates);

  // Fail closed: known status must carry real arrays (may be empty).
  if (
    complete === null ||
    problem === null ||
    pending === null ||
    trusted === null ||
    excluded === null
  ) {
    return failClosedUnknown("date_lists_invalid");
  }

  let longest: LongestCompleteSegment | null = null;
  if (raw.longest_complete_segment !== null && raw.longest_complete_segment !== undefined) {
    if (!isPlainObject(raw.longest_complete_segment)) {
      return failClosedUnknown("longest_segment_invalid");
    }
    const start =
      raw.longest_complete_segment.start_trading_date ??
      raw.longest_complete_segment.start;
    const end =
      raw.longest_complete_segment.end_trading_date ??
      raw.longest_complete_segment.end;
    const count = raw.longest_complete_segment.trading_date_count;
    if (
      !isTradingDateLabel(start) ||
      !isTradingDateLabel(end) ||
      typeof count !== "number" ||
      !Number.isSafeInteger(count) ||
      count < 1
    ) {
      return failClosedUnknown("longest_segment_invalid");
    }
    longest = {
      start_trading_date: start,
      end_trading_date: end,
      trading_date_count: count,
    };
  }

  return {
    schema: "trading_day_coverage.v1",
    status: "known",
    session_name:
      typeof raw.session_name === "string" ? raw.session_name : null,
    first_trading_date: isTradingDateLabel(raw.first_trading_date)
      ? raw.first_trading_date
      : null,
    last_trading_date: isTradingDateLabel(raw.last_trading_date)
      ? raw.last_trading_date
      : null,
    trading_date_count: countOrNull(raw.trading_date_count),
    complete_trading_date_count: countOrNull(raw.complete_trading_date_count),
    problem_trading_date_count: countOrNull(raw.problem_trading_date_count),
    pending_problem_trading_date_count: countOrNull(
      raw.pending_problem_trading_date_count,
    ),
    owner_trusted_problem_trading_date_count: countOrNull(
      raw.owner_trusted_problem_trading_date_count,
    ),
    owner_excluded_trading_date_count: countOrNull(
      raw.owner_excluded_trading_date_count,
    ),
    complete_trading_dates: complete,
    problem_trading_dates: problem,
    pending_problem_trading_dates: pending,
    owner_trusted_problem_trading_dates: trusted,
    owner_excluded_trading_dates: excluded,
    longest_complete_segment: longest,
  };
}

function failClosedUnknown(code: string): TradingDayCoverageFacts {
  return {
    schema: "trading_day_coverage.v1",
    status: "unknown",
    session_name: null,
    first_trading_date: null,
    last_trading_date: null,
    trading_date_count: null,
    complete_trading_date_count: null,
    problem_trading_date_count: null,
    pending_problem_trading_date_count: null,
    owner_trusted_problem_trading_date_count: null,
    owner_excluded_trading_date_count: null,
    complete_trading_dates: null,
    problem_trading_dates: null,
    pending_problem_trading_dates: null,
    owner_trusted_problem_trading_dates: null,
    owner_excluded_trading_dates: null,
    longest_complete_segment: null,
    error_code: code,
  };
}

/**
 * Build handoff from known coverage only.
 * Prefer longest continuous complete segment; else complete date bounds.
 * Returns null when coverage is not known or there are no usable complete days.
 */
export function buildBacktestHandoff(
  symbol: string,
  coverage: TradingDayCoverageFacts,
): BacktestHandoff | null {
  const sym = symbol.trim().toUpperCase();
  if (!sym) {
    return null;
  }
  if (coverage.status !== "known") {
    return null;
  }
  if (
    coverage.longest_complete_segment &&
    isTradingDateLabel(coverage.longest_complete_segment.start_trading_date) &&
    isTradingDateLabel(coverage.longest_complete_segment.end_trading_date)
  ) {
    return {
      symbol: sym,
      rangeStartDate: coverage.longest_complete_segment.start_trading_date,
      rangeEndDate: coverage.longest_complete_segment.end_trading_date,
      source: "longest_complete_segment",
    };
  }
  const complete = coverage.complete_trading_dates;
  if (!complete || complete.length === 0) {
    return null;
  }
  return {
    symbol: sym,
    rangeStartDate: complete[0]!,
    rangeEndDate: complete[complete.length - 1]!,
    source: "complete_bounds",
  };
}

/** Query string for `/backtest?...` — trading days as labels only. */
export function handoffToSearchParams(handoff: BacktestHandoff): string {
  const params = new URLSearchParams();
  params.set("symbol", handoff.symbol);
  params.set("startDate", handoff.rangeStartDate);
  params.set("endDate", handoff.rangeEndDate);
  params.set("from", "data");
  return params.toString();
}

export function backtestHandoffHref(handoff: BacktestHandoff): string {
  return `/backtest?${handoffToSearchParams(handoff)}`;
}

/**
 * Parse inbound handoff query params for Backtest page.
 * Invalid or partial params → null (do not invent dates).
 */
export function parseHandoffSearchParams(
  params: URLSearchParams,
): BacktestHandoff | null {
  const symbol = (params.get("symbol") ?? "").trim().toUpperCase();
  const start = params.get("startDate");
  const end = params.get("endDate");
  if (!symbol || !isTradingDateLabel(start) || !isTradingDateLabel(end)) {
    return null;
  }
  if (start > end) {
    return null;
  }
  return {
    symbol,
    rangeStartDate: start,
    rangeEndDate: end,
    source: "complete_bounds",
  };
}

/**
 * Convert trading-day labels to local datetime-picker values at session edges.
 * Trading day labels are NOT timezone-converted — they are stamped as local
 * calendar dates at 00:00:00 / 23:59:59 for the range picker only.
 * API still receives UTC via existing localDatetimeToUtcIso path.
 */
export function tradingDayToLocalRangeStart(tradingDay: string): string {
  if (!isTradingDateLabel(tradingDay)) {
    return "";
  }
  return `${tradingDay}T00:00:00`;
}

export function tradingDayToLocalRangeEnd(tradingDay: string): string {
  if (!isTradingDateLabel(tradingDay)) {
    return "";
  }
  return `${tradingDay}T23:59:59`;
}

/** Owner-facing copy: never claim clean days when status is not known. */
export function coverageStatusMessage(
  coverage: TradingDayCoverageFacts | null | undefined,
  loading: boolean,
  error: string | null,
): string | null {
  if (loading) {
    return "覆蓋資料載入中…暫時唔可以當所有交易日都乾淨。";
  }
  if (error) {
    return "覆蓋資料暫時核實唔到，唔會顯示假嘅完整日清單。";
  }
  if (!coverage || coverage.status !== "known") {
    return "呢份合約嘅交易日覆蓋暫時核實唔到，唔會當全部可用。";
  }
  return null;
}
