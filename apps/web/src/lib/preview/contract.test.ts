import { describe, expect, it } from "vitest";

import { localDatetimeToUtcIso } from "../backtest/time";
import type { InstrumentCatalogRow } from "../catalog/types";
import type { StrategyUniverse } from "../strategy/universe";
import {
  buildPreviewRequest,
  eligiblePreviewContracts,
  interpretPreviewFunnel,
  parsePreviewEnvelope,
  previewToChartPanes,
  primaryPreviewContractId,
  type PreviewFunnel,
} from "./contract";
import { createOwnerReviewPreviewFixture } from "./ownerReviewFixture";

function cloneFixture(): Record<string, unknown> {
  return JSON.parse(
    JSON.stringify(createOwnerReviewPreviewFixture()),
  ) as Record<string, unknown>;
}

function parseFixture(body: unknown = cloneFixture()) {
  return parsePreviewEnvelope(200, body, {
    contractId: "NQ-202609-CME",
    sessionName: "eth",
  });
}

function failureEnvelope(valid: boolean): Record<string, unknown> {
  return {
    schema: "backtest_preview.v1",
    run_scope: "dry_run",
    request_fingerprint: {
      algorithm: "sha256",
      digest: "a".repeat(64),
    },
    validation: {
      schema: "strategy_validation.v1",
      valid,
      issue_count: valid ? 0 : 1,
      issues: valid
        ? []
        : [
            {
              path: "range_start",
              message: "invalid",
              fix: "pick a valid range",
              layer: "format",
              line: "range_start: invalid — pick a valid range",
            },
          ],
      report_text: valid
        ? ""
        : "range_start: invalid — pick a valid range",
    },
    funnel: null,
    decision_evidence: [],
    rejection_evidence: [],
    evidence_summary: {
      availability: "unavailable",
      complete: false,
      evaluation_count: 0,
      rejection_count: 0,
      layer_reached_counts: {},
      blocking_condition_counts: {},
      deepest_layer: null,
      trade_count: 0,
    },
    charts: {},
    persisted: false,
    warnings: [],
    errors: [valid ? "ValueError: no bars" : "request validation failed"],
  };
}

const BASE_FUNNEL: PreviewFunnel = {
  schema: "funnel.v1",
  status: "ok",
  daily_trend_days: 3,
  evaluations_passing_daily_gate: 8,
  evaluations_passing_mid_gate: 4,
  signals_created: 2,
  fills: 1,
  reject_reasons: {},
  notes: "",
  units: {
    daily_trend_days: "day",
    evaluations_passing_daily_gate: "evaluation",
    evaluations_passing_mid_gate: "evaluation",
    signals_created: "evaluation",
    fills: "trade",
  },
};

