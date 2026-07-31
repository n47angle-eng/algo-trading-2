/**
 * How a simulated trader's numbers and status are worded.
 *
 * Split out of the card component so both card shapes and any future surface
 * read from one place — and so the raw roster status code never reaches the
 * screen (banned-vocabulary rule).
 */

export function formatMoney(n: number | undefined | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function formatPercent(n: number | undefined | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(2)}%`;
}

/**
 * The three values the roster actually produces (ledger.py `card()`):
 * `ready` before any session exists, `live` mid-session, `flat` once the
 * session is complete.
 */
export function traderStatusLabel(status: string | undefined): string {
  switch (status) {
    case "ready":
      return "未開始";
    case "live":
      return "行緊";
    case "flat":
      return "今日收咗";
    default:
      // Fail closed rather than printing an unknown engineering code.
      return status ? "狀態未知" : "—";
  }
}

/** Sign of the day, for colour only — an unknown value must stay neutral. */
export function dayTone(n: number | undefined | null): "up" | "down" | "flat" {
  if (n == null || Number.isNaN(n) || n === 0) return "flat";
  return n > 0 ? "up" : "down";
}
