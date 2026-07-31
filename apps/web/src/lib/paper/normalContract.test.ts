import { describe, expect, it } from "vitest";

import {
  buildPaperCreateRequest,
  buildPaperProvisioningRequest,
  buildPaperReadinessRequest,
  derivePaperOverall,
  isCanonicalUuid4,
  isPaperGenerationCurrent,
  paperErrorCopy,
  paperTraderEquals,
  parsePaperApiError,
  parsePaperBaselineList,
  parsePaperContractList,
  parsePaperCreatedTrader,
  parsePaperEligibleStrategyList,
  parsePaperProvisioningReadiness,
  parsePaperReadiness,
  parsePaperTraderDetail,
  parsePaperTraderList,
  parsePaperTraderRequestStatus,
} from "./normalContract";
import type {
  PaperContractSelection,
  PaperSelection,
  PaperStrategySelection,
} from "./types";
import type { PaperHttpResult } from "../../api/client";

/*
 * Stage A engineering target — allowed in tests only. Product code must never
 * hard-code these identities or the capital amount ([205] §6.2).
 */
const STRATEGY_ID = "strategy-0003";
const CONTENT_SHA =
  "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97";
const CONTRACT_ID = "NQ-202609-CME";
const RUN_ID = "nq-20260728-standard-365adf";
const RESULT_SHA =
  "5edc3cf2e5d314ddc2594f57cfbc52987e35b855d804c147f86d7e6e3cf486f7";
const REQUEST_ID = "00000000-0000-4000-8000-000000000000";
const TRADER_ID = "trader-1f2e3d4c5b6a798877665544332211ff";
const ACCOUNT_ID = "paper-account-aabbccddeeff00112233445566778899";
const AT = "2026-07-29T00:00:00Z";

const STRATEGY_SELECTION: PaperStrategySelection = {
  strategy_id: STRATEGY_ID,
  content_sha256: CONTENT_SHA,
};

const CONTRACT_SELECTION: PaperContractSelection = {
  ...STRATEGY_SELECTION,
  contract_id: CONTRACT_ID,
};

const SELECTION: PaperSelection = {
  ...CONTRACT_SELECTION,
  baseline_run_id: RUN_ID,
  baseline_result_sha256: RESULT_SHA,
};

function res(
  body: unknown,
  status = 200,
  method: "GET" | "POST" = "GET",
): PaperHttpResult {
  return {
    path: "/api/v1/paper/test",
    method,
    status,
    ok: status >= 200 && status < 300,
    rawText: "",
    jsonParsed: true,
    body,
  };
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

// --- canonical fixtures -----------------------------------------------------

function eligibleBody() {
  return {
    schema: "eligible_strategy_list.v1",
    count: 1,
    strategies: [
      {
        strategy_id: STRATEGY_ID,
        content_sha256: CONTENT_SHA,
        supporting_decisions: [
          {
            decision_id: "promotion-5a0212a27b2749cb97951945eea87b68",
            run_id: RUN_ID,
          },
        ],
      },
    ],
  };
}

function contractsBody() {
  return {
    schema: "paper_contract_list.v1",
    selection: { strategy_id: STRATEGY_ID, content_sha256: CONTENT_SHA },
    count: 1,
    contracts: [
      {
        contract_id: CONTRACT_ID,
        symbol: "NQ",
        display_name: "E-mini Nasdaq-100",
      },
    ],
  };
}

function baselinesBody() {
  return {
    schema: "paper_baseline_list.v1",
    selection: {
      strategy_id: STRATEGY_ID,
      content_sha256: CONTENT_SHA,
      contract_id: CONTRACT_ID,
    },
    count: 1,
    baselines: [
      {
        run_id: RUN_ID,
        result_sha256: RESULT_SHA,
        range_start: "2026-07-22T22:00:00Z",
        range_end: "2026-07-23T21:00:00Z",
        currency: "USD",
        initial_capital: 100000.0,
        trade_count: 0,
        net_r: 0,
        validation_run: false,
        integrity: "verified",
      },
    ],
  };
}

function checks(overrides: Record<string, unknown>[] = []) {
  const base = [
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
  return base.map((row, index) => ({ ...row, ...(overrides[index] ?? {}) }));
}

function readinessBody() {
  return {
    schema: "paper_readiness.v1",
    selection: { ...SELECTION },
    overall: "ready",
    market_session: "closed",
    checked_at: AT,
    checks: checks(),
  };
}

function traderBody() {
  return {
    schema: "paper_trader.v1",
    trader_id: TRADER_ID,
    request_id: REQUEST_ID,
    strategy: { strategy_id: STRATEGY_ID, content_sha256: CONTENT_SHA },
    contract_id: CONTRACT_ID,
    baseline: {
      run_id: RUN_ID,
      result_sha256: RESULT_SHA,
      range_start: "2026-07-22T22:00:00Z",
      range_end: "2026-07-23T21:00:00Z",
    },
    account: {
      account_id: ACCOUNT_ID,
      currency: "USD",
      initial_capital: 100000.0,
    },
    safeguards: {
      max_drawdown_r: 8,
      max_losing_streak: 8,
      blind_minutes: 5,
    },
    lifecycle: {
      status: "provisioned",
      reason: "simulation runtime is not activated in Stage A",
      as_of: AT,
    },
    readiness_snapshot: {
      schema: "paper_readiness_snapshot.v1",
      overall: "ready",
      market_session: "closed",
      checked_at: AT,
      checks: checks(),
    },
    created_at: AT,
  };
}

const CREATE_REQUEST = buildPaperCreateRequest(REQUEST_ID, SELECTION);

function apiError(code: string, status: number) {
  return res(
    {
      detail: {
        schema: "paper_api_error.v1",
        code,
        message: "人話原因",
        retryable: false,
      },
    },
    status,
  );
}

// --- eligible strategies ----------------------------------------------------

describe("eligible_strategy_list.v1 strict parser", () => {
  it("accepts the canonical response", () => {
    const parsed = parsePaperEligibleStrategyList(res(eligibleBody()));
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.count).toBe(1);
      expect(parsed.value.strategies[0].strategy_id).toBe(STRATEGY_ID);
    }
  });

  it("rejects an extra top-level key", () => {
    const body = { ...eligibleBody(), note: "extra" };
    expect(parsePaperEligibleStrategyList(res(body)).ok).toBe(false);
  });

  it("rejects a missing top-level key", () => {
    const body = clone(eligibleBody()) as Record<string, unknown>;
    delete body.count;
    expect(parsePaperEligibleStrategyList(res(body)).ok).toBe(false);
  });

  it("rejects a null required value", () => {
    const body = clone(eligibleBody()) as Record<string, unknown>;
    body.strategies = null;
    expect(parsePaperEligibleStrategyList(res(body)).ok).toBe(false);
  });

  it("rejects the wrong schema", () => {
    const body = { ...eligibleBody(), schema: "eligible_strategy_list.v2" };
    expect(parsePaperEligibleStrategyList(res(body)).ok).toBe(false);
  });

  it("rejects the `items` collection alias", () => {
    const canonical = eligibleBody();
    const body = {
      schema: canonical.schema,
      count: canonical.count,
      items: canonical.strategies,
    };
    expect(parsePaperEligibleStrategyList(res(body)).ok).toBe(false);
  });

  it("rejects a count that drifts from the array length", () => {
    const body = { ...eligibleBody(), count: 2 };
    expect(parsePaperEligibleStrategyList(res(body)).ok).toBe(false);
  });

  it("rejects a nested extra key", () => {
    const body = clone(eligibleBody());
    (body.strategies[0] as unknown as Record<string, unknown>).contract_id =
      CONTRACT_ID;
    expect(parsePaperEligibleStrategyList(res(body)).ok).toBe(false);
  });

  it("rejects a malformed content hash", () => {
    const body = clone(eligibleBody());
    body.strategies[0].content_sha256 = CONTENT_SHA.toUpperCase();
    expect(parsePaperEligibleStrategyList(res(body)).ok).toBe(false);
  });

  it("rejects a strategy with no supporting immutable decision", () => {
    const body = clone(eligibleBody());
    body.strategies[0].supporting_decisions = [];
    expect(parsePaperEligibleStrategyList(res(body)).ok).toBe(false);
  });

  it("rejects a non-200 status and a non-JSON body", () => {
    expect(parsePaperEligibleStrategyList(res(eligibleBody(), 503)).ok).toBe(
      false,
    );
    expect(
      parsePaperEligibleStrategyList({
        ...res(null),
        jsonParsed: false,
        rawText: "<html>",
      }).ok,
    ).toBe(false);
  });
});

