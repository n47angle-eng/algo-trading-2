/**
 * Owner-review only instrument catalog — isolated from live coverage API.
 * Values match docs/05 controlled mapping; not used as normal-mode fallback.
 */

import type { InstrumentCatalogRow } from "./types";

export const OWNER_REVIEW_CATALOG: InstrumentCatalogRow[] = [
  {
    symbol: "NQ",
    contractId: "NQ-202609-CME",
    displayName: "E-mini Nasdaq-100",
    assetClass: "equity_index_futures",
    currency: "USD",
    sessionsAvailable: ["rth", "eth"],
  },
  {
    symbol: "YM",
    contractId: "YM-202609-CBOT",
    displayName: "E-mini Dow",
    assetClass: "equity_index_futures",
    currency: "USD",
    sessionsAvailable: ["rth", "eth"],
  },
  {
    symbol: "GC",
    contractId: "GC-202608-COMEX",
    displayName: "Gold",
    assetClass: "commodity_futures",
    currency: "USD",
    sessionsAvailable: ["rth", "eth"],
  },
];

/** Response shape matching coverage API with additive catalog fields. */
export function ownerReviewCoverageBody(): {
  schema: string;
  count: number;
  contracts: Array<Record<string, unknown>>;
} {
  return {
    schema: "data_coverage.v1",
    count: OWNER_REVIEW_CATALOG.length,
    contracts: OWNER_REVIEW_CATALOG.map((r) => ({
      symbol: r.symbol,
      contract_id: r.contractId,
      display_name: r.displayName,
      asset_class: r.assetClass,
      currency: r.currency,
      sessions_available: r.sessionsAvailable,
      partition_count: 1,
      bar_count: 100,
      first_timestamp: null,
      last_timestamp: null,
      roll_blackout_dates: [],
      owner_excluded_dates: [],
      quality: {
        report_count: 0,
        latest_error_count: null,
        latest_report_id: null,
      },
    })),
  };
}
