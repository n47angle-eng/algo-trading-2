import { afterEach, describe, expect, it, vi } from "vitest";
import JSZip from "jszip";

import {
  fetchP5ConfirmedStrategies,
  fetchP5Run,
  fetchP5RunChart,
  fetchP5RunEvents,
  fetchP5RunExport,
  fetchP5RunNarrative,
  fetchP5RunPromotionDecisions,
  fetchP5Runs,
  postP5RunPromotionDecision,
  P5TransportError,
  type P5ExportHttpResult,
  type P5HttpResult,
  type P5PromotionDecisionRequest,
} from "../../api/client";
import {
  mapP5Chart,
  mapP5NearMisses,
  mapP5RunListItem,
  mapP5Trades,
  parseP5Chart,
  parseP5Events,
  parseP5Narrative,
  parseP5PromotionDecisionHistory,
  parseP5PromotionDecisionRecord,
  parseP5ResultMain,
  parseP5ResultExport,
  parseP5RunList,
  parseP5StrategyLabels,
  parseP5Trades,
  P5_CHART_CACHE_STATES,
  selectClosestRejections,
  shouldCommitP5DecisionHistory,
} from "./normalContract";

const RUN_ID = "run-1";
const CONTRACT_ID = "NQ-202609-CME";

function clone<T>(value: T): T {
  return structuredClone(value);
}

function envelope(
  body: unknown,
  path = "/api/v1/test",
  status = 200,
): P5HttpResult {
  return {
    path,
    status,
    ok: status >= 200 && status < 300,
    rawText: JSON.stringify(body),
    jsonParsed: true,
    body,
  };
}