// --- contract candidates ----------------------------------------------------

describe("paper_contract_list.v1 strict parser", () => {
  it("accepts exactly one contract", () => {
    const parsed = parsePaperContractList(
      res(contractsBody()),
      STRATEGY_SELECTION,
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.contracts).toHaveLength(1);
      expect(parsed.value.contracts[0].display_name).toBe("E-mini Nasdaq-100");
    }
  });

  it("accepts several expiry contracts that share one root symbol", () => {
    const body = clone(contractsBody());
    body.contracts = [
      { contract_id: "NQ-202609-CME", symbol: "NQ", display_name: "E-mini Nasdaq-100" },
      { contract_id: "NQ-202612-CME", symbol: "NQ", display_name: "E-mini Nasdaq-100" },
    ];
    body.count = 2;
    const parsed = parsePaperContractList(res(body), STRATEGY_SELECTION);
    expect(parsed.ok).toBe(true);
  });

  it("accepts an eligible strategy with zero available contracts", () => {
    const body = clone(contractsBody());
    body.contracts = [];
    body.count = 0;
    const parsed = parsePaperContractList(res(body), STRATEGY_SELECTION);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.count).toBe(0);
    }
  });

  it("rejects a duplicate contract id", () => {
    const body = clone(contractsBody());
    body.contracts = [body.contracts[0], clone(body.contracts[0])];
    body.count = 2;
    expect(parsePaperContractList(res(body), STRATEGY_SELECTION).ok).toBe(false);
  });

  it("rejects contract order drift", () => {
    const body = clone(contractsBody());
    body.contracts = [
      { contract_id: "NQ-202612-CME", symbol: "NQ", display_name: "E-mini Nasdaq-100" },
      { contract_id: "NQ-202609-CME", symbol: "NQ", display_name: "E-mini Nasdaq-100" },
    ];
    body.count = 2;
    expect(parsePaperContractList(res(body), STRATEGY_SELECTION).ok).toBe(false);
  });

  it("rejects count drift", () => {
    const body = { ...contractsBody(), count: 3 };
    expect(parsePaperContractList(res(body), STRATEGY_SELECTION).ok).toBe(false);
  });

  it("rejects a selection echo for another strategy identity", () => {
    const body = clone(contractsBody());
    body.selection.strategy_id = "strategy-0002";
    expect(parsePaperContractList(res(body), STRATEGY_SELECTION).ok).toBe(false);
  });

  it("rejects a selection echo whose hash drifts", () => {
    const body = clone(contractsBody());
    body.selection.content_sha256 = "a".repeat(64);
    expect(parsePaperContractList(res(body), STRATEGY_SELECTION).ok).toBe(false);
  });

  it("rejects extra or missing row keys and padded strings", () => {
    const extra = clone(contractsBody());
    (extra.contracts[0] as unknown as Record<string, unknown>).currency = "USD";
    expect(parsePaperContractList(res(extra), STRATEGY_SELECTION).ok).toBe(
      false,
    );

    const missing = clone(contractsBody()) as unknown as {
      contracts: Record<string, unknown>[];
    };
    delete missing.contracts[0].display_name;
    expect(
      parsePaperContractList(
        res(missing as unknown),
        STRATEGY_SELECTION,
      ).ok,
    ).toBe(false);

    const padded = clone(contractsBody());
    padded.contracts[0].contract_id = ` ${CONTRACT_ID} `;
    expect(parsePaperContractList(res(padded), STRATEGY_SELECTION).ok).toBe(
      false,
    );
  });

  it("fails closed on 422, 409, 503 and unknown bodies", () => {
    for (const status of [422, 409, 503, 500]) {
      expect(
        parsePaperContractList(
          res(contractsBody(), status),
          STRATEGY_SELECTION,
        ).ok,
      ).toBe(false);
    }
    expect(
      parsePaperContractList(res({ unexpected: true }), STRATEGY_SELECTION).ok,
    ).toBe(false);
  });
});

// --- baselines --------------------------------------------------------------

