/**
 * P5 owner-review fixture — memory only, never mixed into live /results.
 */

import type {
  ChartPaneData,
  NearMiss,
  PromotionDecisionRecord,
  ResultDetail,
  ResultListItem,
  ScorecardRow,
  TradeCausal,
} from "./types";
import { isRunRead } from "./readState";
import {
  FIXTURE_TZ,
  formatHktLocal,
  hktLocalToUnixSec,
  unixSecToIso,
  utcDayBucket,
  verticalLabel,
} from "./timeIdentity";

const SCORE_BASE: ScorecardRow[] = [
  {
    dim: "edge",
    label: "優勢是否存在",
    status: "pass",
    statusLabel: "通過",
    detail: "樣本內期望 R 為正",
  },
  {
    dim: "sample",
    label: "樣本夠唔夠",
    status: "warn",
    statusLabel: "樣本偏少",
    detail: "少過 30 筆，要小心解讀",
  },
  {
    dim: "stability",
    label: "穩唔穩",
    status: "pass",
    statusLabel: "通過",
    detail: "分月表現冇單邊崩",
  },
  {
    dim: "drawdown",
    label: "最大回撤",
    status: "pass",
    statusLabel: "可接受",
    detail: "最大回撤 420 USD",
  },
  {
    dim: "profit_factor",
    label: "獲利因子",
    status: "pass",
    statusLabel: "通過",
    detail: "獲利因子 1.4",
  },
  {
    dim: "expectancy",
    label: "期望值",
    status: "pass",
    statusLabel: "通過",
    detail: "每筆期望 +0.2R",
  },
  {
    dim: "consistency",
    label: "一致性",
    status: "not_available",
    statusLabel: "樣本不足",
    detail: "子期間樣本不足",
  },
  {
    dim: "tail",
    label: "尾部風險",
    status: "pass",
    statusLabel: "通過",
    detail: "最差單筆 −1.1R",
  },
  {
    dim: "sub_fill",
    label: "成交品質",
    status: "pass",
    statusLabel: "通過",
    detail: "滑點假設已套用",
  },
  {
    dim: "sub_time",
    label: "時段分佈",
    status: "warn",
    statusLabel: "集中",
    detail: "多數喺開市後兩小時",
  },
  {
    dim: "sub_regime",
    label: "市況覆蓋",
    status: "pass",
    statusLabel: "通過",
    detail: "升跌日都有樣本",
  },
];

function makeCandles(
  start: number,
  n: number,
  step: number,
): ChartPaneData["candles"] {
  const out: ChartPaneData["candles"] = [];
  let px = 18000;
  for (let i = 0; i < n; i++) {
    const o = px;
    const c = px + (i % 3 === 0 ? 12 : -8);
    out.push({
      time: start + i * step,
      open: o,
      high: Math.max(o, c) + 15,
      low: Math.min(o, c) - 15,
      close: c,
    });
    px = c;
  }
  return out;
}

/** Per-trade stop/target levels — never silent trades[0] (D29). */
export function levelsForTrade(t: TradeCausal): ChartPaneData["levels"] {
  const targetPrice =
    t.side === "long"
      ? Math.max(t.exitPrice, t.entryPrice + 10)
      : Math.min(t.exitPrice, t.entryPrice - 10);
  // Prefer explicit exit target when exit kind is 止賺
  const tgt = t.exit.kind === "止賺" ? t.exitPrice : targetPrice;
  return [
    {
      price: t.stop.finalPrice,
      token: "color-negative",
      label: `#${t.tradeIndex} 止損`,
    },
    {
      price: t.exit.kind === "止賺" ? t.exitPrice : tgt,
      token: "color-positive",
      label: `#${t.tradeIndex} 目標`,
    },
  ];
}