function jsonResponseForContract(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function runRow(overrides: Record<string, unknown> = {}) {
  return {
    run_id: RUN_ID,
    strategy_version: "strategy-0001",
    contract_id: CONTRACT_ID,
    session_name: "eth",
    range_start: "2026-07-01T00:00:00Z",
    range_end: "2026-07-22T00:00:00Z",
    validation_run: false,
    trade_count: 0,
    net_r: 0,
    net_pnl: 0,
    win_rate: null,
    profit_factor: null,
    max_drawdown_pnl: 0,
    max_drawdown_r: null,
    expectancy_r: null,
    scorecard_statuses: [],
    funnel_status: "ok",
    funnel_fills: 0,
    has_scorecard: false,
    has_funnel: true,
    result_file: `results/${RUN_ID}.json`,
    ...overrides,
  };
}

function runList(rows = [runRow()]) {
  return { schema: "run_list.v1", count: rows.length, runs: rows };
}

function funnel(fills = 0) {
  return {
    schema: "funnel.v1",
    status: "ok",
    daily_trend_days: 2,
    evaluations_passing_daily_gate: 4,
    evaluations_passing_mid_gate: 3,
    signals_created: 1,
    fills,
    reject_reasons: { entry_block: 1 },
    notes: "deterministic",
    units: {
      daily_trend_days: "trading days",
      evaluations_passing_daily_gate: "5m evaluations",
      evaluations_passing_mid_gate: "5m evaluations",
      signals_created: "signals",
      fills: "completed trades",
    },
  };
}

function compactMetrics(tradeCount = 0) {
  return {
    trade_count: tradeCount,
    gross_pnl: tradeCount === 0 ? 0 : 110,
    net_pnl: tradeCount === 0 ? 0 : 100,
    net_r: tradeCount === 0 ? 0 : 0.5,
    win_rate: tradeCount === 0 ? null : 1,
    profit_factor: tradeCount === 0 ? null : 2,
    expectancy_r: tradeCount === 0 ? null : 0.5,
    max_drawdown_pnl: tradeCount === 0 ? 0 : 20,
  };
}

function enrichedMetrics(tradeCount = 0) {
  const compact = compactMetrics(tradeCount);
  const periodBucket =
    tradeCount === 0
      ? {}
      : {
          "2026": {
            trade_count: tradeCount,
            net_r: compact.net_r,
            net_pnl: compact.net_pnl,
            expectancy_r: compact.expectancy_r,
          },
        };
  const monthBucket =
    tradeCount === 0
      ? {}
      : {
          "2026-07": {
            trade_count: tradeCount,
            net_r: compact.net_r,
            net_pnl: compact.net_pnl,
            expectancy_r: compact.expectancy_r,
          },
        };
  const scenario = (name: "x1" | "x1.5" | "x2") => {
    const multiplier = name === "x1" ? 1 : name === "x1.5" ? 1.5 : 2;
    return {
      multiplier,
      net_pnl: name === "x1" ? compact.net_pnl : compact.net_pnl - multiplier,
      net_r: name === "x1" ? compact.net_r : compact.net_r - multiplier / 10,
      expectancy_r:
        tradeCount === 0
          ? null
          : name === "x1"
            ? compact.expectancy_r
            : compact.expectancy_r! - multiplier / 10,
      trade_count: tradeCount,
    };
  };
  return {
    ...compact,
    max_drawdown_r: tradeCount === 0 ? null : 0.2,
    payoff_ratio: tradeCount === 0 ? null : 2,
    max_losing_streak: 0,
    dd_duration_trades: 0,
    calmar_r: tradeCount === 0 ? null : 1,
    param_count: 2,
    trades_per_param: tradeCount === 0 ? null : tradeCount / 2,
    rule_count: 4,
    skew: tradeCount === 0 ? null : 0,
    kurtosis: tradeCount === 0 ? null : 0,
    tail_ratio: tradeCount === 0 ? null : 1,
    var95_r: tradeCount === 0 ? null : -0.5,
    cvar95_r: tradeCount === 0 ? null : -0.7,
    psr: tradeCount === 0 ? null : 0.8,
    sharpe_per_trade: tradeCount === 0 ? null : 0.2,
    profit_concentration: {
      top5_removed_net_r: tradeCount === 0 ? null : 0,
      top10_removed_net_r: tradeCount === 0 ? null : 0,
    },
    cost_scenarios: {
      x1: scenario("x1"),
      "x1.5": scenario("x1.5"),
      x2: scenario("x2"),
    },
    period_cuts: { by_year: periodBucket, by_month: monthBucket },
  };
}

function mainDocument(
  profile: "compact" | "enriched" | "current" = "current",
  tradeCount = 0,
) {
  const result: Record<string, unknown> = {
    schema: "result.v1",
    run: {
      run_id: RUN_ID,
      strategy_version: "strategy-0001",
      manifest: {
        schema: "run_manifest.v1",
        run_id: RUN_ID,
        strategy_version: "strategy-0001",
        contract_id: CONTRACT_ID,
        session_name: "eth",
        range_start: "2026-07-01T00:00:00Z",
        range_end: "2026-07-22T00:00:00Z",
        validation_run: false,
        trading_days: 15,
        capital: 100_000,
        ...(profile === "current"
          ? {
              strategy_binding: {
                schema: "strategy_binding.v1",
                source: "strategy_file",
                strategy_id: "strategy-0001",
                strategy_name: "Trend exact",
                content_sha256: "a".repeat(64),
                universe_contracts: [CONTRACT_ID],
                universe_authorized: true,
                overrides: [],
              },
            }
          : {}),
      },
      engine: { app: "test", version: "1" },
    },
    metrics:
      profile === "compact"
        ? compactMetrics(tradeCount)
        : enrichedMetrics(tradeCount),
    scorecard: [],
    warnings: [],
    owner_action: null,
    trades_ref: `trades/${RUN_ID}.json`,
    equity_curve_ref: `equity/${RUN_ID}.json`,
    events_ref: `events/${RUN_ID}.json`,
  };
  if (profile !== "compact") {
    result.funnel = funnel(tradeCount);
  }
  if (profile === "current") {
    result.decision_evidence_complete = true;
  }
  return result;
}

function conditionFact(
  sequence = 1,
  overrides: Record<string, unknown> = {},
) {
  return {
    condition_id: "entry_break",
    layer_id: "entry",
    observed_at: "2026-07-08T14:00:00Z",
    status: "failed",
    actual: 100,
    operator: "gte",
    required: 101,
    unit: "price",
    source_sequences: [sequence],
    ...overrides,
  };
}

function tradeWire() {
  const fact = conditionFact(1, {
    status: "passed",
    actual: true,
    required: true,
    unit: "boolean",
  });
  return {
    trade_id: "trade-1",
    contract_id: CONTRACT_ID,
    direction: "long",
    signal_kind: "inside",
    quantity: 1,
    signal_timestamp: "2026-07-08T13:55:00Z",
    entry_timestamp: "2026-07-08T14:00:00Z",
    entry_ts_init: "2026-07-08T14:00:00Z",
    exit_timestamp: "2026-07-08T14:30:00Z",
    exit_ts_init: "2026-07-08T14:30:00Z",
    entry_reference: 100,
    entry_price: 101,
    stop_price: 99,
    target_price: 105,
    exit_price: 105,
    exit_reason: "target",
    gross_points: 4,
    gross_pnl: 120,
    total_commission: 20,
    net_pnl: 100,
    entry_slippage_ticks: 1,
    exit_slippage_ticks: 1,
    tags: {
      signal_kind: "inside",
      daily_regime: "trend",
      regime_strength: 1,
      entry_session: "eth",
      entry_local_time: "10:00",
      entry_layers: ["daily", "mid", "entry"],
      inside_count: 1,
      multiple_inside: false,
      has_sweep_bonus: false,
      lmr_step1_leg_atr: null,
      lmr_step2_leg_atr: null,
      atr_expansion_ratio: 1,
      mfe_r: 1,
      mae_r: -0.2,
      gap_through_target: false,
      volatility_owner_view: "normal",
      volatility_system_daily_atr_percentile: 0.5,
      volatility_system_range_ratio: 1,
      volatility_actual_daily_range: 100,
    },
    decision_evidence: {
      trade_id: "trade-1",
      ordinal: 1,
      entry: {
        signal_kind: "inside",
        signal_timestamp: "2026-07-08T13:55:00Z",
        entry_timestamp: "2026-07-08T14:00:00Z",
        condition_facts: [fact],
        entry_reference: 100,
        fill_price: 101,
      },
      stop: {
        reference_type: "swing_low",
        reference_price: 100,
        offset_ticks: 4,
        final_stop_price: 99,
        condition_facts: [fact],
      },
      exit: {
        actual_reason: "target",
        trigger_timestamp: "2026-07-08T14:30:00Z",
        trigger_price: 105,
        candidates: [
          {
            reason: "target",
            timestamp: "2026-07-08T14:30:00Z",
            price: 105,
            source_sequences: [1],
          },
        ],
        selected_candidate: "target",
        resolution: "first_trigger",
      },
      conservative_assumptions: [
        {
          code: "same_minute_stop_first",
          applied: false,
          effects: [],
          source_sequences: [],
        },
      ],
    },
  };
}

function tradesDocument(kind: "complete" | "unavailable" = "complete") {
  if (kind === "unavailable") {
    return {
      schema: "trades.v1",
      run_id: RUN_ID,
      trades: [],
      decision_evidence_complete: false,
      decision_evidence_availability: "unavailable",
    };
  }
  return {
    schema: "trades.v1",
    run_id: RUN_ID,
    trades: [tradeWire()],
    decision_evidence_complete: true,
  };
}

function eventWire(sequence: number, timestamp: string) {
  return {
    sequence,
    timestamp,
    ts_init: timestamp,
    phase: "intrabar",
    machine: "strategy",
    event_type: "signal_rejected",
    from_state: null,
    to_state: null,
    direction: "long",
    price: 100,
    details: { reason: "entry_break" },
  };
}

function rejectionWire(
  sequence: number,
  overrides: Record<string, unknown> = {},
) {
  const timestamp = new Date(
    Date.parse("2026-07-08T14:00:00Z") + sequence * 60_000,
  )
    .toISOString()
    .replace(".000Z", "Z");
  return {
    evidence_id: `rejection_${String(sequence).padStart(6, "0")}`,
    timestamp,
    ts_init: timestamp,
    trading_date: "2026-07-08",
    direction: "long",
    evaluation_sequence: sequence,
    reached_layers: ["daily", "mid", "entry"],
    condition_facts: [conditionFact(sequence)],
    blocking_condition_ids: ["entry_break"],
    context: {
      candidate_signal_kinds: ["inside"],
      inside_count: 1,
      entry_pullback_state: "blocked",
      mid_pullback_state: "ready",
      daily_regime: "trend",
    },
    source_event_sequences: [sequence],
    ...overrides,
  };
}

function eventsDocument(count = 1) {
  const rejections = Array.from({ length: count }, (_, index) =>
    rejectionWire(index + 1),
  );
  const events = rejections.map((rejection, index) =>
    eventWire(index + 1, rejection.timestamp),
  );
  return {
    schema: "events.v1",
    run_id: RUN_ID,
    events,
    evidence_complete: true,
    rejection_evidence: rejections,
    evidence_summary: {
      availability: "available",
      complete: true,
      evaluation_count: count,
      rejection_count: count,
      layer_reached_counts:
        count === 0 ? {} : { daily: count, mid: count, entry: count },
      blocking_condition_counts: count === 0 ? {} : { entry_break: count },
      deepest_layer: count === 0 ? null : "entry",
      trade_count: 0,
    },
  };
}

function chartDocument(
  timeframe: "D" | "1H" | "5m" = "5m",
  cache = "memory",
) {
  const step = timeframe === "D" ? 86_400 : timeframe === "1H" ? 3_600 : 300;
  const candles = [0, 1].map((index) => ({
    time: 1_700_000_000 + index * step,
    open: 100,
    high: 110,
    low: 90,
    close: 105,
  }));
  const line = candles.map((candle) => ({
    time: candle.time,
    value: candle.close,
  }));
  return {
    schema: "chart_series.v1",
    run_id: RUN_ID,
    timeframe,
    contract_id: CONTRACT_ID,
    session_name: "eth",
    data_fingerprint: "f".repeat(64),
    lookback_days: 10,
    visible_start: "2026-07-01T00:00:00Z",
    visible_end: "2026-07-22T00:00:00Z",
    candles,
    ema18: line,
    ema50: line,
    ema90: line,
    markers: [],
    levels: [],
    source: "backend_mtf_precompute",
    sidecar_relpath: `chart/${RUN_ID}-${timeframe}.json`,
    cache,
  };
}

function narrativeDocument() {
  return {
    schema: "narrative.v1",
    run_id: RUN_ID,
    count: 1,
    steps: [
      {
        time: "2026-07-08T14:00:00Z",
        time_label: "07-08 14:00",
        layer: "Entry",
        tone: "warn",
        text: "Signal rejected",
      },
    ],
    note: "deterministic",
  };
}

const DECISION_REQUEST: P5PromotionDecisionRequest = {
  schema: "promotion_decision_request.v1",
  request_id: "11111111-1111-4111-8111-111111111111",
  decision: "use",
  reason: "Owner approved",
};

function parsedCurrentMain() {
  const parsed = parseP5ResultMain(envelope(mainDocument()), RUN_ID);
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  return parsed.value;
}

function decisionRecord(
  request: P5PromotionDecisionRequest = DECISION_REQUEST,
  decisionId = "decision-1",
  createdAt = "2026-07-28T10:00:00Z",
) {
  return {
    schema: "promotion_decision.v1",
    decision_id: decisionId,
    request_id: request.request_id,
    run_id: RUN_ID,
    strategy: {
      strategy_id: "strategy-0001",
      content_sha256: "a".repeat(64),
    },
    result_sha256: "b".repeat(64),
    decision: request.decision,
    reason: request.reason,
    scorecard_snapshot: [] as unknown[],
    created_at: createdAt,
  };
}

function decisionHistory(records: ReturnType<typeof decisionRecord>[] = []) {
  return {
    schema: "promotion_decision_list.v1",
    run_id: RUN_ID,
    count: records.length,
    decisions: records,
  };
}

function equityDocument() {
  return {
    schema: "equity_curve.v1",
    run_id: RUN_ID,
    points: [
      {
        timestamp: "2026-07-08T14:00:00Z",
        equity: 100_000,
        cumulative_net_pnl: 0,
      },
    ],
  };
}

function exportMemberBodies() {
  return {
    "result.json": mainDocument(),
    [`trades/${RUN_ID}.json`]: {
      schema: "trades.v1",
      run_id: RUN_ID,
      trades: [],
      decision_evidence_complete: true,
    },
    [`equity/${RUN_ID}.json`]: equityDocument(),
    [`events/${RUN_ID}.json`]: eventsDocument(0),
  };
}

function binaryEnvelope(
  bytes: Uint8Array,
  overrides: Partial<P5ExportHttpResult> = {},
): P5ExportHttpResult {
  const owned = Uint8Array.from(bytes);
  return {
    path: `/api/v1/runs/${RUN_ID}/export`,
    status: 200,
    ok: true,
    contentType: "application/zip",
    contentDisposition: `attachment; filename="result-${RUN_ID}.zip"`,
    arrayBuffer: owned.buffer,
    bytes: new Uint8Array(owned.buffer),
    rawText: "",
    ...overrides,
  };
}

async function exportEnvelope(
  options: {
    order?: string[];
    bodies?: Record<string, unknown>;
    response?: Partial<P5ExportHttpResult>;
  } = {},
): Promise<P5ExportHttpResult> {
  const bodies: Record<string, unknown> =
    options.bodies ?? exportMemberBodies();
  const order = options.order ?? Object.keys(bodies);
  const zip = new JSZip();
  for (const path of order) {
    zip.file(path, JSON.stringify(bodies[path]), {
      createFolders: false,
      compression: "STORE",
    });
  }
  const bytes = await zip.generateAsync({
    type: "uint8array",
    compression: "STORE",
  });
  return binaryEnvelope(bytes, options.response);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("P5 unknown-body GET transport", () => {
  it("uses exact GET paths, one encoding pass and the caller signal", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(input), init });
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }),
    );
    const controller = new AbortController();
    await fetchP5Runs(controller.signal);
    await fetchP5ConfirmedStrategies(controller.signal);
    await fetchP5Run("a/b%20", controller.signal);
    await fetchP5RunEvents("a/b%20", controller.signal);
    await fetchP5RunChart("a/b%20", "1H", controller.signal);
    await fetchP5RunNarrative("a/b%20", controller.signal);
    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/runs",
      "/api/v1/strategies?status=confirmed",
      "/api/v1/runs/a%2Fb%2520",
      "/api/v1/runs/a%2Fb%2520/events",
      "/api/v1/runs/a%2Fb%2520/chart?tf=1H",
      "/api/v1/runs/a%2Fb%2520/narrative",
    ]);
    expect(calls.every((call) => call.init?.method === "GET")).toBe(true);
    expect(calls.every((call) => call.init?.signal === controller.signal)).toBe(
      true,
    );
  });

  it("preserves complete non-2xx text and reports invalid JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("not-json full body", { status: 503 })),
    );
    const result = await fetchP5Runs();
    expect(result).toMatchObject({
      path: "/api/v1/runs",
      status: 503,
      ok: false,
      rawText: "not-json full body",
      jsonParsed: false,
      body: null,
    });
  });

  it("keeps AbortError recognizable and adds path to network failures", async () => {
    const abort = new DOMException("cancelled", "AbortError");
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(abort)));
    await expect(fetchP5Runs()).rejects.toBe(abort);

    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new Error("down"))));
    await expect(fetchP5Run("run-x")).rejects.toMatchObject({
      name: "P5TransportError",
      path: "/api/v1/runs/run-x",
    } satisfies Partial<P5TransportError>);
  });
});

