/**
 * Owner-review fixture application — deterministic, no backend, memory storage only.
 */

import type { SketchDetailResponse } from "../../api/client";
import { OWNER_REVIEW_CATALOG, ownerReviewCoverageBody } from "../catalog/fixtureCatalog";
import { parseInstrumentCatalog } from "../catalog/parseCatalog";
import type { CatalogState } from "../catalog/types";
import {
  INSIGHT_STORAGE_KEY,
  type InsightRecord,
} from "../insightStore";
import { chartSlotFileName } from "../sketch/paths";
import { TINY_PNG_DATA_URL } from "../sketch/pngGate";
import {
  SKETCH_STORAGE_KEY,
  type SketchDraft,
  type SketchChartSlot,
} from "../sketch/types";
import {
  fixtureInvalidNqGcStrategy,
  fixturePrimaryOnlyStrategy,
  fixtureValidNqYmStrategy,
} from "./ownerReviewFixtures";

export type OwnerReviewFixtureId =
  | "fresh"
  | "export_ready"
  | "valid_nq_ym"
  | "invalid_nq_gc"
  | "primary_only"
  | "local_404"
  | "server_5xx"
  | "legacy_unexported"
  | "legacy_exported"
  | "catalog_loading"
  | "catalog_empty"
  | "catalog_503"
  | "catalog_invalid";

const SKETCH_ID = "sketch-20260727-01";

function emptyCharts(): SketchChartSlot[] {
  return (["D", "1H", "30m", "5m"] as const).map((tf, i) => ({
    slotId: `slot-${i + 1}`,
    timeframe: tf,
    role: (["bias", "mid", "auxiliary", "entry"] as const)[i],
    indicatorsShown: ["ema18"] as SketchChartSlot["indicatorsShown"],
    ownerView: "",
    imageDataUrl: null,
    imageFileName: null,
  }));
}

/** Four views + optional real tiny PNG (export-ready signature). */
function filledCharts(withImage: boolean): SketchChartSlot[] {
  return emptyCharts().map((c, i) => ({
    ...c,
    ownerView: `判斷 ${i + 1}`,
    imageDataUrl: withImage ? TINY_PNG_DATA_URL : null,
    imageFileName: withImage ? chartSlotFileName(i) : null,
  }));
}

function baseDraft(over: Partial<SketchDraft> = {}): SketchDraft {
  const now = "2026-07-27T12:00:00.000Z";
  return {
    schema: "sketch.v1",
    sketchId: SKETCH_ID,
    kind: "strategy",
    origin: "workshop",
    chartSource: "screenshot",
    instructionsTemplate: "instructions.v1",
    title: "NQ 趨勢日回踩 90EMA",
    rationale: "測試理據",
    charts: filledCharts(false),
    instrument: "NQ",
    assetClass: "equity_index_futures",
    instrumentLocked: false,
    instrumentLegacy: false,
    created: now,
    updatedAt: now,
    exported: false,
    exportedAt: null,
    ...over,
  };
}

function detailFromDraft(draft: SketchDraft): SketchDetailResponse {
  return {
    schema: "sketch_detail.v1",
    meta: {
      schema: "sketch.v1",
      sketch_id: draft.sketchId,
      kind: draft.kind,
      origin: "workshop",
      chart_source: "screenshot",
      instructions_template: "instructions.v1",
      instrument: draft.instrument ?? "NQ",
      asset_class: draft.assetClass ?? "equity_index_futures",
      created: "2026-07-27",
      title: draft.title,
      rationale: draft.rationale,
      charts: draft.charts.map((c, i) => ({
        file: chartSlotFileName(i),
        timeframe: c.timeframe,
        role: c.role,
        indicators_shown: [...c.indicatorsShown],
        owner_view: c.ownerView,
      })),
    },
    instructions_markdown: "# fixture",
    images: draft.charts.map((_c, i) => ({
      file: chartSlotFileName(i),
      url: "/fixture.png",
      content_type: "image/png",
      byte_count: 10,
      sha256: "a".repeat(64),
    })),
  };
}

export function ownerReviewCatalogState(
  mode: "ready" | "loading" | "empty" | "error" | "invalid",
): CatalogState {
  switch (mode) {
    case "loading":
      return { status: "loading" };
    case "empty":
      return {
        status: "empty",
        message: "未有已設定合約——暫時唔可以揀 instrument。",
      };
    case "error":
      return {
        status: "error",
        message: "暫時讀唔到合約清單：HTTP 503",
      };
    case "invalid":
      return {
        status: "invalid",
        message:
          "合約清單缺 display_name／asset_class／currency／sessions_available 等必要欄——唔會用硬編碼名稱頂替。",
      };
    default:
      return parseInstrumentCatalog(ownerReviewCoverageBody());
  }
}

export interface FixtureApplyResult {
  catalog: CatalogState;
  detailMap: Record<string, SketchDetailResponse | "404" | "500">;
  quantifyYaml: string | null;
}

