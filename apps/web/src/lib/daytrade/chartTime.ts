/**
 * Convert ISO timestamps to lightweight-charts Time (unix seconds).
 */

export function toChartTime(iso: string | null | undefined): number | null {
  if (!iso) {
    return null;
  }
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) {
    return null;
  }
  return Math.floor(ms / 1000);
}
