import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InsightTab } from "../../components/strategies/InsightTab";
import { StrategiesPage } from "../../pages/StrategiesPage";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { ownerReviewCoverageBody } from "../catalog/fixtureCatalog";
import * as insightStore from "../insightStore";
import { INSIGHT_STORAGE_KEY } from "../insightStore";
import type {
  LiveInsightArchiveSummary,
  LiveInsightSummary,
} from "../insights/liveContract";
import { WorkbenchProvider } from "./WorkbenchContext";

const SHA = "a".repeat(64);
const ARCHIVE_ID = "delete-00000000000040008000000000000000";
const ARCHIVE_ID_B = "delete-11111111111141119111111111111111";
const DELETED_AT = "2026-07-28T12:40:00.000000Z";
const IMPORTED_AT = "2026-07-28T11:00:00.123456Z";

function sourceText(
  insightId = "insight-007",
  origin = "workshop",
  version = 2,
): string {
  return `schema: insight.v1
insight_id: ${insightId}
origin: ${origin}
version: ${version}
based_on_sketch: sketch-20260725-02
based_on_sketch_origin: ${origin}
instrument: NQ
asset_class: equity_index_futures
title: 開市觀察
condition:
  type: session_time_filter
  suggested_params: {}
measurement:
  tag: opening_window
  hypothesis: 開市時段要再量度
validation_status: recording
`;
}

function liveVersion(
  insightId = "insight-007",
  origin: "workshop" | "journal-app" = "workshop",
  version = 2,
) {
  return {
    schema: "insight_version.v1",
    insight_id: insightId,
    origin,
    version,
    title: "開市觀察",
    instrument: "NQ",
    asset_class: "equity_index_futures",
    tag: "opening_window",
    validation_status: version === 1 ? "unverified" : "recording",
    based_on_sketch: "sketch-20260725-02",
    based_on_sketch_origin: origin,
    imported_from_origin: null,
    content_sha256: SHA,
    imported_at: IMPORTED_AT,
    source_text: sourceText(insightId, origin, version),
  };
}

function liveSummary(
  insightId = "insight-007",
  origin: "workshop" | "journal-app" = "workshop",
  versions = [1, 2],
): LiveInsightSummary {
  const latest = versions[versions.length - 1];
  const { source_text: _sourceText, ...record } = liveVersion(
    insightId,
    origin,
    latest,
  );
  void _sourceText;
  return {
    ...record,
    versions,
    latest_version: latest,
  } as LiveInsightSummary;
}

function archiveSummary(
  insightId = "insight-007",
  origin: "workshop" | "journal-app" = "workshop",
  archiveId = ARCHIVE_ID,
): LiveInsightArchiveSummary {
  return {
    archive_id: archiveId,
    origin,
    insight_id: insightId,
    deleted_at: DELETED_AT,
    version_count: 2,
  };
}

function activeBody(rows: LiveInsightSummary[]) {
  return { schema: "insight_list.v1", count: rows.length, insights: rows };
}

function archivesBody(rows: LiveInsightArchiveSummary[]) {
  return {
    schema: "insight_archive_list.v1",
    count: rows.length,
    archives: rows,
  };
}