/** 5m entry/exit markers for one or more trades (D27/D29). */
export function tradeMarkersFor(
  trades: TradeCausal[],
): NonNullable<ChartPaneData["markers"]> {
  const out: NonNullable<ChartPaneData["markers"]> = [];
  for (const t of trades) {
    const entrySec = Math.floor(new Date(t.focusTimeUtc).getTime() / 1000);
    const exitSec = Math.floor(
      // exit local is also HKT; derive from entry + known labels when possible
      hktLocalToUnixSec(
        Number(t.exitTimeLocal.slice(0, 4)),
        Number(t.exitTimeLocal.slice(5, 7)),
        Number(t.exitTimeLocal.slice(8, 10)),
        Number(t.exitTimeLocal.slice(11, 13)),
        Number(t.exitTimeLocal.slice(14, 16)),
      ),
    );
    out.push({
      time: entrySec,
      position: t.side === "long" ? "belowBar" : "aboveBar",
      shape: t.side === "long" ? "arrowUp" : "arrowDown",
      token: "chart-marker-entry",
      text: `#${t.tradeIndex} · ${t.entryTimeLocal} · ${t.side === "long" ? "買" : "賣"} ${t.entryPrice}`,
    });
    out.push({
      time: exitSec,
      position: t.side === "long" ? "aboveBar" : "belowBar",
      shape: "circle",
      token: "chart-marker-exit",
      text: `#${t.tradeIndex} · ${t.exitTimeLocal} · ${t.exit.kind} ${t.exitPrice}`,
    });
  }
  return out;
}

function verticalsForTrades(
  trades: TradeCausal[],
): NonNullable<ChartPaneData["verticalLines"]> {
  return trades.map((t) => {
    const sec = Math.floor(new Date(t.focusTimeUtc).getTime() / 1000);
    return {
      time: sec,
      label: verticalLabel(t.tradeIndex, sec),
    };
  });
}

function fullCharts(opts: {
  focus: number;
  /** Higher TFs: true verticals only (no 5m marker spam). */
  verticalTimes?: number[];
  verticalTrades?: TradeCausal[];
  /** 5m only */
  markers?: ChartPaneData["markers"];
  rejectMarkers?: ChartPaneData["rejectMarkers"];
  levels?: ChartPaneData["levels"];
  trendDayTimes?: number[];
  /** When true, put markers only on 5m (D29). */
  markersOnlyOn5m?: boolean;
}): ChartPaneData[] {
  const focus = opts.focus;
  const vFromTrades = opts.verticalTrades
    ? verticalsForTrades(opts.verticalTrades)
    : undefined;
  const vTimes =
    opts.verticalTimes && opts.verticalTimes.length > 0
      ? opts.verticalTimes
      : vFromTrades
        ? vFromTrades.map((v) => v.time)
        : [focus];
  const verticals =
    vFromTrades ??
    vTimes.map((t, i) => ({
      time: t,
      label: verticalLabel(i + 1, t),
    }));
  const tMin = Math.min(...vTimes, focus);
  const tMax = Math.max(...vTimes, focus);
  const dStart = utcDayBucket(tMin) - 10 * 86400;
  const dCount = Math.ceil((tMax - dStart) / 86400) + 15;
  const hStart = Math.floor((tMin - 12 * 3600) / 3600) * 3600;
  const hCount = Math.ceil((tMax - hStart) / 3600) + 24;
  const m30Start = Math.floor((tMin - 6 * 3600) / 1800) * 1800;
  const m30Count = Math.ceil((tMax - m30Start) / 1800) + 24;
  const m5Start = Math.floor((tMin - 3 * 3600) / 300) * 300;
  const m5Count = Math.ceil((tMax + 2 * 3600 - m5Start) / 300) + 20;
  const dCandles = makeCandles(dStart, Math.max(dCount, 30), 86400);
  const hCandles = makeCandles(hStart, Math.max(hCount, 48), 3600);
  const m30Candles = makeCandles(m30Start, Math.max(m30Count, 48), 1800);
  const m5Candles = makeCandles(m5Start, Math.max(m5Count, 80), 300);
  const trendDays =
    opts.trendDayTimes ?? vTimes.map((t) => utcDayBucket(t));
  const only5m = opts.markersOnlyOn5m !== false;
  return [
    {
      timeframe: "D",
      roleLabel: "大框架",
      available: true,
      candles: dCandles,
      volume: dCandles.map((c) => ({
        time: c.time,
        value: 1000 + (c.time % 500),
      })),
      indicators: [
        {
          id: "ema90",
          token: "chart-ema-90",
          points: dCandles.map((c) => ({
            time: c.time,
            value: c.close - 20,
          })),
        },
      ],
      verticalLines: verticals,
      verticalAnnotations: verticals,
      markers: only5m ? undefined : opts.markers,
      rejectMarkers: opts.rejectMarkers,
      trendDayTimes: trendDays,
    },
    {
      timeframe: "1H",
      roleLabel: "中框架",
      available: true,
      candles: hCandles,
      volume: hCandles.map((c) => ({
        time: c.time,
        value: 200 + (c.time % 80),
      })),
      indicators: [
        {
          id: "ema18",
          token: "chart-ema-18",
          points: hCandles.map((c) => ({
            time: c.time,
            value: c.close - 8,
          })),
        },
      ],
      verticalLines: verticals,
      verticalAnnotations: verticals,
      markers: only5m ? undefined : opts.markers,
      rejectMarkers: opts.rejectMarkers,
    },
    {
      timeframe: "30m",
      roleLabel: "輔助",
      available: true,
      candles: m30Candles,
      volume: m30Candles.map((c) => ({
        time: c.time,
        value: 120 + (c.time % 40),
      })),
      verticalLines: verticals,
      verticalAnnotations: verticals,
      markers: only5m ? undefined : opts.markers,
      rejectMarkers: opts.rejectMarkers,
    },
    {
      timeframe: "5m",
      roleLabel: "入市",
      available: true,
      candles: m5Candles,
      volume: m5Candles.map((c) => ({
        time: c.time,
        value: 50 + (c.time % 20),
      })),
      indicators: [
        {
          id: "ema18",
          token: "chart-ema-18",
          points: m5Candles.map((c) => ({
            time: c.time,
            value: c.close - 4,
          })),
        },
      ],
      markers: opts.markers,
      rejectMarkers: opts.rejectMarkers,
      levels: opts.levels,
    },
  ];
}