describe("paper_baseline_list.v1 strict parser", () => {
  it("accepts the canonical zero-trade candidate", () => {
    const parsed = parsePaperBaselineList(
      res(baselinesBody()),
      CONTRACT_SELECTION,
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.baselines[0].trade_count).toBe(0);
      expect(parsed.value.baselines[0].initial_capital).toBe(100000);
    }
  });

  it("rejects a string amount and a boolean amount (no coercion)", () => {
    const stringAmount = clone(baselinesBody()) as unknown as {
      baselines: Record<string, unknown>[];
    };
    stringAmount.baselines[0].initial_capital = "100000";
    expect(
      parsePaperBaselineList(res(stringAmount as unknown), CONTRACT_SELECTION)
        .ok,
    ).toBe(false);

    const boolAmount = clone(baselinesBody()) as unknown as {
      baselines: Record<string, unknown>[];
    };
    boolAmount.baselines[0].initial_capital = true;
    expect(
      parsePaperBaselineList(res(boolAmount as unknown), CONTRACT_SELECTION).ok,
    ).toBe(false);
  });

  it("rejects NaN and Infinity", () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY]) {
      const body = clone(baselinesBody()) as unknown as {
        baselines: Record<string, unknown>[];
      };
      body.baselines[0].net_r = bad;
      expect(
        parsePaperBaselineList(res(body as unknown), CONTRACT_SELECTION).ok,
      ).toBe(false);
    }
  });

  it("rejects a non-positive initial capital", () => {
    const body = clone(baselinesBody());
    body.baselines[0].initial_capital = 0;
    expect(parsePaperBaselineList(res(body), CONTRACT_SELECTION).ok).toBe(false);
  });

  it("rejects an engineering validation run as a baseline", () => {
    const body = clone(baselinesBody());
    body.baselines[0].validation_run = true;
    expect(parsePaperBaselineList(res(body), CONTRACT_SELECTION).ok).toBe(false);
  });

  it("rejects an unknown integrity value instead of treating it as usable", () => {
    const body = clone(baselinesBody());
    body.baselines[0].integrity = "unverified";
    expect(parsePaperBaselineList(res(body), CONTRACT_SELECTION).ok).toBe(false);
  });

  it("rejects a malformed instant and a malformed result hash", () => {
    const badTime = clone(baselinesBody());
    badTime.baselines[0].range_end = "2026-07-23 21:00:00";
    expect(parsePaperBaselineList(res(badTime), CONTRACT_SELECTION).ok).toBe(
      false,
    );

    const badSha = clone(baselinesBody());
    badSha.baselines[0].result_sha256 = "abc";
    expect(parsePaperBaselineList(res(badSha), CONTRACT_SELECTION).ok).toBe(
      false,
    );
  });

  it("rejects duplicate runs, count drift and cross-identity echoes", () => {
    const duplicate = clone(baselinesBody());
    duplicate.baselines = [duplicate.baselines[0], clone(duplicate.baselines[0])];
    duplicate.count = 2;
    expect(parsePaperBaselineList(res(duplicate), CONTRACT_SELECTION).ok).toBe(
      false,
    );

    const drift = { ...baselinesBody(), count: 9 };
    expect(parsePaperBaselineList(res(drift), CONTRACT_SELECTION).ok).toBe(
      false,
    );

    const cross = clone(baselinesBody());
    cross.selection.contract_id = "YM-202609-CBOT";
    expect(parsePaperBaselineList(res(cross), CONTRACT_SELECTION).ok).toBe(
      false,
    );
  });
});

// --- readiness --------------------------------------------------------------

describe("paper_readiness.v1 strict parser", () => {
  it("accepts the canonical four checks in order", () => {
    const parsed = parsePaperReadiness(res(readinessBody(), 200, "POST"), SELECTION);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.checks.map((row) => row.key)).toEqual([
        "ib_realtime",
        "exchange_calendar",
        "telegram",
        "baseline_integrity",
      ]);
      expect(parsed.value.market_session).toBe("closed");
    }
  });

  it("rejects reordered checks", () => {
    const body = clone(readinessBody());
    const reordered = [body.checks[1], body.checks[0], body.checks[2], body.checks[3]];
    body.checks = reordered;
    expect(parsePaperReadiness(res(body), SELECTION).ok).toBe(false);
  });

  it("rejects a missing check, a fifth check and a duplicate check", () => {
    const missing = clone(readinessBody());
    missing.checks = missing.checks.slice(0, 3);
    expect(parsePaperReadiness(res(missing), SELECTION).ok).toBe(false);

    const fifth = clone(readinessBody());
    fifth.checks = [...fifth.checks, clone(fifth.checks[3])];
    expect(parsePaperReadiness(res(fifth), SELECTION).ok).toBe(false);

    const duplicate = clone(readinessBody());
    duplicate.checks = [
      duplicate.checks[0],
      duplicate.checks[0],
      duplicate.checks[2],
      duplicate.checks[3],
    ];
    expect(parsePaperReadiness(res(duplicate), SELECTION).ok).toBe(false);
  });

  it("rejects an unknown check status", () => {
    const body = clone(readinessBody());
    body.checks[0].status = "degraded";
    expect(parsePaperReadiness(res(body), SELECTION).ok).toBe(false);
  });

  it("rejects an unknown market session", () => {
    const body = clone(readinessBody());
    body.market_session = "half_day";
    expect(parsePaperReadiness(res(body), SELECTION).ok).toBe(false);
  });

  it("rejects overall=ready while a check is blocked or unknown", () => {
    for (const status of ["blocked", "unknown"]) {
      const body = clone(readinessBody());
      body.checks[0].status = status;
      expect(parsePaperReadiness(res(body), SELECTION).ok).toBe(false);
    }
  });

  it("accepts overall=blocked when one check is unknown", () => {
    const body = clone(readinessBody());
    body.checks[2].status = "unknown";
    body.overall = "blocked";
    const parsed = parsePaperReadiness(res(body), SELECTION);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(derivePaperOverall(parsed.value.checks)).toBe("blocked");
    }
  });

  it("rejects a selection echo that is not the mounted selection", () => {
    const body = clone(readinessBody());
    body.selection.baseline_run_id = "nq-20260101-standard-000000";
    expect(parsePaperReadiness(res(body), SELECTION).ok).toBe(false);
  });

  it("builds a request body carrying only schema and selection", () => {
    const request = buildPaperReadinessRequest(SELECTION);
    expect(Object.keys(request).sort()).toEqual(["schema", "selection"]);
    expect(Object.keys(request.selection).sort()).toEqual(
      [
        "baseline_result_sha256",
        "baseline_run_id",
        "content_sha256",
        "contract_id",
        "strategy_id",
      ].sort(),
    );
  });
});

// --- trader record ----------------------------------------------------------

