import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { scanBannedVisibleText } from "../lib/backtest/format";
import { ownerReviewCoverageBody } from "../lib/catalog/fixtureCatalog";
import { SKETCH_STORAGE_KEY } from "../lib/sketch/types";
import {
  DELETE_TOMBSTONE_KEY,
  listTombstones,
} from "../lib/strategyDelete";
import { ThemeProvider } from "../theme/ThemeProvider";
import { StrategiesPage } from "./StrategiesPage";

const version = {
  schema: "strategy_version.v1",
  strategy_id: "strategy-0001",
  status: "draft" as const,
  name: "Trend 回踩 18EMA · p50 閘",
  created: "2026-07-25",
  imported_at: "2026-07-25T03:00:00Z",
  confirmed_at: null,
  content_sha256: "a".repeat(64),
  source_text: "schema: strategy.v1\n",
  spec_ref: "TRADING_SPEC_v0.41",
  based_on: null,
  based_on_sketch: "sketch-20260725-01",
  based_on_sketch_origin: "workshop" as const,
  based_on_insights: [],
  rationale: "大框架趨勢中嘅細框架回調",
  unquantified_notes: [
    { note: "「唔追價」已演繹為 signal bar 突破入市", action_needed: "Owner 確認" },
  ],
  universe: { contracts: ["NQ"], session: "eth" },
  parameters: [
    {
      label: "Regime 分離百分位",
      value: "50",
      path: "regime.sep_mult.value",
      source: "owner_explicit",
      note: null,
      kind: "numeric" as const,
    },
    {
      label: "TF trio",
      value: "D · 1H · 5m",
      path: null,
      source: null,
      note: null,
      kind: "structure" as const,
    },
  ],
  spec: {
    universe_session: "eth",
    regime_separation_percentile: 50,
    regime_slope_percentile: 50,
    pullback_ema_period: 18,
    entry_layers: 3,
    signal_bars: ["inside", "magic"],
    target_r_multiple: 1,
    stop_offset_ticks: 1,
  },
};

const invalidReport = {
  schema: "strategy_validation.v1",
  valid: false,
  issue_count: 2,
  issues: [
    {
      path: "regime.sep_mult.value",
      message: "percentile 90.0 outside P1 band [50.0, 70.0]",
      fix: "set value between 50.0 and 70.0 (matrix/spec band)",
      layer: "semantics",
      line: "regime.sep_mult.value: percentile 90.0 outside P1 band [50.0, 70.0] — set value between 50.0 and 70.0 (matrix/spec band)",
    },
    {
      path: "structures[4].type",
      message: "structure type 'lmr' is P2 未支持",
      fix: "remove this structure",
      layer: "semantics",
      line: "structures[4].type: structure type 'lmr' is P2 未支持 — remove this structure",
    },
  ],
  report_text: [
    "regime.sep_mult.value: percentile 90.0 outside P1 band [50.0, 70.0] — set value between 50.0 and 70.0 (matrix/spec band)",
    "structures[4].type: structure type 'lmr' is P2 未支持 — remove this structure",
  ].join("\n"),
};