/**
 * Canonical trade instants (D28): local HKT strings round-trip to focusTimeUtc.
 * Trade 1: 2026-05-08 10:30 HKT = 02:30Z
 */
const T1_ENTRY = hktLocalToUnixSec(2026, 5, 8, 10, 30);
const T1_EXIT = hktLocalToUnixSec(2026, 5, 8, 11, 5);
const T2_ENTRY = hktLocalToUnixSec(2026, 5, 9, 9, 45);
const T2_EXIT = hktLocalToUnixSec(2026, 5, 9, 10, 20);
const T3_ENTRY = hktLocalToUnixSec(2026, 5, 12, 13, 10);
const T3_EXIT = hktLocalToUnixSec(2026, 5, 12, 14, 0);

const TRADES: TradeCausal[] = [
  {
    tradeIndex: 1,
    side: "long",
    entryTimeLocal: formatHktLocal(T1_ENTRY),
    entryPrice: 18040,
    exitTimeLocal: formatHktLocal(T1_EXIT),
    exitPrice: 18090,
    rMultiple: 1.2,
    pnlUsd: 540,
    whyEntry: {
      d: "日線升勢（收市高過 90EMA，實際 18100 > 18020）",
      h1: "1H 回踩 18EMA 後轉強（實際 18035 觸及）",
      m5: "5m 突破前高 18038（門檻 18038，實際 18040）",
    },
    stop: {
      anchor: "回踩低點",
      offsetTicks: 4,
      finalPrice: 18020,
      reason: "錨在回踩低點再退 4 tick",
    },
    exit: {
      kind: "止賺",
      whichFirst: "目標先觸發",
      detail: "同分鐘先到目標 18090，止蝕未觸",
    },
    conservative:
      "same-bar 止蝕優先假設：呢分鐘目標先到，所以按目標成交；跳空按開市價。",
    focusTimeUtc: unixSecToIso(T1_ENTRY),
  },
  {
    tradeIndex: 2,
    side: "long",
    entryTimeLocal: formatHktLocal(T2_ENTRY),
    entryPrice: 18110,
    exitTimeLocal: formatHktLocal(T2_EXIT),
    exitPrice: 18080,
    rMultiple: -0.8,
    pnlUsd: -360,
    whyEntry: {
      d: "日線仍升勢",
      h1: "1H 再回踩",
      m5: "5m 突破失敗後第二次突破",
    },
    stop: {
      anchor: "結構低點",
      offsetTicks: 4,
      finalPrice: 18085,
      reason: "結構低 −4 tick",
    },
    exit: {
      kind: "止蝕",
      whichFirst: "止蝕先觸發",
      detail: "跌穿止蝕 18085",
    },
    conservative: "same-bar 止蝕優先：該分鐘同時觸及止蝕與反轉訊號，按止蝕。",
    focusTimeUtc: unixSecToIso(T2_ENTRY),
  },
  {
    tradeIndex: 3,
    side: "short",
    entryTimeLocal: formatHktLocal(T3_ENTRY),
    entryPrice: 17990,
    exitTimeLocal: formatHktLocal(T3_EXIT),
    exitPrice: 17940,
    rMultiple: 1.0,
    pnlUsd: 450,
    whyEntry: {
      d: "日線轉弱",
      h1: "1H 跌破 18EMA",
      m5: "5m 跌破前低",
    },
    stop: {
      anchor: "反彈高點",
      offsetTicks: 4,
      finalPrice: 18010,
      reason: "反彈高 +4 tick",
    },
    exit: {
      kind: "日終",
      whichFirst: "日終平倉",
      detail: "未到目標，日終按規則平倉",
    },
    conservative: "日終平倉按收市價；一分鐘精度。",
    focusTimeUtc: unixSecToIso(T3_ENTRY),
  },
];

