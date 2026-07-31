/**
 * Pure export-time catalog revalidation (Correction A D12).
 * All export paths must call this — button disabled is not enough.
 */

import { findCatalogRow } from "../catalog/parseCatalog";
import type { CatalogState, InstrumentCatalogRow } from "../catalog/types";
import { isAssetClass } from "../catalog/types";
import type { SketchDraft } from "./types";

export function validateDraftAgainstCatalog(
  draft: SketchDraft,
  catalog: CatalogState,
): string[] {
  const reasons: string[] = [];
  if (catalog.status === "loading") {
    reasons.push("合約清單載入中——唔可以匯出");
    return reasons;
  }
  if (catalog.status === "empty") {
    reasons.push(catalog.message || "合約清單空白——唔可以匯出");
    return reasons;
  }
  if (catalog.status === "error") {
    reasons.push(catalog.message || "合約清單讀取失敗——唔可以匯出");
    return reasons;
  }
  if (catalog.status === "invalid") {
    reasons.push(catalog.message || "合約清單無效——唔可以匯出");
    return reasons;
  }
  // ready
  const symbol = draft.instrument?.trim() ?? "";
  if (!symbol) {
    reasons.push("未揀 primary instrument");
    return reasons;
  }
  if (!draft.assetClass?.trim()) {
    reasons.push("缺 asset_class（catalog 未帶入）");
    return reasons;
  }
  if (!isAssetClass(draft.assetClass)) {
    reasons.push(`asset_class「${draft.assetClass}」唔係受控 enum`);
  }
  const row = findCatalogRow(catalog.rows, symbol);
  if (!row) {
    reasons.push(`instrument「${symbol}」唔喺合約清單`);
    return reasons;
  }
  if (row.assetClass !== draft.assetClass) {
    reasons.push(
      `draft asset_class「${draft.assetClass}」同 catalog「${row.assetClass}」唔一致`,
    );
  }
  if (!row.currency || !row.sessionsAvailable?.length) {
    reasons.push(`合約 ${symbol} 缺 currency／sessions metadata`);
  }
  return reasons;
}

export function catalogRowsOrNull(
  catalog: CatalogState,
): InstrumentCatalogRow[] | null {
  return catalog.status === "ready" ? catalog.rows : null;
}
