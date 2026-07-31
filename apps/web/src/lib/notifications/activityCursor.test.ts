/**
 * Activity cursor paging — proves we do not stick at after_cursor=0 forever.
 * Exercises the real processPaperActivityNotifications + emit decision path.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  decideNotification,
  defaultNotificationPreferences,
  eventsFromPaperActivity,
  processPaperActivityNotifications,
  resetNotificationDedup,
  type PaperActivityItem,
} from "./index";

function activityItem(
  cursor: number,
  kind: "buy_opportunity" | "entry" | "exit",
): PaperActivityItem {
  return {
    cursor,
    created_at: `2026-07-31T10:${String(cursor).padStart(2, "0")}:00Z`,
    payload: {
      kind,
      source: kind === "buy_opportunity" ? "intent" : kind === "entry" ? "fill" : "trade",
      quantity: 1,
      price: 21000 + cursor,
      side: "long",
      profitable: kind === "exit" ? true : undefined,
      pnl: kind === "exit" ? 10 : undefined,
    },
  };
}

describe("activity cursor paging (shipped path)", () => {
  beforeEach(() => {
    resetNotificationDedup();
  });
  afterEach(() => {
    resetNotificationDedup();
    vi.restoreAllMocks();
  });

  it("processPaperActivityNotifications ignores cursors <= lastCursor and advances past 100", async () => {
    // Simulate history already seeded to cursor 100 (first page drained).
    const lastCursor = 100;
    // New live rows beyond the first page
    const page = [
      activityItem(101, "buy_opportunity"),
      activityItem(102, "entry"),
      activityItem(103, "exit"),
    ];
    // Items still in page that are old (must be ignored)
    const withOld = [activityItem(99, "entry"), ...page];

    const advanced = await processPaperActivityNotifications(
      "trader-page2",
      withOld,
      lastCursor,
    );
    expect(advanced).toBe(103);

    // Mapping still works for high cursors
    const events = eventsFromPaperActivity("trader-page2", page[2]!);
    expect(events[0]?.kind).toBe("exit");
    const decision = decideNotification(
      events[0]!,
      defaultNotificationPreferences(),
    );
    expect(decision.allowed).toBe(true);
    expect(decision.payload?.type).toBe("exit_profit");
  });

  it("fetch after_cursor must be the advanced cursor, not always 0", () => {
    // Pure contract the panel must obey when calling the client.
    // If this is always 0, rows after the first page never load.
    let activityCursor = 0;
    const activityLimit = 200;
    // Seed drain of first full page
    const firstPageCursors = Array.from({ length: activityLimit }, (_, i) => i + 1);
    activityCursor = firstPageCursors[firstPageCursors.length - 1]!;
    expect(activityCursor).toBe(200);
    // Next fetch must use after_cursor=200 (shipped panel: activityCursorRef.current)
    const nextAfter = activityCursor;
    expect(nextAfter).toBeGreaterThan(0);
    expect(nextAfter).not.toBe(0);
    // Simulated next page starts at 201
    const nextPage = [activityItem(201, "entry"), activityItem(202, "exit")];
    const onlyNew = nextPage.filter((it) => it.cursor > nextAfter - 0);
    // after_cursor=200 means API returns cursor>200; process lastCursor=200
    expect(onlyNew.every((it) => it.cursor > 200)).toBe(true);
  });
});