const N1 = hktLocalToUnixSec(2026, 5, 7, 10, 15);
const N2 = hktLocalToUnixSec(2026, 5, 7, 14, 40);
const N3 = hktLocalToUnixSec(2026, 5, 8, 9, 5);

const NEAR: NearMiss[] = [
  {
    index: 1,
    timeLocal: formatHktLocal(N1),
    timezone: FIXTURE_TZ,
    layerReached: "1H 回踩",
    stepsAway: 1,
    missing: "5m 未突破前高",
    values: "前高 18050，最高只到 18048（差 2 tick）",
    focusTimeUtc: unixSecToIso(N1),
  },
  {
    index: 2,
    timeLocal: formatHktLocal(N2),
    timezone: FIXTURE_TZ,
    layerReached: "日線升勢",
    stepsAway: 2,
    missing: "1H 未回踩完成",
    values: "1H 低點仍高過 18EMA 12 tick",
    focusTimeUtc: unixSecToIso(N2),
  },
  {
    index: 3,
    timeLocal: formatHktLocal(N3),
    timezone: FIXTURE_TZ,
    layerReached: "5m 突破",
    stepsAway: 1,
    missing: "成交量過濾未過",
    values: "需要 ≥ 80，實際 72",
    focusTimeUtc: unixSecToIso(N3),
  },
];

const NEAR_TIMES = [N1, N2, N3];

/** Build charts for a selected trade's levels on 5m (D29). */
export function chartsWithActiveTrade(
  base: ChartPaneData[],
  trades: TradeCausal[],
  activeIndex: number | null,
): ChartPaneData[] {
  const active =
    activeIndex != null
      ? trades.find((t) => t.tradeIndex === activeIndex) ?? null
      : trades[0] ?? null;
  return base.map((pane) => {
    if (pane.timeframe !== "5m") {
      return {
        ...pane,
        // higher TFs: verticals only
        markers: undefined,
        levels: undefined,
      };
    }
    if (!active) {
      return {
        ...pane,
        markers: tradeMarkersFor(trades),
        levels: undefined,
      };
    }
    return {
      ...pane,
      markers: tradeMarkersFor([active]),
      levels: levelsForTrade(active),
    };
  });
}