describe("P5 Stage 2 transport", () => {
  it("uses exact export/history/POST paths and preserves original bytes/body", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const exportBytes = new Uint8Array([80, 75, 3, 4, 9, 8, 7]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
        if (url.endsWith("/export")) {
          return new Response(exportBytes, {
            status: 200,
            headers: {
              "Content-Type": "application/zip",
              "Content-Disposition":
                'attachment; filename="result-a%2Fb%2520.zip"',
            },
          });
        }
        return jsonResponseForContract(
          url.endsWith("/promotion-decisions") &&
            init?.method === "POST"
            ? decisionRecord(DECISION_REQUEST)
            : decisionHistory(),
        );
      }),
    );
    const exported = await fetchP5RunExport("a/b%20");
    await fetchP5RunPromotionDecisions("a/b%20");
    await postP5RunPromotionDecision("a/b%20", DECISION_REQUEST);

    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/runs/a%2Fb%2520/export",
      "/api/v1/runs/a%2Fb%2520/promotion-decisions",
      "/api/v1/runs/a%2Fb%2520/promotion-decisions",
    ]);
    expect(Array.from(exported.bytes)).toEqual(Array.from(exportBytes));
    expect(Array.from(new Uint8Array(exported.arrayBuffer))).toEqual(
      Array.from(exportBytes),
    );
    expect(calls[2].init).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    expect(JSON.parse(String(calls[2].init?.body))).toEqual(
      DECISION_REQUEST,
    );
    expect(Object.keys(JSON.parse(String(calls[2].init?.body)))).toEqual([
      "schema",
      "request_id",
      "decision",
      "reason",
    ]);
  });

  it("treats POST/binary body-read failures as transport unknown and preserves AbortError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        status: 200,
        ok: true,
        headers: new Headers(),
        text: () => Promise.reject(new Error("read failed")),
      })),
    );
    await expect(
      postP5RunPromotionDecision(RUN_ID, DECISION_REQUEST),
    ).rejects.toMatchObject({
      name: "P5TransportError",
      path: `/api/v1/runs/${RUN_ID}/promotion-decisions`,
    });

    const abort = new DOMException("cancelled", "AbortError");
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(abort)));
    await expect(fetchP5RunExport(RUN_ID)).rejects.toBe(abort);
  });
});

