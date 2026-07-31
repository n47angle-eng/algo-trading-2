import { describe, expect, it } from "vitest";

import {
  parsePaperRuntimeCapabilities,
  parsePaperTraderV2,
} from "./runtimeContract";

const selection = {
  strategy_id: "strategy-0003",
  content_sha256:
    "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97",
  contract_id: "NQ-202609-CME",
  baseline_run_id: "nq-20260728-standard-365adf",
  baseline_result_sha256:
    "5edc3cf2e5d314ddc2594f57cfbc52987e35b855d804c147f86d7e6e3cf486f7",
  timeframes: {
    market_input: "1m",
    execution: "1m",
    chart_display: "30m",
  },
};

describe("paper runtime strict contracts", () => {
  it("parses capabilities while preserving role-aware ordered values", () => {
    const result = parsePaperRuntimeCapabilities({
      schema: "paper_runtime_capabilities.v1",
      timeframes: {
        market_input: { enabled: ["1m"] },
        execution: { enabled: ["1m"] },
        chart_display: { enabled: ["1m", "30m"] },
        strategy_profile: {
          source: "strategy.v1",
          client_override: false,
        },
      },
      market_modes: ["live", "test_delayed"],
      safety_defaults: {
        max_drawdown_r: 8,
        max_losing_streak: 8,
        blind_minutes: 5,
      },
      lifecycle_states: [
        "provisioned",
        "starting",
        "running",
        "pausing",
        "paused",
        "tripped",
        "stopping",
        "recovery_required",
        "permanently_stopped",
      ],
      as_of: "2026-07-31T01:02:03Z",
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.timeframes.chart_display.enabled).toEqual([
        "1m",
        "30m",
      ]);
    }
  });

  it("rejects an extra capabilities key", () => {
    const result = parsePaperRuntimeCapabilities({
      schema: "paper_runtime_capabilities.v1",
      timeframes: {
        market_input: { enabled: ["1m"] },
        execution: { enabled: ["1m"] },
        chart_display: { enabled: ["1m", "30m"] },
        strategy_profile: {
          source: "strategy.v1",
          client_override: false,
        },
      },
      market_modes: ["live", "test_delayed"],
      safety_defaults: {
        max_drawdown_r: 8,
        max_losing_streak: 8,
        blind_minutes: 5,
      },
      lifecycle_states: [
        "provisioned",
        "starting",
        "running",
        "pausing",
        "paused",
        "tripped",
        "stopping",
        "recovery_required",
        "permanently_stopped",
      ],
      as_of: "2026-07-31T01:02:03Z",
      extra: true,
    });

    expect(result.ok).toBe(false);
  });

  it("parses a v2 trader and never lets display timeframe replace 5m entry", () => {
    const result = parsePaperTraderV2({
      schema: "paper_trader.v2",
      trader_id: `trader-${"a".repeat(32)}`,
      request_id: "4322a78f-7603-4778-bf34-a1f6369d772c",
      selection,
      selection_fingerprint: "b".repeat(64),
      strategy_timeframe_profile: {
        bias: "D",
        mid: "1H",
        entry: "5m",
        source: "strategy.v1",
        client_override: false,
      },
      lifecycle: "provisioned",
      lifecycle_version: 1,
      lifecycle_reason: "trader created; runtime has not started",
      account_id: `paper-account-${"c".repeat(32)}`,
      ledger_origin_id: `paper-ledger-${"d".repeat(32)}`,
      safety: {
        max_drawdown_r: 8,
        max_losing_streak: 8,
        blind_minutes: 5,
      },
      created_at: "2026-07-31T01:02:03Z",
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.strategy_timeframe_profile.entry).toBe("5m");
      expect(result.value.selection.timeframes.chart_display).toBe("30m");
    }
  });
});
