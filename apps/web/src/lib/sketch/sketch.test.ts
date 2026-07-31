import { afterEach, describe, expect, it } from "vitest";

import { buildSketchZipBlob } from "./downloadPackage";
import { canExportSketch, exportBlockingReasons } from "./exportReadiness";
import { nextSketchId, parseSketchSeq, localYyyymmdd } from "./id";
import {
  assertInstructionsEmbedsSchema,
  assertInstructionsSelfContained,
  emitInstructionsMd,
} from "./instructions";
import { allOwnerViewsFilled, emitMetaYaml } from "./metaYaml";
import { CANONICAL_CHART_FILES, sketchRelativeDir } from "./paths";
import { TINY_PNG_DATA_URL } from "./pngGate";
import { ownerReviewCoverageBody } from "../catalog/fixtureCatalog";
import { parseInstrumentCatalog } from "../catalog/parseCatalog";
import type { CatalogState } from "../catalog/types";
import {
  createEmptyDraft,
  exportSketchPackage,
  listDrafts,
  loadOrCreateActiveDraft,
  prepareSketchPackage,
  saveDraft,
  selectDraft,
  startNewDraft,
} from "./store";
import { SKETCH_STORAGE_KEY, type SketchDraft } from "./types";

function readyCatalog(): CatalogState {
  const p = parseInstrumentCatalog(ownerReviewCoverageBody());
  if (p.status !== "ready") {
    throw new Error("fixture catalog not ready");
  }
  return p;
}

/** Minimal in-memory Storage for store tests. */
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

function fillViews(draft: SketchDraft, text = "判斷"): SketchDraft {
  return {
    ...draft,
    // Must NOT equal sketchId — otherwise title==id fallback would still pass.
    title: "NQ 趨勢日回踩 90EMA",
    rationale: "大框架趨勢中嘅細框架回調",
    instrument: draft.instrument ?? "NQ",
    assetClass: draft.assetClass ?? "equity_index_futures",
    charts: draft.charts.map((c, i) => ({
      ...c,
      ownerView: `${text} ${i + 1}`,
      imageDataUrl: TINY_PNG_DATA_URL,
      imageFileName: CANONICAL_CHART_FILES[i] ?? `chart-slot-${i + 1}.png`,
    })),
  };
}

describe("sketch id", () => {
  it("sequences NN for the same local day", () => {
    const now = new Date(2026, 6, 25, 12, 0, 0);
    const day = localYyyymmdd(now);
    expect(nextSketchId([], now)).toBe(`sketch-${day}-01`);
    expect(
      nextSketchId([`sketch-${day}-01`, `sketch-${day}-02`], now),
    ).toBe(`sketch-${day}-03`);
    expect(parseSketchSeq(`sketch-${day}-02`, day)).toBe(2);
  });
});

describe("meta.yaml emitter", () => {
  it("always emits indicators_shown even when empty []", () => {
    const draft = createEmptyDraft([]);
    draft.charts[0] = { ...draft.charts[0], indicatorsShown: [], ownerView: "x" };
    const yaml = emitMetaYaml(draft);
    expect(yaml).toMatch(/indicators_shown: \[\]/);
    expect(yaml).toContain("schema: sketch.v1");
    expect(yaml).toContain(`sketch_id: ${draft.sketchId}`);
  });

  it("writes Owner title verbatim — never falls back to sketchId", () => {
    const draft = fillViews(createEmptyDraft([]));
    expect(draft.title).not.toBe(draft.sketchId);
    const yaml = emitMetaYaml(draft);
    // Spaces force YAML quotes — still must be Owner's string, never sketchId.
    expect(yaml).toMatch(/^title: "NQ 趨勢日回踩 90EMA"$/m);
    expect(yaml).not.toMatch(new RegExp(`^title: ${draft.sketchId}$`, "m"));
    expect(yaml).not.toContain(`title: ${draft.sketchId}`);
  });

  it("omits null-style optionals and keeps owner_view block", () => {
    const draft = fillViews(createEmptyDraft([]));
    draft.charts[0] = {
      ...draft.charts[0],
      ownerView: "第一行\n第二行",
    };
    const yaml = emitMetaYaml(draft);
    expect(yaml).not.toMatch(/: null/);
    expect(yaml).toContain("owner_view: |");
    expect(yaml).toContain("第一行");
  });
});

