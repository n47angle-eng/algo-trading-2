import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
  CrosshairMode,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { toChartTime } from "../../lib/daytrade/chartTime";
import type { DaytradeEquitySeries } from "../../lib/daytrade/types";
import { useTheme } from "../../theme/useTheme";
import { readChartTokenColors } from "../chart/tokenColors";

type Props = {
  data: DaytradeEquitySeries | null;
  loading?: boolean;
};

export function DaytradeEquityChart({ data, loading }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const { theme } = useTheme();

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
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
      height: 160,
    });
    const series = chart.addAreaSeries({
      lineColor: colors.ema18,
      topColor: "rgba(10, 132, 255, 0.28)",
      bottomColor: "rgba(10, 132, 255, 0.02)",
      lineWidth: 2,
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
    if (!series || !chart) return;

    const pts = data?.points ?? [];
    const withTs = pts.filter((p) => p.ts);
    if (!withTs.length) {
      // Synthetic single point if only seed
      if (data && pts.length) {
        const now = Math.floor(Date.now() / 1000);
        series.setData([
          { time: now as Time, value: pts[pts.length - 1].equity },
        ]);
      } else {
        series.setData([]);
      }
      return;
    }

    const byTime = new Map<number, number>();
    for (const p of withTs) {
      const t = toChartTime(p.ts);
      if (t == null) continue;
      byTime.set(t, p.equity);
    }
    const sorted = [...byTime.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([time, value]) => ({ time: time as Time, value }));
    series.setData(sorted);
    chart.timeScale().fitContent();
  }, [data, theme]);

  return (
    <div className="daytrade-chart daytrade-chart--equity">
      <div className="daytrade-chart__head">
        <h3>權益曲線</h3>
        {data ? (
          <span className="muted">
            回撤 {(data.max_drawdown * 100).toFixed(2)}% · 峰值{" "}
            {data.peak_equity.toLocaleString(undefined, {
              maximumFractionDigits: 0,
            })}
            {loading ? " · 更新中…" : ""}
          </span>
        ) : null}
      </div>
      <div ref={containerRef} className="daytrade-chart__canvas daytrade-chart__canvas--sm" />
      <p className="muted daytrade-chart__note">
        由帳本事件 equity 重播 · {data?.formula_version ?? "daytrade-equity-series-v1"}
      </p>
    </div>
  );
}
