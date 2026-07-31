/**
 * Domain event observers — map paper activity/snapshot deltas and
 * backtest terminal status into DomainNotificationEvent, then emit.
 *
 * IMPORTANT (shipped API truth):
 * - GET .../timeline → paper_runtime_timeline.v1 lifecycle rows only
 *   (lifecycle=running|paused|…); these NEVER carry trade kinds.
 * - GET .../activity → paper_runtime_activity.v1 trade stream
 *   (kind=buy_opportunity|entry|exit) built from intents/fills/trades.
 * - Snapshot deltas (position_quantity / trade_count / decision_count)
 *   remain a reliable secondary path while the panel polls.
 */

import { emitDomainNotification } from "./emit";
import type { DomainNotificationEvent } from "./types";

/** Real lifecycle timeline payload from paper_runtime_timeline.v1. */
export interface PaperLifecycleTimelinePayload {
  lifecycle_event_id?: string;
  lifecycle?: string;
  lifecycle_version?: number;
  reason?: string;
  // legacy/synthetic fields (not emitted by shipped timeline):
  kind?: string;
  event_type?: string;
  side?: string;
  quantity?: number;
  price?: number;
  symbol?: string;
  pnl?: number;
  realized_r?: number;
  realized_pnl?: number;
  entry_price?: number;
  exit_price?: number;
  profitable?: boolean;
}

export interface PaperTimelineItem {
  cursor: number;
  created_at: string;
  payload: PaperLifecycleTimelinePayload;
}

/** Real activity payload from paper_runtime_activity.v1. */
export interface PaperActivityPayload {
  kind: string;
  source?: string;
  intent_id?: string;
  fill_id?: string;
  trade_id?: string;
  status?: string;
  side?: string;
  quantity?: number;
  price?: number;
  pnl?: number;
  realized_r?: number;
  profitable?: boolean;
  reason?: string;
  role?: string;
  symbol?: string;
}

export interface PaperActivityItem {
  cursor: number;
  created_at: string;
  payload: PaperActivityPayload;
}

export interface PaperSnapshotTradeSignal {
  traderId: string;
  previousPositionQty: number;
  nextPositionQty: number;
  previousTradeCount: number;
  nextTradeCount: number;
  previousDecisionCount: number;
  nextDecisionCount: number;
  symbol?: string;
  equity?: number;
  realizedPnl?: number;
  realizedR?: number;
  asOf?: string;
}

/**
 * Lifecycle timeline (real API) never produces trade notifications.
 * Only synthetic payloads with kind/event_type trade markers map (tests).
 */
export function eventsFromPaperTimeline(
  traderId: string,
  item: PaperTimelineItem,
): DomainNotificationEvent[] {
  const p = item.payload ?? {};
  // Real shipped shape: lifecycle + lifecycle_version + reason only.
  const kind = (p.kind ?? p.event_type ?? "").toLowerCase();
  if (!kind) {
    // Pure lifecycle control row — not a trade signal.
    return [];
  }
  const at = item.created_at;
  const symbol = p.symbol;
  const price = p.price ?? p.entry_price ?? p.exit_price;
  const sideRaw = (p.side ?? "").toLowerCase();
  const side =
    sideRaw === "long" || sideRaw === "buy"
      ? "long"
      : sideRaw === "short" || sideRaw === "sell"
        ? "short"
        : undefined;

  if (
    kind.includes("opportunity") ||
    kind.includes("signal") ||
    kind === "buy_opportunity" ||
    kind === "entry_intent" ||
    kind === "intent"
  ) {
    return [
      {
        kind: "buy_opportunity",
        traderId,
        symbol,
        price,
        reason: p.reason,
        at,
      },
    ];
  }

  if (
    kind.includes("entry") ||
    kind === "open" ||
    kind === "fill_entry" ||
    kind === "opened" ||
    (kind.includes("fill") && !kind.includes("exit") && !kind.includes("close"))
  ) {
    return [
      {
        kind: "entry",
        traderId,
        symbol,
        side,
        quantity: p.quantity,
        price: p.price ?? p.entry_price,
        reason: p.reason,
        at,
      },
    ];
  }

  if (
    kind.includes("exit") ||
    kind.includes("close") ||
    kind === "fill_exit" ||
    kind === "closed" ||
    kind === "take_profit" ||
    kind === "stop_loss"
  ) {
    const pnl = p.pnl ?? p.realized_pnl;
    const realizedR = p.realized_r;
    let profitable = p.profitable;
    if (profitable === undefined && pnl !== undefined) {
      profitable = pnl >= 0;
    }
    if (
      kind.includes("profit") ||
      kind === "take_profit" ||
      kind.includes("target")
    ) {
      profitable = true;
    }
    if (
      kind.includes("stop") ||
      kind.includes("loss") ||
      kind === "stop_loss"
    ) {
      profitable = false;
    }
    return [
      {
        kind: "exit",
        traderId,
        symbol,
        side,
        quantity: p.quantity,
        price: p.price ?? p.exit_price,
        pnl,
        realizedR,
        reason: p.reason,
        profitable,
        at,
      },
    ];
  }

  return [];
}