function detailBody(row: LiveInsightSummary) {
  return {
    schema: "insight_detail.v1",
    origin: row.origin,
    insight_id: row.insight_id,
    latest_version: row.latest_version,
    version_count: row.versions.length,
    versions: row.versions.map((version) =>
      liveVersion(row.insight_id, row.origin, version),
    ),
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface RecordedCall {
  url: string;
  method: string;
  body: string | undefined;
  init: RequestInit | undefined;
}

interface LiveMockOptions {
  active?: LiveInsightSummary[];
  archives?: LiveInsightArchiveSummary[];
  activeGet?: (
    count: number,
    repo: LiveRepoMock,
  ) => Response | Promise<Response>;
  archiveGet?: (
    count: number,
    repo: LiveRepoMock,
  ) => Response | Promise<Response>;
  importPost?: (
    count: number,
    call: RecordedCall,
    repo: LiveRepoMock,
  ) => Response | Promise<Response>;
  archiveDelete?: (
    count: number,
    call: RecordedCall,
    repo: LiveRepoMock,
  ) => Response | Promise<Response>;
  restorePost?: (
    count: number,
    call: RecordedCall,
    repo: LiveRepoMock,
  ) => Response | Promise<Response>;
}

interface LiveRepoMock {
  active: LiveInsightSummary[];
  archives: LiveInsightArchiveSummary[];
  calls: RecordedCall[];
  counts: {
    activeGet: number;
    archiveGet: number;
    importPost: number;
    archiveDelete: number;
    restorePost: number;
    detailGet: number;
  };
}

function installLiveMock(options: LiveMockOptions = {}): LiveRepoMock {
  const repo: LiveRepoMock = {
    active: [...(options.active ?? [liveSummary()])],
    archives: [...(options.archives ?? [])],
    calls: [],
    counts: {
      activeGet: 0,
      archiveGet: 0,
      importPost: 0,
      archiveDelete: 0,
      restorePost: 0,
      detailGet: 0,
    },
  };

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const call: RecordedCall = {
        url,
        method,
        body: typeof init?.body === "string" ? init.body : undefined,
        init,
      };
      repo.calls.push(call);

      if (url.includes("/api/v1/data/coverage")) {
        return jsonResponse(ownerReviewCoverageBody());
      }
      if (url.includes("/api/v1/sketches/")) {
        return new Response("not found", { status: 404 });
      }
      if (
        method === "POST" &&
        /\/api\/v1\/insights\/archives\/.+\/restore$/.test(url)
      ) {
        repo.counts.restorePost += 1;
        if (options.restorePost) {
          return options.restorePost(repo.counts.restorePost, call, repo);
        }
        const match =
          /\/archives\/([^/]+)\/([^/]+)\/([^/]+)\/restore$/.exec(url);
        const origin = decodeURIComponent(match?.[1] ?? "") as
          | "workshop"
          | "journal-app";
        const insightId = decodeURIComponent(match?.[2] ?? "");
        const archiveId = decodeURIComponent(match?.[3] ?? "");
        const archived = repo.archives.find(
          (row) =>
            row.origin === origin &&
            row.insight_id === insightId &&
            row.archive_id === archiveId,
        );
        if (!archived) {
          return jsonResponse({ detail: "not found" }, 404);
        }
        repo.archives = repo.archives.filter((row) => row !== archived);
        repo.active = [
          liveSummary(insightId, origin, [1, 2]),
          ...repo.active,
        ];
        return jsonResponse({
          schema: "insight_restore.v1",
          origin,
          insight_id: insightId,
          archive_id: archiveId,
          restored_at: "2026-07-28T12:45:00.000000Z",
          version_count: archived.version_count,
          restored_to: `data/insights/${origin}/${insightId}`,
        });
      }
      if (url.endsWith("/api/v1/insights/archives") && method === "GET") {
        repo.counts.archiveGet += 1;
        if (options.archiveGet) {
          return options.archiveGet(repo.counts.archiveGet, repo);
        }
        return jsonResponse(archivesBody(repo.archives));
      }
      if (url.endsWith("/api/v1/insights/import") && method === "POST") {
        repo.counts.importPost += 1;
        if (options.importPost) {
          return options.importPost(repo.counts.importPost, call, repo);
        }
        const request = JSON.parse(call.body ?? "{}") as {
          source_text?: string;
        };
        const source = request.source_text ?? "";
        const insightId =
          /^insight_id:\s*(\S+)/m.exec(source)?.[1] ?? "insight-009";
        const origin = (/^origin:\s*(\S+)/m.exec(source)?.[1] ??
          "workshop") as "workshop" | "journal-app";
        const version = Number(/^version:\s*(\d+)/m.exec(source)?.[1] ?? "1");
        const record = {
          ...liveVersion(insightId, origin, version),
          source_text: source,
        };
        repo.active = [
          liveSummary(insightId, origin, [version]),
          ...repo.active.filter(
            (row) =>
              !(row.origin === origin && row.insight_id === insightId),
          ),
        ];
        return jsonResponse({
          schema: "insight_import.v1",
          deduplicated: false,
          message: "已存入洞察庫",
          insight: record,
        });
      }
      if (url.endsWith("/api/v1/insights") && method === "GET") {
        repo.counts.activeGet += 1;
        if (options.activeGet) {
          return options.activeGet(repo.counts.activeGet, repo);
        }
        return jsonResponse(activeBody(repo.active));
      }
      if (
        method === "DELETE" &&
        /\/api\/v1\/insights\/[^/]+\/[^/]+$/.test(url)
      ) {
        repo.counts.archiveDelete += 1;
        if (options.archiveDelete) {
          return options.archiveDelete(
            repo.counts.archiveDelete,
            call,
            repo,
          );
        }
        const match = /\/api\/v1\/insights\/([^/]+)\/([^/]+)$/.exec(url);
        const origin = decodeURIComponent(match?.[1] ?? "") as
          | "workshop"
          | "journal-app";
        const insightId = decodeURIComponent(match?.[2] ?? "");
        const active = repo.active.find(
          (row) => row.origin === origin && row.insight_id === insightId,
        );
        if (!active) {
          return jsonResponse({ detail: "not found" }, 404);
        }
        const archived = archiveSummary(insightId, origin);
        archived.version_count = active.versions.length;
        repo.active = repo.active.filter((row) => row !== active);
        repo.archives = [archived, ...repo.archives];
        return jsonResponse({
          schema: "insight_archive.v1",
          origin,
          insight_id: insightId,
          archive_id: archived.archive_id,
          deleted_at: archived.deleted_at,
          version_count: archived.version_count,
          archived_to:
            `data/insights/_deleted/${origin}/${insightId}/` +
            archived.archive_id,
        });
      }
      if (
        method === "GET" &&
        /\/api\/v1\/insights\/[^/]+\/[^/]+$/.test(url)
      ) {
        repo.counts.detailGet += 1;
        const match = /\/api\/v1\/insights\/([^/]+)\/([^/]+)$/.exec(url);
        const origin = decodeURIComponent(match?.[1] ?? "");
        const insightId = decodeURIComponent(match?.[2] ?? "");
        const row = repo.active.find(
          (item) =>
            item.origin === origin && item.insight_id === insightId,
        );
        return row
          ? jsonResponse(detailBody(row))
          : jsonResponse({ detail: "not found" }, 404);
      }
      return new Response("unexpected request", { status: 599 });
    }),
  );
  return repo;
}

