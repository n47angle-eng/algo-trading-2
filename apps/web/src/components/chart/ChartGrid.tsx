/**
 * Reusable 2×2 chart grid (P2/P5/P6 union).
 * Presentational only — no route knowledge, no fetch.
 * D17–D36: colors, true verticals with pane anchors, sync, jump, theme.
 */
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type UTCTimestamp,
} from "lightweight-charts";

import type { ChartPaneData } from "../../lib/results/types";
import { TF_DURATION_SEC } from "../../lib/results/timeIdentity";
import { applyTheme, type ThemeId } from "../../theme/theme";
import { useTheme } from "../../theme/useTheme";
import type { ChartApiLike, ChartCreateFn, ChartSeriesApi } from "./chartFactory";
import {
  applyCrosshairToTargets,
  computeVisibleLogicalRange,
  durationForTf,
  findContainingBarAnchor,
  priceAtBarIndex,
} from "./chartGridMath";
import {
  readChartTokenColors,
  resolveTokenColor,
  type ChartTokenColors,
} from "./tokenColors";

export type ChartGridTimeframe = "D" | "1H" | "30m" | "5m";

/** Payload actually handed to the chart renderer (D32/D36 contract). */
export interface PaneRenderReport {
  tf: ChartGridTimeframe;
  theme: ThemeId;
  colors: Pick<
    ChartTokenColors,
    "background" | "text" | "grid" | "up" | "down"
  >;
  candleCount: number;
  volumeCount: number;
  indicatorCount: number;
  markers: Array<{ time: number; text: string; token: string }>;
  /** Event times + labels (canonical). */
  verticalAnnotations: Array<{ time: number; label: string }>;
  /** Per-pane candle anchors used for positioning (exact members). */
  verticalAnchors: Array<{
    eventTime: number;
    anchorTime: number;
    label: string;
  }>;
  rejectMarkers: Array<{ time: number; text: string }>;
  trendDayTimes: number[];
  levels: Array<{ price: number; label?: string; token: string }>;
  usedSeriesUpdate: boolean;
}

export interface ChartGridProps {
  panes: ChartPaneData[];
  focusTimeUtc?: string | null;
  highlightLabel?: string | null;
  className?: string;
  onReady?: (api: ChartGridHandle) => void;
  /** Test seam: injectable chart factory (typed, no IChartApi cast). */
  createChartImpl?: ChartCreateFn;
  /** Test seam: observe renderer payloads (D32/D36). */
  onPaneRender?: (report: PaneRenderReport) => void;
}

export interface ChartGridHandle {
  appendBar: (
    tf: ChartGridTimeframe,
    bar: ChartPaneData["candles"][number],
  ) => void;
  replaceLastBar: (
    tf: ChartGridTimeframe,
    bar: ChartPaneData["candles"][number],
  ) => void;
  setOpenPosition: (payload: {
    entryPrice: number;
    stop?: number;
    target?: number;
    floatingPnl?: string;
  } | null) => void;
}

const ORDER: ChartGridTimeframe[] = ["D", "1H", "30m", "5m"];

const defaultCreate: ChartCreateFn = (host, options) =>
  createChart(host, options as Parameters<typeof createChart>[1]) as unknown as ChartApiLike;

interface PaneEngine {
  tf: ChartGridTimeframe;
  chart: ChartApiLike;
  series: ChartSeriesApi;
  candleTimes: number[];
  candles: ChartPaneData["candles"];
  durationSec: number;
  cleanupExtras: () => void;
}

