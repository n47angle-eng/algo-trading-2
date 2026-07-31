import { describe, expect, it } from "vitest";

import {
  buildPaperReviewCreateRequest,
  paperLedgerMemberPaths,
  paperReviewCompactUtc,
  paperReviewErrorHttpStatus,
  paperReviewErrorRetryable,
  paperReviewMemberPaths,
  parsePaperLedgerOrigin,
  parsePaperReviewErrorEnvelope,
  parsePaperReviewErrorObject,
  parsePaperReviewReady,
  parsePaperReviewStatus,
  parsePaperReviewTerminalOpenerClaim,
  type PaperReviewErrorExpectation,
  type PaperReviewReadyExpectation,
  type PaperReviewStatusExpectation,
} from "./reviewContract";
import {
  PAPER_REVIEW_ERROR_HTTP,
  type PaperReviewErrorCode,
} from "./types";

/*
 * Stage B engineering target — allowed in tests only. Product code must never
 * hard-code these identities, the capital or the rejection counts.
 * Source: approved bridge design §4 and §8.
 */
const TRADER_ID = "trader-1f2e3d4c5b6a798877665544332211ff";
const ACCOUNT_ID = "paper-account-aabbccddeeff00112233445566778899";
const LEDGER_ID = "paper-ledger-0123456789abcdef0123456789abcdef";
const SNAPSHOT_ID = "paper-review-fedcba9876543210fedcba9876543210";
const REQUEST_ID = "3f2b1a4c-9d8e-4f6a-8b7c-1d2e3f4a5b6c";
const RUN_ID = "nq-20260728-standard-365adf";
const STRATEGY_ID = "strategy-0003";
const CONTENT_SHA =
  "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97";
const RESULT_SHA =
  "5edc3cf2e5d314ddc2594f57cfbc52987e35b855d804c147f86d7e6e3cf486f7";
const TRADES_SHA =
  "9f3c4e1cf03d305abd42deadf4f0e940754d8d99cf4e9a52cf74057ed03c6b69";
const EQUITY_SHA =
  "4b38517f2b73ed6b9a986096162d5a5cb030117db69c14e84a6309edfe898346";
const EVENTS_SHA =
  "094ebfbc9e010311292ae4005ce58de187a0aae471c32dc18ea53868629c1d30";
const ARTIFACT_SHA =
  "1111111111111111111111111111111111111111111111111111111111111111";
const AT = "2026-07-29T00:00:00Z";
const COMPACT_AT = "20260729T000000000000Z";

function clone<T>(value: T): T {
  return structuredClone(value);
}

/**
 * The path helpers now return `null` for an unsafe run id. This wrapper throws
 * instead of silently substituting, so a broken helper cannot make a test pass.
 */
function reviewPaths(runId: string = RUN_ID): readonly string[] {
  const paths = paperReviewMemberPaths(runId);
  if (paths === null) {
    throw new Error(`expected a safe run id, got ${JSON.stringify(runId)}`);
  }
  return paths;
}

/** A traversal identity that would escape the ZIP if it reached a path. */
const UNSAFE_RUN_ID = "../escape";
const UNSAFE_TRADER_ID = "trader-../escape";

function checkRows() {
  return [
    {
      key: "ib_realtime",
      status: "ready",
      reason: "isolated provider confirmed for this integration runtime",
      checked_at: AT,
    },
    {
      key: "exchange_calendar",
      status: "ready",
      reason: "isolated provider confirmed for this integration runtime",
      checked_at: AT,
    },
    {
      key: "telegram",
      status: "ready",
      reason: "isolated provider confirmed for this integration runtime",
      checked_at: AT,
    },
    {
      key: "baseline_integrity",
      status: "ready",
      reason: "locked result package verified",
      checked_at: AT,
    },
  ];
}

/** Canonical target ledger origin from approved design §4 and §8. */
function ledgerBody() {
  return {
    schema: "paper_ledger_origin.v1",
    ledger_origin_id: LEDGER_ID,
    trader_id: TRADER_ID,
    origin_at: AT,
    lifecycle: { status: "provisioned", engine_status: "not_enabled" },
    strategy: {
      strategy_id: STRATEGY_ID,
      name: "Trend 回踩 18EMA · engineering activation smoke",
      content_sha256: CONTENT_SHA,
    },
    contract: {
      contract_id: "NQ-202609-CME",
      exchange: "CME",
      timezone: "America/Chicago",
    },
    baseline: {
      run_id: RUN_ID,
      result_sha256: RESULT_SHA,
      range_start: "2026-07-22T22:00:00Z",
      range_end: "2026-07-23T21:00:00Z",
      rejection_count: 14,
      closest_algorithm: "p5_structural_closest.v1",
      closest_rejection_refs: [
        "rejection_000014",
        "rejection_000013",
        "rejection_000012",
      ],
      members: [
        { path: "baseline/result.json", bytes: 8776, sha256: RESULT_SHA },
        {
          path: `baseline/trades/${RUN_ID}.json`,
          bytes: 109,
          sha256: TRADES_SHA,
        },
        {
          path: `baseline/equity/${RUN_ID}.json`,
          bytes: 81,
          sha256: EQUITY_SHA,
        },
        {
          path: `baseline/events/${RUN_ID}.json`,
          bytes: 35136,
          sha256: EVENTS_SHA,
        },
      ],
    },
    account: {
      account_id: ACCOUNT_ID,
      currency: "USD",
      initial_capital: 100000.0,
      independent_account: true,
    },
    balances: {
      cash: 100000.0,
      equity: 100000.0,
      realized_pnl: 0,
      unrealized_pnl: 0,
    },
    high_water_marks: {
      trades: 0,
      equity: 1,
      events: 4,
      expected_decisions: 0,
      positions: 0,
      orders: 0,
    },
    positions: [],
    orders: [],
    safety: {
      state: "not_running",
      drawdown_r: 0,
      loss_streak: 0,
      max_drawdown_r: 8,
      max_losing_streak: 8,
      blind_minutes: 5,
    },
    readiness_snapshot: {
      schema: "paper_readiness_snapshot.v1",
      overall: "ready",
      market_session: "closed",
      checked_at: AT,
      checks: checkRows(),
    },
    interpretation: {
      evaluation_status: "not_evaluable",
      reason: "engine_not_enabled",
      owner_view:
        "模擬引擎尚未啟用；目前只證明初始帳戶、鎖定baseline及建立證據完整，未能判斷真實模擬盤偏離。",
      categories: ["unknown"],
      supporting_evidence_refs: [
        {
          path: `baseline/events/${RUN_ID}.json`,
          evidence_id: "rejection_000014",
        },
        {
          path: `baseline/events/${RUN_ID}.json`,
          evidence_id: "rejection_000013",
        },
        {
          path: `baseline/events/${RUN_ID}.json`,
          evidence_id: "rejection_000012",
        },
      ],
    },
  };
}

