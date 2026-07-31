import { StrictMode } from "react";
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

import { INSIGHT_STORAGE_KEY } from "../insightStore";
import { ownerReviewCoverageBody } from "../catalog/fixtureCatalog";
import { SKETCH_STORAGE_KEY } from "../sketch/types";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { StrategiesPage } from "../../pages/StrategiesPage";

type CatalogMode = "ready" | "loading" | "empty" | "503" | "network" | "invalid";

const PRIMARY_ONLY_YAML = `schema: strategy.v1
meta:
  name: NQ shared catalog probe
rationale: edge
unquantified_notes: []
universe:
  primary_instrument: NQ
  asset_class: equity_index_futures
  contracts: [NQ]
  expansion_rationale: {}
  session: eth
`;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function invalidCatalogBody(): unknown {
  const body = ownerReviewCoverageBody();
  const first = { ...body.contracts[0] };
  delete first.display_name;
  return {
    ...body,
    contracts: [first, ...body.contracts.slice(1)],
  };
}

function requestPath(input: RequestInfo | URL): string {
  const url = new URL(String(input), "http://catalog.test");
  return `${url.pathname}${url.search}`;
}

function coveragePaths(
  fetchMock: ReturnType<typeof vi.fn>,
): string[] {
  return fetchMock.mock.calls
    .map((call) => requestPath(call[0] as RequestInfo | URL))
    .filter((path) => path.startsWith("/api/v1/data/coverage"));
}

function writePaths(
  fetchMock: ReturnType<typeof vi.fn>,
): string[] {
  return fetchMock.mock.calls.flatMap((call) => {
    const path = requestPath(call[0] as RequestInfo | URL);
    const init = call[1] as RequestInit | undefined;
    const method = (init?.method ?? "GET").toUpperCase();
    if (method !== "POST") {
      return [];
    }
    if (
      path.startsWith("/api/v1/sketches") ||
      path.startsWith("/api/v1/strategies/import") ||
      path.includes("/confirm")
    ) {
      return [path];
    }
    return [];
  });
}

function installApi(mode: CatalogMode) {
  let resolveLoading!: (response: Response) => void;
  const loading = new Promise<Response>((resolve) => {
    resolveLoading = resolve;
  });

  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const path = requestPath(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (path.startsWith("/api/v1/data/coverage")) {
        switch (mode) {
          case "loading":
            return loading;
          case "empty":
            return jsonResponse({
              schema: "data_coverage.v1",
              count: 0,
              contracts: [],
            });
          case "503":
            return jsonResponse({ detail: "catalog unavailable" }, 503);
          case "network":
            throw new TypeError("offline");
          case "invalid":
            return jsonResponse(invalidCatalogBody());
          default:
            return jsonResponse(ownerReviewCoverageBody());
        }
      }
      if (
        path === "/api/v1/strategies/validate" &&
        method === "POST"
      ) {
        return jsonResponse({
          schema: "strategy_validation.v1",
          valid: true,
          issue_count: 0,
          issues: [],
          report_text: "",
          name: "shared catalog probe",
          universe: { contracts: ["NQ"], session: "eth" },
        });
      }
      if (path === "/api/v1/strategies" && method === "GET") {
        return jsonResponse({
          schema: "strategy_version_list.v1",
          count: 0,
          versions: [],
        });
      }
      return new Response("not found", { status: 404 });
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, resolveLoading };
}

function renderPage(
  entry = "/strategies",
  strict = false,
) {
  const page = (
    <ThemeProvider>
      <MemoryRouter initialEntries={[entry]}>
        <StrategiesPage />
      </MemoryRouter>
    </ThemeProvider>
  );
  return render(strict ? <StrictMode>{page}</StrictMode> : page);
}

async function switchTab(
  user: ReturnType<typeof userEvent.setup>,
  name: RegExp,
) {
  const tab = screen.getByRole("tab", { name });
  await user.click(tab);
  expect(tab).toHaveAttribute("aria-selected", "true");
}

beforeEach(() => {
  localStorage.removeItem(SKETCH_STORAGE_KEY);
  localStorage.removeItem(INSIGHT_STORAGE_KEY);
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.removeItem(SKETCH_STORAGE_KEY);
  localStorage.removeItem(INSIGHT_STORAGE_KEY);
});

