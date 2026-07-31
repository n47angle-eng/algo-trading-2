/** Live scorecard human-copy mapping (D31) — fail-closed for unknown fields. */

const SCORECARD_DIM_LABELS: Record<string, string> = {
  profit_factor: "獲利因子",
  drawdown: "最大回撤",
  max_drawdown: "最大回撤",
  edge: "優勢是否存在",
  sample: "樣本夠唔夠",
  stability: "穩唔穩",
  expectancy: "期望值",
  consistency: "一致性",
  tail: "尾部風險",
  sub_fill: "成交品質",
  sub_time: "時段分佈",
  sub_regime: "市況覆蓋",
  "1_樣本量": "樣本量",
  "2b_好運依賴": "好運依賴",
};

const SCORECARD_DETAIL_KEYS: Record<string, string> = {
  text: "說明",
  label: "標籤",
  statusLabel: "狀態",
  value: "數值",
  net_r: "淨 R",
  trades: "成交筆數",
  trade_count: "成交筆數",
  win_rate: "勝率",
  profit_factor: "獲利因子",
  max_drawdown: "最大回撤",
  max_drawdown_pnl: "最大回撤",
  sample_size: "樣本量",
};

export function scorecardLabel(dim: string): string {
  if (SCORECARD_DIM_LABELS[dim]) {
    return SCORECARD_DIM_LABELS[dim];
  }
  return "記分項目名稱暫未支援";
}

export function scorecardStatusLabel(status: string): string {
  switch (status) {
    case "pass":
      return "通過";
    case "warn":
      return "警告";
    case "not_available":
    case "not_available_p2":
      return "樣本不足／未提供";
    case "fail":
      return "未過";
    case "":
      return "未提供";
    default:
      return "狀態暫未支援";
  }
}

function formatDetailValue(v: unknown): string | null {
  if (v == null) {
    return null;
  }
  if (
    typeof v === "string" ||
    typeof v === "number" ||
    typeof v === "boolean"
  ) {
    return String(v);
  }
  return null;
}

export function humanDetail(
  detail: Record<string, unknown> | undefined,
): string {
  if (!detail || Object.keys(detail).length === 0) {
    return "—";
  }
  const parts: string[] = [];
  let sawUnsupported = false;
  for (const [k, v] of Object.entries(detail)) {
    if (k === "text" || k === "label" || k === "statusLabel") {
      if (typeof v === "string") {
        parts.push(v);
      }
      continue;
    }
    const keyLabel = SCORECARD_DETAIL_KEYS[k];
    const formatted = formatDetailValue(v);
    if (!keyLabel || formatted == null) {
      sawUnsupported = true;
      continue;
    }
    parts.push(`${keyLabel}：${formatted}`);
  }
  if (parts.length === 0) {
    return sawUnsupported ? "詳細欄位暫未支援" : "—";
  }
  if (sawUnsupported) {
    parts.push("（部分詳細欄位暫未支援）");
  }
  return parts.join(" · ");
}
