import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ChartPaneData } from "../../lib/results/types";
import {
  hktLocalToUnixSec,
  TF_DURATION_SEC,
  utcDayBucket,
} from "../../lib/results/timeIdentity";
import { ThemeSwitch } from "../../layout/ThemeSwitch";
import { ThemeProvider } from "../../theme/ThemeProvider";
import {
  makeExactMemberTimeScale,
  makeMockChartApi,
  type ChartCreateFn,
} from "./chartFactory";
import { ChartGrid, type PaneRenderReport } from "./ChartGrid";
import {
  applyCrosshairToTargets,
  computeVisibleLogicalRange,
  findContainingBarAnchor,
} from "./chartGridMath";
import {
  isClassicChartColor,
  normalizeCssColorForChart,
} from "./tokenColors";

const T1 = hktLocalToUnixSec(2026, 5, 8, 10, 30);
const T2 = hktLocalToUnixSec(2026, 5, 9, 9, 45);
const T3 = hktLocalToUnixSec(2026, 5, 12, 13, 10);

function samplePanes(): ChartPaneData[] {
  const day = utcDayBucket(T1);
  const mk = (
    tf: ChartPaneData["timeframe"],
    step: number,
    role: string,
  ): ChartPaneData => {
    const start = tf === "D" ? day - 5 * 86400 : T1 - 20 * step;
    // Ensure all three trade anchors fall inside range
    const endNeed = T3 + 2 * step;
    const n = Math.max(40, Math.ceil((endNeed - start) / step) + 5);
    const candles = Array.from({ length: n }, (_, i) => ({
      time: start + i * step,
      open: 100,
      high: 110,
      low: 90,
      close: 105,
    }));
    const verticals =
      tf === "5m"
        ? []
        : [
            { time: T1, label: `#1 · 2026-05-08 10:30` },
            { time: T2, label: `#2 · 2026-05-09 09:45` },
            { time: T3, label: `#3 · 2026-05-12 13:10` },
          ];
    return {
      timeframe: tf,
      roleLabel: role,
      available: true,
      candles,
      volume: candles.map((c) => ({ time: c.time, value: 10 })),
      indicators: [
        {
          id: "ema",
          token: "chart-ema-18",
          points: candles.map((c) => ({ time: c.time, value: c.close - 1 })),
        },
      ],
      verticalLines: verticals,
      verticalAnnotations: verticals,
      markers:
        tf === "5m"
          ? [
              {
                time: T1,
                position: "belowBar",
                shape: "arrowUp",
                token: "chart-marker-entry",
                text: `#1 · 2026-05-08 10:30 · 買 18040`,
              },
            ]
          : undefined,
      levels:
        tf === "5m"
          ? [
              { price: 18020, token: "color-negative", label: "#1 止損" },
              { price: 18090, token: "color-positive", label: "#1 目標" },
            ]
          : undefined,
      trendDayTimes: tf === "D" ? [day, utcDayBucket(T2), utcDayBucket(T3)] : undefined,
    };
  };
  return [
    mk("D", 86400, "大框架"),
    mk("1H", 3600, "中框架"),
    mk("30m", 1800, "輔助"),
    mk("5m", 300, "入市"),
  ];
}

function factoryForPanes(
  panes: ChartPaneData[],
  hooks?: {
    onUpdate?: () => void;
    onSetData?: () => void;
    onSetMarkers?: (m: unknown[]) => void;
    onRemove?: () => void;
    crosshairCalls?: Array<{ price: number; time: unknown; series: unknown }>;
  },
): ChartCreateFn {
  let paneIdx = 0;
  return () => {
    const pane = panes[paneIdx % panes.length];
    paneIdx += 1;
    const times = pane.candles.map((c) => c.time);
    const api = makeMockChartApi(times, {
      onUpdate: hooks?.onUpdate,
      onSetData: hooks?.onSetData,
      onSetMarkers: hooks?.onSetMarkers,
      onRemove: hooks?.onRemove,
    });
    if (hooks?.crosshairCalls) {
      api.setCrosshairPosition = (price, time, series) => {
        hooks.crosshairCalls!.push({ price, time, series });
      };
    }
    return api;
  };
}

