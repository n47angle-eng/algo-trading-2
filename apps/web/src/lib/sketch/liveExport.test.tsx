/**
 * Mounted SketchTab live export — transactional POST, late response, owner-review.
 * Correction A: PNG/rationale gates, canonical slots, immutable snapshot, late-A truth.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { SketchDetailResponse } from "../../api/client";
import { ownerReviewCoverageBody } from "../catalog/fixtureCatalog";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { StrategiesPage } from "../../pages/StrategiesPage";
import { TINY_PNG_DATA_URL } from "./pngGate";
import { CANONICAL_CHART_FILES } from "./paths";
import {
  createEmptyDraft,
  listDrafts,
  readSketchStore,
  writeSketchStore,
} from "./store";

function coverageOk() {
  return new Response(JSON.stringify(ownerReviewCoverageBody()), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function detailFor(
  sketchId: string,
  title: string,
): SketchDetailResponse {
  const charts = [
    { file: "chart-D.png", timeframe: "D", role: "bias" as const, indicators_shown: ["ema18"], owner_view: "v1" },
    { file: "chart-1H.png", timeframe: "1H", role: "mid" as const, indicators_shown: ["ema18"], owner_view: "v2" },
    { file: "chart-30m.png", timeframe: "30m", role: "auxiliary" as const, indicators_shown: ["ema18"], owner_view: "v3" },
    { file: "chart-5m.png", timeframe: "5m", role: "entry" as const, indicators_shown: ["ema18"], owner_view: "v4" },
  ];
  return {
    schema: "sketch_detail.v1",
    meta: {
      schema: "sketch.v1",
      sketch_id: sketchId,
      kind: "strategy",
      origin: "workshop",
      chart_source: "screenshot",
      instructions_template: "instructions.v1",
      instrument: "NQ",
      asset_class: "equity_index_futures",
      created: "2026-07-27",
      title,
      rationale: "r",
      charts,
    },
    instructions_markdown: "# x",
    images: charts.map((c) => ({
      file: c.file,
      url: `/api/v1/sketches/workshop/${sketchId}/images/${c.file}`,
      content_type: "image/png",
      byte_count: 10,
      sha256: "a".repeat(64),
    })),
  };
}

function seedReadyDraft(
  title: string,
  existingIds: string[] = [],
  over: Partial<ReturnType<typeof createEmptyDraft>> = {},
) {
  const d = createEmptyDraft(existingIds);
  d.title = title;
  d.rationale = "seed rationale for export";
  d.instrument = "NQ";
  d.assetClass = "equity_index_futures";
  d.instrumentLocked = true;
  d.charts = d.charts.map((c, i) => ({
    ...c,
    ownerView: `${title} view ${i}`,
    imageDataUrl: TINY_PNG_DATA_URL,
    imageFileName: CANONICAL_CHART_FILES[i],
  }));
  Object.assign(d, over);
  return d;
}

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function fillSketchForExport(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => {
    expect(screen.getByTestId("instrument-select")).toBeInTheDocument();
  });
  await user.selectOptions(screen.getByTestId("instrument-select"), "NQ");
  fireEvent.change(screen.getByRole("textbox", { name: "草圖標題" }), {
    target: { value: "Live export title" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "整體理據" }), {
    target: { value: "Live export rationale" },
  });
  const views = screen.getAllByPlaceholderText(/圖你見到咩/);
  for (const [i, el] of views.entries()) {
    fireEvent.change(el, { target: { value: `判斷 ${i + 1}` } });
  }
  // Attach four real tiny PNGs via file inputs (accept image/png)
  const pngBytes = Uint8Array.from(
    atob(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    ),
    (c) => c.charCodeAt(0),
  );
  const fileInputs = document.querySelectorAll<HTMLInputElement>(
    'input[type="file"][accept*="png"]',
  );
  expect(fileInputs.length).toBe(4);
  for (let i = 0; i < 4; i++) {
    const file = new File([pngBytes], `slot-${i}.png`, { type: "image/png" });
    await user.upload(fileInputs[i], file);
  }
  // FileReader is async — wait until export gate sees all four PNGs
  await waitFor(() => {
    expect(screen.getByTestId("sketch-export")).toBeEnabled();
  });
}

function renderNormal() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/strategies"]}>
        <StrategiesPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("Live Seam A SketchTab", () => {
  it("blocks export without images or rationale (POST=0)", async () => {
    const user = userEvent.setup();
    let postCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postCount += 1;
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderNormal();
    await waitFor(() => {
      expect(screen.getByTestId("instrument-select")).toBeInTheDocument();
    });
    await user.selectOptions(screen.getByTestId("instrument-select"), "NQ");
    fireEvent.change(screen.getByRole("textbox", { name: "草圖標題" }), {
      target: { value: "No images" },
    });
    for (const [i, el] of screen.getAllByPlaceholderText(/圖你見到咩/).entries()) {
      fireEvent.change(el, { target: { value: `v${i}` } });
    }
    const btn = screen.getByTestId("sketch-export");
    expect(btn).toBeDisabled();
    expect(screen.getByLabelText("匯出阻擋原因").textContent).toMatch(
      /未上載|理據/,
    );
    expect(postCount).toBe(0);
    expect(listDrafts(localStorage)[0]?.exported).not.toBe(true);
  });

  it("normal exact 201: one POST then dialog persisted; draft exported", async () => {
    const user = userEvent.setup();
    let postCount = 0;
    let postedZip: Blob | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && (init?.method ?? "GET") === "POST") {
          postCount += 1;
          postedZip = init?.body as Blob;
          expect(postedZip).toBeInstanceOf(Blob);
          expect(init?.headers).toMatchObject({
            "Content-Type": "application/zip",
          });
          const snap = readSketchStore(localStorage);
          const id = snap.activeId!;
          return new Response(JSON.stringify(detailFor(id, "Live export title")), {
            status: 201,
          });
        }
        return new Response("no", { status: 404 });
      }),
    );

    renderNormal();
    await fillSketchForExport(user);
    const btn = screen.getByTestId("sketch-export");
    expect(btn).toBeEnabled();
    await user.click(btn);

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
    expect(postCount).toBe(1);
    expect(screen.getByTestId("export-dialog-persisted")).toBeInTheDocument();
    const snap = readSketchStore(localStorage);
    const d = snap.drafts.find((x) => x.sketchId === snap.activeId);
    expect(d?.exported).toBe(true);
    expect(snap.packages[d!.sketchId]).toBeTruthy();
    // ZIP has six members with canonical chart names
    const JSZip = (await import("jszip")).default;
    const zip = await JSZip.loadAsync(postedZip!);
    const root = d!.sketchId;
    for (const name of CANONICAL_CHART_FILES) {
      expect(zip.file(`${root}/${name}`)).toBeTruthy();
    }
    expect(zip.file(`${root}/meta.yaml`)).toBeTruthy();
    expect(zip.file(`${root}/INSTRUCTIONS.md`)).toBeTruthy();
  });

  it("422 leaves draft unexported and no dialog", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          return new Response("instrument missing", { status: 422 });
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderNormal();
    await fillSketchForExport(user);
    await user.click(screen.getByTestId("sketch-export"));
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/驗證唔通過|422/);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    const d = listDrafts(localStorage)[0];
    expect(d.exported).toBe(false);
  });

  it("409 is not success", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          return new Response("dup", { status: 409 });
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderNormal();
    await fillSketchForExport(user);
    await user.click(screen.getByTestId("sketch-export"));
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/複製成新草圖/);
    });
    expect(listDrafts(localStorage)[0].exported).toBe(false);
  });

  it("415 / 500 / network / malformed 201 leave zero local success", async () => {
    const cases: Array<{
      name: string;
      respond: () => Promise<Response> | Response;
      errMatch: RegExp;
    }> = [
      {
        name: "415",
        respond: () => new Response("no", { status: 415 }),
        errMatch: /Content-Type|415|zip/i,
      },
      {
        name: "500",
        respond: () => new Response("boom", { status: 500 }),
        errMatch: /500|寫唔入/,
      },
      {
        name: "network",
        respond: () => {
          throw new TypeError("Failed to fetch");
        },
        errMatch: /結果未知|網絡/,
      },
      {
        name: "malformed201",
        respond: () => new Response("{not-json", { status: 201 }),
        errMatch: /JSON|exact|未標/,
      },
    ];

    for (const c of cases) {
      localStorage.clear();
      const user = userEvent.setup();
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = String(input);
          if (url.includes("/api/v1/data/coverage")) return coverageOk();
          if (url.includes("/api/v1/sketches") && init?.method === "POST") {
            return c.respond();
          }
          return new Response("no", { status: 404 });
        }),
      );
      const { unmount } = renderNormal();
      await fillSketchForExport(user);
      await user.click(screen.getByTestId("sketch-export"));
      await waitFor(() => {
        expect(screen.getByRole("alert").textContent).toMatch(c.errMatch);
      });
      expect(listDrafts(localStorage)[0].exported).toBe(false);
      expect(screen.queryByRole("dialog")).toBeNull();
      unmount();
      vi.unstubAllGlobals();
    }
  });

  it("pending double click: exact one POST", async () => {
    const user = userEvent.setup();
    let postCount = 0;
    let resolvePost: (r: Response) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postCount += 1;
          return new Promise<Response>((res) => {
            resolvePost = res;
          });
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderNormal();
    await fillSketchForExport(user);
    const btn = screen.getByTestId("sketch-export");
    // Sync triple-click — only the pending ref must collapse these to one POST.
    fireEvent.click(btn);
    fireEvent.click(btn);
    fireEvent.click(btn);
    // Flush zip→POST microtasks; waitFor(toBe(1)) alone can pass mid-flight.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 80));
    });
    expect(postCount).toBe(1);
    const id = readSketchStore(localStorage).activeId!;
    await act(async () => {
      resolvePost(
        new Response(JSON.stringify(detailFor(id, "Live export title")), {
          status: 201,
        }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
    expect(postCount).toBe(1);
    void user;
  });

  it("5-chart draft: export disabled, POST=0, local export=0", async () => {
    let postCount = 0;
    const base = seedReadyDraft("Five charts");
    const five = {
      ...base,
      charts: [
        ...base.charts,
        {
          ...base.charts[0],
          slotId: "slot-5-corrupt",
          timeframe: "1m",
          ownerView: "extra",
          imageDataUrl: TINY_PNG_DATA_URL,
          imageFileName: "extra.png",
        },
      ],
    };
    writeSketchStore({
      schema: "sketch_store.v1",
      drafts: [five],
      activeId: five.sketchId,
      packages: {},
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postCount += 1;
          return new Response("nope", { status: 500 });
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderNormal();
    await waitFor(() => {
      expect(screen.getByDisplayValue("Five charts")).toBeInTheDocument();
    });
    const btn = screen.getByTestId("sketch-export");
    expect(btn).toBeDisabled();
    expect(screen.getByLabelText("匯出阻擋原因").textContent).toMatch(
      /預期 4 格.*實際 5/,
    );
    fireEvent.click(btn);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(postCount).toBe(0);
    const snap = readSketchStore(localStorage);
    expect(snap.drafts.find((d) => d.sketchId === five.sketchId)?.exported).toBe(
      false,
    );
    expect(snap.packages[five.sketchId]).toBeUndefined();
  });

  it("A pending → switch B → late A 201: A exported, B active unexported, no A dialog", async () => {
    const user = userEvent.setup();
    let resolveA: (r: Response) => void = () => undefined;
    const posts: string[] = [];

    const a = seedReadyDraft("Draft A");
    const b = seedReadyDraft("Draft B", [a.sketchId]);
    writeSketchStore({
      schema: "sketch_store.v1",
      drafts: [a, b],
      activeId: a.sketchId,
      packages: {},
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          posts.push("post");
          if (posts.length === 1) {
            return new Promise<Response>((res) => {
              resolveA = res;
            });
          }
          return new Response(JSON.stringify(detailFor(b.sketchId, "Draft B")), {
            status: 201,
          });
        }
        return new Response("no", { status: 404 });
      }),
    );

    renderNormal();
    await waitFor(() => {
      expect(screen.getByDisplayValue("Draft A")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("sketch-export"));
    await waitFor(() => {
      expect(posts.length).toBe(1);
    });

    await user.click(screen.getByText(new RegExp(b.sketchId)));
    await waitFor(() => {
      expect(screen.getByDisplayValue("Draft B")).toBeInTheDocument();
    });

    await act(async () => {
      resolveA(
        new Response(JSON.stringify(detailFor(a.sketchId, "Draft A")), {
          status: 201,
        }),
      );
    });

    await waitFor(() => {
      const snap = readSketchStore(localStorage);
      const aDraft = snap.drafts.find((d) => d.sketchId === a.sketchId)!;
      expect(aDraft.exported).toBe(true);
      expect(snap.packages[a.sketchId]).toBeTruthy();
    });

    const snap = readSketchStore(localStorage);
    const bDraft = snap.drafts.find((d) => d.sketchId === b.sketchId)!;
    expect(bDraft.exported).toBe(false);
    expect(snap.activeId).toBe(b.sketchId);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByDisplayValue("Draft B")).toBeInTheDocument();
  });

  it("A pending → B → A 201 + local commit throw: B intact, background alert with A id", async () => {
    const user = userEvent.setup();
    let resolveA: (r: Response) => void = () => undefined;
    let postSeen = false;
    const a = seedReadyDraft("BadgeFail A");
    const b = seedReadyDraft("BadgeFail B", [a.sketchId]);
    writeSketchStore({
      schema: "sketch_store.v1",
      drafts: [a, b],
      activeId: a.sketchId,
      packages: {},
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postSeen = true;
          return new Promise<Response>((res) => {
            resolveA = res;
          });
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderNormal();
    await waitFor(() => {
      expect(screen.getByDisplayValue("BadgeFail A")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("sketch-export"));
    await waitFor(() => {
      expect(postSeen).toBe(true);
    });
    await user.click(screen.getByText(new RegExp(b.sketchId)));
    await waitFor(() => {
      expect(screen.getByDisplayValue("BadgeFail B")).toBeInTheDocument();
    });

    // Quota failure only after A 201 success path starts local commit
    const origSet = Storage.prototype.setItem;
    let failOnce = false;
    Storage.prototype.setItem = function setItemMut(
      this: Storage,
      key: string,
      value: string,
    ) {
      if (
        !failOnce &&
        key.includes("sketch") &&
        value.includes(a.sketchId) &&
        value.includes('"exported":true')
      ) {
        failOnce = true;
        throw new DOMException("QuotaExceededError", "QuotaExceededError");
      }
      return origSet.call(this, key, value);
    };

    try {
      await act(async () => {
        resolveA(
          new Response(JSON.stringify(detailFor(a.sketchId, "BadgeFail A")), {
            status: 201,
          }),
        );
      });
      await waitFor(() => {
        expect(screen.getByTestId("sketch-background-alert")).toBeInTheDocument();
      });
      const alert = screen.getByTestId("sketch-background-alert");
      expect(alert).toHaveAttribute("role", "alert");
      expect(alert.textContent).toMatch(
        new RegExp(`${a.origin}/${a.sketchId}`),
      );
      expect(alert.textContent).toMatch(/repo 已寫入/);
      expect(alert.textContent).toMatch(/本地 badge|草稿狀態同步失敗/);
      expect(alert.textContent).toMatch(/唔好用同一 id 盲重試/);
      // B intact
      expect(screen.getByDisplayValue("BadgeFail B")).toBeInTheDocument();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(readSketchStore(localStorage).activeId).toBe(b.sketchId);
      // A not locally exported (commit failed)
      expect(
        readSketchStore(localStorage).drafts.find((d) => d.sketchId === a.sketchId)
          ?.exported,
      ).toBe(false);
    } finally {
      Storage.prototype.setItem = origSet;
    }
  });

  it("A still active → local commit throw keeps foreground honest error", async () => {
    const user = userEvent.setup();
    let resolveA: (r: Response) => void = () => undefined;
    let postSeen = false;
    const a = seedReadyDraft("Active badge fail");
    writeSketchStore({
      schema: "sketch_store.v1",
      drafts: [a],
      activeId: a.sketchId,
      packages: {},
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postSeen = true;
          return new Promise<Response>((res) => {
            resolveA = res;
          });
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderNormal();
    await waitFor(() => {
      expect(screen.getByDisplayValue("Active badge fail")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("sketch-export"));
    await waitFor(() => expect(postSeen).toBe(true));

    const origSet = Storage.prototype.setItem;
    Storage.prototype.setItem = function setItemMut(
      this: Storage,
      key: string,
      value: string,
    ) {
      if (
        key.includes("sketch") &&
        value.includes('"exported":true')
      ) {
        throw new DOMException("QuotaExceededError", "QuotaExceededError");
      }
      return origSet.call(this, key, value);
    };
    try {
      await act(async () => {
        resolveA(
          new Response(
            JSON.stringify(detailFor(a.sketchId, "Active badge fail")),
            { status: 201 },
          ),
        );
      });
      await waitFor(() => {
        expect(screen.getByRole("alert").textContent).toMatch(/repo 已寫入/);
      });
      expect(screen.queryByTestId("sketch-background-alert")).toBeNull();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(
        readSketchStore(localStorage).drafts.find((d) => d.sketchId === a.sketchId)
          ?.exported,
      ).toBe(false);
    } finally {
      Storage.prototype.setItem = origSet;
    }
  });

  it("A pending → unmount → A 201 local commit throw: no unmounted state update", async () => {
    const user = userEvent.setup();
    let resolveA: (r: Response) => void = () => undefined;
    let postSeen = false;
    const a = seedReadyDraft("Unmount badge fail");
    writeSketchStore({
      schema: "sketch_store.v1",
      drafts: [a],
      activeId: a.sketchId,
      packages: {},
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postSeen = true;
          return new Promise<Response>((res) => {
            resolveA = res;
          });
        }
        return new Response("no", { status: 404 });
      }),
    );
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { unmount } = renderNormal();
    await waitFor(() => {
      expect(screen.getByDisplayValue("Unmount badge fail")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("sketch-export"));
    await waitFor(() => expect(postSeen).toBe(true));
    unmount();

    const origSet = Storage.prototype.setItem;
    Storage.prototype.setItem = function setItemMut(
      this: Storage,
      key: string,
      value: string,
    ) {
      if (key.includes("sketch") && value.includes('"exported":true')) {
        throw new DOMException("QuotaExceededError", "QuotaExceededError");
      }
      return origSet.call(this, key, value);
    };
    try {
      await act(async () => {
        resolveA(
          new Response(
            JSON.stringify(detailFor(a.sketchId, "Unmount badge fail")),
            { status: 201 },
          ),
        );
        await new Promise((r) => setTimeout(r, 40));
      });
      // No dialog / no throw to test runner
      expect(screen.queryByRole("dialog")).toBeNull();
      const reactUnmountNoise = errSpy.mock.calls.some((args) =>
        String(args[0] ?? "").includes("unmounted"),
      );
      expect(reactUnmountNoise).toBe(false);
    } finally {
      Storage.prototype.setItem = origSet;
      errSpy.mockRestore();
    }
  });

  it("A pending → unmount → A 201: store A exported, no throw", async () => {
    const user = userEvent.setup();
    let resolveA: (r: Response) => void = () => undefined;
    let postSeen = false;
    const a = seedReadyDraft("Unmount A");
    writeSketchStore({
      schema: "sketch_store.v1",
      drafts: [a],
      activeId: a.sketchId,
      packages: {},
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postSeen = true;
          return new Promise<Response>((res) => {
            resolveA = res;
          });
        }
        return new Response("no", { status: 404 });
      }),
    );
    const { unmount } = renderNormal();
    await waitFor(() => {
      expect(screen.getByDisplayValue("Unmount A")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("sketch-export"));
    await waitFor(() => {
      expect(postSeen).toBe(true);
      expect(screen.getByTestId("sketch-export").textContent).toMatch(/匯出中/);
    });
    unmount();
    await act(async () => {
      resolveA(
        new Response(JSON.stringify(detailFor(a.sketchId, "Unmount A")), {
          status: 201,
        }),
      );
    });
    await waitFor(() => {
      const snap = readSketchStore(localStorage);
      expect(snap.drafts.find((d) => d.sketchId === a.sketchId)?.exported).toBe(
        true,
      );
      expect(snap.packages[a.sketchId]).toBeTruthy();
    });
  });

  it("A pending edit does not lock new text into package (snapshot)", async () => {
    const user = userEvent.setup();
    let resolveA: (r: Response) => void = () => undefined;
    let postSeen = false;
    const a = seedReadyDraft("Snap A");
    a.rationale = "ORIGINAL_RATIONALE";
    writeSketchStore({
      schema: "sketch_store.v1",
      drafts: [a],
      activeId: a.sketchId,
      packages: {},
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postSeen = true;
          return new Promise<Response>((res) => {
            resolveA = res;
          });
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderNormal();
    await waitFor(() => {
      expect(screen.getByDisplayValue("Snap A")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("sketch-export"));
    // Wait for real POST (ZIP build is async; 匯出中 lights earlier)
    await waitFor(() => {
      expect(postSeen).toBe(true);
    });
    // Edit while pending — must not rebind in-flight package
    fireEvent.change(screen.getByRole("textbox", { name: "整體理據" }), {
      target: { value: "EDITED_WHILE_PENDING" },
    });
    await act(async () => {
      resolveA(
        new Response(JSON.stringify(detailFor(a.sketchId, "Snap A")), {
          status: 201,
        }),
      );
    });
    await waitFor(() => {
      const snap = readSketchStore(localStorage);
      expect(snap.drafts.find((d) => d.sketchId === a.sketchId)?.exported).toBe(
        true,
      );
      expect(snap.packages[a.sketchId]).toBeTruthy();
    });
    const snap = readSketchStore(localStorage);
    const pkg = snap.packages[a.sketchId];
    expect(pkg.metaYaml).toContain("ORIGINAL_RATIONALE");
    expect(pkg.metaYaml).not.toContain("EDITED_WHILE_PENDING");
    // Committed draft locks snapshot, not mid-flight edit
    expect(snap.drafts.find((d) => d.sketchId === a.sketchId)?.rationale).toBe(
      "ORIGINAL_RATIONALE",
    );
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("editable duplicate timeframes still use canonical ZIP member names", async () => {
    const user = userEvent.setup();
    let postedZip: Blob | null = null;
    const a = seedReadyDraft("TF dup");
    a.charts[0] = { ...a.charts[0], timeframe: "15m" };
    a.charts[1] = { ...a.charts[1], timeframe: "15m" };
    writeSketchStore({
      schema: "sketch_store.v1",
      drafts: [a],
      activeId: a.sketchId,
      packages: {},
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) return coverageOk();
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postedZip = init?.body as Blob;
          return new Response(JSON.stringify(detailFor(a.sketchId, "TF dup")), {
            status: 201,
          });
        }
        return new Response("no", { status: 404 });
      }),
    );
    renderNormal();
    await waitFor(() => {
      expect(screen.getByDisplayValue("TF dup")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("sketch-export"));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
    const JSZip = (await import("jszip")).default;
    const zip = await JSZip.loadAsync(postedZip!);
    const names = Object.keys(zip.files)
      .filter((n) => n.endsWith(".png"))
      .map((n) => n.split("/").pop());
    expect(names.sort()).toEqual([...CANONICAL_CHART_FILES].sort());
    expect(names).not.toContain("chart-15m.png");
    const meta = await zip.file(`${a.sketchId}/meta.yaml`)!.async("string");
    expect(meta.match(/timeframe: 15m/g)?.length).toBeGreaterThanOrEqual(2);
    expect(meta).toContain("file: chart-D.png");
  });

  it("owner-review export: zero POST, local package, ready fixture", async () => {
    const user = userEvent.setup();
    let postCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/sketches") && init?.method === "POST") {
          postCount += 1;
        }
        return new Response("no", { status: 599 });
      }),
    );
    render(
      <ThemeProvider>
        <MemoryRouter
          initialEntries={["/strategies?scenario=owner-review&tab=sketch"]}
        >
          <StrategiesPage />
        </MemoryRouter>
      </ThemeProvider>,
    );
    await user.click(screen.getByTestId("fixture-export_ready"));
    await waitFor(() => {
      expect(screen.getByTestId("sketch-export")).toBeEnabled();
    });
    await user.click(screen.getByTestId("sketch-export"));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
    expect(postCount).toBe(0);
    expect(screen.getByTestId("export-dialog-owner-review")).toBeInTheDocument();
  });
});
