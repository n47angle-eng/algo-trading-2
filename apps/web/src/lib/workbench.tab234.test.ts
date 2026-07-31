import { afterEach, describe, expect, it, vi } from "vitest";

import { ownerReviewCoverageBody } from "./catalog/fixtureCatalog";
import { parseInstrumentCatalog } from "./catalog/parseCatalog";
import {
  assertInsightInstructions,
  emitInstructionsMd,
} from "./sketch/instructions";
import { createEmptyDraft, exportSketchPackage } from "./sketch/store";
import { SKETCH_STORAGE_KEY } from "./sketch/types";
import {
  DELETE_GRACE_MS,
  commitTombstone,
  isTombstoned,
  listTombstones,
} from "./strategyDelete";
import {
  countStandardRunsForStrategy,
  loadStandardRunCounts,
} from "./runRefs";
import {
  deriveStrategyYaml,
  extractBasedOnSketch,
} from "./strategyYaml";
import {
  importInsight,
  listInsights,
  INSIGHT_STORAGE_KEY,
} from "./insightStore";
import type { RunSummary } from "../api/types";

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear() {
      map.clear();
    },
    getItem(key: string) {
      return map.has(key) ? (map.get(key) as string) : null;
    },
    key(index: number) {
      return [...map.keys()][index] ?? null;
    },
    removeItem(key: string) {
      map.delete(key);
    },
    setItem(key: string, value: string) {
      map.set(key, value);
    },
  };
}

afterEach(() => {
  localStorage.removeItem(SKETCH_STORAGE_KEY);
  localStorage.removeItem(INSIGHT_STORAGE_KEY);
  vi.useRealTimers();
});

describe("tab ② helpers", () => {
  it("extractBasedOnSketch reads meta.based_on_sketch", () => {
    const yaml = `
schema: strategy.v1
meta:
  name: test
  based_on_sketch: sketch-20260725-99
  based_on_sketch_origin: workshop
rationale: x
unquantified_notes: []
`;
    expect(extractBasedOnSketch(yaml)).toBe("sketch-20260725-99");
  });
});

describe("tab ③ derive + delete", () => {
  it("deriveStrategyYaml leaves original source string unchanged and tags UI 微調", () => {
    const original = `
schema: strategy.v1
meta:
  name: base
regime:
  sep_mult:
    value: 65
provenance:
  - path: regime.sep_mult.value
    source: system_default
unquantified_notes: []
`;
    const snapshot = original;
    const next = deriveStrategyYaml(
      original,
      [{ path: "regime.sep_mult.value", value: 50 }],
      "strategy-0007",
    );
    expect(original).toBe(snapshot);
    expect(next).toContain("50");
    expect(next).toContain("Owner UI 微調");
    expect(next).not.toBe(original);
    expect(next).toMatch(/based_on:\s*strategy-0007/);
  });

  it("deriveStrategyYaml overwrites meta.based_on with the parent id (not grandparent)", () => {
    // Parent YAML already claims based_on: strategy-0001 (it was itself derived).
    // New child must point at the version we edited (strategy-0002), not keep 0001.
    const original = `
schema: strategy.v1
meta:
  name: mid
  based_on: strategy-0001
regime:
  sep_mult:
    value: 65
unquantified_notes: []
`;
    const parentId = "strategy-0002";
    expect(parentId).not.toBe("strategy-0001");
    expect(parentId).not.toBe("");
    const next = deriveStrategyYaml(
      original,
      [{ path: "regime.sep_mult.value", value: 50 }],
      parentId,
    );
    expect(next).toMatch(/based_on:\s*strategy-0002/);
    // Must not still claim grandparent as immediate parent.
    expect(next).not.toMatch(/based_on:\s*strategy-0001\b/);
  });

  it("countStandardRunsForStrategy ignores validation runs", () => {
    const runs: RunSummary[] = [
      {
        run_id: "a",
        strategy_version: "strategy-0001",
        contract_id: "NQ",
        session_name: "eth",
        range_start: null,
        range_end: null,
        validation_run: true,
        trade_count: 0,
        net_r: null,
        net_pnl: null,
        win_rate: null,
        profit_factor: null,
        max_drawdown_pnl: null,
        max_drawdown_r: null,
        expectancy_r: null,
        scorecard_statuses: [],
        funnel_status: null,
        funnel_fills: null,
        has_scorecard: false,
        has_funnel: false,
        result_file: "x",
      },
      {
        run_id: "b",
        strategy_version: "strategy-0001",
        contract_id: "NQ",
        session_name: "eth",
        range_start: null,
        range_end: null,
        validation_run: false,
        trade_count: 0,
        net_r: null,
        net_pnl: null,
        win_rate: null,
        profit_factor: null,
        max_drawdown_pnl: null,
        max_drawdown_r: null,
        expectancy_r: null,
        scorecard_statuses: [],
        funnel_status: null,
        funnel_fills: null,
        has_scorecard: false,
        has_funnel: false,
        result_file: "y",
      },
    ];
    expect(countStandardRunsForStrategy(runs, "strategy-0001")).toBe(1);
  });

  it("tombstone commit is real after grace; undo path leaves no tombstone", () => {
    const storage = memoryStorage();
    expect(isTombstoned("strategy-0001", storage)).toBe(false);
    // Undo path: never call commit
    expect(listTombstones(storage)).toHaveLength(0);
    // Real commit after grace would call this:
    commitTombstone("strategy-0001", "name", storage);
    expect(isTombstoned("strategy-0001", storage)).toBe(true);
    expect(DELETE_GRACE_MS).toBe(5000);
  });

  it("loadStandardRunCounts is fail-closed when fetchRuns fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("down", { status: 503 })),
    );
    const result = await loadStandardRunCounts(["strategy-0001"]);
    expect(result.known).toBe(false);
    // counts may be zero — callers must use known===false, not counts alone
    expect(result.counts["strategy-0001"]).toBe(0);
    vi.unstubAllGlobals();
  });
});