describe("D34/D35 helpers", () => {
  it("computeVisibleLogicalRange uses containing bar", () => {
    const times = Array.from({ length: 100 }, (_, i) => 1_000 + i * 60);
    const target = times[50] + 30;
    const range = computeVisibleLogicalRange(
      times,
      target,
      60,
      10,
    );
    expect(range).not.toBeNull();
    expect(range!.barIndex).toBe(50);
    expect(range!.barTime).toBe(times[50]);
  });

  it("applyCrosshairToTargets maps each target to own anchor time", () => {
    const sourceSeries = { id: "src" };
    const t1Series = { id: "t1" };
    const t2Series = { id: "t2" };
    const calls: Array<{ time: unknown; series: unknown }> = [];
    const day = utcDayBucket(T1);
    const hour = Math.floor(T1 / 3600) * 3600;
    applyCrosshairToTargets(
      [
        {
          chart: {
            setCrosshairPosition: (_p, time, series) => {
              calls.push({ time, series });
            },
          },
          series: t1Series,
          candleTimes: [day - 86400, day, day + 86400],
          candles: [
            { open: 1, high: 2, low: 0, close: 1 },
            { open: 1, high: 4, low: 0, close: 2 },
            { open: 1, high: 2, low: 0, close: 1 },
          ],
          durationSec: TF_DURATION_SEC.D,
        },
        {
          chart: {
            setCrosshairPosition: (_p, time, series) => {
              calls.push({ time, series });
            },
          },
          series: t2Series,
          candleTimes: [hour - 3600, hour, hour + 3600],
          candles: [
            { open: 1, high: 2, low: 0, close: 1 },
            { open: 1, high: 6, low: 0, close: 3 },
            { open: 1, high: 2, low: 0, close: 1 },
          ],
          durationSec: TF_DURATION_SEC["1H"],
        },
      ],
      T1,
      sourceSeries,
    );
    expect(calls).toHaveLength(2);
    expect(calls[0].time).toBe(day);
    expect(calls[0].series).toBe(t1Series);
    expect(calls[1].time).toBe(hour);
    expect(calls[1].series).toBe(t2Series);
    expect(calls[0].time).not.toBe(T1);
  });

  it("exact-member timeToCoordinate returns null for non-member", () => {
    const ts = makeExactMemberTimeScale([100, 200, 300]);
    expect(ts.timeToCoordinate(200 as never)).toBe(60);
    expect(ts.timeToCoordinate(250 as never)).toBeNull();
    expect(ts.timeToCoordinate(T1 as never)).toBeNull();
  });
});

describe("D17 color classic", () => {
  it("normalize converts modern slash-alpha", () => {
    expect(normalizeCssColorForChart("rgb(255 255 255 / 5.2%)")).toBe(
      "rgba(255, 255, 255, 0.052)",
    );
    expect(
      isClassicChartColor(normalizeCssColorForChart("rgb(10 132 255 / 8%)")),
    ).toBe(true);
  });
});