describe("export readiness (constraint #5 revised)", () => {
  it("blocks export when title empty even if four views filled", () => {
    const draft = fillViews(createEmptyDraft([]));
    draft.title = "";
    expect(allOwnerViewsFilled(draft)).toBe(true);
    expect(canExportSketch(draft)).toBe(false);
    expect(exportBlockingReasons(draft)).toContain("標題未填");
  });

  it("lists which chart cells are missing", () => {
    const draft = createEmptyDraft([]);
    draft.title = "有標題";
    draft.charts[0] = { ...draft.charts[0], ownerView: "D ok" };
    draft.charts[1] = { ...draft.charts[1], ownerView: "1H ok" };
    // 30m + 5m empty
    const reasons = exportBlockingReasons(draft);
    expect(reasons.some((r) => r.includes("30m") && r.includes("5m"))).toBe(
      true,
    );
  });

  it("allows export when title + instrument + four views + PNGs + rationale filled", () => {
    expect(canExportSketch(fillViews(createEmptyDraft([])))).toBe(true);
  });

  it("blocks export without primary instrument", () => {
    const draft = fillViews(createEmptyDraft([]));
    draft.instrument = null;
    draft.assetClass = null;
    expect(canExportSketch(draft)).toBe(false);
    expect(exportBlockingReasons(draft)).toContain("未揀 primary instrument");
  });

  it("blocks export without images or with fake PNG / empty rationale", () => {
    const noImg = fillViews(createEmptyDraft([]));
    noImg.charts = noImg.charts.map((c) => ({
      ...c,
      imageDataUrl: null,
      imageFileName: null,
    }));
    expect(canExportSketch(noImg)).toBe(false);
    expect(exportBlockingReasons(noImg).some((r) => r.includes("未上載"))).toBe(
      true,
    );

    const jpeg = fillViews(createEmptyDraft([]));
    jpeg.charts[0] = {
      ...jpeg.charts[0],
      imageDataUrl: "data:image/jpeg;base64,/9j/4AAQ",
    };
    expect(exportBlockingReasons(jpeg).some((r) => r.includes("PNG"))).toBe(
      true,
    );

    const fakeSig = fillViews(createEmptyDraft([]));
    fakeSig.charts[0] = {
      ...fakeSig.charts[0],
      imageDataUrl: "data:image/png;base64,NOTAPNG",
    };
    expect(
      exportBlockingReasons(fakeSig).some((r) => r.includes("簽名")),
    ).toBe(true);

    const noRationale = fillViews(createEmptyDraft([]));
    noRationale.rationale = "   ";
    expect(exportBlockingReasons(noRationale)).toContain("整體理據未填");
  });

  it("insight may omit rationale but still needs four PNGs", () => {
    const d = fillViews(createEmptyDraft([]));
    d.kind = "insight";
    d.rationale = "";
    expect(canExportSketch(d)).toBe(true);
    d.charts[0] = { ...d.charts[0], imageDataUrl: null };
    expect(canExportSketch(d)).toBe(false);
  });

  it("keeps canonical slot filenames when timeframes duplicate/edit", () => {
    const draft = fillViews(createEmptyDraft([]));
    draft.charts[0] = { ...draft.charts[0], timeframe: "15m" };
    draft.charts[1] = { ...draft.charts[1], timeframe: "15m" };
    const { package: pkg } = exportSketchPackage(
      draft,
      memoryStorage(),
      new Date(),
      readyCatalog(),
    );
    expect(pkg.chartImages.map((c) => c.fileName)).toEqual([
      ...CANONICAL_CHART_FILES,
    ]);
    expect(pkg.metaYaml).toMatch(/timeframe: 15m[\s\S]*timeframe: 15m/);
    expect(pkg.metaYaml).toContain("file: chart-D.png");
    expect(pkg.metaYaml).toContain("file: chart-1H.png");
    expect(pkg.metaYaml).not.toContain("chart-15m.png");
  });

  it("exact-four cardinality: 3 blocked, 4 ready, 5 blocked (no silent slice)", () => {
    const four = fillViews(createEmptyDraft([]));
    expect(four.charts).toHaveLength(4);
    expect(canExportSketch(four)).toBe(true);

    const three = fillViews(createEmptyDraft([]));
    three.charts = three.charts.slice(0, 3);
    expect(canExportSketch(three)).toBe(false);
    expect(
      exportBlockingReasons(three).some((r) =>
        r.includes("預期 4 格") && r.includes("實際 3"),
      ),
    ).toBe(true);

    const five = fillViews(createEmptyDraft([]));
    five.charts = [
      ...five.charts,
      {
        ...five.charts[0],
        slotId: "slot-5-extra",
        timeframe: "1m",
        imageDataUrl: TINY_PNG_DATA_URL,
        imageFileName: "extra.png",
        ownerView: "extra view",
      },
    ];
    expect(five.charts).toHaveLength(5);
    expect(canExportSketch(five)).toBe(false);
    expect(
      exportBlockingReasons(five).some((r) =>
        r.includes("預期 4 格") && r.includes("實際 5"),
      ),
    ).toBe(true);
    expect(() =>
      exportSketchPackage(five, memoryStorage(), new Date(), readyCatalog()),
    ).toThrow(/圖格數量錯誤|預期 4/);
    expect(() => prepareSketchPackage(five, readyCatalog())).toThrow(
      /圖格數量錯誤|預期 4/,
    );
    // Four-slot package remains exact six members (control)
    const pkg = prepareSketchPackage(four, readyCatalog());
    expect(pkg.files).toEqual([
      ...CANONICAL_CHART_FILES,
      "meta.yaml",
      "INSTRUCTIONS.md",
    ]);
    expect(pkg.chartImages).toHaveLength(4);
    expect(pkg.chartImages.some((c) => c.fileName.includes("slot-5"))).toBe(
      false,
    );
  });
});