describe("P2 shared catalog request", () => {
  it("normal mounted workbench uses one exact catalog request across all tab switches", async () => {
    const { fetchMock } = installApi("ready");
    const user = userEvent.setup();
    const firstMount = renderPage("/strategies", true);

    const sketchSelect = await screen.findByTestId("instrument-select");
    expect(
      within(sketchSelect).getByRole("option", {
        name: "E-mini Nasdaq-100（NQ）",
      }),
    ).toBeInTheDocument();
    expect(coveragePaths(fetchMock)).toEqual([
      "/api/v1/data/coverage?view=catalog",
    ]);

    await switchTab(user, /② 量化確認/);
    fireEvent.change(
      screen.getByRole("textbox", { name: "strategy.v1 YAML" }),
      { target: { value: PRIMARY_ONLY_YAML } },
    );
    expect(
      await within(screen.getByTestId("universe-members")).findByText(
        /E-mini Nasdaq-100/,
      ),
    ).toBeInTheDocument();

    await switchTab(user, /③ 版本庫/);
    await screen.findByText(/仲未有版本/);
    await switchTab(user, /④ 市場洞察/);
    expect(screen.queryByTestId("catalog-loading")).not.toBeInTheDocument();
    const insightSelect = screen.getByTestId("instrument-select");
    expect(
      within(insightSelect).getByRole("option", {
        name: "E-mini Nasdaq-100（NQ）",
      }),
    ).toBeInTheDocument();

    await switchTab(user, /① 草圖/);
    expect(screen.queryByTestId("catalog-loading")).not.toBeInTheDocument();
    expect(screen.getByTestId("instrument-select")).toBeInTheDocument();
    expect(coveragePaths(fetchMock)).toEqual([
      "/api/v1/data/coverage?view=catalog",
    ]);

    firstMount.unmount();
    const secondMount = renderPage("/strategies", true);
    await screen.findByTestId("instrument-select");
    expect(coveragePaths(fetchMock)).toEqual([
      "/api/v1/data/coverage?view=catalog",
      "/api/v1/data/coverage?view=catalog",
    ]);
    secondMount.unmount();
  });

  it("owner-review keeps coverage requests at zero across all four tabs", async () => {
    const fetchMock = vi.fn(async () => new Response("unexpected", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage("/strategies?scenario=owner-review", true);

    expect(await screen.findByTestId("instrument-select")).toBeInTheDocument();
    await switchTab(user, /② 量化確認/);
    await switchTab(user, /③ 版本庫/);
    expect(
      screen.getByTestId("owner-review-library-isolated"),
    ).toBeInTheDocument();
    await switchTab(user, /④ 市場洞察/);
    await switchTab(user, /① 草圖/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["loading", "loading", "catalog-loading"],
    ["empty", "empty", "catalog-empty"],
    ["503", "503", "catalog-error"],
    ["network", "network", "catalog-error"],
    ["invalid", "invalid", "catalog-invalid"],
  ] as const)(
    "fail-closed %s catalog state keeps every catalog-dependent write at zero",
    async (_label, mode, stateTestId) => {
      const { fetchMock, resolveLoading } = installApi(mode);
      const user = userEvent.setup();
      const mounted = renderPage();

      const stateMessage =
        mode === "loading"
          ? screen.getByTestId(stateTestId)
          : await screen.findByTestId(stateTestId);
      expect(stateMessage).toBeInTheDocument();
      expect(await screen.findByTestId("sketch-export")).toBeDisabled();

      await switchTab(user, /② 量化確認/);
      fireEvent.change(
        screen.getByRole("textbox", { name: "strategy.v1 YAML" }),
        { target: { value: PRIMARY_ONLY_YAML } },
      );
      await user.click(screen.getByRole("button", { name: "驗證" }));
      expect(await screen.findByTestId("confirm-adopt")).toBeDisabled();

      await switchTab(user, /③ 版本庫/);
      await screen.findByText(/仲未有版本/);
      await switchTab(user, /④ 市場洞察/);
      expect(screen.getByTestId(stateTestId)).toBeInTheDocument();
      expect(
        screen.getByRole("button", {
          name: "⬇ 匯出圖文包（kind: insight）",
        }),
      ).toBeDisabled();
      expect(screen.getByTestId("insight-import")).toBeDisabled();

      expect(coveragePaths(fetchMock)).toEqual([
        "/api/v1/data/coverage?view=catalog",
      ]);
      expect(writePaths(fetchMock)).toEqual([]);

      mounted.unmount();
      if (mode === "loading") {
        await act(async () => {
          resolveLoading(jsonResponse(ownerReviewCoverageBody()));
          await Promise.resolve();
        });
        expect(coveragePaths(fetchMock)).toHaveLength(1);
      }
    },
  );

  it("drops a late catalog response after unmount and a fresh mount requests again", async () => {
    const deferred: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL): Promise<Response> => {
        const path = requestPath(input);
        if (!path.startsWith("/api/v1/data/coverage")) {
          return new Response("not found", { status: 404 });
        }
        return new Promise<Response>((resolve) => {
          deferred.push(resolve);
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const firstMount = renderPage();
    expect(screen.getByTestId("catalog-loading")).toBeInTheDocument();
    await waitFor(() => {
      expect(coveragePaths(fetchMock)).toHaveLength(1);
    });
    firstMount.unmount();
    await act(async () => {
      deferred[0](jsonResponse(ownerReviewCoverageBody()));
      await Promise.resolve();
    });

    const secondMount = renderPage();
    expect(screen.getByTestId("catalog-loading")).toBeInTheDocument();
    await waitFor(() => {
      expect(coveragePaths(fetchMock)).toHaveLength(2);
    });
    await act(async () => {
      deferred[1](jsonResponse(ownerReviewCoverageBody()));
      await Promise.resolve();
    });
    expect(await screen.findByTestId("instrument-select")).toBeInTheDocument();
    expect(coveragePaths(fetchMock)).toEqual([
      "/api/v1/data/coverage?view=catalog",
      "/api/v1/data/coverage?view=catalog",
    ]);
    secondMount.unmount();
  });
});
