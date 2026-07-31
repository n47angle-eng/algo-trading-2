import type { CatalogState } from "../catalog/types";
import { validateDraftAgainstCatalog } from "./catalogExportGate";
import { canExportSketch, exportBlockingReasons } from "./exportReadiness";
import { nextSketchId } from "./id";
import {
  assertInsightInstructions,
  assertInstructionsEmbedsSchema,
  assertInstructionsSelfContained,
  emitInstructionsMd,
} from "./instructions";
import { emitMetaYaml } from "./metaYaml";
import { chartSlotFileName, sketchRelativeDir } from "./paths";
import { buildTerminalOpener } from "./terminalOpener";
import {
  SKETCH_STORAGE_KEY,
  type IndicatorToken,
  type SketchChartSlot,
  type SketchDraft,
  type SketchPackagePreview,
} from "./types";

interface SketchStoreSnapshot {
  schema: "sketch_store.v1";
  drafts: SketchDraft[];
  /** sketchId currently open in the editor. */
  activeId: string | null;
  packages: Record<string, SketchPackagePreview>;
}

function emptyCharts(): SketchChartSlot[] {
  const defaults: Array<{
    timeframe: string;
    role: SketchChartSlot["role"];
    indicatorsShown: IndicatorToken[];
  }> = [
    { timeframe: "D", role: "bias", indicatorsShown: ["ema18", "ema90"] },
    { timeframe: "1H", role: "mid", indicatorsShown: ["ema18", "ema90"] },
    { timeframe: "30m", role: "auxiliary", indicatorsShown: ["ema18"] },
    { timeframe: "5m", role: "entry", indicatorsShown: ["ema18", "ema90"] },
  ];
  return defaults.map((d, i) => ({
    slotId: `slot-${i + 1}`,
    timeframe: d.timeframe,
    role: d.role,
    indicatorsShown: [...d.indicatorsShown],
    ownerView: "",
    imageDataUrl: null,
    imageFileName: null,
  }));
}

export function createEmptyDraft(
  existingIds: readonly string[],
  now: Date = new Date(),
  kind: SketchDraft["kind"] = "strategy",
): SketchDraft {
  const iso = now.toISOString();
  return {
    schema: "sketch.v1",
    sketchId: nextSketchId(existingIds, now),
    kind,
    origin: "workshop",
    chartSource: "screenshot",
    instructionsTemplate: "instructions.v1",
    title: "",
    rationale: "",
    charts: emptyCharts(),
    instrument: null,
    assetClass: null,
    instrumentLocked: false,
    instrumentLegacy: false,
    created: iso,
    updatedAt: iso,
    exported: false,
    exportedAt: null,
  };
}

function emptySnapshot(): SketchStoreSnapshot {
  return {
    schema: "sketch_store.v1",
    drafts: [],
    activeId: null,
    packages: {},
  };
}

/** Additive migration for instrument fields (local browser state only). */
export function migrateDraftShape(raw: SketchDraft): SketchDraft {
  const any = raw as SketchDraft & Record<string, unknown>;
  const hasImage = ((any.charts as SketchChartSlot[] | undefined) ?? []).some(
    (c) => Boolean(c.imageDataUrl),
  );
  // Pre-instrument storage shape: no instrument key at all.
  const fromPreInstrumentSchema =
    Boolean(any.instrumentLegacy) || !Object.prototype.hasOwnProperty.call(any, "instrument");
  const instrument =
    typeof any.instrument === "string" && any.instrument.trim()
      ? any.instrument.trim()
      : null;
  const assetClass =
    typeof any.assetClass === "string" && any.assetClass.trim()
      ? any.assetClass.trim()
      : null;
  return {
    ...(any as SketchDraft),
    instrument,
    assetClass,
    // Hard invariant: any image ⇒ locked
    instrumentLocked: Boolean(any.instrumentLocked) || hasImage,
    instrumentLegacy: fromPreInstrumentSchema,
  };
}

export function draftHasAnyImage(draft: SketchDraft): boolean {
  return draft.charts.some((c) => Boolean(c.imageDataUrl));
}

/** Attach image: lock instrument if already selected; never unlock later. */
export function applyChartChange(
  draft: SketchDraft,
  nextChart: SketchChartSlot,
): SketchDraft {
  const charts = draft.charts.map((c) =>
    c.slotId === nextChart.slotId ? nextChart : c,
  );
  const hadImage = draftHasAnyImage(draft);
  const hasImage = charts.some((c) => Boolean(c.imageDataUrl));
  const firstImage = !hadImage && hasImage;
  return {
    ...draft,
    charts,
    instrumentLocked:
      draft.instrumentLocked ||
      firstImage ||
      (hasImage && Boolean(draft.instrument)),
  };
}

