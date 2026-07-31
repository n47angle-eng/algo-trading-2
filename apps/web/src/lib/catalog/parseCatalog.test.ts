import { describe, expect, it } from "vitest";

import { ownerReviewCoverageBody } from "./fixtureCatalog";
import { findCatalogRow, parseInstrumentCatalog } from "./parseCatalog";

describe("parseInstrumentCatalog", () => {
  it("accepts complete owner-review shaped coverage body", () => {
    const state = parseInstrumentCatalog(ownerReviewCoverageBody());
    expect(state.status).toBe("ready");
    if (state.status !== "ready") return;
    expect(state.rows).toHaveLength(3);
    expect(findCatalogRow(state.rows, "NQ")?.displayName).toBe(
      "E-mini Nasdaq-100",
    );
    expect(findCatalogRow(state.rows, "NQ")?.assetClass).toBe(
      "equity_index_futures",
    );
    expect(findCatalogRow(state.rows, "GC")?.assetClass).toBe(
      "commodity_futures",
    );
  });

  it("fails closed when display_name missing (no hardcode fallback)", () => {
    const body = ownerReviewCoverageBody();
    delete body.contracts[0].display_name;
    const state = parseInstrumentCatalog(body);
    expect(state.status).toBe("invalid");
  });

  it("fails closed when asset_class is null", () => {
    const body = ownerReviewCoverageBody();
    body.contracts[0].asset_class = null;
    const state = parseInstrumentCatalog(body);
    expect(state.status).toBe("invalid");
  });

  it("fails closed on duplicate symbol", () => {
    const body = ownerReviewCoverageBody();
    body.contracts.push({ ...body.contracts[0], contract_id: "DUP-1" });
    const state = parseInstrumentCatalog(body);
    expect(state.status).toBe("invalid");
    if (state.status === "invalid") {
      expect(state.message).toMatch(/重複/);
    }
  });

  it("fails closed on unknown asset_class", () => {
    const body = ownerReviewCoverageBody();
    body.contracts[0].asset_class = "crypto_perp";
    const state = parseInstrumentCatalog(body);
    expect(state.status).toBe("invalid");
    if (state.status === "invalid") {
      expect(state.message).toMatch(/未知 asset_class/);
    }
  });

  it("returns empty when contracts array is empty", () => {
    const state = parseInstrumentCatalog({
      schema: "data_coverage.v1",
      count: 0,
      contracts: [],
    });
    expect(state.status).toBe("empty");
  });

  it("rejects wrong schema", () => {
    const state = parseInstrumentCatalog({
      schema: "other.v1",
      contracts: [],
    });
    expect(state.status).toBe("invalid");
  });

  it("rejects coverage without additive catalog fields (live API today)", () => {
    // Simulates current backend rows that only have symbol/contract_id/quality
    const state = parseInstrumentCatalog({
      schema: "data_coverage.v1",
      count: 1,
      contracts: [
        {
          symbol: "NQ",
          contract_id: "NQ-202609-CME",
          partition_count: 1,
          bar_count: 10,
        },
      ],
    });
    expect(state.status).toBe("invalid");
  });
});
