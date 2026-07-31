/**
 * Correction A regression: D8 isolation, D10 zero-write, D12/D13, lineage.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { OWNER_REVIEW_CATALOG } from "../catalog/fixtureCatalog";
import { ownerReviewCoverageBody } from "../catalog/fixtureCatalog";
import { parseInstrumentCatalog } from "../catalog/parseCatalog";
import {
  INSIGHT_STORAGE_KEY,
  importInsight,
  listInsights,
  parseInsightYaml,
  readInsightStore,
  validateInsightCandidate,
} from "../insightStore";
import { createMemoryStorage, readP2LocalStorageBytes } from "../storage/memoryStorage";
import { validateDraftAgainstCatalog } from "../sketch/catalogExportGate";
import { TINY_PNG_DATA_URL } from "../sketch/pngGate";
import {
  createEmptyDraft,
  duplicateDraftAsNew,
  exportSketchPackage,
  saveDraft,
} from "../sketch/store";
import { SKETCH_STORAGE_KEY } from "../sketch/types";
import { applyOwnerReviewFixture } from "./ownerReviewSession";

function withExportImages<T extends { charts: { imageDataUrl: string | null; ownerView: string }[] }>(
  draft: T,
): T {
  return {
    ...draft,
    charts: draft.charts.map((c) => ({
      ...c,
      ownerView: c.ownerView || "v",
      imageDataUrl: TINY_PNG_DATA_URL,
    })),
  };
}

afterEach(() => {
  localStorage.removeItem(SKETCH_STORAGE_KEY);
  localStorage.removeItem(INSIGHT_STORAGE_KEY);
  vi.unstubAllGlobals();
});

describe("D8 owner-review isolation", () => {
  it("fixture ops do not change normal localStorage bytes", () => {
    // Seed normal store
    const draft = createEmptyDraft([]);
    draft.title = "NORMAL_ONLY";
    saveDraft(draft, localStorage);
    const before = readP2LocalStorageBytes(localStorage);

    const mem = createMemoryStorage();
    applyOwnerReviewFixture("valid_nq_ym", mem);
    applyOwnerReviewFixture("invalid_nq_gc", mem);

    const after = readP2LocalStorageBytes(localStorage);
    expect(after).toEqual(before);
    // Memory has fixture, normal has NORMAL_ONLY
    expect(mem.getItem(SKETCH_STORAGE_KEY)).toBeTruthy();
    expect(localStorage.getItem(SKETCH_STORAGE_KEY)).toContain("NORMAL_ONLY");
    expect(mem.getItem(SKETCH_STORAGE_KEY)).not.toContain("NORMAL_ONLY");
  });
});

describe("D10 insight zero-write on mismatch", () => {
  it("NQ + commodity_futures does not write and does not claim success", () => {
    localStorage.removeItem(INSIGHT_STORAGE_KEY);
    const yaml = `
schema: insight.v1
insight_id: insight-bad
origin: workshop
version: 1
based_on_sketch: sketch-20260727-01
based_on_sketch_origin: workshop
instrument: NQ
asset_class: commodity_futures
title: mismatch
condition: { type: x, suggested_params: {} }
measurement: { tag: t, hypothesis: h }
validation_status: unverified
`;
    const candidate = parseInsightYaml(yaml);
    const errs = validateInsightCandidate(candidate, {
      catalogRows: OWNER_REVIEW_CATALOG,
      catalogReady: true,
    });
    expect(errs.length).toBeGreaterThan(0);
    expect(() =>
      importInsight(yaml, localStorage, {
        catalogRows: OWNER_REVIEW_CATALOG,
        catalogReady: true,
      }),
    ).toThrow();
    expect(listInsights(localStorage)).toHaveLength(0);
    expect(readInsightStore(localStorage).items).toHaveLength(0);
  });

  it("missing based_on_sketch_origin fails parse with zero write", () => {
    localStorage.removeItem(INSIGHT_STORAGE_KEY);
    const yaml = `
schema: insight.v1
insight_id: insight-no-origin
origin: workshop
version: 1
based_on_sketch: sketch-20260727-01
instrument: NQ
asset_class: equity_index_futures
title: x
condition: { type: x, suggested_params: {} }
measurement: { tag: t, hypothesis: h }
`;
    expect(() => parseInsightYaml(yaml)).toThrow(/based_on_sketch_origin/);
    expect(listInsights(localStorage)).toHaveLength(0);
  });

  it("composite identity does not overwrite different origin", () => {
    localStorage.removeItem(INSIGHT_STORAGE_KEY);
    const base = `
schema: insight.v1
insight_id: insight-same
origin: workshop
version: 1
based_on_sketch: sketch-20260727-01
based_on_sketch_origin: workshop
instrument: NQ
asset_class: equity_index_futures
title: A
condition: { type: x, suggested_params: {} }
measurement: { tag: t, hypothesis: h }
`;
    const other = base.replace("origin: workshop", "origin: journal-app");
    const ctx = {
      catalogRows: OWNER_REVIEW_CATALOG,
      catalogReady: true,
    };
    importInsight(base, localStorage, ctx);
    importInsight(other, localStorage, ctx);
    expect(listInsights(localStorage)).toHaveLength(2);
  });
});

describe("D12 export catalog recheck", () => {
  it("rejects NQ + commodity class mismatch before package write", () => {
    const storage = createMemoryStorage();
    const cat = parseInstrumentCatalog(ownerReviewCoverageBody());
    let draft = createEmptyDraft([]);
    draft.title = "t";
    draft.rationale = "r";
    draft.instrument = "NQ";
    draft.assetClass = "commodity_futures";
    draft = withExportImages({
      ...draft,
      charts: draft.charts.map((c) => ({ ...c, ownerView: "v" })),
    });
    const reasons = validateDraftAgainstCatalog(draft, cat);
    expect(reasons.some((r) => r.includes("唔一致"))).toBe(true);
    expect(() =>
      exportSketchPackage(draft, storage, new Date(), cat),
    ).toThrow(/唔一致/);
    // no package recorded
    expect(storage.getItem(SKETCH_STORAGE_KEY)).toBeNull();
  });

  it("rejects unknown symbol", () => {
    const cat = parseInstrumentCatalog(ownerReviewCoverageBody());
    const draft = createEmptyDraft([]);
    draft.title = "t";
    draft.instrument = "ES";
    draft.assetClass = "equity_index_futures";
    draft.charts = draft.charts.map((c) => ({ ...c, ownerView: "v" }));
    expect(validateDraftAgainstCatalog(draft, cat).some((r) => r.includes("ES"))).toBe(
      true,
    );
  });
});

describe("D13 duplicate cannot re-label images", () => {
  it("clears images and ownerView so unlock cannot keep NQ shot as GC", () => {
    const d = createEmptyDraft([]);
    d.instrument = "NQ";
    d.assetClass = "equity_index_futures";
    d.instrumentLocked = true;
    d.exported = true;
    d.exportedAt = "x";
    d.charts[0] = {
      ...d.charts[0],
      imageDataUrl: "data:image/png;base64,NQ",
      imageFileName: "nq.png",
      ownerView: "NQ view",
    };
    const copy = duplicateDraftAsNew(d, [d.sketchId]);
    expect(copy.charts[0].imageDataUrl).toBeNull();
    expect(copy.charts[0].ownerView).toBe("");
    expect(copy.instrumentLocked).toBe(false);
  });
});

describe("D11 instructions mutation target", () => {
  it("assert fails when based_on_sketch_origin removed", async () => {
    const { emitInstructionsMd, assertInstructionsEmbedsSchema } =
      await import("../sketch/instructions");
    const draft = createEmptyDraft([]);
    draft.instrument = "NQ";
    draft.assetClass = "equity_index_futures";
    let md = emitInstructionsMd(draft);
    md = md.replace(/based_on_sketch_origin:[^\n]*\n/g, "");
    expect(() => assertInstructionsEmbedsSchema(md)).toThrow(
      /based_on_sketch_origin/,
    );
  });
});