/** One-time instrument selection for new/legacy-unexported drafts. */
export function selectInstrument(
  draft: SketchDraft,
  symbol: string,
  assetClass: string,
): SketchDraft {
  if (draft.exported) {
    return draft;
  }
  if (draft.instrumentLocked && draft.instrument) {
    return draft;
  }
  const lockNow = draftHasAnyImage(draft) || draft.instrumentLocked;
  return {
    ...draft,
    instrument: symbol,
    assetClass,
    instrumentLocked: lockNow,
  };
}

/**
 * Duplicate into a new editable id.
 * Safety (D13): clear all market-bound content (images + ownerView) so unlocking
 * instrument cannot re-label NQ screenshots as GC. Keep timeframe/role/indicators.
 */
export function duplicateDraftAsNew(
  source: SketchDraft,
  existingIds: readonly string[],
  now: Date = new Date(),
): SketchDraft {
  const iso = now.toISOString();
  const charts = source.charts.map((c) => ({
    ...c,
    slotId: `${c.slotId}-copy-${Date.now()}`,
    imageDataUrl: null,
    imageFileName: null,
    ownerView: "",
  }));
  return {
    ...source,
    sketchId: nextSketchId(existingIds, now),
    title: source.title ? `${source.title}（複製）` : "",
    instrument: source.instrument,
    assetClass: source.assetClass,
    // No images ⇒ may unlock; invariant still holds
    instrumentLocked: false,
    instrumentLegacy: false,
    exported: false,
    exportedAt: null,
    created: iso,
    updatedAt: iso,
    charts,
  };
}

export function readSketchStore(
  storage: Storage = localStorage,
): SketchStoreSnapshot {
  try {
    const raw = storage.getItem(SKETCH_STORAGE_KEY);
    if (!raw) {
      return emptySnapshot();
    }
    const parsed = JSON.parse(raw) as SketchStoreSnapshot;
    if (parsed.schema !== "sketch_store.v1" || !Array.isArray(parsed.drafts)) {
      return emptySnapshot();
    }
    return {
      schema: "sketch_store.v1",
      drafts: parsed.drafts.map(migrateDraftShape),
      activeId: parsed.activeId ?? null,
      packages: parsed.packages ?? {},
    };
  } catch {
    return emptySnapshot();
  }
}

export function writeSketchStore(
  snapshot: SketchStoreSnapshot,
  storage: Storage = localStorage,
): void {
  storage.setItem(SKETCH_STORAGE_KEY, JSON.stringify(snapshot));
}

/** Newest first. */
export function listDrafts(storage: Storage = localStorage): SketchDraft[] {
  const drafts = [...readSketchStore(storage).drafts];
  drafts.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
  return drafts;
}

/** Load active draft or create a fresh one. */
export function loadOrCreateActiveDraft(
  storage: Storage = localStorage,
  now: Date = new Date(),
): SketchDraft {
  const snap = readSketchStore(storage);
  if (snap.activeId) {
    const found = snap.drafts.find((d) => d.sketchId === snap.activeId);
    if (found) {
      return found;
    }
  }
  const draft = createEmptyDraft(
    snap.drafts.map((d) => d.sketchId),
    now,
  );
  const next: SketchStoreSnapshot = {
    ...snap,
    drafts: [...snap.drafts, draft],
    activeId: draft.sketchId,
  };
  writeSketchStore(next, storage);
  return draft;
}

/** Persist draft without export gates (constraint: 存草稿 has no preconditions). */
export function saveDraft(
  draft: SketchDraft,
  storage: Storage = localStorage,
  now: Date = new Date(),
): SketchDraft {
  const updated: SketchDraft = {
    ...draft,
    updatedAt: now.toISOString(),
  };
  const snap = readSketchStore(storage);
  const others = snap.drafts.filter((d) => d.sketchId !== updated.sketchId);
  writeSketchStore(
    {
      ...snap,
      drafts: [...others, updated],
      activeId: updated.sketchId,
    },
    storage,
  );
  return updated;
}

/**
 * Open another draft. Saves `current` first so in-progress edits are not lost
 * when switching via the draft list.
 */
export function selectDraft(
  sketchId: string,
  current: SketchDraft | null,
  storage: Storage = localStorage,
): SketchDraft | null {
  if (current) {
    saveDraft(current, storage);
  }
  const snap = readSketchStore(storage);
  const found = snap.drafts.find((d) => d.sketchId === sketchId);
  if (!found) {
    return null;
  }
  writeSketchStore({ ...snap, activeId: sketchId }, storage);
  return found;
}

