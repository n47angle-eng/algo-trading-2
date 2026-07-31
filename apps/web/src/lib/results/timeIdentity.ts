/**
 * Canonical time helpers for P5 fixtures and chart bar mapping (D28/D34/D35).
 * Asia/Hong_Kong has no DST — fixed UTC+8.
 */

export const FIXTURE_TZ = "Asia/Hong_Kong";
const HKT_OFFSET_HOURS = 8;

export type ChartTimeframe = "D" | "1H" | "30m" | "5m";

/** Explicit timeframe durations (seconds) for [barStart, barEnd) membership. */
export const TF_DURATION_SEC: Record<ChartTimeframe, number> = {
  D: 86_400,
  "1H": 3_600,
  "30m": 1_800,
  "5m": 300,
};

/** Build unix seconds from HKT wall-clock components. */
export function hktLocalToUnixSec(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second = 0,
): number {
  return Math.floor(
    Date.UTC(year, month - 1, day, hour - HKT_OFFSET_HOURS, minute, second) /
      1000,
  );
}

/** Format unix seconds as `YYYY-MM-DD HH:mm` in HKT. */
export function formatHktLocal(unixSec: number): string {
  const shifted = new Date((unixSec + HKT_OFFSET_HOURS * 3600) * 1000);
  const y = shifted.getUTCFullYear();
  const m = String(shifted.getUTCMonth() + 1).padStart(2, "0");
  const d = String(shifted.getUTCDate()).padStart(2, "0");
  const hh = String(shifted.getUTCHours()).padStart(2, "0");
  const mm = String(shifted.getUTCMinutes()).padStart(2, "0");
  return `${y}-${m}-${d} ${hh}:${mm}`;
}

/** ISO UTC string from unix seconds. */
export function unixSecToIso(unixSec: number): string {
  return new Date(unixSec * 1000).toISOString();
}

/** Vertical annotation label: `#N · YYYY-MM-DD HH:mm` in fixture TZ. */
export function verticalLabel(n: number, unixSec: number): string {
  return `#${n} · ${formatHktLocal(unixSec)}`;
}

export interface BarAnchor {
  barIndex: number;
  /** Exact candle time (member of candleTimes) — use for timeToCoordinate. */
  barTime: number;
  barEnd: number;
}

/**
 * Resolve the bar that contains event timeSec in [barStart, barEnd).
 * barEnd = min(nextBarStart, barStart + durationSec).
 * Returns null for before-first, after-last, and true gaps (D34/D35).
 * Never uses first bar with time >= event (that jumps D to next day).
 */
export function findContainingBarAnchor(
  candleTimes: number[],
  timeSec: number,
  durationSec: number,
): BarAnchor | null {
  if (candleTimes.length === 0 || durationSec <= 0) {
    return null;
  }
  if (timeSec < candleTimes[0]) {
    return null;
  }
  let idx = -1;
  for (let i = 0; i < candleTimes.length; i++) {
    if (candleTimes[i] <= timeSec) {
      idx = i;
    } else {
      break;
    }
  }
  if (idx < 0) {
    return null;
  }
  const barStart = candleTimes[idx];
  const nextStart =
    idx + 1 < candleTimes.length ? candleTimes[idx + 1] : null;
  const naturalEnd = barStart + durationSec;
  const barEnd = nextStart != null ? Math.min(nextStart, naturalEnd) : naturalEnd;
  if (timeSec >= barStart && timeSec < barEnd) {
    return { barIndex: idx, barTime: barStart, barEnd };
  }
  // gap (between bars) or after last bar's valid end
  return null;
}

/** @deprecated prefer findContainingBarAnchor — kept as index-only wrapper. */
export function findContainingBarIndex(
  candleTimes: number[],
  timeSec: number,
  durationSec: number = TF_DURATION_SEC.D,
): number | null {
  return findContainingBarAnchor(candleTimes, timeSec, durationSec)?.barIndex ?? null;
}

/** Daily bucket (UTC midnight) for an instant — used for trend-day sets. */
export function utcDayBucket(unixSec: number): number {
  return Math.floor(unixSec / 86400) * 86400;
}