const DETAILS: Record<string, ResultDetail> = {
  "fixture-run-1": {
    runId: "fixture-run-1",
    strategyVersion: "strategy-0001",
    strategyLabel: "Trend 回踩 18EMA",
    contractId: "NQ",
    rangeStartLocal: "2026-05-06 22:00 HKT",
    rangeEndLocal: "2026-05-08 21:00 HKT",
    timezone: "Asia/Hong_Kong",
    tradingDays: 3,
    tradeCount: 0,
    winRate: null,
    netR: 0,
    netUsd: 0,
    maxDrawdownUsd: 0,
    scorecard: SCORE_BASE.map((s) =>
      s.dim === "sample"
        ? {
            ...s,
            status: "not_available",
            statusLabel: "未提供",
            detail: "0 成交，樣本維度未提供",
          }
        : s,
    ),
    funnel: {
      dailyPass: 2,
      dailyTotal: 3,
      evalPass: 14,
      fills: 0,
      judgment: "邏輯正常：三次都差一步；定義可以再同 Terminal 對齊。",
    },
    nearMisses: NEAR,
    trades: [],
    charts: fullCharts({
      focus: NEAR_TIMES[0],
      verticalTimes: NEAR_TIMES,
      rejectMarkers: NEAR.map((n) => ({
        time: Math.floor(new Date(n.focusTimeUtc).getTime() / 1000),
        text: `近失 #${n.index} · ${n.missing}`,
      })),
      markers: NEAR.map((n) => ({
        time: Math.floor(new Date(n.focusTimeUtc).getTime() / 1000),
        position: "aboveBar" as const,
        shape: "circle",
        token: "chart-reject",
        text: `近失 #${n.index}`,
      })),
      markersOnlyOn5m: true,
    }),
    warnings: [],
    whyZero: "卡喺 5m 突破／成交量過濾；三次近失都差一步。",
  },
  "fixture-run-trades": {
    runId: "fixture-run-trades",
    strategyVersion: "strategy-0002",
    strategyLabel: "Trend 回踩 90EMA",
    contractId: "YM",
    rangeStartLocal: "2026-05-06 22:00 HKT",
    rangeEndLocal: "2026-05-12 21:00 HKT",
    timezone: "Asia/Hong_Kong",
    tradingDays: 5,
    tradeCount: 3,
    winRate: 0.667,
    netR: 1.4,
    netUsd: 630,
    maxDrawdownUsd: 420,
    scorecard: SCORE_BASE,
    funnel: {
      dailyPass: 4,
      dailyTotal: 5,
      evalPass: 22,
      fills: 3,
      judgment: "有成交樣本；仍要睇逐筆因果同回撤。",
    },
    nearMisses: [],
    trades: TRADES,
    charts: fullCharts({
      focus: T1_ENTRY,
      verticalTrades: TRADES,
      markers: tradeMarkersFor([TRADES[0]]),
      levels: levelsForTrade(TRADES[0]),
      markersOnlyOn5m: true,
    }),
    warnings: ["示範警告：滑點假設較保守"],
    whyZero: null,
  },
  "fixture-run-unread": {
    runId: "fixture-run-unread",
    strategyVersion: "strategy-0001",
    strategyLabel: "Trend 回踩 18EMA",
    contractId: "GC",
    rangeStartLocal: "2026-04-01 22:00 HKT",
    rangeEndLocal: "2026-04-10 21:00 HKT",
    timezone: "Asia/Hong_Kong",
    tradingDays: 7,
    tradeCount: 2,
    winRate: 0.5,
    netR: 0.3,
    netUsd: 120,
    maxDrawdownUsd: 200,
    scorecard: SCORE_BASE,
    funnel: {
      dailyPass: 5,
      dailyTotal: 7,
      evalPass: 18,
      fills: 2,
      judgment: "示範未睇結果。",
    },
    nearMisses: [],
    trades: TRADES.slice(0, 2),
    charts: fullCharts({
      focus: T1_ENTRY,
      verticalTrades: TRADES.slice(0, 2),
      markers: tradeMarkersFor([TRADES[0]]),
      levels: levelsForTrade(TRADES[0]),
      markersOnlyOn5m: true,
    }),
    warnings: [],
    whyZero: null,
  },
};

let decisions: PromotionDecisionRecord[] = [];

/**
 * Soft reset — list remount may call this; D21 forbids clearing decisions.
 * Fixture detail data is immutable constants, so soft reset is a no-op.
 */
export function resetResultsFixture(): void {
  /* intentionally empty — decisions survive soft reset (D21) */
}

/** Test-only full wipe of session decisions. */
export function __hardResetResultsFixture(): void {
  decisions = [];
}

export function decisionTypeLabel(type: PromotionDecisionRecord["type"]): string {
  switch (type) {
    case "use":
      return "用得";
    case "return":
      return "打回";
    case "abandon":
      return "放棄";
    default:
      return type;
  }
}