describe("paper_trader.v1 strict parser", () => {
  it("accepts a 201 first creation and a 200 safe replay", () => {
    const first = parsePaperCreatedTrader(
      res(traderBody(), 201, "POST"),
      CREATE_REQUEST,
    );
    expect(first.ok).toBe(true);
    if (first.ok) {
      expect(first.value.replay).toBe(false);
      expect(first.value.trader.lifecycle.status).toBe("provisioned");
    }

    const replay = parsePaperCreatedTrader(
      res(traderBody(), 200, "POST"),
      CREATE_REQUEST,
    );
    expect(replay.ok).toBe(true);
    if (replay.ok) {
      expect(replay.value.replay).toBe(true);
    }
  });

  it("rejects any lifecycle status other than provisioned", () => {
    for (const status of ["running", "halted", "waiting"]) {
      const body = clone(traderBody());
      body.lifecycle.status = status;
      expect(
        parsePaperCreatedTrader(res(body, 201, "POST"), CREATE_REQUEST).ok,
      ).toBe(false);
    }
  });

  it("rejects a record that does not carry this request identity", () => {
    const body = clone(traderBody());
    body.request_id = "11111111-1111-4111-8111-111111111111";
    expect(
      parsePaperCreatedTrader(res(body, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);
  });

  it("rejects a record whose locked selection drifts", () => {
    const contractDrift = clone(traderBody());
    contractDrift.contract_id = "YM-202609-CBOT";
    expect(
      parsePaperCreatedTrader(res(contractDrift, 201, "POST"), CREATE_REQUEST)
        .ok,
    ).toBe(false);

    const baselineDrift = clone(traderBody());
    baselineDrift.baseline.result_sha256 = "b".repeat(64);
    expect(
      parsePaperCreatedTrader(res(baselineDrift, 201, "POST"), CREATE_REQUEST)
        .ok,
    ).toBe(false);
  });

  it("rejects malformed trader and account identities", () => {
    const badTrader = clone(traderBody());
    badTrader.trader_id = "trader-XYZ";
    expect(
      parsePaperCreatedTrader(res(badTrader, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);

    const badAccount = clone(traderBody());
    badAccount.account.account_id = "account-1";
    expect(
      parsePaperCreatedTrader(res(badAccount, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);
  });

  it("rejects an extra top-level key and a nested extra key", () => {
    const extraTop = { ...traderBody(), equity: 100000 };
    expect(
      parsePaperCreatedTrader(res(extraTop, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);

    const extraNested = clone(traderBody()) as unknown as {
      account: Record<string, unknown>;
    };
    extraNested.account.balance = 100000;
    expect(
      parsePaperCreatedTrader(
        res(extraNested as unknown, 201, "POST"),
        CREATE_REQUEST,
      ).ok,
    ).toBe(false);
  });

  it("rejects a readiness snapshot that carries a selection or fails its own checks", () => {
    const withSelection = clone(traderBody()) as unknown as {
      readiness_snapshot: Record<string, unknown>;
    };
    withSelection.readiness_snapshot.selection = { ...SELECTION };
    expect(
      parsePaperCreatedTrader(
        res(withSelection as unknown, 201, "POST"),
        CREATE_REQUEST,
      ).ok,
    ).toBe(false);

    /*
     * Option A: a provision-only trader legitimately carries a blocked
     * snapshot, so the pair must be accepted — but only when `overall` still
     * equals what the four checks derive.
     */
    const blocked = clone(traderBody());
    blocked.readiness_snapshot.checks[0].status = "blocked";
    blocked.readiness_snapshot.overall = "blocked";
    expect(
      parsePaperCreatedTrader(res(blocked, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(true);

    const overstated = clone(traderBody());
    overstated.readiness_snapshot.checks[0].status = "blocked";
    expect(
      parsePaperCreatedTrader(res(overstated, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);

    const understated = clone(traderBody());
    understated.readiness_snapshot.overall = "blocked";
    expect(
      parsePaperCreatedTrader(res(understated, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);
  });

  it("rejects a non-UTC created_at", () => {
    const body = clone(traderBody());
    body.created_at = "2026-07-29T00:00:00+08:00";
    expect(
      parsePaperCreatedTrader(res(body, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);
  });

  it("rejects a 202 or any other unapproved success status", () => {
    expect(
      parsePaperCreatedTrader(res(traderBody(), 202, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);
  });
});

describe("paper_trader_request_status.v1 strict parser", () => {
  function statusBody() {
    return {
      schema: "paper_trader_request_status.v1",
      request_id: REQUEST_ID,
      status: "completed",
      trader: traderBody(),
    };
  }

  it("accepts a completed lookup carrying the full record", () => {
    const parsed = parsePaperTraderRequestStatus(
      res(statusBody()),
      CREATE_REQUEST,
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.trader.trader_id).toBe(TRADER_ID);
    }
  });

  it("rejects a summary trader instead of the full record", () => {
    const body = statusBody() as unknown as Record<string, unknown>;
    body.trader = { trader_id: TRADER_ID };
    expect(parsePaperTraderRequestStatus(res(body), CREATE_REQUEST).ok).toBe(
      false,
    );
  });

  it("rejects an unknown status value", () => {
    const body = statusBody();
    body.status = "pending";
    expect(parsePaperTraderRequestStatus(res(body), CREATE_REQUEST).ok).toBe(
      false,
    );
  });

  it("rejects another request identity", () => {
    const body = statusBody();
    body.request_id = "22222222-2222-4222-8222-222222222222";
    expect(parsePaperTraderRequestStatus(res(body), CREATE_REQUEST).ok).toBe(
      false,
    );
  });
});

describe("paper_trader_list.v1 and detail strict parsers", () => {
  it("accepts an empty list and a populated list", () => {
    const empty = parsePaperTraderList(
      res({ schema: "paper_trader_list.v1", count: 0, traders: [] }),
    );
    expect(empty.ok).toBe(true);

    const populated = parsePaperTraderList(
      res({ schema: "paper_trader_list.v1", count: 1, traders: [traderBody()] }),
    );
    expect(populated.ok).toBe(true);
  });

  it("rejects list count drift and duplicate traders", () => {
    expect(
      parsePaperTraderList(
        res({ schema: "paper_trader_list.v1", count: 2, traders: [traderBody()] }),
      ).ok,
    ).toBe(false);
    expect(
      parsePaperTraderList(
        res({
          schema: "paper_trader_list.v1",
          count: 2,
          traders: [traderBody(), traderBody()],
        }),
      ).ok,
    ).toBe(false);
  });

  it("rejects a detail record for another trader", () => {
    expect(parsePaperTraderDetail(res(traderBody()), "trader-" + "0".repeat(32)).ok).toBe(
      false,
    );
    expect(parsePaperTraderDetail(res(traderBody()), TRADER_ID).ok).toBe(true);
  });

  it("proves list and detail records are byte-equal by identity comparison", () => {
    const list = parsePaperTraderList(
      res({ schema: "paper_trader_list.v1", count: 1, traders: [traderBody()] }),
    );
    const detail = parsePaperTraderDetail(res(traderBody()), TRADER_ID);
    expect(list.ok && detail.ok).toBe(true);
    if (list.ok && detail.ok) {
      expect(paperTraderEquals(list.value.traders[0], detail.value)).toBe(true);
      const drifted = { ...detail.value, contract_id: "YM-202609-CBOT" };
      expect(paperTraderEquals(list.value.traders[0], drifted)).toBe(false);
    }
  });
});

// --- error envelope ---------------------------------------------------------

describe("paper_api_error.v1 strict parser", () => {
  it("accepts every approved code on its approved status", () => {
    const pairs: [string, number][] = [
      ["eligible_strategy_required", 409],
      ["baseline_not_found", 404],
      ["selection_identity_mismatch", 409],
      ["baseline_integrity_failed", 503],
      ["readiness_blocked", 409],
      ["activation_not_authorized", 503],
      ["request_id_conflict", 409],
      ["request_not_found", 404],
      ["store_unavailable", 503],
    ];
    for (const [code, status] of pairs) {
      const parsed = parsePaperApiError(apiError(code, status));
      expect(parsed.ok).toBe(true);
      if (parsed.ok) {
        expect(paperErrorCopy(parsed.value.code).length).toBeGreaterThan(0);
      }
    }
  });

  it("rejects an unknown code", () => {
    expect(parsePaperApiError(apiError("teapot", 409)).ok).toBe(false);
  });

  it("rejects a known code served on the wrong status", () => {
    expect(parsePaperApiError(apiError("readiness_blocked", 503)).ok).toBe(
      false,
    );
  });

  it("rejects a FastAPI 422 shape and a plain-text body", () => {
    expect(
      parsePaperApiError(
        res({ detail: [{ loc: ["query"], msg: "field required" }] }, 422),
      ).ok,
    ).toBe(false);
    expect(
      parsePaperApiError({
        ...res(null, 500),
        jsonParsed: false,
        rawText: "internal error",
      }).ok,
    ).toBe(false);
  });

  it("never treats a successful response as an error", () => {
    expect(parsePaperApiError(res(traderBody(), 201)).ok).toBe(false);
  });
});

// --- small guards -----------------------------------------------------------

describe("identity and generation guards", () => {
  it("accepts only canonical lowercase UUID4", () => {
    const lettered = "3f2b1a4c-9d8e-4f6a-8b7c-1d2e3f4a5b6c";
    expect(isCanonicalUuid4(REQUEST_ID)).toBe(true);
    expect(isCanonicalUuid4(lettered)).toBe(true);
    expect(isCanonicalUuid4(lettered.toUpperCase())).toBe(false);
    expect(isCanonicalUuid4("00000000-0000-1000-8000-000000000000")).toBe(false);
    expect(isCanonicalUuid4("00000000-0000-4000-c000-000000000000")).toBe(false);
  });

  it("only treats an unchanged generation as current", () => {
    expect(isPaperGenerationCurrent(3, 3)).toBe(true);
    expect(isPaperGenerationCurrent(4, 3)).toBe(false);
  });

  it("builds a create body carrying only schema, request_id and selection", () => {
    expect(Object.keys(CREATE_REQUEST).sort()).toEqual([
      "request_id",
      "schema",
      "selection",
    ]);
  });
});

// --- Correction A: producer-exact strict gaps -------------------------------

describe("Correction A — producer-exact strict contract", () => {
  it("rejects a calendar-invalid timestamp such as 2026-02-30", () => {
    const body = clone(traderBody());
    body.created_at = "2026-02-30T00:00:00Z";
    expect(
      parsePaperCreatedTrader(res(body, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);
  });

  it("accepts only the canonical producer timestamp shapes", () => {
    const canonical = ["2026-07-29T00:00:00Z", "2026-07-29T00:00:00.123456Z"];
    for (const value of canonical) {
      const body = clone(traderBody());
      body.created_at = value;
      expect(
        parsePaperCreatedTrader(res(body, 201, "POST"), CREATE_REQUEST).ok,
      ).toBe(true);
    }
    const rejected = [
      "2026-07-29T00:00:00.1Z",
      "2026-07-29T00:00:00.12345Z",
      "2026-07-29T00:00:00.1234567Z",
      "2026-07-29T00:00:00.123456789Z",
      "2026-07-29T00:00:00z",
      "2026-07-29T00:00:00+00:00",
      "2026-07-29 00:00:00Z",
      "2026-13-01T00:00:00Z",
      "2026-07-29T24:00:00Z",
      "2026-07-29T00:60:00Z",
    ];
    for (const value of rejected) {
      const body = clone(traderBody());
      body.created_at = value;
      expect(
        parsePaperCreatedTrader(res(body, 201, "POST"), CREATE_REQUEST).ok,
      ).toBe(false);
    }
  });

  it("rejects a check timestamp that differs from the top-level checked_at", () => {
    const readiness = clone(readinessBody());
    readiness.checks[0].checked_at = "2026-07-29T00:00:01Z";
    expect(parsePaperReadiness(res(readiness), SELECTION).ok).toBe(false);

    const trader = clone(traderBody());
    trader.readiness_snapshot.checks[3].checked_at = "2026-07-29T00:00:01Z";
    expect(
      parsePaperCreatedTrader(res(trader, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);
  });

  it("rejects a Stage A safeguard drift on any of the three fields", () => {
    const drifts: [string, number][] = [
      ["max_drawdown_r", 9],
      ["max_losing_streak", 7],
      ["blind_minutes", 6],
    ];
    for (const [key, value] of drifts) {
      const body = clone(traderBody()) as unknown as {
        safeguards: Record<string, number>;
      };
      body.safeguards[key] = value;
      expect(
        parsePaperCreatedTrader(
          res(body as unknown, 201, "POST"),
          CREATE_REQUEST,
        ).ok,
      ).toBe(false);
    }
  });

  it("rejects a Stage A lifecycle reason drift", () => {
    const body = clone(traderBody());
    body.lifecycle.reason = "simulation runtime is active";
    expect(
      parsePaperCreatedTrader(res(body, 201, "POST"), CREATE_REQUEST).ok,
    ).toBe(false);
  });

  it("rejects retryable=true as an unknown v1 error state", () => {
    const body = {
      detail: {
        schema: "paper_api_error.v1",
        code: "readiness_blocked",
        message: "人話原因",
        retryable: true,
      },
    };
    expect(parsePaperApiError(res(body, 409)).ok).toBe(false);
  });

  it("applies the same record parser to lookup, list and detail", () => {
    const drifted = clone(traderBody());
    drifted.lifecycle.reason = "simulation runtime is active";

    expect(
      parsePaperTraderRequestStatus(
        res({
          schema: "paper_trader_request_status.v1",
          request_id: REQUEST_ID,
          status: "completed",
          trader: drifted,
        }),
        CREATE_REQUEST,
      ).ok,
    ).toBe(false);

    expect(
      parsePaperTraderList(
        res({ schema: "paper_trader_list.v1", count: 1, traders: [drifted] }),
      ).ok,
    ).toBe(false);

    expect(parsePaperTraderDetail(res(drifted), TRADER_ID).ok).toBe(false);
  });
});

// --- Correction B: producer-exact timestamp boundaries ----------------------

describe("Correction B — timestamp producer set boundaries", () => {
  function createdAt(value: string) {
    const body = clone(traderBody());
    body.created_at = value;
    return parsePaperCreatedTrader(res(body, 201, "POST"), CREATE_REQUEST).ok;
  }

  it("accepts canonical year 0001 and the rest of the 0001..0099 range", () => {
    // `Date.UTC(1, ...)` silently means 1901; the producer accepts year 1.
    expect(createdAt("0001-01-01T00:00:00Z")).toBe(true);
    expect(createdAt("0099-12-31T23:59:59Z")).toBe(true);
    expect(createdAt("0100-01-01T00:00:00Z")).toBe(true);
    expect(createdAt("9999-12-31T23:59:59Z")).toBe(true);
  });

  it("rejects year 0000, which no Python datetime can produce", () => {
    expect(createdAt("0000-01-01T00:00:00Z")).toBe(false);
  });

  it("rejects .000000Z because isoformat omits a zero fraction", () => {
    expect(createdAt("2026-07-29T00:00:00.000000Z")).toBe(false);
    expect(createdAt("0001-01-01T00:00:00.000000Z")).toBe(false);
  });

  it("keeps accepting a non-zero exact six-digit fraction", () => {
    expect(createdAt("2026-07-29T00:00:00.000001Z")).toBe(true);
    expect(createdAt("2026-07-29T00:00:00.100000Z")).toBe(true);
  });

  it("still rejects every previously closed shape", () => {
    for (const value of [
      "2026-02-30T00:00:00Z",
      "0001-02-30T00:00:00Z",
      "2026-07-29T00:00:00.1Z",
      "2026-07-29T00:00:00.12345Z",
      "2026-07-29T00:00:00.1234567Z",
      "2026-07-29T00:00:00z",
      "2026-07-29T00:00:00+00:00",
      "2026-13-01T00:00:00Z",
      "2026-07-29T24:00:00Z",
    ]) {
      expect(createdAt(value)).toBe(false);
    }
  });

  it("applies the same boundary to readiness timestamps", () => {
    const body = clone(readinessBody());
    body.checked_at = "0001-01-01T00:00:00Z";
    for (const row of body.checks) {
      row.checked_at = "0001-01-01T00:00:00Z";
    }
    expect(parsePaperReadiness(res(body), SELECTION).ok).toBe(true);

    const zeroFraction = clone(readinessBody());
    zeroFraction.checked_at = "2026-07-29T00:00:00.000000Z";
    for (const row of zeroFraction.checks) {
      row.checked_at = "2026-07-29T00:00:00.000000Z";
    }
    expect(parsePaperReadiness(res(zeroFraction), SELECTION).ok).toBe(false);
  });
});

/*
 * Option A §7: the provisioning preflight wire. Every body below is the exact
 * shape the approved producer emits; the parser eats a decoded body, never an
 * HTTP envelope.
 */
describe("provisioning authorization wire", () => {
  const OPERATION_ID = "paper-provision-0123456789abcdef0123456789abcdef";
  const AUTHORIZED_AT = "2026-07-30T09:00:00Z";
  const EXPIRES_AT = "2026-07-30T09:30:00Z";
  const REASON = "Owner-approved one-off P6 provision-only flow verification";

  function authorization(
    overrides: Record<string, unknown> = {},
  ): Record<string, unknown> {
    return {
      schema: "paper_provisioning_authorization.v1",
      state: "armed",
      operation_id: OPERATION_ID,
      authorized_at: AUTHORIZED_AT,
      expires_at: EXPIRES_AT,
      reason: REASON,
      ...overrides,
    };
  }

  function disabledAuthorization(
    overrides: Record<string, unknown> = {},
  ): Record<string, unknown> {
    return authorization({
      state: "disabled",
      operation_id: null,
      authorized_at: null,
      expires_at: null,
      ...overrides,
    });
  }

  function preflightBody(
    overrides: Record<string, unknown> = {},
  ): Record<string, unknown> {
    return {
      schema: "paper_provisioning_readiness.v1",
      selection: { ...SELECTION },
      can_provision: true,
      authorization: authorization(),
      runtime_readiness: readinessBody(),
      ...overrides,
    };
  }

  function blockedRuntime(): Record<string, unknown> {
    const runtime = readinessBody();
    runtime.overall = "blocked";
    runtime.checks = checks([
      { status: "unknown", reason: "IB API real-time session unverified" },
      { status: "unknown", reason: "calendar readiness unverified" },
      { status: "unknown", reason: "Telegram readiness unverified" },
    ]);
    return runtime;
  }

  it("builds a request with exactly the schema and the selection", () => {
    const request = buildPaperProvisioningRequest(SELECTION);
    expect(Object.keys(request).sort()).toEqual(["schema", "selection"]);
    expect(request.schema).toBe("paper_provisioning_readiness_request.v1");
    expect(request.selection).toEqual(SELECTION);
    expect(request.selection).not.toBe(SELECTION);
  });

  it("accepts an armed permit whose runtime truth is still blocked", () => {
    const parsed = parsePaperProvisioningReadiness(
      preflightBody({ runtime_readiness: blockedRuntime() }),
      SELECTION,
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.can_provision).toBe(true);
      expect(parsed.value.authorization.state).toBe("armed");
      expect(parsed.value.authorization.operation_id).toBe(OPERATION_ID);
      // Authorization never rewrites the external truth.
      expect(parsed.value.runtime_readiness.overall).toBe("blocked");
      expect(
        parsed.value.runtime_readiness.checks.map((row) => row.status),
      ).toEqual(["unknown", "unknown", "unknown", "ready"]);
    }
  });

  it("accepts the default unauthorized answer", () => {
    const parsed = parsePaperProvisioningReadiness(
      preflightBody({
        can_provision: false,
        authorization: disabledAuthorization(),
      }),
      SELECTION,
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.authorization.state).toBe("disabled");
      expect(parsed.value.authorization.operation_id).toBeNull();
      expect(parsed.value.authorization.authorized_at).toBeNull();
      expect(parsed.value.authorization.expires_at).toBeNull();
      expect(parsed.value.authorization.reason).toBe(REASON);
    }
  });

  it("accepts claimed, consumed and expired with a full identity", () => {
    for (const state of ["claimed", "consumed", "expired"]) {
      const parsed = parsePaperProvisioningReadiness(
        preflightBody({
          can_provision: false,
          authorization: authorization({ state }),
        }),
        SELECTION,
      );
      expect(parsed.ok, state).toBe(true);
      if (parsed.ok) {
        expect(parsed.value.authorization.state).toBe(state);
        expect(parsed.value.can_provision).toBe(false);
      }
    }
  });

  it("refuses a disabled state that leaks any permit field", () => {
    for (const leak of [
      { operation_id: OPERATION_ID },
      { authorized_at: AUTHORIZED_AT },
      { expires_at: EXPIRES_AT },
    ]) {
      const parsed = parsePaperProvisioningReadiness(
        preflightBody({
          can_provision: false,
          authorization: disabledAuthorization(leak),
        }),
        SELECTION,
      );
      expect(parsed.ok, JSON.stringify(leak)).toBe(false);
    }
  });

  it("refuses a live state that drops any permit field", () => {
    for (const missing of [
      { operation_id: null },
      { authorized_at: null },
      { expires_at: null },
    ]) {
      const parsed = parsePaperProvisioningReadiness(
        preflightBody({ authorization: authorization(missing) }),
        SELECTION,
      );
      expect(parsed.ok, JSON.stringify(missing)).toBe(false);
    }
  });

  it("requires the permit window to be exactly thirty minutes", () => {
    for (const expires of [
      "2026-07-30T09:29:00Z",
      "2026-07-30T09:31:00Z",
      "2026-07-30T10:30:00Z",
      "2026-07-30T08:30:00Z",
      "2026-07-30T09:30:00.000001Z",
    ]) {
      const parsed = parsePaperProvisioningReadiness(
        preflightBody({ authorization: authorization({ expires_at: expires }) }),
        SELECTION,
      );
      expect(parsed.ok, expires).toBe(false);
    }
    // The same six-digit fraction on both ends is still exactly thirty minutes.
    const parsed = parsePaperProvisioningReadiness(
      preflightBody({
        authorization: authorization({
          authorized_at: "2026-07-30T09:00:00.123456Z",
          expires_at: "2026-07-30T09:30:00.123456Z",
        }),
      }),
      SELECTION,
    );
    expect(parsed.ok).toBe(true);
  });

  it("refuses an unknown state, an unsafe operation id and a bad timestamp", () => {
    const cases: Array<[string, Record<string, unknown>]> = [
      ["unknown state", { state: "authorized" }],
      ["empty state", { state: "" }],
      ["uppercase operation", { operation_id: OPERATION_ID.toUpperCase() }],
      ["short operation", { operation_id: "paper-provision-0123" }],
      ["other prefix", { operation_id: "paper-review-0123456789abcdef0123456789abcdef" }],
      ["traversal operation", { operation_id: "paper-provision-../escape" }],
      ["offset timestamp", { authorized_at: "2026-07-30T09:00:00+00:00" }],
      ["millisecond fraction", { authorized_at: "2026-07-30T09:00:00.123Z" }],
      ["empty reason", { reason: "" }],
      ["null reason", { reason: null }],
    ];
    for (const [name, override] of cases) {
      const parsed = parsePaperProvisioningReadiness(
        preflightBody({ authorization: authorization(override) }),
        SELECTION,
      );
      expect(parsed.ok, name).toBe(false);
    }

    /*
     * With `can_provision: false` nothing downstream can reject an unknown
     * state, so only the closed enum stands between the body and the page.
     */
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({
          can_provision: false,
          authorization: authorization({ state: "authorized" }),
        }),
        SELECTION,
      ).ok,
    ).toBe(false);
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({
          can_provision: false,
          authorization: authorization({ state: "ARMED" }),
        }),
        SELECTION,
      ).ok,
    ).toBe(false);
  });

  it("refuses a missing, extra or wrongly typed top-level key", () => {
    const missing = preflightBody();
    delete missing.can_provision;
    expect(parsePaperProvisioningReadiness(missing, SELECTION).ok).toBe(false);

    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ eligibility: "ok" }),
        SELECTION,
      ).ok,
    ).toBe(false);
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ can_provision: "true" }),
        SELECTION,
      ).ok,
    ).toBe(false);
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ can_provision: null }),
        SELECTION,
      ).ok,
    ).toBe(false);
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ schema: "paper_provisioning_readiness.v2" }),
        SELECTION,
      ).ok,
    ).toBe(false);
    expect(parsePaperProvisioningReadiness(null, SELECTION).ok).toBe(false);
    expect(parsePaperProvisioningReadiness("{}", SELECTION).ok).toBe(false);

    const missingAuthorizationKey = authorization();
    delete missingAuthorizationKey.reason;
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ authorization: missingAuthorizationKey }),
        SELECTION,
      ).ok,
    ).toBe(false);
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ authorization: authorization({ bound_request_id: REQUEST_ID }) }),
        SELECTION,
      ).ok,
    ).toBe(false);
  });

  it("refuses the internal schema_version key, alone or beside schema", () => {
    const aliasOnly = preflightBody();
    delete aliasOnly.schema;
    aliasOnly.schema_version = "paper_provisioning_readiness.v1";
    expect(parsePaperProvisioningReadiness(aliasOnly, SELECTION).ok).toBe(false);

    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ schema_version: "paper_provisioning_readiness.v1" }),
        SELECTION,
      ).ok,
    ).toBe(false);

    // The same rule applies to the nested authorization object.
    const nestedAlias = authorization();
    delete nestedAlias.schema;
    nestedAlias.schema_version = "paper_provisioning_authorization.v1";
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ authorization: nestedAlias }),
        SELECTION,
      ).ok,
    ).toBe(false);
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({
          authorization: authorization({
            schema_version: "paper_provisioning_authorization.v1",
          }),
        }),
        SELECTION,
      ).ok,
    ).toBe(false);
  });

  it("refuses a selection that drifts on any field, at either level", () => {
    const drifts: Array<Record<string, unknown>> = [
      { strategy_id: "strategy-0004" },
      { content_sha256: RESULT_SHA },
      { contract_id: "YM-202609-CBOT" },
      { baseline_run_id: "nq-20260728-standard-000000" },
      { baseline_result_sha256: CONTENT_SHA },
    ];
    for (const drift of drifts) {
      const label = JSON.stringify(drift);
      expect(
        parsePaperProvisioningReadiness(
          preflightBody({ selection: { ...SELECTION, ...drift } }),
          SELECTION,
        ).ok,
        `top ${label}`,
      ).toBe(false);

      const runtime = readinessBody();
      runtime.selection = { ...SELECTION, ...drift };
      expect(
        parsePaperProvisioningReadiness(
          preflightBody({ runtime_readiness: runtime }),
          SELECTION,
        ).ok,
        `runtime ${label}`,
      ).toBe(false);
    }
  });

  it("refuses a can_provision that the body itself disproves", () => {
    for (const state of ["disabled", "claimed", "consumed", "expired"]) {
      expect(
        parsePaperProvisioningReadiness(
          preflightBody({
            can_provision: true,
            authorization:
              state === "disabled"
                ? disabledAuthorization()
                : authorization({ state }),
          }),
          SELECTION,
        ).ok,
        state,
      ).toBe(false);
    }

    // Armed is not enough: the baseline package must really be verified.
    const runtime = readinessBody();
    runtime.overall = "blocked";
    runtime.checks = checks([
      {},
      {},
      {},
      { status: "unknown", reason: "locked result package unverified" },
    ]);
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ can_provision: true, runtime_readiness: runtime }),
        SELECTION,
      ).ok,
    ).toBe(false);
  });

  it("refuses a runtime object that is not the approved four-check shape", () => {
    const fifth = readinessBody();
    fifth.checks = [
      ...checks(),
      {
        key: "baseline_integrity",
        status: "ready",
        reason: "duplicate",
        checked_at: AT,
      },
    ];
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ runtime_readiness: fifth }),
        SELECTION,
      ).ok,
    ).toBe(false);

    const inconsistent = readinessBody();
    inconsistent.overall = "ready";
    inconsistent.checks = checks([{ status: "blocked" }]);
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ runtime_readiness: inconsistent }),
        SELECTION,
      ).ok,
    ).toBe(false);

    const wrongSchema = readinessBody();
    wrongSchema.schema = "paper_readiness.v2";
    expect(
      parsePaperProvisioningReadiness(
        preflightBody({ runtime_readiness: wrongSchema }),
        SELECTION,
      ).ok,
    ).toBe(false);
  });
});

