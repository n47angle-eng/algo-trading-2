import { afterEach, describe, expect, it, vi } from "vitest";

import type { SketchDetailResponse } from "../../api/client";
import { ownerReviewCoverageBody } from "../catalog/fixtureCatalog";
import { parseInstrumentCatalog } from "../catalog/parseCatalog";
import { buildSketchZipBlob } from "./downloadPackage";
import {
  assertSketchPostMatchesPackage,
  expectedFromPackage,
  humanizeSketchPostFailure,
  postSketchZip,
} from "./postSketchPackage";
import {
  commitLocalSketchExport,
  createEmptyDraft,
  prepareSketchPackage,
  readSketchStore,
} from "./store";
import type { SketchDraft } from "./types";

function filledDraft(): SketchDraft {
  const d = createEmptyDraft([]);
  return {
    ...d,
    title: "NQ live test",
    rationale: "live test rationale",
    instrument: "NQ",
    assetClass: "equity_index_futures",
    charts: d.charts.map((c, i) => ({
      ...c,
      ownerView: `view ${i}`,
      imageDataUrl:
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    })),
  };
}

function readyCatalog() {
  const p = parseInstrumentCatalog(ownerReviewCoverageBody());
  if (p.status !== "ready") throw new Error("catalog");
  return p;
}

function detail201(
  draft: SketchDraft,
  over: Partial<SketchDetailResponse["meta"]> = {},
): SketchDetailResponse {
  const charts = draft.charts.map((c) => ({
    file: `chart-${c.timeframe}.png`,
    timeframe: c.timeframe,
    role: c.role,
    indicators_shown: [...c.indicatorsShown],
    owner_view: c.ownerView,
  }));
  return {
    schema: "sketch_detail.v1",
    meta: {
      schema: "sketch.v1",
      sketch_id: draft.sketchId,
      kind: "strategy",
      origin: "workshop",
      chart_source: "screenshot",
      instructions_template: "instructions.v1",
      instrument: "NQ",
      asset_class: "equity_index_futures",
      created: "2026-07-27",
      title: draft.title,
      rationale: draft.rationale || "r",
      charts,
      ...over,
    },
    instructions_markdown: "# ok",
    images: charts.map((c) => ({
      file: c.file,
      url: `/api/v1/sketches/workshop/${draft.sketchId}/images/${c.file}`,
      content_type: "image/png",
      byte_count: 12,
      sha256: "a".repeat(64),
    })),
  };
}

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear() {
      map.clear();
    },
    getItem(k) {
      return map.has(k) ? (map.get(k) as string) : null;
    },
    key(i) {
      return [...map.keys()][i] ?? null;
    },
    removeItem(k) {
      map.delete(k);
    },
    setItem(k, v) {
      map.set(k, v);
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("postSketchZip + transactional commit", () => {
  it("201 exact: POST once zip content-type; only then local exported", async () => {
    const draft = filledDraft();
    const cat = readyCatalog();
    const pkg = prepareSketchPackage(draft, cat);
    const storage = memoryStorage();
    // not exported before POST
    expect(draft.exported).toBe(false);

    const posts: Array<{ url: string; type: string | null }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        posts.push({
          url,
          type: (init?.headers as Record<string, string>)?.["Content-Type"] ?? null,
        });
        expect(init?.method).toBe("POST");
        expect(init?.body).toBeInstanceOf(Blob);
        return new Response(JSON.stringify(detail201(draft)), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    const zip = await buildSketchZipBlob(pkg);
    const expected = expectedFromPackage(pkg, draft);
    await postSketchZip(zip, expected);
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toMatch(/\/api\/v1\/sketches$/);
    expect(posts[0].type).toBe("application/zip");

    // still not committed until commitLocalSketchExport
    expect(readSketchStore(storage).packages[draft.sketchId]).toBeUndefined();

    const { draft: exported } = commitLocalSketchExport(
      draft,
      pkg,
      storage,
    );
    expect(exported.exported).toBe(true);
    expect(readSketchStore(storage).packages[draft.sketchId]).toBeTruthy();
  });

  it("request ZIP and rebuild ZIP share package source (six members)", async () => {
    const draft = filledDraft();
    const pkg = prepareSketchPackage(draft, readyCatalog());
    const a = await buildSketchZipBlob(pkg);
    const b = await buildSketchZipBlob(pkg);
    const JSZip = (await import("jszip")).default;
    const za = await JSZip.loadAsync(a);
    const zb = await JSZip.loadAsync(b);
    const namesA = Object.keys(za.files).filter((n) => !n.endsWith("/")).sort();
    const namesB = Object.keys(zb.files).filter((n) => !n.endsWith("/")).sort();
    expect(namesA).toEqual(namesB);
    expect(namesA).toHaveLength(6);
    expect(namesA.every((n) => n.startsWith(`${draft.sketchId}/`))).toBe(true);
    const metaA = await za.file(`${draft.sketchId}/meta.yaml`)!.async("string");
    const metaB = await zb.file(`${draft.sketchId}/meta.yaml`)!.async("string");
    expect(metaA).toBe(metaB);
    expect(metaA).toContain("instrument: NQ");
  });

  it("malformed 201 / wrong identity → no commit (caller responsibility)", async () => {
    const draft = filledDraft();
    const pkg = prepareSketchPackage(draft, readyCatalog());
    const expected = expectedFromPackage(pkg, draft);
    const zip = await buildSketchZipBlob(pkg);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("not-json", { status: 201 })),
    );
    await expect(postSketchZip(zip, expected)).rejects.toThrow(/JSON/);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify(detail201(draft, { sketch_id: "sketch-20990101-99" })),
          { status: 201 },
        ),
      ),
    );
    await expect(postSketchZip(zip, expected)).rejects.toBeTruthy();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify(detail201(draft, { instrument: "YM" })),
          { status: 201 },
        ),
      ),
    );
    await expect(postSketchZip(zip, expected)).rejects.toThrow(/instrument/);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify(
            detail201(draft, { asset_class: "commodity_futures" }),
          ),
          { status: 201 },
        ),
      ),
    );
    await expect(postSketchZip(zip, expected)).rejects.toThrow(/asset_class/);
  });

  it("409 is not success", async () => {
    const draft = filledDraft();
    const pkg = prepareSketchPackage(draft, readyCatalog());
    const zip = await buildSketchZipBlob(pkg);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("exists", { status: 409 })),
    );
    await expect(
      postSketchZip(zip, expectedFromPackage(pkg, draft)),
    ).rejects.toMatchObject({ status: 409 });
    expect(humanizeSketchPostFailure(409, "")).toMatch(/複製成新草圖/);
  });

  it("422 / 500 human messages", () => {
    expect(humanizeSketchPostFailure(422, "field x bad")).toMatch(/field x bad/);
    expect(humanizeSketchPostFailure(500, "")).toMatch(/暫時寫唔入/);
  });

  it("assertSketchPostMatchesPackage rejects wrong origin", () => {
    const draft = filledDraft();
    const d = detail201(draft);
    expect(() =>
      assertSketchPostMatchesPackage(d, {
        origin: "journal-app",
        sketchId: draft.sketchId,
        instrument: "NQ",
        assetClass: "equity_index_futures",
        chartFileNames: d.meta.charts.map((c) => c.file),
      }),
    ).toThrow(/origin/);
  });
});