/** Exact `run_reference_list.v1` body for one `mode=strategy` lookup. */
function runReferenceBody(
  strategyVersion: string,
  runIds: string[] = [],
  unindexed: Array<{ run_id: string; reason: string }> = [],
) {
  const known = unindexed.length === 0;
  return {
    schema: "run_reference_list.v1",
    mode: "strategy",
    run_scope: "standard",
    count_known: known,
    count: known ? runIds.length : null,
    known_match_count: runIds.length,
    runs: runIds.map((run_id) => ({
      run_id,
      strategy_version: strategyVersion,
      contract_id: "NQ-202609-CME",
      symbol: "NQ",
      session_name: "eth",
      range_start: "2026-05-01T00:00:00Z",
      range_end: "2026-07-22T21:00:00Z",
    })),
    unindexed_candidates: unindexed,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(
  options: {
    versions?: unknown[];
    validate?: unknown;
    /** When true, validate returns valid; import/confirm succeed. */
    happyPath?: boolean;
    /** Per-strategy delete-guard answer; default is a proven zero. */
    runReferences?: (strategyVersion: string) => Response;
    derive?: () => Response;
    deleteVersion?: () => Response;
  } = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/v1/strategies/validate")) {
        if (options.happyPath) {
          return new Response(
            JSON.stringify({
              schema: "strategy_validation.v1",
              valid: true,
              issue_count: 0,
              issues: [],
              report_text: "",
              name: "happy",
              universe: { contracts: ["NQ"], session: "eth" },
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify(options.validate ?? invalidReport), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/v1/strategies/import") && method === "POST") {
        return new Response(
          JSON.stringify({
            schema: "strategy_import.v1",
            deduplicated: false,
            message: "已匯入為 draft",
            version: { ...version, strategy_id: "strategy-0099", status: "draft" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/confirm") && method === "POST") {
        return new Response(
          JSON.stringify({
            ...version,
            strategy_id: "strategy-0099",
            status: "confirmed",
            confirmed_at: "2026-07-26T00:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/api/v1/run-references")) {
        const strategyVersion =
          new URL(url, "http://localhost").searchParams.get(
            "strategy_version",
          ) ?? "";
        return (
          options.runReferences?.(strategyVersion) ??
          jsonResponse(runReferenceBody(strategyVersion))
        );
      }
      if (url.includes("/derive") && method === "POST") {
        return (
          options.derive?.() ??
          jsonResponse({
            schema: "strategy_derive.v1",
            parent_strategy_id: "strategy-0001",
            changed_count: 1,
            changed_paths: ["regime.sep_mult.value"],
            deduplicated: false,
            version: {
              ...version,
              strategy_id: "strategy-0003",
              status: "draft",
              based_on: "strategy-0001",
              content_sha256: "b".repeat(64),
            },
          })
        );
      }
      if (url.includes("/api/v1/strategies/") && method === "DELETE") {
        const id = url.split("/api/v1/strategies/")[1];
        return (
          options.deleteVersion?.() ??
          jsonResponse({
            schema: "strategy_delete.v1",
            strategy_id: id,
            deleted_at: "2026-07-28T04:05:06Z",
            archived_status: "draft",
            archived_to: `data/strategies/_deleted/${id}.yaml`,
          })
        );
      }
      if (url.includes("/api/v1/runs")) {
        return new Response(
          JSON.stringify({ schema: "run_list.v1", count: 0, runs: [] }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/api/v1/data/coverage")) {
        // Additive catalog fields for instrument picker (P2 closed loop)
        return new Response(JSON.stringify(ownerReviewCoverageBody()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      // Live Seam A: POST ZIP → sketch_detail.v1 201
      if (
        (url.endsWith("/api/v1/sketches") || url.includes("/api/v1/sketches?")) &&
        method === "POST"
      ) {
        const snapRaw = localStorage.getItem(SKETCH_STORAGE_KEY);
        let sketchId = "sketch-20260727-01";
        let title = "title";
        if (snapRaw) {
          try {
            const snap = JSON.parse(snapRaw) as {
              activeId?: string;
              drafts?: Array<{ sketchId: string; title: string }>;
            };
            if (snap.activeId) {
              sketchId = snap.activeId;
              const d = snap.drafts?.find((x) => x.sketchId === snap.activeId);
              if (d?.title) title = d.title;
            }
          } catch {
            /* ignore */
          }
        }
        const charts = [
          { file: "chart-D.png", timeframe: "D", role: "bias", indicators_shown: ["ema18"], owner_view: "v" },
          { file: "chart-1H.png", timeframe: "1H", role: "mid", indicators_shown: ["ema18"], owner_view: "v" },
          { file: "chart-30m.png", timeframe: "30m", role: "auxiliary", indicators_shown: ["ema18"], owner_view: "v" },
          { file: "chart-5m.png", timeframe: "5m", role: "entry", indicators_shown: ["ema18"], owner_view: "v" },
        ];
        return new Response(
          JSON.stringify({
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
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/api/v1/strategies") || url.includes("/api/v1/strategies?")) {
        return new Response(
          JSON.stringify({
            schema: "strategy_version_list.v1",
            count: (options.versions ?? []).length,
            versions: options.versions ?? [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    }),
  );
}

beforeEach(() => {
  localStorage.removeItem(SKETCH_STORAGE_KEY);
  localStorage.removeItem(DELETE_TOMBSTONE_KEY);
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.removeItem(SKETCH_STORAGE_KEY);
  localStorage.removeItem(DELETE_TOMBSTONE_KEY);
});

function renderPage() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/strategies"]}>
        <StrategiesPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

async function openTab(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
  await user.click(screen.getByRole("tab", { name }));
}

async function fillFourViews(
  _user: ReturnType<typeof userEvent.setup>,
  prefix = "判斷內容",
) {
  const views = screen.getAllByPlaceholderText(/圖你見到咩/);
  expect(views).toHaveLength(4);
  for (const [i, el] of views.entries()) {
    // fireEvent is much faster under parallel suite load than userEvent.type
    fireEvent.change(el, {
      target: { value: `${prefix} ${i + 1} · NQ 29640` },
    });
  }
}

async function fillRationale(text = "大框架趨勢中嘅細框架回調") {
  fireEvent.change(screen.getByRole("textbox", { name: "整體理據" }), {
    target: { value: text },
  });
}

/** Upload four real 1×1 PNGs so Correction A export gate passes. */
async function fillFourPngs(user: ReturnType<typeof userEvent.setup>) {
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
  // FileReader is async — wait until all four previews mount.
  await waitFor(() => {
    expect(screen.getAllByRole("img", { name: /上載預覽/ })).toHaveLength(4);
  });
}

async function fillExportReady(user: ReturnType<typeof userEvent.setup>) {
  await selectPrimaryInstrument(user);
  await fillFourViews(user);
  await fillRationale();
  await fillFourPngs(user);
}

/** Wait for catalog + pick NQ (required for export after P2 instrument). */
async function selectPrimaryInstrument(
  user: ReturnType<typeof userEvent.setup>,
  symbol = "NQ",
) {
  const select = await screen.findByTestId("instrument-select");
  await user.selectOptions(select, symbol);
}

function expectNeverCollapsible(region: HTMLElement) {
  expect(region.closest("details")).toBeNull();
  expect(region.querySelector("details")).toBeNull();
  expect(region.hasAttribute("hidden")).toBe(false);
}

describe("P2 workbench shell + tab ① sketch (WO-010 batch 1 fix)", () => {
  it("export stays disabled until title, views, PNGs and rationale are filled", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByRole("heading", { name: "策略工作台" })).toBeInTheDocument();
    const exportBtn = screen.getByRole("button", { name: "⬇ 匯出圖文包" });
    expect(exportBtn).toBeDisabled();

    // 存草稿 — zero preconditions even with empty title
    await user.click(screen.getByRole("button", { name: "存草稿" }));
    expect(await screen.findByText(/草稿已存/)).toBeInTheDocument();

    await selectPrimaryInstrument(user);
    await fillFourViews(user);
    await fillRationale();
    // Title still empty → still disabled (new #5)
    expect(exportBtn).toBeDisabled();
    expect(screen.getByLabelText("匯出阻擋原因")).toHaveTextContent(/標題未填/);

    await user.type(
      screen.getByRole("textbox", { name: "草圖標題" }),
      "NQ 趨勢日回踩 90EMA",
    );
    // Images still missing
    expect(exportBtn).toBeDisabled();
    expect(screen.getByLabelText("匯出阻擋原因").textContent).toMatch(/未上載/);

    await fillFourPngs(user);
    await waitFor(() => {
      expect(exportBtn).toBeEnabled();
    });
  });

  it("shows itemized missing cells (not a vague 未填齊)", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();
    await user.type(
      screen.getByRole("textbox", { name: "草圖標題" }),
      "有標題",
    );
    const views = screen.getAllByPlaceholderText(/圖你見到咩/);
    await user.type(views[0], "D only");
    await user.type(views[1], "1H only");
    // 30m + 5m empty
    const hint = screen.getByLabelText("匯出阻擋原因");
    expect(hint.textContent).toMatch(/30m/);
    expect(hint.textContent).toMatch(/5m/);
    expect(hint.textContent).not.toBe("四格文字未填齊 → 匯出掣 disabled");
  });

  it("export dialog shows path, full opener, download, and copy", async () => {
    mockApi();
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    renderPage();
    await user.type(
      screen.getByRole("textbox", { name: "草圖標題" }),
      "NQ 趨勢日回踩 90EMA",
    );
    await fillExportReady(user);
    await user.click(screen.getByTestId("sketch-export"));

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByTestId("export-dialog-persisted"),
    ).toBeInTheDocument();
    const pathCode = dialog.querySelector(".export-path code");
    expect(pathCode?.textContent).toMatch(
      /^data\/sketches\/workshop\/sketch-\d{8}-\d{2}\/$/,
    );
    expect(
      within(dialog).getByLabelText("圖文包檔案樹").textContent,
    ).toMatch(/meta\.yaml/);
    expect(
      within(dialog).getByRole("button", { name: "⬇ 下載圖文包" }),
    ).toBeInTheDocument();

    const opener = within(dialog).getByRole("textbox", {
      name: "terminal 開場白全文",
    }) as HTMLTextAreaElement;
    expect(opener.value).toContain("data/sketches/workshop/");
    // D3: enough rows that full opener is intended to be visible
    expect(opener.rows).toBeGreaterThanOrEqual(5);

    await user.click(
      within(dialog).getByRole("button", { name: "⧉ 複製 terminal 開場白" }),
    );
    expect(writeText).toHaveBeenCalled();
  });

  it("draft list retains prior sketch after 新草圖 and restores content", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    await user.type(
      screen.getByRole("textbox", { name: "草圖標題" }),
      "第一份草稿標題",
    );
    const views = screen.getAllByPlaceholderText(/圖你見到咩/);
    await user.type(views[0], "日線留低");
    await user.click(screen.getByRole("button", { name: "存草稿" }));

    const firstId = screen.getByLabelText("草圖編號").textContent ?? "";
    expect(firstId).toMatch(/sketch-\d{8}-\d{2}/);

    await user.click(screen.getByRole("button", { name: "新草圖" }));
    // List still has the first draft
    const list = screen.getByRole("region", { name: "草稿列表" });
    expect(within(list).getByText(new RegExp(firstId.trim().split(" ")[0]))).toBeInTheDocument();

    // Switch back
    await user.click(within(list).getByText(new RegExp(firstId.trim().split(" ")[0])));
    expect(screen.getByRole("textbox", { name: "草圖標題" })).toHaveValue(
      "第一份草稿標題",
    );
    expect(screen.getAllByPlaceholderText(/圖你見到咩/)[0]).toHaveValue("日線留低");
  });

  it("library deep-link opens exact strategy and loads origin-aware sketch", async () => {
    const sketchUrls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/sketches/")) {
          sketchUrls.push(url);
          return new Response(
            JSON.stringify({
              schema: "sketch_detail.v1",
              meta: {
                schema: "sketch.v1",
                sketch_id: "sketch-20260725-01",
                kind: "strategy",
                origin: "workshop",
                chart_source: "screenshot",
                instructions_template: "instructions.v1",
                instrument: "NQ",
                asset_class: "equity_index_futures",
                created: "2026-07-25",
                title: "DeepLink 草圖",
                rationale: "理據",
                charts: [
                  {
                    file: "chart-D.png",
                    timeframe: "D",
                    role: "bias",
                    indicators_shown: [],
                    owner_view: "D 文",
                  },
                  {
                    file: "chart-1H.png",
                    timeframe: "1H",
                    role: "mid",
                    indicators_shown: [],
                    owner_view: "1H 文",
                  },
                  {
                    file: "chart-30m.png",
                    timeframe: "30m",
                    role: "auxiliary",
                    indicators_shown: [],
                    owner_view: "30m 文",
                  },
                  {
                    file: "chart-5m.png",
                    timeframe: "5m",
                    role: "entry",
                    indicators_shown: [],
                    owner_view: "5m 文",
                  },
                ],
              },
              instructions_markdown: "x",
              images: [
                "chart-D.png",
                "chart-1H.png",
                "chart-30m.png",
                "chart-5m.png",
              ].map((file) => ({
                file,
                url: `/api/v1/sketches/workshop/sketch-20260725-01/images/${file}`,
                content_type: "image/png",
                byte_count: 1,
                sha256: "b".repeat(64),
              })),
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.includes("/api/v1/strategies") && !url.includes("confirm")) {
          return new Response(
            JSON.stringify({
              schema: "strategy_version_list.v1",
              count: 2,
              versions: [
                { ...version, strategy_id: "strategy-0002", name: "Other" },
                version,
              ],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.includes("/api/v1/runs")) {
          return new Response(
            JSON.stringify({ schema: "run_list.v1", count: 0, runs: [] }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("no", { status: 404 });
      }),
    );

    render(
      <ThemeProvider>
        <MemoryRouter
          initialEntries={[
            "/strategies?tab=library&strategy=strategy-0001",
          ]}
        >
          <StrategiesPage />
        </MemoryRouter>
      </ThemeProvider>,
    );
    await waitFor(() => {
      expect(
        screen.getByRole("region", { name: "策略詳情" }),
      ).toBeInTheDocument();
    });
    expect(screen.getByText(/過目 strategy-0001/)).toBeInTheDocument();
    expect(screen.queryByText(/過目 strategy-0002/)).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("sketch-ready")).toBeInTheDocument();
    });
    expect(screen.getByText("DeepLink 草圖")).toBeInTheDocument();
    expect(sketchUrls.some((u) => u.includes(
      "/api/v1/sketches/workshop/sketch-20260725-01",
    ))).toBe(true);
  });

  it("library deep-link missing strategy does not open first row", async () => {
    // List has other strategies — must NOT fall back to first row.
    mockApi({
      versions: [
        { ...version, strategy_id: "strategy-0001", name: "First Row" },
        { ...version, strategy_id: "strategy-0002", name: "Second Row" },
      ],
    });
    render(
      <ThemeProvider>
        <MemoryRouter
          initialEntries={[
            "/strategies?tab=library&strategy=strategy-does-not-exist",
          ]}
        >
          <StrategiesPage />
        </MemoryRouter>
      </ThemeProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText(/找不到鎖定版本/)).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("region", { name: "策略詳情" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/過目 strategy-0001/)).not.toBeInTheDocument();
  });

  it("shows image preview after file upload", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    const png = new File(
      [
        Uint8Array.from(
          atob(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
          ),
          (c) => c.charCodeAt(0),
        ),
      ],
      "nq-d-chart.png",
      { type: "image/png" },
    );

    const fileInputs = document.querySelectorAll<HTMLInputElement>(
      'input[type="file"][accept*="png"]',
    );
    expect(fileInputs.length).toBe(4);
    await user.upload(fileInputs[0], png);

    expect(
      await screen.findByRole("img", { name: /D 上載預覽/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("nq-d-chart.png")).toBeInTheDocument();
  });

  it("tab ④ is full insight UI (not deferred placeholder)", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();
    await openTab(user, /④ 市場洞察/);
    expect(screen.getByRole("region", { name: "新洞察" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "匯入洞察" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "洞察庫" })).toBeInTheDocument();
    expect(
      screen.getAllByText(/冇「用呢個洞察跑回測」/).length,
    ).toBeGreaterThan(0);
  });
});

describe("P2 strategy library legacy panels (tab ② / ③)", () => {
  it("constraint #15: validate leaves write-count at 0; confirm calls import+confirm", async () => {
    mockApi({ happyPath: true });
    const user = userEvent.setup();
    renderPage();
    await openTab(user, /② 量化確認/);

    // Full v1.4 universe (primary-only) so client gates pass; local sketch missing is warning only.
    const yaml = `schema: strategy.v1
meta:
  name: NQ test
  based_on_sketch: sketch-missing-for-test
  based_on_sketch_origin: workshop
rationale: edge
unquantified_notes: []
universe:
  primary_instrument: NQ
  asset_class: equity_index_futures
  contracts: [NQ]
  expansion_rationale: {}
  session: eth
`;
    const box = screen.getByRole("textbox", { name: /strategy.v1 YAML/ });
    fireEvent.change(box, { target: { value: yaml } });

    await user.click(screen.getByRole("button", { name: "驗證" }));
    await screen.findByRole("region", { name: "左右對照" });
    expect(await screen.findByTestId("universe-gates-ok")).toBeInTheDocument();

    // Dedicated #15 probe while still on tab ② (DOM counter).
    expect(screen.getByTestId("pre-confirm-write-count")).toHaveTextContent(
      "0",
    );
    const fetchMock = vi.mocked(fetch);
    const writeCalls = () =>
      fetchMock.mock.calls.filter((call) => {
        const url = String(call[0]);
        return url.includes("/import") || url.includes("/confirm");
      });
    expect(writeCalls()).toHaveLength(0);

    await user.click(
      screen.getByRole("button", { name: /確認整份策略 → 入版本庫/ }),
    );
    // Confirm navigates to ③ — assert via fetch, not unmounted write-count.
    await waitFor(() => {
      expect(writeCalls()).toHaveLength(2);
    });
    expect(
      writeCalls().filter((c) => String(c[0]).includes("/import")),
    ).toHaveLength(1);
    expect(
      writeCalls().filter((c) => String(c[0]).includes("/confirm")),
    ).toHaveLength(1);
  });

  it("shows each validation issue and offers the verbatim paste-back text", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();
    await openTab(user, /② 量化確認/);

    await user.type(
      screen.getByRole("textbox", { name: /strategy.v1 YAML/ }),
      "schema: strategy.v1",
    );
    await user.click(screen.getByRole("button", { name: "驗證" }));

    const report = await screen.findByRole("region", { name: "驗證失敗" });
    expect(within(report).getByText(/2 個問題/)).toBeInTheDocument();
    expect(
      within(report).getByRole("textbox", { name: /貼返俾 AI 嘅原文/ }),
    ).toHaveValue(invalidReport.report_text);
  });

  it("keeps unquantified_notes visible and parameters read-only", async () => {
    mockApi({ versions: [version] });
    const user = userEvent.setup();
    renderPage();
    await openTab(user, /③ 版本庫/);

    await waitFor(() => {
      expect(screen.getByText("strategy-0001")).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: "過目" }));

    const notes = screen.getByRole("region", { name: "unquantified notes" });
    expect(within(notes).getByText(/1 項/)).toBeInTheDocument();
    expectNeverCollapsible(notes);

    const table = screen.getByRole("region", { name: "參數唯讀表" });
    expect(within(table).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("states 0 項 rather than hiding the notes panel when there are none", async () => {
    mockApi({ versions: [{ ...version, unquantified_notes: [] }] });
    const user = userEvent.setup();
    renderPage();
    await openTab(user, /③ 版本庫/);

    await waitFor(() => {
      expect(screen.getByText("strategy-0001")).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: "過目" }));

    const notes = screen.getByRole("region", { name: "unquantified notes" });
    expect(
      within(notes).getByRole("heading", { name: /unquantified_notes · 0 項/ }),
    ).toBeInTheDocument();
    expectNeverCollapsible(notes);
  });

  it("would fail if the notes panel were turned into a <details>", () => {
    const host = document.createElement("div");
    host.innerHTML =
      '<details><section role="region" aria-label="unquantified notes">x</section></details>';
    document.body.append(host);
    const collapsed = host.querySelector<HTMLElement>('[role="region"]');
    expect(collapsed).not.toBeNull();
    expect(() => {
      expectNeverCollapsible(collapsed as HTMLElement);
    }).toThrow();
    host.remove();
  });

  /**
   * D1 / [118]: while the reference lookup is pending, delete must stay
   * disabled even after the version row has rendered. Click must not arm a
   * pending delete.
   */
  it("keeps 刪除 disabled while the reference lookup is pending; enables only after a proven zero", async () => {
    let resolveRefs!: (value: Response) => void;
    const refsPromise = new Promise<Response>((resolve) => {
      resolveRefs = resolve;
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/run-references")) {
          return refsPromise;
        }
        if (
          url.endsWith("/api/v1/strategies") ||
          url.includes("/api/v1/strategies?")
        ) {
          return jsonResponse({
            schema: "strategy_version_list.v1",
            count: 1,
            versions: [version],
          });
        }
        return new Response("not found", { status: 404 });
      }),
    );

    const user = userEvent.setup();
    renderPage();
    await openTab(user, /③ 版本庫/);

    await waitFor(() => {
      expect(screen.getByText("strategy-0001")).toBeInTheDocument();
    });

    const del = screen.getByRole("button", { name: "刪除" });
    expect(del).toBeDisabled();
    expect(del).toHaveAttribute("title", "引用數量暫時核實唔到，所以唔准刪");

    // Force click while disabled — requestDelete must still guard.
    del.click();
    expect(screen.queryByRole("button", { name: "復原" })).not.toBeInTheDocument();
    expect(listTombstones()).toHaveLength(0);

    resolveRefs(jsonResponse(runReferenceBody("strategy-0001")));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeEnabled();
    });
    expect(screen.getByRole("button", { name: "刪除" })).toHaveAttribute(
      "title",
      "刪除",
    );
  });

  it("keeps 刪除 disabled when the reference lookup returns 503", async () => {
    mockApi({
      versions: [version],
      runReferences: () => new Response("down", { status: 503 }),
    });

    const user = userEvent.setup();
    renderPage();
    await openTab(user, /③ 版本庫/);

    await waitFor(() => {
      expect(screen.getByText("strategy-0001")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeDisabled();
    });
    expect(
      screen.getByText("引用數量暫時核實唔到，所以唔准刪"),
    ).toBeInTheDocument();
  });

  it("keeps 刪除 disabled when the reference payload is malformed", async () => {
    mockApi({
      versions: [version],
      runReferences: (id) =>
        jsonResponse({
          ...runReferenceBody(id),
          // Certainty claimed while an unindexed candidate exists.
          unindexed_candidates: [{ run_id: "legacy-001", reason: "x" }],
        }),
    });

    const user = userEvent.setup();
    renderPage();
    await openTab(user, /③ 版本庫/);

    await waitFor(() => {
      expect(screen.getByText("strategy-0001")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeDisabled();
    });
    expect(
      screen.getByText("引用數量暫時核實唔到，所以唔准刪"),
    ).toBeInTheDocument();
  });

  it("blocks delete with the exact backtest count and run ids", async () => {
    mockApi({
      versions: [version],
      runReferences: (id) =>
        jsonResponse(
          runReferenceBody(id, [
            "nq-20260723-standard-001",
            "nq-20260724-standard-002",
          ]),
        ),
    });

    const user = userEvent.setup();
    renderPage();
    await openTab(user, /③ 版本庫/);

    await waitFor(() => {
      expect(screen.getByText("strategy-0001")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeDisabled();
    });
    expect(screen.getByRole("button", { name: "刪除" })).toHaveAttribute(
      "title",
      "2 次回測用緊呢個版本",
    );
    expect(screen.getByText(/2 次回測用緊呢個版本/)).toBeInTheDocument();
    expect(
      screen.getByText(/nq-20260723-standard-001、nq-20260724-standard-002/),
    ).toBeInTheDocument();
  });
});

/**
 * P2 tab ③ version lifecycle seam ([175] D1–D3).
 * Backend truth: run-reference guard, server-side numeric derive, and a real
 * archive delete after the ~5s undo window.
 */
describe("P2 version lifecycle seam (derive + real delete)", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  function fetchCalls(): Array<{ url: string; method: string; body: unknown }> {
    const mock = globalThis.fetch as unknown as {
      mock: { calls: Array<[RequestInfo | URL, RequestInit | undefined]> };
    };
    return mock.mock.calls.map(([input, init]) => ({
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      body:
        typeof init?.body === "string"
          ? (JSON.parse(init.body) as unknown)
          : null,
    }));
  }

  async function openLibrary(user: ReturnType<typeof userEvent.setup>) {
    renderPage();
    await openTab(user, /③ 版本庫/);
    await waitFor(() => {
      expect(screen.getByText("strategy-0001")).toBeInTheDocument();
    });
  }

  async function enterEditMode(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "過目" }));
    await user.click(screen.getByRole("button", { name: "✎ 編輯參數" }));
  }

  it("version library ban-scan strips strategy tech suffixes and raw status/guard terms", async () => {
    const confirmedVersion = {
      ...version,
      status: "confirmed" as const,
      confirmed_at: "2026-07-26T00:00:00Z",
    };
    mockApi({
      versions: [confirmedVersion],
      runReferences: (id) =>
        jsonResponse(runReferenceBody(id, ["nq-20260723-standard-001"])),
    });
    const user = userEvent.setup();
    await openLibrary(user);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeDisabled();
    });

    const library = screen.getByRole("region", { name: "版本列表" });
    const listCopy = library.textContent ?? "";
    expect(listCopy).toContain("Trend 回踩 18EMA");
    expect(listCopy).toContain("已確認");
    expect(listCopy).toContain("1 次回測用緊呢個版本");
    for (const banned of ["p50 閘", "standard run", "confirmed"]) {
      expect(listCopy).not.toContain(banned);
    }

    const visibleName = within(library).getByTitle(confirmedVersion.name);
    expect(visibleName).toHaveTextContent("Trend 回踩 18EMA");
    await user.click(screen.getByRole("button", { name: "過目" }));
    const reviewHeading = screen.getByRole("heading", {
      name: "過目 strategy-0001 · Trend 回踩 18EMA",
    });
    expect(reviewHeading).toHaveAttribute("title", confirmedVersion.name);
    expect(reviewHeading).not.toHaveTextContent("p50 閘");
  });

  /**
   * Scans the remaining lifecycle copy after the whole-library ban scan above.
   */
  it("shows no banned technical terms in the guard, edit and error copy", async () => {
    mockApi({
      versions: [version],
      runReferences: (id) =>
        jsonResponse(runReferenceBody(id, ["nq-20260723-standard-001"])),
    });
    const user = userEvent.setup();
    await openLibrary(user);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeDisabled();
    });
    const guardCopy = screen.getByTestId("delete-guard-copy");
    expect(scanBannedVisibleText(guardCopy.textContent ?? "")).toEqual([]);

    await enterEditMode(user);
    fireEvent.change(
      screen.getByRole("textbox", { name: "編輯 Regime 分離百分位" }),
      { target: { value: "60" } },
    );
    const summary = screen.getByTestId("derive-change-summary");
    expect(scanBannedVisibleText(summary.textContent ?? "")).toEqual([]);
    const paramsPanel = screen.getByRole("region", { name: "參數唯讀表" });
    // Provenance chips and column headers must stay in Owner language.
    expect(
      scanBannedVisibleText(
        within(paramsPanel)
          .getAllByRole("columnheader")
          .map((cell) => cell.textContent ?? "")
          .join(" "),
      ),
    ).toEqual([]);
  });

  it("edit mode exposes only numeric leaves; structural rows have no input", async () => {
    mockApi({ versions: [version] });
    const user = userEvent.setup();
    await openLibrary(user);
    await enterEditMode(user);

    const table = screen.getByRole("region", { name: "參數唯讀表" });
    const inputs = within(table).getAllByRole("textbox");
    expect(inputs).toHaveLength(1);
    expect(inputs[0]).toHaveAccessibleName("編輯 Regime 分離百分位");
    // The structural row is present but never editable (#17).
    expect(within(table).getByText("TF trio")).toBeInTheDocument();
    expect(within(table).getByText("文件結構 · UI 改唔到")).toBeInTheDocument();
  });

  it("keeps 儲存 disabled for a no-op edit and sends only the changed path", async () => {
    // Two numeric leaves so an untouched one has somewhere to leak into.
    mockApi({
      versions: [
        {
          ...version,
          parameters: [
            ...version.parameters,
            {
              label: "回踩 EMA 週期",
              value: "18",
              path: "pullback.ema.period",
              source: "document",
              note: null,
              kind: "numeric" as const,
            },
          ],
        },
      ],
    });
    const user = userEvent.setup();
    await openLibrary(user);
    await enterEditMode(user);

    const save = screen.getByRole("button", { name: "儲存為新版本" });
    expect(save).toBeDisabled();

    const input = screen.getByRole("textbox", {
      name: "編輯 Regime 分離百分位",
    });
    // The same number written differently is still a no-op.
    fireEvent.change(input, { target: { value: "50.0" } });
    expect(screen.getByRole("button", { name: "儲存為新版本" })).toBeDisabled();

    fireEvent.change(input, { target: { value: "60" } });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "儲存為新版本" })).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "儲存為新版本" }));

    await waitFor(() => {
      expect(fetchCalls().some((call) => call.url.includes("/derive"))).toBe(
        true,
      );
    });
    const derive = fetchCalls().find((call) => call.url.includes("/derive"));
    expect(derive?.method).toBe("POST");
    expect(derive?.url).toContain("/api/v1/strategies/strategy-0001/derive");
    expect(derive?.body).toEqual({
      patches: [{ path: "regime.sep_mult.value", value: 60 }],
    });
  });

  it("blocks 儲存 when an edited value is not a finite number", async () => {
    mockApi({ versions: [version] });
    const user = userEvent.setup();
    await openLibrary(user);
    await enterEditMode(user);

    fireEvent.change(
      screen.getByRole("textbox", { name: "編輯 Regime 分離百分位" }),
      { target: { value: "六十" } },
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "儲存為新版本" }),
      ).toBeDisabled();
    });
    expect(screen.getByTestId("derive-change-summary")).toHaveTextContent(
      "Regime 分離百分位",
    );
    expect(fetchCalls().some((call) => call.url.includes("/derive"))).toBe(
      false,
    );
  });

  it("reports the server-created child version without recomputing it locally", async () => {
    mockApi({ versions: [version] });
    const user = userEvent.setup();
    await openLibrary(user);
    await enterEditMode(user);
    fireEvent.change(
      screen.getByRole("textbox", { name: "編輯 Regime 分離百分位" }),
      { target: { value: "60" } },
    );
    await user.click(screen.getByRole("button", { name: "儲存為新版本" }));

    await waitFor(() => {
      expect(screen.getByTestId("library-notice")).toHaveTextContent(
        "已儲存為新版本 strategy-0003",
      );
    });
    expect(screen.getByTestId("library-notice")).toHaveTextContent(
      "原版本 strategy-0001 不變",
    );
    // Content came from the response; the client never posts a document.
    const derive = fetchCalls().find((call) => call.url.includes("/derive"));
    expect(JSON.stringify(derive?.body)).not.toContain("source_text");
  });

  it("states the twin instead of erroring when the backend deduplicates", async () => {
    mockApi({
      versions: [version],
      derive: () =>
        jsonResponse({
          schema: "strategy_derive.v1",
          parent_strategy_id: "strategy-0001",
          changed_count: 1,
          changed_paths: ["regime.sep_mult.value"],
          deduplicated: true,
          version: {
            ...version,
            strategy_id: "strategy-0003",
            status: "draft",
            based_on: "strategy-0001",
            content_sha256: "b".repeat(64),
          },
        }),
    });
    const user = userEvent.setup();
    await openLibrary(user);
    await enterEditMode(user);
    fireEvent.change(
      screen.getByRole("textbox", { name: "編輯 Regime 分離百分位" }),
      { target: { value: "60" } },
    );
    await user.click(screen.getByRole("button", { name: "儲存為新版本" }));

    await waitFor(() => {
      expect(screen.getByTestId("library-notice")).toHaveTextContent(
        "同一修改已有版本 strategy-0003",
      );
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the verbatim 422 report, offers exact copy, and inserts no local version", async () => {
    const clipboard = vi.fn(async () => undefined);
    const reportText =
      "regime.sep_mult.value: percentile 90.0 outside P1 band [50.0, 70.0] — set value between 50.0 and 70.0";
    mockApi({
      versions: [version],
      derive: () =>
        jsonResponse(
          {
            detail: {
              schema: "strategy_validation.v1",
              valid: false,
              issue_count: 1,
              issues: [],
              report_text: reportText,
            },
          },
          422,
        ),
    });
    const user = userEvent.setup();
    // user-event installs its own clipboard stub during setup, so ours must
    // be defined afterwards to be the one the component actually calls.
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboard },
    });
    await openLibrary(user);
    await enterEditMode(user);
    fireEvent.change(
      screen.getByRole("textbox", { name: "編輯 Regime 分離百分位" }),
      { target: { value: "90" } },
    );
    await user.click(screen.getByRole("button", { name: "儲存為新版本" }));

    await waitFor(() => {
      expect(screen.getByTestId("derive-error-text")).toHaveTextContent(
        "percentile 90.0 outside P1 band",
      );
    });
    // Zero local fake version: the list still holds exactly the parent.
    expect(screen.queryByText("strategy-0003")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "⧉ 複製全部錯誤" }));
    await waitFor(() => {
      expect(clipboard).toHaveBeenCalledWith(reportText);
    });
  });

  it("reports a 503 derive honestly and inserts no local version", async () => {
    mockApi({
      versions: [version],
      derive: () =>
        jsonResponse({ detail: "策略儲存暫時不可用；未有建立新版本" }, 503),
    });
    const user = userEvent.setup();
    await openLibrary(user);
    await enterEditMode(user);
    fireEvent.change(
      screen.getByRole("textbox", { name: "編輯 Regime 分離百分位" }),
      { target: { value: "60" } },
    );
    await user.click(screen.getByRole("button", { name: "儲存為新版本" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "策略儲存暫時不可用，未有建立新版本",
      );
    });
    expect(screen.queryByText("strategy-0003")).not.toBeInTheDocument();
  });

  it("fails closed when the derive response cannot be verified", async () => {
    mockApi({
      versions: [version],
      derive: () =>
        jsonResponse({
          schema: "strategy_derive.v1",
          parent_strategy_id: "strategy-0001",
          changed_count: 1,
          changed_paths: ["regime.sep_mult.value"],
          deduplicated: false,
          // Grandparent kept instead of the direct parent.
          version: {
            ...version,
            strategy_id: "strategy-0003",
            based_on: "strategy-0000",
            content_sha256: "b".repeat(64),
          },
        }),
    });
    const user = userEvent.setup();
    await openLibrary(user);
    await enterEditMode(user);
    fireEvent.change(
      screen.getByRole("textbox", { name: "編輯 Regime 分離百分位" }),
      { target: { value: "60" } },
    );
    await user.click(screen.getByRole("button", { name: "儲存為新版本" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("衍生結果未能核實");
    });
    expect(screen.queryByText("strategy-0003")).not.toBeInTheDocument();
  });

  it("issues no DELETE during the grace window and none at all after undo", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({ versions: [version] });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await openLibrary(user);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "刪除" }));
    expect(screen.getByText("已刪除 · 約 5 秒內可復原")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(fetchCalls().some((call) => call.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: "復原" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fetchCalls().some((call) => call.method === "DELETE")).toBe(false);
    expect(screen.getByTestId("library-notice")).toHaveTextContent(
      "已復原——冇發出任何刪除",
    );
  });

  it("sends the real DELETE once the grace window closes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({ versions: [version] });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await openLibrary(user);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "刪除" }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    await waitFor(() => {
      expect(fetchCalls().filter((call) => call.method === "DELETE")).toHaveLength(
        1,
      );
    });
    const del = fetchCalls().find((call) => call.method === "DELETE");
    expect(del?.url).toContain("/api/v1/strategies/strategy-0001");
  });

  it("flushes a pending delete when the tab changes or the page unmounts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({ versions: [version] });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const view = renderPage();
    await openTab(user, /③ 版本庫/);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "刪除" }));
    expect(fetchCalls().some((call) => call.method === "DELETE")).toBe(false);

    // Leaving the page counts as confirm (#21) — the tab switch unmounts it.
    await openTab(user, /① 草圖/);
    await waitFor(() => {
      expect(fetchCalls().filter((call) => call.method === "DELETE")).toHaveLength(
        1,
      );
    });
    view.unmount();
    // The flush already happened; unmount must not double-send.
    expect(fetchCalls().filter((call) => call.method === "DELETE")).toHaveLength(
      1,
    );
  });

  it("restores the row with human copy and run ids when DELETE returns 409", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({
      versions: [version],
      deleteVersion: () =>
        jsonResponse(
          {
            detail: {
              schema: "strategy_delete_blocked.v1",
              message: "呢個策略版本仍有 standard run 引用，未能刪除",
              standard_run_count: 1,
              run_ids: ["nq-20260723-standard-001"],
            },
          },
          409,
        ),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await openLibrary(user);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "刪除" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "未能刪除：1 次回測用緊呢個版本",
      );
    });
    // Row is back, not silently gone.
    expect(screen.getByText("strategy-0001")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "nq-20260723-standard-001",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("standard run");
    expect(
      screen.queryByRole("button", { name: "復原" }),
    ).not.toBeInTheDocument();
  });

  it("restores the row and says nothing was deleted when DELETE returns 503", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({
      versions: [version],
      deleteVersion: () =>
        jsonResponse(
          { detail: "run reference lookup 暫時不可用；未有刪除任何策略" },
          503,
        ),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await openLibrary(user);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "刪除" })).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "刪除" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "暫時刪唔到，冇任何嘢被刪",
      );
    });
    expect(screen.getByText("strategy-0001")).toBeInTheDocument();
  });
});
