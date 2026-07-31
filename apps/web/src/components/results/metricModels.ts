import { formatNumber, formatPct, toneForSigned } from "./format";

export interface MetricCardModel {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "muted" | "neutral";
}

export function metricsFromResult(
  metrics: Record<string, unknown>,
): MetricCardModel[] {
  const num = (key: string): number | null => {
    const raw = metrics[key];
    return typeof raw === "number" ? raw : null;
  };
  return [
    {
      label: "淨利 R",
      value: formatNumber(num("net_r")),
      tone: toneForSigned(num("net_r")),
    },
    {
      label: "淨 PnL",
      value: formatNumber(num("net_pnl")),
      tone: toneForSigned(num("net_pnl")),
    },
    {
      label: "交易數",
      value: formatNumber(num("trade_count"), 0),
    },
    {
      label: "勝率",
      value: formatPct(num("win_rate")),
    },
    {
      label: "Profit factor",
      value: formatNumber(num("profit_factor")),
    },
    {
      label: "期望值 R",
      value: formatNumber(num("expectancy_r")),
      tone: toneForSigned(num("expectancy_r")),
    },
    {
      label: "Max DD $",
      value: formatNumber(num("max_drawdown_pnl")),
      tone: "neg",
    },
    {
      label: "Max DD R",
      value: formatNumber(num("max_drawdown_r")),
      tone: "neg",
    },
  ];
}
