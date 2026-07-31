import { load as yamlLoad } from "js-yaml";
import { describe, expect, it } from "vitest";

import {
  OWNER_REVIEW_CATALOG,
  ownerReviewCoverageBody,
} from "../catalog/fixtureCatalog";
import { parseInstrumentCatalog } from "../catalog/parseCatalog";
import {
  assertInsightInstructions,
  assertInstructionsEmbedsSchema,
  emitInstructionsMd,
} from "../sketch/instructions";
import { emitMetaYaml } from "../sketch/metaYaml";
import { createEmptyDraft } from "../sketch/store";
import type { SketchDraft } from "../sketch/types";
import {
  parseStrategyUniverse,
  validateUniverseGates,
} from "../strategy/universe";
import {
  fixtureInvalidNqGcStrategy,
  fixturePrimaryOnlyStrategy,
  fixtureValidNqYmStrategy,
} from "./ownerReviewFixtures";

function nqDraft(id = "sketch-20260727-01"): SketchDraft {
  const d = createEmptyDraft([]);
  return {
    ...d,
    sketchId: id,
    title: "NQ test",
    instrument: "NQ",
    assetClass: "equity_index_futures",
    charts: d.charts.map((c) => ({ ...c, ownerView: "view" })),
  };
}

const readyRef = {
  kind: "ready" as const,
  origin: "workshop" as const,
  sketchId: "sketch-20260727-01",
  instrument: "NQ",
  assetClass: "equity_index_futures",
};

describe("P2 instrument closed-loop integration", () => {
  it("fixture catalog isolated and complete", () => {
    const parsed = parseInstrumentCatalog(ownerReviewCoverageBody());
    expect(parsed.status).toBe("ready");
    expect(OWNER_REVIEW_CATALOG.map((r) => r.symbol).sort()).toEqual([
      "GC",
      "NQ",
      "YM",
    ]);
  });

  it("valid NQ+YM strategy passes all gates with origin-aware sketch", () => {
    const yaml = fixtureValidNqYmStrategy("sketch-20260727-01");
    const doc = yamlLoad(yaml);
    const parsed = parseStrategyUniverse(doc);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const issues = validateUniverseGates(
      parsed.universe,
      OWNER_REVIEW_CATALOG,
      readyRef,
    );
    expect(issues).toEqual([]);
  });

  it("invalid NQ+GC fails closed", () => {
    const yaml = fixtureInvalidNqGcStrategy("sketch-20260727-01");
    const doc = yamlLoad(yaml);
    const parsed = parseStrategyUniverse(doc);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const issues = validateUniverseGates(
      parsed.universe,
      OWNER_REVIEW_CATALOG,
      readyRef,
    );
    expect(issues.length).toBeGreaterThan(0);
  });

  it("primary-only empty rationale passes", () => {
    const yaml = fixturePrimaryOnlyStrategy("sketch-20260727-01");
    const doc = yamlLoad(yaml);
    const parsed = parseStrategyUniverse(doc);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(Object.keys(parsed.universe.expansionRationale)).toHaveLength(0);
    const issues = validateUniverseGates(
      parsed.universe,
      OWNER_REVIEW_CATALOG,
      { kind: "none" },
    );
    expect(issues).toEqual([]);
  });

  it("meta + strategy instructions semantic round-trip instrument fields", () => {
    const draft = nqDraft();
    const yaml = emitMetaYaml(draft);
    expect(yaml).toMatch(/^instrument: NQ$/m);
    expect(yaml).toMatch(/^asset_class: equity_index_futures$/m);
    expect(yaml).not.toMatch(/instrument: null/);
    const md = emitInstructionsMd(draft);
    expect(() =>
      assertInstructionsEmbedsSchema(md, {
        sketchId: draft.sketchId,
        origin: "workshop",
        instrument: "NQ",
      }),
    ).not.toThrow();
    expect(md).toContain("based_on_sketch_origin: workshop");
    expect(md).toContain("primary_instrument: NQ");
  });

  it("insight instructions have composite lineage and no strategy.v1", () => {
    const draft = nqDraft();
    draft.kind = "insight";
    draft.origin = "workshop";
    const md = emitInstructionsMd(draft, { insightId: "insight-001" });
    expect(() =>
      assertInsightInstructions(md, {
        sketchId: draft.sketchId,
        origin: "workshop",
        instrument: "NQ",
      }),
    ).not.toThrow();
    expect(md).toContain("based_on_sketch_origin: workshop");
    expect(md).not.toContain("strategy.v1");
  });

  it("journal-app GC insight instructions fill origin+instrument", () => {
    const draft = nqDraft();
    draft.kind = "insight";
    draft.origin = "journal-app";
    draft.instrument = "GC";
    draft.assetClass = "commodity_futures";
    const md = emitInstructionsMd(draft, { insightId: "insight-009" });
    expect(md).toContain("based_on_sketch_origin: journal-app");
    expect(md).toContain("instrument: GC");
    expect(md).toContain("asset_class: commodity_futures");
  });
});
