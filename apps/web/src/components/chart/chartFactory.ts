/**
 * Typed chart factory seam (D36/D37) — production uses lightweight-charts;
 * tests inject a minimal fake without casting production IChartApi to test magic.
 */

import type { UTCTimestamp } from "lightweight-charts";

export interface ChartSeriesApi {
  setData: (data: unknown[]) => void;
  update: (bar: unknown) => void;
  setMarkers: (markers: unknown[]) => void;
  createPriceLine: (opts: unknown) => unknown;
}

export interface ChartTimeScaleApi {
  timeToCoordinate: (time: UTCTimestamp) => number | null;
  setVisibleLogicalRange: (range: { from: number; to: number }) => void;
  subscribeVisibleLogicalRangeChange: (cb: () => void) => void;
  unsubscribeVisibleLogicalRangeChange: (cb: () => void) => void;
}

export interface ChartApiLike {
  addCandlestickSeries: (opts?: unknown) => ChartSeriesApi;
  addHistogramSeries: (opts?: unknown) => ChartSeriesApi;
  addLineSeries: (opts?: unknown) => ChartSeriesApi;
  priceScale: (id: string) => { applyOptions: (opts: unknown) => void };
  timeScale: () => ChartTimeScaleApi;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  subscribeCrosshairMove: (cb: (param: any) => void) => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  setCrosshairPosition: (...args: any[]) => void;
  remove: () => void;
}

export type ChartCreateFn = (
  host: HTMLElement,
  options?: unknown,
) => ChartApiLike;

/**
 * Exact-member coordinate fake for contracts (D36): only returns x when
 * `time` is in the member set; non-members → null (matches real LWC).
 */
export function makeExactMemberTimeScale(
  memberTimes: number[],
  xForIndex: (i: number) => number = (i) => 40 + i * 20,
): ChartTimeScaleApi {
  const set = new Set(memberTimes);
  const list = memberTimes.slice().sort((a, b) => a - b);
  return {
    timeToCoordinate(time: UTCTimestamp) {
      const t = Number(time);
      if (!set.has(t)) {
        return null;
      }
      const i = list.indexOf(t);
      return xForIndex(i < 0 ? 0 : i);
    },
    setVisibleLogicalRange() {
      /* no-op */
    },
    subscribeVisibleLogicalRangeChange() {
      /* no-op */
    },
    unsubscribeVisibleLogicalRangeChange() {
      /* no-op */
    },
  };
}

export function makeMockChartApi(
  memberTimes: number[],
  hooks?: {
    onUpdate?: () => void;
    onSetData?: () => void;
    onSetMarkers?: (m: unknown[]) => void;
    onPriceLine?: (o: unknown) => void;
    onRemove?: () => void;
  },
): ChartApiLike {
  const series: ChartSeriesApi = {
    setData() {
      hooks?.onSetData?.();
    },
    update() {
      hooks?.onUpdate?.();
    },
    setMarkers(m) {
      hooks?.onSetMarkers?.(m);
    },
    createPriceLine(o) {
      hooks?.onPriceLine?.(o);
      return {};
    },
  };
  const ts = makeExactMemberTimeScale(memberTimes);
  return {
    addCandlestickSeries: () => series,
    addHistogramSeries: () => series,
    addLineSeries: () => series,
    priceScale: () => ({ applyOptions() {} }),
    timeScale: () => ts,
    subscribeCrosshairMove() {},
    setCrosshairPosition() {},
    remove() {
      hooks?.onRemove?.();
    },
  };
}
