import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import JSZip from "jszip";

import { isP5GenerationCurrent } from "../lib/results/normalContract";
import {
  __hardResetResultsFixture,
  appendFixtureDecision,
  buildResultZipPayload,
  listFixtureDecisions,
  listFixtureResults,
} from "../lib/results/fixtureStore";
import { __resetReviewReadState } from "../lib/results/readState";
import { ThemeProvider } from "../theme/ThemeProvider";
import { ResultsPage } from "./ResultsPage";
import { RunDetailPage } from "./RunDetailPage";

function TestNavigation() {
  const navigate = useNavigate();
  return (
    <div>
      <button
        type="button"
        data-testid="nav-run-a"
        onClick={() => navigate("/results/run-a")}
      >
        A
      </button>
      <button
        type="button"
        data-testid="nav-run-b"
        onClick={() => navigate("/results/run-b")}
      >
        B
      </button>
      <button
        type="button"
        data-testid="nav-owner"
        onClick={() =>
          navigate("/results/fixture-run-1?scenario=owner-review")
        }
      >
        Owner
      </button>
      <button
        type="button"
        data-testid="nav-normal"
        onClick={() => navigate("/results/run-b")}
      >
        Normal
      </button>
    </div>
  );
}

function renderResults(entry: string) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[entry]}>
        <TestNavigation />
        <Routes>
          <Route path="/results" element={<ResultsPage />} />
          <Route path="/results/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  if (typeof URL.createObjectURL !== "function") {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: () => "blob:test-default",
    });
  }
  if (typeof URL.revokeObjectURL !== "function") {
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: () => undefined,
    });
  }
  localStorage.clear();
  __hardResetResultsFixture();
  __resetReviewReadState();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function liveRunRow(
  runId: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    run_id: runId,
    strategy_version: "strategy-0001",
    contract_id: "NQ-202609-CME",
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
    result_file: `results/${runId}.json`,
    ...overrides,
  };
}

function liveRunList(rows: ReturnType<typeof liveRunRow>[]) {
  return { schema: "run_list.v1", count: rows.length, runs: rows };
}

