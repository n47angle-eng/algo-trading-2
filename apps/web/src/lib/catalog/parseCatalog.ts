import {
  isAssetClass,
  type CatalogState,
  type InstrumentCatalogRow,
} from "./types";

function nonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const t = value.trim();
  return t.length > 0 ? t : null;
}

/**
 * Parse GET /api/v1/data/coverage into instrument catalog.
 * Fail-closed when new additive fields are missing/invalid (no NQ/YM/GC hardcode).
 */
export function parseInstrumentCatalog(body: unknown): CatalogState {
  if (!body || typeof body !== "object") {
    return { status: "invalid", message: "合約清單回應格式無效。" };
  }
  const o = body as Record<string, unknown>;
  if (o.schema !== "data_coverage.v1") {
    return {
      status: "invalid",
      message: `合約清單 schema 應係 data_coverage.v1，而家係 ${String(o.schema ?? "缺席")}。`,
    };
  }
  if (!Array.isArray(o.contracts)) {
    return { status: "invalid", message: "合約清單缺 contracts 陣列。" };
  }
  if (o.contracts.length === 0) {
    return {
      status: "empty",
      message: "未有已設定合約——暫時唔可以揀 instrument。",
    };
  }
  const rows: InstrumentCatalogRow[] = [];
  const symbols = new Set<string>();
  const contractIds = new Set<string>();
  for (const raw of o.contracts) {
    if (!raw || typeof raw !== "object") {
      return { status: "invalid", message: "合約清單有非法列。" };
    }
    const c = raw as Record<string, unknown>;
    const symbol = nonEmptyString(c.symbol);
    const contractId = nonEmptyString(c.contract_id);
    const displayName = nonEmptyString(c.display_name);
    const assetClassRaw = nonEmptyString(c.asset_class);
    const currency = nonEmptyString(c.currency);
    const sessions = c.sessions_available;
    if (
      !symbol ||
      !contractId ||
      !displayName ||
      !assetClassRaw ||
      !currency
    ) {
      return {
        status: "invalid",
        message:
          "合約清單缺 display_name／asset_class／currency／sessions_available 等必要欄——唔會用硬編碼名稱頂替。",
      };
    }
    if (!isAssetClass(assetClassRaw)) {
      return {
        status: "invalid",
        message: `未知 asset_class「${assetClassRaw}」——清單 fail-closed。`,
      };
    }
    if (!Array.isArray(sessions) || sessions.length === 0) {
      return {
        status: "invalid",
        message: `合約 ${symbol} 缺 sessions_available。`,
      };
    }
    if (!sessions.every((s) => typeof s === "string" && s.trim() !== "")) {
      return {
        status: "invalid",
        message: `合約 ${symbol} 嘅 sessions_available 非法。`,
      };
    }
    if (symbols.has(symbol) || contractIds.has(contractId)) {
      return {
        status: "invalid",
        message: "合約清單有重複 symbol 或 contract_id。",
      };
    }
    symbols.add(symbol);
    contractIds.add(contractId);
    rows.push({
      symbol,
      contractId,
      displayName,
      assetClass: assetClassRaw,
      currency,
      sessionsAvailable: sessions.map((s) => String(s).trim()),
    });
  }
  return { status: "ready", rows };
}

export function findCatalogRow(
  rows: InstrumentCatalogRow[],
  symbol: string,
): InstrumentCatalogRow | null {
  return rows.find((r) => r.symbol === symbol) ?? null;
}
