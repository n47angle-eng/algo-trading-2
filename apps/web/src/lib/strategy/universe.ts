/**
 * strategy.v1 universe parse + frontend fail-closed gates (docs/05 §1.1c).
 * Sketch truth is origin-aware server detail — never browser localStorage alone.
 */

import type { InstrumentCatalogRow } from "../catalog/types";
import { findCatalogRow } from "../catalog/parseCatalog";
import type { OwnerSketchLoadState } from "../sketch/useOwnerSketchLoad";

export interface StrategyUniverse {
  primaryInstrument: string;
  assetClass: string;
  contracts: string[];
  expansionRationale: Record<string, string>;
  session: string;
}

export interface UniverseGateIssue {
  path: string;
  message: string;
  fix: string;
}

export type UniverseParseResult =
  | { ok: true; universe: StrategyUniverse }
  | { ok: false; issues: UniverseGateIssue[] };

/**
 * Origin-aware sketch context for gates (from shared load state).
 * Replaces browser-localStorage-only resolution.
 */
export type SketchRefContext =
  | { kind: "none" }
  | { kind: "loading"; origin: string; sketchId: string }
  | { kind: "missing"; sketchId: string; origin: string }
  | { kind: "error"; message: string }
  | { kind: "lineage_incomplete"; reason: string }
  | {
      kind: "ready";
      origin: string;
      sketchId: string;
      instrument: string;
      /** Always present on ready — never optional skip. */
      assetClass: string;
    }
  | {
      kind: "legacy_incomplete";
      sketchId: string;
      origin: string;
      reason: string;
    };

function nonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const t = value.trim();
  return t.length > 0 ? t : null;
}

export function parseStrategyUniverse(doc: unknown): UniverseParseResult {
  if (!doc || typeof doc !== "object") {
    return {
      ok: false,
      issues: [
        {
          path: "universe",
          message: "策略文件無法解析，讀唔到 universe。",
          fix: "請確保 YAML 係合法 strategy.v1。",
        },
      ],
    };
  }
  const root = doc as Record<string, unknown>;
  if (!("universe" in root) || root.universe === null) {
    return {
      ok: false,
      issues: [
        {
          path: "universe",
          message: "缺 universe 段（null 同缺席都唔接受）。",
          fix: "補齊 universe.primary_instrument / asset_class / contracts / expansion_rationale / session。",
        },
      ],
    };
  }
  if (typeof root.universe !== "object" || Array.isArray(root.universe)) {
    return {
      ok: false,
      issues: [
        {
          path: "universe",
          message: "universe 必須係 mapping。",
          fix: "用 YAML mapping 寫 universe。",
        },
      ],
    };
  }
  const u = root.universe as Record<string, unknown>;
  const issues: UniverseGateIssue[] = [];

  const primary = nonEmptyString(u.primary_instrument);
  if (!primary) {
    issues.push({
      path: "universe.primary_instrument",
      message: "primary_instrument 缺席、null 或空白。",
      fix: "填入草圖 primary root symbol（例如 NQ）。",
    });
  }
  const assetClass = nonEmptyString(u.asset_class);
  if (!assetClass) {
    issues.push({
      path: "universe.asset_class",
      message: "asset_class 缺席、null 或空白。",
      fix: "填入 catalog 資產類別（例如 equity_index_futures）。",
    });
  }
  if (!Array.isArray(u.contracts) || u.contracts.length === 0) {
    issues.push({
      path: "universe.contracts",
      message: "contracts 必須係非空字串陣列。",
      fix: "至少列出 primary，例如 [NQ]。",
    });
  }
  const session = nonEmptyString(u.session);
  if (!session) {
    issues.push({
      path: "universe.session",
      message: "session 缺席、null 或空白。",
      fix: "填 rth 或 eth。",
    });
  }
  if (!("expansion_rationale" in u) || u.expansion_rationale === null) {
    issues.push({
      path: "universe.expansion_rationale",
      message: "expansion_rationale 缺席或 null（primary-only 亦要寫 {}）。",
      fix: "primary-only 寫 expansion_rationale: {}；有擴展就每 member 一段非空理由。",
    });
  } else if (
    typeof u.expansion_rationale !== "object" ||
    Array.isArray(u.expansion_rationale)
  ) {
    issues.push({
      path: "universe.expansion_rationale",
      message: "expansion_rationale 必須係 mapping。",
      fix: "用 key: 理由 格式；primary-only 用 {}。",
    });
  }

  if (issues.length > 0) {
    return { ok: false, issues };
  }

  const contracts = (u.contracts as unknown[]).map((c) => String(c).trim());
  if (contracts.some((c) => !c)) {
    return {
      ok: false,
      issues: [
        {
          path: "universe.contracts",
          message: "contracts 含空白 symbol。",
          fix: "移除空白項。",
        },
      ],
    };
  }
  const rationaleRaw = u.expansion_rationale as Record<string, unknown>;
  const expansionRationale: Record<string, string> = {};
  for (const [k, v] of Object.entries(rationaleRaw)) {
    expansionRationale[k] = typeof v === "string" ? v : String(v ?? "");
  }

  return {
    ok: true,
    universe: {
      primaryInstrument: primary!,
      assetClass: assetClass!,
      contracts,
      expansionRationale,
      session: session!,
    },
  };
}