/**
 * Map real paper_runtime_activity.v1 items → domain events.
 */
export function eventsFromPaperActivity(
  traderId: string,
  item: PaperActivityItem,
): DomainNotificationEvent[] {
  const p = item.payload ?? ({} as PaperActivityPayload);
  const kind = (p.kind ?? "").toLowerCase();
  const at = item.created_at;
  const sideRaw = (p.side ?? "").toLowerCase();
  const side =
    sideRaw === "long" || sideRaw === "buy"
      ? "long"
      : sideRaw === "short" || sideRaw === "sell"
        ? "short"
        : undefined;

  if (kind === "buy_opportunity" || kind === "intent" || kind === "signal") {
    return [
      {
        kind: "buy_opportunity",
        traderId,
        symbol: p.symbol,
        price: typeof p.price === "number" ? p.price : undefined,
        reason: p.reason,
        at,
      },
    ];
  }

  if (kind === "entry" || p.role === "entry") {
    return [
      {
        kind: "entry",
        traderId,
        symbol: p.symbol,
        side,
        quantity: p.quantity,
        price: typeof p.price === "number" ? p.price : undefined,
        reason: p.reason,
        at,
      },
    ];
  }

  if (kind === "exit" || p.role === "exit" || kind === "trade") {
    return [
      {
        kind: "exit",
        traderId,
        symbol: p.symbol,
        side,
        quantity: p.quantity,
        price: typeof p.price === "number" ? p.price : undefined,
        pnl: p.pnl,
        realizedR: p.realized_r,
        reason: p.reason,
        profitable: p.profitable,
        at,
      },
    ];
  }

  return [];
}

/**
 * Infer events from position / trade-count deltas between two runtime snapshots.
 */
export function eventsFromPaperSnapshotDelta(
  signal: PaperSnapshotTradeSignal,
): DomainNotificationEvent[] {
  const events: DomainNotificationEvent[] = [];
  const {
    traderId,
    previousPositionQty,
    nextPositionQty,
    previousTradeCount,
    nextTradeCount,
    previousDecisionCount,
    nextDecisionCount,
    symbol,
    asOf,
    realizedPnl,
    realizedR,
  } = signal;

  if (previousPositionQty === 0 && nextPositionQty !== 0) {
    events.push({
      kind: "entry",
      traderId,
      symbol,
      side: nextPositionQty > 0 ? "long" : "short",
      quantity: Math.abs(nextPositionQty),
      at: asOf,
    });
  }

  if (previousPositionQty !== 0 && nextPositionQty === 0) {
    const profitable =
      realizedR !== undefined
        ? realizedR >= 0
        : realizedPnl !== undefined
          ? realizedPnl >= 0
          : undefined;
    events.push({
      kind: "exit",
      traderId,
      symbol,
      side: previousPositionQty > 0 ? "long" : "short",
      quantity: Math.abs(previousPositionQty),
      pnl: realizedPnl,
      realizedR,
      profitable,
      at: asOf,
    });
  }

  if (
    nextDecisionCount > previousDecisionCount &&
    previousPositionQty === 0 &&
    nextPositionQty === 0 &&
    nextTradeCount === previousTradeCount
  ) {
    events.push({
      kind: "buy_opportunity",
      traderId,
      symbol,
      reason: "策略產生新決策",
      at: asOf,
    });
  }

  return events;
}

