import {
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  CrosshairMode,
  LineStyle,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { toChartTime } from "../../lib/daytrade/chartTime";
import type {
  PaperLessonBar,
  PaperLessonLevels,
  PaperLessonMarker,
} from "../../lib/paper/tradeLessonTypes";
import { useTheme } from "../../theme/useTheme";
import { readChartTokenColors } from "../chart/tokenColors";

type Props = {
  bars: PaperLessonBar[];
  markers: PaperLessonMarker[];
  levels: PaperLessonLevels;
  /** When set, only show bars up to this index (inclusive) for replay. */
  replayIndex: number | null;
  height?: number;
  contractLabel?: string;
};

/**
 * Paper trader candlestick chart with entry / stop / target / last lines.
 * Reuses token colors so it matches daytrade + results charts.
 */
export function PaperTradeChart({
  bars,
  markers,
  levels,
  replayIndex,
  height = 280,
  contractLabel,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const { theme } = useTheme();

  useEffect(() => {
    const el = containerRef.current;
    if (!el) {
      return;
    }
    const colors = readChartTokenColors();
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: colors.background },
        textColor: colors.text,
      },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: colors.hairline },
      timeScale: {
        borderColor: colors.hairline,
        timeVisible: true,
        secondsVisible: false,
      },
    });
    const series = chart.addCandlestickSeries({
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [theme]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) {
      return;
    }
    for (const line of linesRef.current) {
      series.removePriceLine(line);
    }
    linesRef.current = [];

    const end =
      replayIndex == null
        ? bars.length
        : Math.min(bars.length, Math.max(0, replayIndex + 1));
    const visible = bars.slice(0, end);

    if (visible.length === 0) {
      series.setData([]);
      series.setMarkers([]);
      return;
    }

    const candles = visible
      .map((b) => {
        const t = toChartTime(b.event_at);
        if (t == null) return null;
        return {
          time: t as Time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        };
      })
      .filter((x): x is NonNullable<typeof x> => x != null);

    const byTime = new Map<number, (typeof candles)[0]>();
    for (const c of candles) {
      byTime.set(c.time as number, c);
    }
    const sorted = [...byTime.values()].sort(
      (a, b) => (a.time as number) - (b.time as number),
    );
    series.setData(sorted);

    const lastTs = visible[visible.length - 1]?.event_at;
    const lastMs = lastTs ? Date.parse(lastTs) : Number.POSITIVE_INFINITY;
    const colors = readChartTokenColors();
    const chartMarkers: SeriesMarker<Time>[] = [];

    for (const m of markers) {
      const t = toChartTime(m.ts ?? undefined);
      if (t == null) continue;
      const ms = m.ts ? Date.parse(m.ts) : NaN;
      if (Number.isFinite(ms) && ms > lastMs) continue;

      let position: "aboveBar" | "belowBar" = "belowBar";
      let shape: "arrowUp" | "arrowDown" | "circle" | "square" = "circle";
      let color = colors.markerEntry;
      let text = m.label;

      if (m.kind === "ENTRY" || m.kind === "OPEN") {
        const long = m.side !== "short";
        position = long ? "belowBar" : "aboveBar";
        shape = long ? "arrowUp" : "arrowDown";
        color = colors.markerEntry;
        text = m.label || (long ? "買入" : "沽空");
      } else if (m.kind === "EXIT") {
        position = "aboveBar";
        shape = "square";
        color = colors.markerExit;
        text = m.label || "平倉";
      } else if (m.kind === "TARGET") {
        position = "aboveBar";
        shape = "circle";
        color = colors.positive;
        text = m.label || "止賺";
      } else if (m.kind === "STOP") {
        position = "belowBar";
        shape = "circle";
        color = colors.negative;
        text = m.label || "止蝕";
      }
      chartMarkers.push({
        time: t as Time,
        position,
        color,
        shape,
        text,
      });
    }
    chartMarkers.sort((a, b) => (a.time as number) - (b.time as number));
    // LWC: one marker set per time — keep last of each second group.
    const dedup = new Map<number, SeriesMarker<Time>>();
    for (const mk of chartMarkers) {
      dedup.set(mk.time as number, mk);
    }
    series.setMarkers([...dedup.values()].sort(
      (a, b) => (a.time as number) - (b.time as number),
    ));

    const addLine = (
      price: number | null | undefined,
      title: string,
      color: string,
      style: LineStyle = LineStyle.Dashed,
    ) => {
      if (price == null || !Number.isFinite(price)) return;
      const line = series.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: style,
        axisLabelVisible: true,
        title,
      });
      linesRef.current.push(line);
    };

    addLine(levels.entry, "買入", colors.markerEntry, LineStyle.Solid);
    addLine(levels.stop, "止蝕", colors.negative, LineStyle.Dashed);
    addLine(levels.target, "止賺", colors.positive, LineStyle.Dashed);
    const replayLast =
      visible.length > 0 ? visible[visible.length - 1].close : levels.last;
    addLine(replayLast, "現價", colors.warning ?? colors.text, LineStyle.Dotted);

    chart.timeScale().fitContent();
  }, [bars, markers, levels, replayIndex, theme]);

  return (
    <div className="paper-lesson-chart">
      <div className="paper-lesson-chart__head">
        <h4 className="paper-step" style={{ marginTop: 0 }}>
          {contractLabel ? `${contractLabel} 走勢` : "走勢圖"}
        </h4>
        <span className="paper-hint">
          買入 · 止蝕 · 止賺 · 現價
        </span>
      </div>
      <div
        ref={containerRef}
        className="paper-lesson-chart__canvas"
        style={{ height }}
      />
      <ul className="paper-lesson-chart__legend" aria-label="圖例">
        <li>
          <span className="paper-lesson-dot paper-lesson-dot--entry" />
          買入／開倉
        </li>
        <li>
          <span className="paper-lesson-dot paper-lesson-dot--stop" />
          止蝕
        </li>
        <li>
          <span className="paper-lesson-dot paper-lesson-dot--target" />
          止賺
        </li>
        <li>
          <span className="paper-lesson-dot paper-lesson-dot--last" />
          現價
        </li>
      </ul>
    </div>
  );
}
