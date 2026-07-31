import { load as yamlLoad } from "js-yaml";

import type { InstrumentCatalogRow } from "./catalog/types";
import { findCatalogRow } from "./catalog/parseCatalog";
import { isSketchOrigin } from "./sketch/paths";

export const INSIGHT_STORAGE_KEY = "futures-research.insights.v1";

export type InsightStatus =
  | "unverified"
  | "recording"
  | "supported"
  | "rejected";

export interface InsightRecord {
  schema: "insight.v1";
  insight_id: string;
  origin: string;
  version: number;
  based_on_sketch: string;
  based_on_sketch_origin: "workshop" | "journal-app";
  instrument: string;
  asset_class: string;
  title: string;
  condition: {
    type: string;
    suggested_params: Record<string, unknown>;
  };
  measurement: {
    tag: string;
    hypothesis: string;
  };
  validation_status: InsightStatus;
  source_text: string;
  imported_at: string;
}

interface InsightStoreSnapshot {
  schema: "insight_store.v1";
  items: InsightRecord[];
}

export interface InsightImportContext {
  catalogRows: InstrumentCatalogRow[] | null;
  catalogReady: boolean;
  /** When sketch detail is ready: must exact match instrument AND class. */
  sketchMatch?: {
    instrument: string;
    assetClass: string;
  } | null;
  /** exact 404: warning only for sketch; catalog still required. */
  sketchMissing?: boolean;
  /** 5xx/network/invalid sketch read: fail-closed, zero write. */
  sketchError?: string | null;
  /** Server sketch legacy incomplete (no local-match): fail-closed. */
  sketchLegacyIncomplete?: string | null;
  /** Sketch still loading: fail-closed. */
  sketchLoading?: boolean;
}

function empty(): InsightStoreSnapshot {
  return { schema: "insight_store.v1", items: [] };
}

export function readInsightStore(
  storage: Storage = localStorage,
): InsightStoreSnapshot {
  try {
    const raw = storage.getItem(INSIGHT_STORAGE_KEY);
    if (!raw) {
      return empty();
    }
    const parsed = JSON.parse(raw) as InsightStoreSnapshot;
    if (parsed.schema !== "insight_store.v1" || !Array.isArray(parsed.items)) {
      return empty();
    }
    return parsed;
  } catch {
    return empty();
  }
}

function writeInsightStore(
  snap: InsightStoreSnapshot,
  storage: Storage = localStorage,
): void {
  storage.setItem(INSIGHT_STORAGE_KEY, JSON.stringify(snap));
}

export function listInsights(storage: Storage = localStorage): InsightRecord[] {
  return [...readInsightStore(storage).items].sort((a, b) =>
    a.imported_at < b.imported_at ? 1 : -1,
  );
}

/**
 * Parse only — builds candidate. Does NOT write.
 * Validates composite lineage + instrument/class presence.
 */
export function parseInsightYaml(sourceText: string): InsightRecord {
  const doc = yamlLoad(sourceText) as Record<string, unknown> | null;
  if (!doc || typeof doc !== "object") {
    throw new Error("無法解析 insight YAML");
  }
  if (doc.schema !== "insight.v1") {
    throw new Error(
      `schema 必須係 insight.v1，而家係 ${String(doc.schema ?? "缺席")}`,
    );
  }
  const insightId = String(doc.insight_id ?? "").trim();
  if (!insightId) {
    throw new Error("缺少 insight_id");
  }
  const origin = String(doc.origin ?? "").trim();
  if (!origin || !isSketchOrigin(origin)) {
    throw new Error(
      "origin 必須係 workshop 或 journal-app（based_on_sketch_origin 同源規則）",
    );
  }
  const basedOnSketch =
    typeof doc.based_on_sketch === "string" ? doc.based_on_sketch.trim() : "";
  const basedOnOriginRaw =
    typeof doc.based_on_sketch_origin === "string"
      ? doc.based_on_sketch_origin.trim()
      : "";
  if (!basedOnSketch || !basedOnOriginRaw) {
    throw new Error(
      "based_on_sketch 同 based_on_sketch_origin 成對必填（missing/null/blank 唔接受）",
    );
  }
  if (!isSketchOrigin(basedOnOriginRaw)) {
    throw new Error(
      `based_on_sketch_origin 非法「${basedOnOriginRaw}」（只准 workshop／journal-app）`,
    );
  }
  const instrument =
    typeof doc.instrument === "string" && doc.instrument.trim()
      ? doc.instrument.trim()
      : "";
  const assetClass =
    typeof doc.asset_class === "string" && doc.asset_class.trim()
      ? doc.asset_class.trim()
      : "";
  if (!instrument || !assetClass) {
    throw new Error(
      "insight.v1 必須有 instrument 同 asset_class（同草圖/catalog exact）",
    );
  }
  const condition = (doc.condition ?? {}) as Record<string, unknown>;
  const measurement = (doc.measurement ?? {}) as Record<string, unknown>;
  const status = (doc.validation_status as InsightStatus) || "unverified";
  return {
    schema: "insight.v1",
    insight_id: insightId,
    origin,
    version: Number(doc.version ?? 1),
    based_on_sketch: basedOnSketch,
    based_on_sketch_origin: basedOnOriginRaw,
    instrument,
    asset_class: assetClass,
    title: String(doc.title ?? insightId),
    condition: {
      type: String(condition.type ?? "unknown"),
      suggested_params:
        (condition.suggested_params as Record<string, unknown>) ?? {},
    },
    measurement: {
      tag: String(measurement.tag ?? ""),
      hypothesis: String(measurement.hypothesis ?? ""),
    },
    validation_status: status,
    source_text: sourceText,
    imported_at: new Date().toISOString(),
  };
}

