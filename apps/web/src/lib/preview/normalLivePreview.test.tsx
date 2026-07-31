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

import { ThemeProvider } from "../../theme/ThemeProvider";
import { StrategiesPage } from "../../pages/StrategiesPage";
import { localDatetimeToUtcIso } from "../backtest/time";
import { ownerReviewCoverageBody } from "../catalog/fixtureCatalog";
import { INSIGHT_STORAGE_KEY } from "../insightStore";
import { SKETCH_STORAGE_KEY } from "../sketch/types";
import { createOwnerReviewPreviewFixture } from "./ownerReviewFixture";

const VALID_NQ_YAML = `schema: strategy.v1
meta:
  name: NQ preview exact source
  based_on_sketch: sketch-20260728-01
  based_on_sketch_origin: workshop
rationale: deterministic preview probe
unquantified_notes: []
universe:
  primary_instrument: NQ
  asset_class: equity_index_futures
  contracts: [NQ]
  expansion_rationale: {}
  session: eth
`;

const VALID_MULTI_YAML = `schema: strategy.v1
meta:
  name: NQ YM preview invalidation
  based_on_sketch: sketch-20260728-01
  based_on_sketch_origin: workshop
rationale: deterministic preview probe
unquantified_notes: []
universe:
  primary_instrument: NQ
  asset_class: equity_index_futures
  contracts: [NQ, YM]
  expansion_rationale:
    YM: 同 class test
  session: eth
`;

const VALID_ES_YAML = `schema: strategy.v1
meta:
  name: ES catalog-only preview
  based_on_sketch: sketch-20260728-01
  based_on_sketch_origin: workshop
rationale: no symbol hardcode
unquantified_notes: []
universe:
  primary_instrument: ES
  asset_class: equity_index_futures
  contracts: [ES]
  expansion_rationale: {}
  session: eth
`;

interface PreviewRequestBody {
  schema: string;
  source_text: string;
  filename: string | null;
  contract_id: string;
  range_start: string;
  range_end: string;
  assumptions: {
    initial_capital_usd: number;
    commission_per_side: number;
    slippage_ticks: number;
  };
}

type PreviewResponder = (
  body: PreviewRequestBody,
  index: number,
) => Response | Promise<Response>;

type ValidationResponder = (
  sourceText: string,
  index: number,
) => Response | Promise<Response>;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function validationBody(
  valid = true,
  message = "preview valid",
): Record<string, unknown> {
  return {
    schema: "strategy_validation.v1",
    valid,
    issue_count: valid ? 0 : 1,
    issues: valid
      ? []
      : [
          {
            path: "meta.name",
            message,
            fix: "validate the current source",
            layer: "semantic",
            line: `meta.name: ${message}`,
          },
        ],
    report_text: valid ? "" : `meta.name: ${message}`,
    name: valid ? message : undefined,
    universe: valid ? { contracts: ["NQ"], session: "eth" } : undefined,
  };
}

function requestPath(input: RequestInfo | URL): string {
  const url = new URL(String(input), "http://preview.test");
  return `${url.pathname}${url.search}`;
}

function successBody(
  contractId = "NQ-202609-CME",
  warning = "fixture success",
): Record<string, unknown> {
  const body = JSON.parse(
    JSON.stringify(createOwnerReviewPreviewFixture()),
  ) as Record<string, unknown>;
  const charts = body.charts as Record<string, Record<string, unknown>>;
  for (const chart of Object.values(charts)) {
    chart.contract_id = contractId;
  }
  body.warnings = warning ? [warning] : [];
  return body;
}

function failureBody(valid: boolean): Record<string, unknown> {
  return {
    schema: "backtest_preview.v1",
    run_scope: "dry_run",
    request_fingerprint: {
      algorithm: "sha256",
      digest: "e".repeat(64),
    },
    validation: {
      schema: "strategy_validation.v1",
      valid,
      issue_count: valid ? 0 : 1,
      issues: valid
        ? []
        : [
            {
              path: "range_start",
              message: "preview range invalid",
              fix: "pick an exact range",
              layer: "format",
              line: "range_start: preview range invalid — pick an exact range",
            },
          ],
      report_text: valid
        ? ""
        : "range_start: preview range invalid — pick an exact range",
    },
    funnel: null,
    decision_evidence: [],
    rejection_evidence: [],
    evidence_summary: {
      availability: "unavailable",
      complete: false,
      evaluation_count: 0,
      rejection_count: 0,
      layer_reached_counts: {},
      blocking_condition_counts: {},
      deepest_layer: null,
      trade_count: 0,
    },
    charts: {},
    persisted: false,
    warnings: [],
    errors: [
      valid
        ? "ValueError: canonical bars unavailable"
        : "range_start: preview range invalid",
    ],
  };
}