function liveFunnel(tradeCount: number) {
  return {
    schema: "funnel.v1",
    status: "ok",
    daily_trend_days: 2,
    evaluations_passing_daily_gate: 4,
    evaluations_passing_mid_gate: 3,
    signals_created: tradeCount,
    fills: tradeCount,
    reject_reasons: {},
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

function liveMain(
  runId: string,
  tradeCount = 0,
  overrides: {
    contractId?: string;
    strategyVersion?: string;
    validationRun?: boolean;
  } = {},
) {
  const contractId = overrides.contractId ?? "NQ-202609-CME";
  const strategyVersion = overrides.strategyVersion ?? "strategy-0001";
  return {
    schema: "result.v1",
    run: {
      run_id: runId,
      strategy_version: strategyVersion,
      manifest: {
        schema: "run_manifest.v1",
        run_id: runId,
        strategy_version: strategyVersion,
        contract_id: contractId,
        session_name: "eth",
        range_start: "2026-07-01T00:00:00Z",
        range_end: "2026-07-22T00:00:00Z",
        validation_run: overrides.validationRun ?? false,
        trading_days: 15,
        strategy_binding: {
          schema: "strategy_binding.v1",
          source: "strategy_file",
          strategy_id: strategyVersion,
          strategy_name: "Trend exact",
          content_sha256: "a".repeat(64),
          universe_contracts: [contractId],
          universe_authorized: true,
          overrides: [],
        },
      },
      engine: { app: "test", version: "1" },
    },
    metrics: {
      trade_count: tradeCount,
      gross_pnl: tradeCount === 0 ? 0 : 110,
      net_pnl: tradeCount === 0 ? 0 : 100,
      net_r: tradeCount === 0 ? 0 : 0.5,
      win_rate: tradeCount === 0 ? null : 1,
      profit_factor: tradeCount === 0 ? null : 2,
      expectancy_r: tradeCount === 0 ? null : 0.5,
      max_drawdown_pnl: tradeCount === 0 ? 0 : 20,
    },
    scorecard: [],
    warnings: [],
    owner_action: null,
    trades_ref: `trades/${runId}.json`,
    equity_curve_ref: `equity/${runId}.json`,
    events_ref: `events/${runId}.json`,
    funnel: liveFunnel(tradeCount),
    decision_evidence_complete: true,
  };
}

interface LiveDecisionRequest {
  schema: "promotion_decision_request.v1";
  request_id: string;
  decision: "use" | "return" | "abandon";
  reason: string;
}

function liveDecisionRecord(
  runId: string,
  request: LiveDecisionRequest,
  decisionId = `decision-${request.request_id}`,
) {
  return {
    schema: "promotion_decision.v1",
    decision_id: decisionId,
    request_id: request.request_id,
    run_id: runId,
    strategy: {
      strategy_id: "strategy-0001",
      content_sha256: "a".repeat(64),
    },
    result_sha256: "b".repeat(64),
    decision: request.decision,
    reason: request.reason,
    scorecard_snapshot: [],
    created_at: "2026-07-28T10:00:00Z",
  };
}

function liveDecisionHistory(
  runId: string,
  decisions: ReturnType<typeof liveDecisionRecord>[] = [],
) {
  return {
    schema: "promotion_decision_list.v1",
    run_id: runId,
    count: decisions.length,
    decisions,
  };
}

async function liveExportBytes(
  runId: string,
  tradeCount = 0,
): Promise<Uint8Array> {
  const members: Array<[string, unknown]> = [
    ["result.json", liveMain(runId, tradeCount)],
    [`trades/${runId}.json`, liveTrades(runId, tradeCount)],
    [
      `equity/${runId}.json`,
      {
        schema: "equity_curve.v1",
        run_id: runId,
        points: [
          {
            timestamp: "2026-07-08T14:00:00Z",
            equity: 100_000,
            cumulative_net_pnl: 0,
          },
        ],
      },
    ],
    [`events/${runId}.json`, liveEvents(runId, tradeCount)],
  ];
  const zip = new JSZip();
  for (const [path, body] of members) {
    zip.file(path, JSON.stringify(body), {
      createFolders: false,
      compression: "STORE",
    });
  }
  return zip.generateAsync({
    type: "uint8array",
    compression: "STORE",
  });
}

function liveExportResponse(runId: string, bytes: Uint8Array): Response {
  return new Response(bytes, {
    status: 200,
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="result-${runId}.zip"`,
    },
  });
}

function liveConditionFact(
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

function liveTrade() {
  const fact = liveConditionFact(1, {
    status: "passed",
    actual: true,
    required: true,
    unit: "boolean",
  });
  return {
    trade_id: "trade-1",
    contract_id: "NQ-202609-CME",
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

function liveTrades(runId: string, tradeCount: number) {
  return {
    schema: "trades.v1",
    run_id: runId,
    trades: tradeCount === 0 ? [] : [liveTrade()],
    decision_evidence_complete: true,
  };
}

function liveEvents(
  runId: string,
  tradeCount: number,
  rejectionCount = 0,
) {
  const rejections = Array.from({ length: rejectionCount }, (_, index) => {
    const sequence = index + 1;
    const timestamp = new Date(
      Date.parse("2026-07-08T14:00:00Z") + index * 60_000,
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
      condition_facts: [liveConditionFact(sequence)],
      blocking_condition_ids: ["entry_break"],
      context: {
        candidate_signal_kinds: ["inside"],
        inside_count: 1,
        entry_pullback_state: "blocked",
        mid_pullback_state: "ready",
        daily_regime: "trend",
      },
      source_event_sequences: [sequence],
    };
  });
  const events = rejections.map((rejection, index) => ({
    sequence: index + 1,
    timestamp: rejection.timestamp,
    ts_init: rejection.timestamp,
    phase: "intrabar",
    machine: "strategy",
    event_type: "signal_rejected",
    from_state: null,
    to_state: null,
    direction: "long",
    price: 100,
    details: { reason: "entry_break" },
  }));
  return {
    schema: "events.v1",
    run_id: runId,
    events,
    evidence_complete: true,
    rejection_evidence: rejections,
    evidence_summary: {
      availability: "available",
      complete: true,
      evaluation_count: rejectionCount,
      rejection_count: rejectionCount,
      layer_reached_counts:
        rejectionCount === 0
          ? {}
          : {
              daily: rejectionCount,
              mid: rejectionCount,
              entry: rejectionCount,
            },
      blocking_condition_counts:
        rejectionCount === 0 ? {} : { entry_break: rejectionCount },
      deepest_layer: rejectionCount === 0 ? null : "entry",
      trade_count: tradeCount,
    },
  };
}

function liveChart(
  runId: string,
  timeframe: "D" | "1H" | "5m",
  contractId = "NQ-202609-CME",
  cache = "memory",
) {
  const step = timeframe === "D" ? 86_400 : timeframe === "1H" ? 3_600 : 300;
  const candles = [0, 1].map((index) => ({
    time: 1_720_444_800 + index * step,
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
    run_id: runId,
    timeframe,
    contract_id: contractId,
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
    sidecar_relpath: `chart/${runId}-${timeframe}.json`,
    cache,
  };
}

function liveNarrative(runId: string) {
  return {
    schema: "narrative.v1",
    run_id: runId,
    count: 1,
    steps: [
      {
        time: "2026-07-08T14:00:00Z",
        time_label: "07-08 14:00",
        layer: "Entry",
        tone: "warn",
        text: "Signal rejected（deterministic）",
      },
    ],
    note: "event log",
  };
}

function confirmedCatalog(name = "Trend exact") {
  return {
    schema: "strategy_version_list.v1",
    count: 1,
    versions: [{ strategy_id: "strategy-0001", name }],
  };
}

function detailFetch(options: {
  runId: string;
  tradeCount?: number;
  rejectionCount?: number;
  eventsStatus?: number;
  eventsBody?: unknown;
  tradesStatus?: number;
  tradesBody?: unknown;
  narrativeStatus?: number;
  narrativeBody?: unknown;
  chartStatus?: Partial<Record<"D" | "1H" | "5m", number>>;
  catalogBody?: unknown;
  mainBody?: unknown;
  historyFetch?: () => Response | Promise<Response>;
  decisionFetch?: (
    request: LiveDecisionRequest,
    init?: RequestInit,
  ) => Response | Promise<Response>;
  exportFetch?: (init?: RequestInit) => Response | Promise<Response>;
}) {
  const tradeCount = options.tradeCount ?? 0;
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/strategies?status=confirmed")) {
      return jsonResponse(options.catalogBody ?? confirmedCatalog());
    }
    if (url.endsWith(`/${options.runId}/trades`)) {
      return jsonResponse(
        options.tradesBody ?? liveTrades(options.runId, tradeCount),
        options.tradesStatus ?? 200,
      );
    }
    if (url.endsWith(`/${options.runId}/events`)) {
      return jsonResponse(
        options.eventsBody ??
          liveEvents(
            options.runId,
            tradeCount,
            options.rejectionCount ?? 0,
          ),
        options.eventsStatus ?? 200,
      );
    }
    if (url.endsWith(`/${options.runId}/narrative`)) {
      return jsonResponse(
        options.narrativeBody ?? liveNarrative(options.runId),
        options.narrativeStatus ?? 200,
      );
    }
    if (url.endsWith(`/${options.runId}/promotion-decisions`)) {
      if (init?.method === "POST") {
        const request = JSON.parse(String(init.body)) as LiveDecisionRequest;
        return options.decisionFetch
          ? options.decisionFetch(request, init)
          : jsonResponse(liveDecisionRecord(options.runId, request));
      }
      return options.historyFetch
        ? options.historyFetch()
        : jsonResponse(liveDecisionHistory(options.runId));
    }
    if (url.endsWith(`/${options.runId}/export`)) {
      return options.exportFetch
        ? options.exportFetch(init)
        : new Response("unexpected export", { status: 404 });
    }
    for (const timeframe of ["D", "1H", "5m"] as const) {
      if (
        url.endsWith(
          `/${options.runId}/chart?tf=${encodeURIComponent(timeframe)}`,
        )
      ) {
        return jsonResponse(
          liveChart(options.runId, timeframe),
          options.chartStatus?.[timeframe] ?? 200,
        );
      }
    }
    if (url.endsWith(`/api/v1/runs/${options.runId}`)) {
      return jsonResponse(
        options.mainBody ?? liveMain(options.runId, tradeCount),
      );
    }
    return new Response("unexpected", { status: 404 });
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function stubBlobUrls() {
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockImplementation((blob: Blob | MediaSource) => {
      void blob;
      return "blob:p5-stage-2";
    });
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  return { createObjectURL, revokeObjectURL };
}

function readBlobBytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () =>
      resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.readAsArrayBuffer(blob);
  });
}

describe("P5 live path", () => {
  it("does not load owner-review fixture on normal /results", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/runs") && !url.includes("/chart")) {
          return new Response(
            JSON.stringify({
              schema: "run_list.v1",
              count: 1,
              runs: [
                {
                  run_id: "live-1",
                  strategy_version: "strategy-0001",
                  contract_id: "NQ",
                  validation_run: false,
                  trade_count: 0,
                  net_r: 0,
                  net_pnl: 0,
                  win_rate: null,
                  max_drawdown_pnl: 0,
                  scorecard_statuses: [],
                  funnel_status: null,
                  funnel_fills: 0,
                  has_scorecard: false,
                  has_funnel: false,
                  result_file: "x",
                  session_name: null,
                  range_start: null,
                  range_end: null,
                  profit_factor: null,
                  max_drawdown_r: null,
                  expectancy_r: null,
                },
              ],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.includes("/api/v1/strategies")) {
          return new Response(
            JSON.stringify({
              schema: "strategy_version_list.v1",
              count: 0,
              versions: [],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderResults("/results");
    await waitFor(() => {
      expect(screen.getByTestId("result-row-live-1")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("result-row-fixture-run-1")).not.toBeInTheDocument();
    expect(screen.queryByText(/Owner 驗收入口/)).not.toBeInTheDocument();
  });

  it("hides validation_run true from main list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/runs")) {
          return new Response(
            JSON.stringify({
              schema: "run_list.v1",
              count: 2,
              runs: [
                {
                  run_id: "val-1",
                  strategy_version: "strategy-0001",
                  contract_id: "NQ",
                  validation_run: true,
                  trade_count: 1,
                  net_r: 1,
                  net_pnl: 1,
                  win_rate: 1,
                  max_drawdown_pnl: 0,
                  scorecard_statuses: [],
                  funnel_status: null,
                  funnel_fills: 1,
                  has_scorecard: false,
                  has_funnel: false,
                  result_file: "x",
                  session_name: null,
                  range_start: null,
                  range_end: null,
                  profit_factor: null,
                  max_drawdown_r: null,
                  expectancy_r: null,
                },
                {
                  run_id: "std-1",
                  strategy_version: "strategy-0001",
                  contract_id: "YM",
                  validation_run: false,
                  trade_count: 2,
                  net_r: 0.5,
                  net_pnl: 100,
                  win_rate: 0.5,
                  max_drawdown_pnl: 10,
                  scorecard_statuses: [],
                  funnel_status: null,
                  funnel_fills: 2,
                  has_scorecard: false,
                  has_funnel: false,
                  result_file: "x",
                  session_name: null,
                  range_start: null,
                  range_end: null,
                  profit_factor: null,
                  max_drawdown_r: null,
                  expectancy_r: null,
                },
              ],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.includes("/strategies")) {
          return new Response(
            JSON.stringify({
              schema: "strategy_version_list.v1",
              count: 0,
              versions: [],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderResults("/results");
    await waitFor(() => {
      expect(screen.getByTestId("result-row-std-1")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("result-row-val-1")).not.toBeInTheDocument();
  });

  it("renders a valid run list before optional catalog, then updates only label", async () => {
    const catalog = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/runs") {
          return jsonResponse(liveRunList([liveRunRow("catalog-run")]));
        }
        if (url.includes("/strategies?status=confirmed")) {
          return catalog.promise;
        }
        return new Response("unexpected", { status: 404 });
      }),
    );
    renderResults("/results");
    await waitFor(() =>
      expect(screen.getByTestId("result-row-catalog-run")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("result-row-catalog-run").textContent).toContain(
      "strategy-0001",
    );
    catalog.resolve(jsonResponse(confirmedCatalog("策略正式名")));
    await waitFor(() =>
      expect(screen.getByTestId("result-row-catalog-run").textContent).toContain(
        "策略正式名",
      ),
    );
    expect(screen.getByTestId("result-row-catalog-run").getAttribute("href")).toBe(
      "/results/catalog-run",
    );
  });

  it.each([
    ["404", jsonResponse({ detail: "missing" }, 404)],
    ["503", jsonResponse({ detail: "down" }, 503)],
    [
      "invalid",
      jsonResponse({
        schema: "strategy_version_list.v1",
        count: 2,
        versions: [],
      }),
    ],
  ])("catalog %s does not drag a valid run list into error", async (_case, response) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/runs") {
          return jsonResponse(liveRunList([liveRunRow("fallback-run")]));
        }
        if (url.includes("/strategies?status=confirmed")) {
          return response;
        }
        return new Response("unexpected", { status: 404 });
      }),
    );
    renderResults("/results");
    await waitFor(() =>
      expect(screen.getByTestId("result-row-fallback-run")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("results-error")).toBeNull();
    expect(screen.getByTestId("result-row-fallback-run").textContent).toContain(
      "strategy-0001",
    );
  });

  it.each([
    ["schema", { schema: "wrong", count: 0, runs: [] }],
    ["count", { schema: "run_list.v1", count: 1, runs: [] }],
    [
      "duplicate",
      liveRunList([liveRunRow("same"), liveRunRow("same")]),
    ],
    [
      "classification",
      liveRunList([
        liveRunRow("bad-classification", { validation_run: "false" }),
      ]),
    ],
  ])("invalid list %s is fatal and renders zero stale rows", async (_case, body) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input) === "/api/v1/runs"
          ? jsonResponse(body)
          : jsonResponse(confirmedCatalog()),
      ),
    );
    renderResults("/results");
    await waitFor(() =>
      expect(screen.getByTestId("results-error")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("results-list")).toBeNull();
  });

  it("uses exact zero-trade copy and never treats funnel_status ok as blocker", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input) === "/api/v1/runs"
          ? jsonResponse(liveRunList([liveRunRow("zero-copy")]))
          : jsonResponse(confirmedCatalog()),
      ),
    );
    renderResults("/results");
    await waitFor(() =>
      expect(screen.getByTestId("result-row-zero-copy")).toBeInTheDocument(),
    );
    const text = screen.getByTestId("result-row-zero-copy").textContent ?? "";
    expect(text).toContain("0 成交 · 點入去睇阻擋條件");
    expect(text).not.toContain("截住：ok");
  });
});

describe("P5 normal detail strict main + isolated children", () => {
  it("rejects stale generation tokens", () => {
    expect(isP5GenerationCurrent(8, 8)).toBe(true);
    expect(isP5GenerationCurrent(9, 8)).toBe(false);
  });

  it("verifies main before mark-read, then launches six children plus history in parallel", async () => {
    const fetchMock = detailFetch({
      runId: "run-a",
      rejectionCount: 1,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderResults("/results/run-a");

    await waitFor(() =>
      expect(screen.getByText(/Trend exact · NQ-202609-CME/)).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText(/Signal rejected（deterministic）/)).toBeInTheDocument(),
    );
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.filter((url) => url.endsWith("/run-a/trades"))).toHaveLength(1);
    expect(urls.filter((url) => url.endsWith("/run-a/events"))).toHaveLength(1);
    expect(urls.filter((url) => url.endsWith("/run-a/narrative"))).toHaveLength(1);
    expect(urls.filter((url) => url.includes("/run-a/chart?tf="))).toHaveLength(3);
    expect(
      urls.filter((url) =>
        url.endsWith("/run-a/promotion-decisions"),
      ),
    ).toHaveLength(1);
    expect(urls.some((url) => url.includes("tf=30m"))).toBe(false);
    expect(localStorage.getItem("p5-result-read-v1")).toContain('"run-a":true');
    expect(screen.getByTestId("volume-unavailable")).toHaveTextContent(
      "後端未提供",
    );
    expect(screen.getByTestId("chart-missing-30m")).toHaveTextContent(
      "唔會用 1H／5m 冒充",
    );
    expect(
      screen.getByRole("link", { name: "用咗策略版本" }),
    ).toHaveAttribute(
      "href",
      "/strategies?tab=library&strategy=strategy-0001",
    );
  });

  it("invalid or validation main is fatal, unmarked, and starts zero child calls", async () => {
    const invalid = liveMain("wrong-run");
    const fetchMock = detailFetch({ runId: "run-a", mainBody: invalid });
    vi.stubGlobal("fetch", fetchMock);
    renderResults("/results/run-a");
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("run id"),
    );
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.filter((url) => /\/(trades|events|narrative)$/.test(url))).toHaveLength(
      0,
    );
    expect(urls.filter((url) => url.includes("/chart?tf="))).toHaveLength(0);
    expect(localStorage.getItem("p5-result-read-v1")).toBeNull();

    const validationFetch = detailFetch({
      runId: "run-b",
      mainBody: liveMain("run-b", 0, { validationRun: true }),
    });
    vi.stubGlobal("fetch", validationFetch);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("nav-run-b"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("工程驗證結果"),
    );
    expect(localStorage.getItem("p5-result-read-v1") ?? "").not.toContain(
      '"run-b":true',
    );
  });

  it("events failure is isolated from main, narrative and all valid charts", async () => {
    const fetchMock = detailFetch({
      runId: "run-a",
      eventsStatus: 503,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderResults("/results/run-a");
    await waitFor(() =>
      expect(screen.getByTestId("events-error")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Trend exact · NQ-202609-CME/)).toBeInTheDocument();
    expect(screen.getByText(/Signal rejected（deterministic）/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart-missing-D")).toBeNull();
    expect(screen.queryByTestId("chart-missing-1H")).toBeNull();
    expect(screen.queryByTestId("chart-missing-5m")).toBeNull();
  });

  it("trades failure is isolated from main, events, narrative and charts", async () => {
    vi.stubGlobal(
      "fetch",
      detailFetch({
        runId: "run-a",
        tradeCount: 1,
        rejectionCount: 1,
        tradesStatus: 503,
      }),
    );
    renderResults("/results/run-a");
    await waitFor(() =>
      expect(screen.getByTestId("trades-error")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Signal rejected（deterministic）/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart-missing-D")).toBeNull();
    expect(screen.queryByTestId("chart-missing-1H")).toBeNull();
    expect(screen.queryByTestId("chart-missing-5m")).toBeNull();
  });

  it("narrative failure is isolated from main, events, trades and charts", async () => {
    vi.stubGlobal(
      "fetch",
      detailFetch({
        runId: "run-a",
        tradeCount: 1,
        rejectionCount: 1,
        narrativeStatus: 503,
      }),
    );
    renderResults("/results/run-a");
    const narrative = await screen.findByRole("region", { name: "判斷鏈" });
    await waitFor(() =>
      expect(narrative.querySelector('[role="alert"]')).toBeInTheDocument(),
    );
    expect(screen.getByTestId("trade-1")).toBeInTheDocument();
    expect(screen.queryByTestId("chart-missing-D")).toBeNull();
    expect(screen.queryByTestId("chart-missing-1H")).toBeNull();
    expect(screen.queryByTestId("chart-missing-5m")).toBeNull();
  });

  it("known-empty events stays distinct from legacy unavailable", async () => {
    vi.stubGlobal("fetch", detailFetch({ runId: "run-a" }));
    renderResults("/results/run-a");
    await waitFor(() =>
      expect(screen.getByTestId("events-empty")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("events-unavailable")).toBeNull();
  });

  it("one chart failure stays in that pane; miss_no_write remains a valid chart", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/run-a/chart?tf=D")) {
        return jsonResponse({ detail: "D unavailable" }, 503);
      }
      if (url.endsWith("/run-a/chart?tf=1H")) {
        return jsonResponse(liveChart("run-a", "1H", "NQ-202609-CME", "miss_no_write"));
      }
      return detailFetch({ runId: "run-a" })(input);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderResults("/results/run-a");
    await waitFor(() =>
      expect(screen.getByTestId("chart-missing-D")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("chart-missing-1H")).toBeNull();
    expect(screen.queryByTestId("chart-missing-5m")).toBeNull();
  });

  it("keeps legacy trades unavailable distinct while other children remain ready", async () => {
    const fetchMock = detailFetch({
      runId: "run-a",
      tradeCount: 1,
      tradesBody: {
        schema: "trades.v1",
        run_id: "run-a",
        trades: [],
        decision_evidence_complete: false,
        decision_evidence_availability: "unavailable",
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderResults("/results/run-a");
    await waitFor(() =>
      expect(screen.getByTestId("trades-unavailable")).toHaveTextContent(
        "歷史 run",
      ),
    );
    expect(screen.getByText(/Signal rejected（deterministic）/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart-missing-5m")).toBeNull();
  });

  it("maps complete trade ordinal/timestamp and shows per-trade R honestly unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      detailFetch({ runId: "run-a", tradeCount: 1 }),
    );
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await waitFor(() =>
      expect(screen.getByTestId("trade-1")).toHaveTextContent("R 未提供"),
    );
    await user.click(screen.getByTestId("trade-1"));
    expect(screen.getByTestId("trade-detail-1")).toHaveTextContent("為何入市");
    expect(screen.getAllByTestId("chart-highlight")[0]).toHaveTextContent("#1");
  });

  it("maps ranked rejection timestamp to chart focus and accepts fewer than three", async () => {
    vi.stubGlobal(
      "fetch",
      detailFetch({ runId: "run-a", rejectionCount: 1 }),
    );
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await waitFor(() =>
      expect(screen.getByTestId("near-miss-list")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("near-miss-2")).toBeNull();
    await user.click(screen.getByTestId("near-miss-1"));
    expect(screen.getAllByTestId("chart-highlight")[0]).toHaveTextContent(
      "近失 #1",
    );
  });

  it("A pending → B ready → late A cannot replace B", async () => {
    const pendingA = deferred<Response>();
    const fallbackB = detailFetch({
      runId: "run-b",
      mainBody: liveMain("run-b", 0, {
        contractId: "YM-202609-CBOT",
        strategyVersion: "strategy-0002",
      }),
      catalogBody: {
        schema: "strategy_version_list.v1",
        count: 1,
        versions: [{ strategy_id: "strategy-0002", name: "B strategy" }],
      },
    });
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/v1/runs/run-a")) {
          void init;
          return pendingA.promise;
        }
        if (url.includes("/run-b/")) {
          if (url.includes("/chart?")) {
            const timeframe = new URL(`http://local${url}`).searchParams.get(
              "tf",
            ) as "D" | "1H" | "5m";
            return jsonResponse(
              liveChart("run-b", timeframe, "YM-202609-CBOT"),
            );
          }
        }
        return fallbackB(input);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await user.click(screen.getByTestId("nav-run-b"));
    await waitFor(() =>
      expect(screen.getByText(/B strategy · YM-202609-CBOT/)).toBeInTheDocument(),
    );
    await act(async () => {
      pendingA.resolve(jsonResponse(liveMain("run-a")));
      await Promise.resolve();
    });
    expect(screen.getByText(/B strategy · YM-202609-CBOT/)).toBeInTheDocument();
    expect(screen.queryByText(/Trend exact · NQ-202609-CME/)).toBeNull();
  });

  it("normal → owner aborts live generation and owner performs zero further business fetch", async () => {
    const pending = deferred<Response>();
    const captured = { normalSignal: null as AbortSignal | null };
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith("/api/v1/runs/run-a")) {
          captured.normalSignal = init?.signal ?? null;
          return pending.promise;
        }
        return new Response("unexpected", { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await user.click(screen.getByTestId("nav-owner"));
    await waitFor(() =>
      expect(screen.getByText(/Trend 回踩 18EMA · NQ/)).toBeInTheDocument(),
    );
    expect(captured.normalSignal?.aborted).toBe(true);
    const countAtOwner = fetchMock.mock.calls.length;
    await act(async () => {
      pending.resolve(jsonResponse(liveMain("run-a")));
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(countAtOwner);
    expect(screen.getByText(/Trend 回踩 18EMA · NQ/)).toBeInTheDocument();
  });

  it("owner → normal keeps owner fetch zero, then starts the new live generation", async () => {
    const fetchMock = detailFetch({
      runId: "run-b",
      mainBody: liveMain("run-b", 0, {
        contractId: "YM-202609-CBOT",
        strategyVersion: "strategy-0002",
      }),
      catalogBody: {
        schema: "strategy_version_list.v1",
        count: 1,
        versions: [{ strategy_id: "strategy-0002", name: "B strategy" }],
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/fixture-run-1?scenario=owner-review");
    await waitFor(() =>
      expect(screen.getByText(/Trend 回踩 18EMA · NQ/)).toBeInTheDocument(),
    );
    expect(fetchMock).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("nav-normal"));
    await waitFor(() =>
      expect(screen.getByText(/B strategy · YM-202609-CBOT/)).toBeInTheDocument(),
    );
    expect(fetchMock.mock.calls.some((call) =>
      String(call[0]).endsWith("/api/v1/runs/run-b"),
    )).toBe(true);
  });

  it("unmount aborts pending main and prevents read mark", async () => {
    const pending = deferred<Response>();
    const captured = { signal: null as AbortSignal | null };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        captured.signal = init?.signal ?? null;
        return pending.promise;
      }),
    );
    const rendered = renderResults("/results/run-a");
    rendered.unmount();
    expect(captured.signal?.aborted).toBe(true);
    await act(async () => {
      pending.resolve(jsonResponse(liveMain("run-a")));
      await Promise.resolve();
    });
    expect(localStorage.getItem("p5-result-read-v1")).toBeNull();
  });
});