function renderNormal() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/strategies?tab=insight"]}>
        <StrategiesPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

function renderOwnerReview() {
  return render(
    <ThemeProvider>
      <MemoryRouter
        initialEntries={[
          "/strategies?scenario=owner-review&tab=sketch",
        ]}
      >
        <StrategiesPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

function ModeChangeHarness() {
  const navigate = useNavigate();
  return (
    <>
      <button
        type="button"
        data-testid="switch-owner-review"
        onClick={() => {
          navigate("/strategies?scenario=owner-review&tab=insight");
        }}
      >
        switch mode
      </button>
      <StrategiesPage />
    </>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function waitForActive(insightId = "insight-007") {
  await waitFor(() => {
    expect(screen.getByText(new RegExp(insightId))).toBeInTheDocument();
  });
}

async function pasteImport(source: string) {
  const box = screen.getByRole("textbox", { name: /insight.v1 YAML/ });
  fireEvent.change(box, { target: { value: source } });
  await waitFor(() => {
    expect(screen.getByTestId("insight-import")).not.toBeDisabled();
  });
  return box as HTMLTextAreaElement;
}

afterEach(() => {
  localStorage.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("normal live truth and independent reads", () => {
  it("starts active+archive GET in parallel and one side can fail independently", async () => {
    const active = deferred<Response>();
    const archives = deferred<Response>();
    const repo = installLiveMock({
      activeGet: () => active.promise,
      archiveGet: () => archives.promise,
    });
    renderNormal();

    await waitFor(() => {
      expect(repo.counts.activeGet).toBe(1);
      expect(repo.counts.archiveGet).toBe(1);
    });
    await act(async () => {
      active.resolve(jsonResponse(activeBody([liveSummary()])));
      archives.resolve(jsonResponse({ detail: "archive down" }, 503));
      await Promise.resolve();
    });
    await waitForActive();
    expect(screen.getByTestId("archived-insights-error")).toHaveTextContent(
      "唔會猜測歸檔狀態",
    );
    expect(screen.queryByTestId("active-insights-error")).toBeNull();
  });

  it("never turns a non-v4 archive response id into a restore request", async () => {
    const repo = installLiveMock({
      active: [],
      archives: [
        archiveSummary(
          "insight-007",
          "workshop",
          "delete-11111111111111111111111111111111",
        ),
      ],
    });
    renderNormal();
    await waitFor(() => {
      expect(
        screen.getByTestId("archived-insights-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("restore-workshop-insight-007"),
    ).not.toBeInTheDocument();
    expect(repo.counts.restorePost).toBe(0);
  });

  it("normal never reads/writes the old insight store and status is immutable/read-only", async () => {
    const sentinel =
      '{"schema":"insight_store.v1","items":[{"owner":"NORMAL_SENTINEL"}]}';
    localStorage.setItem(INSIGHT_STORAGE_KEY, sentinel);
    const listSpy = vi.spyOn(insightStore, "listInsights");
    const importSpy = vi.spyOn(insightStore, "importInsight");
    const statusSpy = vi.spyOn(insightStore, "updateInsightStatus");
    const deleteSpy = vi.spyOn(insightStore, "deleteInsight");
    const repo = installLiveMock();
    renderNormal();
    await waitForActive();

    expect(screen.getByText(/狀態來自不可變洞察版本，只讀/)).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /狀態 insight/ })).toBeNull();
    expect(listSpy).not.toHaveBeenCalled();
    expect(importSpy).not.toHaveBeenCalled();
    expect(statusSpy).not.toHaveBeenCalled();
    expect(deleteSpy).not.toHaveBeenCalled();
    expect(localStorage.getItem(INSIGHT_STORAGE_KEY)).toBe(sentinel);
    expect(
      repo.calls.filter((call) =>
        /\/api\/v1\/runs|promotion-decisions|\/export|\/api\/v1\/paper|\/api\/v1\/ib|strategies\?status=confirmed/.test(
          call.url,
        ),
      ),
    ).toHaveLength(0);
  });

  it("loads selected detail from the composite route and reconciles all versions", async () => {
    const repo = installLiveMock();
    const user = userEvent.setup();
    renderNormal();
    await waitForActive();
    await user.click(screen.getByRole("button", { name: "過目" }));

    await waitFor(() => {
      expect(screen.getByRole("region", { name: "洞察詳情" })).toHaveTextContent(
        "開市時段要再量度",
      );
    });
    expect(repo.counts.detailGet).toBe(1);
    expect(
      repo.calls.some(
        (call) =>
          call.url === "/api/v1/insights/workshop/insight-007" &&
          call.method === "GET",
      ),
    ).toBe(true);
  });
});

describe("normal import contract", () => {
  it("posts exact source bytes once, accepts strict success and refreshes both lists", async () => {
    const repo = installLiveMock();
    const user = userEvent.setup();
    const source = sourceText("insight-009", "workshop", 1);
    renderNormal();
    await waitForActive();
    const box = await pasteImport(source);
    await user.click(screen.getByTestId("insight-import"));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "已入洞察庫 insight-009 v1",
      );
    });
    expect(repo.counts.importPost).toBe(1);
    expect(
      repo.calls.find(
        (call) =>
          call.url.endsWith("/api/v1/insights/import") &&
          call.method === "POST",
      )?.body,
    ).toBe(JSON.stringify({ source_text: source }));
    expect(repo.counts.activeGet).toBe(2);
    expect(repo.counts.archiveGet).toBe(2);
    expect(box.value).toBe(source);
  });

  it.each([422, 409, 503])(
    "HTTP %s keeps exact textarea, writes no local insight and never retries",
    async (status) => {
      const sentinel = '{"schema":"insight_store.v1","items":[]}';
      localStorage.setItem(INSIGHT_STORAGE_KEY, sentinel);
      const oldImport = vi.spyOn(insightStore, "importInsight");
      const repo = installLiveMock({
        importPost: () =>
          jsonResponse(
            { detail: `full backend ${status} body`, unchanged: true },
            status,
          ),
      });
      const user = userEvent.setup();
      const source = sourceText("insight-009", "workshop", 1);
      renderNormal();
      await waitForActive();
      const box = await pasteImport(source);
      await user.click(screen.getByTestId("insight-import"));

      await waitFor(() => {
        expect(screen.getByTestId("insight-mutation-error")).toHaveTextContent(
          "原文已保留",
        );
      });
      expect(
        within(screen.getByTestId("insight-mutation-error")).getByText(
          new RegExp(`HTTP ${status}`),
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("insight-mutation-error").textContent,
      ).toContain(`full backend ${status} body`);
      expect(box.value).toBe(source);
      expect(repo.counts.importPost).toBe(1);
      expect(repo.counts.activeGet).toBe(1);
      expect(repo.counts.archiveGet).toBe(1);
      expect(oldImport).not.toHaveBeenCalled();
      expect(localStorage.getItem(INSIGHT_STORAGE_KEY)).toBe(sentinel);
    },
  );

  it.each(["malformed", "network"])(
    "%s result stays unknown, preserves source and rereads both lists exactly once",
    async (caseName) => {
      const repo = installLiveMock({
        importPost: () => {
          if (caseName === "network") {
            throw new TypeError("offline while reading response");
          }
          return new Response("malformed success body", { status: 200 });
        },
      });
      const user = userEvent.setup();
      const source = sourceText("insight-009", "workshop", 1);
      renderNormal();
      await waitForActive();
      const box = await pasteImport(source);
      await user.click(screen.getByTestId("insight-import"));

      await waitFor(() => {
        expect(repo.counts.activeGet).toBe(2);
        expect(repo.counts.archiveGet).toBe(2);
      });
      expect(screen.getByTestId("insight-mutation-error")).toHaveTextContent(
        /結果未能核實|結果未知/,
      );
      expect(box.value).toBe(source);
      expect(repo.counts.importPost).toBe(1);
    },
  );
});

describe("archive grace, cancellation and strict commit", () => {
  it("grace sends DELETE 0; undo keeps DELETE at 0 forever and old delete helper at 0", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const repo = installLiveMock();
    const oldDelete = vi.spyOn(insightStore, "deleteInsight");
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderNormal();
    await waitForActive();

    await user.click(
      screen.getByTestId("archive-workshop-insight-007"),
    );
    expect(screen.getByText(/待歸檔 · 約 5 秒內可復原/)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(repo.counts.archiveDelete).toBe(0);
    await user.click(
      screen.getByTestId("undo-archive-workshop-insight-007"),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(repo.counts.archiveDelete).toBe(0);
    expect(oldDelete).not.toHaveBeenCalled();
    expect(screen.getByText(/insight-007/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "已復原，未有發出歸檔要求",
    );
  });

  it("navigation/unmount cancels grace instead of flushing a destructive request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const repo = installLiveMock();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const view = renderNormal();
    await waitForActive();
    await user.click(
      screen.getByTestId("archive-workshop-insight-007"),
    );
    await user.click(screen.getByRole("tab", { name: /① 草圖/ }));
    view.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(repo.counts.archiveDelete).toBe(0);
  });

  it("fixture generation change cancels grace and starts no background DELETE", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const repo = installLiveMock();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const view = render(
      <ThemeProvider>
        <WorkbenchProvider ownerReview={false}>
          <InsightTab fixtureEpoch={0} />
        </WorkbenchProvider>
      </ThemeProvider>,
    );
    await waitForActive();
    await user.click(
      screen.getByTestId("archive-workshop-insight-007"),
    );
    view.rerender(
      <ThemeProvider>
        <WorkbenchProvider ownerReview={false}>
          <InsightTab fixtureEpoch={1} />
        </WorkbenchProvider>
      </ThemeProvider>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(repo.counts.archiveDelete).toBe(0);
  });

  it("mode change cancels grace and starts no live DELETE", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const repo = installLiveMock();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={["/strategies?tab=insight"]}>
          <ModeChangeHarness />
        </MemoryRouter>
      </ThemeProvider>,
    );
    await waitForActive();
    await user.click(
      screen.getByTestId("archive-workshop-insight-007"),
    );
    await user.click(screen.getByTestId("switch-owner-review"));
    await waitFor(() => {
      expect(screen.getByTestId("owner-review-banner")).toBeInTheDocument();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(repo.counts.archiveDelete).toBe(0);
  });

  it("expiry sends exactly one DELETE, double action is suppressed, then strict success moves the row", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const deleteResult = deferred<Response>();
    const repo = installLiveMock({
      archiveDelete: () => deleteResult.promise,
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderNormal();
    await waitForActive();
    await user.click(screen.getByRole("button", { name: "過目" }));
    await waitFor(() => {
      expect(screen.getByRole("region", { name: "洞察詳情" })).toHaveTextContent(
        "開市時段要再量度",
      );
    });
    const archiveButton = screen.getByTestId(
      "archive-workshop-insight-007",
    );
    await user.click(archiveButton);
    expect(
      screen.queryByTestId("archive-workshop-insight-007"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("undo-archive-workshop-insight-007"),
    ).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });

    await waitFor(() => {
      expect(repo.counts.archiveDelete).toBe(1);
      expect(
        screen.getByTestId("archive-workshop-insight-007"),
      ).toBeDisabled();
    });
    fireEvent.click(screen.getByTestId("archive-workshop-insight-007"));
    fireEvent.click(screen.getByTestId("archive-workshop-insight-007"));
    expect(repo.counts.archiveDelete).toBe(1);

    repo.active = [];
    repo.archives = [archiveSummary()];
    deleteResult.resolve(
      jsonResponse({
        schema: "insight_archive.v1",
        origin: "workshop",
        insight_id: "insight-007",
        archive_id: ARCHIVE_ID,
        deleted_at: DELETED_AT,
        version_count: 2,
        archived_to:
          `data/insights/_deleted/workshop/insight-007/${ARCHIVE_ID}`,
      }),
    );
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "已歸檔，可在已歸檔區恢復",
      );
    });
    expect(
      screen.queryByRole("region", { name: "洞察詳情" }),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "洞察庫" })).queryByText(
        /insight-007/,
      ),
    ).toBeNull();
    expect(
      within(screen.getByRole("region", { name: "已歸檔" })).getByText(
        /insight-007/,
      ),
    ).toBeInTheDocument();
    expect(repo.counts.activeGet).toBe(2);
    expect(repo.counts.archiveGet).toBe(2);
  });

  it.each(["404", "409", "503", "malformed", "network"])(
    "%s archive failure restores the row, rereads both lists once and never retries",
    async (caseName) => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const status =
        caseName === "404"
          ? 404
          : caseName === "409"
            ? 409
            : 503;
      const repo = installLiveMock({
        archiveDelete: () => {
          if (caseName === "network") {
            throw new TypeError("archive connection dropped");
          }
          if (caseName === "malformed") {
            return jsonResponse(
              {
                schema: "insight_archive.v1",
                origin: "journal-app",
                insight_id: "insight-007",
                archive_id: ARCHIVE_ID,
                deleted_at: DELETED_AT,
                version_count: 2,
                archived_to:
                  `data/insights/_deleted/journal-app/insight-007/` +
                  ARCHIVE_ID,
              },
              200,
            );
          }
          return jsonResponse(
            { detail: `archive ${status} full body`, unchanged: true },
            status,
          );
        },
      });
      const user = userEvent.setup({
        advanceTimers: vi.advanceTimersByTime,
      });
      renderNormal();
      await waitForActive();
      await user.click(
        screen.getByTestId("archive-workshop-insight-007"),
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5100);
      });

      await waitFor(() => {
        expect(repo.counts.activeGet).toBe(2);
        expect(repo.counts.archiveGet).toBe(2);
      });
      expect(repo.counts.archiveDelete).toBe(1);
      expect(screen.getByTestId("insight-mutation-error")).toHaveTextContent(
        /仍保留/,
      );
      expect(
        within(screen.getByRole("region", { name: "洞察庫" })).getByText(
          /insight-007/,
        ),
      ).toBeInTheDocument();
    },
  );
});

