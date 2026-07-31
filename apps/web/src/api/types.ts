/** API DTOs for WO-006 / 6-2 read-only result endpoints. */

export interface ScorecardStatusChip {
  dim: string | null;
  status: string | null;
}

export interface RunSummary {
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
  scorecard_statuses: ScorecardStatusChip[];
  funnel_status: string | null;
  funnel_fills: number | null;
  has_scorecard: boolean;
  has_funnel: boolean;
  result_file: string;
}

export interface RunListResponse {
  schema: string;
  count: number;
  runs: RunSummary[];
  batch_id?: string;
}

export interface BatchSummary {
  batch_id: string;
  label: string;
  run_count: number;
  run_ids: string[];
  source: string;
}

export interface BatchListResponse {
  schema: string;
  count: number;
  batches: BatchSummary[];
}

export interface ScorecardItem {
  dim: string;
  status: string;
  detail: Record<string, unknown>;
}

export interface FunnelV1 {
  schema: string;
  status: string;
  daily_trend_days?: number;
  evaluations_passing_daily_gate?: number;
  evaluations_passing_mid_gate?: number;
  signals_created?: number;
  fills?: number;
  reject_reasons?: Record<string, number>;
  notes?: string;
  units?: Record<string, string>;
}

export interface ResultDocument {
  schema: string;
  run: {
    run_id: string;
    strategy_version?: string;
    manifest?: Record<string, unknown>;
    engine?: Record<string, unknown>;
  };
  metrics: Record<string, unknown>;
  scorecard?: ScorecardItem[];
  funnel?: FunnelV1 | null;
  trades_ref?: string;
  events_ref?: string;
  equity_curve_ref?: string;
  warnings?: string[];
  owner_action?: unknown;
}

export interface TradesResponse {
  schema?: string;
  run_id?: string;
  trades: Array<Record<string, unknown>>;
}

export interface EventsResponse {
  schema?: string;
  run_id?: string;
  events: Array<Record<string, unknown>>;
}