/*
 * [243] Correction A and [243-A]: the exact producer truths C-v5 proved against
 * the registered A1 route and the A2 create path.
 */
describe("Option A correction — error codes and blocked snapshots", () => {
  const NEW_CODES: Array<[string, number]> = [
    ["external_readiness_invalid", 503],
    ["provisioning_not_authorized", 503],
    ["provisioning_selection_mismatch", 409],
    ["provisioning_request_conflict", 409],
  ];

  function errorBody(code: string) {
    return {
      detail: {
        schema: "paper_api_error.v1",
        code,
        message: "backend engineering text",
        retryable: false,
      },
    };
  }

  it("accepts each Option A code only at its approved status", () => {
    for (const [code, status] of NEW_CODES) {
      const parsed = parsePaperApiError(res(errorBody(code), status, "POST"));
      expect(parsed.ok, code).toBe(true);
      if (parsed.ok) {
        expect(parsed.value.code).toBe(code);
        // Owner copy exists and never repeats the raw code or backend text.
        const copy = paperErrorCopy(parsed.value.code);
        expect(copy.length).toBeGreaterThan(0);
        expect(copy).not.toContain(code);
        expect(copy).not.toContain("backend engineering text");
        expect(copy).not.toContain(String(status));
      }
      const wrongStatus = status === 503 ? 409 : 503;
      expect(
        parsePaperApiError(res(errorBody(code), wrongStatus, "POST")).ok,
        `${code} at ${String(wrongStatus)}`,
      ).toBe(false);
    }
  });

  it("still refuses an unknown provisioning code", () => {
    for (const code of [
      "provisioning_denied",
      "provisioning_not_authorised",
      "external_readiness_unknown",
    ]) {
      expect(parsePaperApiError(res(errorBody(code), 503, "POST")).ok, code).toBe(
        false,
      );
    }
  });

  it("keeps the Stage A codes and their statuses untouched", () => {
    const legacy: Array<[string, number]> = [
      ["eligible_strategy_required", 409],
      ["baseline_not_found", 404],
      ["selection_identity_mismatch", 409],
      ["baseline_integrity_failed", 503],
      ["readiness_blocked", 409],
      ["activation_not_authorized", 503],
      ["request_id_conflict", 409],
      ["request_not_found", 404],
      ["store_unavailable", 503],
    ];
    for (const [code, status] of legacy) {
      expect(parsePaperApiError(res(errorBody(code), status, "POST")).ok, code).toBe(
        true,
      );
    }
  });

  it("reads can_provision as an exact equality, not an implication", () => {
    const OPERATION_ID = "paper-provision-0123456789abcdef0123456789abcdef";
    const armed = {
      schema: "paper_provisioning_authorization.v1",
      state: "armed",
      operation_id: OPERATION_ID,
      authorized_at: "2026-07-30T09:00:00Z",
      expires_at: "2026-07-30T09:30:00Z",
      reason: "Owner-approved one-off P6 provision-only flow verification",
    };
    const body = (
      canProvision: boolean,
      authorization: Record<string, unknown>,
      runtime = readinessBody(),
    ) => ({
      schema: "paper_provisioning_readiness.v1",
      selection: { ...SELECTION },
      can_provision: canProvision,
      authorization,
      runtime_readiness: runtime,
    });

    // The body the producer cannot emit: armed, verified, yet false.
    expect(
      parsePaperProvisioningReadiness(body(false, armed), SELECTION).ok,
    ).toBe(false);
    // The same pair the other way round stays legal.
    expect(parsePaperProvisioningReadiness(body(true, armed), SELECTION).ok).toBe(
      true,
    );
    // False beside a non-armed state is still the normal default answer.
    expect(
      parsePaperProvisioningReadiness(
        body(false, {
          ...armed,
          state: "disabled",
          operation_id: null,
          authorized_at: null,
          expires_at: null,
        }),
        SELECTION,
      ).ok,
    ).toBe(true);
    // Armed but unverified baseline must also be false, not true.
    const unverified = readinessBody();
    unverified.overall = "blocked";
    unverified.checks = checks([{}, {}, {}, { status: "unknown" }]);
    expect(
      parsePaperProvisioningReadiness(body(false, armed, unverified), SELECTION)
        .ok,
    ).toBe(true);
    expect(
      parsePaperProvisioningReadiness(body(true, armed, unverified), SELECTION)
        .ok,
    ).toBe(false);
  });

  it("accepts the real A2 shape: authorized, externally blocked, baseline ready", () => {
    const runtime = readinessBody();
    runtime.overall = "blocked";
    runtime.checks = checks([
      { status: "unknown" },
      { status: "unknown" },
      { status: "blocked" },
    ]);
    const parsed = parsePaperProvisioningReadiness(
      {
        schema: "paper_provisioning_readiness.v1",
        selection: { ...SELECTION },
        can_provision: true,
        authorization: {
          schema: "paper_provisioning_authorization.v1",
          state: "armed",
          operation_id: "paper-provision-0123456789abcdef0123456789abcdef",
          authorized_at: "2026-07-30T09:00:00Z",
          expires_at: "2026-07-30T09:30:00Z",
          reason: "Owner-approved one-off P6 provision-only flow verification",
        },
        runtime_readiness: runtime,
      },
      SELECTION,
    );
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.value.can_provision).toBe(true);
      expect(parsed.value.runtime_readiness.overall).toBe("blocked");
    }
  });

  it("accepts the same blocked trader on create, replay, status, list and detail", () => {
    const blockedTrader = () => {
      const body = clone(traderBody());
      body.readiness_snapshot.checks[0].status = "unknown";
      body.readiness_snapshot.checks[1].status = "unknown";
      body.readiness_snapshot.checks[2].status = "blocked";
      body.readiness_snapshot.overall = "blocked";
      return body;
    };

    expect(
      parsePaperCreatedTrader(res(blockedTrader(), 201, "POST"), CREATE_REQUEST)
        .ok,
    ).toBe(true);
    expect(
      parsePaperCreatedTrader(res(blockedTrader(), 200, "POST"), CREATE_REQUEST)
        .ok,
    ).toBe(true);
    expect(
      parsePaperTraderRequestStatus(
        res({
          schema: "paper_trader_request_status.v1",
          request_id: REQUEST_ID,
          status: "completed",
          trader: blockedTrader(),
        }),
        CREATE_REQUEST,
      ).ok,
    ).toBe(true);
    expect(
      parsePaperTraderList(
        res({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [blockedTrader()],
        }),
      ).ok,
    ).toBe(true);
    expect(
      parsePaperTraderDetail(res(blockedTrader()), TRADER_ID).ok,
    ).toBe(true);
  });
});