describe("INSTRUCTIONS.md", () => {
  it("is self-contained (no docs/0X references)", () => {
    const draft = fillViews(createEmptyDraft(["x"]));
    const md = emitInstructionsMd(draft);
    expect(() => {
      assertInstructionsSelfContained(md);
    }).not.toThrow();
    expect(md).not.toMatch(/docs\/0\d/i);
    expect(md).toContain(draft.sketchId);
    expect(md).toContain(sketchRelativeDir(draft.sketchId));
  });

  it("embeds full schema essentials (six sections + strategy.v1 vocabulary)", () => {
    const draft = fillViews(createEmptyDraft([]));
    const md = emitInstructionsMd(draft);
    expect(() => {
      assertInstructionsEmbedsSchema(md);
    }).not.toThrow();
    expect(md).toContain("strategy.v1");
    expect(md).toContain("pullback_lifecycle");
    expect(md).toContain("signal_bar");
    expect(md).toContain("provenance");
    expect(md).toContain("unquantified_notes");
    const heads = md.match(/^## \d+\. /gm) ?? [];
    expect(heads.length).toBeGreaterThanOrEqual(6);
    // Must not use the old empty-title fallback wording.
    expect(md).not.toContain("（未填）");
  });

  it("assertInstructionsSelfContained fails when docs/0X appears", () => {
    expect(() => {
      assertInstructionsSelfContained("see docs/05-file-contract");
    }).toThrow(/docs\/0X/);
  });

  it("assertInstructionsEmbedsSchema fails when a required token is missing", () => {
    expect(() => {
      assertInstructionsEmbedsSchema("## 1. a\n## 2. b\n## 3. c\n## 4. d\n## 5. e\n## 6. f\n");
    }).toThrow(/strategy\.v1/);
  });
});

describe("sketch store", () => {
  afterEach(() => {
    localStorage.removeItem(SKETCH_STORAGE_KEY);
  });

  it("saveDraft works with empty title and incomplete views (no preconditions)", () => {
    const storage = memoryStorage();
    const draft = createEmptyDraft([]);
    draft.title = "";
    draft.charts[0] = { ...draft.charts[0], ownerView: "只填日線" };
    const saved = saveDraft(draft, storage);
    expect(saved.charts[0].ownerView).toBe("只填日線");
    expect(saved.title).toBe("");
    const reloaded = loadOrCreateActiveDraft(storage);
    expect(reloaded.charts[0].ownerView).toBe("只填日線");
    expect(canExportSketch(reloaded)).toBe(false);
  });

  it("export rejects incomplete packs and accepts filled ones with real path", () => {
    const storage = memoryStorage();
    const cat = readyCatalog();
    const incomplete = createEmptyDraft([]);
    expect(() =>
      exportSketchPackage(incomplete, storage, new Date(), cat),
    ).toThrow();

    const titleOnly = fillViews(createEmptyDraft([]));
    titleOnly.title = "";
    expect(() =>
      exportSketchPackage(titleOnly, storage, new Date(), cat),
    ).toThrow(/標題/);

    const filled = fillViews(createEmptyDraft([]));
    const { package: pkg, draft } = exportSketchPackage(
      filled,
      storage,
      new Date(),
      readyCatalog(),
    );
    expect(draft.exported).toBe(true);
    expect(pkg.relativeDir).toBe(
      `data/sketches/workshop/${filled.sketchId}/`,
    );
    expect(pkg.metaYaml).toMatch(/^title: "NQ 趨勢日回踩 90EMA"$/m);
    expect(pkg.metaYaml).toMatch(/^instrument: NQ$/m);
    expect(pkg.metaYaml).toMatch(/^asset_class: equity_index_futures$/m);
    expect(pkg.instructionsMd).toContain("strategy.v1");
    expect(pkg.instructionsMd).toContain("primary_instrument: NQ");
    expect(pkg.chartImages).toHaveLength(4);
  });

  it("download zip embeds the same meta + instructions as the Dialog package", async () => {
    const storage = memoryStorage();
    const filled = fillViews(createEmptyDraft([]));
    // Tiny red-pixel-ish payload
    filled.charts[0] = {
      ...filled.charts[0],
      imageDataUrl:
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    };
    const { package: pkg } = exportSketchPackage(
      filled,
      storage,
      new Date(),
      readyCatalog(),
    );
    const blob = await buildSketchZipBlob(pkg);
    expect(blob.size).toBeGreaterThan(100);

    // Round-trip via JSZip to prove content identity (not just "has a button").
    const JSZip = (await import("jszip")).default;
    const zip = await JSZip.loadAsync(blob);
    const metaPath = `${pkg.sketchId}/meta.yaml`;
    const instrPath = `${pkg.sketchId}/INSTRUCTIONS.md`;
    expect(await zip.file(metaPath)?.async("string")).toBe(pkg.metaYaml);
    expect(await zip.file(instrPath)?.async("string")).toBe(pkg.instructionsMd);
    const chart0 = pkg.chartImages[0];
    const chartBytes = await zip
      .file(`${pkg.sketchId}/${chart0.fileName}`)
      ?.async("uint8array");
    expect(chartBytes?.length).toBeGreaterThan(0);
  });

  it("truth correction D3: generated INSTRUCTIONS references exactly the four ZIP members with canonical case", async () => {
    const storage = memoryStorage();
    const filled = fillViews(createEmptyDraft([]));
    const { package: pkg } = exportSketchPackage(
      filled,
      storage,
      new Date(),
      readyCatalog(),
    );
    const blob = await buildSketchZipBlob(pkg);
    const JSZip = (await import("jszip")).default;
    const zip = await JSZip.loadAsync(blob);
    const instructionsPath = `${pkg.sketchId}/INSTRUCTIONS.md`;
    const instructions = await zip.file(instructionsPath)!.async("string");

    const instructionRefs = [
      ...instructions.matchAll(/\bchart-[A-Za-z0-9]+\.png\b/g),
    ].map((match) => match[0]);
    const uniqueRefs = [...new Set(instructionRefs)].sort();
    expect(uniqueRefs).toEqual([...CANONICAL_CHART_FILES].sort());
    expect(uniqueRefs).toHaveLength(4);

    const zipChartMembers = Object.keys(zip.files)
      .filter(
        (path) =>
          path.startsWith(`${pkg.sketchId}/chart-`) &&
          path.endsWith(".png"),
      )
      .map((path) => path.slice(`${pkg.sketchId}/`.length))
      .sort();
    expect(zipChartMembers).toEqual([...CANONICAL_CHART_FILES].sort());
    for (const ref of instructionRefs) {
      expect(zip.file(`${pkg.sketchId}/${ref}`)).not.toBeNull();
    }
  });

  it("keeps prior draft after 新草圖 and restores full content on select", () => {
    const storage = memoryStorage();
    let a = fillViews(createEmptyDraft([]));
    a = saveDraft(a, storage);
    const aId = a.sketchId;
    a = { ...a, charts: a.charts.map((c, i) => ({ ...c, ownerView: `A格${i}` })) };
    a = saveDraft(a, storage);

    const b = startNewDraft(a, storage);
    expect(b.sketchId).not.toBe(aId);
    const listed = listDrafts(storage);
    expect(listed.some((d) => d.sketchId === aId)).toBe(true);

    const restored = selectDraft(aId, b, storage);
    expect(restored).not.toBeNull();
    expect(restored!.charts[0].ownerView).toBe("A格0");
    expect(restored!.title).toBe("NQ 趨勢日回踩 90EMA");
  });
});