const LEDGER_EXPECTED = { trader_id: TRADER_ID };

function readyMembers() {
  return reviewPaths().map((path, index) => ({
    path,
    bytes: 100 + index,
    sha256: ARTIFACT_SHA,
  }));
}

function readyBody() {
  return {
    schema: "paper_review_ready.v1",
    display_filename: `paper-review-${TRADER_ID}-${COMPACT_AT}.zip`,
    artifact_bytes: 45678,
    artifact_sha256: ARTIFACT_SHA,
    member_count: 10,
    members: readyMembers(),
    terminal_opener: { bytes: 1234, sha256: ARTIFACT_SHA },
    ready_at: AT,
  };
}

const READY_EXPECTED: PaperReviewReadyExpectation = {
  trader_id: TRADER_ID,
  captured_at: AT,
  baseline_run_id: RUN_ID,
};

const STATUS_EXPECTED: PaperReviewStatusExpectation = {
  request_id: REQUEST_ID,
  trader_id: TRADER_ID,
  baseline_run_id: RUN_ID,
};

interface StatusFixture {
  schema: string;
  request_id: string;
  snapshot_id: string;
  trader_id: string;
  status: string;
  captured_at: string;
  progress: {
    completed_parts: number;
    total_parts: number;
    current_part: string | null;
  };
  ready: unknown;
  error: unknown;
}

function statusBase(): StatusFixture {
  return {
    schema: "paper_review_status.v1",
    request_id: REQUEST_ID,
    snapshot_id: SNAPSHOT_ID,
    trader_id: TRADER_ID,
    status: "preparing",
    captured_at: AT,
    progress: {
      completed_parts: 4,
      total_parts: 10,
      current_part: "paper/events.json",
    },
    ready: null,
    error: null,
  };
}

function preparingBody() {
  return statusBase();
}

function readyStatusBody() {
  const body = statusBase();
  body.status = "ready";
  body.progress = {
    completed_parts: 10,
    total_parts: 10,
    current_part: null,
  };
  body.ready = readyBody();
  return body;
}

interface IssueFixture {
  kind: string;
  path: string | null;
  source_ref: string | null;
  expected_sha256: string | null;
  actual_sha256: string | null;
  ref_chain: string[];
}

interface ErrorFixture {
  schema: string;
  code: string;
  message: string;
  retryable: boolean;
  request_id: string | null;
  snapshot_id: string | null;
  progress: {
    completed_parts: number;
    total_parts: number;
    current_part: string | null;
  } | null;
  issues: IssueFixture[];
}

function errorInner(overrides: Record<string, unknown> = {}): ErrorFixture {
  return {
    schema: "paper_review_error.v1",
    code: "snapshot_integrity_failed",
    message: "鎖定baseline有一個member雜湊不一致。",
    retryable: false,
    request_id: REQUEST_ID,
    snapshot_id: SNAPSHOT_ID,
    progress: { completed_parts: 7, total_parts: 10, current_part: null },
    issues: [
      {
        kind: "hash_mismatch",
        path: `baseline/events/${RUN_ID}.json`,
        source_ref: "baseline/result.json#events_ref",
        expected_sha256: EVENTS_SHA,
        actual_sha256: ARTIFACT_SHA,
        ref_chain: [
          "paper-review.json#baseline.result_ref",
          "baseline/result.json#events_ref",
        ],
      },
    ],
    ...overrides,
  } as ErrorFixture;
}

const ACCEPTED_ERROR_EXPECTED: PaperReviewErrorExpectation = {
  mode: "accepted_known",
  request_id: REQUEST_ID,
  snapshot_id: SNAPSHOT_ID,
};

function failedStatusBody() {
  const body = statusBase();
  body.status = "failed";
  body.progress = {
    completed_parts: 7,
    total_parts: 10,
    current_part: null,
  };
  body.error = errorInner();
  return body;
}

function openerBody() {
  return {
    schema: "paper_review_terminal_opener.v1",
    snapshot_id: SNAPSHOT_ID,
    text: "你會收到一個不可變 P6 review ZIP：…\n",
    bytes: 1234,
    sha256: ARTIFACT_SHA,
  };
}

// --- helpers ----------------------------------------------------------------

describe("Stage B path and identity helpers", () => {
  it("derives the four ordered ledger member paths", () => {
    expect(paperLedgerMemberPaths(RUN_ID)).toEqual([
      "baseline/result.json",
      `baseline/trades/${RUN_ID}.json`,
      `baseline/equity/${RUN_ID}.json`,
      `baseline/events/${RUN_ID}.json`,
    ]);
  });

  it("derives the ten ordered review member paths", () => {
    const paths = reviewPaths();
    expect(paths).toHaveLength(10);
    expect(paths[0]).toBe("paper-review.json");
    expect(paths[1]).toBe("paper/ledger-origin.json");
    expect(paths[5]).toBe("divergence/expected-actual.json");
    expect(paths[9]).toBe(`baseline/events/${RUN_ID}.json`);
  });

  it("always renders six microsecond digits in the compact stamp", () => {
    expect(paperReviewCompactUtc(AT)).toBe(COMPACT_AT);
    expect(paperReviewCompactUtc("2026-07-29T00:00:00.123456Z")).toBe(
      "20260729T000000123456Z",
    );
    expect(paperReviewCompactUtc(AT)).toHaveLength(22);
    expect(paperReviewCompactUtc("2026-07-29T00:00:00.000000Z")).toBeNull();
    expect(paperReviewCompactUtc("0000-01-01T00:00:00Z")).toBeNull();
  });

  it("maps every approved code to its HTTP status and retryable flag", () => {
    for (const code of Object.keys(
      PAPER_REVIEW_ERROR_HTTP,
    ) as PaperReviewErrorCode[]) {
      expect(paperReviewErrorHttpStatus(code)).toBe(
        PAPER_REVIEW_ERROR_HTTP[code],
      );
      expect(paperReviewErrorRetryable(code)).toBe(
        code === "snapshot_not_ready",
      );
    }
  });
});

// --- §9.1 create request serializer -----------------------------------------

describe("paper_review_create_request.v1 serializer", () => {
  it("emits exactly schema and request_id", () => {
    const built = buildPaperReviewCreateRequest(REQUEST_ID);
    expect(built.ok).toBe(true);
    if (built.ok) {
      expect(Object.keys(built.value).sort()).toEqual([
        "request_id",
        "schema",
      ]);
      expect(built.value.schema).toBe("paper_review_create_request.v1");
      expect(built.value.request_id).toBe(REQUEST_ID);
    }
  });

  it("refuses anything that is not a canonical lowercase UUID4", () => {
    for (const bad of [
      REQUEST_ID.toUpperCase(),
      "00000000-0000-1000-8000-000000000000",
      "00000000-0000-4000-c000-000000000000",
      "not-a-uuid",
      "",
    ]) {
      expect(buildPaperReviewCreateRequest(bad).ok).toBe(false);
    }
  });
});

