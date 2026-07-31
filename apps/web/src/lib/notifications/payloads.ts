/**
 * Map domain events → Notification API payloads.
 * Pure functions — no browser I/O.
 */

import type {
  DomainNotificationEvent,
  NotificationPayload,
  NotificationType,
} from "./types";

function fmtPrice(price: number | undefined): string {
  if (price === undefined || Number.isNaN(price)) {
    return "";
  }
  return price.toFixed(2);
}

function fmtPnl(pnl: number | undefined, r: number | undefined): string {
  const parts: string[] = [];
  if (pnl !== undefined && !Number.isNaN(pnl)) {
    const sign = pnl >= 0 ? "+" : "";
    parts.push(`${sign}${pnl.toFixed(2)}`);
  }
  if (r !== undefined && !Number.isNaN(r)) {
    const sign = r >= 0 ? "+" : "";
    parts.push(`${sign}${r.toFixed(2)}R`);
  }
  return parts.join(" · ");
}

function exitType(event: Extract<DomainNotificationEvent, { kind: "exit" }>): NotificationType {
  if (event.profitable === true) {
    return "exit_profit";
  }
  if (event.profitable === false) {
    return "exit_loss";
  }
  if (event.pnl !== undefined) {
    return event.pnl >= 0 ? "exit_profit" : "exit_loss";
  }
  if (event.realizedR !== undefined) {
    return event.realizedR >= 0 ? "exit_profit" : "exit_loss";
  }
  // Unknown PnL — treat as generic leave-market under exit_profit channel
  // (settings still has both; default body does not claim profit).
  return "exit_profit";
}

/**
 * Build a notification payload from a domain event.
 * Always returns a payload — preference gating is separate.
 */
export function buildNotificationPayload(
  event: DomainNotificationEvent,
): NotificationPayload {
  switch (event.kind) {
    case "buy_opportunity": {
      const symbol = event.symbol ?? "合約";
      const pricePart = event.price !== undefined ? ` @ ${fmtPrice(event.price)}` : "";
      const reason = event.reason ? ` — ${event.reason}` : "";
      return {
        type: "buy_opportunity",
        title: "買入機會",
        body: `模擬交易員 ${event.traderId} · ${symbol}${pricePart}${reason}`,
        tag: `buy-opp-${event.traderId}-${event.at ?? "now"}`,
        data: {
          type: "buy_opportunity",
          traderId: event.traderId,
          symbol: event.symbol ?? null,
          price: event.price ?? null,
          reason: event.reason ?? null,
          at: event.at ?? null,
        },
      };
    }
    case "entry": {
      const symbol = event.symbol ?? "合約";
      const side =
        event.side === "short" ? "空" : event.side === "long" ? "多" : "開倉";
      const qty =
        event.quantity !== undefined ? ` ×${event.quantity}` : "";
      const pricePart =
        event.price !== undefined ? ` @ ${fmtPrice(event.price)}` : "";
      return {
        type: "entry",
        title: "入市開倉",
        body: `模擬交易員 ${event.traderId} · ${side}${qty} ${symbol}${pricePart}`,
        tag: `entry-${event.traderId}-${event.at ?? "now"}`,
        data: {
          type: "entry",
          traderId: event.traderId,
          symbol: event.symbol ?? null,
          side: event.side ?? null,
          quantity: event.quantity ?? null,
          price: event.price ?? null,
          at: event.at ?? null,
        },
      };
    }
    case "exit": {
      const type = exitType(event);
      const symbol = event.symbol ?? "合約";
      const pnlPart = fmtPnl(event.pnl, event.realizedR);
      const title =
        type === "exit_profit" ? "獲利離場" : "止損 / 離場";
      const bodyCore = `模擬交易員 ${event.traderId} · ${symbol}`;
      const body = pnlPart ? `${bodyCore} · ${pnlPart}` : bodyCore;
      return {
        type,
        title,
        body: event.reason ? `${body} — ${event.reason}` : body,
        tag: `exit-${event.traderId}-${event.at ?? "now"}`,
        data: {
          type,
          traderId: event.traderId,
          symbol: event.symbol ?? null,
          pnl: event.pnl ?? null,
          realizedR: event.realizedR ?? null,
          profitable: event.profitable ?? null,
          reason: event.reason ?? null,
          at: event.at ?? null,
        },
      };
    }
    case "backtest_complete": {
      const statusLabel: Record<string, string> = {
        completed: "已完成",
        failed: "失敗",
        cancelled: "已取消",
        partial: "部分完成",
      };
      const statusText = statusLabel[event.status] ?? event.status;
      const counts =
        event.total !== undefined
          ? ` · ${event.completed ?? 0}/${event.total} 成功` +
            (event.failed ? ` · ${event.failed} 失敗` : "")
          : "";
      return {
        type: "backtest_complete",
        title: `回測${statusText}`,
        body: `批次 ${event.batchId}${counts}`,
        tag: `backtest-${event.batchId}-${event.status}`,
        data: {
          type: "backtest_complete",
          batchId: event.batchId,
          status: event.status,
          completed: event.completed ?? null,
          failed: event.failed ?? null,
          total: event.total ?? null,
          at: event.at ?? null,
        },
      };
    }
    default: {
      const _exhaustive: never = event;
      void _exhaustive;
      throw new Error("unknown domain notification event");
    }
  }
}
