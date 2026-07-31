/* eslint-disable @typescript-eslint/no-explicit-any */

import { describe, expect, it } from "vitest";

import {
  buildP4StandardRequest,
  exactDuplicateAcknowledgements,
  isMonotonicP4BatchUpdate,
  parseP4Batch,
  parseP4BatchList,
  parseP4Precheck,
  parseP4StandardRequest,
  requestToAssumptionSnapshot,
} from "./liveContract";
import { cloneAssumptions } from "./assumptions";
import { parseCompleteRerunSnapshot } from "./liveRerun";
import { makeBatch, makePrecheck, makeStandardRequest } from "../../test/p4Fixtures";

function copy<T>(value: T): T {
  return structuredClone(value);
}

describe("P4 exact standard request", () => {
  it("serializes only the D1 envelope and keeps full capital per cell", () => {
    const assumptions = cloneAssumptions();
    const request = buildP4StandardRequest({
      strategyVersions: ["strategy-0001", "strategy-0002"],
      symbols: ["NQ", "YM"],
      rangeStartUtc: "2026-05-01T00:00:00Z",
      rangeEndUtc: "2026-07-22T21:00:00Z",
      assumptions,
    });
    expect(request).not.toBeNull();
    expect(Object.keys(request!)).toEqual([
      "strategy_versions",
      "symbols",
      "range_start",
      "range_end",
      "execution_assumptions",
      "duplicate_acknowledgements",
    ]);
    expect(request!.execution_assumptions.initial_capital_usd).toBe(100_000);
    expect(
      request!.execution_assumptions.commission_per_side_by_symbol,
    ).toEqual({ NQ: 2.5, YM: 2.5 });
    expect(request!.execution_assumptions.slippage_ticks).toEqual({
      breakout_entry: 1,
      stop_exit: 2,
      target_exit: 0,
      day_end_exit: 1,
    });
    expect(JSON.stringify(request)).not.toMatch(
      /validation_run|session_name|quantity|skip_nautilus_replay|initial_capital":/,
    );
  });

  it("rejects missing/extra/engineering fields, non-finite values and bad keys", () => {
    const good = makeStandardRequest();
    expect(parseP4StandardRequest(good)).not.toBeNull();

    const extra = { ...copy(good), validation_run: false };
    expect(parseP4StandardRequest(extra)).toBeNull();

    const oldCapital = { ...copy(good), initial_capital: 100_000 };
    expect(parseP4StandardRequest(oldCapital)).toBeNull();

    const missing = copy(good) as unknown as Record<string, unknown>;
    delete missing.duplicate_acknowledgements;
    expect(parseP4StandardRequest(missing)).toBeNull();

    const infinite = copy(good);
    infinite.execution_assumptions.commission_per_side_by_symbol.NQ =
      Number.POSITIVE_INFINITY;
    expect(parseP4StandardRequest(infinite)).toBeNull();

    const badSlip = copy(good);
    (
      badSlip.execution_assumptions.slippage_ticks as unknown as Record<
        string,
        number
      >
    ).stop = 2;
    expect(parseP4StandardRequest(badSlip)).toBeNull();

    const extraCommission = copy(good);
    extraCommission.execution_assumptions.commission_per_side_by_symbol.YM =
      2.5;
    expect(parseP4StandardRequest(extraCommission)).toBeNull();
  });
});

