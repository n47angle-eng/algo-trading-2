/**
 * Notification domain types — pure contracts, no browser I/O.
 * Preference gating and payload mapping live here so unit tests
 * exercise the real shipped modules without Notification API mocks
 * for the decision path.
 */

export const NOTIFICATION_TYPES = [
  "buy_opportunity",
  "entry",
  "exit_profit",
  "exit_loss",
  "backtest_complete",
] as const;

export type NotificationType = (typeof NOTIFICATION_TYPES)[number];

export interface NotificationPreferences {
  /** Master switch — when false, no notification may fire. */
  enabled: boolean;
  types: Record<NotificationType, boolean>;
}

export const NOTIFICATION_TYPE_LABELS: Record<NotificationType, string> = {
  buy_opportunity: "買入機會",
  entry: "入市 / 開倉",
  exit_profit: "獲利離場",
  exit_loss: "止損 / 離場",
  backtest_complete: "回測完成",
};

export const NOTIFICATION_TYPE_DESCRIPTIONS: Record<NotificationType, string> = {
  buy_opportunity: "模擬交易員偵測到買入機會時通知",
  entry: "模擬交易員實際入市開倉時通知",
  exit_profit: "獲利平倉 / 離場時通知",
  exit_loss: "止損或虧損離場時通知",
  backtest_complete: "回測批次完成（成功／失敗／部分）時通知",
};

export const DEFAULT_NOTIFICATION_PREFERENCES: NotificationPreferences = {
  enabled: true,
  types: {
    buy_opportunity: true,
    entry: true,
    exit_profit: true,
    exit_loss: true,
    backtest_complete: true,
  },
};

export const NOTIFICATION_PREFS_STORAGE_KEY =
  "futures-research.notification-prefs.v1";

/** Domain event the product can emit. */
export type DomainNotificationEvent =
  | {
      kind: "buy_opportunity";
      traderId: string;
      symbol?: string;
      price?: number;
      reason?: string;
      at?: string;
    }
  | {
      kind: "entry";
      traderId: string;
      symbol?: string;
      side?: "long" | "short";
      quantity?: number;
      price?: number;
      reason?: string;
      at?: string;
    }
  | {
      kind: "exit";
      traderId: string;
      symbol?: string;
      side?: "long" | "short";
      quantity?: number;
      price?: number;
      pnl?: number;
      realizedR?: number;
      reason?: string;
      profitable?: boolean;
      at?: string;
    }
  | {
      kind: "backtest_complete";
      batchId: string;
      status: "completed" | "failed" | "cancelled" | "partial";
      completed?: number;
      failed?: number;
      total?: number;
      at?: string;
    };

export interface NotificationPayload {
  type: NotificationType;
  title: string;
  body: string;
  tag: string;
  data: Record<string, string | number | boolean | null | undefined>;
}