/** Map shared OwnerSketchLoadState → SketchRefContext for gates. */
export function sketchRefFromLoadState(
  state: OwnerSketchLoadState,
): SketchRefContext {
  switch (state.kind) {
    case "idle":
      return { kind: "none" };
    case "lineage":
      return { kind: "lineage_incomplete", reason: state.reason };
    case "loading":
      return {
        kind: "loading",
        origin: state.origin,
        sketchId: state.sketchId,
      };
    case "not_found":
      return {
        kind: "missing",
        sketchId: state.sketchId,
        origin: state.origin,
      };
    case "error":
      return { kind: "error", message: state.message };
    case "ready": {
      const meta = state.detail.meta;
      const hasInst = Object.prototype.hasOwnProperty.call(meta, "instrument");
      const hasClass = Object.prototype.hasOwnProperty.call(meta, "asset_class");
      // Both omitted (legacy) — never invent identity via trim/empty
      if (!hasInst && !hasClass) {
        return {
          kind: "legacy_incomplete",
          sketchId: meta.sketch_id,
          origin: meta.origin,
          reason: "server sketch 缺 instrument／asset_class（legacy）",
        };
      }
      if (
        !hasInst ||
        !hasClass ||
        typeof meta.instrument !== "string" ||
        typeof meta.asset_class !== "string" ||
        meta.instrument === "" ||
        meta.asset_class === ""
      ) {
        return {
          kind: "legacy_incomplete",
          sketchId: meta.sketch_id,
          origin: meta.origin,
          reason: "server sketch instrument／asset_class 不成對",
        };
      }
      // Ready only from exact-validated raw values (no trim)
      return {
        kind: "ready",
        origin: meta.origin,
        sketchId: meta.sketch_id,
        instrument: meta.instrument,
        assetClass: meta.asset_class,
      };
    }
    default:
      return { kind: "none" };
  }
}