describe("backtest_precheck.v1 exact parser", () => {
  it("accepts exact identity and produces exact duplicate acknowledgements", () => {
    const base = makeStandardRequest();
    const response = makePrecheck(base, { duplicate: "exact" });
    const parsed = parseP4Precheck(response, base);
    expect(parsed).not.toBeNull();
    expect(exactDuplicateAcknowledgements(parsed!)).toEqual([
      {
        strategy_version: "strategy-0001",
        symbol: "NQ",
        range_start: base.range_start,
        range_end: base.range_end,
      },
    ]);
  });

  it("accepts exact unavailable-strategy truth with an unknown session", () => {
    const request = makeStandardRequest();
    const body: any = copy(makePrecheck(request));
    const unit = body.units[0];
    unit.session_name = null;
    unit.status = "unknown";
    unit.reason_codes = [
      "strategy_unavailable",
      "coverage_unknown",
      "warmup_unknown",
    ];
    unit.coverage = {
      status: "unknown",
      requested_trading_date_count: 0,
      admitted_trading_date_count: 0,
      complete_trading_dates: [],
      owner_trusted_problem_trading_dates: [],
      owner_excluded_trading_dates: [],
      roll_blackout_trading_dates: [],
      excluded_trading_dates: [],
      blocking_problem_trading_dates: [],
      missing_native_daily_trading_dates: [],
      reason_codes: ["coverage_unknown"],
    };
    unit.warmup = {
      status: "unknown",
      required_prior_trading_date_count: null,
      available_prior_trading_date_count: null,
      evaluable_trading_date_count: null,
      first_evaluable_trading_date: null,
      suggested_range_start: null,
      reason_codes: ["warmup_unknown"],
    };
    body.overall_status = "unknown";
    body.can_submit = false;

    expect(parseP4Precheck(body, request)?.units[0].session_name).toBeNull();
  });

  it("rejects a warm-up suggestion that would move the start earlier", () => {
    const request = makeStandardRequest();
    const body: any = copy(makePrecheck(request));
    const unit = body.units[0];
    unit.status = "warn";
    unit.reason_codes = ["warmup_short"];
    unit.warmup = {
      status: "warn",
      required_prior_trading_date_count: 95,
      available_prior_trading_date_count: 94,
      evaluable_trading_date_count: 0,
      first_evaluable_trading_date: null,
      suggested_range_start: "2026-04-01T00:00:00Z",
      reason_codes: ["warmup_short"],
    };
    body.overall_status = "warn";
    body.can_submit = true;

    expect(parseP4Precheck(body, request)).toBeNull();
  });

  it.each([
    ["missing top key", (body: any) => delete body.checked_at],
    ["extra top key", (body: any) => (body.extra = true)],
    ["unknown enum", (body: any) => (body.units[0].status = "maybe")],
    [
      "unknown reason",
      (body: any) => body.units[0].coverage.reason_codes.push("coverage_new"),
    ],
    [
      "bad nested count",
      (body: any) =>
        (body.units[0].coverage.admitted_trading_date_count = 99),
    ],
    [
      "coverage status contradicts its reason",
      (body: any) => {
        body.units[0].coverage.reason_codes = ["coverage_unknown"];
      },
    ],
    [
      "bad identity",
      (body: any) => (body.units[0].strategy_version = "strategy-9999"),
    ],
    [
      "non-canonical time",
      (body: any) => (body.checked_at = "2026-07-27T08:00:00+00:00"),
    ],
  ])("fails closed on %s", (_name, mutate) => {
    const request = makeStandardRequest();
    const body: any = copy(makePrecheck(request));
    mutate(body);
    expect(parseP4Precheck(body, request)).toBeNull();
  });
});

describe("batch_job.v2 exact parser and conservation", () => {
  it.each([
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
  ] as const)("accepts exact %s truth", (status) => {
    const request = makeStandardRequest();
    const parsed = parseP4Batch(makeBatch(request, [status]));
    expect(parsed?.status).toBe(status);
    expect(parsed?.summary[status]).toBe(1);
  });

  it("accepts five-state mixed truth and exact list, including cancelled", () => {
    const request = makeStandardRequest({
      strategy_versions: [
        "strategy-0001",
        "strategy-0002",
        "strategy-0003",
        "strategy-0004",
        "strategy-0005",
      ],
    });
    const batch = makeBatch(
      request,
      ["queued", "running", "completed", "failed", "cancelled"],
    );
    expect(batch.status).toBe("running");
    const parsed = parseP4Batch(batch);
    expect(parsed?.summary).toEqual({
      total: 5,
      queued: 1,
      running: 1,
      completed: 1,
      failed: 1,
      cancelled: 1,
    });
    expect(
      parseP4BatchList({
        schema: "batch_job_list.v2",
        count: 1,
        batches: [batch],
      }),
    ).not.toBeNull();
  });

  it.each([
    [
      "summary misses cancelled",
      (batch: any) => {
        delete batch.summary.cancelled;
      },
      "queued",
    ],
    [
      "bad baseline progress",
      (batch: any) => {
        batch.jobs[0].progress.trade_count = 1;
      },
      "running",
    ],
    [
      "failed error_full missing",
      (batch: any) => {
        batch.jobs[0].error_full = null;
      },
      "failed",
    ],
    [
      "standard failed job omits both error fields",
      (batch: any) => {
        batch.jobs[0].error_summary = null;
        batch.jobs[0].error_full = null;
      },
      "failed",
    ],
    [
      "standard completed job omits final progress",
      (batch: any) => {
        batch.jobs[0].progress = null;
      },
      "completed",
    ],
    [
      "job extra field",
      (batch: any) => {
        batch.jobs[0].extra = true;
      },
      "queued",
    ],
    [
      "capital split",
      (batch: any) => {
        batch.jobs[0].execution_assumptions.initial_capital_usd = 50_000;
        batch.jobs[0].assumptions.initial_capital_usd = 50_000;
      },
      "queued",
    ],
  ])("fails closed when %s", (_name, mutate, requestedStatus) => {
    const request = makeStandardRequest();
    const batch: any = copy(
      makeBatch(request, [requestedStatus as any]),
    );
    mutate(batch);
    expect(parseP4Batch(batch)).toBeNull();
  });

  it("fails the whole list when any member is malformed", () => {
    const request = makeStandardRequest();
    const good = makeBatch(request, ["completed"], "good");
    const bad: any = copy(makeBatch(request, ["completed"], "bad"));
    bad.jobs[0].progress.total_trading_date_count = 0;
    expect(
      parseP4BatchList({
        schema: "batch_job_list.v2",
        count: 2,
        batches: [good, bad],
      }),
    ).toBeNull();
  });

  it("accepts a mixed list with normalized legacy completed and failed records", () => {
    const request = makeStandardRequest();
    const standard = makeBatch(request, ["completed"], "standard");
    const legacyCompleted: any = copy(
      makeBatch(request, ["completed"], "legacy-completed"),
    );
    legacyCompleted.request = { validation_run: false, symbols: ["NQ"] };
    delete legacyCompleted.jobs[0].execution_assumptions;
    legacyCompleted.jobs[0].assumptions = null;
    legacyCompleted.jobs[0].strategy_version = "trend-v0";
    legacyCompleted.jobs[0].progress = null;

    const legacyFailed: any = copy(
      makeBatch(request, ["failed"], "legacy-failed"),
    );
    legacyFailed.request = { validation_run: true, symbols: ["NQ"] };
    delete legacyFailed.jobs[0].execution_assumptions;
    legacyFailed.jobs[0].assumptions = null;
    legacyFailed.jobs[0].strategy_version = "trend-v0";
    legacyFailed.jobs[0].progress = null;
    legacyFailed.jobs[0].error_summary = null;
    legacyFailed.jobs[0].error_full = null;

    const parsed = parseP4BatchList({
      schema: "batch_job_list.v2",
      count: 3,
      batches: [standard, legacyCompleted, legacyFailed],
    });
    expect(parsed?.count).toBe(3);
    expect(parseP4StandardRequest(legacyCompleted.request)).toBeNull();
  });
});

