/**
 * Unit tests for notification preferences + event→payload gating.
 * Calls real shipped modules only (no re-implementation).
 *
 * Honest API shapes:
 * - paper_runtime_timeline.v1 lifecycle rows → NO trade events
 * - paper_runtime_activity.v1 kind payloads → buy/entry/exit
 * - snapshot position/trade/decision deltas → secondary path
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createMemoryStorage } from "../storage/memoryStorage";
import {
  buildNotificationPayload,
  decideNotification,
  defaultNotificationPreferences,
  eventFromBacktestTerminal,
  eventsFromPaperActivity,
  eventsFromPaperSnapshotDelta,
  eventsFromPaperTimeline,
  isNotificationAllowed,
  loadNotificationPreferences,
  NOTIFICATION_PREFS_STORAGE_KEY,
  parseNotificationPreferences,
  resetNotificationDedup,
  saveNotificationPreferences,
  setMasterNotificationEnabled,
  setNotificationTypeEnabled,
  type DomainNotificationEvent,
  type NotificationPreferences,
} from "./index";

describe("notification preferences", () => {
  it("defaults enable all types and master switch", () => {
    const prefs = defaultNotificationPreferences();
    expect(prefs.enabled).toBe(true);
    expect(prefs.types.buy_opportunity).toBe(true);
    expect(prefs.types.entry).toBe(true);
    expect(prefs.types.exit_profit).toBe(true);
    expect(prefs.types.exit_loss).toBe(true);
    expect(prefs.types.backtest_complete).toBe(true);
  });

  it("persists and reloads preferences from storage", () => {
    const storage = createMemoryStorage();
    const prefs = setNotificationTypeEnabled(
      defaultNotificationPreferences(),
      "buy_opportunity",
      false,
    );
    saveNotificationPreferences(prefs, storage);
    const raw = storage.getItem(NOTIFICATION_PREFS_STORAGE_KEY);
    expect(raw).toBeTruthy();
    const loaded = loadNotificationPreferences(storage);
    expect(loaded.types.buy_opportunity).toBe(false);
    expect(loaded.types.entry).toBe(true);
  });

  it("parse falls back field-by-field on partial / invalid input", () => {
    const partial = parseNotificationPreferences({
      enabled: false,
      types: { entry: false, not_a_type: true },
    });
    expect(partial.enabled).toBe(false);
    expect(partial.types.entry).toBe(false);
    expect(partial.types.backtest_complete).toBe(true);
    expect(parseNotificationPreferences(null).enabled).toBe(true);
    expect(parseNotificationPreferences("bad").enabled).toBe(true);
  });

  it("master off blocks all types; type off blocks only that type", () => {
    let prefs: NotificationPreferences = defaultNotificationPreferences();
    prefs = setMasterNotificationEnabled(prefs, false);
    expect(isNotificationAllowed(prefs, "entry")).toBe(false);
    expect(isNotificationAllowed(prefs, "backtest_complete")).toBe(false);

    prefs = setMasterNotificationEnabled(prefs, true);
    prefs = setNotificationTypeEnabled(prefs, "exit_profit", false);
    expect(isNotificationAllowed(prefs, "exit_profit")).toBe(false);
    expect(isNotificationAllowed(prefs, "entry")).toBe(true);
  });
});

describe("event → payload mapping", () => {
  it("maps buy opportunity, entry, exit profit/loss, backtest complete", () => {
    const buy = buildNotificationPayload({
      kind: "buy_opportunity",
      traderId: "trader-1",
      symbol: "NQ",
      price: 21000.5,
      reason: "突破",
    });
    expect(buy.type).toBe("buy_opportunity");
    expect(buy.title).toContain("買入");
    expect(buy.body).toContain("trader-1");
    expect(buy.body).toContain("NQ");
    expect(buy.tag).toContain("buy-opp");

    const entry = buildNotificationPayload({
      kind: "entry",
      traderId: "trader-1",
      symbol: "YM",
      side: "long",
      quantity: 1,
      price: 40000,
    });
    expect(entry.type).toBe("entry");
    expect(entry.title).toContain("入市");
    expect(entry.body).toContain("多");

    const profit = buildNotificationPayload({
      kind: "exit",
      traderId: "trader-1",
      symbol: "GC",
      pnl: 120,
      realizedR: 1.5,
      profitable: true,
    });
    expect(profit.type).toBe("exit_profit");
    expect(profit.title).toContain("獲利");

    const loss = buildNotificationPayload({
      kind: "exit",
      traderId: "trader-1",
      pnl: -50,
      profitable: false,
    });
    expect(loss.type).toBe("exit_loss");

    const bt = buildNotificationPayload({
      kind: "backtest_complete",
      batchId: "batch-abc",
      status: "completed",
      completed: 3,
      total: 3,
    });
    expect(bt.type).toBe("backtest_complete");
    expect(bt.title).toContain("回測");
    expect(bt.body).toContain("batch-abc");
    expect(bt.tag).toContain("backtest-batch-abc");
  });
});

describe("preference gating via decideNotification", () => {
  it("allows when prefs enable type", () => {
    const event: DomainNotificationEvent = {
      kind: "entry",
      traderId: "t1",
      symbol: "NQ",
    };
    const decision = decideNotification(
      event,
      defaultNotificationPreferences(),
    );
    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBe("ok");
    expect(decision.payload?.type).toBe("entry");
  });

  it("blocks when type is disabled (does not schedule notify)", () => {
    const prefs = setNotificationTypeEnabled(
      defaultNotificationPreferences(),
      "backtest_complete",
      false,
    );
    const decision = decideNotification(
      {
        kind: "backtest_complete",
        batchId: "b1",
        status: "completed",
      },
      prefs,
    );
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toBe("type_disabled");
    expect(decision.payload?.type).toBe("backtest_complete");
  });

  it("blocks when master switch is off", () => {
    const prefs = setMasterNotificationEnabled(
      defaultNotificationPreferences(),
      false,
    );
    const decision = decideNotification(
      { kind: "buy_opportunity", traderId: "t1" },
      prefs,
    );
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toBe("prefs_disabled");
  });
});

describe("real shipped API payload shapes", () => {
  beforeEach(() => {
    resetNotificationDedup();
  });
  afterEach(() => {
    resetNotificationDedup();
  });

  it("lifecycle timeline.v1 rows produce ZERO trade events (honest API)", () => {
    // Exact shape from paper_runtime_service.runtime_timeline
    const lifecycleItem = {
      cursor: 3,
      created_at: "2026-07-31T13:30:00Z",
      payload: {
        lifecycle_event_id: "paper-lifecycle-abc",
        lifecycle: "running",
        lifecycle_version: 2,
        reason: "runtime started after explicit Owner command",
      },
    };
    expect(eventsFromPaperTimeline("trader-x", lifecycleItem)).toEqual([]);

    for (const lifecycle of [
      "provisioned",
      "paused",
      "pausing",
      "permanently_stopped",
      "tripped",
    ]) {
      expect(
        eventsFromPaperTimeline("trader-x", {
          cursor: 1,
          created_at: "2026-07-31T13:30:00Z",
          payload: {
            lifecycle_event_id: "id",
            lifecycle,
            lifecycle_version: 1,
            reason: "control",
          },
        }),
      ).toEqual([]);
    }
  });

  it("activity.v1 buy_opportunity / entry / exit map to domain events", () => {
    const buy = eventsFromPaperActivity("trader-x", {
      cursor: 1,
      created_at: "2026-07-31T10:00:00Z",
      payload: {
        kind: "buy_opportunity",
        source: "intent",
        intent_id: "paper-intent-1",
        status: "pending",
        reason: "策略產生入市意圖",
        price: 21000,
      },
    });
    expect(buy).toHaveLength(1);
    expect(buy[0]?.kind).toBe("buy_opportunity");

    const entry = eventsFromPaperActivity("trader-x", {
      cursor: 2,
      created_at: "2026-07-31T10:01:00Z",
      payload: {
        kind: "entry",
        source: "fill",
        fill_id: "paper-fill-1",
        role: "entry",
        side: "long",
        quantity: 1,
        price: 21001.5,
      },
    });
    expect(entry[0]?.kind).toBe("entry");
    if (entry[0]?.kind === "entry") {
      expect(entry[0].side).toBe("long");
      expect(entry[0].price).toBe(21001.5);
    }

    const exit = eventsFromPaperActivity("trader-x", {
      cursor: 3,
      created_at: "2026-07-31T10:02:00Z",
      payload: {
        kind: "exit",
        source: "trade",
        trade_id: "paper-trade-1",
        side: "long",
        quantity: 1,
        pnl: 12.5,
        realized_r: 0.8,
        profitable: true,
        reason: "模擬交易平倉",
      },
    });
    expect(exit[0]?.kind).toBe("exit");
    if (exit[0]?.kind === "exit") {
      expect(exit[0].profitable).toBe(true);
      expect(exit[0].pnl).toBe(12.5);
    }
  });

  it("snapshot deltas infer entry/exit/opportunity (live poll path)", () => {
    const entry = eventsFromPaperSnapshotDelta({
      traderId: "t1",
      previousPositionQty: 0,
      nextPositionQty: 1,
      previousTradeCount: 0,
      nextTradeCount: 0,
      previousDecisionCount: 0,
      nextDecisionCount: 0,
    });
    expect(entry.some((e) => e.kind === "entry")).toBe(true);

    const exit = eventsFromPaperSnapshotDelta({
      traderId: "t1",
      previousPositionQty: 1,
      nextPositionQty: 0,
      previousTradeCount: 0,
      nextTradeCount: 1,
      previousDecisionCount: 1,
      nextDecisionCount: 1,
      realizedR: 0.8,
    });
    expect(exit.some((e) => e.kind === "exit")).toBe(true);

    const opp = eventsFromPaperSnapshotDelta({
      traderId: "t1",
      previousPositionQty: 0,
      nextPositionQty: 0,
      previousTradeCount: 0,
      nextTradeCount: 0,
      previousDecisionCount: 1,
      nextDecisionCount: 2,
    });
    expect(opp.some((e) => e.kind === "buy_opportunity")).toBe(true);
  });

  it("activity event gated by disabled preference does not allow fire", () => {
    const prefs = setNotificationTypeEnabled(
      defaultNotificationPreferences(),
      "entry",
      false,
    );
    const events = eventsFromPaperActivity("t1", {
      cursor: 1,
      created_at: "2026-07-31T10:00:00Z",
      payload: {
        kind: "entry",
        source: "fill",
        role: "entry",
        quantity: 1,
        price: 100,
        side: "long",
      },
    });
    expect(events).toHaveLength(1);
    const decision = decideNotification(events[0]!, prefs);
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toBe("type_disabled");
  });

  it("emits backtest terminal only on transition into terminal status", () => {
    const first = eventFromBacktestTerminal({
      batchId: "b1",
      status: "completed",
      previousStatus: "running",
      completed: 2,
      total: 2,
    });
    expect(first?.kind).toBe("backtest_complete");

    const again = eventFromBacktestTerminal({
      batchId: "b1",
      status: "completed",
      previousStatus: "completed",
    });
    expect(again).toBeNull();
  });
});

describe("decideNotification integration with disabled type does not allow fire", () => {
  it("type_disabled decision never returns allowed true", () => {
    const types = [
      "buy_opportunity",
      "entry",
      "exit_profit",
      "exit_loss",
      "backtest_complete",
    ] as const;

    for (const type of types) {
      const prefs = setNotificationTypeEnabled(
        defaultNotificationPreferences(),
        type,
        false,
      );
      let event: DomainNotificationEvent;
      if (type === "buy_opportunity") {
        event = { kind: "buy_opportunity", traderId: "t" };
      } else if (type === "entry") {
        event = { kind: "entry", traderId: "t" };
      } else if (type === "exit_profit") {
        event = { kind: "exit", traderId: "t", profitable: true };
      } else if (type === "exit_loss") {
        event = { kind: "exit", traderId: "t", profitable: false };
      } else {
        event = {
          kind: "backtest_complete",
          batchId: "b",
          status: "completed",
        };
      }
      const decision = decideNotification(event, prefs);
      expect(decision.allowed).toBe(false);
      expect(decision.reason).toBe("type_disabled");
    }
  });
});

void vi;
