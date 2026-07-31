export interface DaytradeOpenPosition {
  side: string;
  qty: number;
  entry_price: number;
  entry_ts?: string;
  stop: number;
  target: number;
  entry_reference?: number;
}

export interface DaytradeTraderCard {
  trader_id: string;
  display_name: string;
  symbol: string;
  contract_id?: string;
  strategy_id: string;
  status: string;
  equity: number;
  cash: number;
  starting_equity?: number;
  day_return: number;
  realized_pnl: number;
  positions_count: number;
  open_position: DaytradeOpenPosition | null;
  trading_date: string | null;
  execution_mode_id: string;
  semantics_version: string;
  mtm_quality: string;
  live_scale_label: string;
  notes?: string;
  /** Session / risk params surfaced on roster cards */
  quantity?: number;
  or_minutes?: number;
  no_new_entry_after?: string;
  force_flat_time?: string;
  rth_start?: string;
  rth_end?: string;
  timezone?: string;
  max_daily_loss_r?: number;
  profile_path: string;
}

export interface DaytradeTradersList {
  traders: DaytradeTraderCard[];
  count: number;
  runner_enabled: boolean;
  supported_symbols: string[];
  live_scale_label: string;
  formula_version: string;
}

export interface DaytradeProfile {
  trader_id: string;
  display_name: string;
  symbol: string;
  contract_id: string;
  strategy_id: string;
  starting_equity: number;
  quantity: number;
  or_minutes: number;
  no_new_entry_after: string;
  force_flat_time: string;
  rth_start: string;
  rth_end: string;
  timezone: string;
  max_daily_loss_r: number;
  notes: string;
  config_generation: number;
}

export interface DaytradeLive {
  trader_id: string;
  display_name: string;
  symbol: string;
  contract_id?: string;
  strategy_id?: string;
  equity: number;
  cash: number;
  day_return: number;
  realized_pnl: number;
  open_position: DaytradeOpenPosition | null;
  positions: DaytradeOpenPosition[];
  events_today: Array<
    Record<string, unknown> & {
      ordinal?: number;
      kind?: string;
      note?: string;
      price?: number;
      ts?: string;
    }
  >;
  live_scale_label: string;
  block_reasons: string[];
  risk_flags: string[];
  trading_date?: string;
  config?: Record<string, unknown>;
}

export interface DaytradePositions {
  trader_id: string;
  trading_date: string;
  source: string;
  positions: DaytradeOpenPosition[];
  equity: number;
  cash: number;
  realized_pnl: number;
  day_return: number;
  event_count: number;
  live_scale_label: string;
  formula_version: string;
}

export interface DaytradeScoreTrade {
  side?: string;
  qty?: number;
  entry_price?: number;
  exit_price?: number;
  exit_kind?: string;
  entry_ts?: string;
  exit_ts?: string;
  stop?: number;
  target?: number;
  pnl: number;
  won: boolean;
  breakeven?: boolean;
  risk_points?: number | null;
  one_r_dollars?: number | null;
  r_multiple?: number | null;
}

export interface DaytradeMarketBar {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface DaytradeMarketMarker {
  ts?: string;
  kind: string;
  side?: string;
  price?: number;
  ordinal?: number;
  note?: string;
}

export interface DaytradeMarketReference {
  last_price: number | null;
  session_open: number | null;
  session_high: number | null;
  session_low: number | null;
  session_range: number | null;
  session_range_dollars: number | null;
  or_high: number | null;
  or_low: number | null;
  or_width: number | null;
  or_width_dollars: number | null;
  bar_count: number;
  one_r_dollars: number | null;
  unrealized_hint: number | null;
}

export interface DaytradeMarketChart {
  schema: string;
  trader_id: string;
  display_name: string;
  symbol: string;
  contract_id: string;
  bar_mode: string;
  trading_date: string;
  bars: DaytradeMarketBar[];
  markers: DaytradeMarketMarker[];
  levels: {
    or_high: number | null;
    or_low: number | null;
    stop: number | null;
    target: number | null;
    entry: number | null;
  };
  reference: DaytradeMarketReference;
  point_value: number;
  tick_size: number;
  open_position: DaytradeOpenPosition | null;
  live_scale_label: string;
  formula_version: string;
  limitations?: string[];
}

export interface DaytradeEquityPoint {
  ts: string | null;
  equity: number;
  realized_pnl: number;
  kind?: string;
  ordinal?: number;
}

export interface DaytradeEquitySeries {
  schema: string;
  trader_id: string;
  trading_date: string;
  points: DaytradeEquityPoint[];
  starting_equity: number;
  ending_equity: number;
  peak_equity: number;
  max_drawdown: number;
  day_return: number;
  realized_pnl: number;
  live_scale_label: string;
  formula_version: string;
  source: string;
}

export interface DaytradeStats {
  schema?: string;
  trader_id: string;
  display_name?: string;
  symbol?: string;
  window: string;
  closed_trade_count: number;
  wins: number;
  losses: number;
  breakevens?: number;
  win_rate: number | null;
  sample_sufficient?: boolean;
  total_closed_pnl: number;
  avg_win: number | null;
  avg_loss: number | null;
  expectancy: number | null;
  expectancy_r: number | null;
  profit_factor: number | null;
  profit_factor_infinite?: boolean;
  max_win_streak: number;
  max_loss_streak: number;
  exit_mix: Record<string, number>;
  peak_equity?: number;
  max_drawdown: number;
  starting_equity?: number;
  ending_equity?: number;
  total_return: number | null;
  trading_days?: number;
  active_days?: number;
  flat_compliance_days?: number;
  flat_compliance_rate: number | null;
  open_count?: number;
  trades: DaytradeScoreTrade[];
  formula_version: string;
  live_scale_label: string;
  source?: string;
}

export interface DaytradeScorecard {
  trader_id: string;
  display_name: string;
  symbol: string;
  trading_date: string;
  equity: number;
  cash: number;
  day_return: number;
  realized_pnl: number;
  open_count: number;
  closed_trade_count: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  total_closed_pnl: number;
  flat_compliance: boolean;
  open_position: DaytradeOpenPosition | null;
  trades: DaytradeScoreTrade[];
  formula_version: string;
  source: string;
  live_scale_label: string;
}

export interface DaytradeTraderDetail {
  profile: DaytradeProfile;
  summary: DaytradeTraderCard;
  live: DaytradeLive;
  scorecard: DaytradeScorecard;
  links: Record<string, string>;
  live_scale_label: string;
}

export interface DaytradeActivity {
  trader_id: string;
  view: string;
  bubbles: Array<{
    trader_id: string;
    display_name: string;
    ts?: string;
    kind?: string;
    text: string;
  }>;
  source: string;
  live_scale_label: string;
}

export interface DaytradeHealth {
  service: string;
  runner_enabled: boolean;
  pending_incidents: number;
  trader_count?: number;
  live_execution_mode_id: string;
  live_scale_label: string;
  authority: string;
  writes_authority: boolean;
}

export interface CreateTraderRequest {
  display_name: string;
  symbol: "NQ" | "YM" | "GC";
  strategy_id?: string;
  starting_equity?: number;
  quantity?: number;
  or_minutes?: number;
  no_new_entry_after?: string;
  force_flat_time?: string;
  max_daily_loss_r?: number;
  notes?: string;
  trader_id?: string;
}
