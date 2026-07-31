import { describe, expect, it } from "vitest";

import { OWNER_REVIEW_CATALOG } from "../catalog/fixtureCatalog";
import {
  parseStrategyUniverse,
  sketchRefFromLoadState,
  validateUniverseGates,
  type StrategyUniverse,
} from "./universe";

function baseUniverse(
  over: Partial<StrategyUniverse> = {},
): StrategyUniverse {
  return {
    primaryInstrument: "NQ",
    assetClass: "equity_index_futures",
    contracts: ["NQ", "YM"],
    expansionRationale: {
      YM: "同屬股指趨勢結構",
    },
    session: "eth",
    ...over,
  };
}

describe("parseStrategyUniverse", () => {
  it("parses valid NQ+YM universe", () => {
    const doc = {
      universe: {
        primary_instrument: "NQ",
        asset_class: "equity_index_futures",
        contracts: ["NQ", "YM"],
        expansion_rationale: { YM: "同 class" },
        session: "eth",
      },
    };
    const r = parseStrategyUniverse(doc);
    expect(r.ok).toBe(true);
  });

  it("rejects null expansion_rationale", () => {
    const r = parseStrategyUniverse({
      universe: {
        primary_instrument: "NQ",
        asset_class: "equity_index_futures",
        contracts: ["NQ"],
        expansion_rationale: null,
        session: "eth",
      },
    });
    expect(r.ok).toBe(false);
  });
});

describe("validateUniverseGates origin-aware", () => {
  it("accepts valid NQ+YM with ready sketch ref", () => {
    const issues = validateUniverseGates(
      baseUniverse(),
      OWNER_REVIEW_CATALOG,
      {
        kind: "ready",
        origin: "workshop",
        sketchId: "sketch-20260727-01",
        instrument: "NQ",
        assetClass: "equity_index_futures",
      },
    );
    expect(issues).toEqual([]);
  });

  it("accepts primary-only empty rationale", () => {
    const issues = validateUniverseGates(
      baseUniverse({
        contracts: ["NQ"],
        expansionRationale: {},
      }),
      OWNER_REVIEW_CATALOG,
      { kind: "none" },
    );
    expect(issues).toEqual([]);
  });

  it("rejects NQ+GC cross-class", () => {
    const issues = validateUniverseGates(
      baseUniverse({
        contracts: ["NQ", "GC"],
        expansionRationale: { GC: "不應通過" },
      }),
      OWNER_REVIEW_CATALOG,
      { kind: "none" },
    );
    expect(issues.some((i) => i.message.includes("GC"))).toBe(true);
  });

  it("rejects primary mismatch vs ready sketch (origin-aware)", () => {
    const issues = validateUniverseGates(
      baseUniverse({
        primaryInstrument: "YM",
        contracts: ["YM"],
        expansionRationale: {},
      }),
      OWNER_REVIEW_CATALOG,
      {
        kind: "ready",
        origin: "workshop",
        sketchId: "s1",
        instrument: "NQ",
        assetClass: "equity_index_futures",
      },
    );
    expect(issues.some((i) => i.message.includes("唔一致"))).toBe(true);
  });

  it("rejects asset_class mismatch unconditionally on ready", () => {
    const issues = validateUniverseGates(
      baseUniverse(),
      OWNER_REVIEW_CATALOG,
      {
        kind: "ready",
        origin: "workshop",
        sketchId: "s1",
        instrument: "NQ",
        assetClass: "commodity_futures",
      },
    );
    expect(
      issues.some((i) => i.path === "universe.asset_class"),
    ).toBe(true);
  });

  it("loading sketch disables confirm path via issues", () => {
    const issues = validateUniverseGates(
      baseUniverse(),
      OWNER_REVIEW_CATALOG,
      { kind: "loading", origin: "workshop", sketchId: "s1" },
    );
    expect(issues.some((i) => i.path === "sketch_ref")).toBe(true);
  });

  it("error (5xx) is not treated as missing 404", () => {
    const issues = validateUniverseGates(
      baseUniverse(),
      OWNER_REVIEW_CATALOG,
      { kind: "error", message: "HTTP 500" },
    );
    expect(issues.some((i) => i.message.includes("失敗"))).toBe(true);
  });

  it("missing 404 does not add gate issues (warning only)", () => {
    const issues = validateUniverseGates(
      baseUniverse(),
      OWNER_REVIEW_CATALOG,
      {
        kind: "missing",
        sketchId: "sketch-gone",
        origin: "workshop",
      },
    );
    expect(issues).toEqual([]);
  });

  it("same sketch_id different origin does not auto-match without ready ref", () => {
    // Without ready ref for that origin, no false localStorage hit
    const issues = validateUniverseGates(
      baseUniverse(),
      OWNER_REVIEW_CATALOG,
      { kind: "none" },
    );
    expect(issues).toEqual([]);
  });

  it("rejects blank rationale and extra keys", () => {
    expect(
      validateUniverseGates(
        baseUniverse({ expansionRationale: { YM: "   " } }),
        OWNER_REVIEW_CATALOG,
        { kind: "none" },
      ).length,
    ).toBeGreaterThan(0);
    expect(
      validateUniverseGates(
        baseUniverse({
          contracts: ["NQ"],
          expansionRationale: { YM: "多餘" },
        }),
        OWNER_REVIEW_CATALOG,
        { kind: "none" },
      ).length,
    ).toBeGreaterThan(0);
  });
});

describe("sketchRefFromLoadState", () => {
  it("maps not_found to missing and error separately", () => {
    expect(
      sketchRefFromLoadState({
        kind: "not_found",
        origin: "workshop",
        sketchId: "s1",
      }).kind,
    ).toBe("missing");
    expect(
      sketchRefFromLoadState({
        kind: "error",
        message: "x",
        isNotFound: false,
      }).kind,
    ).toBe("error");
  });
});
