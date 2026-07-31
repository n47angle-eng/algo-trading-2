import type {
  CreateTraderRequest,
  DaytradeActivity,
  DaytradeEquitySeries,
  DaytradeHealth,
  DaytradeLive,
  DaytradeMarketChart,
  DaytradePositions,
  DaytradeScorecard,
  DaytradeStats,
  DaytradeTraderDetail,
  DaytradeTradersList,
} from "../lib/daytrade/types";
import { trackedFetch } from "../lib/net/transport";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function getJson<T>(path: string): Promise<T> {
  const response = await trackedFetch(`${API_BASE}${path}`);
  const raw = await response.text();
  if (!response.ok) {
    throw new Error(`${response.status} ${path}: ${raw || response.statusText}`);
  }
  return JSON.parse(raw) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await trackedFetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const raw = await response.text();
  if (!response.ok) {
    throw new Error(`${response.status} ${path}: ${raw || response.statusText}`);
  }
  return raw ? (JSON.parse(raw) as T) : ({} as T);
}

export function fetchDaytradeHealth(): Promise<DaytradeHealth> {
  return getJson("/api/v1/daytrade/health");
}

export function fetchDaytradeTraders(): Promise<DaytradeTradersList> {
  return getJson("/api/v1/daytrade/traders");
}

export function createDaytradeTrader(
  body: CreateTraderRequest,
): Promise<{ trader: DaytradeTradersList["traders"][number]; entry: string }> {
  return postJson("/api/v1/daytrade/traders", body);
}

export function fetchDaytradeTrader(traderId: string): Promise<DaytradeTraderDetail> {
  return getJson(`/api/v1/daytrade/traders/${encodeURIComponent(traderId)}`);
}

export function fetchDaytradeLive(
  traderId: string,
  tradingDate?: string,
): Promise<DaytradeLive> {
  const q = tradingDate ? `?trading_date=${encodeURIComponent(tradingDate)}` : "";
  return getJson(
    `/api/v1/daytrade/traders/${encodeURIComponent(traderId)}/live${q}`,
  );
}

export function fetchDaytradePositions(
  traderId: string,
  tradingDate?: string,
): Promise<DaytradePositions> {
  const q = tradingDate ? `?trading_date=${encodeURIComponent(tradingDate)}` : "";
  return getJson(
    `/api/v1/daytrade/traders/${encodeURIComponent(traderId)}/positions${q}`,
  );
}

export function fetchDaytradeScorecard(
  traderId: string,
  tradingDate?: string,
): Promise<DaytradeScorecard> {
  const q = tradingDate ? `?trading_date=${encodeURIComponent(tradingDate)}` : "";
  return getJson(
    `/api/v1/daytrade/traders/${encodeURIComponent(traderId)}/scorecard${q}`,
  );
}

export function fetchDaytradeMarketChart(
  traderId: string,
  tradingDate?: string,
): Promise<DaytradeMarketChart> {
  const q = tradingDate ? `?trading_date=${encodeURIComponent(tradingDate)}` : "";
  return getJson(
    `/api/v1/daytrade/traders/${encodeURIComponent(traderId)}/market-chart${q}`,
  );
}

export function fetchDaytradeEquitySeries(
  traderId: string,
  tradingDate?: string,
): Promise<DaytradeEquitySeries> {
  const q = tradingDate ? `?trading_date=${encodeURIComponent(tradingDate)}` : "";
  return getJson(
    `/api/v1/daytrade/traders/${encodeURIComponent(traderId)}/equity-series${q}`,
  );
}

export function fetchDaytradeStats(
  traderId: string,
  window: "today" | "7d" | "30d" | "all" = "today",
  tradingDate?: string,
): Promise<DaytradeStats> {
  const params = new URLSearchParams({ window });
  if (tradingDate) {
    params.set("trading_date", tradingDate);
  }
  return getJson(
    `/api/v1/daytrade/traders/${encodeURIComponent(traderId)}/stats?${params}`,
  );
}

export function fetchDaytradeActivity(
  traderId: string,
  view = "summary",
): Promise<DaytradeActivity> {
  return getJson(
    `/api/v1/daytrade/traders/${encodeURIComponent(traderId)}/activity?view=${encodeURIComponent(view)}`,
  );
}

export function postDaytradeRunner(enabled: boolean): Promise<{ runner_enabled: boolean }> {
  return postJson("/api/v1/daytrade/runner", { enabled });
}

export function postDaytradeStep(body: {
  trading_date?: string;
  trader_id?: string;
  complete?: boolean;
}): Promise<unknown> {
  return postJson("/api/v1/daytrade/step", body);
}

export function postDaytradeOperatorCommand(
  traderId: string,
  command: "pause" | "resume" | "force_flat",
): Promise<unknown> {
  return postJson(
    `/api/v1/daytrade/traders/${encodeURIComponent(traderId)}/operator-command`,
    { command },
  );
}
