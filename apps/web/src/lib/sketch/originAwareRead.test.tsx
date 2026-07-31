import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchSketchDetail,
  parseSketchDetailResponse,
  resolveApiUrl,
  SketchFetchError,
  type SketchDetailChart,
  type SketchDetailResponse,
} from "../../api/client";
import { OwnerSketchPanel } from "../../components/strategies/OwnerSketchPanel";
import {
  extractBasedOnSketch,
  extractBasedOnSketchLineage,
} from "../strategyYaml";
import { SKETCH_APP_ORIGIN, sketchRelativeDir } from "./paths";
import { buildTerminalOpener } from "./terminalOpener";

function validDetail(
  over: Partial<SketchDetailResponse["meta"]> & {
    origin?: "workshop" | "journal-app";
    sketch_id?: string;
  } = {},
): SketchDetailResponse {
  const origin = over.origin ?? "workshop";
  const sketch_id = over.sketch_id ?? "sketch-20260725-01";
  const charts: SketchDetailChart[] = [
    {
      file: "chart-D.png",
      timeframe: "D",
      role: "bias",
      indicators_shown: ["ema90"],
      owner_view: "日線升勢",
    },
    {
      file: "chart-1H.png",
      timeframe: "1H",
      role: "mid",
      indicators_shown: ["ema18"],
      owner_view: "1H 回踩",
    },
    {
      file: "chart-30m.png",
      timeframe: "30m",
      role: "auxiliary",
      indicators_shown: [],
      owner_view: "30m 輔助",
    },
    {
      file: "chart-5m.png",
      timeframe: "5m",
      role: "entry",
      indicators_shown: ["ema18"],
      owner_view: "5m 突破",
    },
  ];
  return {
    schema: "sketch_detail.v1",
    meta: {
      schema: "sketch.v1",
      sketch_id,
      kind: "strategy",
      origin,
      chart_source: "screenshot",
      instructions_template: "instructions.v1",
      instrument: "NQ",
      asset_class: "equity_index_futures",
      created: "2026-07-25",
      title: "測試草圖標題",
      rationale: "整體理據文",
      charts,
      ...over,
    },
    instructions_markdown: "# instructions",
    images: charts.map((c) => ({
      file: c.file,
      url: `/api/v1/sketches/${origin}/${sketch_id}/images/${c.file}`,
      content_type: "image/png",
      byte_count: 12,
      sha256: "a".repeat(64),
    })),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("D1 lineage parse", () => {
  it("complete workshop + id", () => {
    const yaml = `
schema: strategy.v1
meta:
  name: t
  based_on_sketch: sketch-20260725-01
  based_on_sketch_origin: workshop
`;
    const lin = extractBasedOnSketchLineage(yaml);
    expect(lin.status).toBe("complete");
    if (lin.status === "complete") {
      expect(lin.origin).toBe("workshop");
      expect(lin.sketchId).toBe("sketch-20260725-01");
    }
  });

  it("missing origin is incomplete — not local-missing", () => {
    const yaml = `
schema: strategy.v1
meta:
  name: t
  based_on_sketch: sketch-20260725-01
`;
    const lin = extractBasedOnSketchLineage(yaml);
    expect(lin.status).toBe("incomplete");
    if (lin.status === "incomplete") {
      expect(lin.reason).toMatch(/origin|溯源/i);
    }
  });

  it("does not use comment-line based_on_sketch via broad regex", () => {
    const yaml = `
schema: strategy.v1
meta:
  name: t
# based_on_sketch: sketch-from-comment
# based_on_sketch_origin: journal-app
`;
    const lin = extractBasedOnSketchLineage(yaml);
    expect(lin.status).toBe("incomplete");
  });

  it("extractBasedOnSketch returns null unless composite complete", () => {
    expect(
      extractBasedOnSketch(`
schema: strategy.v1
meta:
  name: t
  based_on_sketch: sketch-20260725-01
  based_on_sketch_origin: workshop
`),
    ).toBe("sketch-20260725-01");
    expect(
      extractBasedOnSketch(`
schema: strategy.v1
meta:
  name: t
  based_on_sketch: sketch-only-id
`),
    ).toBeNull();
    expect(
      extractBasedOnSketch(`
schema: strategy.v1
meta:
  name: t
  based_on_sketch_origin: workshop
`),
    ).toBeNull();
    expect(
      extractBasedOnSketch(`
schema: strategy.v1
meta:
  name: t
  based_on_sketch: sketch-20260725-99
  based_on_sketch_origin: not-a-real-origin
`),
    ).toBeNull();
    expect(extractBasedOnSketch("not: yaml: [[[")).toBeNull();
  });
});

describe("D2/D3 paths + API client", () => {
  it("repo path is data/sketches/workshop/<id>/", () => {
    expect(sketchRelativeDir("sketch-20260725-01")).toBe(
      "data/sketches/workshop/sketch-20260725-01/",
    );
    expect(SKETCH_APP_ORIGIN).toBe("workshop");
    expect(buildTerminalOpener("sketch-20260725-01")).toContain(
      "data/sketches/workshop/sketch-20260725-01/",
    );
  });

  it("fetchSketchDetail uses origin-aware URL encoding", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      expect(url).toContain("/api/v1/sketches/workshop/sketch-20260725-01");
      return new Response(JSON.stringify(validDetail()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const d = await fetchSketchDetail("workshop", "sketch-20260725-01");
    expect(d.schema).toBe("sketch_detail.v1");
    expect(d.meta.charts).toHaveLength(4);
  });

  it("same id different origin hits exact path", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      expect(url).toContain("/api/v1/sketches/journal-app/sketch-20260725-01");
      expect(url).not.toMatch(/\/workshop\//);
      return new Response(
        JSON.stringify(
          validDetail({ origin: "journal-app", sketch_id: "sketch-20260725-01" }),
        ),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    await fetchSketchDetail("journal-app", "sketch-20260725-01");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("404 vs invalid payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("gone", { status: 404 })),
    );
    await expect(
      fetchSketchDetail("workshop", "sketch-20260725-99"),
    ).rejects.toMatchObject({ status: 404 });

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ schema: "nope" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(
      fetchSketchDetail("workshop", "sketch-20260725-99"),
    ).rejects.toBeInstanceOf(SketchFetchError);
  });

  it("resolveApiUrl keeps origin-aware response urls", () => {
    const rel = "/api/v1/sketches/workshop/s1/images/chart-D.png";
    expect(resolveApiUrl(rel)).toContain(rel);
    expect(resolveApiUrl(rel)).not.toMatch(/\/sketches\/s1\//);
    expect(resolveApiUrl("https://cdn.example/x.png")).toBe(
      "https://cdn.example/x.png",
    );
  });

  it("wrong-origin / wrong-id response is invalid body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify(
            validDetail({
              origin: "journal-app",
              sketch_id: "sketch-20260725-01",
            }),
          ),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    await expect(
      fetchSketchDetail("workshop", "sketch-20260725-01"),
    ).rejects.toMatchObject({ status: 0 });

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify(
            validDetail({
              origin: "workshop",
              sketch_id: "sketch-20260725-97",
            }),
          ),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    await expect(
      fetchSketchDetail("workshop", "sketch-20260725-01"),
    ).rejects.toMatchObject({ status: 0 });
  });

  it("chart/image mismatch fail-closed", () => {
    const ok = validDetail();
    const missingImage = {
      ...ok,
      images: ok.images.slice(0, 3),
    };
    expect(
      parseSketchDetailResponse(missingImage, "workshop", "sketch-20260725-01"),
    ).toBeNull();

    const extraImage = {
      ...ok,
      images: [
        ...ok.images,
        {
          file: "chart-extra.png",
          url: "/x",
          content_type: "image/png",
          byte_count: 1,
          sha256: "c".repeat(64),
        },
      ],
    };
    expect(
      parseSketchDetailResponse(extraImage, "workshop", "sketch-20260725-01"),
    ).toBeNull();

    const badImage = {
      ...ok,
      images: ok.images.map((im, i) =>
        i === 0 ? { file: im.file, url: im.url } : im,
      ),
    };
    expect(
      parseSketchDetailResponse(badImage, "workshop", "sketch-20260725-01"),
    ).toBeNull();
  });

  it("accepts omitted optional chart fields (role/range/etc)", () => {
    const d = validDetail();
    d.meta.charts = d.meta.charts.map((c) => ({
      file: c.file,
      timeframe: c.timeframe,
      indicators_shown: c.indicators_shown,
      owner_view: c.owner_view,
    }));
    expect(
      parseSketchDetailResponse(d, "workshop", "sketch-20260725-01"),
    ).not.toBeNull();
  });

  it("accepts insight package with omitted rationale", () => {
    const d = validDetail({ kind: "insight" });
    delete (d.meta as { rationale?: string }).rationale;
    expect(
      parseSketchDetailResponse(d, "workshop", "sketch-20260725-01"),
    ).not.toBeNull();
  });

  it("rejects explicit null optional fields", () => {
    const d = validDetail();
    (d.meta.charts[0] as { role?: string | null }).role = null;
    expect(
      parseSketchDetailResponse(d, "workshop", "sketch-20260725-01"),
    ).toBeNull();
  });

  it("rejects illegal chart_source / template / instrument / created", () => {
    expect(
      parseSketchDetailResponse(
        validDetail({ chart_source: "photo" as "screenshot" }),
        "workshop",
        "sketch-20260725-01",
      ),
    ).toBeNull();
    expect(
      parseSketchDetailResponse(
        validDetail({
          instructions_template: "other.v1" as "instructions.v1",
        }),
        "workshop",
        "sketch-20260725-01",
      ),
    ).toBeNull();
    const noInst = validDetail();
    delete (noInst.meta as { instrument?: string }).instrument;
    // instrument-only missing while class present → invalid (pair broken)
    expect(
      parseSketchDetailResponse(noInst, "workshop", "sketch-20260725-01"),
    ).toBeNull();
    // both omitted → legacy read allowed; parser must NOT insert instrument
    const legacyBoth = validDetail();
    delete (legacyBoth.meta as { instrument?: string }).instrument;
    delete (legacyBoth.meta as { asset_class?: string }).asset_class;
    const beforeKeys = Object.keys(legacyBoth.meta).sort();
    const legacyParsed = parseSketchDetailResponse(
      legacyBoth,
      "workshop",
      "sketch-20260725-01",
    );
    expect(legacyParsed).not.toBeNull();
    expect(
      Object.prototype.hasOwnProperty.call(legacyParsed!.meta, "instrument"),
    ).toBe(false);
    expect(Object.keys(legacyBoth.meta).sort()).toEqual(beforeKeys);
    expect(
      parseSketchDetailResponse(
        validDetail({ created: "2026-07-25T12:00:00Z" }),
        "workshop",
        "sketch-20260725-01",
      ),
    ).toBeNull();
    expect(
      parseSketchDetailResponse(
        validDetail({ created: "2026-02-30" }),
        "workshop",
        "sketch-20260725-01",
      ),
    ).toBeNull();
  });

  it("rejects strategy without rationale and illegal indicators", () => {
    const noRat = validDetail();
    delete (noRat.meta as { rationale?: string }).rationale;
    expect(
      parseSketchDetailResponse(noRat, "workshop", "sketch-20260725-01"),
    ).toBeNull();
    const badTok = validDetail();
    badTok.meta.charts[0].indicators_shown = ["not_a_token"];
    expect(
      parseSketchDetailResponse(badTok, "workshop", "sketch-20260725-01"),
    ).toBeNull();
  });

  it("rejects bad image content_type / sha256", () => {
    const d = validDetail();
    d.images[0] = { ...d.images[0], content_type: "image/jpeg" };
    expect(
      parseSketchDetailResponse(d, "workshop", "sketch-20260725-01"),
    ).toBeNull();
    const d2 = validDetail();
    d2.images[0] = { ...d2.images[0], sha256: "ABCDEF".repeat(10) + "ab" };
    expect(
      parseSketchDetailResponse(d2, "workshop", "sketch-20260725-01"),
    ).toBeNull();
  });
});

describe("D4 OwnerSketchPanel states", () => {
  it("complete lineage renders four views from server package", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify(validDetail()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    render(
      <OwnerSketchPanel
        active
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-01",
        }}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("sketch-ready")).toBeInTheDocument();
    });
    expect(screen.getByTestId("sketch-four-grid").querySelectorAll("article")).toHaveLength(
      4,
    );
    expect(screen.getByText("日線升勢")).toBeInTheDocument();
    expect(screen.getByText("整體理據文")).toBeInTheDocument();
    const img = screen.getAllByRole("img")[0] as HTMLImageElement;
    expect(img.src).toContain(
      "/api/v1/sketches/workshop/sketch-20260725-01/images/",
    );
  });

  it("404 shows 本機缺包 not network wording", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("no", { status: 404 })),
    );
    render(
      <OwnerSketchPanel
        active
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-98",
        }}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("sketch-not-found")).toBeInTheDocument();
    });
    expect(screen.getByTestId("sketch-not-found").textContent).toMatch(
      /本機缺少/,
    );
    expect(screen.queryByTestId("sketch-read-error")).toBeNull();
  });

  it("500 is not 本機缺包", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("err", { status: 500 })),
    );
    render(
      <OwnerSketchPanel
        active
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-99",
        }}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("sketch-read-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("sketch-read-error").textContent).toMatch(
      /暫時讀唔到/,
    );
    expect(screen.getByTestId("sketch-read-error").textContent).not.toMatch(
      /本機缺少/,
    );
  });

  it("stale A does not overwrite B", async () => {
    let resolveA: (v: Response) => void = () => undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("sketch-20260725-91")) {
        return new Promise<Response>((resolve) => {
          resolveA = resolve;
        });
      }
      return new Response(
        JSON.stringify(
          validDetail({ sketch_id: "sketch-20260725-92", title: "BBBB" }),
        ),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(
      <OwnerSketchPanel
        active
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-91",
        }}
      />,
    );
    expect(screen.getByTestId("sketch-loading")).toBeInTheDocument();
    rerender(
      <OwnerSketchPanel
        active
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-92",
        }}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("sketch-ready").textContent).toMatch(/BBBB/);
    });
    // late A arrives
    resolveA(
      new Response(
        JSON.stringify(
          validDetail({ sketch_id: "sketch-20260725-91", title: "AAAA-stale" }),
        ),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByTestId("sketch-ready").textContent).toMatch(/BBBB/);
    expect(screen.getByTestId("sketch-ready").textContent).not.toMatch(
      /AAAA-stale/,
    );
  });

  it("incomplete lineage shows lineage error and zero fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <OwnerSketchPanel
        active
        lineage={{
          status: "incomplete",
          origin: null,
          sketchId: null,
          reason: "缺 origin",
        }}
      />,
    );
    expect(screen.getByTestId("sketch-lineage-error")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("A pending → incomplete → A late success stays lineage error", async () => {
    let resolveA: (v: Response) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Promise<Response>((resolve) => {
          resolveA = resolve;
        }),
      ),
    );
    const { rerender } = render(
      <OwnerSketchPanel
        active
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-91",
        }}
      />,
    );
    expect(screen.getByTestId("sketch-loading")).toBeInTheDocument();
    rerender(
      <OwnerSketchPanel
        active
        lineage={{
          status: "incomplete",
          origin: null,
          sketchId: null,
          reason: "轉咗 incomplete",
        }}
      />,
    );
    expect(screen.getByTestId("sketch-lineage-error")).toBeInTheDocument();
    resolveA(
      new Response(
        JSON.stringify(
          validDetail({ sketch_id: "sketch-20260725-91", title: "LATE-A" }),
        ),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.getByTestId("sketch-lineage-error")).toBeInTheDocument();
    expect(screen.queryByTestId("sketch-ready")).toBeNull();
    expect(screen.queryByText(/LATE-A/)).toBeNull();
  });

  it("A pending → inactive → A late success is not ready", async () => {
    let resolveA: (v: Response) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Promise<Response>((resolve) => {
          resolveA = resolve;
        }),
      ),
    );
    const { rerender } = render(
      <OwnerSketchPanel
        active
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-91",
        }}
      />,
    );
    rerender(
      <OwnerSketchPanel
        active={false}
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-91",
        }}
      />,
    );
    expect(screen.getByText(/未有策略內容可對照/)).toBeInTheDocument();
    resolveA(
      new Response(JSON.stringify(validDetail({ title: "SHOULD-NOT" })), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.queryByTestId("sketch-ready")).toBeNull();
    expect(screen.queryByText(/SHOULD-NOT/)).toBeNull();
  });

  it("unmount before resolve does not throw or leave ready", async () => {
    let resolveA: (v: Response) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Promise<Response>((resolve) => {
          resolveA = resolve;
        }),
      ),
    );
    const { unmount } = render(
      <OwnerSketchPanel
        active
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-91",
        }}
      />,
    );
    unmount();
    resolveA(
      new Response(JSON.stringify(validDetail({ title: "AFTER-UNMOUNT" })), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.queryByText(/AFTER-UNMOUNT/)).toBeNull();
  });

  it("A pending → lineage=null → A late success stays idle", async () => {
    let resolveA: (v: Response) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Promise<Response>((resolve) => {
          resolveA = resolve;
        }),
      ),
    );
    const { rerender } = render(
      <OwnerSketchPanel
        active
        lineage={{
          status: "complete",
          origin: "workshop",
          sketchId: "sketch-20260725-91",
        }}
      />,
    );
    expect(screen.getByTestId("sketch-loading")).toBeInTheDocument();
    rerender(<OwnerSketchPanel active lineage={null} />);
    expect(screen.getByText(/未有策略內容可對照/)).toBeInTheDocument();
    resolveA(
      new Response(
        JSON.stringify(validDetail({ title: "NULL-TRANSITION-A" })),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.queryByTestId("sketch-ready")).toBeNull();
    expect(screen.queryByText(/NULL-TRANSITION-A/)).toBeNull();
    expect(screen.getByText(/未有策略內容可對照/)).toBeInTheDocument();
  });
});