describe("P4 operational update safety", () => {
  it("cancel may only turn queued cells into cancelled and never touches running", () => {
    const request = makeStandardRequest({
      strategy_versions: ["strategy-0001", "strategy-0002"],
    });
    const previous = makeBatch(request, ["running", "queued"]);
    const valid = makeBatch(request, ["running", "cancelled"]);
    valid.created_at = previous.created_at;
    valid.updated_at = "2026-07-27T08:00:02Z";
    valid.jobs[0] = copy(previous.jobs[0]);
    expect(isMonotonicP4BatchUpdate(previous, valid, "cancel")).toBe(true);

    const mutation = copy(valid);
    mutation.jobs[0].status = "cancelled";
    mutation.jobs[0].started_at = null;
    mutation.jobs[0].finished_at = mutation.updated_at;
    mutation.jobs[0].cancelled_at = mutation.updated_at;
    mutation.jobs[0].progress = null;
    mutation.status = "cancelled";
    mutation.summary = {
      total: 2,
      queued: 0,
      running: 0,
      completed: 0,
      failed: 0,
      cancelled: 2,
    };
    expect(isMonotonicP4BatchUpdate(previous, mutation, "cancel")).toBe(false);
  });

  it("poll never discards already-confirmed progress", () => {
    const request = makeStandardRequest();
    const previous = makeBatch(request, ["running"]);
    const next = copy(previous);
    next.updated_at = "2026-07-27T08:00:02Z";
    next.jobs[0].status = "failed";
    next.jobs[0].finished_at = next.updated_at;
    next.jobs[0].error_summary = "回測執行失敗";
    next.jobs[0].error_full = "RuntimeError: sanitized";
    next.jobs[0].progress = null;
    next.status = "failed";
    next.summary.running = 0;
    next.summary.failed = 1;

    expect(isMonotonicP4BatchUpdate(previous, next, "poll")).toBe(false);
  });

  it("strict history/rerun rejects the old validation flag heuristic and never fills gaps", () => {
    const exact = makeStandardRequest();
    expect(parseCompleteRerunSnapshot(exact)).toEqual({
      strategyVersions: ["strategy-0001"],
      symbols: ["NQ"],
      rangeStartUtc: exact.range_start,
      rangeEndUtc: exact.range_end,
      assumptions: requestToAssumptionSnapshot(exact),
    });
    expect(
      parseCompleteRerunSnapshot({
        validation_run: false,
        strategy_versions: ["strategy-0001"],
        symbols: ["NQ"],
        range_start: exact.range_start,
        range_end: exact.range_end,
      }),
    ).toBeNull();
  });
});
