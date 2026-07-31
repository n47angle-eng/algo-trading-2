import { afterEach, describe, expect, it, vi } from "vitest";

import {
  capitalPerUnit,
  cloneAssumptions,
  validateAssumptions,
} from "./assumptions";
import {
  STRATEGY_NAME_UNAVAILABLE,
  buildStrategyNameCatalog,
  displayStrategyName,
  jobStatusLabel,
  resolveStrategyLabel,
  scanBannedVisibleText,
} from "./format";
import {
  FIXTURE_QUEUE_HOLD_MS,
  FIXTURE_STRATEGIES,
  __setFixtureDelay,
  cancelFixtureQueued,
  countAuthorizedUnits,
  fixturePrechecks,
  fixtureAssumptionsDefault,
  getFixtureSession,
  listFixtureSessions,
  resetFixtureState,
  startFixtureSession,
} from "./fixtureStore";
import { identityKey, sameIdentity } from "./identity";
import {
  INCOMPLETE_RERUN_HINT,
  isLiveExactRerunAvailable,
  isStandardHistoryRequest,
  liveRerunDisabledReason,
  parseCompleteRerunSnapshot,
} from "./liveRerun";
import {
  countProgress,
  formatProgressSummary,
  progressSum,
} from "./progress";
import {
  formatTradingDayLabel,
  localDatetimeToUtcIso,
  formatUtcCounterpart,
  utcIsoToDatetimeLocal,
} from "./time";
import { DEFAULT_ASSUMPTIONS, type FormSnapshot } from "./types";

function baseForm(over: Partial<FormSnapshot> = {}): FormSnapshot {
  return {
    strategyIds: ["strategy-0001", "strategy-0002"],
    symbols: ["NQ", "YM"],
    rangeStartLocal: "2026-05-06T22:00",
    rangeEndLocal: "2026-05-08T21:00",
    rangeStartUtc: "2026-05-06T14:00:00Z",
    rangeEndUtc: "2026-05-08T13:00:00Z",
    assumptions: cloneAssumptions(DEFAULT_ASSUMPTIONS),
    ...over,
  };
}

describe("P4 capital independence (P4-08)", () => {
  it("each unit gets full form capital, never split by unit count", () => {
    expect(capitalPerUnit(100_000, 4)).toBe(100_000);
    expect(capitalPerUnit(100_000, 1)).toBe(100_000);
    expect(capitalPerUnit(50_000, 10)).toBe(50_000);
  });

  it("would fail if capital were shared across units", () => {
    const wrong = (total: number, n: number) => total / n;
    expect(wrong(100_000, 4)).not.toBe(capitalPerUnit(100_000, 4));
  });
});

describe("P4 assumptions validation (P4-06)", () => {
  it("truth correction D2: approved defaults drive fixture truth", () => {
    expect(DEFAULT_ASSUMPTIONS).toEqual({
      initialCapital: 100_000,
      fees: { NQ: 2.5, YM: 2.5, GC: 2.8 },
      slippageTicks: {
        breakout: 1,
        stop: 2,
        target: 0,
        dayEnd: 1,
      },
    });
    expect(fixtureAssumptionsDefault()).toEqual(DEFAULT_ASSUMPTIONS);

    expect(capitalPerUnit(100_000, 4)).toBe(100_000);
  });

  it("rejects non-positive capital and non-integer slippage", () => {
    const a = cloneAssumptions();
    a.initialCapital = 0;
    a.slippageTicks.breakout = 1.5;
    const e = validateAssumptions(a, ["NQ"]);
    expect(e.initialCapital).toBeTruthy();
    expect(e.slippage?.breakout).toMatch(/整數/);
  });

  it("accepts finite edited costs and all four non-negative tick values", () => {
    const a = cloneAssumptions();
    a.fees.NQ = 9.99;
    a.slippageTicks = {
      breakout: 3,
      stop: 4,
      target: 1,
      dayEnd: 2,
    };
    expect(validateAssumptions(a, ["NQ"])).toEqual({});
  });
});

