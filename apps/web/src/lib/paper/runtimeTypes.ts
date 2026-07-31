export const PAPER_RUNTIME_LIFECYCLES = [
  "provisioned",
  "starting",
  "running",
  "pausing",
  "paused",
  "tripped",
  "stopping",
  "recovery_required",
  "permanently_stopped",
] as const;

export type PaperRuntimeLifecycle =
  (typeof PAPER_RUNTIME_LIFECYCLES)[number];
export type PaperMarketMode = "live" | "test_delayed" | "replay_test";

export interface RuntimeTimeframeSelection {
  market_input: string;
  execution: string;
  chart_display: string;
}

export interface PaperTraderSelectionV2 {
  strategy_id: string;
  content_sha256: string;
  contract_id: string;
  baseline_run_id: string;
  baseline_result_sha256: string;
  timeframes: RuntimeTimeframeSelection;
}

export interface StrategyTimeframeProfile {
  bias: string;
  mid: string;
  entry: string;
  source: "strategy.v1";
  client_override: false;
}

export interface PaperSafetyLimits {
  max_drawdown_r: 8;
  max_losing_streak: 8;
  blind_minutes: 5;
}

export interface PaperRuntimeCapabilities {
  schema: "paper_runtime_capabilities.v1";
  timeframes: {
    market_input: { enabled: string[] };
    execution: { enabled: string[] };
    chart_display: { enabled: string[] };
    strategy_profile: {
      source: "strategy.v1";
      client_override: false;
    };
  };
  market_modes: ["live", "test_delayed"];
  safety_defaults: PaperSafetyLimits;
  lifecycle_states: typeof PAPER_RUNTIME_LIFECYCLES;
  as_of: string;
}

export interface PaperTraderV2 {
  schema: "paper_trader.v2";
  trader_id: string;
  request_id: string;
  selection: PaperTraderSelectionV2;
  selection_fingerprint: string;
  strategy_timeframe_profile: StrategyTimeframeProfile;
  lifecycle: PaperRuntimeLifecycle;
  lifecycle_version: number;
  lifecycle_reason: string;
  account_id: string;
  ledger_origin_id: string;
  safety: PaperSafetyLimits;
  created_at: string;
}

export interface PaperTraderCreateRequestV2 {
  schema: "paper_trader_create_request.v2";
  request_id: string;
  selection: PaperTraderSelectionV2;
}

/** Runtime polling snapshot from GET .../runtime */
export interface PaperRuntimeSnapshot {
  schema: "paper_runtime_snapshot.v1";
  trader_id: string;
  selection_fingerprint: string;
  lifecycle: PaperRuntimeLifecycle;
  lifecycle_version: number;
  lifecycle_reason: string;
  cash: number;
  equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  realized_r: number;
  unrealized_r: number;
  position_quantity: number;
  pending_intent_count: number;
  decision_count: number;
  trade_count: number;
  safety: PaperSafetyLimits & {
    drawdown_r: number;
    losing_streak: number;
    equity_high_water_r: number;
  };
  as_of: string;
}