export interface BacktestTerminalSignal {
  batchId: string;
  status: "completed" | "failed" | "cancelled" | "partial";
  previousStatus?: string;
  completed?: number;
  failed?: number;
  total?: number;
  at?: string;
}

export function eventFromBacktestTerminal(
  signal: BacktestTerminalSignal,
): DomainNotificationEvent | null {
  const terminal = ["completed", "failed", "cancelled", "partial"] as const;
  if (!(terminal as readonly string[]).includes(signal.status)) {
    return null;
  }
  if (
    signal.previousStatus &&
    (terminal as readonly string[]).includes(signal.previousStatus)
  ) {
    return null;
  }
  return {
    kind: "backtest_complete",
    batchId: signal.batchId,
    status: signal.status,
    completed: signal.completed,
    failed: signal.failed,
    total: signal.total,
    at: signal.at,
  };
}

const notifiedKeys = new Set<string>();

export function resetNotificationDedup(): void {
  notifiedKeys.clear();
}

export function markNotified(key: string): boolean {
  if (notifiedKeys.has(key)) {
    return false;
  }
  notifiedKeys.add(key);
  if (notifiedKeys.size > 500) {
    const first = notifiedKeys.values().next().value;
    if (first !== undefined) {
      notifiedKeys.delete(first);
    }
  }
  return true;
}

export async function processPaperTimelineNotifications(
  traderId: string,
  items: readonly PaperTimelineItem[],
  lastCursor: number,
): Promise<number> {
  let maxCursor = lastCursor;
  const sorted = [...items].sort((a, b) => a.cursor - b.cursor);
  for (const item of sorted) {
    if (item.cursor <= lastCursor) {
      continue;
    }
    maxCursor = Math.max(maxCursor, item.cursor);
    const events = eventsFromPaperTimeline(traderId, item);
    for (const event of events) {
      const key = `tl:${traderId}:${item.cursor}:${event.kind}`;
      if (!markNotified(key)) {
        continue;
      }
      await emitDomainNotification(event);
    }
  }
  return maxCursor;
}

export async function processPaperActivityNotifications(
  traderId: string,
  items: readonly PaperActivityItem[],
  lastCursor: number,
): Promise<number> {
  let maxCursor = lastCursor;
  const sorted = [...items].sort((a, b) => a.cursor - b.cursor);
  for (const item of sorted) {
    if (item.cursor <= lastCursor) {
      continue;
    }
    maxCursor = Math.max(maxCursor, item.cursor);
    const events = eventsFromPaperActivity(traderId, item);
    for (const event of events) {
      const key = `act:${traderId}:${item.cursor}:${event.kind}`;
      if (!markNotified(key)) {
        continue;
      }
      await emitDomainNotification(event);
    }
  }
  return maxCursor;
}

export async function processPaperSnapshotNotifications(
  signal: PaperSnapshotTradeSignal,
): Promise<void> {
  const events = eventsFromPaperSnapshotDelta(signal);
  for (const event of events) {
    const key = `snap:${signal.traderId}:${signal.asOf ?? ""}:${event.kind}:${signal.nextPositionQty}:${signal.nextTradeCount}:${signal.nextDecisionCount}`;
    if (!markNotified(key)) {
      continue;
    }
    await emitDomainNotification(event);
  }
}

export async function processBacktestTerminalNotification(
  signal: BacktestTerminalSignal,
): Promise<void> {
  const event = eventFromBacktestTerminal(signal);
  if (!event) {
    return;
  }
  const key = `bt:${signal.batchId}:${signal.status}`;
  if (!markNotified(key)) {
    return;
  }
  await emitDomainNotification(event);
}
