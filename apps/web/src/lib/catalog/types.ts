/** Canonical instrument catalog rows for P2 (from coverage API or owner-review fixture). */

export type AssetClass =
  | "equity_index_futures"
  | "commodity_futures";

export const KNOWN_ASSET_CLASSES: readonly AssetClass[] = [
  "equity_index_futures",
  "commodity_futures",
] as const;

export interface InstrumentCatalogRow {
  symbol: string;
  contractId: string;
  displayName: string;
  assetClass: AssetClass;
  currency: string;
  sessionsAvailable: string[];
}

export type CatalogState =
  | { status: "loading" }
  | { status: "ready"; rows: InstrumentCatalogRow[] }
  | { status: "empty"; message: string }
  | { status: "error"; message: string }
  | { status: "invalid"; message: string };

export function isAssetClass(value: string): value is AssetClass {
  return (KNOWN_ASSET_CLASSES as readonly string[]).includes(value);
}

/** Human label for asset class enum — not symbol-specific hardcodes. */
export function assetClassLabel(assetClass: string): string {
  switch (assetClass) {
    case "equity_index_futures":
      return "股票指數期貨";
    case "commodity_futures":
      return "商品期貨";
    default:
      return "未知資產類別";
  }
}