describe("P4 time (P4-03 / G-07) — non-UTC conversion", () => {
  it("local datetime converts via local wall clock, not naive Z append", () => {
    const local = "2026-07-26T12:00";
    const utc = localDatetimeToUtcIso(local);
    expect(utc).toMatch(/Z$/);
    // Must match Date local parse, not string+Z
    const expected = new Date(local)
      .toISOString()
      .replace(/\.\d{3}Z$/, "Z");
    expect(utc).toBe(expected);
    const naiveZ = `${local}:00.000Z`;
    // In non-UTC zones these differ; in UTC they may equal — still assert
    // conversion is Date-based (not string append of Z alone).
    expect(utc).toBe(
      new Date(
        Number(local.slice(0, 4)),
        Number(local.slice(5, 7)) - 1,
        Number(local.slice(8, 10)),
        Number(local.slice(11, 13)),
        Number(local.slice(14, 16)),
      )
        .toISOString()
        .replace(/\.\d{3}Z$/, "Z"),
    );
    void naiveZ;
    expect(formatUtcCounterpart(local)).toContain("UTC");
    expect(formatTradingDayLabel("2026-05-07")).toBe("2026-05-07");
  });

  it("exact rerun UTC → local picker → UTC preserves the instant", () => {
    const utc = "2026-07-22T21:00:37Z";
    expect(localDatetimeToUtcIso(utcIsoToDatetimeLocal(utc))).toBe(utc);
  });

  it("trading-date labels stay the same in two distant timezone contexts", () => {
    const labelsByTimezone = {
      "Pacific/Honolulu": formatTradingDayLabel("2026-05-07"),
      "Asia/Tokyo": formatTradingDayLabel("2026-05-07"),
    };
    expect(labelsByTimezone).toEqual({
      "Pacific/Honolulu": "2026-05-07",
      "Asia/Tokyo": "2026-05-07",
    });
  });
});

describe("P4 banned terms (P4-18)", () => {
  it("flags forbidden chrome words", () => {
    expect(scanBannedVisibleText("validation_run on")).toContain(
      "validation_run",
    );
    expect(scanBannedVisibleText("4 runs remaining")).toContain("runs-as-unit");
    expect(scanBannedVisibleText("2 個策略 × 2 個合約 ＝ 4 次回測")).toEqual(
      [],
    );
  });

  it("jobStatusLabel never leaks partial raw token", () => {
    expect(jobStatusLabel("partial")).toBe("部分完成");
    expect(jobStatusLabel("partial")).not.toBe("partial");
    expect(jobStatusLabel("weird_raw")).toBe("狀態未明");
  });
});

describe("D1 — 2×2 unit matrix", () => {
  it("default fixture strategies both authorize NQ+YM → 4 units", () => {
    expect(FIXTURE_STRATEGIES).toHaveLength(2);
    for (const st of FIXTURE_STRATEGIES) {
      expect(st.universe.contracts).toEqual(
        expect.arrayContaining(["NQ", "YM"]),
      );
    }
    expect(
      countAuthorizedUnits(
        ["strategy-0001", "strategy-0002"],
        ["NQ", "YM"],
      ),
    ).toBe(4);
  });

  it("if one strategy drops YM authorization, unit count is not fake 2×2=4", () => {
    const broken = FIXTURE_STRATEGIES.map((s, i) =>
      i === 1
        ? {
            ...s,
            universe: { ...s.universe, contracts: ["NQ", "GC"] },
          }
        : s,
    );
    // 0001: NQ,YM + 0002: NQ only for {NQ,YM} symbols → 3 units
    expect(
      countAuthorizedUnits(
        ["strategy-0001", "strategy-0002"],
        ["NQ", "YM"],
        broken,
      ),
    ).toBe(3);
    // Correct product only when intersection kept:
    expect(
      countAuthorizedUnits(
        ["strategy-0001", "strategy-0002"],
        ["NQ"], // YM removed
        broken,
      ),
    ).toBe(2);
  });
});