describe("D34/D36 ChartGrid renderer contract", () => {
  it("positions #1/#2/#3 via anchors; zero unpositioned/left:0", async () => {
    const panes = samplePanes();
    const reports: PaneRenderReport[] = [];
    const markerBatches: unknown[][] = [];
    render(
      <ThemeProvider>
        <ChartGrid
          panes={panes}
          createChartImpl={factoryForPanes(panes, {
            onSetMarkers: (m) => markerBatches.push(m),
          })}
          onPaneRender={(r) => {
            reports.push(r);
          }}
        />
      </ThemeProvider>,
    );
    await waitFor(() => expect(reports.length).toBeGreaterThanOrEqual(4));

    for (const tf of ["D", "1H", "30m"] as const) {
      const r = reports.find((x) => x.tf === tf)!;
      expect(r.verticalAnnotations).toHaveLength(3);
      expect(r.verticalAnchors).toHaveLength(3);
      for (const a of r.verticalAnchors) {
        // anchor must be exact candle member (what timeToCoordinate receives)
        const pane = panes.find((p) => p.timeframe === tf)!;
        expect(pane.candles.some((c) => c.time === a.anchorTime)).toBe(true);
        const dur = TF_DURATION_SEC[tf];
        const resolved = findContainingBarAnchor(
          pane.candles.map((c) => c.time),
          a.eventTime,
          dur,
        );
        expect(resolved?.barTime).toBe(a.anchorTime);
        // D: event mid-day ≠ midnight bar start
        if (tf === "D") {
          expect(a.anchorTime).not.toBe(a.eventTime);
        }
      }
      expect(r.verticalAnchors[0].label).toMatch(/#1 · 2026-05-08 10:30/);
    }

    // DOM: positioned lines only
    expect(document.querySelectorAll(".chart-vline--unpositioned")).toHaveLength(
      0,
    );
    const lines = document.querySelectorAll(".chart-vline");
    expect(lines.length).toBeGreaterThanOrEqual(9); // 3 panes × 3
    for (const el of Array.from(lines)) {
      const left = (el as HTMLElement).style.left;
      expect(left).not.toBe("0px");
      expect(left).not.toBe("0");
      expect(el.getAttribute("data-vertical-anchor-time")).toBeTruthy();
    }

    // markers must not be square vertical stand-ins on higher TFs
    for (const batch of markerBatches) {
      const text = JSON.stringify(batch);
      if (text.includes("#1 · 2026-05-08 10:30") && !text.includes("買")) {
        expect(text).not.toMatch(/"shape":"square"/);
      }
    }
  });

  it("OOR: before-first / after-last / gap do not paint fake lines", async () => {
    const base = 1_700_000_000;
    const candles = Array.from({ length: 10 }, (_, i) => ({
      time: base + i * 300,
      open: 1,
      high: 2,
      low: 0.5,
      close: 1.5,
    }));
    const panes: ChartPaneData[] = [
      {
        timeframe: "5m",
        roleLabel: "入市",
        available: true,
        candles,
        verticalAnnotations: [
          { time: base - 10_000, label: "#before" },
          { time: base + 9 * 300 + 300, label: "#after" },
          { time: base + 150, label: "#ok" }, // inside first bar
        ],
      },
      {
        timeframe: "D",
        roleLabel: "大框架",
        available: true,
        candles: [
          { time: base, open: 1, high: 2, low: 0, close: 1 },
          // gap then far bar
          { time: base + 1_000_000, open: 1, high: 2, low: 0, close: 1 },
        ],
        verticalAnnotations: [
          { time: base + 500_000, label: "#gap" },
        ],
      },
      {
        timeframe: "1H",
        roleLabel: "中框架",
        available: true,
        candles,
        verticalAnnotations: [],
      },
      {
        timeframe: "30m",
        roleLabel: "輔助",
        available: true,
        candles,
        verticalAnnotations: [],
      },
    ];
    const reports: PaneRenderReport[] = [];
    render(
      <ThemeProvider>
        <ChartGrid
          panes={panes}
          createChartImpl={factoryForPanes(panes)}
          onPaneRender={(r) => reports.push(r)}
        />
      </ThemeProvider>,
    );
    await waitFor(() => expect(reports.length).toBeGreaterThanOrEqual(4));
    const m5 = reports.find((r) => r.tf === "5m")!;
    expect(m5.verticalAnchors.map((a) => a.label)).toEqual(["#ok"]);
    const d = reports.find((r) => r.tf === "D")!;
    expect(d.verticalAnchors).toHaveLength(0);
    expect(document.querySelectorAll(".chart-vline--unpositioned")).toHaveLength(
      0,
    );
  });

  it("theme switch via ThemeSwitch click re-applies tokens", async () => {
    const panes = samplePanes();
    const reports: PaneRenderReport[] = [];
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <div>
          <ThemeSwitch />
          <ChartGrid
            panes={panes}
            createChartImpl={factoryForPanes(panes)}
            onPaneRender={(r) => reports.push(r)}
          />
        </div>
      </ThemeProvider>,
    );
    await waitFor(() => expect(reports.length).toBeGreaterThanOrEqual(4));
    const before = reports.length;
    const firstTheme = reports[0].theme;
    // click a different theme once
    const nextLabel =
      firstTheme === "light" ? "深色" : firstTheme === "dark" ? "淺色" : "深色";
    await user.click(screen.getByRole("button", { name: nextLabel }));
    await waitFor(() => expect(reports.length).toBeGreaterThan(before));
    const afterTheme = reports[reports.length - 1].theme;
    expect(afterTheme).not.toBe(firstTheme);
    expect(isClassicChartColor(reports[reports.length - 1].colors.background)).toBe(
      true,
    );
  });

  it("append and replaceLast use series.update", async () => {
    const panes = samplePanes();
    let updates = 0;
    let setData = 0;
    let api: import("./ChartGrid").ChartGridHandle | null = null;
    render(
      <ThemeProvider>
        <ChartGrid
          panes={panes}
          createChartImpl={factoryForPanes(panes, {
            onUpdate: () => {
              updates += 1;
            },
            onSetData: () => {
              setData += 1;
            },
          })}
          onReady={(h) => {
            api = h;
          }}
        />
      </ThemeProvider>,
    );
    await waitFor(() => expect(api).not.toBeNull());
    const before = setData;
    api!.appendBar("5m", {
      time: T1 + 300,
      open: 1,
      high: 2,
      low: 0.5,
      close: 1.5,
    });
    api!.replaceLastBar("5m", {
      time: T1 + 300,
      open: 1,
      high: 3,
      low: 0.5,
      close: 2,
    });
    expect(updates).toBeGreaterThanOrEqual(2);
    expect(setData).toBe(before);
  });

  it("open position HUD and single-pane error isolation", async () => {
    const panes = samplePanes();
    let n = 0;
    const createImpl: ChartCreateFn = (host, opts) => {
      n += 1;
      if (n === 1) {
        throw new Error("boom-D");
      }
      return factoryForPanes(panes)(host, opts);
    };
    let api: import("./ChartGrid").ChartGridHandle | null = null;
    render(
      <ThemeProvider>
        <ChartGrid
          panes={panes}
          createChartImpl={createImpl}
          onReady={(h) => {
            api = h;
          }}
        />
      </ThemeProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("chart-error-D")).toBeInTheDocument();
    });
    expect(screen.getByTestId("chart-error-D").textContent).toMatch(
      /圖表繪製失敗/,
    );
    expect(screen.queryByTestId("chart-error-5m")).toBeNull();
    await waitFor(() => expect(api).not.toBeNull());
    act(() => {
      api!.setOpenPosition({
        entryPrice: 18040,
        stop: 18020,
        target: 18090,
        floatingPnl: "+12",
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("open-position-hud").textContent).toMatch(
        /持倉 18040/,
      );
    });
  });

  it("cleanup removes charts on unmount", async () => {
    const panes = samplePanes();
    let removes = 0;
    const { unmount } = render(
      <ThemeProvider>
        <ChartGrid
          panes={panes}
          createChartImpl={factoryForPanes(panes, {
            onRemove: () => {
              removes += 1;
            },
          })}
        />
      </ThemeProvider>,
    );
    await waitFor(() => expect(removes).toBe(0));
    unmount();
    expect(removes).toBeGreaterThanOrEqual(4);
  });
});