export function validateUniverseGates(
  universe: StrategyUniverse,
  catalogRows: InstrumentCatalogRow[] | null,
  sketchRef: SketchRefContext,
): UniverseGateIssue[] {
  const issues: UniverseGateIssue[] = [];

  if (!universe.contracts.includes(universe.primaryInstrument)) {
    issues.push({
      path: "universe.primary_instrument",
      message: `primary「${universe.primaryInstrument}」唔喺 contracts 入面。`,
      fix: `把 ${universe.primaryInstrument} 加入 contracts，或改 primary。`,
    });
  }

  if (!catalogRows) {
    issues.push({
      path: "catalog",
      message: "合約清單未就緒——無法驗證 universe members。",
      fix: "等 catalog 載入成功，或檢查 coverage API 是否回傳完整欄位。",
    });
  } else {
    for (const symbol of universe.contracts) {
      const row = findCatalogRow(catalogRows, symbol);
      if (!row) {
        issues.push({
          path: `universe.contracts[${symbol}]`,
          message: `未知 symbol「${symbol}」——唔喺合約清單。`,
          fix: "只可用 catalog 已知 root symbol，或先擴充 backend catalog。",
        });
        continue;
      }
      if (row.assetClass !== universe.assetClass) {
        issues.push({
          path: `universe.contracts[${symbol}]`,
          message: `「${symbol}」catalog class 係 ${row.assetClass}，同宣告 asset_class ${universe.assetClass} 唔一致。`,
          fix: "universe 只可包含同 asset class 嘅 markets；跨 class 必須拆策略。",
        });
      }
      if (!row.sessionsAvailable.includes(universe.session)) {
        issues.push({
          path: `universe.session`,
          message: `「${symbol}」唔支援 session「${universe.session}」。`,
          fix: `改用 ${row.sessionsAvailable.join("／")}，或移除唔支援嘅 member。`,
        });
      }
      if (row.currency !== "USD") {
        issues.push({
          path: `universe.contracts[${symbol}]`,
          message: `「${symbol}」幣別係 ${row.currency}——MVP 只接受 USD。`,
          fix: "移除非 USD member；跨幣別要等 FX conversion。",
        });
      }
    }
    const primaryRow = findCatalogRow(
      catalogRows,
      universe.primaryInstrument,
    );
    if (primaryRow && primaryRow.assetClass !== universe.assetClass) {
      issues.push({
        path: "universe.asset_class",
        message: `宣告 asset_class「${universe.assetClass}」同 primary catalog「${primaryRow.assetClass}」唔一致。`,
        fix: `改 asset_class 做 ${primaryRow.assetClass}。`,
      });
    }
  }

  const expectedKeys = new Set(
    universe.contracts.filter((c) => c !== universe.primaryInstrument),
  );
  const actualKeys = new Set(Object.keys(universe.expansionRationale));
  for (const k of expectedKeys) {
    if (!actualKeys.has(k)) {
      issues.push({
        path: `universe.expansion_rationale.${k}`,
        message: `缺「${k}」嘅 expansion_rationale。`,
        fix: `為新增 member ${k} 寫一段非空理由。`,
      });
    } else {
      const val = universe.expansionRationale[k]?.trim() ?? "";
      if (!val) {
        issues.push({
          path: `universe.expansion_rationale.${k}`,
          message: `「${k}」嘅 expansion_rationale 空白。`,
          fix: "填寫人話擴展理由。",
        });
      }
    }
  }
  for (const k of actualKeys) {
    if (!expectedKeys.has(k)) {
      issues.push({
        path: `universe.expansion_rationale.${k}`,
        message:
          k === universe.primaryInstrument
            ? `primary「${k}」唔應該出現喺 expansion_rationale（primary-only 用 {}）。`
            : `expansion_rationale 有多餘 key「${k}」（唔喺 contracts−primary）。`,
        fix: "rationale keys 必須恰好等於 contracts 減 primary。",
      });
    }
  }

  // Origin-aware sketch ref (D9)
  if (sketchRef.kind === "loading") {
    issues.push({
      path: "sketch_ref",
      message: "原草圖載入中——確認 disabled。",
      fix: "等 origin-aware sketch 讀取完成。",
    });
  } else if (sketchRef.kind === "error") {
    issues.push({
      path: "sketch_ref",
      message: `原草圖讀取失敗：${sketchRef.message}`,
      fix: "唔當 404。請檢查網絡／server 後再驗證。",
    });
  } else if (sketchRef.kind === "lineage_incomplete") {
    issues.push({
      path: "meta.based_on_sketch",
      message: sketchRef.reason,
      fix: "補齊 based_on_sketch_origin 同 based_on_sketch 成對。",
    });
  } else if (sketchRef.kind === "legacy_incomplete") {
    issues.push({
      path: "sketch_ref",
      message: `原草圖 ${sketchRef.sketchId}（${sketchRef.origin}）legacy／incomplete：${sketchRef.reason}`,
      fix: "唔好當 missing。請用完整 instrument/class 草圖。",
    });
  } else if (sketchRef.kind === "ready") {
    if (sketchRef.instrument !== universe.primaryInstrument) {
      issues.push({
        path: "universe.primary_instrument",
        message: `strategy primary「${universe.primaryInstrument}」同原草圖「${sketchRef.instrument}」（${sketchRef.origin}/${sketchRef.sketchId}）唔一致。`,
        fix: "primary 必須 exact match 原草圖 instrument。",
      });
    }
    // Unconditional class compare — never skip when ready (D16)
    if (sketchRef.assetClass !== universe.assetClass) {
      issues.push({
        path: "universe.asset_class",
        message: `strategy asset_class「${universe.assetClass}」同原草圖「${sketchRef.assetClass}」唔一致。`,
        fix: "asset_class 必須 exact match 原草圖。",
      });
    }
  }
  // kind missing: warning only in UI — no issue

  return issues;
}