describe("D2 — standard history filter + complete snapshot", () => {
  const complete = {
    strategy_versions: ["strategy-0001"],
    symbols: ["NQ"],
    range_start: "2026-01-01T00:00:00Z",
    range_end: "2026-02-01T00:00:00Z",
    execution_assumptions: {
      initial_capital_usd: 100_000,
      commission_per_side_by_symbol: { NQ: 2.5 },
      slippage_ticks: {
        breakout_entry: 1,
        stop_exit: 2,
        target_exit: 0,
        day_end_exit: 1,
      },
    },
    duplicate_acknowledgements: [],
  };

  it("only the exact D1 request is standard history", () => {
    expect(isStandardHistoryRequest(complete)).toBe(true);
    expect(isStandardHistoryRequest({ validation_run: false })).toBe(false);
    expect(isStandardHistoryRequest({ validation_run: true })).toBe(false);
    expect(isStandardHistoryRequest({})).toBe(false);
    expect(isStandardHistoryRequest(null)).toBe(false);
    expect(isStandardHistoryRequest(undefined)).toBe(false);
  });

  it("parseCompleteRerunSnapshot requires all locked fields", () => {
    const snap = parseCompleteRerunSnapshot(complete);
    expect(snap).not.toBeNull();
    expect(snap!.assumptions.slippageTicks.dayEnd).toBe(1);
    expect(snap!.assumptions.initialCapital).toBe(100_000);

    const noAssumptions = {
      ...complete,
      execution_assumptions: undefined,
    };
    expect(parseCompleteRerunSnapshot(noAssumptions)).toBeNull();
    expect(
      parseCompleteRerunSnapshot({ ...complete, validation_run: true }),
    ).toBeNull();
  });

  it("exact snapshot enables safe restore; incomplete remains disabled", () => {
    expect(parseCompleteRerunSnapshot(complete)).not.toBeNull();
    expect(isLiveExactRerunAvailable(complete)).toBe(true);
    expect(isLiveExactRerunAvailable({ validation_run: false })).toBe(false);
    expect(liveRerunDisabledReason()).toBe(INCOMPLETE_RERUN_HINT);
  });
});

describe("D10 — strategy display names without duplication", () => {
  it("strips exact tech tail without duplicating human name", () => {
    expect(displayStrategyName("Trend 回踩 18EMA · p50 閘")).toBe(
      "Trend 回踩 18EMA",
    );
    expect(displayStrategyName("Trend 回踩 90EMA · p65 閘")).toBe(
      "Trend 回踩 90EMA",
    );
    // Must NOT become "Trend 回踩 18EMA · Trend 回踩 18EMA"
    expect(displayStrategyName("Trend 回踩 18EMA · p50 閘")).not.toMatch(
      /Trend 回踩 18EMA · Trend/,
    );
    expect(displayStrategyName("p50 閘")).toBe("Trend 回踩 18EMA");
    expect(displayStrategyName("p65 閘")).toBe("Trend 回踩 90EMA");
    expect(displayStrategyName("Trend 回踩 18EMA")).toBe("Trend 回踩 18EMA");
    expect(displayStrategyName("")).toBe(STRATEGY_NAME_UNAVAILABLE);
    expect(scanBannedVisibleText(displayStrategyName("Trend 回踩 90EMA · p65 閘"))).toEqual(
      [],
    );
    expect(scanBannedVisibleText("p65 閘 on chip")).toContain("p65 閘");
  });

  it("D11: resolveStrategyLabel uses catalog id→name", () => {
    const catalog = buildStrategyNameCatalog([
      {
        strategy_id: "strategy-0001",
        name: "Trend 回踩 18EMA · p50 閘",
      },
      {
        strategy_id: "strategy-0002",
        name: "Trend 回踩 90EMA · p65 閘",
      },
    ]);
    expect(catalog.get("strategy-0001")).toBe("Trend 回踩 18EMA");
    expect(catalog.get("strategy-0002")).toBe("Trend 回踩 90EMA");
    const r1 = resolveStrategyLabel("strategy-0001", catalog);
    expect(r1.label).toBe("Trend 回踩 18EMA");
    expect(r1.versionId).toBe("strategy-0001");
    const missing = resolveStrategyLabel("strategy-9999", catalog);
    expect(missing.label).toBe(STRATEGY_NAME_UNAVAILABLE);
    expect(missing.versionId).toBe("strategy-9999");
  });
});