export function listFixtureResults(): ResultListItem[] {
  return Object.values(DETAILS).map((d) => ({
    runId: d.runId,
    strategyVersion: d.strategyVersion,
    strategyLabel: d.strategyLabel,
    contractId: d.contractId,
    tradeCount: d.tradeCount,
    winRate: d.winRate,
    netR: d.netR,
    netUsd: d.netUsd,
    maxDrawdownUsd: d.maxDrawdownUsd,
    whyLine:
      d.tradeCount === 0
        ? (d.whyZero ?? "點入去睇為咩")
        : `淨 ${d.netR?.toFixed(1) ?? "—"}R · ${d.tradeCount} 筆`,
    bottleneck: d.tradeCount === 0 ? d.whyZero : null,
    unread: !isRunRead(d.runId, "owner-review"),
    rangeStart: d.rangeStartLocal,
    rangeEnd: d.rangeEndLocal,
    tradingDays: d.tradingDays,
  }));
}

export function getFixtureDetail(runId: string): ResultDetail | null {
  return DETAILS[runId] ?? null;
}

export function appendFixtureDecision(
  rec: Omit<PromotionDecisionRecord, "id" | "at">,
): PromotionDecisionRecord {
  const full: PromotionDecisionRecord = {
    ...rec,
    scorecardSnapshot: rec.scorecardSnapshot.map((row) => ({ ...row })),
    id: `dec-${decisions.length + 1}`,
    at: new Date().toISOString(),
  };
  decisions = [...decisions, full];
  return full;
}

export function listFixtureDecisions(runId: string): PromotionDecisionRecord[] {
  return decisions.filter((d) => d.runId === runId);
}

export function buildResultZipPayload(runId: string): {
  files: Record<string, string>;
  zipName: string;
  opener: string;
} {
  const detail = DETAILS[runId];
  if (!detail) {
    throw new Error(`unknown fixture run ${runId}`);
  }
  const zipName = `result-${runId}.zip`;
  const tradesPath = `trades/${runId}.json`;
  const equityPath = `equity/${runId}.json`;
  const eventsPath = `events/${runId}.json`;
  const result = {
    schema: "result.v1",
    run: {
      run_id: runId,
      strategy_version: detail.strategyVersion,
      manifest: {
        contract: detail.contractId,
        range: [detail.rangeStartLocal, detail.rangeEndLocal],
        capital: 100_000,
        costs: {
          commission_per_side: 2.25,
          slippage_ticks: 1,
        },
        fill_model: "conservative",
        data_fingerprint: `fp-fixture-${runId}`,
      },
      engine: {
        nautilus: "1.230.0-fixture",
        app: "futures-research-owner-review",
      },
    },
    metrics: {
      trade_count: detail.tradeCount,
      net_r: detail.netR,
      net_pnl: detail.netUsd,
      win_rate: detail.winRate,
      max_drawdown_pnl: detail.maxDrawdownUsd,
    },
    scorecard: detail.scorecard,
    funnel: detail.funnel,
    warnings: detail.warnings,
    trades_ref: tradesPath,
    equity_curve_ref: equityPath,
    events_ref: eventsPath,
  };
  const trades = {
    schema: "trades.v1",
    run_id: runId,
    trades: detail.trades,
  };
  const equity = {
    schema: "equity_curve.v1",
    run_id: runId,
    points: [
      { t: detail.rangeStartLocal, v: 100_000 },
      {
        t: detail.rangeEndLocal,
        v: 100_000 + (detail.netUsd ?? 0),
      },
    ],
  };
  const events = {
    schema: "events.v1",
    run_id: runId,
    events:
      detail.nearMisses.length > 0
        ? detail.nearMisses.map((n) => ({
            kind: "near_miss",
            index: n.index,
            time: n.focusTimeUtc,
            detail: n.missing,
            cut_reason: n.missing,
            steps_away: n.stepsAway,
          }))
        : detail.trades.map((t) => ({
            kind: "fill",
            trade: t.tradeIndex,
            time: t.focusTimeUtc,
          })),
  };
  const opener = [
    `請讀取 ${zipName}。`,
    "先讀主檔 result.json（schema result.v1），需要時再讀 sidecar：",
    `- ${tradesPath}`,
    `- ${equityPath}`,
    `- ${eventsPath}`,
    "要改良就輸出 direct-lineage 新 strategy.v1，唔好覆蓋舊版。",
  ].join("\n");
  return {
    zipName,
    opener,
    files: {
      "result.json": JSON.stringify(result, null, 2),
      [tradesPath]: JSON.stringify(trades, null, 2),
      [equityPath]: JSON.stringify(equity, null, 2),
      [eventsPath]: JSON.stringify(events, null, 2),
    },
  };
}
