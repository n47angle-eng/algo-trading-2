import { describe, expect, it } from "vitest";

import {
  parseRunReferenceList,
  parseStrategyDelete,
  parseStrategyDerive,
  readProblemDetail,
  strategyDeleteGuard,
} from "./lifecycleContract";

const STRATEGY = "strategy-0002";

function referenceRow(overrides: Record<string, unknown> = {}) {
  return {
    run_id: "nq-20260723-standard-001",
    strategy_version: STRATEGY,
    contract_id: "NQ-202609-CME",
    symbol: "NQ",
    session_name: "eth",
    range_start: "2026-05-01T00:00:00Z",
    range_end: "2026-07-22T21:00:00Z",
    ...overrides,
  };
}

function referenceList(overrides: Record<string, unknown> = {}) {
  return {
    schema: "run_reference_list.v1",
    mode: "strategy",
    run_scope: "standard",
    count_known: true,
    count: 0,
    known_match_count: 0,
    runs: [],
    unindexed_candidates: [],
    ...overrides,
  };
}

function derivedVersion(overrides: Record<string, unknown> = {}) {
  return {
    schema: "strategy_version.v1",
    strategy_id: "strategy-0003",
    status: "draft",
    name: "回踩 18EMA · p50 閘",
    created: "2026-07-28",
    imported_at: "2026-07-28T03:00:00Z",
    confirmed_at: null,
    content_sha256: "b".repeat(64),
    source_text: "schema: strategy.v1\n",
    spec_ref: null,
    based_on: STRATEGY,
    based_on_sketch: "sketch-20260725-01",
    based_on_sketch_origin: "workshop",
    based_on_insights: [],
    rationale: "r",
    unquantified_notes: [],
    universe: { contracts: ["NQ"], session: "eth" },
    parameters: [],
    ...overrides,
  };
}

function deriveBody(overrides: Record<string, unknown> = {}) {
  return {
    schema: "strategy_derive.v1",
    parent_strategy_id: STRATEGY,
    changed_count: 1,
    changed_paths: ["regime.sep_mult.value"],
    deduplicated: false,
    version: derivedVersion(),
    ...overrides,
  };
}

function deleteBody(overrides: Record<string, unknown> = {}) {
  return {
    schema: "strategy_delete.v1",
    strategy_id: "strategy-0003",
    deleted_at: "2026-07-28T04:05:06Z",
    archived_status: "draft",
    archived_to: "data/strategies/_deleted/strategy-0003.yaml",
    ...overrides,
  };
}

