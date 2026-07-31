export type ChartTimeframe = "5m" | "1H" | "D";

export type ChartComputeBackend = "python" | "rust";

export interface ChartComputeProvenance {
  schema?: string;
  feature?: string;
  requested_backend?: string;
  effective_backend?: ChartComputeBackend | string;
  fallback_reason?: string | null;
  writes_authority?: boolean;
  source?: string;
  [key: string]: unknown;
}

export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface ChartLinePoint {
  time: number;
  value: number;
}

export interface ChartMarker {
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  shape: string;
  token: string;
  text: string;
}

export interface ChartLevel {
  kind: string;
  price: number;
  time: number | null;
  token: string;
}

export interface ChartSeriesResponse {
  schema: string;
  run_id: string;
  timeframe: ChartTimeframe;
  contract_id: string;
  candles: ChartCandle[];
  ema18: ChartLinePoint[];
  ema50: ChartLinePoint[];
  ema90: ChartLinePoint[];
  markers: ChartMarker[];
  levels: ChartLevel[];
  source: string;
  visible_start?: string;
  visible_end?: string;
  /** Phase C/F: present on materialize; optional on older sidecars. */
  compute_provenance?: ChartComputeProvenance;
}

export type ChartShadowVerdict = "PASS" | "DIFF" | "SKIPPED" | "INCOMPARABLE";

export interface ChartShadowCompareResponse {
  schema: "chart_shadow_compare.v1" | string;
  verdict: ChartShadowVerdict | string;
  reason?: string;
  note?: string;
  timeframe?: string;
  run_id?: string;
  lookback_days?: number;
  abs_eps?: number;
  reference?: {
    source?: string;
    compute_provenance?: ChartComputeProvenance;
    candle_count?: number;
  };
  candidate?: {
    source?: string;
    compute_provenance?: ChartComputeProvenance;
    candle_count?: number;
  } | null;
  timeline?: {
    shared_count?: number;
    only_in_reference_count?: number;
    only_in_candidate_count?: number;
  };
  ohlc?: {
    max_abs_diff?: number;
    mean_abs_diff?: number;
    within_eps_ratio?: number;
    worst?: Record<string, unknown> | null;
  };
  ema?: Record<
    string,
    {
      max_abs_diff?: number;
      mean_abs_diff?: number;
      within_eps_ratio?: number;
    }
  >;
  summary?: {
    max_abs_ohlc_diff?: number;
    max_abs_ema_diff?: number;
    timeline_mismatch?: boolean;
    pass_exact_within_eps?: boolean;
  };
}

export interface NarrativeStep {
  time: string | null;
  time_label: string;
  layer: string;
  tone: string;
  text: string;
}

export interface NarrativeResponse {
  schema: string;
  run_id: string;
  count: number;
  steps: NarrativeStep[];
  note?: string;
}