describe("P5 Stage 2 normal export and decisions", () => {
  it("keeps legacy detail readable while export/history/POST all stay at zero", async () => {
    const legacy = liveMain("legacy-run") as Record<string, unknown>;
    delete legacy.decision_evidence_complete;
    delete (
      (legacy.run as { manifest: Record<string, unknown> }).manifest
    ).strategy_binding;
    const fetchMock = detailFetch({
      runId: "legacy-run",
      mainBody: legacy,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderResults("/results/legacy-run");
    await waitFor(() =>
      expect(
        screen.getByText(/Trend exact · NQ-202609-CME/),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/歷史結果缺完整不可變證據，未能安全匯出/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("decision-reason")).toBeDisabled();
    expect(screen.getByTestId("decision-use")).toBeDisabled();
    const businessCalls = fetchMock.mock.calls.filter(([input, init]) => {
      const url = String(input);
      return (
        url.endsWith("/export") ||
        url.endsWith("/promotion-decisions") ||
        init?.method === "POST"
      );
    });
    expect(businessCalls).toHaveLength(0);
  });

  it("gates whitespace/overlong reason and sends exact trimmed return request", async () => {
    const uuid = "11111111-1111-4111-8111-111111111111";
    vi.spyOn(crypto, "randomUUID").mockReturnValue(uuid);
    const fetchMock = detailFetch({ runId: "run-a" });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await screen.findByTestId("decision-history-empty");
    const reason = screen.getByTestId("decision-reason");

    fireEvent.change(reason, { target: { value: "   " } });
    expect(screen.getByTestId("decision-return")).toBeDisabled();
    fireEvent.change(reason, { target: { value: "x".repeat(4_001) } });
    expect(screen.getByTestId("decision-return")).toBeDisabled();
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(0);

    fireEvent.change(reason, { target: { value: "  Needs work  " } });
    await user.click(screen.getByTestId("decision-return"));
    await waitFor(() =>
      expect(screen.getByTestId("decision-history")).toHaveTextContent(
        "打回",
      ),
    );
    const posts = fetchMock.mock.calls.filter(
      ([, init]) => init?.method === "POST",
    );
    expect(posts).toHaveLength(1);
    const body = JSON.parse(String(posts[0][1]?.body));
    expect(body).toEqual({
      schema: "promotion_decision_request.v1",
      request_id: uuid,
      decision: "return",
      reason: "Needs work",
    });
    expect(Object.keys(body)).toEqual([
      "schema",
      "request_id",
      "decision",
      "reason",
    ]);
    expect(body).not.toHaveProperty("strategy");
    expect(screen.getByTestId("decision-history")).not.toHaveTextContent(
      /\breturn\b/,
    );
  });

  it("blocks pending double-click, appends strict use once and never opens P6", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(
      "22222222-2222-4222-8222-222222222222",
    );
    const pending = deferred<Response>();
    let request: LiveDecisionRequest | null = null;
    const fetchMock = detailFetch({
      runId: "run-a",
      decisionFetch: (captured) => {
        request = captured;
        return pending.promise;
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await screen.findByTestId("decision-history-empty");
    await user.type(screen.getByTestId("decision-reason"), "Ship it");
    await user.dblClick(screen.getByTestId("decision-use"));
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(1);
    expect(screen.getByTestId("decision-reason")).toBeDisabled();

    await act(async () => {
      pending.resolve(
        jsonResponse(liveDecisionRecord("run-a", request!)),
      );
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getByTestId("decision-history")).toHaveTextContent(
        "用得",
      ),
    );
    expect(screen.getByTestId("decision-history").children).toHaveLength(1);
    expect(screen.getByTestId("decision-note")).toHaveTextContent(
      "模擬盤尚未開放",
    );
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/paper"),
      ),
    ).toBe(false);
    expect(window.location.pathname).not.toBe("/paper");
  });

  it.each([404, 409, 422, 503, "malformed"] as const)(
    "keeps reason and appends zero records for %s decision response",
    async (failure) => {
      const fetchMock = detailFetch({
        runId: "run-a",
        decisionFetch: () =>
          failure === "malformed"
            ? new Response("{bad", { status: 200 })
            : new Response(`full failure ${failure}`, {
                status: failure,
              }),
      });
      vi.stubGlobal("fetch", fetchMock);
      const user = userEvent.setup();
      renderResults("/results/run-a");
      await screen.findByTestId("decision-history-empty");
      await user.type(screen.getByTestId("decision-reason"), "Keep reason");
      await user.click(screen.getByTestId("decision-abandon"));
      await screen.findByTestId("decision-error");
      expect(screen.getByTestId("decision-reason")).toHaveValue(
        "Keep reason",
      );
      expect(screen.queryByTestId("decision-history")).toBeNull();
      expect(screen.getByTestId("decision-error").textContent).toContain(
        failure === "malformed" ? "{bad" : `full failure ${failure}`,
      );
    },
  );

  it("treats network failure as unknown and retries the byte-exact request once", async () => {
    const randomUUID = vi
      .spyOn(crypto, "randomUUID")
      .mockReturnValue("33333333-3333-4333-8333-333333333333");
    let decisionCalls = 0;
    const fetchMock = detailFetch({
      runId: "run-a",
      decisionFetch: (request) => {
        decisionCalls += 1;
        if (decisionCalls === 1) {
          throw new Error("connection lost");
        }
        return jsonResponse(
          liveDecisionRecord("run-a", request, "decision-replayed"),
        );
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await screen.findByTestId("decision-history-empty");
    await user.type(screen.getByTestId("decision-reason"), "Stable intent");
    await user.click(screen.getByTestId("decision-use"));
    await screen.findByTestId("retry-unknown-decision");
    expect(screen.getByTestId("decision-note")).toHaveTextContent(
      "結果未知",
    );
    expect(screen.getByTestId("decision-reason")).toBeDisabled();
    await user.click(screen.getByTestId("retry-unknown-decision"));
    await waitFor(() =>
      expect(screen.getByTestId("decision-history").children).toHaveLength(
        1,
      ),
    );
    const posts = fetchMock.mock.calls.filter(
      ([, init]) => init?.method === "POST",
    );
    expect(posts).toHaveLength(2);
    expect(posts[0][1]?.body).toBe(posts[1][1]?.body);
    expect(randomUUID).toHaveBeenCalledTimes(1);
  });

  it("isolates history error and binds retry to the same live run", async () => {
    let attempts = 0;
    const fetchMock = detailFetch({
      runId: "run-a",
      historyFetch: () => {
        attempts += 1;
        return attempts === 1
          ? new Response("full history down", { status: 503 })
          : jsonResponse(liveDecisionHistory("run-a"));
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await screen.findByTestId("retry-decision-history");
    expect(screen.getByText(/Signal rejected/)).toBeInTheDocument();
    expect(screen.getByTestId("decision-use")).toBeDisabled();
    expect(screen.getByText(/full history down/)).toBeInTheDocument();
    await user.click(screen.getByTestId("retry-decision-history"));
    await screen.findByTestId("decision-history-empty");
    expect(attempts).toBe(2);
  });

  it("downloads only a fully verified byte-exact ZIP and revokes it on route change", async () => {
    const bytes = await liveExportBytes("run-a");
    const urls = stubBlobUrls();
    const fetchMock = detailFetch({
      runId: "run-a",
      exportFetch: () => liveExportResponse("run-a", bytes),
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await screen.findByTestId("decision-history-empty");
    await user.dblClick(screen.getByTestId("open-export"));
    const download = await screen.findByTestId("download-zip");
    expect(download).toHaveAttribute("download", "result-run-a.zip");
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/run-a/export"),
      ),
    ).toHaveLength(1);
    expect(urls.createObjectURL).toHaveBeenCalledTimes(1);
    const blob = urls.createObjectURL.mock.calls[0][0] as Blob;
    expect(Array.from(await readBlobBytes(blob))).toEqual(
      Array.from(bytes),
    );
    expect(screen.getByText(/trades\/run-a\.json/)).toBeInTheDocument();
    expect(screen.getByText(/equity\/run-a\.json/)).toBeInTheDocument();
    expect(screen.getByText(/events\/run-a\.json/)).toBeInTheDocument();

    await user.click(screen.getByTestId("nav-run-b"));
    await waitFor(() =>
      expect(urls.revokeObjectURL).toHaveBeenCalledWith(
        "blob:p5-stage-2",
      ),
    );
  });

  it("creates no Blob URL for malformed success and allows an exact retry", async () => {
    const bytes = await liveExportBytes("run-a");
    const urls = stubBlobUrls();
    let attempts = 0;
    const fetchMock = detailFetch({
      runId: "run-a",
      exportFetch: () => {
        attempts += 1;
        return attempts === 1
          ? new Response(bytes, {
              status: 200,
              headers: {
                "Content-Type": "application/json",
                "Content-Disposition":
                  'attachment; filename="result-run-a.zip"',
              },
            })
          : liveExportResponse("run-a", bytes);
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await screen.findByTestId("decision-history-empty");
    await user.click(screen.getByTestId("open-export"));
    await screen.findByTestId("retry-export");
    expect(urls.createObjectURL).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("retry-export"));
    await screen.findByTestId("download-zip");
    expect(attempts).toBe(2);
    expect(urls.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("drops a late POST after switching to owner-review", async () => {
    const pending = deferred<Response>();
    let request: LiveDecisionRequest | null = null;
    const fetchMock = detailFetch({
      runId: "run-a",
      decisionFetch: (captured) => {
        request = captured;
        return pending.promise;
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/run-a");
    await screen.findByTestId("decision-history-empty");
    await user.type(screen.getByTestId("decision-reason"), "Late");
    await user.click(screen.getByTestId("decision-use"));
    await user.click(screen.getByTestId("nav-owner"));
    await waitFor(() =>
      expect(
        screen.getByText(/Trend 回踩 18EMA · NQ/),
      ).toBeInTheDocument(),
    );
    await act(async () => {
      pending.resolve(
        jsonResponse(liveDecisionRecord("run-a", request!)),
      );
      await Promise.resolve();
    });
    expect(screen.queryByText(/decision-/)).toBeNull();
    expect(screen.queryByText(/模擬盤尚未開放/)).toBeNull();
  });
});

describe("P5 owner-review", () => {
  it("keeps local export/decision isolated from all three normal endpoints", async () => {
    stubBlobUrls();
    const fetchMock = vi.fn(async () => new Response("no", { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results/fixture-run-1?scenario=owner-review");
    await waitFor(() =>
      expect(screen.getByTestId("open-export")).toBeInTheDocument(),
    );
    await user.click(screen.getByTestId("open-export"));
    await screen.findByTestId("download-zip");
    await user.type(screen.getByTestId("decision-reason"), "Owner local");
    await user.click(screen.getByTestId("decision-abandon"));
    expect(screen.getByTestId("decision-history")).toHaveTextContent(
      "放棄",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lists fixture runs and opens zero-trade detail with 3 near-misses + chart grid", async () => {
    const fetchMock = vi.fn(async () => new Response("no", { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderResults("/results?scenario=owner-review");
    expect(screen.getByText(/Owner 驗收入口/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("result-row-fixture-run-1")).toBeInTheDocument();
    });
    expect(listFixtureResults().length).toBeGreaterThanOrEqual(3);
    await user.click(screen.getByTestId("result-row-fixture-run-1"));
    await waitFor(() => {
      expect(screen.getByTestId("near-miss-list")).toBeInTheDocument();
    });
    expect(screen.getByTestId("near-miss-1")).toBeInTheDocument();
    expect(screen.getByTestId("near-miss-2")).toBeInTheDocument();
    expect(screen.getByTestId("near-miss-3")).toBeInTheDocument();
    expect(screen.getByTestId("chart-grid")).toBeInTheDocument();
    expect(screen.getByTestId("chart-pane-D")).toBeInTheDocument();
    expect(screen.getByTestId("chart-pane-30m")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /5m|1H/ })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("trade detail expands causal four answers and decision reason gate", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 404 })));
    const user = userEvent.setup();
    renderResults("/results/fixture-run-trades?scenario=owner-review");
    await waitFor(() => {
      expect(screen.getByTestId("trade-1")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("trade-1"));
    expect(screen.getByTestId("trade-detail-1").textContent).toMatch(/為何入市/);
    expect(screen.getByTestId("trade-detail-1").textContent).toMatch(/止損/);
    expect(screen.getByTestId("trade-detail-1").textContent).toMatch(/離場/);
    expect(screen.getByTestId("trade-detail-1").textContent).toMatch(/保守假設/);

    expect(screen.getByTestId("decision-use")).toBeDisabled();
    await user.type(screen.getByTestId("decision-reason"), "   ");
    expect(screen.getByTestId("decision-use")).toBeDisabled();
    await user.clear(screen.getByTestId("decision-reason"));
    await user.type(screen.getByTestId("decision-reason"), "值得上模擬");
    expect(screen.getByTestId("decision-use")).not.toBeDisabled();
    await user.click(screen.getByTestId("decision-use"));
    expect(screen.getByTestId("decision-history").textContent).toMatch(/用得/);
    await user.click(screen.getByTestId("decision-return"));
    const hist = screen.getByTestId("decision-history").textContent ?? "";
    expect(hist).toMatch(/用得/);
    expect(hist).toMatch(/打回/);
  });

  it("export zip has result.v1 and matching refs", async () => {
    const payload = buildResultZipPayload("fixture-run-1");
    expect(payload.zipName).toBe("result-fixture-run-1.zip");
    const result = JSON.parse(payload.files["result.json"]) as {
      schema: string;
      trades_ref: string;
      equity_curve_ref: string;
      events_ref: string;
      run: { manifest: { costs: unknown; capital: number } };
    };
    expect(result.schema).toBe("result.v1");
    expect(result.run.manifest.capital).toBe(100_000);
    expect(result.run.manifest.costs).toBeTruthy();
    expect(payload.files[result.trades_ref]).toBeTruthy();
    expect(payload.files[result.equity_curve_ref]).toBeTruthy();
    expect(payload.files[result.events_ref]).toBeTruthy();
    const equity = JSON.parse(payload.files[result.equity_curve_ref]) as {
      schema: string;
    };
    expect(equity.schema).toBe("equity_curve.v1");
    const events = JSON.parse(payload.files[result.events_ref]) as {
      events: Array<{ kind?: string }>;
    };
    expect(events.events.filter((e) => e.kind === "near_miss")).toHaveLength(
      3,
    );
    expect(payload.opener).toContain(payload.zipName);

    const zip = new JSZip();
    for (const [p, body] of Object.entries(payload.files)) {
      zip.file(p, body);
    }
    const blob = await zip.generateAsync({ type: "uint8array" });
    const reopened = await JSZip.loadAsync(blob);
    const fileKeys = Object.keys(reopened.files).filter(
      (k) => !reopened.files[k].dir,
    );
    expect(fileKeys.sort()).toEqual(Object.keys(payload.files).sort());
  });

  it("strategy deep-link path is exact id", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 404 })));
    renderResults("/results/fixture-run-1?scenario=owner-review");
    await waitFor(() => {
      expect(screen.getByRole("link", { name: "用咗策略版本" })).toBeInTheDocument();
    });
    const href = screen
      .getByRole("link", { name: "用咗策略版本" })
      .getAttribute("href");
    expect(href).toBe("/strategies?tab=library&strategy=strategy-0001");
  });

  it("D20/D30: filter query preserved on detail link and back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 404 })));
    const user = userEvent.setup();
    renderResults(
      "/results?scenario=owner-review&strategy=strategy-0002",
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("result-row-fixture-run-trades"),
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId("result-row-fixture-run-1")).not.toBeInTheDocument();
    const href = screen
      .getByTestId("result-row-fixture-run-trades")
      .getAttribute("href");
    expect(href).toMatch(/strategy=strategy-0002/);
    expect(href).toMatch(/scenario=owner-review/);
    await user.click(screen.getByTestId("result-row-fixture-run-trades"));
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /返回所有結果/ })).toBeInTheDocument();
    });
    const back = screen
      .getByRole("link", { name: /返回所有結果/ })
      .getAttribute("href");
    expect(back).toMatch(/strategy=strategy-0002/);
    expect(back).toMatch(/scenario=owner-review/);
  });

  it("D30: unread control visible and toggles URL", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 404 })));
    const user = userEvent.setup();
    renderResults("/results?scenario=owner-review");
    await waitFor(() => {
      expect(screen.getByTestId("filter-unread")).toBeInTheDocument();
    });
    const btn = screen.getByTestId("filter-unread");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    await user.click(btn);
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    // MemoryRouter updates location via setSearchParams — check pressed + list filter
    expect(screen.getByTestId("results-count").textContent).toMatch(/未睇/);
  });

  it("D21: decisions survive list remount", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 404 })));
    const d = listFixtureResults().find((r) => r.runId === "fixture-run-trades")!;
    appendFixtureDecision({
      type: "use",
      runId: d.runId,
      strategyVersion: d.strategyVersion,
      reason: "keep-me",
      scorecardSnapshot: [],
    });
    // remount list (would previously wipe decisions)
    renderResults("/results?scenario=owner-review");
    await waitFor(() => {
      expect(screen.getByTestId("results-list")).toBeInTheDocument();
    });
    expect(listFixtureDecisions("fixture-run-trades")).toHaveLength(1);
    expect(listFixtureDecisions("fixture-run-trades")[0].reason).toBe(
      "keep-me",
    );
  });

  it("D21: decision history uses human labels not raw use", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 404 })));
    const user = userEvent.setup();
    renderResults("/results/fixture-run-trades?scenario=owner-review");
    await waitFor(() => {
      expect(screen.getByTestId("decision-reason")).toBeInTheDocument();
    });
    await user.type(screen.getByTestId("decision-reason"), "理由甲");
    await user.click(screen.getByTestId("decision-use"));
    expect(screen.getByTestId("decision-history").textContent).toMatch(/用得/);
    expect(screen.getByTestId("decision-history").textContent).not.toMatch(
      /\buse\b/,
    );
  });
});

describe("P5 chart grid contract (smoke)", () => {
  it("desktop four panes exist without TF tabs in owner-review detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 404 })));
    renderResults("/results/fixture-run-trades?scenario=owner-review");
    await waitFor(() => {
      expect(screen.getByTestId("chart-grid")).toBeInTheDocument();
    });
    for (const tf of ["D", "1H", "30m", "5m"]) {
      expect(screen.getByTestId(`chart-pane-${tf}`)).toBeInTheDocument();
    }
    expect(document.body.textContent).not.toMatch(/TF tab|timeframe tabs/i);
  });
});