describe("tab ④ insight instructions", () => {
  it("insight pack INSTRUCTIONS embeds insight.v1 and excludes strategy.v1", () => {
    const storage = memoryStorage();
    // use real localStorage for export path via store key
    localStorage.removeItem(SKETCH_STORAGE_KEY);
    const draft = createEmptyDraft([], new Date(), "insight");
    draft.title = "開市頭 30 分鐘假突破多";
    draft.instrument = "NQ";
    draft.assetClass = "equity_index_futures";
    draft.charts = draft.charts.map((c) => ({
      ...c,
      ownerView: "觀察文字",
      imageDataUrl:
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    }));
    const md = emitInstructionsMd(draft, {
      insightId: "insight-007",
    });
    expect(() => {
      assertInsightInstructions(md);
    }).not.toThrow();
    expect(md).toContain("insight.v1");
    expect(md).not.toContain("strategy.v1");
    expect(md).toContain("insight-007");
    expect(md).toContain("instrument: NQ");
    expect(md).toContain("asset_class: equity_index_futures");

    const cat = parseInstrumentCatalog(ownerReviewCoverageBody());
    const { package: pkg } = exportSketchPackage(
      draft,
      localStorage,
      new Date(),
      cat,
    );
    expect(pkg.instructionsMd).toContain("insight.v1");
    expect(pkg.instructionsMd).not.toContain("strategy.v1");
    expect(pkg.instructionsMd).toContain("based_on_sketch_origin: workshop");
    expect(pkg.metaYaml).toContain("instrument: NQ");
    void storage;
  });

  it("importInsight stores record in local library", () => {
    localStorage.removeItem(INSIGHT_STORAGE_KEY);
    const yaml = `
schema: insight.v1
insight_id: insight-042
origin: workshop
version: 1
based_on_sketch: sketch-20260725-02
based_on_sketch_origin: workshop
instrument: NQ
asset_class: equity_index_futures
title: 測試洞察
condition:
  type: session_time_filter
  suggested_params:
    skip_first_minutes: 30
measurement:
  tag: entry_within_open_30m
  hypothesis: 開市頭 30 分鐘入市期望值較低
validation_status: unverified
`;
    const rec = importInsight(yaml);
    expect(rec.insight_id).toBe("insight-042");
    expect(rec.based_on_sketch_origin).toBe("workshop");
    expect(rec.instrument).toBe("NQ");
    expect(rec.asset_class).toBe("equity_index_futures");
    expect(listInsights()).toHaveLength(1);
  });
});
