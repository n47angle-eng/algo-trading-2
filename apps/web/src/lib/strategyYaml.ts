import { dump as yamlDump, load as yamlLoad } from "js-yaml";

import { isSketchOrigin, type SketchOrigin } from "./sketch/paths";

/** Composite sketch lineage from strategy.v1 (docs/05 §1.1a). */
export type SketchLineage =
  | {
      status: "complete";
      origin: SketchOrigin;
      sketchId: string;
    }
  | {
      status: "incomplete";
      origin: string | null;
      sketchId: string | null;
      /** Owner-facing reason (not raw snake_case dump). */
      reason: string;
    };

/**
 * Parse meta.based_on_sketch_origin + meta.based_on_sketch as one composite ref.
 * YAML structure only — no broad file-wide regex (avoids comment / nested hits).
 */
export function extractBasedOnSketchLineage(
  sourceText: string,
): SketchLineage {
  try {
    const doc = yamlLoad(sourceText) as Record<string, unknown> | null;
    if (!doc || typeof doc !== "object") {
      return {
        status: "incomplete",
        origin: null,
        sketchId: null,
        reason: "策略 YAML 無法解析，讀唔到草圖溯源。",
      };
    }
    const meta =
      doc.meta && typeof doc.meta === "object"
        ? (doc.meta as Record<string, unknown>)
        : null;
    const originRaw =
      meta && typeof meta.based_on_sketch_origin === "string"
        ? meta.based_on_sketch_origin.trim()
        : null;
    const sketchRaw =
      meta && typeof meta.based_on_sketch === "string"
        ? meta.based_on_sketch.trim()
        : null;
    const originOk = originRaw && isSketchOrigin(originRaw) ? originRaw : null;
    const sketchOk = sketchRaw && sketchRaw.length > 0 ? sketchRaw : null;
    if (originOk && sketchOk) {
      return { status: "complete", origin: originOk, sketchId: sketchOk };
    }
    if (!originOk && !sketchOk) {
      return {
        status: "incomplete",
        origin: originRaw,
        sketchId: sketchRaw,
        reason:
          "YAML 缺少完整草圖溯源（meta.based_on_sketch_origin 同 meta.based_on_sketch 必須成對）。",
      };
    }
    if (!originOk) {
      return {
        status: "incomplete",
        origin: originRaw,
        sketchId: sketchOk,
        reason:
          "YAML 有 based_on_sketch 但 based_on_sketch_origin 缺席或非法（只准 workshop／journal-app）。",
      };
    }
    return {
      status: "incomplete",
      origin: originOk,
      sketchId: sketchRaw,
      reason: "YAML 有 based_on_sketch_origin 但 based_on_sketch 缺席或空白。",
    };
  } catch {
    return {
      status: "incomplete",
      origin: null,
      sketchId: null,
      reason: "策略 YAML 無法解析，讀唔到草圖溯源。",
    };
  }
}

/**
 * Sketch id only when composite lineage is complete.
 * Incomplete / id-only / origin-only / invalid → null (Correction A D3).
 * Production lookup must use extractBasedOnSketchLineage, not this helper.
 */
export function extractBasedOnSketch(sourceText: string): string | null {
  const lineage = extractBasedOnSketchLineage(sourceText);
  return lineage.status === "complete" ? lineage.sketchId : null;
}

/** Lineage from StrategyVersion API fields. */
export function lineageFromStrategyFields(
  origin: string | null | undefined,
  sketchId: string | null | undefined,
): SketchLineage {
  const o = typeof origin === "string" ? origin.trim() : "";
  const s = typeof sketchId === "string" ? sketchId.trim() : "";
  if ((o === "workshop" || o === "journal-app") && s.length > 0) {
    return { status: "complete", origin: o, sketchId: s };
  }
  if (!o && !s) {
    return {
      status: "incomplete",
      origin: null,
      sketchId: null,
      reason:
        "策略缺少完整草圖溯源（based_on_sketch_origin 同 based_on_sketch）。",
    };
  }
  return {
    status: "incomplete",
    origin: o || null,
    sketchId: s || null,
    reason: "策略草圖溯源不完整（origin 同 sketch_id 必須成對）。",
  };
}

export function parseYamlDocument(sourceText: string): unknown {
  return yamlLoad(sourceText);
}