describe("run-list strict parser and mapping", () => {
  it("accepts exact run_list.v1 and preserves null versus zero", () => {
    const parsed = parseP5RunList(
      envelope(
        runList([
          runRow({ run_id: "zero", trade_count: 0 }),
          runRow({
            run_id: "unknown",
            trade_count: null,
            net_r: null,
            net_pnl: null,
            max_drawdown_pnl: null,
          }),
        ]),
      ),
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.runs.map((run) => run.trade_count)).toEqual([0, null]);
    const zero = mapP5RunListItem(parsed.value.runs[0], "策略甲", true);
    expect(zero.whyLine).toBe("0 成交 · 點入去睇阻擋條件");
    expect(zero.whyLine).not.toContain("截住：ok");
    expect(
      mapP5RunListItem(parsed.value.runs[1], "策略甲", true).whyLine,
    ).toContain("未提供");
  });

  it.each([
    ["wrong schema", (body: ReturnType<typeof runList>) => (body.schema = "x")],
    ["wrong count", (body: ReturnType<typeof runList>) => (body.count = 2)],
    [
      "extra key",
      (body: ReturnType<typeof runList>) =>
        Object.assign(body.runs[0], { surprise: 1 }),
    ],
    [
      "non-boolean validation",
      (body: ReturnType<typeof runList>) =>
        Object.assign(body.runs[0], { validation_run: 0 }),
    ],
    [
      "missing validation",
      (body: ReturnType<typeof runList>) => {
        delete (
          body.runs[0] as Partial<ReturnType<typeof runRow>>
        ).validation_run;
      },
    ],
  ])("rejects %s", (_label, mutate) => {
    const body = runList();
    mutate(body);
    expect(parseP5RunList(envelope(body)).ok).toBe(false);
  });

  it("rejects duplicate run id and invalid HTTP/JSON envelope", () => {
    expect(
      parseP5RunList(envelope(runList([runRow(), runRow()]))).ok,
    ).toBe(false);
    const invalidJson = envelope(null);
    invalidJson.jsonParsed = false;
    invalidJson.rawText = "{";
    expect(parseP5RunList(invalidJson).ok).toBe(false);
    expect(parseP5RunList(envelope(runList(), "/runs", 503)).ok).toBe(false);
  });
});

