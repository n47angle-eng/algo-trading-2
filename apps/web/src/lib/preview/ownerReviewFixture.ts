import type { PreviewTimeframe } from "./contract";

const BASE_TIME = Math.floor(
  Date.parse("2026-07-08T14:00:00Z") / 1000,
);

const STEP_SECONDS: Record<PreviewTimeframe, number> = {
  D: 86_400,
  "1H": 3_600,
  "30m": 1_800,
  "5m": 300,
};

function fixtureChart(
  timeframe: PreviewTimeframe,
  contractId: string,
  sessionName: string,
) {
  const step = STEP_SECONDS[timeframe];
  const candles = Array.from({ length: 10 }, (_, index) => {
    const open = 21_000 + index * 5;
    return {
      time: BASE_TIME + index * step,
      open,
      high: open + 12,
      low: open - 8,
      close: open + 4,
    };
  });
  const line = (offset: number) =>
    candles.map((candle) => ({
      time: candle.time,
      value: candle.close - offset,
    }));
  return {
    schema: "chart_series.v1",
    timeframe,
    contract_id: contractId,
    session_name: sessionName,
    data_fingerprint: "d".repeat(64),
    lookback_days:
      timeframe === "D"
        ? 200
        : timeframe === "1H"
          ? 30
          : timeframe === "30m"
            ? 20
            : 10,
    visible_start: "2026-07-01T00:00:00Z",
    visible_end: "2026-07-22T00:00:00Z",
    candles,
    ema18: line(2),
    ema50: line(5),
    ema90: line(9),
    markers: [],
    levels:
      timeframe === "5m"
        ? [
            {
              kind: "stop",
              price: 20_980,
              time: null,
              token: "color-negative",
            },
            {
              kind: "target",
              price: 21_080,
              time: null,
              token: "color-positive",
            },
          ]
        : [],
    source:
      timeframe === "D"
        ? "backend_native_daily_mtf"
        : "backend_mtf_precompute",
    persisted: false,
  };
}

function fixtureRejection(index: number) {
  const unknown = index === 7;
  const conditionId = unknown ? "owner_unknown_block" : "mid_direction";
  const timestamp = new Date((BASE_TIME + index * 300) * 1000)
    .toISOString()
    .replace(".000Z", "Z");
  return {
    evidence_id: `reject_${String(index + 1).padStart(2, "0")}`,
    timestamp,
    ts_init: timestamp,
    trading_date: "2026-07-08",
    direction: "long",
    evaluation_sequence: index + 1,
    reached_layers: ["daily", "mid"],
    condition_facts: [
      {
        condition_id: conditionId,
        layer_id: "mid",
        observed_at: timestamp,
        status: "failed",
        actual: false,
        operator: "eq",
        required: true,
        unit: "boolean",
        source_sequences: [index + 1],
      },
    ],
    blocking_condition_ids: [conditionId],
    context: {
      candidate_signal_kinds: ["inside"],
      inside_count: null,
      entry_pullback_state: "ready",
      mid_pullback_state: "blocked",
      daily_regime: "trend",
    },
    source_event_sequences: [index + 1],
  };
}

/**
 * One deterministic in-memory response for `?scenario=owner-review`.
 * It deliberately contains zero trades, one unknown blocking id, four chart
 * payloads and no durable identity.
 */
export function createOwnerReviewPreviewFixture(
  contractId = "NQ-202609-CME",
  sessionName = "eth",
): unknown {
  return {
    schema: "backtest_preview.v1",
    run_scope: "dry_run",
    request_fingerprint: {
      algorithm: "sha256",
      digest: "c".repeat(64),
    },
    validation: {
      schema: "strategy_validation.v1",
      valid: true,
      issue_count: 0,
      issues: [],
      report_text: "",
      name: "Owner-review zero-trade preview",
      universe: {
        primary_instrument: "NQ",
        asset_class: "equity_index_futures",
        contracts: ["NQ", "YM"],
        expansion_rationale: {
          YM: "同 class deterministic fixture",
        },
        session: sessionName,
      },
    },
    funnel: {
      schema: "funnel.v1",
      status: "ok",
      daily_trend_days: 3,
      evaluations_passing_daily_gate: 8,
      evaluations_passing_mid_gate: 0,
      signals_created: 0,
      fills: 0,
      reject_reasons: {
        mid_direction: 7,
        owner_unknown_block: 1,
      },
      notes: "Owner-review deterministic zero-trade fixture",
      units: {
        daily_trend_days: "day-level: trading days",
        evaluations_passing_daily_gate:
          "evaluation-level: 5m signal evaluations",
        evaluations_passing_mid_gate:
          "evaluation-level: 5m signal evaluations",
        signals_created: "evaluation-level: signal count",
        fills: "completed trades",
      },
    },
    decision_evidence: [],
    rejection_evidence: Array.from({ length: 8 }, (_, index) =>
      fixtureRejection(index),
    ),
    evidence_summary: {
      availability: "available",
      complete: true,
      evaluation_count: 8,
      rejection_count: 8,
      layer_reached_counts: { daily: 8, mid: 8 },
      blocking_condition_counts: {
        mid_direction: 7,
        owner_unknown_block: 1,
      },
      deepest_layer: "mid",
      trade_count: 0,
    },
    charts: {
      D: fixtureChart("D", contractId, sessionName),
      "1H": fixtureChart("1H", contractId, sessionName),
      "30m": fixtureChart("30m", contractId, sessionName),
      "5m": fixtureChart("5m", contractId, sessionName),
    },
    persisted: false,
    warnings: ["Owner-review fixture：冇連接 live preview engine。"],
    errors: [],
  };
}
