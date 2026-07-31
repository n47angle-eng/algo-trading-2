import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../theme/ThemeProvider";
import { DataPage } from "./DataPage";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function knownCoverageRow(symbol = "NQ") {
  return {
    symbol,
    contract_id: `${symbol}-202609-CME`,
    display_name: `${symbol} Sep`,
    partition_count: 2,
    bar_count: 1000,
    first_timestamp: "2026-05-01T00:00:00Z",
    last_timestamp: "2026-05-20T00:00:00Z",
    roll_blackout_dates: [],
    owner_excluded_dates: [],
    quality: {
      report_count: 1,
      latest_error_count: 0,
      latest_report_id: "q1",
    },
    trading_day_coverage: {
      schema: "trading_day_coverage.v1",
      status: "known",
      session_name: "eth",
      first_trading_date: "2026-05-01",
      last_trading_date: "2026-05-20",
      trading_date_count: 10,
      complete_trading_date_count: 5,
      problem_trading_date_count: 2,
      pending_problem_trading_date_count: 1,
      owner_trusted_problem_trading_date_count: 0,
      owner_excluded_trading_date_count: 0,
      complete_trading_dates: [
        "2026-05-05",
        "2026-05-06",
        "2026-05-07",
        "2026-05-08",
        "2026-05-09",
      ],
      problem_trading_dates: ["2026-05-12", "2026-05-13"],
      pending_problem_trading_dates: ["2026-05-12"],
      owner_trusted_problem_trading_dates: [],
      owner_excluded_trading_dates: [],
      longest_complete_segment: {
        start_trading_date: "2026-05-05",
        end_trading_date: "2026-05-09",
        trading_date_count: 5,
      },
    },
    native_daily_coverage: {
      schema: "native_daily_coverage.v1",
      status: "known",
      available_trading_date_count: 10,
      missing_trading_date_count: 0,
    },
  };
}

function renderData(entry = "/data") {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/data" element={<DataPage />} />
          <Route path="/backtest" element={<div>backtest-page</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("DataPage P3→P4 handoff", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) {
          return json({
            schema: "data_coverage.v1",
            count: 1,
            contracts: [knownCoverageRow("NQ")],
          });
        }
        if (url.includes("/api/v1/data/quality-reports")) {
          return json({ schema: "quality_report_list.v1", count: 0, reports: [] });
        }
        return json({ detail: "not found" }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("surfaces complete dates from real API shape and links handoff to backtest", async () => {
    renderData();
    await waitFor(() => {
      expect(screen.getByText(/連續完整最長一段/)).toBeInTheDocument();
    });
    expect(screen.getByText(/2026-05-05 → 2026-05-09/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /帶可用日子去回測/ });
    expect(link.getAttribute("href")).toBe(
      "/backtest?symbol=NQ&startDate=2026-05-05&endDate=2026-05-09&from=data",
    );
  });

  it("fails closed when nested coverage is missing — no handoff, no fake clean days", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) {
          return json({
            schema: "data_coverage.v1",
            count: 1,
            contracts: [
              {
                ...knownCoverageRow("YM"),
                symbol: "YM",
                trading_day_coverage: undefined,
                native_daily_coverage: undefined,
              },
            ],
          });
        }
        if (url.includes("/api/v1/data/quality-reports")) {
          return json({ schema: "quality_report_list.v1", count: 0, reports: [] });
        }
        return json({}, 404);
      }),
    );
    renderData();
    await waitFor(() => {
      expect(
        screen.getByText(/交易日覆蓋暫時核實唔到/),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /暫時帶唔到去回測/ }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("link", { name: /帶可用日子去回測/ }),
    ).toBeNull();
    // Must not invent a complete-day count of zero as "all clean".
    expect(screen.queryByText(/可以直接回測：\s*0 日/)).toBeNull();
  });

  it("fails closed on coverage API error — no fabricated date list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo) => {
        const url = String(input);
        if (url.includes("/api/v1/data/coverage")) {
          return new Response("upstream failed", { status: 503 });
        }
        if (url.includes("/api/v1/data/quality-reports")) {
          return json({ schema: "quality_report_list.v1", count: 0, reports: [] });
        }
        return json({}, 404);
      }),
    );
    renderData();
    await waitFor(() => {
      expect(
        screen.getByRole("alert"),
      ).toHaveTextContent(/唔會顯示假嘅完整日清單/);
    });
    expect(screen.queryByText(/2026-05-05 → 2026-05-09/)).toBeNull();
  });

  it("shows pending problem days for Owner decision without free-typing dates", async () => {
    renderData();
    await waitFor(() => {
      expect(screen.getByText("等你裁決")).toBeInTheDocument();
    });
    const card = screen.getByLabelText(/NQ 覆蓋/);
    expect(within(card).getByText("2026-05-12")).toBeInTheDocument();
    expect(
      within(card).getByRole("button", { name: /信呢日數據/ }),
    ).toBeInTheDocument();
    expect(
      within(card).getByRole("button", { name: /唔好回測呢日/ }),
    ).toBeInTheDocument();
  });
});