describe("result main strict profiles and identity", () => {
  it.each([
    ["compact", "legacy-compact"],
    ["enriched", "legacy-enriched"],
    ["current", "current-complete"],
  ] as const)("accepts %s approved profile", (profile, expected) => {
    const parsed = parseP5ResultMain(
      envelope(mainDocument(profile)),
      RUN_ID,
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.profile).toBe(expected);
      expect(parsed.value.contractId).toBe(CONTRACT_ID);
    }
  });

  it("rejects route/main/manifest identity mismatch and validation direct URL", () => {
    expect(
      parseP5ResultMain(envelope(mainDocument()), "another-run").ok,
    ).toBe(false);
    const manifestMismatch = mainDocument();
    (
      (manifestMismatch.run as Record<string, unknown>)
        .manifest as Record<string, unknown>
    ).run_id = "another-run";
    expect(parseP5ResultMain(envelope(manifestMismatch), RUN_ID).ok).toBe(false);
    const validation = mainDocument();
    (
      (validation.run as Record<string, unknown>).manifest as Record<
        string,
        unknown
      >
    ).validation_run = true;
    const parsed = parseP5ResultMain(envelope(validation), RUN_ID);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.error).toContain("工程驗證結果");
  });

  it("rejects partial metric enrichment and main extra key", () => {
    const partial = mainDocument("compact");
    (partial.metrics as Record<string, unknown>).max_drawdown_r = 0;
    expect(parseP5ResultMain(envelope(partial), RUN_ID).ok).toBe(false);
    const extra = mainDocument();
    extra.unapproved = true;
    expect(parseP5ResultMain(envelope(extra), RUN_ID).ok).toBe(false);
  });

  it("requires exact immutable strategy binding for current-complete only", () => {
    const valid = parseP5ResultMain(envelope(mainDocument()), RUN_ID);
    expect(valid).toMatchObject({
      ok: true,
      value: {
        strategyBinding: {
          strategy_id: "strategy-0001",
          content_sha256: "a".repeat(64),
          universe_authorized: true,
          overrides: [],
        },
      },
    });
    const typeOnlyFields = mainDocument();
    const typeOnlyBinding = (
      (typeOnlyFields.run as {
        manifest: Record<string, unknown>;
      }).manifest.strategy_binding as Record<string, unknown>
    );
    typeOnlyBinding.strategy_name = "";
    typeOnlyBinding.universe_contracts = ["", ""];
    expect(
      parseP5ResultMain(envelope(typeOnlyFields), RUN_ID).ok,
    ).toBe(true);

    for (const mutate of [
      (binding: Record<string, unknown>) => {
        binding.strategy_id = "strategy-other";
      },
      (binding: Record<string, unknown>) => {
        binding.content_sha256 = "A".repeat(64);
      },
      (binding: Record<string, unknown>) => {
        binding.universe_authorized = false;
      },
      (binding: Record<string, unknown>) => {
        binding.overrides = [
          { path: "x", spec_value: "one", applied_value: "two" },
        ];
      },
    ]) {
      const body = mainDocument();
      const manifest = (body.run as {
        manifest: Record<string, unknown>;
      }).manifest;
      mutate(manifest.strategy_binding as Record<string, unknown>);
      expect(parseP5ResultMain(envelope(body), RUN_ID).ok).toBe(false);
    }
    const missing = mainDocument();
    delete (
      (missing.run as { manifest: Record<string, unknown> }).manifest
    ).strategy_binding;
    expect(parseP5ResultMain(envelope(missing), RUN_ID).ok).toBe(false);

    const legacy = parseP5ResultMain(
      envelope(mainDocument("enriched")),
      RUN_ID,
    );
    expect(legacy).toMatchObject({
      ok: true,
      value: { profile: "legacy-enriched", strategyBinding: null },
    });
  });
});

describe("trades exact complete/unavailable profiles", () => {
  it("accepts complete evidence, reconciles count and maps net-R unavailable", () => {
    const parsed = parseP5Trades(
      envelope(tradesDocument()),
      RUN_ID,
      CONTRACT_ID,
      1,
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok || parsed.value.kind !== "complete") return;
    const mapped = mapP5Trades(parsed.value.trades);
    expect(mapped).toHaveLength(1);
    expect(mapped[0].rMultiple).toBeNull();
    expect(mapped[0].pnlUsd).toBe(100);
    expect(mapped[0].focusTimeUtc).toBe("2026-07-08T14:00:00Z");
  });

  it("keeps legacy unavailable distinct from complete known-empty", () => {
    const unavailable = parseP5Trades(
      envelope(tradesDocument("unavailable")),
      RUN_ID,
      CONTRACT_ID,
      0,
    );
    expect(unavailable).toMatchObject({
      ok: true,
      value: { kind: "unavailable" },
    });
    const completeEmpty = {
      schema: "trades.v1",
      run_id: RUN_ID,
      trades: [],
      decision_evidence_complete: true,
    };
    expect(
      parseP5Trades(
        envelope(completeEmpty),
        RUN_ID,
        CONTRACT_ID,
        0,
      ),
    ).toMatchObject({ ok: true, value: { kind: "complete", trades: [] } });
  });

  it("rejects wrong trade count, ordinal and contract identity", () => {
    expect(
      parseP5Trades(
        envelope(tradesDocument()),
        RUN_ID,
        CONTRACT_ID,
        0,
      ).ok,
    ).toBe(false);
    const ordinal = tradesDocument();
    (
      (ordinal.trades[0].decision_evidence as Record<string, unknown>)
    ).ordinal = 2;
    expect(
      parseP5Trades(envelope(ordinal), RUN_ID, CONTRACT_ID, 1).ok,
    ).toBe(false);
    expect(
      parseP5Trades(envelope(tradesDocument()), RUN_ID, "YM", 1).ok,
    ).toBe(false);
  });
});