export function startNewDraft(
  current: SketchDraft | null = null,
  storage: Storage = localStorage,
  now: Date = new Date(),
): SketchDraft {
  if (current) {
    saveDraft(current, storage, now);
  }
  const snap = readSketchStore(storage);
  const draft = createEmptyDraft(
    snap.drafts.map((d) => d.sketchId),
    now,
  );
  writeSketchStore(
    {
      ...snap,
      drafts: [...snap.drafts, draft],
      activeId: draft.sketchId,
    },
    storage,
  );
  return draft;
}

function buildPackagePreview(draft: SketchDraft): SketchPackagePreview {
  const insightId =
    draft.kind === "insight"
      ? `insight-${draft.sketchId.replace(/^sketch-/, "")}`
      : undefined;
  const metaYaml = emitMetaYaml(draft);
  const instructionsMd = emitInstructionsMd(draft, { insightId });
  assertInstructionsSelfContained(instructionsMd);
  if (draft.kind === "insight") {
    assertInsightInstructions(instructionsMd);
  } else {
    assertInstructionsEmbedsSchema(instructionsMd);
  }
  const relativeDir = sketchRelativeDir(draft.sketchId);
  const chartImages = draft.charts.map((c, slotIndex) => ({
    fileName: chartSlotFileName(slotIndex),
    dataUrl: c.imageDataUrl,
  }));
  const chartFiles = chartImages.map((c) => c.fileName);
  return {
    sketchId: draft.sketchId,
    relativeDir,
    files: [...chartFiles, "meta.yaml", "INSTRUCTIONS.md"],
    metaYaml,
    instructionsMd,
    terminalOpener: buildTerminalOpener(draft.sketchId),
    chartImages,
  };
}

/**
 * Prepare validated package without marking exported (Live Seam A D2).
 * Same gates as export; does not write storage.
 */
export function prepareSketchPackage(
  draft: SketchDraft,
  catalog: CatalogState = { status: "invalid", message: "缺 catalog" },
): SketchPackagePreview {
  const blockers = exportBlockingReasons(draft);
  if (blockers.length > 0) {
    throw new Error(blockers.join(" · "));
  }
  if (!canExportSketch(draft)) {
    throw new Error("未填齊，唔可以匯出");
  }
  const catReasons = validateDraftAgainstCatalog(draft, catalog);
  if (catReasons.length > 0) {
    throw new Error(catReasons.join(" · "));
  }
  return buildPackagePreview(draft);
}

/**
 * After exact-valid backend 201 (or local-only insight/owner-review): mark exported.
 * Identity must match package.sketchId.
 * @param preserveActiveId — when true (background commit of A while viewing B),
 *   do not switch activeId to the committed sketch.
 */
export function commitLocalSketchExport(
  draft: SketchDraft,
  packagePreview: SketchPackagePreview,
  storage: Storage = localStorage,
  now: Date = new Date(),
  options?: { preserveActiveId?: boolean },
): { draft: SketchDraft; package: SketchPackagePreview } {
  if (draft.sketchId !== packagePreview.sketchId) {
    throw new Error("commit sketch id 同 package 唔一致");
  }
  const exported: SketchDraft = {
    ...draft,
    exported: true,
    exportedAt: now.toISOString(),
    updatedAt: now.toISOString(),
  };
  const snap = readSketchStore(storage);
  // Prefer frozen snapshot fields; if store has a newer copy of same id with
  // extra local edits after click, still lock export from snapshot identity.
  const others = snap.drafts.filter((d) => d.sketchId !== exported.sketchId);
  const nextActive =
    options?.preserveActiveId && snap.activeId
      ? snap.activeId
      : exported.sketchId;
  writeSketchStore(
    {
      ...snap,
      drafts: [...others, exported],
      activeId: nextActive,
      packages: { ...snap.packages, [exported.sketchId]: packagePreview },
    },
    storage,
  );
  return { draft: exported, package: packagePreview };
}

/**
 * Local-only export (Insight / owner-review): prepare + commit without POST.
 * Normal live path must use prepareSketchPackage → postSketchZip → commitLocalSketchExport.
 */
export function exportSketchPackage(
  draft: SketchDraft,
  storage: Storage = localStorage,
  now: Date = new Date(),
  /** Required catalog revalidation (D12) — cannot be bypassed. */
  catalog: CatalogState = { status: "invalid", message: "缺 catalog" },
): { draft: SketchDraft; package: SketchPackagePreview } {
  const packagePreview = prepareSketchPackage(draft, catalog);
  return commitLocalSketchExport(draft, packagePreview, storage, now);
}

export function getPackage(
  sketchId: string,
  storage: Storage = localStorage,
): SketchPackagePreview | null {
  return readSketchStore(storage).packages[sketchId] ?? null;
}

export { allOwnerViewsFilled } from "./metaYaml";
export { canExportSketch, exportBlockingReasons, draftGapSummary } from "./exportReadiness";
/** Exposed for tests that need to build a pack without storing. */
export { buildPackagePreview };
