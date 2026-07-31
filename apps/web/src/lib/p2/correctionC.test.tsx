/**
 * Correction C — D21 exact parser (no normalize/mutate) + D22 mounted Insight paths.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import {
  parseSketchDetailResponse,
  SketchFetchError,
  type SketchDetailResponse,
} from "../../api/client";
import * as insightStore from "../insightStore";
import { INSIGHT_STORAGE_KEY, listInsights } from "../insightStore";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { StrategiesPage } from "../../pages/StrategiesPage";
import { SKETCH_STORAGE_KEY } from "../sketch/types";
import { createEmptyDraft, saveDraft } from "../sketch/store";
import { readP2LocalStorageBytes } from "../storage/memoryStorage";
import { sketchRefFromLoadState } from "../strategy/universe";
import {
  sketchLookupTestHooks,
} from "./WorkbenchContext";

function baseMeta(over: Record<string, unknown> = {}) {
  return {
    schema: "sketch.v1",
    sketch_id: "sketch-20260725-01",
    kind: "strategy",
    origin: "workshop",
    chart_source: "screenshot",
    instructions_template: "instructions.v1",
    instrument: "NQ",
    asset_class: "equity_index_futures",
    created: "2026-07-25",
    title: "t",
    rationale: "r",
    charts: [
      {
        file: "chart-D.png",
        timeframe: "D",
        role: "bias",
        indicators_shown: ["ema18"],
        owner_view: "v",
      },
      {
        file: "chart-1H.png",
        timeframe: "1H",
        role: "mid",
        indicators_shown: ["ema18"],
        owner_view: "v",
      },
      {
        file: "chart-30m.png",
        timeframe: "30m",
        role: "auxiliary",
        indicators_shown: ["ema18"],
        owner_view: "v",
      },
      {
        file: "chart-5m.png",
        timeframe: "5m",
        role: "entry",
        indicators_shown: ["ema18"],
        owner_view: "v",
      },
    ],
    ...over,
  };
}

function detailBody(metaOver: Record<string, unknown> = {}): SketchDetailResponse {
  const meta = baseMeta(metaOver);
  return {
    schema: "sketch_detail.v1",
    meta: meta as SketchDetailResponse["meta"],
    instructions_markdown: "# x",
    images: (meta.charts as Array<{ file: string }>).map((c) => ({
      file: c.file,
      url: "/x",
      content_type: "image/png",
      byte_count: 1,
      sha256: "a".repeat(64),
    })),
  };
}

function insightYaml(over: {
  id?: string;
  origin?: string;
  sketchId?: string;
  sketchOrigin?: string;
  instrument?: string;
  assetClass?: string;
}): string {
  return `schema: insight.v1
insight_id: ${over.id ?? "insight-c-01"}
origin: ${over.origin ?? "workshop"}
version: 1
based_on_sketch: ${over.sketchId ?? "sketch-20260727-01"}
based_on_sketch_origin: ${over.sketchOrigin ?? "workshop"}
instrument: ${over.instrument ?? "NQ"}
asset_class: ${over.assetClass ?? "equity_index_futures"}
title: correction-c
condition:
  type: x
  suggested_params: {}
measurement:
  tag: t
  hypothesis: h
validation_status: unverified
`;
}

afterEach(() => {
  localStorage.removeItem(SKETCH_STORAGE_KEY);
  localStorage.removeItem(INSIGHT_STORAGE_KEY);
  sketchLookupTestHooks.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("D21 exact-value parser (no normalize / no mutate)", () => {
  it("canonical pair passes with exact original values", () => {
    const body = detailBody();
    const snap = structuredClone(body);
    const p = parseSketchDetailResponse(
      body,
      "workshop",
      "sketch-20260725-01",
    );
    expect(p).not.toBeNull();
    expect(p!.meta.instrument).toBe("NQ");
    expect(p!.meta.asset_class).toBe("equity_index_futures");
    expect(body).toEqual(snap);
  });

  it("instrument left/right whitespace fails", () => {
    expect(
      parseSketchDetailResponse(
        detailBody({ instrument: " NQ" }),
        "workshop",
        "sketch-20260725-01",
      ),
    ).toBeNull();
    expect(
      parseSketchDetailResponse(
        detailBody({ instrument: "NQ " }),
        "workshop",
        "sketch-20260725-01",
      ),
    ).toBeNull();
  });

  it("asset_class left/right whitespace fails", () => {
    expect(
      parseSketchDetailResponse(
        detailBody({ asset_class: " equity_index_futures" }),
        "workshop",
        "sketch-20260725-01",
      ),
    ).toBeNull();
    expect(
      parseSketchDetailResponse(
        detailBody({ asset_class: "equity_index_futures " }),
        "workshop",
        "sketch-20260725-01",
      ),
    ).toBeNull();
  });

  it("legacy both-omitted: no instrument key inserted on input or output", () => {
    const body = detailBody();
    delete (body.meta as { instrument?: string }).instrument;
    delete (body.meta as { asset_class?: string }).asset_class;
    const snap = structuredClone(body);
    Object.freeze(body.meta);
    Object.freeze(body);
    const p = parseSketchDetailResponse(
      body,
      "workshop",
      "sketch-20260725-01",
    );
    expect(p).not.toBeNull();
    expect(body).toEqual(snap);
    expect(
      Object.prototype.hasOwnProperty.call(p!.meta, "instrument"),
    ).toBe(false);
    expect(
      Object.prototype.hasOwnProperty.call(p!.meta, "asset_class"),
    ).toBe(false);
    expect(sketchRefFromLoadState({ kind: "ready", detail: p! }).kind).toBe(
      "legacy_incomplete",
    );
  });

  it("frozen current payload is not mutated", () => {
    const body = detailBody();
    Object.freeze(body.meta);
    Object.freeze(body.images);
    Object.freeze(body);
    const p = parseSketchDetailResponse(
      body,
      "workshop",
      "sketch-20260725-01",
    );
    expect(p).not.toBeNull();
    expect(p!.meta.instrument).toBe("NQ");
  });
});

function mockNoP2BusinessFetch() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/api/v1/ib/") || url.includes("/api/v1/system/")) {
        return new Response("{}", { status: 200 });
      }
      return new Response("unexpected P2 fetch " + url, { status: 599 });
    }),
  );
  return {
    p2Count: () =>
      calls.filter(
        (u) =>
          u.includes("/api/v1/data/coverage") ||
          u.includes("/api/v1/sketches/") ||
          u.includes("/api/v1/strategies"),
      ).length,
  };
}

function renderOwnerReview(tab = "sketch") {
  return render(
    <ThemeProvider>
      <MemoryRouter
        initialEntries={[`/strategies?scenario=owner-review&tab=${tab}`]}
      >
        <StrategiesPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

async function goInsightWithFixture(
  user: ReturnType<typeof userEvent.setup>,
  fixtureTestId: string,
) {
  await user.click(screen.getByTestId(fixtureTestId));
  await user.click(screen.getByRole("tab", { name: /④ 市場洞察/ }));
  await waitFor(() => {
    expect(
      screen.getByRole("textbox", { name: /insight.v1 YAML/ }),
    ).toBeInTheDocument();
  });
}

function pasteInsight(yaml: string) {
  const box = screen.getByRole("textbox", { name: /insight.v1 YAML/ });
  fireEvent.change(box, { target: { value: yaml } });
}

describe("D22 mounted Insight click paths", () => {
  it("1) server ready exact → one write + success notice", async () => {
    const user = userEvent.setup();
    const normal = createEmptyDraft([]);
    normal.title = "SENTINEL_C";
    saveDraft(normal, localStorage);
    const before = readP2LocalStorageBytes(localStorage);
    const fetchProbe = mockNoP2BusinessFetch();
    const spy = vi.spyOn(insightStore, "importInsight");

    renderOwnerReview("sketch");
    await goInsightWithFixture(user, "fixture-valid_nq_ym");
    pasteInsight(
      insightYaml({
        id: "insight-ready-exact",
        sketchId: "sketch-20260727-01",
        sketchOrigin: "workshop",
        instrument: "NQ",
        assetClass: "equity_index_futures",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("insight-import")).not.toBeDisabled();
    });
    await user.click(screen.getByTestId("insight-import"));
    await waitFor(() => {
      expect(screen.getByText(/已入洞察庫 insight-ready-exact/)).toBeInTheDocument();
    });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(listInsights(localStorage)).toHaveLength(0);
    expect(readP2LocalStorageBytes(localStorage)).toEqual(before);
    expect(fetchProbe.p2Count()).toBe(0);
    // UI library shows the record (may appear in notice + table)
    expect(
      screen.getAllByText(/insight-ready-exact/).length,
    ).toBeGreaterThan(0);
  });

  it("2) server class mismatch → 0 write + no success", async () => {
    const user = userEvent.setup();
    mockNoP2BusinessFetch();
    const spy = vi.spyOn(insightStore, "importInsight");
    renderOwnerReview("sketch");
    await goInsightWithFixture(user, "fixture-valid_nq_ym");
    // Sketch is NQ equity; claim commodity
    pasteInsight(
      insightYaml({
        id: "insight-class-mismatch",
        instrument: "NQ",
        assetClass: "commodity_futures",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("insight-import")).not.toBeDisabled();
    });
    await user.click(screen.getByTestId("insight-import"));
    await waitFor(() => {
      expect(screen.queryByText(/已入洞察庫/)).toBeNull();
    });
    expect(spy).not.toHaveBeenCalled();
  });

  it("3) exact 404 → missing warning + catalog valid allows one write", async () => {
    const user = userEvent.setup();
    mockNoP2BusinessFetch();
    const spy = vi.spyOn(insightStore, "importInsight");
    renderOwnerReview("sketch");
    // local_404 fixture: catalog ready, sketch missing for strategy id; use unknown sketch id
    await goInsightWithFixture(user, "fixture-local_404");
    pasteInsight(
      insightYaml({
        id: "insight-404-ok",
        sketchId: "sketch-missing-404",
        sketchOrigin: "workshop",
        instrument: "NQ",
        assetClass: "equity_index_futures",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("insight-sketch-missing")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("insight-import"));
    await waitFor(() => {
      expect(screen.getByText(/已入洞察庫 insight-404-ok/)).toBeInTheDocument();
    });
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("4) 5xx → 0 write + no success (retained)", async () => {
    const user = userEvent.setup();
    mockNoP2BusinessFetch();
    const spy = vi.spyOn(insightStore, "importInsight");
    renderOwnerReview("sketch");
    await goInsightWithFixture(user, "fixture-server_5xx");
    pasteInsight(insightYaml({ id: "insight-5xx-c" }));
    await waitFor(() => {
      expect(screen.getByTestId("insight-sketch-error")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("insight-import"));
    await waitFor(() => {
      expect(screen.queryByText(/已入洞察庫/)).toBeNull();
    });
    expect(spy).not.toHaveBeenCalled();
  });

  it("5) true A pending → B error → late A resolve cannot authorize write", async () => {
    // D24: A must stay pending first (not immediate ready), then switch to B error,
    // then resolve stale A ready — import still write 0.
    const user = userEvent.setup();
    mockNoP2BusinessFetch();
    const spy = vi.spyOn(insightStore, "importInsight");
    renderOwnerReview("sketch");
    await goInsightWithFixture(user, "fixture-valid_nq_ym");

    const sketchId = "sketch-20260727-01";
    // Arm A (workshop) BEFORE paste so deferred wins over fixture map → true pending
    sketchLookupTestHooks.arm("workshop", sketchId);

    pasteInsight(
      insightYaml({
        id: "insight-late-a",
        sketchOrigin: "workshop",
        sketchId,
      }),
    );
    await waitFor(() => {
      // A must be pending (not immediately ready) — import disabled while loading
      expect(screen.getByTestId("insight-sketch-loading")).toBeInTheDocument();
      expect(screen.getByTestId("insight-import")).toBeDisabled();
    });

    // Switch lineage to B while A is still pending
    sketchLookupTestHooks.arm("journal-app", sketchId);
    pasteInsight(
      insightYaml({
        id: "insight-late-b",
        sketchOrigin: "journal-app",
        sketchId,
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("insight-sketch-loading")).toBeInTheDocument();
    });

    // B fails closed
    sketchLookupTestHooks.reject(
      "journal-app",
      sketchId,
      new SketchFetchError(500, "server error"),
    );
    await waitFor(() => {
      expect(screen.getByTestId("insight-sketch-error")).toBeInTheDocument();
    });

    // Late A ready (must not overwrite B / authorize import)
    sketchLookupTestHooks.resolve(
      "workshop",
      sketchId,
      detailBody({
        sketch_id: sketchId,
        origin: "workshop",
        instrument: "NQ",
        asset_class: "equity_index_futures",
        title: "LATE-A-READY",
      }),
    );
    // Stay on B error after late A
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.getByTestId("insight-sketch-error")).toBeInTheDocument();
    expect(screen.queryByText(/LATE-A-READY/)).toBeNull();

    await user.click(screen.getByTestId("insight-import"));
    await waitFor(() => {
      expect(screen.queryByText(/已入洞察庫/)).toBeNull();
    });
    expect(spy).not.toHaveBeenCalled();
  });

  it("6) same sketch id different origin: A ready does not authorize B mismatch", async () => {
    const user = userEvent.setup();
    mockNoP2BusinessFetch();
    const spy = vi.spyOn(insightStore, "importInsight");
    renderOwnerReview("sketch");
    await goInsightWithFixture(user, "fixture-valid_nq_ym");

    // journal-app same id resolves to commodity detail (mismatch vs insight equity)
    sketchLookupTestHooks.arm("journal-app", "sketch-20260727-01");
    pasteInsight(
      insightYaml({
        id: "insight-origin-b",
        sketchOrigin: "journal-app",
        sketchId: "sketch-20260727-01",
        instrument: "NQ",
        assetClass: "equity_index_futures",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("insight-sketch-loading")).toBeInTheDocument();
    });
    sketchLookupTestHooks.resolve(
      "journal-app",
      "sketch-20260727-01",
      detailBody({
        sketch_id: "sketch-20260727-01",
        origin: "journal-app",
        instrument: "GC",
        asset_class: "commodity_futures",
        title: "journal GC",
      }),
    );
    // Wait load ready then import — must fail class/instrument match
    await waitFor(() => {
      expect(screen.queryByTestId("insight-sketch-loading")).toBeNull();
    });
    await user.click(screen.getByTestId("insight-import"));
    await waitFor(() => {
      expect(screen.queryByText(/已入洞察庫/)).toBeNull();
    });
    expect(spy).not.toHaveBeenCalled();
  });
});