describe("events reconciliation and Owner A ranking", () => {
  it("distinguishes complete known-empty from legacy unavailable", () => {
    expect(
      parseP5Events(envelope(eventsDocument(0)), RUN_ID, 0),
    ).toMatchObject({
      ok: true,
      value: {
        kind: "complete",
        rejections: [],
        summary: { rejection_count: 0 },
      },
    });
    const unavailable = {
      schema: "events.v1",
      run_id: RUN_ID,
      events: [],
      evidence_complete: false,
      evidence_availability: "unavailable",
    };
    expect(
      parseP5Events(envelope(unavailable), RUN_ID, 0),
    ).toMatchObject({ ok: true, value: { kind: "unavailable" } });
  });

  it("recomputes summary and blocker-to-failed-fact references", () => {
    const drift = eventsDocument();
    drift.evidence_summary.layer_reached_counts.entry = 99;
    expect(parseP5Events(envelope(drift), RUN_ID, 0).ok).toBe(false);
    const wrongBlocker = eventsDocument();
    wrongBlocker.rejection_evidence[0].blocking_condition_ids = ["missing"];
    expect(parseP5Events(envelope(wrongBlocker), RUN_ID, 0).ok).toBe(false);
  });

  it("selects current 14-record expected ids exactly", () => {
    const parsed = parseP5Events(envelope(eventsDocument(14)), RUN_ID, 0);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok || parsed.value.kind !== "complete") return;
    expect(
      selectClosestRejections(parsed.value.rejections).records.map(
        (item) => item.evidence_id,
      ),
    ).toEqual([
      "rejection_000014",
      "rejection_000013",
      "rejection_000012",
    ]);
  });

  it("uses structural tie levels and never numeric actual-required distance", () => {
    const parsed = parseP5Events(envelope(eventsDocument(4)), RUN_ID, 0);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok || parsed.value.kind !== "complete") return;
    const records = clone(parsed.value.rejections);
    records[0].reached_layers = ["daily", "mid", "entry", "execution"];
    records[0].blocking_condition_ids = ["entry_break"];
    records[0].condition_facts[0].actual = -999_999;
    records[0].condition_facts[0].required = 999_999;
    records[1].condition_facts[0].actual = 100.999999;
    records[1].condition_facts[0].required = 101;
    expect(selectClosestRejections(records).records[0].evidence_id).toBe(
      records[0].evidence_id,
    );
    const before = selectClosestRejections(records).records.map(
      (item) => item.evidence_id,
    );
    records.forEach((record, index) => {
      record.condition_facts[0].actual = index * 1_000_000;
      record.condition_facts[0].required = -index * 1_000_000;
    });
    expect(
      selectClosestRejections(records).records.map((item) => item.evidence_id),
    ).toEqual(before);
  });

  it("applies every structural comparator tie level in order", () => {
    const parsed = parseP5Events(envelope(eventsDocument(2)), RUN_ID, 0);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok || parsed.value.kind !== "complete") return;
    const base = parsed.value.rejections;
    const winner = (
      mutate: (
        first: (typeof base)[number],
        second: (typeof base)[number],
      ) => void,
    ) => {
      const [first, second] = clone(base);
      mutate(first, second);
      return selectClosestRejections([first, second]).records[0].evidence_id;
    };

    expect(
      winner((first, second) => {
        first.reached_layers = ["entry"];
        second.reached_layers = ["daily", "mid"];
      }),
    ).toBe("rejection_000001");
    expect(
      winner((first, second) => {
        first.reached_layers = ["daily", "entry"];
        second.reached_layers = ["entry"];
      }),
    ).toBe("rejection_000001");
    expect(
      winner((first, second) => {
        first.blocking_condition_ids = ["entry_break"];
        second.blocking_condition_ids = ["a", "b"];
      }),
    ).toBe("rejection_000001");
    expect(
      winner((first, second) => {
        first.evaluation_sequence = 9;
        second.evaluation_sequence = 8;
      }),
    ).toBe("rejection_000001");
    expect(
      winner((first, second) => {
        first.evaluation_sequence = 8;
        second.evaluation_sequence = 8;
        first.timestamp = "2026-07-08T16:00:00Z";
        second.timestamp = "2026-07-08T15:00:00Z";
      }),
    ).toBe("rejection_000001");
    expect(
      winner((first, second) => {
        first.evaluation_sequence = 8;
        second.evaluation_sequence = 8;
        first.timestamp = "2026-07-08T15:00:00Z";
        second.timestamp = "2026-07-08T15:00:00Z";
        first.evidence_id = "rejection_z";
        second.evidence_id = "rejection_a";
      }),
    ).toBe("rejection_z");
  });

  it("retains unknown condition ids as exact raw facts", () => {
    const body = eventsDocument();
    body.rejection_evidence[0].condition_facts[0].condition_id =
      "future_condition";
    body.rejection_evidence[0].blocking_condition_ids = ["future_condition"];
    body.evidence_summary.blocking_condition_counts = {
      future_condition: 1,
    } as typeof body.evidence_summary.blocking_condition_counts;
    const parsed = parseP5Events(envelope(body), RUN_ID, 0);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok || parsed.value.kind !== "complete") return;
    expect(
      mapP5NearMisses(parsed.value.rejections).nearMisses?.[0].values,
    ).toContain("future_condition");
  });

  it("refuses to rank unknown layer but retains parsed exact raw facts", () => {
    const parsed = parseP5Events(envelope(eventsDocument()), RUN_ID, 0);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok || parsed.value.kind !== "complete") return;
    const unknown = clone(parsed.value.rejections);
    unknown[0].reached_layers.push("future-layer");
    expect(selectClosestRejections(unknown)).toMatchObject({
      supported: false,
      reason: "排序規則未支援此 layer",
    });
    expect(mapP5NearMisses(parsed.value.rejections).nearMisses?.[0].values).toContain(
      "entry_break",
    );
  });
});

