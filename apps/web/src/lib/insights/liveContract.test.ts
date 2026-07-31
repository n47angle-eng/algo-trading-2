import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../../api/client";
import {
  archiveLiveInsight,
  fetchLiveInsightArchives,
  fetchLiveInsightDetail,
  fetchLiveInsights,
  importLiveInsight,
  InsightTransportError,
  restoreLiveInsight,
} from "../../api/client";
import {
  insightIdentityKey,
  parseInsightActiveList,
  parseInsightArchive,
  parseInsightArchiveList,
  parseInsightDetail,
  parseInsightImport,
  parseInsightRestore,
  type LiveInsightSummary,
} from "./liveContract";

const SHA = "a".repeat(64);
const ARCHIVE_A = "delete-00000000000040008000000000000000";
const ARCHIVE_B = "delete-11111111111141119111111111111111";
const ARCHIVE_C = "delete-2222222222224222a222222222222222";
const TIME_NEW = "2026-07-28T12:40:00.000000Z";
const TIME_OLD = "2026-07-28T12:34:56.123456Z";
const SOURCE = `schema: insight.v1
insight_id: insight-007
origin: workshop
version: 2
based_on_sketch: sketch-20260725-02
based_on_sketch_origin: workshop
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

function version(overrides: Record<string, unknown> = {}) {
  return {
    schema: "insight_version.v1",
    insight_id: "insight-007",
    origin: "workshop",
    version: 2,
    title: "開市觀察",
    instrument: "NQ",
    asset_class: "equity_index_futures",
    tag: "opening_window",
    validation_status: "recording",
    based_on_sketch: "sketch-20260725-02",
    based_on_sketch_origin: "workshop",
    imported_from_origin: null,
    content_sha256: SHA,
    imported_at: "2026-07-28T11:00:00.123456Z",
    source_text: SOURCE,
    ...overrides,
  };
}

function summary(overrides: Record<string, unknown> = {}) {
  const { source_text: _sourceText, ...row } = version();
  void _sourceText;
  return {
    ...row,
    versions: [1, 2],
    latest_version: 2,
    ...overrides,
  };
}

function activeList(rows: unknown[] = [summary()]) {
  return {
    schema: "insight_list.v1",
    count: rows.length,
    insights: rows,
  };
}

function detail(overrides: Record<string, unknown> = {}) {
  return {
    schema: "insight_detail.v1",
    origin: "workshop",
    insight_id: "insight-007",
    latest_version: 2,
    version_count: 2,
    versions: [
      version({
        version: 1,
        validation_status: "unverified",
        source_text: SOURCE.replace("version: 2", "version: 1").replace(
          "validation_status: recording",
          "validation_status: unverified",
        ),
      }),
      version(),
    ],
    ...overrides,
  };
}

function archiveSummary(overrides: Record<string, unknown> = {}) {
  return {
    archive_id: ARCHIVE_A,
    origin: "workshop",
    insight_id: "insight-007",
    deleted_at: TIME_NEW,
    version_count: 2,
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("six live insight transports", () => {
  it("uses exact paths/methods, encode-once segments, exact import body and no restore body", async () => {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(input), init });
        return new Response('{"schema":"probe"}', {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    const responses = await Promise.all([
      fetchLiveInsights(),
      fetchLiveInsightDetail("work/shop", "insight%007"),
      importLiveInsight(" exact yaml bytes \n"),
      fetchLiveInsightArchives(),
      archiveLiveInsight("journal-app", "insight/007"),
      restoreLiveInsight("workshop", "insight%007", "delete/abc"),
    ]);

    expect(
      calls.map(({ url, init }) => [
        url,
        init?.method,
        init?.body ?? "<absent>",
      ]),
    ).toEqual([
      ["/api/v1/insights", "GET", "<absent>"],
      [
        "/api/v1/insights/work%2Fshop/insight%25007",
        "GET",
        "<absent>",
      ],
      [
        "/api/v1/insights/import",
        "POST",
        '{"source_text":" exact yaml bytes \\n"}',
      ],
      ["/api/v1/insights/archives", "GET", "<absent>"],
      [
        "/api/v1/insights/journal-app/insight%2F007",
        "DELETE",
        "<absent>",
      ],
      [
        "/api/v1/insights/archives/workshop/insight%25007/delete%2Fabc/restore",
        "POST",
        "<absent>",
      ],
    ]);
    expect(Object.prototype.hasOwnProperty.call(calls[5].init, "body")).toBe(
      false,
    );
    expect(responses[0]).toMatchObject({
      path: "/api/v1/insights",
      method: "GET",
      status: 200,
      ok: true,
      rawText: '{"schema":"probe"}',
      jsonParsed: true,
      body: { schema: "probe" },
    });
  });

  it("preserves non-JSON raw bodies and distinguishes network/body-read failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response("backend raw", { status: 503 }))
        .mockRejectedValueOnce(new TypeError("offline"))
        .mockResolvedValueOnce({
          status: 200,
          ok: true,
          text: vi.fn(async () => {
            throw new Error("body stream broke");
          }),
        }),
    );
    const response = await fetchLiveInsights();
    expect(response).toMatchObject({
      status: 503,
      ok: false,
      rawText: "backend raw",
      jsonParsed: false,
      body: null,
    });
    await expect(fetchLiveInsightArchives()).rejects.toMatchObject({
      name: "InsightTransportError",
      method: "GET",
      path: "/api/v1/insights/archives",
    });
    await expect(fetchLiveInsights()).rejects.toBeInstanceOf(
      InsightTransportError,
    );
  });

  it("rethrows the original AbortError identity", async () => {
    const abort = new DOMException("cancelled", "AbortError");
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(abort)));
    await expect(fetchLiveInsights()).rejects.toBe(abort);
  });

  it("exposes no purge, single-version archive or insight status PATCH seam", () => {
    const exports = apiClient as Record<string, unknown>;
    expect(exports.purgeLiveInsight).toBeUndefined();
    expect(exports.archiveLiveInsightVersion).toBeUndefined();
    expect(exports.patchLiveInsightStatus).toBeUndefined();
  });
});

describe("active list strict adapter", () => {
  it("keeps the same insight id distinct across origins", () => {
    expect(insightIdentityKey("workshop", "insight-007")).not.toBe(
      insightIdentityKey("journal-app", "insight-007"),
    );
  });

  it("accepts exact rows, preserves backend order and uses composite identity", () => {
    const body = activeList([
      summary({
        origin: "journal-app",
        insight_id: "insight-008",
        based_on_sketch_origin: "journal-app",
      }),
      summary(),
    ]);
    const parsed = parseInsightActiveList(body);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(
        parsed.value.insights.map((row) =>
          insightIdentityKey(row.origin, row.insight_id),
        ),
      ).toEqual([
        insightIdentityKey("journal-app", "insight-008"),
        insightIdentityKey("workshop", "insight-007"),
      ]);
    }
  });

  it.each([
    ["wrong schema", { ...activeList(), schema: "insight_list.v2" }],
    ["wrong count", { ...activeList(), count: 2 }],
    ["top extra", { ...activeList(), extra: true }],
    [
      "row missing",
      activeList([
        Object.fromEntries(
          Object.entries(summary()).filter(([key]) => key !== "tag"),
        ),
      ]),
    ],
    ["row extra", activeList([{ ...summary(), extra: true }])],
    ["source leak", activeList([{ ...summary(), source_text: SOURCE }])],
    ["wrong latest", activeList([{ ...summary(), latest_version: 1 }])],
    ["wrong current", activeList([{ ...summary(), version: 1 }])],
    ["unsorted versions", activeList([{ ...summary(), versions: [2, 1] }])],
    ["duplicate versions", activeList([{ ...summary(), versions: [1, 1] }])],
    [
      "duplicate composite",
      activeList([summary(), summary({ title: "second wire row" })]),
    ],
    ["unknown status", activeList([{ ...summary(), validation_status: "ok" }])],
    ["upper SHA", activeList([{ ...summary(), content_sha256: SHA.toUpperCase() }])],
  ])("rejects %s", (_label, body) => {
    expect(parseInsightActiveList(body).ok).toBe(false);
  });
});

describe("detail and import strict adapters", () => {
  const selected = summary() as LiveInsightSummary;

  it("reconciles route, selected list row and every ascending source record", () => {
    const parsed = parseInsightDetail(
      detail(),
      { origin: "workshop", insight_id: "insight-007" },
      selected,
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.versions.map((row) => row.version)).toEqual([1, 2]);
      expect(parsed.value.versions[1].source_text).toBe(SOURCE);
    }
  });

  it.each([
    [
      "route drift",
      detail(),
      { origin: "journal-app", insight_id: "insight-007" },
    ],
    [
      "record identity drift",
      detail({
        versions: [
          version({ version: 1, insight_id: "insight-008" }),
          version(),
        ],
      }),
      { origin: "workshop", insight_id: "insight-007" },
    ],
    [
      "record order drift",
      detail({ versions: [version(), version({ version: 1 })] }),
      { origin: "workshop", insight_id: "insight-007" },
    ],
    [
      "count drift",
      detail({ version_count: 1 }),
      { origin: "workshop", insight_id: "insight-007" },
    ],
    [
      "latest drift",
      detail({ latest_version: 1 }),
      { origin: "workshop", insight_id: "insight-007" },
    ],
    [
      "source missing",
      detail({
        versions: [
          version({ version: 1 }),
          Object.fromEntries(
            Object.entries(version()).filter(([key]) => key !== "source_text"),
          ),
        ],
      }),
      { origin: "workshop", insight_id: "insight-007" },
    ],
  ])("rejects %s", (_label, body, route) => {
    expect(parseInsightDetail(body, route, selected).ok).toBe(false);
  });

  it("accepts exact import envelope and reconciles immutable candidate", () => {
    const parsed = parseInsightImport(
      {
        schema: "insight_import.v1",
        deduplicated: false,
        message: "已存入洞察庫",
        insight: version(),
      },
      { origin: "workshop", insight_id: "insight-007", version: 2 },
    );
    expect(parsed.ok).toBe(true);
  });

  it.each([
    [
      "candidate origin",
      { origin: "journal-app", insight_id: "insight-007", version: 2 },
    ],
    [
      "candidate id",
      { origin: "workshop", insight_id: "insight-008", version: 2 },
    ],
    [
      "candidate version",
      { origin: "workshop", insight_id: "insight-007", version: 1 },
    ],
  ])("rejects %s drift", (_label, candidate) => {
    const body = {
      schema: "insight_import.v1",
      deduplicated: false,
      message: "已存入洞察庫",
      insight: version(),
    };
    expect(parseInsightImport(body, candidate).ok).toBe(false);
  });
});

describe("archive/list/restore strict adapters", () => {
  const archiveAction = {
    origin: "workshop",
    insight_id: "insight-007",
    version_count: 2,
  };
  const restoreAction = { ...archiveAction, archive_id: ARCHIVE_A };

  it("accepts exact archive and restore identity/path/time/count", () => {
    expect(
      parseInsightArchive(
        {
          schema: "insight_archive.v1",
          origin: "workshop",
          insight_id: "insight-007",
          archive_id: ARCHIVE_A,
          deleted_at: TIME_NEW,
          version_count: 2,
          archived_to:
            `data/insights/_deleted/workshop/insight-007/${ARCHIVE_A}`,
        },
        archiveAction,
      ).ok,
    ).toBe(true);
    expect(
      parseInsightRestore(
        {
          schema: "insight_restore.v1",
          origin: "workshop",
          insight_id: "insight-007",
          archive_id: ARCHIVE_A,
          restored_at: TIME_NEW,
          version_count: 2,
          restored_to: "data/insights/workshop/insight-007",
        },
        restoreAction,
      ).ok,
    ).toBe(true);
  });

  it("accepts two distinct canonical UUID4 archive ids", () => {
    for (const archiveId of [ARCHIVE_A, ARCHIVE_B]) {
      expect(
        parseInsightArchive(
          {
            schema: "insight_archive.v1",
            origin: "workshop",
            insight_id: "insight-007",
            archive_id: archiveId,
            deleted_at: TIME_NEW,
            version_count: 2,
            archived_to:
              `data/insights/_deleted/workshop/insight-007/${archiveId}`,
          },
          archiveAction,
        ).ok,
      ).toBe(true);
    }
  });

  it("rejects non-v4 archive ids across archive, list and restore adapters", () => {
    const archiveId = "delete-11111111111111111111111111111111";
    expect(
      parseInsightArchive(
        {
          schema: "insight_archive.v1",
          origin: "workshop",
          insight_id: "insight-007",
          archive_id: archiveId,
          deleted_at: TIME_NEW,
          version_count: 2,
          archived_to:
            `data/insights/_deleted/workshop/insight-007/${archiveId}`,
        },
        archiveAction,
      ).ok,
    ).toBe(false);
    expect(
      parseInsightArchiveList({
        schema: "insight_archive_list.v1",
        count: 1,
        archives: [archiveSummary({ archive_id: archiveId })],
      }).ok,
    ).toBe(false);
    expect(
      parseInsightRestore(
        {
          schema: "insight_restore.v1",
          origin: "workshop",
          insight_id: "insight-007",
          archive_id: archiveId,
          restored_at: TIME_NEW,
          version_count: 2,
          restored_to: "data/insights/workshop/insight-007",
        },
        { ...archiveAction, archive_id: archiveId },
      ).ok,
    ).toBe(false);
  });

  it.each([
    ["wrong UUID version", "delete-00000000000030008000000000000000"],
    ["wrong UUID variant", "delete-00000000000040007000000000000000"],
    ["uppercase", "delete-AAAAAAAAAAAA4AAA8AAAAAAAAAAAAAAA"],
    ["hyphenated", "delete-00000000-0000-4000-8000-000000000000"],
    ["short", "delete-0000000000004000800000000000000"],
    ["long", "delete-000000000000400080000000000000000"],
  ])("rejects %s archive id", (_label, archiveId) => {
    expect(
      parseInsightArchiveList({
        schema: "insight_archive_list.v1",
        count: 1,
        archives: [archiveSummary({ archive_id: archiveId })],
      }).ok,
    ).toBe(false);
  });

  it.each([
    ["identity", { origin: "journal-app" }],
    ["count", { version_count: 1 }],
    ["time", { deleted_at: "2026-07-28T12:40:00Z" }],
    ["archive id", { archive_id: "delete-bypass" }],
    ["path", { archived_to: "C:\\data\\insights\\insight-007" }],
    ["extra", { extra: true }],
  ])("rejects archive %s drift", (_label, drift) => {
    const body = {
      schema: "insight_archive.v1",
      origin: "workshop",
      insight_id: "insight-007",
      archive_id: ARCHIVE_A,
      deleted_at: TIME_NEW,
      version_count: 2,
      archived_to:
        `data/insights/_deleted/workshop/insight-007/${ARCHIVE_A}`,
      ...drift,
    };
    expect(parseInsightArchive(body, archiveAction).ok).toBe(false);
  });

  it.each([
    ["identity", { insight_id: "insight-008" }],
    ["archive id", { archive_id: ARCHIVE_B }],
    ["count", { version_count: 1 }],
    ["time", { restored_at: "2026-07-28T12:40:00.000Z" }],
    ["path", { restored_to: "../data/insights/workshop/insight-007" }],
    ["extra", { extra: true }],
  ])("rejects restore %s drift", (_label, drift) => {
    const body = {
      schema: "insight_restore.v1",
      origin: "workshop",
      insight_id: "insight-007",
      archive_id: ARCHIVE_A,
      restored_at: TIME_NEW,
      version_count: 2,
      restored_to: "data/insights/workshop/insight-007",
      ...drift,
    };
    expect(parseInsightRestore(body, restoreAction).ok).toBe(false);
  });

  it("accepts deleted-descending order and identity-ascending ties", () => {
    const parsed = parseInsightArchiveList({
      schema: "insight_archive_list.v1",
      count: 3,
      archives: [
        archiveSummary({ deleted_at: TIME_NEW }),
        archiveSummary({
          archive_id: ARCHIVE_B,
          origin: "journal-app",
          insight_id: "insight-008",
          deleted_at: TIME_OLD,
        }),
        archiveSummary({
          archive_id: ARCHIVE_C,
          origin: "workshop",
          insight_id: "insight-009",
          deleted_at: TIME_OLD,
        }),
      ],
    });
    expect(parsed.ok).toBe(true);
  });

  it("rejects the same composite identity with different archive ids", () => {
    expect(
      parseInsightArchiveList({
        schema: "insight_archive_list.v1",
        count: 2,
        archives: [
          archiveSummary({ archive_id: ARCHIVE_A }),
          archiveSummary({ archive_id: ARCHIVE_B }),
        ],
      }).ok,
    ).toBe(false);
  });

  it("rejects different composite identities reusing one archive id", () => {
    expect(
      parseInsightArchiveList({
        schema: "insight_archive_list.v1",
        count: 2,
        archives: [
          archiveSummary({
            archive_id: ARCHIVE_A,
            deleted_at: TIME_NEW,
          }),
          archiveSummary({
            archive_id: ARCHIVE_A,
            origin: "journal-app",
            insight_id: "insight-008",
            deleted_at: TIME_OLD,
          }),
        ],
      }).ok,
    ).toBe(false);
  });

  it.each([
    [
      "wrong time order",
      [
        archiveSummary({ deleted_at: TIME_OLD }),
        archiveSummary({ archive_id: ARCHIVE_B, deleted_at: TIME_NEW }),
      ],
    ],
    [
      "wrong tie order",
      [
        archiveSummary({
          origin: "workshop",
          insight_id: "insight-009",
          deleted_at: TIME_OLD,
        }),
        archiveSummary({
          archive_id: ARCHIVE_B,
          origin: "journal-app",
          insight_id: "insight-008",
          deleted_at: TIME_OLD,
        }),
      ],
    ],
    ["duplicate", [archiveSummary(), archiveSummary()]],
  ])("rejects archive list %s", (_label, archives) => {
    expect(
      parseInsightArchiveList({
        schema: "insight_archive_list.v1",
        count: archives.length,
        archives,
      }).ok,
    ).toBe(false);
  });
});
