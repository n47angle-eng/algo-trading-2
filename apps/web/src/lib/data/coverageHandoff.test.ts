import { describe, expect, it } from "vitest";

import {
  buildBacktestHandoff,
  coverageStatusMessage,
  handoffToSearchParams,
  parseHandoffSearchParams,
  parseTradingDayCoverage,
  tradingDayToLocalRangeEnd,
  tradingDayToLocalRangeStart,
} from "./coverageHandoff";

const KNOWN_COVERAGE = {
  schema: "trading_day_coverage.v1",
  status: "known",
  session_name: "eth",
  first_trading_date: "2026-05-01",
  last_trading_date: "2026-05-20",
  trading_date_count: 14,
  complete_trading_date_count: 10,
  problem_trading_date_count: 3,
  pending_problem_trading_date_count: 2,
  owner_trusted_problem_trading_date_count: 1,
  owner_excluded_trading_date_count: 1,
  complete_trading_dates: [
    "2026-05-01",
    "2026-05-02",
    "2026-05-05",
    "2026-05-06",
    "2026-05-07",
    "2026-05-08",
    "2026-05-09",
    "2026-05-12",
    "2026-05-13",
    "2026-05-14",
  ],
  problem_trading_dates: ["2026-05-15", "2026-05-16", "2026-05-19"],
  pending_problem_trading_dates: ["2026-05-15", "2026-05-16"],
  owner_trusted_problem_trading_dates: ["2026-05-19"],
  owner_excluded_trading_dates: ["2026-05-20"],
  longest_complete_segment: {
    start_trading_date: "2026-05-05",
    end_trading_date: "2026-05-09",
    trading_date_count: 5,
  },
};

describe("parseTradingDayCoverage", () => {
  it("parses known API-shaped coverage without inventing dates", () => {
    const parsed = parseTradingDayCoverage(KNOWN_COVERAGE);
    expect(parsed.status).toBe("known");
    expect(parsed.complete_trading_dates).toEqual(
      KNOWN_COVERAGE.complete_trading_dates,
    );
    expect(parsed.problem_trading_dates).toEqual(
      KNOWN_COVERAGE.problem_trading_dates,
    );
    expect(parsed.longest_complete_segment).toEqual(
      KNOWN_COVERAGE.longest_complete_segment,
    );
  });

  it("fails closed on missing nested coverage — no fake complete days", () => {
    const parsed = parseTradingDayCoverage(undefined);
    expect(parsed.status).toBe("unknown");
    expect(parsed.complete_trading_dates).toBeNull();
    expect(parsed.complete_trading_date_count).toBeNull();
  });

  it("fails closed when status is error", () => {
    const parsed = parseTradingDayCoverage({
      schema: "trading_day_coverage.v1",
      status: "error",
      error_code: "minute_data_unreadable",
    });
    expect(parsed.status).toBe("error");
    expect(parsed.complete_trading_dates).toBeNull();
  });

  it("fails closed when known status has non-array complete dates", () => {
    const parsed = parseTradingDayCoverage({
      ...KNOWN_COVERAGE,
      complete_trading_dates: "all-clean",
    });
    expect(parsed.status).toBe("unknown");
    expect(parsed.complete_trading_dates).toBeNull();
  });
});

describe("buildBacktestHandoff", () => {
  it("prefers longest complete segment from real coverage", () => {
    const coverage = parseTradingDayCoverage(KNOWN_COVERAGE);
    const handoff = buildBacktestHandoff("nq", coverage);
    expect(handoff).toEqual({
      symbol: "NQ",
      rangeStartDate: "2026-05-05",
      rangeEndDate: "2026-05-09",
      source: "longest_complete_segment",
    });
  });

  it("returns null when coverage is unknown — never fabricates range", () => {
    const coverage = parseTradingDayCoverage({ status: "unknown" });
    expect(buildBacktestHandoff("NQ", coverage)).toBeNull();
  });

  it("returns null when known but zero complete days", () => {
    const coverage = parseTradingDayCoverage({
      ...KNOWN_COVERAGE,
      complete_trading_dates: [],
      complete_trading_date_count: 0,
      longest_complete_segment: null,
    });
    expect(buildBacktestHandoff("NQ", coverage)).toBeNull();
  });

  it("falls back to complete bounds when no longest segment", () => {
    const coverage = parseTradingDayCoverage({
      ...KNOWN_COVERAGE,
      longest_complete_segment: null,
    });
    const handoff = buildBacktestHandoff("YM", coverage);
    expect(handoff).toEqual({
      symbol: "YM",
      rangeStartDate: "2026-05-01",
      rangeEndDate: "2026-05-14",
      source: "complete_bounds",
    });
  });
});

describe("handoff query params", () => {
  it("round-trips symbol and trading-day labels without timezone shift", () => {
    const handoff = {
      symbol: "NQ",
      rangeStartDate: "2026-05-05",
      rangeEndDate: "2026-05-09",
      source: "longest_complete_segment" as const,
    };
    const qs = handoffToSearchParams(handoff);
    const parsed = parseHandoffSearchParams(new URLSearchParams(qs));
    expect(parsed).toEqual({
      symbol: "NQ",
      rangeStartDate: "2026-05-05",
      rangeEndDate: "2026-05-09",
      source: "complete_bounds",
    });
    // Labels must survive as exact calendar dates.
    expect(parsed?.rangeStartDate).toBe("2026-05-05");
    expect(parsed?.rangeEndDate).toBe("2026-05-09");
  });

  it("rejects partial or inverted params", () => {
    expect(
      parseHandoffSearchParams(
        new URLSearchParams("symbol=NQ&startDate=2026-05-09&endDate=2026-05-05"),
      ),
    ).toBeNull();
    expect(
      parseHandoffSearchParams(new URLSearchParams("symbol=NQ&startDate=2026-05-05")),
    ).toBeNull();
  });
});

describe("trading day local picker mapping", () => {
  it("stamps labels without converting the calendar day", () => {
    expect(tradingDayToLocalRangeStart("2026-05-05")).toBe("2026-05-05T00:00:00");
    expect(tradingDayToLocalRangeEnd("2026-05-09")).toBe("2026-05-09T23:59:59");
  });
});

describe("coverageStatusMessage fail-closed copy", () => {
  it("never claims clean days on loading/error/unknown", () => {
    expect(coverageStatusMessage(null, true, null)).toMatch(/載入中/);
    expect(coverageStatusMessage(null, false, "boom")).toMatch(/核實唔到/);
    const unknown = parseTradingDayCoverage(undefined);
    expect(coverageStatusMessage(unknown, false, null)).toMatch(/核實唔到/);
  });
});