describe("chart and narrative exact identity", () => {
  it.each(P5_CHART_CACHE_STATES)(
    "accepts complete chart cache provenance %s",
    (cache) => {
      const parsed = parseP5Chart(
        envelope(chartDocument("5m", cache)),
        RUN_ID,
        "5m",
        CONTRACT_ID,
      );
      expect(parsed.ok).toBe(true);
      if (parsed.ok) expect(parsed.value.cache).toBe(cache);
    },
  );

  it("accepts optional compute_provenance and maps backend badge", () => {
    const chart = chartDocument("5m", "memory");
    (chart as { compute_provenance?: unknown }).compute_provenance = {
      schema: "compute_provenance.v1",
      feature: "chart_series_product",
      requested_backend: "accelerated",
      effective_backend: "rust",
      fallback_reason: null,
      writes_authority: false,
    };
    const parsed = parseP5Chart(envelope(chart), RUN_ID, "5m", CONTRACT_ID);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.compute_provenance?.effective_backend).toBe("rust");
    const pane = mapP5Chart(parsed.value);
    expect(pane.computeBackend?.effectiveBackend).toBe("rust");
    expect(pane.computeBackend?.writesAuthority).toBe(false);
  });

  it.each([
    ["missing", undefined],
    ["null", null],
    ["blank", ""],
    ["bypass", "bypass"],
    ["unknown", "future-cache"],
  ])("rejects chart cache %s", (kind, cache) => {
    const chart = chartDocument();
    if (kind === "missing") {
      delete (chart as Partial<typeof chart>).cache;
    } else {
      (chart as { cache?: unknown }).cache = cache;
    }
    expect(
      parseP5Chart(envelope(chart), RUN_ID, "5m", CONTRACT_ID).ok,
    ).toBe(false);
  });

  it("rejects wrong run/timeframe/contract, OHLC and nonfinite line", () => {
    expect(
      parseP5Chart(
        envelope(chartDocument()),
        "wrong-run",
        "5m",
        CONTRACT_ID,
      ).ok,
    ).toBe(false);
    expect(
      parseP5Chart(
        envelope(chartDocument("1H")),
        RUN_ID,
        "5m",
        CONTRACT_ID,
      ).ok,
    ).toBe(false);
    expect(
      parseP5Chart(
        envelope(chartDocument()),
        RUN_ID,
        "5m",
        "YM",
      ).ok,
    ).toBe(false);
    const ohlc = chartDocument();
    ohlc.candles[0].low = 120;
    expect(
      parseP5Chart(envelope(ohlc), RUN_ID, "5m", CONTRACT_ID).ok,
    ).toBe(false);
    const nonfinite = chartDocument();
    nonfinite.ema18[0].value = Number.POSITIVE_INFINITY;
    expect(
      parseP5Chart(envelope(nonfinite), RUN_ID, "5m", CONTRACT_ID).ok,
    ).toBe(false);
  });

  it("parses narrative exact count and identity", () => {
    expect(
      parseP5Narrative(envelope(narrativeDocument()), RUN_ID).ok,
    ).toBe(true);
    const wrongCount = narrativeDocument();
    wrongCount.count = 2;
    expect(parseP5Narrative(envelope(wrongCount), RUN_ID).ok).toBe(false);
    expect(
      parseP5Narrative(envelope(narrativeDocument()), "other").ok,
    ).toBe(false);
  });
});

describe("optional strategy label projection", () => {
  it("accepts valid catalog and rejects invalid/duplicate as a whole", () => {
    const valid = {
      schema: "strategy_version_list.v1",
      count: 1,
      versions: [
        {
          strategy_id: "strategy-0001",
          name: "Trend pullback",
          status: "confirmed",
        },
      ],
    };
    const parsed = parseP5StrategyLabels(envelope(valid));
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.get("strategy-0001")).toBe("Trend pullback");
    }
    const duplicate = clone(valid);
    duplicate.count = 2;
    duplicate.versions.push(clone(duplicate.versions[0]));
    expect(parseP5StrategyLabels(envelope(duplicate)).ok).toBe(false);
    const invalid = clone(valid);
    invalid.versions[0].name = " ";
    expect(parseP5StrategyLabels(envelope(invalid)).ok).toBe(false);
  });
});

