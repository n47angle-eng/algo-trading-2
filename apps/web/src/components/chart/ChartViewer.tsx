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
import { useEffect, useRef, useState } from "react";

import { fetchRunChart } from "../../api/client";
import type {
  ChartSeriesResponse,
  ChartTimeframe,
} from "../../api/chartTypes";
import { useTheme } from "../../theme/useTheme";
import {
  readChartTokenColors,
  resolveTokenColor,
} from "./tokenColors";

const TIMEFRAMES: ChartTimeframe[] = ["D", "1H", "5m"];

interface ChartViewerProps {
  runId: string;
}

/**
 * P5 chart viewer — LWC candles + EMA from backend only (D18).
 * Colors from --chart-* tokens via getComputedStyle.
 */
export function ChartViewer({ runId }: ChartViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ema18Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ema50Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ema90Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const { theme } = useTheme();
  const [tf, setTf] = useState<ChartTimeframe>("5m");
  const [data, setData] = useState<ChartSeriesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [crosshair, setCrosshair] = useState<string>("—");

  useEffect(() => {
    let cancelled = false;
    // Soft: keep previous candles on screen while the next TF loads (no flash).
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const payload = await fetchRunChart(runId, tf);
        if (!cancelled) {
          setData(payload);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          // Keep prior series if we already had candles (stale-while-revalidate).
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId, tf]);

  // Create chart once.
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
        textColor: colors.textMuted,
      },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      rightPriceScale: {
        borderColor: colors.hairline,
      },
      timeScale: {
        borderColor: colors.hairline,
        timeVisible: true,
        secondsVisible: false,
      },
    });
    const candles = chart.addCandlestickSeries({
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
    });
    const ema18 = chart.addLineSeries({
      color: colors.ema18,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const ema50 = chart.addLineSeries({
      color: colors.ema50,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const ema90 = chart.addLineSeries({
      color: colors.ema90,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData) {
        setCrosshair("—");
        return;
      }
      const candle = param.seriesData.get(candles) as
        | { open: number; high: number; low: number; close: number }
        | undefined;
      if (!candle) {
        setCrosshair("—");
        return;
      }
      setCrosshair(
        `O ${candle.open.toFixed(2)}  H ${candle.high.toFixed(2)}  L ${candle.low.toFixed(2)}  C ${candle.close.toFixed(2)}`,
      );
    });
    chartRef.current = chart;
    candleRef.current = candles;
    ema18Ref.current = ema18;
    ema50Ref.current = ema50;
    ema90Ref.current = ema90;
    return () => {
      try {
        chart.remove();
      } catch {
        /* jsdom canvas teardown can throw; ignore on unmount */
      }
      chartRef.current = null;
      candleRef.current = null;
      ema18Ref.current = null;
      ema50Ref.current = null;
      ema90Ref.current = null;
    };
  }, []);

  // Apply theme token colors when theme changes.
  useEffect(() => {
    const chart = chartRef.current;
    const candles = candleRef.current;
    if (!chart || !candles) {
      return;
    }
    const colors = readChartTokenColors();
    chart.applyOptions({
      layout: {
        background: { color: colors.background },
        textColor: colors.textMuted,
      },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      rightPriceScale: { borderColor: colors.hairline },
      timeScale: { borderColor: colors.hairline },
    });
    candles.applyOptions({
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
    });
    ema18Ref.current?.applyOptions({ color: colors.ema18 });
    ema50Ref.current?.applyOptions({ color: colors.ema50 });
    ema90Ref.current?.applyOptions({ color: colors.ema90 });
  }, [theme]);

  // Push series data.
  useEffect(() => {
    if (!data || !candleRef.current || !ema18Ref.current) {
      return;
    }
    const colors = readChartTokenColors();
    const candleData = data.candles.map((item) => ({
      time: item.time as Time,
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    }));
    candleRef.current.setData(candleData);
    ema18Ref.current.setData(
      data.ema18.map((item) => ({ time: item.time as Time, value: item.value })),
    );
    ema50Ref.current?.setData(
      data.ema50.map((item) => ({ time: item.time as Time, value: item.value })),
    );
    ema90Ref.current?.setData(
      data.ema90.map((item) => ({ time: item.time as Time, value: item.value })),
    );

    const markers: SeriesMarker<Time>[] = data.markers.map((marker) => ({
      time: marker.time as Time,
      position: marker.position,
      shape: marker.shape as SeriesMarker<Time>["shape"],
      color: resolveTokenColor(marker.token, colors),
      text: marker.text,
    }));
    candleRef.current.setMarkers(markers);

    // Clear previous price lines.
    for (const line of priceLinesRef.current) {
      candleRef.current.removePriceLine(line);
    }
    priceLinesRef.current = data.levels.map((level) =>
      candleRef.current!.createPriceLine({
        price: level.price,
        color: resolveTokenColor(level.token, colors),
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: level.kind,
      }),
    );

    chartRef.current?.timeScale().fitContent();
  }, [data, theme]);

  return (
    <section className="panel chart-panel" role="region" aria-label="圖表檢視器">
      <div className="chart-panel__head">
        <h2 className="panel__title">主圖 · {tf}（hover 十字線）</h2>
        <div className="batch-tabs" role="tablist" aria-label="Timeframe">
          {TIMEFRAMES.map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={tf === item}
              className={
                tf === item
                  ? "batch-tabs__btn batch-tabs__btn--on"
                  : "batch-tabs__btn"
              }
              onClick={() => {
                setTf(item);
              }}
            >
              {item}
            </button>
          ))}
        </div>
      </div>
      {loading ? <p className="state-msg">載入圖表序列…</p> : null}
      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="chart-panel__canvas" ref={containerRef} />
      <div className="chart-panel__crosshair" aria-live="polite">
        {crosshair}
      </div>
      <div className="chart-legend">
        <span>
          <i className="chart-legend__swatch chart-legend__swatch--ema18" />
          EMA 18
        </span>
        <span>
          <i className="chart-legend__swatch chart-legend__swatch--ema50" />
          EMA 50
        </span>
        <span>
          <i className="chart-legend__swatch chart-legend__swatch--ema90" />
          EMA 90
        </span>
        <span className="metric-card__value--pos">▲ 入市</span>
        <span className="metric-card__value--muted">▽ 離場</span>
        <span className="metric-card__value--muted">— 止損／目標</span>
      </div>
      {data ? (
        <p className="state-msg chart-viewer__meta">
          <span
            className={
              data.compute_provenance?.effective_backend === "rust"
                ? "chart-backend-badge chart-backend-badge--rust"
                : "chart-backend-badge chart-backend-badge--python"
            }
            data-testid="chart-backend-badge"
            title={
              data.compute_provenance?.fallback_reason
                ? String(data.compute_provenance.fallback_reason)
                : data.source
            }
          >
            {data.compute_provenance?.effective_backend === "rust"
              ? "Rust"
              : "Python"}
            {data.compute_provenance?.fallback_reason ? " · fallback" : ""}
          </span>
          <span>
            source: {data.source} · candles: {data.candles.length} ·{" "}
            {data.contract_id}
          </span>
        </p>
      ) : null}
    </section>
  );
}
