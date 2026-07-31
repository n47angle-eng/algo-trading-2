/**
 * Pure helpers for ChartGrid — separated so component file only exports React component.
 */

import {
  findContainingBarAnchor,
  findContainingBarIndex,
  type BarAnchor,
  type ChartTimeframe,
  TF_DURATION_SEC,
} from "../../lib/results/timeIdentity";

export {
  findContainingBarAnchor,
  findContainingBarIndex,
  TF_DURATION_SEC,
};
export type { BarAnchor, ChartTimeframe };

/**
 * Jump visible range so the containing bar is near the center (D19/D28/D35).
 */
export function computeVisibleLogicalRange(
  candleTimes: number[],
  timeSec: number,
  durationSec: number,
  pad = 20,
): { from: number; to: number; barIndex: number; barTime: number } | null {
  const anchor = findContainingBarAnchor(candleTimes, timeSec, durationSec);
  if (!anchor) {
    return null;
  }
  const from = Math.max(0, anchor.barIndex - pad);
  const to = Math.min(candleTimes.length - 1, anchor.barIndex + pad);
  return {
    from,
    to,
    barIndex: anchor.barIndex,
    barTime: anchor.barTime,
  };
}

export interface CrosshairTarget {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  chart: { setCrosshairPosition: (...args: any[]) => void };
  series: unknown;
  candleTimes: number[];
  candles: Array<{ open: number; high: number; low: number; close: number }>;
  durationSec: number;
}

/**
 * Crosshair sync (D19/D35): each target maps canonical event time to its own
 * containing bar, series, anchor time, and price. Never reuses raw source time
 * for all panes.
 */
export function applyCrosshairToTargets(
  targets: CrosshairTarget[],
  /** Canonical event instant (unix seconds). */
  eventTimeSec: number,
  sourceSeries: unknown,
): void {
  void sourceSeries;
  for (const t of targets) {
    const anchor = findContainingBarAnchor(
      t.candleTimes,
      eventTimeSec,
      t.durationSec,
    );
    if (!anchor) {
      continue;
    }
    const price = priceAtBarIndex(t.candles, anchor.barIndex);
    if (price == null) {
      continue;
    }
    try {
      t.chart.setCrosshairPosition(price, anchor.barTime, t.series);
    } catch {
      /* range miss ok */
    }
  }
}

/** Price for highlight: use target bar OHLC mid; never hardcode 0 (D28). */
export function priceAtBarIndex(
  candles: Array<{ open: number; high: number; low: number; close: number }>,
  barIndex: number,
): number | null {
  const c = candles[barIndex];
  if (!c) {
    return null;
  }
  return (c.high + c.low) / 2;
}

export function durationForTf(tf: ChartTimeframe): number {
  return TF_DURATION_SEC[tf];
}