describe("run_reference_list.v1 strict parse", () => {
  it("accepts an exact proven-zero document", () => {
    const parsed = parseRunReferenceList(referenceList(), STRATEGY);
    expect(parsed).not.toBeNull();
    expect(strategyDeleteGuard(parsed)).toEqual({ kind: "allowed" });
  });

  it("accepts an exact in-use document and reports the exact count", () => {
    const parsed = parseRunReferenceList(
      referenceList({
        count: 2,
        known_match_count: 2,
        runs: [
          referenceRow(),
          referenceRow({ run_id: "nq-20260724-standard-002" }),
        ],
      }),
      STRATEGY,
    );
    expect(parsed).not.toBeNull();
    expect(strategyDeleteGuard(parsed)).toEqual({
      kind: "in-use",
      count: 2,
      runIds: ["nq-20260723-standard-001", "nq-20260724-standard-002"],
    });
  });

  it("treats unindexed candidates as unknown, never as zero", () => {
    const parsed = parseRunReferenceList(
      referenceList({
        count_known: false,
        count: null,
        unindexed_candidates: [
          { run_id: "legacy-001", reason: "manifest not indexed" },
        ],
      }),
      STRATEGY,
    );
    expect(parsed).not.toBeNull();
    expect(strategyDeleteGuard(parsed)).toEqual({ kind: "unknown" });
  });

  it("fails closed on a missing top-level key", () => {
    const body = referenceList() as Record<string, unknown>;
    delete body.known_match_count;
    expect(parseRunReferenceList(body, STRATEGY)).toBeNull();
  });

  it("fails closed on an extra top-level key", () => {
    expect(
      parseRunReferenceList(referenceList({ extra: 1 }), STRATEGY),
    ).toBeNull();
  });

  it("fails closed on a malformed body", () => {
    for (const body of [null, "run_reference_list.v1", 3, [], undefined]) {
      expect(parseRunReferenceList(body, STRATEGY)).toBeNull();
    }
  });

  it("fails closed on the wrong schema, mode or run scope", () => {
    expect(
      parseRunReferenceList(referenceList({ schema: "run_reference_list.v2" }), STRATEGY),
    ).toBeNull();
    expect(
      parseRunReferenceList(referenceList({ mode: "duplicate" }), STRATEGY),
    ).toBeNull();
    expect(
      parseRunReferenceList(referenceList({ run_scope: "all" }), STRATEGY),
    ).toBeNull();
  });

  it("fails closed when a row belongs to another strategy version", () => {
    expect(
      parseRunReferenceList(
        referenceList({
          count: 1,
          known_match_count: 1,
          runs: [referenceRow({ strategy_version: "strategy-0009" })],
        }),
        STRATEGY,
      ),
    ).toBeNull();
  });

  it("fails closed when count disagrees with the rows it claims to summarise", () => {
    expect(
      parseRunReferenceList(
        referenceList({ count: 3, known_match_count: 0 }),
        STRATEGY,
      ),
    ).toBeNull();
    expect(
      parseRunReferenceList(
        referenceList({
          count: 0,
          known_match_count: 0,
          runs: [referenceRow()],
        }),
        STRATEGY,
      ),
    ).toBeNull();
  });

  it("fails closed when certainty is claimed alongside unindexed candidates", () => {
    expect(
      parseRunReferenceList(
        referenceList({
          count_known: true,
          count: 0,
          unindexed_candidates: [{ run_id: "legacy-001", reason: "x" }],
        }),
        STRATEGY,
      ),
    ).toBeNull();
  });

  it("guards fail closed when the lookup produced nothing at all", () => {
    expect(strategyDeleteGuard(null)).toEqual({ kind: "unknown" });
  });
});

describe("strategy_derive.v1 strict parse", () => {
  const paths = ["regime.sep_mult.value"];

  it("accepts an exact response for the requested parent and paths", () => {
    const parsed = parseStrategyDerive(deriveBody(), STRATEGY, paths);
    expect(parsed?.version.strategy_id).toBe("strategy-0003");
    expect(parsed?.deduplicated).toBe(false);
  });

  it("accepts a deduplicated twin", () => {
    const parsed = parseStrategyDerive(
      deriveBody({ deduplicated: true }),
      STRATEGY,
      paths,
    );
    expect(parsed?.deduplicated).toBe(true);
  });

  it("fails closed when the response answers a different parent", () => {
    expect(
      parseStrategyDerive(
        deriveBody({ parent_strategy_id: "strategy-0001" }),
        STRATEGY,
        paths,
      ),
    ).toBeNull();
  });

  it("fails closed when changed paths are not exactly what was sent", () => {
    expect(
      parseStrategyDerive(
        deriveBody({
          changed_count: 2,
          changed_paths: ["pullback.ema.period", "regime.sep_mult.value"],
        }),
        STRATEGY,
        paths,
      ),
    ).toBeNull();
    expect(
      parseStrategyDerive(
        deriveBody({ changed_paths: ["pullback.ema.period"] }),
        STRATEGY,
        paths,
      ),
    ).toBeNull();
  });

  it("fails closed when changed paths are not canonically sorted", () => {
    expect(
      parseStrategyDerive(
        deriveBody({
          changed_count: 2,
          changed_paths: ["regime.sep_mult.value", "pullback.ema.period"],
        }),
        STRATEGY,
        ["regime.sep_mult.value", "pullback.ema.period"],
      ),
    ).toBeNull();
  });

  it("fails closed when changed_count disagrees with changed_paths", () => {
    expect(
      parseStrategyDerive(deriveBody({ changed_count: 2 }), STRATEGY, paths),
    ).toBeNull();
  });

  it("fails closed when the child does not record the direct parent", () => {
    expect(
      parseStrategyDerive(
        deriveBody({ version: derivedVersion({ based_on: "strategy-0001" }) }),
        STRATEGY,
        paths,
      ),
    ).toBeNull();
  });

  it("fails closed when the returned version is the parent itself", () => {
    expect(
      parseStrategyDerive(
        deriveBody({
          version: derivedVersion({ strategy_id: STRATEGY }),
        }),
        STRATEGY,
        paths,
      ),
    ).toBeNull();
  });

  it("fails closed on missing/extra keys and malformed bodies", () => {
    const missing = deriveBody() as Record<string, unknown>;
    delete missing.deduplicated;
    expect(parseStrategyDerive(missing, STRATEGY, paths)).toBeNull();
    expect(
      parseStrategyDerive(deriveBody({ extra: true }), STRATEGY, paths),
    ).toBeNull();
    expect(parseStrategyDerive(null, STRATEGY, paths)).toBeNull();
  });
});