describe("P5 immutable export adapter", () => {
  it("validates exact headers/member order/four schemas and returns original bytes", async () => {
    const input = await exportEnvelope();
    const parsed = await parseP5ResultExport(input, parsedCurrentMain());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.value.bytes).toBe(input.bytes);
    expect(parsed.value.arrayBuffer).toBe(input.arrayBuffer);
    expect(parsed.value.fileName).toBe(`result-${RUN_ID}.zip`);
    expect(parsed.value.members).toEqual([
      "result.json",
      `trades/${RUN_ID}.json`,
      `equity/${RUN_ID}.json`,
      `events/${RUN_ID}.json`,
    ]);
  });

  it.each([
    [
      "404",
      async () =>
        exportEnvelope({
          response: { status: 404, ok: false, rawText: "full missing" },
        }),
    ],
    [
      "503",
      async () =>
        exportEnvelope({
          response: { status: 503, ok: false, rawText: "full down" },
        }),
    ],
    [
      "wrong content type",
      async () =>
        exportEnvelope({ response: { contentType: "application/json" } }),
    ],
    [
      "wrong filename",
      async () =>
        exportEnvelope({
          response: {
            contentDisposition:
              'attachment; filename="result-another.zip"',
          },
        }),
    ],
    [
      "bad zip",
      async () => binaryEnvelope(new Uint8Array([80, 75, 3, 4])),
    ],
  ])("rejects %s before download success", async (_label, build) => {
    const parsed = await parseP5ResultExport(
      await build(),
      parsedCurrentMain(),
    );
    expect(parsed.ok).toBe(false);
  });

  it("rejects wrong order, extra member and sidecar identity/ref drift", async () => {
    const bodies = exportMemberBodies();
    const names = Object.keys(bodies);
    expect(
      (
        await parseP5ResultExport(
          await exportEnvelope({
            order: [names[1], names[0], names[2], names[3]],
            bodies,
          }),
          parsedCurrentMain(),
        )
      ).ok,
    ).toBe(false);

    const extraBodies = {
      ...exportMemberBodies(),
      "extra.json": { no: "extras" },
    };
    expect(
      (
        await parseP5ResultExport(
          await exportEnvelope({ bodies: extraBodies }),
          parsedCurrentMain(),
        )
      ).ok,
    ).toBe(false);

    const wrongTrade = exportMemberBodies();
    (
      wrongTrade[`trades/${RUN_ID}.json`] as Record<string, unknown>
    ).run_id = "wrong-run";
    expect(
      (
        await parseP5ResultExport(
          await exportEnvelope({ bodies: wrongTrade }),
          parsedCurrentMain(),
        )
      ).ok,
    ).toBe(false);

    const wrongRef = exportMemberBodies();
    (wrongRef["result.json"] as Record<string, unknown>).events_ref =
      "events/wrong.json";
    expect(
      (
        await parseP5ResultExport(
          await exportEnvelope({ bodies: wrongRef }),
          parsedCurrentMain(),
        )
      ).ok,
    ).toBe(false);

    const badEquity = exportMemberBodies();
    const point = (
      badEquity[`equity/${RUN_ID}.json`] as {
        points: Array<Record<string, unknown>>;
      }
    ).points[0];
    point.equity = Number.POSITIVE_INFINITY;
    expect(
      (
        await parseP5ResultExport(
          await exportEnvelope({ bodies: badEquity }),
          parsedCurrentMain(),
        )
      ).ok,
    ).toBe(false);
  });
});

describe("P5 PromotionDecision/history strict adapters", () => {
  it("accepts empty and exact multiple ordered history", () => {
    const main = parsedCurrentMain();
    expect(
      parseP5PromotionDecisionHistory(
        envelope(decisionHistory()),
        main,
      ),
    ).toMatchObject({
      ok: true,
      value: { count: 0, decisions: [] },
    });
    const secondRequest = {
      ...DECISION_REQUEST,
      request_id: "22222222-2222-4222-8222-222222222222",
      decision: "return" as const,
      reason: "Needs work",
    };
    const records = [
      decisionRecord(
        DECISION_REQUEST,
        "decision-a",
        "2026-07-28T10:00:00Z",
      ),
      decisionRecord(
        secondRequest,
        "decision-b",
        "2026-07-28T11:00:00Z",
      ),
    ];
    expect(
      parseP5PromotionDecisionHistory(
        envelope(decisionHistory(records)),
        main,
      ),
    ).toMatchObject({
      ok: true,
      value: {
        count: 2,
        decisions: [
          { decision_id: "decision-a" },
          { decision_id: "decision-b" },
        ],
      },
    });
  });

  it("rejects count/run/order/id/hash/strategy/scorecard drift", () => {
    const main = parsedCurrentMain();
    const mutations: Array<
      (history: ReturnType<typeof decisionHistory>) => void
    > = [
      (history) => {
        history.count = 2;
      },
      (history) => {
        history.run_id = "wrong-run";
      },
      (history) => {
        history.decisions[0].run_id = "wrong-run";
      },
      (history) => {
        history.decisions[0].strategy.content_sha256 = "c".repeat(64);
      },
      (history) => {
        history.decisions[0].result_sha256 = "UPPER";
      },
      (history) => {
        history.decisions[0].scorecard_snapshot = [
          { dim: "drift" },
        ];
      },
    ];
    for (const mutate of mutations) {
      const history = decisionHistory([decisionRecord()]);
      mutate(history);
      expect(
        parseP5PromotionDecisionHistory(envelope(history), main).ok,
      ).toBe(false);
    }

    const later = {
      ...DECISION_REQUEST,
      request_id: "22222222-2222-4222-8222-222222222222",
    };
    const reversed = decisionHistory([
      decisionRecord(later, "decision-b", "2026-07-28T11:00:00Z"),
      decisionRecord(
        DECISION_REQUEST,
        "decision-a",
        "2026-07-28T10:00:00Z",
      ),
    ]);
    expect(
      parseP5PromotionDecisionHistory(envelope(reversed), main).ok,
    ).toBe(false);
  });

  it("accepts only a strict 200 record matching immutable request snapshot", () => {
    const main = parsedCurrentMain();
    expect(
      parseP5PromotionDecisionRecord(
        envelope(decisionRecord()),
        main,
        DECISION_REQUEST,
      ),
    ).toMatchObject({
      ok: true,
      value: {
        request_id: DECISION_REQUEST.request_id,
        decision: "use",
        reason: "Owner approved",
      },
    });
    const mismatch = decisionRecord();
    mismatch.decision = "return";
    expect(
      parseP5PromotionDecisionRecord(
        envelope(mismatch),
        main,
        DECISION_REQUEST,
      ).ok,
    ).toBe(false);
    expect(
      parseP5PromotionDecisionRecord(
        envelope(decisionRecord(), "/decision", 503),
        main,
        DECISION_REQUEST,
      ).ok,
    ).toBe(false);
  });

  it("guards late history from overwriting a newer POST revision", () => {
    expect(shouldCommitP5DecisionHistory(3, 3)).toBe(true);
    expect(shouldCommitP5DecisionHistory(3, 4)).toBe(false);
  });
});