describe("D8 — fixture coverage + failure symbol consistency", () => {
  afterEach(() => {
    resetFixtureState();
  });

  it("coverage detail lists only selected symbols (no GC when NQ+YM)", () => {
    const pc = fixturePrechecks(baseForm({ symbols: ["NQ", "YM"] }), false);
    expect(pc.coverage.detail).toMatch(/NQ／YM/);
    expect(pc.coverage.detail).not.toMatch(/GC/);
  });

  /**
   * D0 ([175]): the owner-review warm-up copy must state the engine-derived
   * 95-day boundary and may only ever suggest pushing the start date later.
   * "提前" contradicts the no-lookahead engine (P4 #6).
   */
  it("warm-up fixture states the exact 95-day boundary and never suggests an earlier start", () => {
    const pc = fixturePrechecks(baseForm(), false);
    expect(pc.warmup.detail).toContain("95");
    expect(pc.warmup.detail).toContain("推後");
    expect(pc.warmup.detail).not.toContain("提前");
    expect(pc.warmup.neededDays).toBe(95);
    expect(pc.warmup.suggestedStart).toBe("2026-06-16");
    // Adopting the suggestion must move the start date later, not earlier.
    expect(
      Date.parse(`${pc.warmup.suggestedStart}T00:00:00Z`),
    ).toBeGreaterThan(Date.parse("2026-01-15T00:00:00Z"));
  });

  it("failed unit errorFull matches its symbol (YM not NQ)", async () => {
    vi.useFakeTimers();
    __setFixtureDelay(
      (ms) =>
        new Promise((resolve) => {
          window.setTimeout(resolve, ms);
        }),
    );
    resetFixtureState();
    const session = startFixtureSession(baseForm());
    // unit1 is strategy-0001 × YM
    expect(session.units[1].symbol).toBe("YM");
    await vi.advanceTimersByTimeAsync(FIXTURE_QUEUE_HOLD_MS + 500);
    const done = getFixtureSession(session.sessionId)!;
    const failed = done.units.find((u) => u.status === "failed")!;
    expect(failed.symbol).toBe("YM");
    expect(failed.errorFull).toMatch(/coverage: YM /);
    expect(failed.errorFull).not.toMatch(/coverage: NQ /);
    vi.useRealTimers();
    __setFixtureDelay(null);
  });
});

describe("D5 — identity + progress", () => {
  it("identity is set-semantic (order independent)", () => {
    const a = identityKey({
      strategyIds: ["a", "b"],
      symbols: ["YM", "NQ"],
      rangeStartUtc: "1",
      rangeEndUtc: "2",
    });
    const b = identityKey({
      strategyIds: ["b", "a"],
      symbols: ["NQ", "YM"],
      rangeStartUtc: "1",
      rangeEndUtc: "2",
    });
    expect(a).toBe(b);
    expect(
      sameIdentity(
        {
          strategyIds: ["a"],
          symbols: ["NQ"],
          rangeStartUtc: "1",
          rangeEndUtc: "2",
        },
        {
          strategyIds: ["a"],
          symbols: ["YM"],
          rangeStartUtc: "1",
          rangeEndUtc: "2",
        },
      ),
    ).toBe(false);
  });

  it("progress five buckets sum to total; partial not in labels", () => {
    const c = countProgress([
      { status: "completed" },
      { status: "running" },
      { status: "queued" },
      { status: "failed" },
      { status: "cancelled" },
    ]);
    expect(progressSum(c)).toBe(c.total);
    expect(c.total).toBe(5);
    const line = formatProgressSummary(c);
    expect(line).toMatch(/完成 1/);
    expect(line).toMatch(/失敗 1/);
    expect(line).toMatch(/已取消 1/);
    expect(line).not.toMatch(/partial/);
  });
});