// --- §8 ledger origin -------------------------------------------------------

describe("paper_ledger_origin.v1 strict parser", () => {
  it("accepts the canonical target ledger origin", () => {
    const parsed = parsePaperLedgerOrigin(ledgerBody(), LEDGER_EXPECTED);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.baseline.rejection_count).toBe(14);
      expect(parsed.value.baseline.closest_rejection_refs).toEqual([
        "rejection_000014",
        "rejection_000013",
        "rejection_000012",
      ]);
      expect(parsed.value.high_water_marks).toEqual({
        trades: 0,
        equity: 1,
        events: 4,
        expected_decisions: 0,
        positions: 0,
        orders: 0,
      });
      expect(parsed.value.positions).toEqual([]);
      expect(parsed.value.orders).toEqual([]);
      expect(parsed.value.interpretation.evaluation_status).toBe(
        "not_evaluable",
      );
      expect(parsed.value.balances.cash).toBe(100000);
    }
  });

  it("rejects a top-level extra, missing or null key", () => {
    const extra = { ...ledgerBody(), engine_state: "running" };
    expect(parsePaperLedgerOrigin(extra, LEDGER_EXPECTED).ok).toBe(false);

    const missing = clone(ledgerBody()) as unknown as Record<string, unknown>;
    delete missing.balances;
    expect(parsePaperLedgerOrigin(missing, LEDGER_EXPECTED).ok).toBe(false);

    const nulled = clone(ledgerBody()) as unknown as Record<string, unknown>;
    nulled.safety = null;
    expect(parsePaperLedgerOrigin(nulled, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("rejects a nested extra, missing or null key", () => {
    const extra = clone(ledgerBody()) as unknown as {
      balances: Record<string, unknown>;
    };
    extra.balances.margin = 0;
    expect(parsePaperLedgerOrigin(extra, LEDGER_EXPECTED).ok).toBe(false);

    const missing = clone(ledgerBody()) as unknown as {
      contract: Record<string, unknown>;
    };
    delete missing.contract.timezone;
    expect(parsePaperLedgerOrigin(missing, LEDGER_EXPECTED).ok).toBe(false);

    const nulled = clone(ledgerBody()) as unknown as {
      strategy: Record<string, unknown>;
    };
    nulled.strategy.name = null;
    expect(parsePaperLedgerOrigin(nulled, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("rejects the wrong schema and a cross-trader identity", () => {
    const wrongSchema = { ...ledgerBody(), schema: "paper_ledger_origin.v2" };
    expect(parsePaperLedgerOrigin(wrongSchema, LEDGER_EXPECTED).ok).toBe(false);

    expect(
      parsePaperLedgerOrigin(ledgerBody(), {
        trader_id: "trader-00000000000000000000000000000000",
      }).ok,
    ).toBe(false);
  });

  it("rejects malformed prefixed IDs and SHA values", () => {
    const badLedger = { ...ledgerBody(), ledger_origin_id: "ledger-123" };
    expect(parsePaperLedgerOrigin(badLedger, LEDGER_EXPECTED).ok).toBe(false);

    const badAccount = clone(ledgerBody());
    badAccount.account.account_id = "paper-account-XYZ";
    expect(parsePaperLedgerOrigin(badAccount, LEDGER_EXPECTED).ok).toBe(false);

    const badSha = clone(ledgerBody());
    badSha.strategy.content_sha256 = CONTENT_SHA.toUpperCase();
    expect(parsePaperLedgerOrigin(badSha, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("accepts canonical UTC years 0001, 0099, 0100 and 9999", () => {
    for (const value of [
      "0001-01-01T00:00:00Z",
      "0099-12-31T23:59:59Z",
      "0100-01-01T00:00:00Z",
      "9999-12-31T23:59:59Z",
      "2026-07-29T00:00:00.000001Z",
    ]) {
      const body = clone(ledgerBody());
      body.origin_at = value;
      expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(true);
    }
  });

  it("rejects year 0000, .000000Z, bad fractions, offsets and lowercase z", () => {
    for (const value of [
      "0000-01-01T00:00:00Z",
      "2026-07-29T00:00:00.000000Z",
      "2026-07-29T00:00:00.1Z",
      "2026-07-29T00:00:00.12345Z",
      "2026-07-29T00:00:00.1234567Z",
      "2026-07-29T00:00:00+00:00",
      "2026-07-29T00:00:00z",
      "2026-02-30T00:00:00Z",
    ]) {
      const body = clone(ledgerBody());
      body.origin_at = value;
      expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(false);
    }
  });

  it("rejects booleans, NaN, Infinity and negative zero", () => {
    const boolCapital = clone(ledgerBody()) as unknown as {
      account: Record<string, unknown>;
    };
    boolCapital.account.initial_capital = true;
    expect(parsePaperLedgerOrigin(boolCapital, LEDGER_EXPECTED).ok).toBe(false);

    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, -0]) {
      const body = clone(ledgerBody()) as unknown as {
        balances: Record<string, unknown>;
      };
      body.balances.realized_pnl = bad;
      expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(false);
    }
  });

  it("rejects an unsafe or fractional count", () => {
    for (const bad of [Number.MAX_SAFE_INTEGER + 1, 14.5, -1, "14"]) {
      const body = clone(ledgerBody()) as unknown as {
        baseline: Record<string, unknown>;
      };
      body.baseline.rejection_count = bad;
      expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(false);
    }
  });

  it("rejects an unknown lifecycle, engine, safety or category value", () => {
    const running = clone(ledgerBody());
    running.lifecycle.status = "running";
    expect(parsePaperLedgerOrigin(running, LEDGER_EXPECTED).ok).toBe(false);

    const enabled = clone(ledgerBody());
    enabled.lifecycle.engine_status = "enabled";
    expect(parsePaperLedgerOrigin(enabled, LEDGER_EXPECTED).ok).toBe(false);

    const armed = clone(ledgerBody());
    armed.safety.state = "armed";
    expect(parsePaperLedgerOrigin(armed, LEDGER_EXPECTED).ok).toBe(false);

    const categories = clone(ledgerBody());
    categories.interpretation.categories = ["execution"];
    expect(parsePaperLedgerOrigin(categories, LEDGER_EXPECTED).ok).toBe(false);

    const evaluated = clone(ledgerBody());
    evaluated.interpretation.evaluation_status = "evaluable";
    expect(parsePaperLedgerOrigin(evaluated, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("rejects any high-water-mark drift", () => {
    const drifts: [string, number][] = [
      ["trades", 1],
      ["equity", 0],
      ["events", 3],
      ["expected_decisions", 1],
      ["positions", 1],
      ["orders", 1],
    ];
    for (const [key, value] of drifts) {
      const body = clone(ledgerBody()) as unknown as {
        high_water_marks: Record<string, number>;
      };
      body.high_water_marks[key] = value;
      expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(false);
    }
  });

  it("rejects a non-empty positions or orders array", () => {
    for (const key of ["positions", "orders"] as const) {
      const body = clone(ledgerBody()) as unknown as unknown as Record<string, unknown>;
      body[key] = [{ contract_id: "NQ-202609-CME" }];
      expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(false);
    }
  });

  it("rejects balances that are not a zero-trade origin", () => {
    const drifted = clone(ledgerBody());
    drifted.balances.equity = 100001;
    expect(parsePaperLedgerOrigin(drifted, LEDGER_EXPECTED).ok).toBe(false);

    const pnl = clone(ledgerBody());
    pnl.balances.unrealized_pnl = 145;
    expect(parsePaperLedgerOrigin(pnl, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("rejects reordered, duplicated or drifted baseline members", () => {
    const reordered = clone(ledgerBody());
    const rows = reordered.baseline.members;
    reordered.baseline.members = [rows[1], rows[0], rows[2], rows[3]];
    expect(parsePaperLedgerOrigin(reordered, LEDGER_EXPECTED).ok).toBe(false);

    const duplicated = clone(ledgerBody());
    duplicated.baseline.members = [rows[0], rows[0], rows[2], rows[3]];
    expect(parsePaperLedgerOrigin(duplicated, LEDGER_EXPECTED).ok).toBe(false);

    const wrongRun = clone(ledgerBody());
    wrongRun.baseline.members[3].path = "baseline/events/other-run.json";
    expect(parsePaperLedgerOrigin(wrongRun, LEDGER_EXPECTED).ok).toBe(false);

    const zeroBytes = clone(ledgerBody());
    zeroBytes.baseline.members[2].bytes = 0;
    expect(parsePaperLedgerOrigin(zeroBytes, LEDGER_EXPECTED).ok).toBe(false);

    const three = clone(ledgerBody());
    three.baseline.members = three.baseline.members.slice(0, 3);
    expect(parsePaperLedgerOrigin(three, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("rejects a baseline result hash that disagrees with the main member", () => {
    const body = clone(ledgerBody());
    body.baseline.members[0].sha256 = ARTIFACT_SHA;
    expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("rejects an unknown closest algorithm, duplicates or count drift", () => {
    const unknown = clone(ledgerBody());
    unknown.baseline.closest_algorithm = "latest_three.v1";
    expect(parsePaperLedgerOrigin(unknown, LEDGER_EXPECTED).ok).toBe(false);

    const duplicated = clone(ledgerBody());
    duplicated.baseline.closest_rejection_refs = [
      "rejection_000014",
      "rejection_000014",
      "rejection_000012",
    ];
    expect(parsePaperLedgerOrigin(duplicated, LEDGER_EXPECTED).ok).toBe(false);

    const tooMany = clone(ledgerBody());
    tooMany.baseline.closest_rejection_refs = [
      "rejection_000014",
      "rejection_000013",
      "rejection_000012",
      "rejection_000011",
    ];
    expect(parsePaperLedgerOrigin(tooMany, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("keeps the producer order and never re-sorts the closest refs", () => {
    const body = clone(ledgerBody());
    body.baseline.closest_rejection_refs = [
      "rejection_000012",
      "rejection_000014",
      "rejection_000013",
    ];
    body.interpretation.supporting_evidence_refs = [
      { path: `baseline/events/${RUN_ID}.json`, evidence_id: "rejection_000012" },
      { path: `baseline/events/${RUN_ID}.json`, evidence_id: "rejection_000014" },
      { path: `baseline/events/${RUN_ID}.json`, evidence_id: "rejection_000013" },
    ];
    const parsed = parsePaperLedgerOrigin(body, LEDGER_EXPECTED);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.baseline.closest_rejection_refs).toEqual([
        "rejection_000012",
        "rejection_000014",
        "rejection_000013",
      ]);
    }
  });

  it("reconciles the closest refs with the count and with the interpretation", () => {
    const fewer = clone(ledgerBody());
    fewer.baseline.rejection_count = 2;
    expect(parsePaperLedgerOrigin(fewer, LEDGER_EXPECTED).ok).toBe(false);

    const mismatch = clone(ledgerBody());
    mismatch.interpretation.supporting_evidence_refs[1].evidence_id =
      "rejection_000009";
    expect(parsePaperLedgerOrigin(mismatch, LEDGER_EXPECTED).ok).toBe(false);

    const wrongPath = clone(ledgerBody());
    wrongPath.interpretation.supporting_evidence_refs[0].path =
      "baseline/result.json";
    expect(parsePaperLedgerOrigin(wrongPath, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("accepts a zero-rejection baseline with no closest refs", () => {
    const body = clone(ledgerBody());
    body.baseline.rejection_count = 0;
    body.baseline.closest_rejection_refs = [];
    body.interpretation.supporting_evidence_refs = [];
    expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(true);
  });

  it("rejects a readiness snapshot with drifted order, timestamps or status", () => {
    const reordered = clone(ledgerBody());
    const rows = reordered.readiness_snapshot.checks;
    reordered.readiness_snapshot.checks = [rows[1], rows[0], rows[2], rows[3]];
    expect(parsePaperLedgerOrigin(reordered, LEDGER_EXPECTED).ok).toBe(false);

    const drifted = clone(ledgerBody());
    drifted.readiness_snapshot.checks[2].checked_at = "2026-07-29T00:00:01Z";
    expect(parsePaperLedgerOrigin(drifted, LEDGER_EXPECTED).ok).toBe(false);

    /*
     * Option A: the persisted snapshot of a provision-only trader may be
     * blocked, so the ledger consumer accepts it — and still refuses either
     * direction of drift between `overall` and the four checks.
     */
    const blocked = clone(ledgerBody());
    blocked.readiness_snapshot.checks[0].status = "blocked";
    blocked.readiness_snapshot.overall = "blocked";
    expect(parsePaperLedgerOrigin(blocked, LEDGER_EXPECTED).ok).toBe(true);

    const overstated = clone(ledgerBody());
    overstated.readiness_snapshot.checks[0].status = "unknown";
    expect(parsePaperLedgerOrigin(overstated, LEDGER_EXPECTED).ok).toBe(false);

    const understated = clone(ledgerBody());
    understated.readiness_snapshot.overall = "blocked";
    expect(parsePaperLedgerOrigin(understated, LEDGER_EXPECTED).ok).toBe(false);
  });

  it("accepts the real A2 ledger: blocked snapshot, exact identity", () => {
    const body = clone(ledgerBody());
    body.readiness_snapshot.checks[0].status = "unknown";
    body.readiness_snapshot.checks[1].status = "unknown";
    body.readiness_snapshot.checks[2].status = "blocked";
    body.readiness_snapshot.overall = "blocked";
    const parsed = parsePaperLedgerOrigin(body, LEDGER_EXPECTED);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.readiness_snapshot.overall).toBe("blocked");
      expect(
        parsed.value.readiness_snapshot.checks.map((row) => row.status),
      ).toEqual(["unknown", "unknown", "blocked", "ready"]);
      // A blocked snapshot never implies a running engine.
      expect(parsed.value.lifecycle.engine_status).toBe("not_enabled");
      expect(parsed.value.trader_id).toBe(LEDGER_EXPECTED.trader_id);
    }
  });

  it("rejects safeguard drift", () => {
    for (const [key, value] of [
      ["max_drawdown_r", 9],
      ["max_losing_streak", 7],
      ["blind_minutes", 6],
      ["drawdown_r", 1],
      ["loss_streak", 1],
    ] as [string, number][]) {
      const body = clone(ledgerBody()) as unknown as {
        safety: Record<string, number | string>;
      };
      body.safety[key] = value;
      expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(false);
    }
  });

  it("rejects independent_account that is not exactly true", () => {
    const body = clone(ledgerBody()) as unknown as {
      account: Record<string, unknown>;
    };
    body.account.independent_account = "true";
    expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(false);
  });
});

// --- §9.2 status -----------------------------------------------------------

describe("paper_review_status.v1 strict parser", () => {
  it("accepts the preparing branch", () => {
    const parsed = parsePaperReviewStatus(preparingBody(), STATUS_EXPECTED);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.status).toBe("preparing");
      expect(parsed.value.progress.current_part).toBe("paper/events.json");
      expect(parsed.value.ready).toBeNull();
      expect(parsed.value.error).toBeNull();
    }
  });

  it("accepts the ready branch with ten ordered members", () => {
    const parsed = parsePaperReviewStatus(readyStatusBody(), STATUS_EXPECTED);
    expect(parsed.ok).toBe(true);
    if (parsed.ok && parsed.value.ready) {
      expect(parsed.value.ready.member_count).toBe(10);
      expect(parsed.value.ready.members.map((row) => row.path)).toEqual(
        reviewPaths(),
      );
      expect(parsed.value.ready.display_filename).toBe(
        `paper-review-${TRADER_ID}-${COMPACT_AT}.zip`,
      );
    }
  });

  it("accepts the failed branch carrying the inner error object", () => {
    const parsed = parsePaperReviewStatus(failedStatusBody(), STATUS_EXPECTED);
    expect(parsed.ok).toBe(true);
    if (parsed.ok && parsed.value.error) {
      expect(parsed.value.error.code).toBe("snapshot_integrity_failed");
      expect(parsed.value.error.progress?.completed_parts).toBe(7);
      expect(parsed.value.ready).toBeNull();
    }
  });

  it("rejects every invalid ready, error and progress combination", () => {
    const preparingWithReady = preparingBody() as unknown as Record<string, unknown>;
    preparingWithReady.ready = readyBody();
    expect(
      parsePaperReviewStatus(preparingWithReady, STATUS_EXPECTED).ok,
    ).toBe(false);

    const preparingWithError = preparingBody() as unknown as Record<string, unknown>;
    preparingWithError.error = errorInner();
    expect(
      parsePaperReviewStatus(preparingWithError, STATUS_EXPECTED).ok,
    ).toBe(false);

    const readyWithoutReady = readyStatusBody();
    readyWithoutReady.ready = null;
    expect(parsePaperReviewStatus(readyWithoutReady, STATUS_EXPECTED).ok).toBe(
      false,
    );

    const readyWithError = readyStatusBody();
    readyWithError.error = errorInner();
    expect(parsePaperReviewStatus(readyWithError, STATUS_EXPECTED).ok).toBe(
      false,
    );

    const failedWithReady = failedStatusBody();
    failedWithReady.ready = readyBody();
    expect(parsePaperReviewStatus(failedWithReady, STATUS_EXPECTED).ok).toBe(
      false,
    );

    const failedWithoutError = failedStatusBody();
    failedWithoutError.error = null;
    expect(parsePaperReviewStatus(failedWithoutError, STATUS_EXPECTED).ok).toBe(
      false,
    );

    const nullProgress = preparingBody() as unknown as Record<string, unknown>;
    nullProgress.progress = null;
    expect(parsePaperReviewStatus(nullProgress, STATUS_EXPECTED).ok).toBe(false);
  });

  it("requires ten completed parts and a null current part when ready", () => {
    const notTen = readyStatusBody();
    notTen.progress = {
      completed_parts: 9,
      total_parts: 10,
      current_part: null,
    };
    expect(parsePaperReviewStatus(notTen, STATUS_EXPECTED).ok).toBe(false);

    const stillWorking = readyStatusBody();
    stillWorking.progress = {
      completed_parts: 10,
      total_parts: 10,
      current_part: "paper/trades.json",
    };
    expect(parsePaperReviewStatus(stillWorking, STATUS_EXPECTED).ok).toBe(false);
  });

  it("rejects a current part outside the ten members and a wrong total", () => {
    const outside = preparingBody();
    outside.progress.current_part = "paper/positions.json";
    expect(parsePaperReviewStatus(outside, STATUS_EXPECTED).ok).toBe(false);

    const otherRun = preparingBody();
    outside.progress.current_part = `baseline/events/other-run.json`;
    expect(parsePaperReviewStatus(outside, STATUS_EXPECTED).ok).toBe(false);
    otherRun.progress.total_parts = 9;
    expect(parsePaperReviewStatus(otherRun, STATUS_EXPECTED).ok).toBe(false);
  });

  it("rejects an unknown status value and the wrong schema", () => {
    const unknown = preparingBody();
    unknown.status = "queued";
    expect(parsePaperReviewStatus(unknown, STATUS_EXPECTED).ok).toBe(false);

    const wrongSchema = preparingBody();
    wrongSchema.schema = "paper_review_status.v2";
    expect(parsePaperReviewStatus(wrongSchema, STATUS_EXPECTED).ok).toBe(false);
  });

  it("rejects a cross-request or cross-trader status", () => {
    expect(
      parsePaperReviewStatus(preparingBody(), {
        ...STATUS_EXPECTED,
        request_id: "00000000-0000-4000-8000-000000000000",
      }).ok,
    ).toBe(false);

    expect(
      parsePaperReviewStatus(preparingBody(), {
        ...STATUS_EXPECTED,
        trader_id: "trader-00000000000000000000000000000000",
      }).ok,
    ).toBe(false);
  });

  it("rejects a failed status whose error identity drifts", () => {
    const body = failedStatusBody();
    body.error = errorInner({
      snapshot_id: "paper-review-00000000000000000000000000000000",
    });
    expect(parsePaperReviewStatus(body, STATUS_EXPECTED).ok).toBe(false);
  });
});

// --- §9.2 ready -------------------------------------------------------------

describe("paper_review_ready.v1 strict parser", () => {
  it("accepts the canonical ready payload", () => {
    expect(parsePaperReviewReady(readyBody(), READY_EXPECTED).ok).toBe(true);
  });

  it("rejects a member count or member list that is not ten", () => {
    const nine = clone(readyBody());
    nine.members = nine.members.slice(0, 9);
    expect(parsePaperReviewReady(nine, READY_EXPECTED).ok).toBe(false);

    const wrongCount = clone(readyBody());
    wrongCount.member_count = 9;
    expect(parsePaperReviewReady(wrongCount, READY_EXPECTED).ok).toBe(false);
  });

  it("rejects reordered members and a drifted baseline run", () => {
    const reordered = clone(readyBody());
    const rows = reordered.members;
    reordered.members = [rows[1], rows[0], ...rows.slice(2)];
    expect(parsePaperReviewReady(reordered, READY_EXPECTED).ok).toBe(false);

    expect(
      parsePaperReviewReady(readyBody(), {
        ...READY_EXPECTED,
        baseline_run_id: "other-run",
      }).ok,
    ).toBe(false);
  });

  it("rejects a filename that is not trader identity plus compact capture time", () => {
    const secondsOnly = clone(readyBody());
    secondsOnly.display_filename = `paper-review-${TRADER_ID}-20260729T000000Z.zip`;
    expect(parsePaperReviewReady(secondsOnly, READY_EXPECTED).ok).toBe(false);

    const otherTrader = clone(readyBody());
    otherTrader.display_filename =
      "paper-review-trader-00000000000000000000000000000000-20260729T000000000000Z.zip";
    expect(parsePaperReviewReady(otherTrader, READY_EXPECTED).ok).toBe(false);

    const otherTime = clone(readyBody());
    expect(
      parsePaperReviewReady(otherTime, {
        ...READY_EXPECTED,
        captured_at: "2026-07-29T00:00:01Z",
      }).ok,
    ).toBe(false);
  });

  it("rejects zero or unsafe artifact and opener byte counts", () => {
    for (const bad of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, true]) {
      const body = clone(readyBody()) as unknown as unknown as Record<string, unknown>;
      body.artifact_bytes = bad;
      expect(parsePaperReviewReady(body, READY_EXPECTED).ok).toBe(false);
    }
    const opener = clone(readyBody());
    opener.terminal_opener.bytes = 0;
    expect(parsePaperReviewReady(opener, READY_EXPECTED).ok).toBe(false);
  });

  it("rejects an extra or missing nested key", () => {
    const extra = clone(readyBody()) as unknown as {
      terminal_opener: Record<string, unknown>;
    };
    extra.terminal_opener.text = "…";
    expect(parsePaperReviewReady(extra, READY_EXPECTED).ok).toBe(false);

    const missing = clone(readyBody()) as unknown as unknown as Record<string, unknown>;
    delete missing.ready_at;
    expect(parsePaperReviewReady(missing, READY_EXPECTED).ok).toBe(false);
  });
});

// --- §10 error --------------------------------------------------------------

describe("paper_review_error.v1 strict parser", () => {
  it("accepts the inner object and the HTTP envelope separately", () => {
    expect(
      parsePaperReviewErrorObject(errorInner(), ACCEPTED_ERROR_EXPECTED).ok,
    ).toBe(true);
    expect(
      parsePaperReviewErrorEnvelope(
        { detail: errorInner() },
        ACCEPTED_ERROR_EXPECTED,
      ).ok,
    ).toBe(true);
  });

  it("never lets the outer wrapper and the inner object be interchanged", () => {
    expect(
      parsePaperReviewErrorObject(
        { detail: errorInner() },
        ACCEPTED_ERROR_EXPECTED,
      ).ok,
    ).toBe(false);
    expect(
      parsePaperReviewErrorEnvelope(errorInner(), ACCEPTED_ERROR_EXPECTED).ok,
    ).toBe(false);
  });

  it("rejects an outer wrapper with any extra key", () => {
    expect(
      parsePaperReviewErrorEnvelope(
        { detail: errorInner(), status: 503 },
        ACCEPTED_ERROR_EXPECTED,
      ).ok,
    ).toBe(false);
  });

  it("accepts every approved code with its exact retryable flag", () => {
    for (const code of Object.keys(
      PAPER_REVIEW_ERROR_HTTP,
    ) as PaperReviewErrorCode[]) {
      const body = errorInner({
        code,
        retryable: code === "snapshot_not_ready",
      });
      const parsed = parsePaperReviewErrorObject(body, ACCEPTED_ERROR_EXPECTED);
      expect(parsed.ok).toBe(true);
      if (parsed.ok) {
        expect(paperReviewErrorHttpStatus(parsed.value.code)).toBe(
          PAPER_REVIEW_ERROR_HTTP[code],
        );
      }
    }
  });

  it("rejects a retryable flag that disagrees with the code", () => {
    expect(
      parsePaperReviewErrorObject(
        errorInner({ retryable: true }),
        ACCEPTED_ERROR_EXPECTED,
      ).ok,
    ).toBe(false);
    expect(
      parsePaperReviewErrorObject(
        errorInner({ code: "snapshot_not_ready", retryable: false }),
        ACCEPTED_ERROR_EXPECTED,
      ).ok,
    ).toBe(false);
  });

  it("rejects an unknown code and an unknown issue kind", () => {
    expect(
      parsePaperReviewErrorObject(
        errorInner({ code: "teapot" }),
        ACCEPTED_ERROR_EXPECTED,
      ).ok,
    ).toBe(false);

    const body = errorInner();
    body.issues[0].kind = "zip_too_large";
    expect(parsePaperReviewErrorObject(body, ACCEPTED_ERROR_EXPECTED).ok).toBe(
      false,
    );
  });

  it("requires every issue field to be present, using explicit null", () => {
    const missing = errorInner() as unknown as {
      issues: Record<string, unknown>[];
    };
    delete missing.issues[0].source_ref;
    expect(
      parsePaperReviewErrorObject(missing, ACCEPTED_ERROR_EXPECTED).ok,
    ).toBe(false);

    const nulled = errorInner();
    nulled.issues[0].path = null;
    nulled.issues[0].source_ref = null;
    nulled.issues[0].expected_sha256 = null;
    nulled.issues[0].actual_sha256 = null;
    nulled.issues[0].ref_chain = [];
    expect(parsePaperReviewErrorObject(nulled, ACCEPTED_ERROR_EXPECTED).ok).toBe(
      true,
    );
  });

  it("accepts an empty issue list but never a missing one", () => {
    expect(
      parsePaperReviewErrorObject(
        errorInner({ issues: [] }),
        ACCEPTED_ERROR_EXPECTED,
      ).ok,
    ).toBe(true);

    const missing = errorInner() as unknown as Record<string, unknown>;
    delete missing.issues;
    expect(
      parsePaperReviewErrorObject(missing, ACCEPTED_ERROR_EXPECTED).ok,
    ).toBe(false);
  });

  /*
   * [237] fed these exact bodies from the registered FastAPI test client into
   * this parser. Each one is a real producer response, not a mock preference.
   */
  it("accepts the backend-assigned snapshot of a first accepted failure", () => {
    const assigned = errorInner({
      code: "artifact_build_failed",
      request_id: REQUEST_ID,
      snapshot_id: SNAPSHOT_ID,
      progress: { completed_parts: 3, total_parts: 10, current_part: null },
      issues: [],
    });
    const parsed = parsePaperReviewErrorObject(assigned, {
      mode: "accepted_assigned",
      request_id: REQUEST_ID,
    });
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      // The browser could not have known this id before the response.
      expect(parsed.value.snapshot_id).toBe(SNAPSHOT_ID);
      expect(parsed.value.progress?.completed_parts).toBe(3);
    }
  });

  it("refuses an assigned mode without a snapshot or without progress", () => {
    expect(
      parsePaperReviewErrorObject(
        errorInner({
          code: "artifact_build_failed",
          snapshot_id: null,
          progress: { completed_parts: 3, total_parts: 10, current_part: null },
          issues: [],
        }),
        { mode: "accepted_assigned", request_id: REQUEST_ID },
      ).ok,
    ).toBe(false);
    expect(
      parsePaperReviewErrorObject(
        errorInner({
          code: "artifact_build_failed",
          snapshot_id: SNAPSHOT_ID,
          progress: null,
          issues: [],
        }),
        { mode: "accepted_assigned", request_id: REQUEST_ID },
      ).ok,
    ).toBe(false);
    // A drifted request id is refused in every mode.
    expect(
      parsePaperReviewErrorObject(
        errorInner({
          code: "artifact_build_failed",
          request_id: "00000000-0000-4000-8000-000000000000",
          snapshot_id: SNAPSHOT_ID,
          progress: { completed_parts: 3, total_parts: 10, current_part: null },
          issues: [],
        }),
        { mode: "accepted_assigned", request_id: REQUEST_ID },
      ).ok,
    ).toBe(false);
  });

  it("keeps the three modes mutually exclusive on one body", () => {
    const conflict = errorInner({
      code: "request_id_conflict",
      request_id: REQUEST_ID,
      snapshot_id: SNAPSHOT_ID,
      progress: { completed_parts: 0, total_parts: 10, current_part: null },
      issues: [],
    });
    // Real conflict body: assigned accepts it, pre-acceptance refuses it.
    expect(
      parsePaperReviewErrorObject(conflict, {
        mode: "accepted_assigned",
        request_id: REQUEST_ID,
      }).ok,
    ).toBe(true);
    expect(
      parsePaperReviewErrorObject(conflict, {
        mode: "pre_acceptance",
        request_id: REQUEST_ID,
      }).ok,
    ).toBe(false);
    // `accepted_known` still demands the exact snapshot the caller already has.
    expect(
      parsePaperReviewErrorObject(conflict, {
        mode: "accepted_known",
        request_id: REQUEST_ID,
        snapshot_id: "paper-review-00000000000000000000000000000000",
      }).ok,
    ).toBe(false);
  });

  it("reads a download or opener refusal as this exact request and snapshot", () => {
    const refusal = errorInner({
      code: "snapshot_not_ready",
      retryable: true,
      request_id: REQUEST_ID,
      snapshot_id: SNAPSHOT_ID,
      progress: { completed_parts: 0, total_parts: 10, current_part: null },
      issues: [],
    });
    const parsed = parsePaperReviewErrorEnvelope(
      { detail: refusal },
      {
        mode: "accepted_known",
        request_id: REQUEST_ID,
        snapshot_id: SNAPSHOT_ID,
      },
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.detail.code).toBe("snapshot_not_ready");
      expect(parsed.value.detail.retryable).toBe(true);
    }
    // Another snapshot's refusal is never accepted for this one.
    expect(
      parsePaperReviewErrorEnvelope(
        { detail: refusal },
        {
          mode: "accepted_known",
          request_id: REQUEST_ID,
          snapshot_id: "paper-review-00000000000000000000000000000000",
        },
      ).ok,
    ).toBe(false);
  });

  it("separates pre-acceptance from accepted identity rules", () => {
    const preAcceptance = errorInner({
      code: "trader_not_found",
      snapshot_id: null,
      progress: null,
      issues: [],
    });
    const expectation: PaperReviewErrorExpectation = {
      mode: "pre_acceptance",
      request_id: REQUEST_ID,
    };
    expect(parsePaperReviewErrorObject(preAcceptance, expectation).ok).toBe(
      true,
    );

    // The same body must fail once the request has been accepted.
    expect(
      parsePaperReviewErrorObject(preAcceptance, ACCEPTED_ERROR_EXPECTED).ok,
    ).toBe(false);

    // An accepted failure may not drop its progress.
    expect(
      parsePaperReviewErrorObject(
        errorInner({ progress: null }),
        ACCEPTED_ERROR_EXPECTED,
      ).ok,
    ).toBe(false);

    // A request with no id at all stays null on both sides.
    const anonymous = errorInner({
      code: "request_not_found",
      request_id: null,
      snapshot_id: null,
      progress: null,
      issues: [],
    });
    expect(
      parsePaperReviewErrorObject(anonymous, {
        mode: "pre_acceptance",
        request_id: null,
      }).ok,
    ).toBe(true);
  });

  it("requires a null current_part on a terminal error", () => {
    const body = errorInner({
      progress: {
        completed_parts: 7,
        total_parts: 10,
        current_part: "paper/events.json",
      },
    });
    expect(
      parsePaperReviewErrorObject(body, ACCEPTED_ERROR_EXPECTED, reviewPaths())
        .ok,
    ).toBe(false);
  });

  it("never mistakes a FastAPI 422 body for this schema", () => {
    expect(
      parsePaperReviewErrorEnvelope(
        { detail: [{ loc: ["body", "request_id"], msg: "field required" }] },
        ACCEPTED_ERROR_EXPECTED,
      ).ok,
    ).toBe(false);
  });
});

// --- §9.4 terminal opener ---------------------------------------------------

describe("paper_review_terminal_opener.v1 strict parser", () => {
  it("accepts the canonical claim and keeps the exact text", () => {
    const parsed = parsePaperReviewTerminalOpenerClaim(openerBody(), {
      snapshot_id: SNAPSHOT_ID,
    });
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.text.endsWith("\n")).toBe(true);
      expect(parsed.value.bytes).toBe(1234);
    }
  });

  it("rejects empty text, so no caller can substitute a blank opener", () => {
    const body = clone(openerBody());
    body.text = "";
    expect(
      parsePaperReviewTerminalOpenerClaim(body, { snapshot_id: SNAPSHOT_ID }).ok,
    ).toBe(false);
  });

  it("rejects a cross-snapshot identity, extra keys and bad byte counts", () => {
    expect(
      parsePaperReviewTerminalOpenerClaim(openerBody(), {
        snapshot_id: "paper-review-00000000000000000000000000000000",
      }).ok,
    ).toBe(false);

    const extra = { ...openerBody(), display_filename: "x.zip" };
    expect(
      parsePaperReviewTerminalOpenerClaim(extra, { snapshot_id: SNAPSHOT_ID }).ok,
    ).toBe(false);

    const zero = clone(openerBody());
    zero.bytes = 0;
    expect(
      parsePaperReviewTerminalOpenerClaim(zero, { snapshot_id: SNAPSHOT_ID }).ok,
    ).toBe(false);
  });
});

// --- Correction A: safe artifact identities ---------------------------------

describe("Correction A — safe run and trader identities", () => {
  const UNSAFE_RUN_IDS = [
    "",
    " run",
    "run ",
    ".hidden",
    "../escape",
    "a/b",
    "a\\b",
    "C:drive",
    "a#b",
    "a?b",
    `a${String.fromCharCode(0)}b`,
    "a\nb",
    "-leading",
    "_leading",
  ];

  const SAFE_RUN_IDS = [RUN_ID, "a", "A_1.x-y"];

  it("accepts only the backend safe run pattern in both path helpers", () => {
    for (const runId of SAFE_RUN_IDS) {
      expect(paperReviewMemberPaths(runId)).toHaveLength(10);
      expect(paperLedgerMemberPaths(runId)).toHaveLength(4);
    }
    for (const runId of UNSAFE_RUN_IDS) {
      // null, never a sanitised path, an empty array or a throw.
      expect(paperReviewMemberPaths(runId)).toBeNull();
      expect(paperLedgerMemberPaths(runId)).toBeNull();
    }
  });

  it("refuses a traversal ledger run id even when every derived path agrees", () => {
    const body = clone(ledgerBody());
    body.baseline.run_id = UNSAFE_RUN_ID;
    body.baseline.members[1].path = `baseline/trades/${UNSAFE_RUN_ID}.json`;
    body.baseline.members[2].path = `baseline/equity/${UNSAFE_RUN_ID}.json`;
    body.baseline.members[3].path = `baseline/events/${UNSAFE_RUN_ID}.json`;
    for (const ref of body.interpretation.supporting_evidence_refs) {
      ref.path = `baseline/events/${UNSAFE_RUN_ID}.json`;
    }
    // Internally consistent, and still refused.
    const parsed = parsePaperLedgerOrigin(body, LEDGER_EXPECTED);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) {
      expect(parsed.error).toContain("baseline.run_id");
    }
  });

  it("refuses every unsafe ledger run id shape", () => {
    for (const runId of UNSAFE_RUN_IDS) {
      const body = clone(ledgerBody());
      body.baseline.run_id = runId;
      body.baseline.members[1].path = `baseline/trades/${runId}.json`;
      body.baseline.members[2].path = `baseline/equity/${runId}.json`;
      body.baseline.members[3].path = `baseline/events/${runId}.json`;
      for (const ref of body.interpretation.supporting_evidence_refs) {
        ref.path = `baseline/events/${runId}.json`;
      }
      expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(false);
    }
  });

  it("keeps accepting the target run and A_1.x-y in the ledger body", () => {
    for (const runId of SAFE_RUN_IDS) {
      const body = clone(ledgerBody());
      body.baseline.run_id = runId;
      body.baseline.members[1].path = `baseline/trades/${runId}.json`;
      body.baseline.members[2].path = `baseline/equity/${runId}.json`;
      body.baseline.members[3].path = `baseline/events/${runId}.json`;
      for (const ref of body.interpretation.supporting_evidence_refs) {
        ref.path = `baseline/events/${runId}.json`;
      }
      expect(parsePaperLedgerOrigin(body, LEDGER_EXPECTED).ok).toBe(true);
    }
  });

  it("refuses an unsafe ready baseline expectation with matching members", () => {
    const body = clone(readyBody());
    body.members[7].path = `baseline/trades/${UNSAFE_RUN_ID}.json`;
    body.members[8].path = `baseline/equity/${UNSAFE_RUN_ID}.json`;
    body.members[9].path = `baseline/events/${UNSAFE_RUN_ID}.json`;
    expect(
      parsePaperReviewReady(body, {
        ...READY_EXPECTED,
        baseline_run_id: UNSAFE_RUN_ID,
      }).ok,
    ).toBe(false);
  });

  it("refuses an unsafe ready trader expectation with a matching filename", () => {
    const body = clone(readyBody());
    body.display_filename = `paper-review-${UNSAFE_TRADER_ID}-${COMPACT_AT}.zip`;
    expect(
      parsePaperReviewReady(body, {
        ...READY_EXPECTED,
        trader_id: UNSAFE_TRADER_ID,
      }).ok,
    ).toBe(false);
  });

  it("refuses an unsafe preparing status expectation with a matching current part", () => {
    const body = preparingBody();
    body.progress.current_part = `baseline/events/${UNSAFE_RUN_ID}.json`;
    expect(
      parsePaperReviewStatus(body, {
        ...STATUS_EXPECTED,
        baseline_run_id: UNSAFE_RUN_ID,
      }).ok,
    ).toBe(false);
  });

  it("refuses an unsafe status expectation on the ready branch too", () => {
    const body = readyStatusBody();
    expect(
      parsePaperReviewStatus(body, {
        ...STATUS_EXPECTED,
        baseline_run_id: UNSAFE_RUN_ID,
      }).ok,
    ).toBe(false);
  });

  it("fails closed with ok=false and never throws out of a public parser", () => {
    expect(() =>
      parsePaperLedgerOrigin({ baseline: { run_id: UNSAFE_RUN_ID } }, {
        trader_id: UNSAFE_TRADER_ID,
      }),
    ).not.toThrow();
    expect(() =>
      parsePaperReviewReady(readyBody(), {
        ...READY_EXPECTED,
        trader_id: UNSAFE_TRADER_ID,
        baseline_run_id: UNSAFE_RUN_ID,
      }),
    ).not.toThrow();
    expect(() =>
      parsePaperReviewStatus(preparingBody(), {
        ...STATUS_EXPECTED,
        baseline_run_id: UNSAFE_RUN_ID,
      }),
    ).not.toThrow();
  });
});
