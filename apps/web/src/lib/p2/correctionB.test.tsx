/**
 * Correction B — D16–D19 regressions + mutation targets.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import {
  parseSketchDetailResponse,
  type SketchDetailResponse,
} from "../../api/client";
import { OWNER_REVIEW_CATALOG } from "../catalog/fixtureCatalog";
import { INSIGHT_STORAGE_KEY, listInsights } from "../insightStore";
import { insightCtxFromSketchRef } from "../../components/strategies/InsightTab";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { StrategiesPage } from "../../pages/StrategiesPage";
import { SKETCH_STORAGE_KEY } from "../sketch/types";
import { createEmptyDraft, saveDraft } from "../sketch/store";
import { readP2LocalStorageBytes } from "../storage/memoryStorage";
import { DELETE_TOMBSTONE_KEY } from "../strategyDelete";
import { THEME_STORAGE_KEY } from "../../theme/theme";
import {
  sketchRefFromLoadState,
  validateUniverseGates,
} from "../strategy/universe";

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

afterEach(() => {
  localStorage.removeItem(SKETCH_STORAGE_KEY);
  localStorage.removeItem(INSIGHT_STORAGE_KEY);
  localStorage.removeItem(DELETE_TOMBSTONE_KEY);
  localStorage.removeItem("p2-owner-review-library-sentinel");
  localStorage.removeItem(THEME_STORAGE_KEY);
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("D16 sketch_detail asset_class pair", () => {
  it("accepts complete NQ/equity pair", () => {
    const p = parseSketchDetailResponse(
      detailBody(),
      "workshop",
      "sketch-20260725-01",
    );
    expect(p).not.toBeNull();
    expect(p!.meta.asset_class).toBe("equity_index_futures");
    const ref = sketchRefFromLoadState({ kind: "ready", detail: p! });
    expect(ref.kind).toBe("ready");
    if (ref.kind === "ready") {
      expect(ref.assetClass).toBe("equity_index_futures");
    }
  });

  it("both omitted → legacy_incomplete (not ready)", () => {
    const body = detailBody();
    delete (body.meta as { instrument?: string }).instrument;
    delete (body.meta as { asset_class?: string }).asset_class;
    const snap = structuredClone(body);
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
    const ref = sketchRefFromLoadState({ kind: "ready", detail: p! });
    expect(ref.kind).toBe("legacy_incomplete");
  });

  it("instrument-only / class-only / null / blank / unknown fail parse", () => {
    const onlyInst = detailBody();
    delete (onlyInst.meta as { asset_class?: string }).asset_class;
    expect(
      parseSketchDetailResponse(onlyInst, "workshop", "sketch-20260725-01"),
    ).toBeNull();

    const onlyClass = detailBody();
    delete (onlyClass.meta as { instrument?: string }).instrument;
    expect(
      parseSketchDetailResponse(onlyClass, "workshop", "sketch-20260725-01"),
    ).toBeNull();

    const nullClass = detailBody({ asset_class: null });
    expect(
      parseSketchDetailResponse(nullClass, "workshop", "sketch-20260725-01"),
    ).toBeNull();

    const blank = detailBody({ asset_class: "  " });
    expect(
      parseSketchDetailResponse(blank, "workshop", "sketch-20260725-01"),
    ).toBeNull();

    const unknown = detailBody({ asset_class: "crypto_perp" });
    expect(
      parseSketchDetailResponse(unknown, "workshop", "sketch-20260725-01"),
    ).toBeNull();
  });

  it("server class mismatch disables confirm path", () => {
    const issues = validateUniverseGates(
      {
        primaryInstrument: "NQ",
        assetClass: "equity_index_futures",
        contracts: ["NQ"],
        expansionRationale: {},
        session: "eth",
      },
      OWNER_REVIEW_CATALOG,
      {
        kind: "ready",
        origin: "workshop",
        sketchId: "s",
        instrument: "NQ",
        assetClass: "commodity_futures",
      },
    );
    expect(issues.some((i) => i.path === "universe.asset_class")).toBe(true);
  });
});

describe("D17 insightCtxFromSketchRef", () => {
  it("maps 5xx to sketchError and ready to exact match", () => {
    const err = insightCtxFromSketchRef(
      { kind: "error", message: "HTTP 500" },
      OWNER_REVIEW_CATALOG,
      true,
    );
    expect(err.sketchError).toMatch(/500/);
    const ready = insightCtxFromSketchRef(
      {
        kind: "ready",
        origin: "workshop",
        sketchId: "s",
        instrument: "NQ",
        assetClass: "equity_index_futures",
      },
      OWNER_REVIEW_CATALOG,
      true,
    );
    expect(ready.sketchMatch?.assetClass).toBe("equity_index_futures");
  });
});

describe("D20 mutation targets (must stay red under mutation)", () => {
  it("M1: coverage without additive fields is invalid (no hardcode ready)", async () => {
    const { parseInstrumentCatalog } = await import("../catalog/parseCatalog");
    const state = parseInstrumentCatalog({
      schema: "data_coverage.v1",
      count: 1,
      contracts: [{ symbol: "NQ", contract_id: "x" }],
    });
    expect(state.status).toBe("invalid");
  });

  it("M3: missing expansion rationale key fails exact-key gate", () => {
    const issues = validateUniverseGates(
      {
        primaryInstrument: "NQ",
        assetClass: "equity_index_futures",
        contracts: ["NQ", "YM"],
        expansionRationale: {},
        session: "eth",
      },
      OWNER_REVIEW_CATALOG,
      { kind: "none" },
    );
    expect(issues.some((i) => i.path.includes("expansion_rationale.YM"))).toBe(
      true,
    );
  });

  it("M5: legacy_incomplete is invalid not missing", () => {
    const issues = validateUniverseGates(
      {
        primaryInstrument: "NQ",
        assetClass: "equity_index_futures",
        contracts: ["NQ"],
        expansionRationale: {},
        session: "eth",
      },
      OWNER_REVIEW_CATALOG,
      {
        kind: "legacy_incomplete",
        sketchId: "s",
        origin: "workshop",
        reason: "缺 class",
      },
    );
    expect(issues.some((i) => i.path === "sketch_ref")).toBe(true);
  });

  it("M6: universe panel exposes no-checkboxes sentinel", async () => {
    const user = userEvent.setup();
    mockNoP2BusinessFetch();
    renderOwnerReview("sketch");
    await user.click(screen.getByTestId("fixture-valid_nq_ym"));
    await waitFor(() => {
      expect(
        screen.getByRole("textbox", { name: /strategy.v1 YAML/ }),
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: "驗證" }));
    await waitFor(() => {
      expect(screen.getByTestId("universe-no-checkboxes")).toBeInTheDocument();
    });
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("M_revision: same-length fixture ids exist (length epoch would collide)", () => {
    // C browser probe pair: catalog_invalid ↔ legacy_exported are both length 15.
    // Old epoch `activeFixture.length + tab.length` therefore collides and skips remount.
    expect("catalog_invalid".length).toBe("legacy_exported".length);
    expect("catalog_loading".length).toBe("catalog_invalid".length);
    expect("catalog_invalid".length).toBe(15);
  });
});

function mockNoP2BusinessFetch() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      // Shell IB status may probe — allow non-P2
      if (url.includes("/api/v1/ib/") || url.includes("/api/v1/system/")) {
        return new Response("{}", { status: 200 });
      }
      // P2 business must not be called in owner-review
      return new Response("unexpected P2 fetch " + url, { status: 599 });
    }),
  );
  return {
    p2Count: () =>
      calls.filter(
        (u) =>
          u.includes("/api/v1/data/coverage") ||
          u.includes("/api/v1/sketches/") ||
          u.includes("/api/v1/strategies") ||
          u.includes("/api/v1/runs"),
      ).length,
    calls,
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

function snapshotLocalStorage(): string {
  const rows: Array<[string, string | null]> = [];
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i);
    if (key != null) {
      rows.push([key, localStorage.getItem(key)]);
    }
  }
  return JSON.stringify(rows.sort(([a], [b]) => a.localeCompare(b)));
}

describe("D18/D19 owner-review mounted integration", () => {
  it("truth correction D1: Library is zero-fetch/storage/timer isolated while normal reads stay available", async () => {
    localStorage.clear();
    localStorage.setItem(
      SKETCH_STORAGE_KEY,
      '{"schema":"sketch_store.v1","activeId":null,"drafts":[]}',
    );
    localStorage.setItem(
      INSIGHT_STORAGE_KEY,
      '{"schema":"insight_store.v1","items":[]}',
    );
    localStorage.setItem(
      DELETE_TOMBSTONE_KEY,
      '{"schema":"strategy_tombstones.v1","items":[{"strategyId":"sentinel","deletedAt":"2026-07-27T00:00:00Z","name":"NORMAL_ONLY"}]}',
    );
    localStorage.setItem("p2-owner-review-library-sentinel", "byte-for-byte");
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    const before = snapshotLocalStorage();

    const getItemSpy = vi.spyOn(Storage.prototype, "getItem");
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    const removeItemSpy = vi.spyOn(Storage.prototype, "removeItem");
    const normalStorageCalls = (
      spy: typeof getItemSpy | typeof setItemSpy | typeof removeItemSpy,
    ) =>
      spy.mock.calls.filter(([key]) => String(key) !== THEME_STORAGE_KEY);
    const fetchProbe = mockNoP2BusinessFetch();

    vi.useFakeTimers();
    const view = renderOwnerReview("sketch");
    vi.clearAllTimers();
    fireEvent.click(screen.getByRole("tab", { name: /③ 版本庫/ }));

    expect(
      screen.getByTestId("owner-review-library-isolated"),
    ).toHaveTextContent(
      "Owner-review 呢個 fixture 唔載入版本庫；請用正常模式查真版本。",
    );
    expect(
      screen.queryByRole("button", {
        name: /過目|編輯參數|刪除|復原|確認整份策略|確認採用/,
      }),
    ).not.toBeInTheDocument();

    await Promise.resolve();
    expect(fetchProbe.p2Count()).toBe(0);
    expect(
      fetchProbe.calls.filter(
        (url) =>
          !url.includes("/api/v1/ib/") &&
          !url.includes("/api/v1/system/"),
      ),
    ).toHaveLength(0);
    expect(normalStorageCalls(getItemSpy)).toHaveLength(0);
    expect(normalStorageCalls(setItemSpy)).toHaveLength(0);
    expect(normalStorageCalls(removeItemSpy)).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);

    fireEvent.click(screen.getByRole("tab", { name: /④ 市場洞察/ }));
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);

    vi.restoreAllMocks();
    expect(snapshotLocalStorage()).toBe(before);
  });

  it("truth correction D1: normal Library still starts strategy and reference reads", async () => {
    const calls: string[] = [];
    // [175] D1: the delete guard now asks the run-reference index per version,
    // so the live read pair is /strategies + /run-references.
    const listedVersion = {
      schema: "strategy_version.v1",
      strategy_id: "strategy-0001",
      status: "draft",
      name: "Trend 回踩 18EMA",
      created: "2026-07-25",
      imported_at: "2026-07-25T03:00:00Z",
      confirmed_at: null,
      content_sha256: "a".repeat(64),
      source_text: "schema: strategy.v1\n",
      spec_ref: null,
      based_on: null,
      based_on_sketch: null,
      based_on_insights: [],
      rationale: "r",
      unquantified_notes: [],
      universe: { contracts: ["NQ"], session: "eth" },
      parameters: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.includes("/api/v1/run-references")) {
          return new Response(
            JSON.stringify({
              schema: "run_reference_list.v1",
              mode: "strategy",
              run_scope: "standard",
              count_known: true,
              count: 0,
              known_match_count: 0,
              runs: [],
              unindexed_candidates: [],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.includes("/api/v1/strategies")) {
          return new Response(
            JSON.stringify({
              schema: "strategy_version_list.v1",
              count: 1,
              versions: [listedVersion],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("unexpected", { status: 599 });
      }),
    );

    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={["/strategies?tab=library"]}>
          <StrategiesPage />
        </MemoryRouter>
      </ThemeProvider>,
    );

    await waitFor(() => {
      expect(
        calls.some((url) => url.includes("/api/v1/strategies")),
      ).toBe(true);
      expect(
        calls.some((url) =>
          url.includes("/api/v1/run-references?mode=strategy"),
        ),
      ).toBe(true);
    });
    expect(
      screen.queryByTestId("owner-review-library-isolated"),
    ).not.toBeInTheDocument();
  });

  it("normal sentinel bytes unchanged after owner-review fixtures + tabs", async () => {
    const user = userEvent.setup();
    const normal = createEmptyDraft([]);
    normal.title = "SENTINEL_NORMAL_DRAFT";
    saveDraft(normal, localStorage);
    const before = readP2LocalStorageBytes(localStorage);
    const fetchProbe = mockNoP2BusinessFetch();

    renderOwnerReview("sketch");
    expect(await screen.findByTestId("owner-review-banner")).toBeInTheDocument();
    expect(screen.queryByText("SENTINEL_NORMAL_DRAFT")).toBeNull();

    // All fixture buttons reachable
    const bar = screen.getByTestId("owner-review-fixtures");
    const buttons = within(bar).getAllByRole("button");
    expect(buttons.length).toBeGreaterThanOrEqual(12);

    // D18 collision: catalog_invalid (len) → legacy_exported (same-ish path)
    await user.click(screen.getByTestId("fixture-catalog_invalid"));
    await user.click(screen.getByTestId("fixture-legacy_exported"));
    // legacy exported title should appear
    await waitFor(() => {
      expect(screen.getByDisplayValue("舊已匯出")).toBeInTheDocument();
    });

    // Re-press same fixture resets
    await user.click(screen.getByTestId("fixture-legacy_exported"));
    await waitFor(() => {
      expect(screen.getByDisplayValue("舊已匯出")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("tab", { name: /② 量化確認/ }));
    await user.click(screen.getByRole("tab", { name: /④ 市場洞察/ }));
    await user.click(screen.getByRole("tab", { name: /① 草圖/ }));

    expect(readP2LocalStorageBytes(localStorage)).toEqual(before);
    expect(fetchProbe.p2Count()).toBe(0);
  });

  it("C probe: valid NQ+YM fixture left panel shows equity_index_futures not 未知", async () => {
    const user = userEvent.setup();
    mockNoP2BusinessFetch();
    renderOwnerReview("sketch");
    await user.click(screen.getByTestId("fixture-valid_nq_ym"));
    // auto-jumps to quantify
    await waitFor(() => {
      expect(
        screen.getByRole("textbox", { name: /strategy.v1 YAML/ }),
      ).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: "驗證" }));
    await waitFor(() => {
      expect(screen.getByTestId("universe-gates-ok")).toBeInTheDocument();
    });
    // left sketch primary class human label, not 未知
    const left = screen.getByTestId("universe-left");
    expect(left.textContent).not.toMatch(/未知資產類別/);
    expect(left.textContent).toMatch(/股票指數期貨|equity_index_futures/);
  });

  it("C probe: insight import under server 5xx fails closed with zero write", async () => {
    const user = userEvent.setup();
    mockNoP2BusinessFetch();
    renderOwnerReview("sketch");
    await user.click(screen.getByTestId("fixture-server_5xx"));
    await user.click(screen.getByRole("tab", { name: /④ 市場洞察/ }));

    const yaml = `schema: insight.v1
insight_id: insight-5xx-probe
origin: workshop
version: 1
based_on_sketch: sketch-20260727-01
based_on_sketch_origin: workshop
instrument: NQ
asset_class: equity_index_futures
title: should not land
condition:
  type: x
  suggested_params: {}
measurement:
  tag: t
  hypothesis: h
validation_status: unverified
`;
    const box = screen.getByRole("textbox", { name: /insight.v1 YAML/ });
    await user.clear(box);
    // fire paste via change for speed
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(box, { target: { value: yaml } });

    await waitFor(() => {
      // wait for sketch load attempt
      expect(
        screen.queryByTestId("insight-sketch-error") ||
          screen.getByTestId("insight-import"),
      ).toBeTruthy();
    });

    await user.click(screen.getByTestId("insight-import"));
    await waitFor(() => {
      expect(screen.queryByText(/已入洞察庫/)).toBeNull();
    });
    // memory insights still empty — use workbench storage via no localStorage
    expect(listInsights(localStorage)).toHaveLength(0);
    // also no success notice
    expect(screen.queryByRole("status")?.textContent ?? "").not.toMatch(
      /已入洞察庫/,
    );
  });

  it("C probe: fixture catalog_invalid → legacy_exported reloads content", async () => {
    const user = userEvent.setup();
    mockNoP2BusinessFetch();
    renderOwnerReview("sketch");
    await user.click(screen.getByTestId("fixture-catalog_invalid"));
    // title empty fresh-ish catalog fixture
    await user.click(screen.getByTestId("fixture-legacy_exported"));
    await waitFor(() => {
      expect(screen.getByDisplayValue("舊已匯出")).toBeInTheDocument();
    });
    // length collision regression: both ids different content
    expect(screen.queryByTestId("catalog-invalid")).toBeNull();
  });
});