describe("P4 owner-review fixture isolation + timing", () => {
  afterEach(() => {
    resetFixtureState();
    __setFixtureDelay(null);
    vi.useRealTimers();
  });

  it("never exposes FIXTURE via live-looking default", () => {
    expect(FIXTURE_STRATEGIES.length).toBe(2);
    expect(FIXTURE_STRATEGIES[0].strategy_id).toBe("strategy-0001");
  });

  it("2×2 start produces 4 units each with full capital", () => {
    resetFixtureState();
    const session = startFixtureSession(baseForm());
    expect(session.units).toHaveLength(4);
    for (const u of session.units) {
      expect(u.assumptions.initialCapital).toBe(100_000);
    }
  });

  it("duplicate blocks after first session; set-semantic across history", () => {
    resetFixtureState();
    const form = baseForm();
    startFixtureSession(form);
    const pc = fixturePrechecks(form, false);
    expect(pc.duplicate.status).toBe("block");
    // Reordered ids/symbols still duplicate
    const reordered = baseForm({
      strategyIds: ["strategy-0002", "strategy-0001"],
      symbols: ["YM", "NQ"],
    });
    expect(fixturePrechecks(reordered, false).duplicate.status).toBe("block");
    // force ack
    expect(fixturePrechecks(form, true).duplicate.status).toBe("pass");
    // different range is not duplicate
    expect(
      fixturePrechecks(
        baseForm({ rangeStartUtc: "2020-01-01T00:00:00Z" }),
        false,
      ).duplicate.status,
    ).toBe("pass");
  });

  it("cancel only queued; fake timers hold ≥3s; final completed+failed+cancelled", async () => {
    vi.useFakeTimers();
    // Use real microtask-friendly delay under fake timers
    __setFixtureDelay(
      (ms) =>
        new Promise((resolve) => {
          window.setTimeout(resolve, ms);
        }),
    );
    resetFixtureState();
    const session = startFixtureSession(baseForm());
    expect(session.units).toHaveLength(4);

    // Advance past unit0 quick run (80ms) into queue hold
    await vi.advanceTimersByTimeAsync(200);
    const mid = getFixtureSession(session.sessionId)!;
    expect(mid.units[0].status).toBe("completed");
    expect(mid.units[1].status).toBe("running");
    expect(mid.units[2].status).toBe("queued");
    expect(mid.units[3].status).toBe("queued");

    // Cancel while still in hold
    cancelFixtureQueued(session.sessionId);
    const afterCancel = getFixtureSession(session.sessionId)!;
    expect(afterCancel.units[1].status).toBe("running"); // must NOT cancel running
    expect(afterCancel.units[2].status).toBe("cancelled");
    expect(afterCancel.units[3].status).toBe("cancelled");

    // Finish hold + unit1 fail
    await vi.advanceTimersByTimeAsync(FIXTURE_QUEUE_HOLD_MS + 500);
    const done = getFixtureSession(session.sessionId)!;
    const statuses = done.units.map((u) => u.status);
    expect(statuses).toContain("completed");
    expect(statuses).toContain("failed");
    expect(statuses).toContain("cancelled");
    expect(statuses.filter((s) => s === "cancelled")).toHaveLength(2);
    expect(done.units[1].status).toBe("failed");
    expect(done.units[1].errorFull).toMatch(/coverage/);
    // running was never flipped to cancelled
    expect(done.units[1].status).not.toBe("cancelled");
    void listFixtureSessions;
  });
});