/** Catalog + sketch match validation — zero write. */
export function validateInsightCandidate(
  candidate: InsightRecord,
  ctx: InsightImportContext,
): string[] {
  const errors: string[] = [];
  if (ctx.sketchLoading) {
    errors.push("原草圖載入中——唔可以入庫");
  }
  if (ctx.sketchError) {
    errors.push(`原草圖讀取失敗：${ctx.sketchError}`);
  }
  if (ctx.sketchLegacyIncomplete) {
    errors.push(
      `原草圖 legacy／incomplete：${ctx.sketchLegacyIncomplete}`,
    );
  }
  if (!ctx.catalogReady || !ctx.catalogRows) {
    errors.push("合約清單未就緒——唔可以入庫");
    return errors;
  }
  const row = findCatalogRow(ctx.catalogRows, candidate.instrument);
  if (!row) {
    errors.push(`instrument「${candidate.instrument}」唔喺 catalog`);
  } else if (row.assetClass !== candidate.asset_class) {
    errors.push(
      `asset_class「${candidate.asset_class}」同 catalog「${row.assetClass}」唔一致`,
    );
  }
  if (ctx.sketchMatch) {
    if (ctx.sketchMatch.instrument !== candidate.instrument) {
      errors.push(
        `instrument「${candidate.instrument}」同原草圖「${ctx.sketchMatch.instrument}」唔一致`,
      );
    }
    // Unconditional class exact match when ready (D16/D17)
    if (ctx.sketchMatch.assetClass !== candidate.asset_class) {
      errors.push(
        `asset_class「${candidate.asset_class}」同原草圖「${ctx.sketchMatch.assetClass}」唔一致`,
      );
    }
  }
  // sketchMissing: external package warning only — catalog still required (no extra error)
  return errors;
}

/**
 * Validate fully then single atomic write.
 * On any failure: zero write, throws.
 * Identity: (origin, insight_id, version) — different origin does not overwrite.
 */
export function importInsight(
  sourceText: string,
  storage: Storage = localStorage,
  ctx?: InsightImportContext,
): InsightRecord {
  const candidate = parseInsightYaml(sourceText);
  if (ctx) {
    const errs = validateInsightCandidate(candidate, ctx);
    if (errs.length > 0) {
      throw new Error(errs.join(" · "));
    }
  }
  const snap = readInsightStore(storage);
  const others = snap.items.filter(
    (i) =>
      !(
        i.origin === candidate.origin &&
        i.insight_id === candidate.insight_id &&
        i.version === candidate.version
      ),
  );
  writeInsightStore({ ...snap, items: [...others, candidate] }, storage);
  return candidate;
}

export function updateInsightStatus(
  insightId: string,
  version: number,
  status: InsightStatus,
  storage: Storage = localStorage,
  origin?: string,
): InsightRecord | null {
  const snap = readInsightStore(storage);
  const idx = snap.items.findIndex(
    (i) =>
      i.insight_id === insightId &&
      i.version === version &&
      (origin === undefined || i.origin === origin),
  );
  if (idx < 0) {
    return null;
  }
  const updated = { ...snap.items[idx], validation_status: status };
  const items = [...snap.items];
  items[idx] = updated;
  writeInsightStore({ ...snap, items }, storage);
  return updated;
}

export function deleteInsight(
  insightId: string,
  version: number,
  storage: Storage = localStorage,
  origin?: string,
): void {
  const snap = readInsightStore(storage);
  writeInsightStore(
    {
      ...snap,
      items: snap.items.filter(
        (i) =>
          !(
            i.insight_id === insightId &&
            i.version === version &&
            (origin === undefined || i.origin === origin)
          ),
      ),
    },
    storage,
  );
}
