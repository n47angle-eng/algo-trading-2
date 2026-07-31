import { CANONICAL_CHART_FILES } from "./paths";
import { imageSlotBlocker } from "./pngGate";
import type { SketchDraft } from "./types";

const COUNT_WORDS = ["", "一", "兩", "三", "四", "五", "六", "七", "八"] as const;

/**
 * Human-readable export blockers (constraint #5 revised 2026-07-25).
 * Itemized — never a single vague "未填齊".
 * Correction A: four real PNGs + strategy rationale required for export.
 */
export function exportBlockingReasons(draft: SketchDraft): string[] {
  const reasons: string[] = [];
  if (!draft.title.trim()) {
    reasons.push("標題未填");
  }
  if (!draft.instrument || !draft.instrument.trim()) {
    reasons.push("未揀 primary instrument");
  }
  if (!draft.assetClass || !draft.assetClass.trim()) {
    reasons.push("缺 asset_class（catalog 未帶入）");
  }
  if (draft.exported && (!draft.instrument || !draft.assetClass)) {
    reasons.push("已匯出 legacy 缺 instrument／asset_class，只可複製成新草圖");
  }
  // Strategy rationale required; insight may omit
  if (draft.kind === "strategy" && !draft.rationale.trim()) {
    reasons.push("整體理據未填");
  }
  const missingViews = draft.charts
    .filter((c) => !c.ownerView.trim())
    .map((c, i) => c.timeframe || CANONICAL_CHART_FILES[i] || `格${i + 1}`);
  if (missingViews.length === 1) {
    reasons.push(`${missingViews[0]} 未填`);
  } else if (missingViews.length > 1) {
    const nWord =
      missingViews.length < COUNT_WORDS.length
        ? COUNT_WORDS[missingViews.length]
        : String(missingViews.length);
    reasons.push(`${missingViews.join("、")} ${nWord}格未填`);
  }
  // Exact four chart slots (package cardinality — not "at least four")
  if (draft.charts.length !== 4) {
    reasons.push(
      `圖格數量錯誤：預期 4 格，實際 ${draft.charts.length} 格；請複製或重建草圖`,
    );
  }
  // Four real PNG slots (strategy + insight) — only when cardinality is exact
  // so we do not invent slot labels for corrupt extra members.
  if (draft.charts.length === 4) {
    draft.charts.forEach((c, i) => {
      const label =
        CANONICAL_CHART_FILES[i]?.replace(/^chart-/, "").replace(/\.png$/, "") ??
        c.timeframe ??
        `格${i + 1}`;
      const imgBlock = imageSlotBlocker(label, c.imageDataUrl);
      if (imgBlock) {
        reasons.push(imgBlock);
      }
    });
  }
  return reasons;
}

export function canExportSketch(draft: SketchDraft): boolean {
  return exportBlockingReasons(draft).length === 0;
}

/** Summarize gaps for the draft list (constraint #27). */
export function draftGapSummary(draft: SketchDraft): string {
  const reasons = exportBlockingReasons(draft);
  if (reasons.length === 0) {
    return draft.exported ? "可匯出／已匯出" : "齊，可匯出";
  }
  return reasons.join(" · ");
}