describe("P2 preview request contract", () => {
  it("keeps source text exact and emits canonical UTC with explicit owner assumptions", () => {
    const sourceText = "schema: strategy.v1\nmeta:\n  name: exact\n\n";
    const result = buildPreviewRequest({
      sourceText,
      filename: null,
      contractId: "ES-202609-CME",
      rangeStartLocal: "2026-07-01T09:30:00",
      rangeEndLocal: "2026-07-22T16:00:00",
      commissionText: "2.5",
      slippageText: "1",
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.request).toEqual({
      schema: "backtest_preview_request.v1",
      source_text: sourceText,
      filename: null,
      contract_id: "ES-202609-CME",
      range_start: localDatetimeToUtcIso("2026-07-01T09:30:00"),
      range_end: localDatetimeToUtcIso("2026-07-22T16:00:00"),
      assumptions: {
        initial_capital_usd: 100_000,
        commission_per_side: 2.5,
        slippage_ticks: 1,
      },
    });
  });

  it.each([
    ["blank range", { rangeStartLocal: "" }],
    ["reverse range", { rangeEndLocal: "2026-06-01T09:30:00" }],
    ["blank commission", { commissionText: "" }],
    ["negative commission", { commissionText: "-0.1" }],
    ["non-finite commission", { commissionText: "Infinity" }],
    ["blank slippage", { slippageText: "" }],
    ["fractional slippage", { slippageText: "1.5" }],
    ["negative slippage", { slippageText: "-1" }],
  ])("rejects %s before any request", (_label, patch) => {
    const result = buildPreviewRequest({
      sourceText: "schema: strategy.v1\n",
      filename: "exact.yaml",
      contractId: "NQ-202609-CME",
      rangeStartLocal: "2026-07-01T09:30:00",
      rangeEndLocal: "2026-07-22T16:00:00",
      commissionText: "2.5",
      slippageText: "1",
      ...patch,
    });
    expect(result.ok).toBe(false);
  });

  it("derives eligible exact contracts only from shared catalog plus universe", () => {
    const universe: StrategyUniverse = {
      primaryInstrument: "ES",
      assetClass: "equity_index_futures",
      contracts: ["ES", "YM"],
      expansionRationale: { YM: "same class" },
      session: "eth",
    };
    const rows: InstrumentCatalogRow[] = [
      {
        symbol: "ES",
        contractId: "ES-202609-CME",
        displayName: "E-mini S&P 500",
        assetClass: "equity_index_futures",
        currency: "USD",
        sessionsAvailable: ["eth"],
      },
      {
        symbol: "YM",
        contractId: "YM-202609-CBOT",
        displayName: "E-mini Dow",
        assetClass: "equity_index_futures",
        currency: "USD",
        sessionsAvailable: ["eth"],
      },
      {
        symbol: "NQ",
        contractId: "NQ-202609-CME",
        displayName: "Nasdaq",
        assetClass: "equity_index_futures",
        currency: "USD",
        sessionsAvailable: ["eth"],
      },
    ];
    const eligible = eligiblePreviewContracts(universe, rows);
    expect(eligible.map((row) => row.contractId)).toEqual([
      "ES-202609-CME",
      "YM-202609-CBOT",
    ]);
    expect(primaryPreviewContractId(universe, eligible)).toBe(
      "ES-202609-CME",
    );
    expect(
      primaryPreviewContractId(universe, [
        ...eligible,
        { ...eligible[0], contractId: "ES-202612-CME" },
      ]),
    ).toBe("");
  });
});

describe("P2 preview runtime response contract", () => {
  it("accepts complete dry-run truth and adapts exact four panes with real reject timestamps", () => {
    const parsed = parseFixture();
    expect(parsed.ok).toBe(true);
    if (!parsed.ok || parsed.value.kind !== "success") return;
    const panes = previewToChartPanes(parsed.value);
    expect(panes.map((pane) => pane.timeframe)).toEqual([
      "D",
      "1H",
      "30m",
      "5m",
    ]);
    expect(panes.map((pane) => pane.roleLabel)).toEqual([
      "大框架",
      "中框架",
      "輔助",
      "入市",
    ]);
    const oneHour = panes.find((pane) => pane.timeframe === "1H");
    expect(oneHour?.verticalAnnotations).toHaveLength(8);
    expect(oneHour?.verticalAnnotations?.[0]?.time).toBe(
      Math.floor(Date.parse("2026-07-08T14:00:00Z") / 1000),
    );
    expect(
      oneHour?.verticalAnnotations?.some((item) =>
        item.label.includes("owner_unknown_block"),
      ),
    ).toBe(true);
    expect(panes.every((pane) => pane.trendDayTimes === undefined)).toBe(true);
  });

  it.each([
    [
      "persisted true",
      (body: Record<string, unknown>) => {
        body.persisted = true;
      },
    ],
    [
      "deep run_id",
      (body: Record<string, unknown>) => {
        const validation = body.validation as Record<string, unknown>;
        validation.debug = { run_id: "fake-preview-run" };
      },
    ],
    [
      "missing 30m",
      (body: Record<string, unknown>) => {
        const charts = body.charts as Record<string, unknown>;
        delete charts["30m"];
      },
    ],
    [
      "chart sidecar",
      (body: Record<string, unknown>) => {
        const charts = body.charts as Record<
          string,
          Record<string, unknown>
        >;
        charts.D.sidecar_relpath = "chart/fake.json";
      },
    ],
    [
      "non-finite OHLC",
      (body: Record<string, unknown>) => {
        const charts = body.charts as Record<
          string,
          Record<string, unknown>
        >;
        const candles = charts.D.candles as Array<Record<string, unknown>>;
        candles[0].open = Number.NaN;
      },
    ],
    [
      "contract drift",
      (body: Record<string, unknown>) => {
        const charts = body.charts as Record<
          string,
          Record<string, unknown>
        >;
        charts.D.contract_id = "YM-202609-CBOT";
      },
    ],
  ])("fails closed on %s", (_label, mutate) => {
    const body = cloneFixture();
    mutate(body);
    expect(parseFixture(body).ok).toBe(false);
  });

  it("preserves complete request-validation and engine-failure 422 envelopes", () => {
    for (const valid of [false, true]) {
      const parsed = parsePreviewEnvelope(422, failureEnvelope(valid), {
        contractId: "NQ-202609-CME",
        sessionName: "eth",
      });
      expect(parsed.ok).toBe(true);
      if (parsed.ok) {
        expect(parsed.value.kind).toBe("failure");
        expect(parsed.value.validation.valid).toBe(valid);
        expect(parsed.value.errors).toHaveLength(1);
      }
    }
  });

  it("rejects malformed/non-contract HTTP results instead of retaining old truth", () => {
    expect(
      parsePreviewEnvelope(500, { detail: "down" }, {
        contractId: "NQ-202609-CME",
        sessionName: "eth",
      }).ok,
    ).toBe(false);
    expect(
      parsePreviewEnvelope(200, null, {
        contractId: "NQ-202609-CME",
        sessionName: "eth",
      }).ok,
    ).toBe(false);
  });
});

describe("P2 deterministic zero-boundary interpretation", () => {
  it.each([
    [
      {
        daily_trend_days: 0,
        evaluations_passing_daily_gate: 0,
        evaluations_passing_mid_gate: 0,
        signals_created: 0,
        fills: 0,
      },
      /Daily 市況閘係最早/,
    ],
    [
      { evaluations_passing_mid_gate: 0, signals_created: 0, fills: 0 },
      /中層閘/,
    ],
    [{ signals_created: 0, fills: 0 }, /入市訊號層/],
    [{ fills: 0 }, /成交／執行層/],
    [{ fills: 1 }, /1 筆成交/],
  ])("uses only deterministic facts %#", (patch, expected) => {
    expect(
      interpretPreviewFunnel({ ...BASE_FUNNEL, ...patch }),
    ).toMatch(expected);
  });

  it("states it cannot interpret contradictory cross-layer facts", () => {
    expect(
      interpretPreviewFunnel({
        ...BASE_FUNNEL,
        evaluations_passing_mid_gate: 2,
        signals_created: 3,
      }),
    ).toMatch(/暫時解讀唔到/);
  });
});