export function ChartGrid({
  panes,
  focusTimeUtc,
  highlightLabel,
  className,
  onReady,
  createChartImpl = defaultCreate,
  onPaneRender,
}: ChartGridProps) {
  const { theme } = useTheme();
  const gid = useId();
  const enginesRef = useRef<PaneEngine[]>([]);
  const hostRefs = useRef<
    Partial<Record<ChartGridTimeframe, HTMLDivElement | null>>
  >({});
  const [openPos, setOpenPos] = useState<{
    entryPrice: number;
    stop?: number;
    target?: number;
    floatingPnl?: string;
  } | null>(null);
  const [paneErrors, setPaneErrors] = useState<
    Partial<Record<ChartGridTimeframe, string>>
  >({});
  const [outOfRange, setOutOfRange] = useState<
    Partial<Record<ChartGridTimeframe, boolean>>
  >({});
  const syncing = useRef(false);
  const createCountRef = useRef(0);
  const removeCountRef = useRef(0);

  const byTf = useMemo(() => {
    const m = new Map<string, ChartPaneData>();
    for (const p of panes) {
      m.set(p.timeframe, p);
    }
    return m;
  }, [panes]);

  useEffect(() => {
    applyTheme(theme);
    const colors = readChartTokenColors();
    const engines: PaneEngine[] = [];
    const errors: Partial<Record<ChartGridTimeframe, string>> = {};

    for (const tf of ORDER) {
      const host = hostRefs.current[tf];
      const pane = byTf.get(tf);
      if (!host) {
        continue;
      }
      host.innerHTML = "";
      if (!pane?.available) {
        continue;
      }
      const isJsdom =
        typeof navigator !== "undefined" &&
        /jsdom/i.test(navigator.userAgent);
      let hasCanvas = !isJsdom;
      if (hasCanvas) {
        try {
          hasCanvas = Boolean(
            document.createElement("canvas").getContext("2d"),
          );
        } catch {
          hasCanvas = false;
        }
      }
      const usingMock = createChartImpl !== defaultCreate;
      if (!hasCanvas && !usingMock) {
        host.setAttribute("data-chart-skipped", "true");
        onPaneRender?.(buildReport(tf, theme, colors, pane, [], false));
        continue;
      }
      try {
        const engine = mountPane(
          host,
          pane,
          colors,
          theme,
          createChartImpl,
          onPaneRender,
          (param, self) => {
            if (syncing.current || param.time == null) {
              return;
            }
            syncing.current = true;
            // Canonical event instant from source pane time (unix sec)
            const eventTimeSec =
              typeof param.time === "number"
                ? param.time
                : Number(param.time);
            const targets = enginesRef.current
              .filter((e) => e.chart !== self.chart)
              .map((e) => ({
                chart: e.chart,
                series: e.series,
                candleTimes: e.candleTimes,
                candles: e.candles,
                durationSec: e.durationSec,
              }));
            applyCrosshairToTargets(targets, eventTimeSec, self.series);
            syncing.current = false;
          },
        );
        createCountRef.current += 1;
        engines.push(engine);
      } catch (err) {
        errors[tf] =
          err instanceof Error
            ? `圖表繪製失敗：${err.message}`
            : "圖表繪製失敗";
      }
    }

    enginesRef.current = engines;
    setPaneErrors(errors);

    const handle: ChartGridHandle = {
      appendBar(tf, bar) {
        const eng = enginesRef.current.find((e) => e.tf === tf);
        eng?.series.update({
          time: bar.time as UTCTimestamp,
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        });
        if (eng && !eng.candleTimes.includes(bar.time)) {
          eng.candleTimes.push(bar.time);
          eng.candles.push(bar);
        }
      },
      replaceLastBar(tf, bar) {
        const eng = enginesRef.current.find((e) => e.tf === tf);
        eng?.series.update({
          time: bar.time as UTCTimestamp,
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        });
        if (eng && eng.candles.length > 0) {
          eng.candles[eng.candles.length - 1] = bar;
          eng.candleTimes[eng.candleTimes.length - 1] = bar.time;
        }
      },
      setOpenPosition(payload) {
        setOpenPos(payload);
      },
    };
    onReady?.(handle);

    return () => {
      for (const e of engines) {
        e.cleanupExtras();
        e.chart.remove();
        removeCountRef.current += 1;
      }
      enginesRef.current = [];
    };
  }, [byTf, theme, onReady, gid, createChartImpl, onPaneRender]);

  // External jump — per-pane anchor mapping (D35)
  useEffect(() => {
    if (!focusTimeUtc) {
      setOutOfRange({});
      return;
    }
    const t = Math.floor(new Date(focusTimeUtc).getTime() / 1000);
    const oor: Partial<Record<ChartGridTimeframe, boolean>> = {};
    for (const eng of enginesRef.current) {
      const range = computeVisibleLogicalRange(
        eng.candleTimes,
        t,
        eng.durationSec,
      );
      if (!range) {
        oor[eng.tf] = true;
        continue;
      }
      oor[eng.tf] = false;
      try {
        eng.chart.timeScale().setVisibleLogicalRange({
          from: range.from,
          to: range.to,
        });
        const price =
          priceAtBarIndex(eng.candles, range.barIndex) ??
          eng.candles[range.barIndex]?.close ??
          1;
        eng.chart.setCrosshairPosition(
          price,
          range.barTime as UTCTimestamp,
          eng.series,
        );
      } catch {
        oor[eng.tf] = true;
      }
    }
    setOutOfRange(oor);
  }, [focusTimeUtc, highlightLabel, byTf, theme]);

  return (
    <div
      className={className ?? "chart-grid"}
      data-testid="chart-grid"
      data-theme={theme}
      data-chart-creates={createCountRef.current}
      data-chart-removes={removeCountRef.current}
    >
      {ORDER.map((tf) => {
        const pane = byTf.get(tf);
        const err = paneErrors[tf];
        return (
          <div
            key={tf}
            className="chart-grid__cell"
            data-testid={`chart-pane-${tf}`}
            data-tf={tf}
          >
            <div className="chart-grid__label">
              <strong>{tf}</strong>
              <span data-testid={`chart-role-${tf}`}>
                {pane?.roleLabel ?? "—"}
              </span>
              {pane?.computeBackend ? (
                <span
                  className={
                    pane.computeBackend.effectiveBackend === "rust"
                      ? "chart-backend-badge chart-backend-badge--rust"
                      : "chart-backend-badge chart-backend-badge--python"
                  }
                  data-testid={`chart-backend-${tf}`}
                  title={
                    pane.computeBackend.fallbackReason
                      ? `fallback: ${pane.computeBackend.fallbackReason}`
                      : pane.computeBackend.source ?? ""
                  }
                >
                  {pane.computeBackend.effectiveBackend === "rust"
                    ? "Rust"
                    : "Python"}
                  {pane.computeBackend.fallbackReason
                    ? " · fallback"
                    : ""}
                </span>
              ) : null}
              {highlightLabel ? (
                <span className="chart-grid__hl" data-testid="chart-highlight">
                  {highlightLabel}
                </span>
              ) : null}
            </div>
            {!pane?.available ? (
              <p className="state-msg" data-testid={`chart-missing-${tf}`}>
                {pane?.unavailableReason ?? "資料暫未提供"}
              </p>
            ) : err ? (
              <p
                className="state-msg state-msg--error"
                data-testid={`chart-error-${tf}`}
              >
                {err}
              </p>
            ) : (
              <div
                className="chart-grid__host"
                ref={(el) => {
                  (
                    hostRefs as MutableRefObject<
                      Partial<
                        Record<ChartGridTimeframe, HTMLDivElement | null>
                      >
                    >
                  ).current[tf] = el;
                }}
              />
            )}
            {outOfRange[tf] ? (
              <p className="state-msg" data-testid={`chart-oor-${tf}`}>
                呢個時刻超出圖表資料範圍
              </p>
            ) : null}
            {openPos && tf === "5m" ? (
              <p className="utc-hint" data-testid="open-position-hud">
                持倉 {openPos.entryPrice}
                {openPos.stop != null ? ` · 止蝕 ${openPos.stop}` : ""}
                {openPos.target != null ? ` · 目標 ${openPos.target}` : ""}
                {openPos.floatingPnl != null
                  ? ` · 浮動 ${openPos.floatingPnl}`
                  : ""}
              </p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function verticalAnnotationsOf(
  pane: ChartPaneData,
): Array<{ time: number; label: string }> {
  return pane.verticalAnnotations ?? pane.verticalLines ?? [];
}

function buildReport(
  tf: ChartGridTimeframe,
  theme: ThemeId,
  colors: ChartTokenColors,
  pane: ChartPaneData,
  verticalAnchors: PaneRenderReport["verticalAnchors"],
  usedSeriesUpdate: boolean,
): PaneRenderReport {
  return {
    tf,
    theme,
    colors: {
      background: colors.background,
      text: colors.text,
      grid: colors.grid,
      up: colors.up,
      down: colors.down,
    },
    candleCount: pane.candles.length,
    volumeCount: pane.volume?.length ?? 0,
    indicatorCount: pane.indicators?.length ?? 0,
    markers: (pane.markers ?? []).map((m) => ({
      time: m.time,
      text: m.text,
      token: m.token,
    })),
    verticalAnnotations: verticalAnnotationsOf(pane),
    verticalAnchors,
    rejectMarkers: pane.rejectMarkers ?? [],
    trendDayTimes: pane.trendDayTimes ?? [],
    levels: (pane.levels ?? []).map((l) => ({
      price: l.price,
      label: l.label,
      token: l.token,
    })),
    usedSeriesUpdate,
  };
}

function mountPane(
  host: HTMLDivElement,
  pane: ChartPaneData,
  colors: ChartTokenColors,
  theme: ThemeId,
  create: ChartCreateFn,
  onPaneRender: ChartGridProps["onPaneRender"],
  onCrosshair: (
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    param: any,
    self: PaneEngine,
  ) => void,
): PaneEngine {
  host.style.position = "relative";
  const tf = pane.timeframe as ChartGridTimeframe;
  const durationSec = durationForTf(tf);
  const candleTimes = pane.candles.map((c) => c.time);

  const chart = create(host, {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: colors.background },
      textColor: colors.text,
    },
    grid: {
      vertLines: { color: colors.grid },
      horzLines: { color: colors.grid },
    },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: colors.hairline },
    timeScale: { borderColor: colors.hairline },
  });

  const series = chart.addCandlestickSeries({
    upColor: colors.up,
    downColor: colors.down,
    borderUpColor: colors.up,
    borderDownColor: colors.down,
    wickUpColor: colors.up,
    wickDownColor: colors.down,
  });

  const trendSet = new Set(pane.trendDayTimes ?? []);
  series.setData(
    pane.candles.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    })),
  );

  if (trendSet.size > 0 && pane.candles.length > 0) {
    const lo = Math.min(...pane.candles.map((c) => c.low));
    const hi = Math.max(...pane.candles.map((c) => c.high));
    const span = Math.max(hi - lo, 1);
    const trend = chart.addHistogramSeries({
      priceScaleId: "trend",
      base: lo - span * 0.05,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("trend").applyOptions({
      scaleMargins: { top: 0, bottom: 0 },
      visible: false,
    });
    trend.setData(
      pane.candles.map((c) => ({
        time: c.time as UTCTimestamp,
        value: trendSet.has(c.time) ? hi + span * 0.05 : lo - span * 0.05,
        color: trendSet.has(c.time) ? colors.trendShade : "rgba(0,0,0,0)",
      })),
    );
  }

  if (pane.volume?.length) {
    const vol = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
    });
    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });
    vol.setData(
      pane.volume.map((v) => ({
        time: v.time as UTCTimestamp,
        value: v.value,
        color: v.color ?? colors.grid,
      })),
    );
  }

  for (const ind of pane.indicators ?? []) {
    const line = chart.addLineSeries({
      color: resolveTokenColor(ind.token, colors),
      lineWidth: 2,
    });
    line.setData(
      ind.points.map((p) => ({
        time: p.time as UTCTimestamp,
        value: p.value,
      })),
    );
  }

  const markers = [
    ...(pane.markers ?? []),
    ...(pane.rejectMarkers ?? []).map((r) => ({
      time: r.time,
      position: "aboveBar" as const,
      shape: "circle",
      token: "chart-reject",
      text: r.text,
    })),
  ];
  if (markers.length) {
    series.setMarkers(
      markers
        .slice()
        .sort((a, b) => a.time - b.time)
        .map((m) => ({
          time: m.time as UTCTimestamp,
          position: m.position,
          shape: m.shape as "arrowUp" | "arrowDown" | "circle" | "square",
          color: resolveTokenColor(m.token, colors),
          text: m.text,
        })),
    );
  }

  for (const lvl of pane.levels ?? []) {
    series.createPriceLine({
      price: lvl.price,
      color: resolveTokenColor(lvl.token, colors),
      lineWidth: 1,
      title: lvl.label ?? "",
      axisLabelVisible: true,
    });
  }

  const vAnns = verticalAnnotationsOf(pane);
  const overlay = document.createElement("div");
  overlay.className = "chart-vertical-overlay";
  overlay.setAttribute("data-testid", `chart-verticals-${pane.timeframe}`);
  overlay.style.cssText =
    "position:absolute;inset:0;pointer-events:none;z-index:3;overflow:hidden;";
  host.appendChild(overlay);

  const oorNote = document.createElement("div");
  oorNote.setAttribute("data-testid", `chart-vline-oor-${pane.timeframe}`);
  oorNote.className = "state-msg";
  oorNote.style.display = "none";
  host.appendChild(oorNote);

  const resolvedAnchors: PaneRenderReport["verticalAnchors"] = [];

  const paintVerticals = () => {
    overlay.innerHTML = "";
    resolvedAnchors.length = 0;
    let oorCount = 0;
    for (const v of vAnns) {
      const anchor = findContainingBarAnchor(
        candleTimes,
        v.time,
        durationSec,
      );
      if (!anchor) {
        oorCount += 1;
        continue;
      }
      let x: number | null = null;
      try {
        // D34: pass EXACT member candle time, never raw event time
        x = chart.timeScale().timeToCoordinate(anchor.barTime as UTCTimestamp);
      } catch {
        x = null;
      }
      if (x == null) {
        // Scale not ready or non-member — do NOT draw left:0 fake line (D34)
        oorCount += 1;
        continue;
      }
      resolvedAnchors.push({
        eventTime: v.time,
        anchorTime: anchor.barTime,
        label: v.label,
      });
      const el = document.createElement("div");
      el.setAttribute("data-vertical-annotation", v.label);
      el.setAttribute("data-vertical-event-time", String(v.time));
      el.setAttribute("data-vertical-anchor-time", String(anchor.barTime));
      el.className = "chart-vline";
      el.style.cssText =
        `position:absolute;left:${x}px;top:0;bottom:18px;width:0;` +
        `border-left:1px dashed ${colors.verticalLine};`;
      const lab = document.createElement("span");
      lab.textContent = v.label;
      lab.style.cssText = `position:absolute;left:3px;top:2px;font-size:10px;color:${colors.verticalLine};white-space:nowrap;`;
      el.appendChild(lab);
      overlay.appendChild(el);
    }
    if (oorCount > 0 && resolvedAnchors.length === 0 && vAnns.length > 0) {
      oorNote.style.display = "";
      oorNote.textContent = "呢個時刻超出圖表資料範圍";
    } else if (oorCount > 0) {
      oorNote.style.display = "";
      oorNote.textContent = `有 ${oorCount} 條垂直標註超出本格資料範圍`;
    } else {
      oorNote.style.display = "none";
      oorNote.textContent = "";
    }
  };
  paintVerticals();
  const onRange = () => {
    paintVerticals();
  };
  try {
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
  } catch {
    /* mock may lack subscribe */
  }

  onPaneRender?.(
    buildReport(tf, theme, colors, pane, resolvedAnchors.slice(), false),
  );

  const engine: PaneEngine = {
    tf,
    chart,
    series,
    candleTimes: candleTimes.slice(),
    candles: pane.candles.slice(),
    durationSec,
    cleanupExtras: () => {
      try {
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
      } catch {
        /* ignore */
      }
      overlay.remove();
      oorNote.remove();
    },
  };

  chart.subscribeCrosshairMove((param) => {
    onCrosshair(param, engine);
  });

  void TF_DURATION_SEC;
  return engine;
}