function esCatalogBody(): unknown {
  const body = ownerReviewCoverageBody();
  return {
    ...body,
    count: 1,
    contracts: [
      {
        ...body.contracts[0],
        symbol: "ES",
        contract_id: "ES-202609-CME",
        display_name: "E-mini S&P 500",
      },
    ],
  };
}

function installApi(
  responder: PreviewResponder,
  catalogBody: unknown = ownerReviewCoverageBody(),
  validationResponder: ValidationResponder = () =>
    jsonResponse(validationBody()),
) {
  let previewIndex = 0;
  let validationIndex = 0;
  const fetchMock = vi.fn(
    async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      const path = requestPath(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (path === "/api/v1/data/coverage?view=catalog") {
        return jsonResponse(catalogBody);
      }
      if (
        path === "/api/v1/strategies/validate" &&
        method === "POST"
      ) {
        const body = JSON.parse(String(init?.body)) as {
          source_text: string;
        };
        const index = validationIndex;
        validationIndex += 1;
        return validationResponder(body.source_text, index);
      }
      if (
        path === "/api/v1/backtests/preview" &&
        method === "POST"
      ) {
        const body = JSON.parse(String(init?.body)) as PreviewRequestBody;
        const index = previewIndex;
        previewIndex += 1;
        return responder(body, index);
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
  return fetchMock;
}

function previewCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    (call) =>
      requestPath(call[0] as RequestInfo | URL) ===
      "/api/v1/backtests/preview",
  );
}

function validationCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    (call) =>
      requestPath(call[0] as RequestInfo | URL) ===
      "/api/v1/strategies/validate",
  );
}

function persistenceCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter((call) => {
    const path = requestPath(call[0] as RequestInfo | URL);
    return (
      path.startsWith("/api/v1/strategies/import") ||
      path.includes("/confirm") ||
      path.startsWith("/api/v1/runs") ||
      path.startsWith("/api/v1/batches") ||
      path.includes("/decision") ||
      path.includes("/artifact")
    );
  });
}

function renderPage(entry = "/strategies?tab=quantify") {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[entry]}>
        <StrategiesPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

async function validateSource(
  user: ReturnType<typeof userEvent.setup>,
  source = VALID_NQ_YAML,
) {
  fireEvent.change(
    screen.getByRole("textbox", { name: "strategy.v1 YAML" }),
    { target: { value: source } },
  );
  await user.click(screen.getByRole("button", { name: "驗證" }));
  expect(await screen.findByTestId("universe-gates-ok")).toBeInTheDocument();
  expect(await screen.findByTestId("preview-panel")).toBeInTheDocument();
}

function fillValidPreviewForm() {
  fireEvent.change(screen.getByTestId("preview-range-start"), {
    target: { value: "2026-07-01T09:30:00" },
  });
  fireEvent.change(screen.getByTestId("preview-range-end"), {
    target: { value: "2026-07-22T16:00:00" },
  });
  fireEvent.change(screen.getByTestId("preview-commission"), {
    target: { value: "2.5" },
  });
  fireEvent.change(screen.getByTestId("preview-slippage"), {
    target: { value: "1" },
  });
}

async function expectPreviewReady() {
  await waitFor(() => {
    expect(screen.getByTestId("run-preview")).toBeEnabled();
  });
}

async function runPreview(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByTestId("run-preview"));
  const result = await screen.findByTestId("preview-success");
  await expectPreviewReady();
  return result;
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