/** Reset memory store and apply one fixture deterministically. */
export function applyOwnerReviewFixture(
  id: OwnerReviewFixtureId,
  memory: Storage,
): FixtureApplyResult {
  memory.clear();
  const detailMap: Record<string, SketchDetailResponse | "404" | "500"> = {};
  let quantifyYaml: string | null = null;
  let catalog = ownerReviewCatalogState("ready");

  const writeSketch = (drafts: SketchDraft[], activeId: string | null) => {
    memory.setItem(
      SKETCH_STORAGE_KEY,
      JSON.stringify({
        schema: "sketch_store.v1",
        drafts,
        activeId,
        packages: {},
      }),
    );
  };

  switch (id) {
    case "fresh": {
      const d = baseDraft({
        instrument: null,
        assetClass: null,
        instrumentLocked: false,
        instrumentLegacy: false,
        title: "",
        rationale: "",
        charts: emptyCharts(),
      });
      writeSketch([d], d.sketchId);
      break;
    }
    case "export_ready": {
      // D1: built-in four real tiny PNGs + rationale — export without local files.
      const d = baseDraft({
        instrumentLocked: true,
        charts: filledCharts(true),
        exported: false,
        exportedAt: null,
        title: "Owner-review export ready",
        rationale: "內建四張合法 PNG 同理據，可測 ready gate／local export",
      });
      writeSketch([d], d.sketchId);
      break;
    }
    case "valid_nq_ym": {
      const d = baseDraft({
        instrumentLocked: true,
        charts: filledCharts(true),
        exported: true,
        exportedAt: "2026-07-27T12:00:00.000Z",
      });
      writeSketch([d], d.sketchId);
      detailMap[`workshop::${d.sketchId}`] = detailFromDraft(d);
      quantifyYaml = fixtureValidNqYmStrategy(d.sketchId);
      break;
    }
    case "invalid_nq_gc": {
      const d = baseDraft({
        instrumentLocked: true,
        charts: filledCharts(true),
        exported: true,
        exportedAt: "2026-07-27T12:00:00.000Z",
      });
      writeSketch([d], d.sketchId);
      detailMap[`workshop::${d.sketchId}`] = detailFromDraft(d);
      quantifyYaml = fixtureInvalidNqGcStrategy(d.sketchId);
      break;
    }
    case "primary_only": {
      const d = baseDraft({
        instrumentLocked: true,
        charts: filledCharts(true),
        exported: true,
        exportedAt: "2026-07-27T12:00:00.000Z",
      });
      writeSketch([d], d.sketchId);
      detailMap[`workshop::${d.sketchId}`] = detailFromDraft(d);
      quantifyYaml = fixturePrimaryOnlyStrategy(d.sketchId);
      break;
    }
    case "local_404": {
      const d = baseDraft({ instrumentLocked: true, exported: true, exportedAt: "x" });
      writeSketch([d], d.sketchId);
      // Detail map empty → lookup throws 404 for strategy lineage id
      quantifyYaml = fixtureValidNqYmStrategy("sketch-missing-404");
      break;
    }
    case "server_5xx": {
      const d = baseDraft({ instrumentLocked: true, exported: true, exportedAt: "x" });
      writeSketch([d], d.sketchId);
      detailMap[`workshop::${d.sketchId}`] = "500";
      quantifyYaml = fixtureValidNqYmStrategy(d.sketchId);
      break;
    }
    case "legacy_unexported": {
      // Simulate pre-instrument migration: instrumentLegacy true, missing fields, has image
      const d = baseDraft({
        instrument: null,
        assetClass: null,
        instrumentLocked: true,
        instrumentLegacy: true,
        charts: filledCharts(true),
        exported: false,
        title: "舊草稿",
      });
      writeSketch([d], d.sketchId);
      break;
    }
    case "legacy_exported": {
      const d = baseDraft({
        instrument: null,
        assetClass: null,
        instrumentLocked: true,
        instrumentLegacy: true,
        charts: filledCharts(true),
        exported: true,
        exportedAt: "2026-07-20T00:00:00.000Z",
        title: "舊已匯出",
      });
      writeSketch([d], d.sketchId);
      break;
    }
    case "catalog_loading":
      catalog = ownerReviewCatalogState("loading");
      writeSketch(
        [
          baseDraft({
            instrument: null,
            assetClass: null,
            charts: emptyCharts(),
            title: "",
          }),
        ],
        SKETCH_ID,
      );
      break;
    case "catalog_empty":
      catalog = ownerReviewCatalogState("empty");
      writeSketch(
        [baseDraft({ instrument: null, assetClass: null, charts: emptyCharts(), title: "" })],
        SKETCH_ID,
      );
      break;
    case "catalog_503":
      catalog = ownerReviewCatalogState("error");
      writeSketch(
        [baseDraft({ instrument: null, assetClass: null, charts: emptyCharts(), title: "" })],
        SKETCH_ID,
      );
      break;
    case "catalog_invalid":
      catalog = ownerReviewCatalogState("invalid");
      writeSketch(
        [baseDraft({ instrument: null, assetClass: null, charts: emptyCharts(), title: "" })],
        SKETCH_ID,
      );
      break;
    default:
      break;
  }

  // Ensure insights key empty in memory
  if (!memory.getItem(INSIGHT_STORAGE_KEY)) {
    memory.setItem(
      INSIGHT_STORAGE_KEY,
      JSON.stringify({ schema: "insight_store.v1", items: [] as InsightRecord[] }),
    );
  }

  void OWNER_REVIEW_CATALOG;
  return { catalog, detailMap, quantifyYaml };
}
