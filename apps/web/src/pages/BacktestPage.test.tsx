import {
  act,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { P4StandardRequest } from "../lib/backtest/liveContract";
import {
  FIXTURE_QUEUE_HOLD_MS,
  __abortFixtureTasks,
  __setFixtureDelay,
  resetFixtureState,
} from "../lib/backtest/fixtureStore";
import { scanBannedVisibleText } from "../lib/backtest/format";
import {
  makeBatch,
  makePrecheck,
  makeStandardRequest,
} from "../test/p4Fixtures";
import { ThemeProvider } from "../theme/ThemeProvider";
import { BacktestPage } from "./BacktestPage";

function renderPage(entry = "/backtest") {
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/backtest" element={<BacktestPage />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

function strategyRow(
  strategyId = "strategy-0001",
  name = "Live Strat",
) {
  return {
    schema: "strategy_version.v1",
    strategy_id: strategyId,
    status: "confirmed",
    name,
    created: "2026-07-25",
    imported_at: "2026-07-25T00:00:00Z",
    confirmed_at: "2026-07-25T00:00:00Z",
    content_sha256: "b".repeat(64),
    source_text: "schema: strategy.v1\n",
    spec_ref: null,
    based_on: null,
    based_on_sketch: null,
    based_on_insights: [],
    rationale: "r",
    unquantified_notes: [],
    universe: { contracts: ["NQ", "YM"], session: "eth" },
    parameters: [],
    spec: {
      universe_session: "eth",
      regime_separation_percentile: 50,
      regime_slope_percentile: 50,
      pullback_ema_period: 18,
      entry_layers: 3,
      signal_bars: [],
      target_r_multiple: 1,
      stop_offset_ticks: 1,
    },
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface LiveHandlers {
  strategies?: unknown[];
  list?: unknown;
  precheck?: (
    request: P4StandardRequest,
    signal: AbortSignal | null,
  ) => Response | Promise<Response>;
  submit?: (request: P4StandardRequest) => Response | Promise<Response>;
  detail?: (batchId: string) => Response | Promise<Response>;
  cancel?: (batchId: string) => Response | Promise<Response>;
}

function mockLiveServer(handlers: LiveHandlers = {}) {
  const calls: Array<{
    url: string;
    method: string;
    body: unknown;
  }> = [];
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body =
        typeof init?.body === "string" ? JSON.parse(init.body) : null;
      calls.push({ url, method, body });

      if (url.endsWith("/api/v1/strategies?status=confirmed")) {
        const versions = handlers.strategies ?? [strategyRow()];
        return json({
          schema: "strategy_version_list.v1",
          count: versions.length,
          versions,
        });
      }
      if (url.endsWith("/api/v1/batches/precheck")) {
        const request = body as P4StandardRequest;
        return (
          handlers.precheck?.(
            request,
            (init?.signal as AbortSignal | null) ?? null,
          ) ?? json(makePrecheck(request))
        );
      }
      if (url.endsWith("/api/v1/batches/submit")) {
        const request = body as P4StandardRequest;
        return (
          handlers.submit?.(request) ??
          json(makeBatch(request, ["queued"]))
        );
      }
      if (url.endsWith("/cancel-queued")) {
        const batchId = url.split("/").at(-2) ?? "";
        return (
          handlers.cancel?.(decodeURIComponent(batchId)) ??
          json({ detail: "not configured" }, 503)
        );
      }
      if (url.endsWith("/api/v1/batches/jobs")) {
        return json(
          handlers.list ?? {
            schema: "batch_job_list.v2",
            count: 0,
            batches: [],
          },
        );
      }
      if (url.includes("/api/v1/batches/jobs/")) {
        const batchId = decodeURIComponent(url.split("/").at(-1) ?? "");
        return (
          handlers.detail?.(batchId) ??
          json({ detail: "not configured" }, 503)
        );
      }
      return json({ detail: "not found" }, 404);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

async function selectLiveStrategy(user: ReturnType<typeof userEvent.setup>) {
  renderPage();
  await user.click(
    await screen.findByTestId("strategy-chip-strategy-0001"),
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(async () => {
  vi.unstubAllGlobals();
  __abortFixtureTasks();
  resetFixtureState();
  __setFixtureDelay(null);
  vi.useRealTimers();
  await Promise.resolve();
  await Promise.resolve();
});

describe("P4 normal live seam", () => {
  it("prechecks and submits the same exact request with full capital and no engineering fields", async () => {
    const submitted: P4StandardRequest[] = [];
    const { calls } = mockLiveServer({
      submit: (request) => {
        submitted.push(request);
        return json(makeBatch(request, ["queued"]));
      },
    });
    const user = userEvent.setup();
    await selectLiveStrategy(user);
    await screen.findByTestId("live-precheck-ready");

    const precheckCall = calls.find((call) =>
      call.url.endsWith("/api/v1/batches/precheck"),
    );
    const precheckRequest = precheckCall?.body as P4StandardRequest;
    expect(Object.keys(precheckRequest)).toEqual([
      "strategy_versions",
      "symbols",
      "range_start",
      "range_end",
      "execution_assumptions",
      "duplicate_acknowledgements",
    ]);
    expect(
      precheckRequest.execution_assumptions.initial_capital_usd,
    ).toBe(100_000);
    expect(
      precheckRequest.execution_assumptions.commission_per_side_by_symbol,
    ).toEqual({ NQ: 2.5 });
    expect(JSON.stringify(precheckRequest)).not.toMatch(
      /validation_run|session_name|skip_nautilus_replay|initial_capital":/,
    );

    await user.click(screen.getByTestId("start-backtest"));
    await screen.findByTestId("live-progress-region");
    expect(submitted).toHaveLength(1);
    expect(submitted[0]).toEqual(precheckRequest);
  });

  it("A pending → form B → late A cannot replace B", async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    const requests: P4StandardRequest[] = [];
    let count = 0;
    mockLiveServer({
      precheck: (request) => {
        requests.push(request);
        count += 1;
        if (count === 1) {
          return first.promise;
        }
        if (count === 2) {
          return second.promise;
        }
        return json(makePrecheck(request));
      },
    });
    const user = userEvent.setup();
    await selectLiveStrategy(user);
    await waitFor(() => {
      expect(count).toBe(1);
    });
    await user.click(screen.getByRole("button", { name: "YM" }));
    await waitFor(() => {
      expect(count).toBe(2);
    });

    second.resolve(json(makePrecheck(requests[1])));
    await waitFor(() => {
      expect(screen.getByTestId("live-precheck-ready")).toHaveTextContent(
        "已核實 2 次回測",
      );
    });
    first.resolve(json(makePrecheck(requests[0])));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("live-precheck-ready")).toHaveTextContent(
      "已核實 2 次回測",
    );
  });

  it("ready A becomes fail-closed immediately after identity drift", async () => {
    const next = deferred<Response>();
    let count = 0;
    mockLiveServer({
      precheck: (request) => {
        count += 1;
        return count === 1
          ? json(makePrecheck(request))
          : next.promise;
      },
    });
    const user = userEvent.setup();
    await selectLiveStrategy(user);
    await screen.findByTestId("live-precheck-ready");
    expect(screen.getByTestId("start-backtest")).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "YM" }));
    expect(screen.getByTestId("start-backtest")).toBeDisabled();
    expect(
      await screen.findByTestId("live-precheck-loading"),
    ).toBeInTheDocument();
  });

  it("acknowledges the exact duplicate set, rechecks, and clears ack on assumption drift", async () => {
    const requests: P4StandardRequest[] = [];
    mockLiveServer({
      precheck: (request) => {
        requests.push(request);
        return json(
          makePrecheck(request, {
            duplicate:
              request.duplicate_acknowledgements.length > 0
                ? "acknowledged"
                : "exact",
          }),
        );
      },
    });
    const user = userEvent.setup();
    await selectLiveStrategy(user);
    await screen.findByTestId("acknowledge-live-duplicates");
    expect(screen.getByTestId("start-backtest")).toBeDisabled();

    await user.click(screen.getByTestId("acknowledge-live-duplicates"));
    await waitFor(() => {
      expect(requests.at(-1)?.duplicate_acknowledgements).toEqual([
        {
          strategy_version: "strategy-0001",
          symbol: "NQ",
          range_start: requests[0].range_start,
          range_end: requests[0].range_end,
        },
      ]);
    });
    await waitFor(() => {
      expect(screen.getByTestId("start-backtest")).toBeEnabled();
    });

    await user.click(screen.getByRole("button", { name: /資金與成交假設/ }));
    const fee = screen.getByLabelText("NQ 手續費");
    await user.clear(fee);
    await user.type(fee, "3");
    await waitFor(() => {
      expect(requests.at(-1)?.duplicate_acknowledgements).toEqual([]);
    });
  });

  it("submit 409 updates precheck truth and creates no active panel", async () => {
    mockLiveServer({
      submit: (request) =>
        json(makePrecheck(request, { duplicate: "exact" }), 409),
    });
    const user = userEvent.setup();
    await selectLiveStrategy(user);
    await screen.findByTestId("live-precheck-ready");
    await user.click(screen.getByTestId("start-backtest"));

    expect(await screen.findByTestId("live-submit-error")).toHaveTextContent(
      "未有新回測開始",
    );
    expect(screen.queryByTestId("live-progress-region")).not.toBeInTheDocument();
    expect(screen.getByTestId("live-precheck-ready")).toHaveTextContent(
      "有項目要先處理",
    );
  });

  it("late poll cannot overwrite a newer queued-only cancel response", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const poll = deferred<Response>();
    let submittedRequest: P4StandardRequest | null = null;
    mockLiveServer({
      submit: (request) => {
        submittedRequest = request;
        return json(makeBatch(request, ["running", "queued"]));
      },
      detail: () => poll.promise,
      cancel: () =>
        json(
          makeBatch(
            submittedRequest!,
            ["running", "cancelled"],
          ),
        ),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await selectLiveStrategy(user);
    await user.click(screen.getByRole("button", { name: "YM" }));
    await screen.findByTestId("live-precheck-ready");
    await user.click(screen.getByTestId("start-backtest"));
    await screen.findByTestId("live-progress-region");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    await user.click(screen.getByTestId("live-cancel-queued"));
    await waitFor(() => {
      expect(screen.getByTestId("live-progress-summary")).toHaveTextContent(
        "已取消 1",
      );
    });
    poll.resolve(
      json(makeBatch(submittedRequest!, ["running", "queued"])),
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("live-progress-summary")).toHaveTextContent(
      "已取消 1",
    );
    expect(screen.getByTestId("live-cancel-queued")).toBeDisabled();
  });

  it.each([
    ["completed", ["completed"] as const, "完成 1"],
    ["failed", ["failed"] as const, "失敗 1"],
    ["cancelled", ["cancelled"] as const, "已取消 1"],
    [
      "partial",
      ["completed", "failed"] as const,
      "完成 1",
    ],
  ])("polls into %s terminal truth and then stops", async (
    _name,
    terminalStatuses,
    expected,
  ) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let requestSeen: P4StandardRequest | null = null;
    let detailCalls = 0;
    mockLiveServer({
      submit: (request) => {
        requestSeen = request;
        const initial = terminalStatuses.map((status) =>
          status === "cancelled" ? "queued" : "running",
        );
        return json(makeBatch(request, [...initial]));
      },
      detail: () => {
        detailCalls += 1;
        return json(makeBatch(requestSeen!, [...terminalStatuses]));
      },
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await selectLiveStrategy(user);
    if (terminalStatuses.length === 2) {
      await user.click(screen.getByRole("button", { name: "YM" }));
    }
    await screen.findByTestId("live-precheck-ready");
    await user.click(screen.getByTestId("start-backtest"));
    await screen.findByTestId("live-progress-region");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    await waitFor(() => {
      expect(screen.getByTestId("live-progress-summary")).toHaveTextContent(
        expected,
      );
    });
    const stoppedAt = detailCalls;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(detailCalls).toBe(stoppedAt);
  });

  it("shows baseline zero truth, no fake ETA, full error, and exact clipboard", async () => {
    const clipboard = vi.fn(async () => undefined);
    let requestSeen: P4StandardRequest | null = null;
    mockLiveServer({
      submit: (request) => {
        requestSeen = request;
        return json(makeBatch(request, ["running"]));
      },
      detail: () =>
        json(makeBatch(requestSeen!, ["failed"])),
    });
    const user = userEvent.setup();
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboard },
    });
    await selectLiveStrategy(user);
    await screen.findByTestId("live-precheck-ready");
    await user.click(screen.getByTestId("start-backtest"));
    expect(await screen.findByText("準備中")).toBeInTheDocument();
    expect(screen.getByTestId("live-progress-table")).toHaveTextContent(
      "0R / USD 0",
    );
    expect(screen.getByTestId("live-progress-region").textContent).not.toMatch(
      /估計剩餘|約 \d+ 分鐘/,
    );

    await waitFor(
      () => {
        expect(
          screen.getByRole("button", { name: "看原因 · NQ" }),
        ).toBeInTheDocument();
      },
      { timeout: 2500 },
    );
    await user.click(screen.getByRole("button", { name: "看原因 · NQ" }));
    const full = "RuntimeError: NQ minute coverage unavailable";
    expect(screen.getByTestId("live-error-full-job-1").textContent).toBe(full);
    await user.click(screen.getByRole("button", { name: "複製全文" }));
    expect(clipboard).toHaveBeenCalledWith(full);
    expect(await screen.findByText("已複製")).toBeInTheDocument();
  });

  it("history only lists exact standard snapshots and rerun restores without submit or ack", async () => {
    const historyAck = {
      strategy_version: "strategy-0001",
      symbol: "NQ",
      range_start: "2026-05-01T00:00:00Z",
      range_end: "2026-05-02T00:00:00Z",
    };
    const historyRequest = makeStandardRequest({
      range_start: "2026-05-01T00:00:00Z",
      range_end: "2026-05-02T00:00:00Z",
      execution_assumptions: {
        initial_capital_usd: 75_000,
        commission_per_side_by_symbol: { NQ: 3.25 },
        slippage_ticks: {
          breakout_entry: 2,
          stop_exit: 3,
          target_exit: 1,
          day_end_exit: 2,
        },
      },
      duplicate_acknowledgements: [historyAck],
    });
    const standard = makeBatch(historyRequest, ["completed"], "standard");
    const engineering = structuredClone(standard);
    engineering.batch_id = "engineering";
    engineering.request = { validation_run: false, symbols: ["NQ"] };
    delete engineering.jobs[0].execution_assumptions;
    engineering.jobs[0].assumptions = null;
    const { calls } = mockLiveServer({
      strategies: [
        {
          ...strategyRow(),
          universe: { contracts: ["YM"], session: "eth" },
        },
      ],
      list: {
        schema: "batch_job_list.v2",
        count: 2,
        batches: [standard, engineering],
      },
    });
    const user = userEvent.setup();
    renderPage();
    expect(
      await screen.findByTestId("live-history-standard"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("live-history-engineering"),
    ).not.toBeInTheDocument();

    const precheckCountBefore = calls.filter((call) =>
      call.url.endsWith("/api/v1/batches/precheck"),
    ).length;
    await user.click(screen.getByTestId("live-rerun-standard"));
    expect(screen.getByRole("button", { name: "NQ" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: /資金與成交假設/ }));
    expect(screen.getByLabelText("初始資金")).toHaveValue(75_000);
    expect(screen.getByLabelText("NQ 手續費")).toHaveValue(3.25);
    await waitFor(() => {
      const prechecks = calls.filter((call) =>
        call.url.endsWith("/api/v1/batches/precheck"),
      );
      const rerunPrechecks = prechecks.slice(precheckCountBefore);
      expect(rerunPrechecks.length).toBeGreaterThan(0);
      for (const call of rerunPrechecks) {
        expect(call.body).toMatchObject({
          duplicate_acknowledgements: [],
          execution_assumptions: {
            initial_capital_usd: 75_000,
          },
        });
      }
    });
    expect(
      calls.filter((call) => call.url.endsWith("/api/v1/batches/submit")),
    ).toHaveLength(0);
  });

  it("keeps a missing catalog name neutral in history and the restored form", async () => {
    const request = makeStandardRequest();
    mockLiveServer({
      strategies: [],
      list: {
        schema: "batch_job_list.v2",
        count: 1,
        batches: [makeBatch(request, ["completed"], "missing-catalog")],
      },
    });
    const user = userEvent.setup();
    renderPage();

    expect(
      await screen.findByTestId("live-history-names-missing-catalog"),
    ).toHaveTextContent("策略名稱暫未取得");
    await user.click(screen.getByTestId("live-rerun-missing-catalog"));
    expect(
      screen.getByTestId("unresolved-strategy-strategy-0001"),
    ).toHaveTextContent("策略名稱暫未取得");
    expect(
      screen.getByTestId("unresolved-strategy-strategy-0001"),
    ).toHaveAttribute("title", "strategy-0001");
    expect(await screen.findByTestId("live-precheck-ready")).toBeInTheDocument();
  });

  it("malformed list is an error rather than partial success", async () => {
    const request = makeStandardRequest();
    const malformed = makeBatch(request, ["completed"]);
    malformed.summary.completed = 0;
    mockLiveServer({
      list: {
        schema: "batch_job_list.v2",
        count: 1,
        batches: [malformed],
      },
    });
    renderPage();
    expect(await screen.findByTestId("history-error")).toBeInTheDocument();
    expect(screen.queryByTestId("history-empty")).not.toBeInTheDocument();
  });

  it("keeps normal visible chrome free of forbidden technical terms", async () => {
    const request = makeStandardRequest();
    mockLiveServer({
      list: {
        schema: "batch_job_list.v2",
        count: 1,
        batches: [makeBatch(request, ["completed"])],
      },
    });
    const user = userEvent.setup();
    await selectLiveStrategy(user);
    await screen.findByTestId("live-precheck-ready");
    const visible = document.body.textContent ?? "";
    expect(scanBannedVisibleText(visible)).toEqual([]);
  });
});

describe("P4 owner-review fixture isolation and regression", () => {
  it("is zero-fetch and keeps approved 2×2/full-capital form truth", async () => {
    const fetchMock = vi.fn(async () => json({ detail: "unexpected" }, 500));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage("/backtest?scenario=owner-review");

    expect(screen.getByTestId("preview-line")).toHaveTextContent(
      "2 個策略 × 2 個合約 ＝ 4 次回測",
    );
    expect(screen.getByTestId("capital-per-unit")).toHaveTextContent(
      "USD 100,000",
    );
    expect(screen.getByTestId("capital-per-unit")).toHaveTextContent(
      "4 個獨立帳戶",
    );
    await user.click(screen.getByRole("button", { name: /資金與成交假設/ }));
    expect(screen.getByLabelText("NQ 手續費")).toHaveValue(2.5);
    expect(screen.getByLabelText("YM 手續費")).toHaveValue(2.5);
    expect(screen.getByLabelText("突破 滑點")).toHaveValue(1);
    expect(screen.getByLabelText("止蝕 滑點")).toHaveValue(2);
    expect(screen.getByLabelText("目標 滑點")).toHaveValue(0);
    expect(screen.getByLabelText("日終 滑點")).toHaveValue(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  /** D0 ([175]): the rendered warm-up check must carry the corrected copy. */
  it("renders the 95-day warm-up truth and a later suggested start", () => {
    const fetchMock = vi.fn(async () => json({}, 500));
    vi.stubGlobal("fetch", fetchMock);
    renderPage("/backtest?scenario=owner-review");
    const setup = screen.getByRole("region", { name: "設定與開始" });
    expect(within(setup).getByText(/95 個已收市交易日/)).toBeInTheDocument();
    expect(within(setup).getByText(/推後至 2026-06-16/)).toBeInTheDocument();
    expect(within(setup).getByText(/建議起點：2026-06-16/)).toBeInTheDocument();
    expect(setup.textContent ?? "").not.toContain("提前");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps human session labels and no forbidden visible terms", () => {
    const fetchMock = vi.fn(async () => json({}, 500));
    vi.stubGlobal("fetch", fetchMock);
    renderPage("/backtest?scenario=owner-review");
    const setup = screen.getByRole("region", { name: "設定與開始" });
    expect(within(setup).getByText(/夜盤全時段/)).toBeInTheDocument();
    expect(within(setup).getByText(/美股時段/)).toBeInTheDocument();
    const hits = scanBannedVisibleText(document.body.textContent ?? "");
    expect(hits).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("starts four fixture cells and keeps the approved cancel control", async () => {
    const fetchMock = vi.fn(async () => json({}, 500));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage("/backtest?scenario=owner-review");
    await user.click(screen.getByTestId("start-backtest"));
    expect(
      await screen.findByRole("region", { name: "正在回測" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "取消未開始嘅回測（正在跑嗰個會跑完）",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText("停止")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rerun restores fixture snapshot but never auto-acknowledges", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    __setFixtureDelay(
      (ms) =>
        new Promise((resolve) => {
          window.setTimeout(resolve, ms);
        }),
    );
    const fetchMock = vi.fn(async () => json({}, 500));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderPage("/backtest?scenario=owner-review");
    await user.click(screen.getByTestId("start-backtest"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FIXTURE_QUEUE_HOLD_MS + 2000);
    });
    await waitFor(() => {
      expect(screen.getByTestId("duplicate-check")).toHaveTextContent(
        "已有相同",
      );
    });
    const history = screen.getByRole("region", { name: "最近嘅回測" });
    await user.click(
      within(history).getByRole("button", { name: "再跑一次" }),
    );
    expect(screen.getByTestId("force-duplicate")).not.toBeChecked();
    expect(screen.getByTestId("start-backtest")).toBeDisabled();
    await user.click(screen.getByTestId("force-duplicate"));
    expect(screen.getByTestId("start-backtest")).toBeEnabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("queue hold can finish with completed + failed + cancelled and exact YM failure", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    __setFixtureDelay(
      (ms) =>
        new Promise((resolve) => {
          window.setTimeout(resolve, ms);
        }),
    );
    vi.stubGlobal("fetch", vi.fn(async () => json({}, 500)));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderPage("/backtest?scenario=owner-review");
    await user.click(screen.getByTestId("start-backtest"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    await user.click(screen.getByTestId("cancel-queued"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FIXTURE_QUEUE_HOLD_MS + 1000);
    });
    await waitFor(() => {
      const summary = screen.getByTestId("progress-summary");
      expect(summary).toHaveTextContent(/完成 [1-9]/);
      expect(summary).toHaveTextContent(/失敗 [1-9]/);
      expect(summary).toHaveTextContent(/已取消 [1-9]/);
    });
    const errorButtons = screen.getAllByRole("button", {
      name: "看原因 · YM",
    });
    await user.click(errorButtons[0]);
    const full =
      "coverage: YM 2026-05-08 missing bars — gap 09:30–10:15 UTC\nfix: extend download or exclude trading day";
    expect(screen.getByText(/coverage: YM/).textContent).toBe(full);
    expect(screen.getByText(/coverage: YM/).textContent).not.toMatch(
      /coverage: NQ/,
    );
    const resultLink = screen.getAllByRole("link", { name: "看結果" })[0];
    expect(resultLink).toHaveAttribute(
      "href",
      expect.stringMatching(/scenario=owner-review/),
    );
  });
});