describe("strategy_delete.v1 strict parse", () => {
  it("accepts an exact archive receipt", () => {
    expect(parseStrategyDelete(deleteBody(), "strategy-0003")).not.toBeNull();
  });

  it("fails closed when the receipt is for another id", () => {
    expect(parseStrategyDelete(deleteBody(), "strategy-0004")).toBeNull();
  });

  it("fails closed on a non-canonical timestamp", () => {
    expect(
      parseStrategyDelete(
        deleteBody({ deleted_at: "2026-07-28T04:05:06+08:00" }),
        "strategy-0003",
      ),
    ).toBeNull();
  });

  it("fails closed on a local absolute or escaping archive path", () => {
    for (const archived_to of [
      "C:/repo/data/strategies/_deleted/strategy-0003.yaml",
      "/var/data/strategies/_deleted/strategy-0003.yaml",
      "data\\strategies\\_deleted\\strategy-0003.yaml",
      "data/strategies/_deleted/../strategy-0003.yaml",
      "data/strategies/strategy-0003.yaml",
      "data/strategies/_deleted/strategy-0003.json",
    ]) {
      expect(
        parseStrategyDelete(deleteBody({ archived_to }), "strategy-0003"),
      ).toBeNull();
    }
  });

  it("fails closed on missing/extra keys", () => {
    const missing = deleteBody() as Record<string, unknown>;
    delete missing.archived_status;
    expect(parseStrategyDelete(missing, "strategy-0003")).toBeNull();
    expect(
      parseStrategyDelete(deleteBody({ extra: 1 }), "strategy-0003"),
    ).toBeNull();
  });
});

describe("problem detail reading", () => {
  it("keeps a plain string detail exactly as the server wrote it", () => {
    const detail = "策略歸檔已存在；未有覆蓋或刪除任何檔案";
    expect(readProblemDetail({ detail }, "fallback")).toEqual({
      text: detail,
      blocked: null,
    });
  });

  it("expands the structured standard-run block with its exact run ids", () => {
    const result = readProblemDetail(
      {
        detail: {
          schema: "strategy_delete_blocked.v1",
          message: "呢個策略版本仍有 standard run 引用，未能刪除",
          standard_run_count: 2,
          run_ids: ["run-a", "run-b"],
        },
      },
      "fallback",
    );
    expect(result.blocked?.standard_run_count).toBe(2);
    expect(result.text).toBe(
      "呢個策略版本仍有 standard run 引用，未能刪除\nstandard run 數目：2\nrun-a\nrun-b",
    );
  });

  it("uses the verbatim paste-back report for validation failures", () => {
    const result = readProblemDetail(
      {
        detail: {
          schema: "strategy_validation.v1",
          valid: false,
          issue_count: 1,
          issues: [],
          report_text: "regime.sep_mult.value: percentile 90.0 outside band",
        },
      },
      "fallback",
    );
    expect(result.text).toBe(
      "regime.sep_mult.value: percentile 90.0 outside band",
    );
    expect(result.blocked).toBeNull();
  });

  it("falls back without inventing a reason when there is no detail", () => {
    expect(readProblemDetail({}, "刪除失敗（HTTP 500）").text).toBe(
      "刪除失敗（HTTP 500）",
    );
    expect(readProblemDetail("nope", "刪除失敗（HTTP 500）").text).toBe(
      "刪除失敗（HTTP 500）",
    );
  });
});
