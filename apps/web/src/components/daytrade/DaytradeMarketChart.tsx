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
import type { DaytradeMarketChart as ChartPayload } from "../../lib/daytrade/types";
import { useTheme } from "../../theme/useTheme";
import { readChartTokenColors } from "../chart/tokenColors";

type Props = {
  data: ChartPayload | null;
  loading?: boolean;
  error?: string | null;
};

/**
 * Intraday OHLC for a daytrade symbol + entry/exit markers + OR/stop/target lines.
 */
export function DaytradeMarketChart({ data, loading, error }: Props) {
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
      timeScale: { borderColor: colors.hairline, timeVisible: true, secondsVisible: false },
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

    if (!data?.bars?.length) {
      series.setData([]);
      series.setMarkers([]);
      return;
    }

    const candles = data.bars
      .map((b) => {
        const t = toChartTime(b.ts);
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

    // Deduplicate / sort by time (LWC requires ascending unique)
    const byTime = new Map<number, (typeof candles)[0]>();
    for (const c of candles) {
      byTime.set(c.time as number, c);
    }
    const sorted = [...byTime.values()].sort(
      (a, b) => (a.time as number) - (b.time as number),
    );
    series.setData(sorted);

    const colors = readChartTokenColors();
    const markers: SeriesMarker<Time>[] = [];
    for (const m of data.markers ?? []) {
      const t = toChartTime(m.ts ?? undefined);
      if (t == null) continue;
      const kind = m.kind;
      let position: "aboveBar" | "belowBar" = "aboveBar";
      let shape: "arrowUp" | "arrowDown" | "circle" | "square" = "circle";
      let color = colors.markerExit;
      let text = kind;
      if (kind === "OPEN") {
        const long = m.side === "long";
        position = long ? "belowBar" : "aboveBar";
        shape = long ? "arrowUp" : "arrowDown";
        color = colors.markerEntry;
        text = long ? "開多" : "開空";
      } else if (kind === "TARGET") {
        position = "aboveBar";
        shape = "circle";
        color = colors.positive;
        text = "目標";
      } else if (kind === "STOP") {
        position = "belowBar";
        shape = "circle";
        color = colors.negative;
        text = "止蝕";
      } else if (kind === "FORCE_FLAT") {
        shape = "square";
        color = colors.warning;
        text = "強平";
      }
      markers.push({
        time: t as Time,
        position,
        color,
        shape,
        text,
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    series.setMarkers(markers);

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

    addLine(data.levels?.or_high, "OR高", colors.ema18, LineStyle.Dotted);
    addLine(data.levels?.or_low, "OR低", colors.ema18, LineStyle.Dotted);
    addLine(data.levels?.entry, "入場", colors.markerEntry, LineStyle.Solid);
    addLine(data.levels?.stop, "止蝕", colors.negative, LineStyle.Dashed);
    addLine(data.levels?.target, "目標", colors.positive, LineStyle.Dashed);

    chart.timeScale().fitContent();
  }, [data, theme]);

  return (
    <div className="daytrade-chart">
      <div className="daytrade-chart__head">
        <h3>
          {data?.symbol ?? "—"} 走勢
          {data?.trading_date ? (
            <span className="muted"> · {data.trading_date}</span>
          ) : null}
        </h3>
        {loading ? <span className="muted">更新中…</span> : null}
      </div>
      {error ? (
        <p className="daytrade-page__error" role="alert">
          {error}
        </p>
      ) : null}
      {!loading && data && data.bars.length === 0 ? (
        <p className="muted">未有 completed 1m bar — 等 runner 推進或 ingest 數據。</p>
      ) : null}
      <div ref={containerRef} className="daytrade-chart__canvas" />
      {data?.reference ? (
        <ul className="daytrade-chart__refs" aria-label="重要數據參考">
          <li>
            <span>最新</span>
            <strong>{fmtNum(data.reference.last_price)}</strong>
          </li>
          <li>
            <span>今開</span>
            <strong>{fmtNum(data.reference.session_open)}</strong>
          </li>
          <li>
            <span>區間</span>
            <strong>
              {fmtNum(data.reference.session_low)} –{" "}
              {fmtNum(data.reference.session_high)}
            </strong>
          </li>
          <li>
            <span>OR 寬</span>
            <strong>
              {fmtNum(data.reference.or_width)}
              {data.reference.or_width_dollars != null
                ? ` ($${fmtNum(data.reference.or_width_dollars)})`
                : ""}
            </strong>
          </li>
          <li>
            <span>1R</span>
            <strong>
              {data.reference.one_r_dollars != null
                ? `$${fmtNum(data.reference.one_r_dollars)}`
                : "—"}
            </strong>
          </li>
          <li>
            <span>浮盈虧*</span>
            <strong
              className={
                (data.reference.unrealized_hint ?? 0) >= 0 ? "is-up" : "is-down"
              }
            >
              {data.reference.unrealized_hint != null
                ? fmtNum(data.reference.unrealized_hint)
                : "—"}
            </strong>
          </li>
        </ul>
      ) : null}
      <p className="muted daytrade-chart__note">
        *浮盈虧係用最新 close × 點值估算，正式 P&amp;L 以帳本事件為準 ·{" "}
        {data?.formula_version ?? "daytrade-market-chart-v1"}
      </p>
    </div>
  );
}

function fmtNum(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
