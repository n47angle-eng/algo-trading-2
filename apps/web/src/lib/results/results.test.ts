import { describe, expect, it } from "vitest";
import JSZip from "jszip";

import {
  __hardResetResultsFixture,
  appendFixtureDecision,
  buildResultZipPayload,
  chartsWithActiveTrade,
  decisionTypeLabel,
  getFixtureDetail,
  levelsForTrade,
  listFixtureDecisions,
  listFixtureResults,
  resetResultsFixture,
} from "./fixtureStore";
import { formatOptionalNumber, parseOptionalNumber } from "./metrics";
import { formatHktLocal, hktLocalToUnixSec } from "./timeIdentity";

describe("P5 fixture isolation", () => {
  it("has zero-trade and trades fixtures with chart annotations", () => {
    __hardResetResultsFixture();
    const list = listFixtureResults();
    expect(list.some((r) => r.runId === "fixture-run-1")).toBe(true);
    expect(list.some((r) => r.runId === "fixture-run-trades")).toBe(true);
    const zero = getFixtureDetail("fixture-run-1")!;
    expect(zero.tradeCount).toBe(0);
    expect(zero.nearMisses).toHaveLength(3);
    expect(zero.charts[0].rejectMarkers?.length).toBe(3);
    // near-miss HKT round-trip
    expect(zero.nearMisses[0].timeLocal).toBe(
      formatHktLocal(hktLocalToUnixSec(2026, 5, 7, 10, 15)),
    );
    const trades = getFixtureDetail("fixture-run-trades")!;
    expect(trades.trades).toHaveLength(3);
    expect(trades.charts).toHaveLength(4);
    // D28: entry local matches focusTimeUtc
    expect(trades.trades[0].entryTimeLocal).toBe("2026-05-08 10:30");
    expect(trades.trades[0].focusTimeUtc).toBe("2026-05-08T02:30:00.000Z");
    // D27: higher TF verticals have #N + local datetime; not only「入市日」
    expect(trades.charts[0].verticalLines?.length).toBe(3);
    expect(trades.charts[0].verticalLines![0].label).toMatch(
      /#1 · 2026-05-08 10:30/,
    );
    expect(trades.charts[0].verticalLines![1].label).toMatch(/#2 ·/);
    // D29: D/1H/30m do not spam 5m entry markers
    expect(trades.charts[0].markers ?? []).toHaveLength(0);
    // default 5m levels are #1
    expect(trades.charts[3].levels?.some((l) => l.label === "#1 止損")).toBe(
      true,
    );
    expect(
      trades.charts[3].markers!.some(
        (m) => m.text.includes("買") || m.text.includes("賣"),
      ),
    ).toBe(true);
  });

  it("D29 per-trade levels: #2/#3 differ from #1", () => {
    const trades = getFixtureDetail("fixture-run-trades")!;
    const l1 = levelsForTrade(trades.trades[0])!;
    const l2 = levelsForTrade(trades.trades[1])!;
    const l3 = levelsForTrade(trades.trades[2])!;
    expect(l2.find((x) => x.label?.includes("止損"))!.price).not.toBe(
      l1.find((x) => x.label?.includes("止損"))!.price,
    );
    expect(l3.find((x) => x.label?.includes("止損"))!.price).not.toBe(
      l1.find((x) => x.label?.includes("止損"))!.price,
    );
    const active2 = chartsWithActiveTrade(trades.charts, trades.trades, 2);
    const m5 = active2.find((p) => p.timeframe === "5m")!;
    expect(m5.levels?.some((l) => l.label === "#2 止損")).toBe(true);
    expect(m5.levels?.some((l) => l.label === "#1 止損")).toBe(false);
    expect(m5.markers?.every((m) => m.text.includes("#2"))).toBe(true);
    // mutation probe: trades[0]-only would make #2 stop equal #1
    const wrong = levelsForTrade(trades.trades[0]);
    expect(wrong![0].price).not.toBe(l2[0].price);
  });

  it("decisions survive soft reset and append-only with deep snapshot", () => {
    __hardResetResultsFixture();
    const d = getFixtureDetail("fixture-run-trades")!;
    const snap = d.scorecard.map((s) => ({ ...s }));
    appendFixtureDecision({
      type: "use",
      runId: d.runId,
      strategyVersion: d.strategyVersion,
      reason: "first",
      scorecardSnapshot: snap,
    });
    // soft reset must NOT clear
    resetResultsFixture();
    expect(listFixtureDecisions(d.runId)).toHaveLength(1);
    appendFixtureDecision({
      type: "abandon",
      runId: d.runId,
      strategyVersion: d.strategyVersion,
      reason: "second",
      scorecardSnapshot: snap,
    });
    const all = listFixtureDecisions(d.runId);
    expect(all).toHaveLength(2);
    expect(all[0].reason).toBe("first");
    expect(decisionTypeLabel("use")).toBe("用得");
    // mutate original scorecard row
    snap[0].detail = "MUTATED";
    expect(all[0].scorecardSnapshot[0].detail).not.toBe("MUTATED");
  });

  it("zip round-trip: equity_curve.v1 + complete manifest + refs", async () => {
    const payload = buildResultZipPayload("fixture-run-1");
    expect(payload.zipName).toBe("result-fixture-run-1.zip");
    const result = JSON.parse(payload.files["result.json"]) as {
      schema: string;
      run: {
        run_id: string;
        strategy_version: string;
        manifest: {
          contract: string;
          capital: number;
          costs: unknown;
          fill_model: string;
          data_fingerprint: string;
        };
        engine: unknown;
      };
      trades_ref: string;
      equity_curve_ref: string;
      events_ref: string;
    };
    expect(result.schema).toBe("result.v1");
    expect(result.run.manifest.capital).toBe(100_000);
    expect(result.run.manifest.costs).toBeTruthy();
    expect(result.run.manifest.fill_model).toBeTruthy();
    expect(result.run.manifest.data_fingerprint).toBeTruthy();
    expect(result.run.engine).toBeTruthy();
    const equity = JSON.parse(payload.files[result.equity_curve_ref]) as {
      schema: string;
    };
    expect(equity.schema).toBe("equity_curve.v1");
    const events = JSON.parse(payload.files[result.events_ref]) as {
      events: unknown[];
    };
    expect(
      events.events.filter(
        (e) => (e as { kind?: string }).kind === "near_miss",
      ),
    ).toHaveLength(3);

    const zip = new JSZip();
    for (const [path, body] of Object.entries(payload.files)) {
      zip.file(path, body);
    }
    const bytes = await zip.generateAsync({ type: "uint8array" });
    const again = await JSZip.loadAsync(bytes);
    const resultJson = await again.file("result.json")!.async("string");
    const againResult = JSON.parse(resultJson) as typeof result;
    expect(again.file(againResult.trades_ref)).toBeTruthy();
    expect(again.file(againResult.events_ref)).toBeTruthy();
    expect(again.file(againResult.equity_curve_ref)).toBeTruthy();
    expect(payload.opener).toContain(payload.zipName);
  });
});

describe("D22 missing ≠ 0", () => {
  it("parseOptionalNumber distinguishes null and 0", () => {
    expect(parseOptionalNumber(0)).toBe(0);
    expect(parseOptionalNumber(null)).toBeNull();
    expect(parseOptionalNumber(undefined)).toBeNull();
    expect(parseOptionalNumber(3)).toBe(3);
    expect(formatOptionalNumber(null)).toBe("未提供");
    expect(formatOptionalNumber(0)).toBe("0.00");
  });

  it("mutation: coalescing missing to 0 would hide 未提供", () => {
    const missing: number | null = null;
    const wrong = missing ?? 0;
    expect(wrong).toBe(0);
    expect(parseOptionalNumber(missing)).toBeNull();
    expect(formatOptionalNumber(parseOptionalNumber(missing))).toBe("未提供");
  });
});