describe("P2 normal live preview mounted seam", () => {
  it("normal validation never auto-posts a preview", async () => {
    const fetchMock = installApi((body) =>
      jsonResponse(successBody(body.contract_id)),
    );
    const user = userEvent.setup();
    renderPage();

    await validateSource(user);
    fillValidPreviewForm();
    await expectPreviewReady();

    expect(previewCalls(fetchMock)).toHaveLength(0);
    expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();
  });

  it("does not let stale A validation authorize source B", async () => {
    let resolveA!: (response: Response) => void;
    const pendingA = new Promise<Response>((resolve) => {
      resolveA = resolve;
    });
    const fetchMock = installApi(
      (body) => jsonResponse(successBody(body.contract_id)),
      ownerReviewCoverageBody(),
      (_sourceText, index) =>
        index === 0
          ? pendingA
          : jsonResponse(validationBody(true, "B valid")),
    );
    const user = userEvent.setup();
    renderPage();
    const sourceB = `${VALID_NQ_YAML}\n# changed while validating`;
    const sourceBox = screen.getByRole("textbox", {
      name: "strategy.v1 YAML",
    });

    fireEvent.change(sourceBox, { target: { value: VALID_NQ_YAML } });
    fireEvent.click(screen.getByRole("button", { name: "驗證" }));
    await waitFor(() => {
      expect(validationCalls(fetchMock)).toHaveLength(1);
    });
    fireEvent.change(sourceBox, { target: { value: sourceB } });
    expect(sourceBox).toHaveValue(sourceB);

    await act(async () => {
      resolveA(jsonResponse(validationBody(true, "A stale valid")));
      await pendingA;
    });

    const staleRunButton = screen.queryByTestId("run-preview");
    if (staleRunButton) {
      fillValidPreviewForm();
      await expectPreviewReady();
      await user.click(staleRunButton);
    }
    expect(
      previewCalls(fetchMock),
      "stale A must not authorize unvalidated B preview",
    ).toHaveLength(0);
    expect(screen.queryByTestId("preview-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("confirm-adopt")).not.toBeInTheDocument();
  });

  it("requires explicit B validation before posting exact source B once", async () => {
    let resolveA!: (response: Response) => void;
    const pendingA = new Promise<Response>((resolve) => {
      resolveA = resolve;
    });
    const previewBodies: PreviewRequestBody[] = [];
    const fetchMock = installApi(
      (body) => {
        previewBodies.push(body);
        return jsonResponse(successBody(body.contract_id));
      },
      ownerReviewCoverageBody(),
      (_sourceText, index) =>
        index === 0
          ? pendingA
          : jsonResponse(validationBody(true, "B current valid")),
    );
    const user = userEvent.setup();
    renderPage();
    const sourceB = `${VALID_NQ_YAML}\n# B explicitly revalidated`;
    const sourceBox = screen.getByRole("textbox", {
      name: "strategy.v1 YAML",
    });

    fireEvent.change(sourceBox, { target: { value: VALID_NQ_YAML } });
    fireEvent.click(screen.getByRole("button", { name: "驗證" }));
    await waitFor(() => {
      expect(validationCalls(fetchMock)).toHaveLength(1);
    });
    fireEvent.change(sourceBox, { target: { value: sourceB } });
    await act(async () => {
      resolveA(jsonResponse(validationBody(true, "A stale valid")));
      await pendingA;
    });
    expect(previewCalls(fetchMock)).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "驗證" }));
    expect(await screen.findByTestId("preview-panel")).toBeInTheDocument();
    fillValidPreviewForm();
    await expectPreviewReady();
    await runPreview(user);

    expect(validationCalls(fetchMock)).toHaveLength(2);
    const validationSources = validationCalls(fetchMock).map((call) => {
      const body = JSON.parse(
        String((call[1] as RequestInit | undefined)?.body),
      ) as { source_text: string };
      return body.source_text;
    });
    expect(validationSources).toEqual([VALID_NQ_YAML, sourceB]);
    expect(previewCalls(fetchMock)).toHaveLength(1);
    expect(previewBodies).toHaveLength(1);
    expect(previewBodies[0].source_text).toBe(sourceB);
  });

  it("keeps B validation truth and busy state when late A settles", async () => {
    let resolveA!: (response: Response) => void;
    let resolveB!: (response: Response) => void;
    const pendingA = new Promise<Response>((resolve) => {
      resolveA = resolve;
    });
    const pendingB = new Promise<Response>((resolve) => {
      resolveB = resolve;
    });
    const fetchMock = installApi(
      (body) => jsonResponse(successBody(body.contract_id)),
      ownerReviewCoverageBody(),
      (_sourceText, index) => (index === 0 ? pendingA : pendingB),
    );
    renderPage();
    const sourceB = `${VALID_NQ_YAML}\n# B validation is current`;
    const sourceBox = screen.getByRole("textbox", {
      name: "strategy.v1 YAML",
    });

    fireEvent.change(sourceBox, { target: { value: VALID_NQ_YAML } });
    fireEvent.click(screen.getByRole("button", { name: "驗證" }));
    await waitFor(() => {
      expect(validationCalls(fetchMock)).toHaveLength(1);
    });
    fireEvent.change(sourceBox, { target: { value: sourceB } });
    fireEvent.click(screen.getByRole("button", { name: "驗證" }));
    await waitFor(() => {
      expect(validationCalls(fetchMock)).toHaveLength(2);
    });

    await act(async () => {
      resolveA(jsonResponse(validationBody(true, "A stale valid")));
      await pendingA;
    });
    expect(screen.getByRole("button", { name: "驗證" })).toBeDisabled();
    expect(screen.queryByText(/四層驗證通過/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("preview-panel")).not.toBeInTheDocument();

    await act(async () => {
      resolveB(jsonResponse(validationBody(true, "B current valid")));
      await pendingB;
    });
    expect(await screen.findByTestId("preview-panel")).toBeInTheDocument();
    expect(screen.getByText(/四層驗證通過/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "驗證" })).toBeEnabled();
    expect(previewCalls(fetchMock)).toHaveLength(0);
  });

  it("ignores a pending validation response after quantify unmount", async () => {
    let resolveLate!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      resolveLate = resolve;
    });
    const fetchMock = installApi(
      (body) => jsonResponse(successBody(body.contract_id)),
      ownerReviewCoverageBody(),
      () => pending,
    );
    const user = userEvent.setup();
    renderPage();

    fireEvent.change(
      screen.getByRole("textbox", { name: "strategy.v1 YAML" }),
      { target: { value: VALID_NQ_YAML } },
    );
    fireEvent.click(screen.getByRole("button", { name: "驗證" }));
    await waitFor(() => {
      expect(validationCalls(fetchMock)).toHaveLength(1);
    });
    await user.click(screen.getByRole("tab", { name: /③ 版本庫/ }));
    await act(async () => {
      resolveLate(jsonResponse(validationBody(true, "late unmounted A")));
      await pending;
    });
    await user.click(screen.getByRole("tab", { name: /② 量化確認/ }));

    expect(
      screen.getByRole("textbox", { name: "strategy.v1 YAML" }),
    ).toHaveValue("");
    expect(screen.queryByTestId("preview-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("confirm-adopt")).not.toBeInTheDocument();
    expect(previewCalls(fetchMock)).toHaveLength(0);
  });

  it("posts one exact URL/body only after click and uses catalog ES without a symbol hardcode", async () => {
    const received: PreviewRequestBody[] = [];
    const fetchMock = installApi(
      (body) => {
        received.push(body);
        return jsonResponse(successBody(body.contract_id));
      },
      esCatalogBody(),
    );
    const user = userEvent.setup();
    renderPage();

    await validateSource(user, VALID_ES_YAML);
    await waitFor(() => {
      expect(screen.getByTestId("preview-contract")).toHaveValue(
        "ES-202609-CME",
      );
    });
    expect(
      within(screen.getByTestId("preview-contract")).getByRole("option", {
        name: /E-mini S&P 500（ES） · ES-202609-CME/,
      }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("preview-contract")).queryByText(/NQ/),
    ).not.toBeInTheDocument();
    fillValidPreviewForm();
    await expectPreviewReady();
    await runPreview(user);

    expect(previewCalls(fetchMock)).toHaveLength(1);
    expect(
      requestPath(
        previewCalls(fetchMock)[0][0] as RequestInfo | URL,
      ),
    ).toBe("/api/v1/backtests/preview");
    expect(received).toEqual([
      {
        schema: "backtest_preview_request.v1",
        source_text: VALID_ES_YAML,
        filename: null,
        contract_id: "ES-202609-CME",
        range_start: localDatetimeToUtcIso("2026-07-01T09:30:00"),
        range_end: localDatetimeToUtcIso("2026-07-22T16:00:00"),
        assumptions: {
          initial_capital_usd: 100_000,
          commission_per_side: 2.5,
          slippage_ticks: 1,
        },
      },
    ]);
    expect(persistenceCalls(fetchMock)).toEqual([]);
  });

  it("deduplicates an in-flight double submit but allows a later explicit click", async () => {
    let resolveFirst!: (response: Response) => void;
    const first = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const fetchMock = installApi((body, index) =>
      index === 0
        ? first
        : jsonResponse(successBody(body.contract_id, "second explicit click")),
    );
    const user = userEvent.setup();
    renderPage();
    await validateSource(user);
    fillValidPreviewForm();
    await expectPreviewReady();

    const button = screen.getByTestId("run-preview");
    fireEvent.click(button);
    fireEvent.click(button);
    expect(previewCalls(fetchMock)).toHaveLength(1);

    await act(async () => {
      resolveFirst(jsonResponse(successBody()));
      await first;
    });
    expect(await screen.findByTestId("preview-success")).toBeInTheDocument();

    await user.click(screen.getByTestId("run-preview"));
    await screen.findByText("second explicit click");
    expect(previewCalls(fetchMock)).toHaveLength(2);
  });

  it("invalidates old output after source, contract, range or either assumption changes", async () => {
    const fetchMock = installApi((body, index) =>
      jsonResponse(successBody(body.contract_id, `success ${index + 1}`)),
    );
    const user = userEvent.setup();
    renderPage();
    await validateSource(user, VALID_MULTI_YAML);
    fillValidPreviewForm();
    await expectPreviewReady();

    await runPreview(user);
    fireEvent.change(screen.getByTestId("preview-commission"), {
      target: { value: "3" },
    });
    expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();

    await runPreview(user);
    fireEvent.change(screen.getByTestId("preview-slippage"), {
      target: { value: "2" },
    });
    expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();

    await runPreview(user);
    fireEvent.change(screen.getByTestId("preview-range-start"), {
      target: { value: "2026-07-02T09:30:00" },
    });
    expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();

    await runPreview(user);
    fireEvent.change(screen.getByTestId("preview-range-end"), {
      target: { value: "2026-07-23T16:00:00" },
    });
    expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();

    await runPreview(user);
    await user.selectOptions(
      screen.getByTestId("preview-contract"),
      "YM-202609-CBOT",
    );
    expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();

    await runPreview(user);
    fireEvent.change(
      screen.getByRole("textbox", { name: "strategy.v1 YAML" }),
      { target: { value: `${VALID_MULTI_YAML}\n# Owner changed source` } },
    );
    expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();
    expect(screen.queryByTestId("preview-panel")).not.toBeInTheDocument();
    expect(
      await screen.findByTestId("local-sketch-missing"),
    ).toBeInTheDocument();
    expect(previewCalls(fetchMock)).toHaveLength(6);
  });

  it("keeps every invalid range/assumption disabled with preview POST zero", async () => {
    const fetchMock = installApi((body) =>
      jsonResponse(successBody(body.contract_id)),
    );
    const user = userEvent.setup();
    renderPage();
    await validateSource(user);

    expect(screen.getByTestId("run-preview")).toBeDisabled();
    fireEvent.change(screen.getByTestId("preview-range-start"), {
      target: { value: "2026-07-22T16:00:00" },
    });
    fireEvent.change(screen.getByTestId("preview-range-end"), {
      target: { value: "2026-07-01T09:30:00" },
    });
    fireEvent.change(screen.getByTestId("preview-commission"), {
      target: { value: "2.5" },
    });
    fireEvent.change(screen.getByTestId("preview-slippage"), {
      target: { value: "1" },
    });
    expect(screen.getByTestId("run-preview")).toBeDisabled();

    fireEvent.change(screen.getByTestId("preview-range-start"), {
      target: { value: "2026-07-01T09:30:00" },
    });
    fireEvent.change(screen.getByTestId("preview-range-end"), {
      target: { value: "2026-07-22T16:00:00" },
    });
    fireEvent.change(screen.getByTestId("preview-commission"), {
      target: { value: "" },
    });
    expect(screen.getByTestId("run-preview")).toBeDisabled();
    fireEvent.change(screen.getByTestId("preview-commission"), {
      target: { value: "-1" },
    });
    expect(screen.getByTestId("run-preview")).toBeDisabled();
    fireEvent.change(screen.getByTestId("preview-commission"), {
      target: { value: "2.5" },
    });
    fireEvent.change(screen.getByTestId("preview-slippage"), {
      target: { value: "" },
    });
    expect(screen.getByTestId("run-preview")).toBeDisabled();
    fireEvent.change(screen.getByTestId("preview-slippage"), {
      target: { value: "1.5" },
    });
    expect(screen.getByTestId("run-preview")).toBeDisabled();
    fireEvent.change(screen.getByTestId("preview-slippage"), {
      target: { value: "1" },
    });
    await expectPreviewReady();
    expect(previewCalls(fetchMock)).toHaveLength(0);
  });

  it("renders complete 422 validation envelope instead of a generic error", async () => {
    const fetchMock = installApi(() =>
      jsonResponse(failureBody(false), 422),
    );
    const user = userEvent.setup();
    renderPage();
    await validateSource(user);
    fillValidPreviewForm();
    await expectPreviewReady();
    await user.click(screen.getByTestId("run-preview"));

    const failure = await screen.findByTestId("preview-422");
    expect(failure).toHaveAttribute("data-preview-persisted", "false");
    expect(
      within(failure).getByRole("region", { name: "驗證失敗" }),
    ).toBeInTheDocument();
    expect(
      within(failure).getAllByText(/range_start: preview range invalid/)
        .length,
    ).toBeGreaterThanOrEqual(2);
    expect(persistenceCalls(fetchMock)).toEqual([]);
  });

  it("renders a complete engine-failure envelope with zero downstream writes", async () => {
    const fetchMock = installApi(() =>
      jsonResponse(failureBody(true), 422),
    );
    const user = userEvent.setup();
    renderPage();
    await validateSource(user);
    fillValidPreviewForm();
    await expectPreviewReady();
    await user.click(screen.getByTestId("run-preview"));

    const failure = await screen.findByTestId("preview-422");
    expect(failure).toHaveTextContent(/engine／資料層未完成/);
    expect(failure).toHaveTextContent(/ValueError: canonical bars unavailable/);
    expect(persistenceCalls(fetchMock)).toEqual([]);
  });

  it.each([
    [
      "network",
      async () => {
        throw new TypeError("offline");
      },
    ],
    [
      "HTTP 500",
      () => jsonResponse({ detail: "down" }, 500),
    ],
    [
      "malformed JSON",
      () =>
        new Response("<html>not json</html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
    ],
    [
      "schema drift",
      (body: PreviewRequestBody) => {
        const bad = successBody(body.contract_id);
        bad.schema = "backtest_preview.v2";
        return jsonResponse(bad);
      },
    ],
    [
      "persisted true",
      (body: PreviewRequestBody) => {
        const bad = successBody(body.contract_id);
        bad.persisted = true;
        return jsonResponse(bad);
      },
    ],
    [
      "deep run_id",
      (body: PreviewRequestBody) => {
        const bad = successBody(body.contract_id);
        const validation = bad.validation as Record<string, unknown>;
        validation.debug = { run_id: "fake" };
        return jsonResponse(bad);
      },
    ],
    [
      "missing 30m",
      (body: PreviewRequestBody) => {
        const bad = successBody(body.contract_id);
        const charts = bad.charts as Record<string, unknown>;
        delete charts["30m"];
        return jsonResponse(bad);
      },
    ],
  ] as const)(
    "fails closed on %s without retaining old success",
    async (_label, badResponse) => {
      const fetchMock = installApi((body, index) =>
        index === 0
          ? jsonResponse(successBody(body.contract_id, "old success"))
          : badResponse(body),
      );
      const user = userEvent.setup();
      renderPage();
      await validateSource(user);
      fillValidPreviewForm();
      await expectPreviewReady();
      await runPreview(user);

      await user.click(screen.getByTestId("run-preview"));
      expect(
        await screen.findByTestId("preview-fail-closed"),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();
      expect(previewCalls(fetchMock)).toHaveLength(2);
      expect(persistenceCalls(fetchMock)).toEqual([]);
    },
  );

  it("keeps daily and evaluation funnels on separate scales with raw unknown ids and four charts", async () => {
    const fetchMock = installApi((body) =>
      jsonResponse(successBody(body.contract_id)),
    );
    const user = userEvent.setup();
    renderPage();
    await validateSource(user);
    fillValidPreviewForm();
    await expectPreviewReady();
    const result = await runPreview(user);

    const daily = within(result).getByRole("region", {
      name: "日級漏斗",
    });
    const evaluations = within(result).getByRole("region", {
      name: "評估級漏斗",
    });
    expect(daily).toHaveAttribute("data-preview-scale", "daily");
    expect(evaluations).toHaveAttribute(
      "data-preview-scale",
      "evaluation",
    );
    expect(daily).toHaveTextContent("單位：交易日");
    expect(evaluations).toHaveTextContent("單位：5m 評估次數");
    expect(result).not.toHaveTextContent("%");
    expect(
      within(result).getByRole("region", { name: "系統解讀" }),
    ).toHaveTextContent(/中層閘/);
    expect(within(result).getAllByText("owner_unknown_block").length).toBe(2);
    for (const timeframe of ["D", "1H", "30m", "5m"]) {
      expect(
        within(result).getByTestId(`chart-pane-${timeframe}`),
      ).toBeInTheDocument();
    }
    expect(within(result).getByTestId("preview-overlay-evidence")).toHaveTextContent(
      /8 個/,
    );
    expect(persistenceCalls(fetchMock)).toEqual([]);
  });

  it("late A response cannot overwrite new B input and result", async () => {
    let resolveA!: (response: Response) => void;
    const responseA = new Promise<Response>((resolve) => {
      resolveA = resolve;
    });
    const fetchMock = installApi((body, index) =>
      index === 0
        ? responseA
        : jsonResponse(successBody(body.contract_id, "B current truth")),
    );
    const user = userEvent.setup();
    renderPage();
    await validateSource(user);
    fillValidPreviewForm();
    await expectPreviewReady();

    fireEvent.click(screen.getByTestId("run-preview"));
    fireEvent.change(screen.getByTestId("preview-commission"), {
      target: { value: "3.5" },
    });
    await expectPreviewReady();
    await runPreview(user);
    expect(screen.getByText("B current truth")).toBeInTheDocument();

    await act(async () => {
      resolveA(jsonResponse(successBody("NQ-202609-CME", "A stale truth")));
      await responseA;
    });
    expect(screen.getByText("B current truth")).toBeInTheDocument();
    expect(screen.queryByText("A stale truth")).not.toBeInTheDocument();
    expect(previewCalls(fetchMock)).toHaveLength(2);
  });

  it("unmounts on tab switch and ignores the late preview response", async () => {
    let resolveLate!: (response: Response) => void;
    const late = new Promise<Response>((resolve) => {
      resolveLate = resolve;
    });
    const fetchMock = installApi(() => late);
    const user = userEvent.setup();
    renderPage();
    await validateSource(user);
    fillValidPreviewForm();
    await expectPreviewReady();
    fireEvent.click(screen.getByTestId("run-preview"));

    await user.click(screen.getByRole("tab", { name: /③ 版本庫/ }));
    expect(screen.queryByTestId("preview-panel")).not.toBeInTheDocument();
    await act(async () => {
      resolveLate(jsonResponse(successBody()));
      await late;
    });
    expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();
    expect(previewCalls(fetchMock)).toHaveLength(1);
  });
});

describe("P2 owner-review live-preview isolation", () => {
  it("owner-review click uses the in-memory fixture with zero business fetch", async () => {
    const fetchMock = vi.fn(async () =>
      new Response("unexpected business fetch", { status: 500 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage("/strategies?scenario=owner-review&tab=quantify");

    await user.click(await screen.findByTestId("fixture-valid_nq_ym"));
    expect(
      await screen.findByDisplayValue(/NQ趨勢日回踩90EMA_v1/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "驗證" }));
    await expectPreviewReady();
    await runPreview(user);
    expect(screen.getByTestId("preview-success")).toHaveAttribute(
      "data-preview-persisted",
      "false",
    );
    expect(screen.getAllByText("owner_unknown_block")).toHaveLength(2);

    for (const name of [
      /① 草圖/,
      /③ 版本庫/,
      /④ 市場洞察/,
      /② 量化確認/,
    ]) {
      await user.click(screen.getByRole("tab", { name }));
    }
    expect(screen.queryByTestId("preview-success")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
