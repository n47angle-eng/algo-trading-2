/**
 * Navigation model — one list, two presentations.
 *
 * Desktop renders every item in the sidebar. The phone tab bar shows only the
 * pages the owner actually switches between mid-session (MOBILE_NAV_IDS); the
 * rest live one tap away in the 更多 sheet, so the bar never shrinks its touch
 * targets below 44px.
 */

import type { IconName } from "./components/ui/Icon";

/**
 * Rail sections. Eight equal-weight rows read as one long list you have to
 * scan every time; three labelled groups let the eye jump straight to the
 * part of the work you are in.
 */
export type NavGroupId = "research" | "simulation" | "system";

export const NAV_GROUPS: ReadonlyArray<{ id: NavGroupId; label: string }> = [
  { id: "research", label: "研究" },
  { id: "simulation", label: "模擬" },
  { id: "system", label: "系統" },
];

export interface NavItem {
  id: string;
  label: string;
  /** Two-character label for the phone tab bar. */
  short: string;
  icon: IconName;
  path: string;
  group: NavGroupId;
  /** Placeholder = route exists but feature deferred (模擬盤 later WO). */
  placeholder?: boolean;
  /** One line explaining the page — shown behind the info glyph, not printed. */
  subtitle: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: "overview",
    group: "research",
    label: "總覽",
    short: "總覽",
    icon: "overview",
    path: "/",
    subtitle: "P1 — 連接狀態、進行中 run、盤前摘要",
  },
  {
    id: "strategies",
    group: "research",
    label: "策略工作台",
    short: "策略",
    icon: "strategies",
    path: "/strategies",
    subtitle: "P2 — 草圖、量化確認、版本庫",
  },
  {
    id: "data",
    group: "research",
    label: "數據",
    short: "數據",
    icon: "data",
    path: "/data",
    subtitle: "P3 — 覆蓋、下載、質量報告、黑名單",
  },
  {
    id: "backtest",
    group: "research",
    label: "回測",
    short: "回測",
    icon: "backtest",
    path: "/backtest",
    subtitle: "P4 — 設定、進度、最近回測",
  },
  {
    id: "results",
    group: "research",
    label: "結果",
    short: "結果",
    icon: "results",
    path: "/results",
    subtitle: "P5 — 列表、四格、因果、決定",
  },
  {
    id: "paper",
    group: "simulation",
    label: "模擬盤",
    short: "模擬",
    icon: "paper",
    path: "/paper",
    subtitle: "P6 — 多交易員總覽 · IB 行情驅動嘅 app 自家模擬",
  },
  {
    id: "daytrade",
    group: "simulation",
    label: "日內模擬",
    short: "日內",
    icon: "daytrade",
    path: "/daytrade",
    subtitle: "模擬交易員名單 · 個人檔案／倉位／成績表 · 1m_close live",
  },
  {
    id: "settings",
    group: "system",
    label: "設定",
    short: "設定",
    icon: "settings",
    path: "/settings",
    subtitle: "通知偏好 · 主畫面安裝 · PWA",
  },
] as const;

/**
 * Phone tab bar — ordered by how often the owner reaches for it mid-session,
 * not by where a page sits in the research loop.
 *
 * 策略工作台 left the bar: it is a sit-down task that starts from a Terminal
 * conversation, not something tapped between glances, and its slot now carries
 * 交易員快覽 — the roster, which *is* checked repeatedly while the market is
 * open. It stays one tap away in 更多.
 */
export const MOBILE_NAV_IDS = [
  "overview",
  "backtest",
  "results",
  "paper",
] as const;

/** Everything else reachable from the 更多 sheet. */
export const MOBILE_OVERFLOW_ITEMS = NAV_ITEMS.filter(
  (item) => !(MOBILE_NAV_IDS as readonly string[]).includes(item.id),
);
