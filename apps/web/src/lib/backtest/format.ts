/** Owner-facing labels — banned technical terms must not appear in UI. */

export function sessionLabel(session: string): string {
  const s = session.toLowerCase();
  if (s === "eth") {
    return "夜盤全時段";
  }
  if (s === "rth") {
    return "美股時段";
  }
  return session;
}

/** Neutral when catalog / raw name cannot be resolved truthfully (D10/D11). */
export const STRATEGY_NAME_UNAVAILABLE = "策略名稱暫未取得";

/**
 * Bare technical codes → known Owner-facing names (only when raw is the code alone).
 * Never used as mid-string broad replace (that caused D10 duplication).
 */
const BARE_TECH_FALLBACK: Record<string, string> = {
  "p50 閘": "Trend 回踩 18EMA",
  "p65 閘": "Trend 回踩 90EMA",
  p50_gate: "Trend 回踩 18EMA",
  p65_gate: "Trend 回踩 90EMA",
};

const BARE_TECH_RE = /^(?:p50\s*閘|p65\s*閘|sep_mult)$/i;

/** Exact trailing tech marker, optionally after a separator. */
const TRAILING_TECH_RE =
  /(?:\s*[·•|／/,]\s*)?(?:p50\s*閘|p65\s*閘|sep_mult)\s*$/gi;

/**
 * Sanitize a raw strategy *name* for Owner UI (constraint #16 / D10).
 *
 * - "Trend 回踩 18EMA · p50 閘" → "Trend 回踩 18EMA" (strip exact tail only)
 * - "p50 閘" alone → "Trend 回踩 18EMA" (bare fallback)
 * - never broad-replaces mid-name (avoids "Trend… · Trend…")
 * - empty / unresolvable → STRATEGY_NAME_UNAVAILABLE
 */
export function displayStrategyName(raw: string): string {
  const t = (raw ?? "").trim();
  if (!t) {
    return STRATEGY_NAME_UNAVAILABLE;
  }

  if (BARE_TECH_RE.test(t) || BARE_TECH_FALLBACK[t]) {
    return BARE_TECH_FALLBACK[t] ?? STRATEGY_NAME_UNAVAILABLE;
  }

  let out = t;
  // Strip only exact trailing technical markers (repeat for multiple tails).
  for (let i = 0; i < 4; i++) {
    const next = out.replace(TRAILING_TECH_RE, "").trim();
    if (next === out) {
      break;
    }
    out = next;
  }
  // Clean leftover leading/trailing separators
  out = out
    .replace(/^[·•|／/,]\s*/, "")
    .replace(/\s*[·•|／/,]$/, "")
    .replace(/\s{2,}/g, " ")
    .trim();

  if (!out) {
    return STRATEGY_NAME_UNAVAILABLE;
  }
  // Fail-closed: any remaining banned token → do not invent a name
  if (/p50\s*閘|p65\s*閘|sep_mult/i.test(out)) {
    return STRATEGY_NAME_UNAVAILABLE;
  }
  return out;
}

/**
 * Build strategy_id → Owner-facing name from confirmed catalog (D11).
 */
export function buildStrategyNameCatalog(
  versions: ReadonlyArray<{ strategy_id: string; name: string }>,
): Map<string, string> {
  const map = new Map<string, string>();
  for (const v of versions) {
    if (v.strategy_id) {
      map.set(v.strategy_id, displayStrategyName(v.name));
    }
  }
  return map;
}

export interface ResolvedStrategyLabel {
  /** Main visible text — never a raw technical code or bare ID as primary name. */
  label: string;
  /** Locked version id for tooltip / fine print, if known. */
  versionId: string | null;
}

/**
 * Resolve job/request strategy_version (usually strategy-000N) via catalog.
 * Missing catalog → neutral label + keep id in tooltip (D11).
 */
export function resolveStrategyLabel(
  strategyVersionOrName: string,
  catalog: ReadonlyMap<string, string>,
): ResolvedStrategyLabel {
  const raw = (strategyVersionOrName ?? "").trim();
  if (!raw) {
    return { label: STRATEGY_NAME_UNAVAILABLE, versionId: null };
  }
  if (catalog.has(raw)) {
    return { label: catalog.get(raw)!, versionId: raw };
  }
  // Looks like a locked version id but catalog has no row yet
  if (/^strategy-/i.test(raw)) {
    return { label: STRATEGY_NAME_UNAVAILABLE, versionId: raw };
  }
  // Treat as a raw name string (fixture / legacy)
  return { label: displayStrategyName(raw), versionId: null };
}

export function jobStatusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "等緊";
    case "running":
      return "進行中";
    case "completed":
      return "完成";
    case "failed":
      return "失敗";
    case "cancelled":
      return "已取消";
    case "partial":
      return "部分完成";
    default:
      // Never leak raw technical codes into chrome.
      return "狀態未明";
  }
}

/** Visible UI text must not contain these (constraint #16). */
export const BANNED_UI_TERMS = [
  "validation_run",
  "工程驗證，非策略主張",
  "override",
  "開啟參數 override",
  "range_start",
  "range_end",
  "p50 閘",
  "p65 閘",
  "sep_mult",
] as const;

/** Words that are banned as UI units/labels (case-sensitive scan helpers). */
export function scanBannedVisibleText(text: string): string[] {
  const hits: string[] = [];
  for (const term of BANNED_UI_TERMS) {
    if (text.includes(term)) {
      hits.push(term);
    }
  }
  // "batch" / "BATCH" as visible chrome (not inside tooltips of IDs)
  if (/\bbatch\b/i.test(text) && !text.includes("batch_id_hidden")) {
    // allow only if it's an internal test marker
    if (!text.includes("__allow_batch_in_test__")) {
      hits.push("batch");
    }
  }
  // bare "runs" as unit — crude but catches "4 runs"
  if (/\d+\s*runs\b/i.test(text)) {
    hits.push("runs-as-unit");
  }
  // bare rth/eth as UI words
  if (/\brth\b/i.test(text) || /\beth\b/i.test(text)) {
    // session labels should already be translated; raw codes are banned
    if (!text.includes("夜盤") && !text.includes("美股時段")) {
      hits.push("rth/eth");
    }
  }
  return hits;
}

export function estimateMinutes(unitCount: number): number {
  return Math.max(1, unitCount * 3);
}