describe("archive list restore and races", () => {
  it("restore sends exact one POST with no body; strict success removes archive and refreshes active", async () => {
    const repo = installLiveMock({
      active: [],
      archives: [archiveSummary()],
    });
    const user = userEvent.setup();
    renderNormal();
    await waitFor(() => {
      expect(
        screen.getByTestId("restore-workshop-insight-007"),
      ).toBeInTheDocument();
    });
    const restore = screen.getByTestId(
      "restore-workshop-insight-007",
    );
    await user.click(restore);
    restore.click();

    await waitFor(() => {
      expect(repo.counts.restorePost).toBe(1);
      expect(screen.getByRole("status")).toHaveTextContent(
        "已恢復 workshop/insight-007",
      );
    });
    const post = repo.calls.find((call) => call.method === "POST");
    expect(post?.url).toBe(
      `/api/v1/insights/archives/workshop/insight-007/${ARCHIVE_ID}/restore`,
    );
    expect(post?.body).toBeUndefined();
    expect(Object.prototype.hasOwnProperty.call(post?.init ?? {}, "body")).toBe(
      false,
    );
    await waitFor(() => {
      expect(
        within(screen.getByRole("region", { name: "洞察庫" })).getByText(
          /insight-007/,
        ),
      ).toBeInTheDocument();
    });
    expect(
      within(screen.getByRole("region", { name: "已歸檔" })).queryByText(
        /insight-007/,
      ),
    ).toBeNull();
  });

  it.each(["404", "409", "503", "malformed", "network"])(
    "%s restore failure retains archive row, rereads both lists once and never retries",
    async (caseName) => {
      const status =
        caseName === "404"
          ? 404
          : caseName === "409"
            ? 409
            : 503;
      const repo = installLiveMock({
        active: [],
        archives: [archiveSummary()],
        restorePost: () => {
          if (caseName === "network") {
            throw new TypeError("restore connection dropped");
          }
          if (caseName === "malformed") {
            return jsonResponse({
              schema: "insight_restore.v1",
              origin: "workshop",
              insight_id: "insight-007",
              archive_id: ARCHIVE_ID_B,
              restored_at: "2026-07-28T12:45:00.000000Z",
              version_count: 2,
              restored_to: "data/insights/workshop/insight-007",
            });
          }
          return jsonResponse(
            { detail: `restore ${status} full body`, unchanged: true },
            status,
          );
        },
      });
      const user = userEvent.setup();
      renderNormal();
      await waitFor(() => {
        expect(
          screen.getByTestId("restore-workshop-insight-007"),
        ).toBeInTheDocument();
      });
      await user.click(
        screen.getByTestId("restore-workshop-insight-007"),
      );

      await waitFor(() => {
        expect(repo.counts.activeGet).toBe(2);
        expect(repo.counts.archiveGet).toBe(2);
      });
      expect(repo.counts.restorePost).toBe(1);
      expect(screen.getByTestId("insight-mutation-error")).toHaveTextContent(
        /仍保留/,
      );
      expect(
        screen.getByTestId("restore-workshop-insight-007"),
      ).toBeInTheDocument();
    },
  );

  it("an older in-flight list cannot overwrite a newer import+archive result", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const staleActive = deferred<Response>();
    const staleArchives = deferred<Response>();
    const repo = installLiveMock({
      activeGet: (count, state) =>
        count === 2
          ? staleActive.promise
          : jsonResponse(activeBody(state.active)),
      archiveGet: (count, state) =>
        count === 2
          ? staleArchives.promise
          : jsonResponse(archivesBody(state.archives)),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderNormal();
    await waitForActive();
    await pasteImport(sourceText("insight-009", "workshop", 1));
    await user.click(screen.getByTestId("insight-import"));
    await waitFor(() => {
      expect(repo.counts.activeGet).toBe(2);
      expect(repo.counts.archiveGet).toBe(2);
    });

    await user.click(
      screen.getByTestId("archive-workshop-insight-007"),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    await waitFor(() => {
      expect(repo.counts.activeGet).toBe(3);
      expect(repo.counts.archiveGet).toBe(3);
    });
    expect(
      within(screen.getByRole("region", { name: "洞察庫" })).queryByText(
        /insight-007/,
      ),
    ).toBeNull();

    await act(async () => {
      staleActive.resolve(jsonResponse(activeBody([liveSummary()])));
      staleArchives.resolve(jsonResponse(archivesBody([])));
      for (let turn = 0; turn < 8; turn += 1) {
        await Promise.resolve();
      }
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(
      within(screen.getByRole("region", { name: "洞察庫" })).queryByText(
        /insight-007/,
      ),
    ).toBeNull();
    expect(
      within(screen.getByRole("region", { name: "已歸檔" })).getByText(
        /insight-007/,
      ),
    ).toBeInTheDocument();
  });

  it("unmount aborts an in-flight DELETE and its late 200 commits no state or retry", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const lateDelete = deferred<Response>();
    const repo = installLiveMock({
      archiveDelete: () => lateDelete.promise,
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const view = renderNormal();
    await waitForActive();
    await user.click(
      screen.getByTestId("archive-workshop-insight-007"),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    await waitFor(() => {
      expect(repo.counts.archiveDelete).toBe(1);
    });
    view.unmount();
    lateDelete.resolve(
      jsonResponse({
        schema: "insight_archive.v1",
        origin: "workshop",
        insight_id: "insight-007",
        archive_id: ARCHIVE_ID,
        deleted_at: DELETED_AT,
        version_count: 2,
        archived_to:
          `data/insights/_deleted/workshop/insight-007/${ARCHIVE_ID}`,
      }),
    );
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(repo.counts.archiveDelete).toBe(1);
    expect(repo.counts.activeGet).toBe(1);
    expect(repo.counts.archiveGet).toBe(1);
  });

  it("A and B composite actions stay isolated when responses settle out of order", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const responseA = deferred<Response>();
    const responseB = deferred<Response>();
    const rowA = liveSummary("insight-007", "workshop");
    const rowB = liveSummary("insight-007", "journal-app");
    const repo = installLiveMock({
      active: [rowA, rowB],
      archiveDelete: (_count, call) =>
        call.url.includes("/workshop/")
          ? responseA.promise
          : responseB.promise,
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderNormal();
    await waitFor(() => {
      expect(
        screen.getByTestId("archive-workshop-insight-007"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("archive-journal-app-insight-007"),
      ).toBeInTheDocument();
    });
    await user.click(
      screen.getByTestId("archive-workshop-insight-007"),
    );
    await user.click(
      screen.getByTestId("archive-journal-app-insight-007"),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    await waitFor(() => {
      expect(repo.counts.archiveDelete).toBe(2);
    });

    repo.active = [rowA];
    repo.archives = [
      archiveSummary(
        "insight-007",
        "journal-app",
        ARCHIVE_ID_B,
      ),
    ];
    responseB.resolve(
      jsonResponse({
        schema: "insight_archive.v1",
        origin: "journal-app",
        insight_id: "insight-007",
        archive_id: ARCHIVE_ID_B,
        deleted_at: DELETED_AT,
        version_count: 2,
        archived_to:
          `data/insights/_deleted/journal-app/insight-007/${ARCHIVE_ID_B}`,
      }),
    );
    responseA.resolve(jsonResponse({ detail: "A stayed active" }, 503));

    await waitFor(() => {
      expect(screen.getByTestId("insight-mutation-error")).toHaveTextContent(
        "仍保留",
      );
    });
    expect(
      within(screen.getByRole("region", { name: "洞察庫" })).getByText(
        /insight-007/,
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "洞察庫" })).queryByText(
        /insight-008/,
      ),
    ).toBeNull();
    expect(
      screen.getByTestId("archive-workshop-insight-007"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("archive-journal-app-insight-007"),
    ).not.toBeInTheDocument();
  });
});

describe("owner-review remains isolated in-memory truth", () => {
  it("keeps fixture import/status/detail/delete and sends all six live endpoint counts to zero", async () => {
    const calls: RecordedCall[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({
          url: String(input),
          method: (init?.method ?? "GET").toUpperCase(),
          body: typeof init?.body === "string" ? init.body : undefined,
          init,
        });
        return new Response("unexpected owner-review fetch", { status: 599 });
      }),
    );
    const sentinel = '{"schema":"insight_store.v1","items":[]}';
    localStorage.setItem(INSIGHT_STORAGE_KEY, sentinel);
    const user = userEvent.setup();
    renderOwnerReview();
    await user.click(screen.getByTestId("fixture-valid_nq_ym"));
    await user.click(screen.getByRole("tab", { name: /④ 市場洞察/ }));
    await pasteImport(sourceText("insight-007", "workshop", 1));
    await user.click(screen.getByTestId("insight-import"));
    await waitFor(() => {
      expect(screen.getByText(/已入洞察庫 insight-007 v1/)).toBeInTheDocument();
    });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "狀態 insight-007" }),
      "supported",
    );
    await user.click(screen.getByRole("button", { name: "過目" }));
    expect(screen.getByRole("region", { name: "洞察詳情" })).toHaveTextContent(
      "開市時段要再量度",
    );
    await user.click(screen.getByRole("button", { name: "刪除" }));
    expect(
      within(screen.getByRole("region", { name: "洞察庫" })).queryByText(
        /insight-007/,
      ),
    ).toBeNull();

    expect(
      calls.filter((call) => call.url.includes("/api/v1/insights")),
    ).toHaveLength(0);
    expect(localStorage.getItem(INSIGHT_STORAGE_KEY)).toBe(sentinel);
  });
});
