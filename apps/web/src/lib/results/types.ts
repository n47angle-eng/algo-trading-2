/** P5 domain types — live API vs owner-review fixture never mixed. */

export type ResultsMode = "live" | "owner-review";

export type DecisionType = "use" | "return" | "abandon";

export interface ResultListItem {
  runId: string;
  strategyVersion: string;
  strategyLabel: string;
  contractId: string;
  /** null = 未提供; 0 = true zero trades */
  tradeCount: number | null;
  winRate: number | null;
  netR: number | null;
  netUsd: number | null;
  maxDrawdownUsd: number | null;
  whyLine: string;
  /** Zero-trade bottleneck when tradeCount === 0 (explicit) */
  bottleneck: string | null;
  unread: boolean;
  rangeStart: string | null;
  rangeEnd: string | null;
  tradingDays: number | null;
}

export interface TradeCausal {
  tradeIndex: number;
  side: "long" | "short";
  entryTimeLocal: string;
  entryPrice: number;
  exitTimeLocal: string;
  exitTimeUtc?: string;
  exitPrice: number;
  targetPrice?: number;
  /** Current durable wire has no canonical per-trade net-R. */
  rMultiple: number | null;
  pnlUsd: number;
  whyEntry: {
    d: string;
    h1: string;
    m5: string;
  };
  stop: {
    anchor: string;
    offsetTicks: number;
    finalPrice: number;
    reason: string;
  };
  exit: {
    kind: string;
    whichFirst: string;
    detail: string;
  };
  conservative: string;
  focusTimeUtc: string;
}

export interface NearMiss {
  index: number;
  timeLocal: string;
  timezone: string;
  layerReached: string;
  stepsAway: number;
  missing: string;
  values: string;
  focusTimeUtc: string;
}

export interface ScorecardRow {
  dim: string;
  label: string;
  status: string;
  statusLabel: string;
  detail: string;
}

export interface FunnelView {
  dailyPass: number | null;
  dailyTotal: number | null;
  evalPass: number | null;
  fills: number | null;
  judgment: string;
}

export interface ChartPaneData {
  timeframe: "D" | "1H" | "30m" | "5m";
  roleLabel: string;
  candles: Array<{
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
  }>;
  volume?: Array<{ time: number; value: number; color?: string }>;
  indicators?: Array<{
    id: string;
    points: Array<{ time: number; value: number }>;
    token: string;
  }>;
  markers?: Array<{
    time: number;
    position: "aboveBar" | "belowBar" | "inBar";
    shape: string;
    token: string;
    text: string;
  }>;
  levels?: Array<{ price: number; token: string; label?: string }>;
  /**
   * True plot-area vertical annotations (D27) — NOT series markers.
   * Renderer draws top-to-bottom lines; must not be folded into markers.
   */
  verticalLines?: Array<{ time: number; label: string }>;
  /** Alias used by renderer contract tests (same payload as verticalLines). */
  verticalAnnotations?: Array<{ time: number; label: string }>;
  rejectMarkers?: Array<{ time: number; text: string }>;
  /** Unix times of trend days for shading (D/ higher TFs). */
  trendDayTimes?: number[];
  available: boolean;
  unavailableReason?: string;
  /**
   * Phase C/F compute backend badge (from compute_provenance).
   * Optional so older fixtures and preview panes stay valid.
   */
  computeBackend?: {
    effectiveBackend: string;
    source?: string;
    fallbackReason?: string | null;
    writesAuthority?: boolean;
  };
}

/** null = missing/unknown (show 未提供); number includes explicit 0. */
export type OptionalMetric = number | null;

export interface ResultDetail {
  runId: string;
  strategyVersion: string;
  strategyLabel: string;
  contractId: string;
  rangeStartLocal: string;
  rangeEndLocal: string;
  timezone: string;
  tradingDays: number | null;
  tradeCount: number | null;
  winRate: number | null;
  netR: number | null;
  netUsd: number | null;
  maxDrawdownUsd: number | null;
  scorecard: ScorecardRow[];
  funnel: FunnelView | null;
  nearMisses: NearMiss[];
  trades: TradeCausal[];
  charts: ChartPaneData[];
  warnings: string[];
  whyZero: string | null;
}

export interface PromotionDecisionRecord {
  id: string;
  type: DecisionType;
  runId: string;
  strategyVersion: string;
  reason: string;
  scorecardSnapshot: ScorecardRow[];
  at: string;
}
