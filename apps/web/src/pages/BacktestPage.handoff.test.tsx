import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../theme/ThemeProvider";
import { BacktestPage } from "./BacktestPage";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderBacktest(entry: string) {
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

describe("BacktestPage data handoff prefill", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo) => {
        const url = String(input);
        if (url.includes("/api/v1/strategies")) {
          return json({ schema: "strategy_list.v1", count: 0, strategies: [] });
        }
        if (url.includes("/api/v1/batches")) {
          return json({ schema: "batch_list.v1", count: 0, batches: [] });
        }
        return json({ detail: "not found" }, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("prefills symbol and trading-day range from handoff query params", async () => {
    renderBacktest(
      "/backtest?symbol=YM&startDate=2026-05-05&endDate=2026-05-09&from=data",
    );
    await waitFor(() => {
      expect(screen.getByTestId("data-handoff-notice")).toHaveTextContent(
        /YM · 2026-05-05 → 2026-05-09/,
      );
    });
    // Symbol chips / checkboxes should include YM from handoff.
    expect(screen.getByTestId("data-handoff-notice").textContent).toContain(
      "YM",
    );
    // Range fields carry local datetime values stamped from trading-day labels
    // without shifting the calendar date. Asserted on the text the owner
    // actually reads, not on a DOM value they never see.
    const start = screen.getByTestId("range-start-field");
    expect(start.textContent?.startsWith("2026-05-05")).toBe(true);
  });

  it("ignores incomplete handoff params (fail closed — no invented dates)", async () => {
    renderBacktest("/backtest?symbol=NQ&startDate=2026-05-05");
    await waitFor(() => {
      expect(screen.getByText("回測")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("data-handoff-notice")).toBeNull();
  });
});