export function dumpYamlDocument(doc: unknown): string {
  return yamlDump(doc, {
    lineWidth: 100,
    noRefs: true,
    sortKeys: false,
  });
}

/** Read nested path like regime.sep_mult.value */
export function getByPath(obj: unknown, path: string): unknown {
  const parts = path.split(".");
  let cur: unknown = obj;
  for (const p of parts) {
    if (cur === null || cur === undefined || typeof cur !== "object") {
      return undefined;
    }
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}

/** Set nested path; mutates a deep clone conceptually via reassignment. */
export function setByPath(
  obj: Record<string, unknown>,
  path: string,
  value: unknown,
): void {
  const parts = path.split(".");
  let cur: Record<string, unknown> = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i];
    const next = cur[p];
    if (next === null || typeof next !== "object" || Array.isArray(next)) {
      cur[p] = {};
    }
    cur = cur[p] as Record<string, unknown>;
  }
  cur[parts[parts.length - 1]] = value;
}

/**
 * Apply numeric UI edits onto a strategy document and tag provenance.
 * Returns new YAML source; original string is never mutated.
 *
 * @param parentStrategyId — the version the Owner was editing. Always written to
 *   `meta.based_on` (overwrite, never preserve a grandparent id). Constraint #18.
 */
export function deriveStrategyYaml(
  originalSource: string,
  edits: Array<{ path: string; value: number }>,
  parentStrategyId: string,
): string {
  const doc = yamlLoad(originalSource) as Record<string, unknown>;
  if (!doc || typeof doc !== "object") {
    throw new Error("無法解析策略 YAML");
  }
  if (!parentStrategyId.trim()) {
    throw new Error("derive 需要 parentStrategyId");
  }
  for (const edit of edits) {
    setByPath(doc, edit.path, edit.value);
  }
  // Tag provenance for UI-derived values.
  const prov = Array.isArray(doc.provenance) ? [...doc.provenance] : [];
  for (const edit of edits) {
    const existing = prov.findIndex(
      (p) =>
        p &&
        typeof p === "object" &&
        (p as { path?: string }).path === edit.path,
    );
    const entry = {
      path: edit.path,
      source: "owner_explicit",
      note: "Owner UI 微調",
    };
    if (existing >= 0) {
      prov[existing] = entry;
    } else {
      prov.push(entry);
    }
  }
  doc.provenance = prov;
  // Lineage + display name. based_on MUST be the parent we edited (overwrite).
  const meta =
    doc.meta && typeof doc.meta === "object"
      ? { ...(doc.meta as Record<string, unknown>) }
      : {};
  const name = typeof meta.name === "string" ? meta.name : "strategy";
  if (!name.includes("UI微調")) {
    meta.name = `${name}_UI微調`;
  }
  meta.based_on = parentStrategyId;
  doc.meta = meta;
  return dumpYamlDocument(doc);
}

export function extractUnquantifiedNotes(
  sourceText: string,
): Array<{ note: string; action_needed: string | null }> {
  try {
    const doc = yamlLoad(sourceText) as Record<string, unknown> | null;
    const raw = doc?.unquantified_notes;
    if (!Array.isArray(raw)) {
      return [];
    }
    return raw.map((item) => {
      if (typeof item === "string") {
        return { note: item, action_needed: null };
      }
      if (item && typeof item === "object") {
        const o = item as Record<string, unknown>;
        return {
          note: String(o.note ?? ""),
          action_needed:
            o.action_needed === null || o.action_needed === undefined
              ? null
              : String(o.action_needed),
        };
      }
      return { note: String(item), action_needed: null };
    });
  } catch {
    return [];
  }
}

export function extractRationale(sourceText: string): string {
  try {
    const doc = yamlLoad(sourceText) as Record<string, unknown> | null;
    const r = doc?.rationale;
    return typeof r === "string" ? r : "";
  } catch {
    return "";
  }
}

export function extractProvenance(
  sourceText: string,
): Array<Record<string, unknown>> {
  try {
    const doc = yamlLoad(sourceText) as Record<string, unknown> | null;
    const p = doc?.provenance;
    return Array.isArray(p) ? (p as Array<Record<string, unknown>>) : [];
  } catch {
    return [];
  }
}
