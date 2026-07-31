/** sketch.v1 domain model — stage-1 browser store (docs/05 §3.5). */

export type ChartRole = "bias" | "mid" | "auxiliary" | "entry";

export type SketchKind = "strategy" | "insight";

/** Canonical indicator tokens (docs/05). Unknown free-text goes elsewhere later. */
export const INDICATOR_OPTIONS = [
  "ema18",
  "ema50",
  "ema90",
  "vwap",
  "atr14",
] as const;

export type IndicatorToken = (typeof INDICATOR_OPTIONS)[number];

export const TIMEFRAME_OPTIONS = ["D", "1H", "30m", "5m", "15m", "4H"] as const;

export const ROLE_OPTIONS: readonly { value: ChartRole; label: string }[] = [
  { value: "bias", label: "bias 大框架" },
  { value: "mid", label: "mid 中框架" },
  { value: "auxiliary", label: "auxiliary 輔助" },
  { value: "entry", label: "entry 入市" },
] as const;

export interface SketchChartSlot {
  /** Stable key inside one sketch (slot index is not enough after reorder). */
  slotId: string;
  timeframe: string;
  role: ChartRole;
  /** Always present; empty array means Owner explicitly selected none. */
  indicatorsShown: IndicatorToken[];
  ownerView: string;
  /** Browser-only preview (data URL). Not written to disk in stage 1. */
  imageDataUrl: string | null;
  /** Original upload file name for display. */
  imageFileName: string | null;
}

export interface SketchDraft {
  schema: "sketch.v1";
  sketchId: string;
  kind: SketchKind;
  origin: "workshop" | "journal-app";
  chartSource: "screenshot";
  instructionsTemplate: "instructions.v1";
  title: string;
  rationale: string;
  charts: SketchChartSlot[];
  /** Root product symbol (e.g. NQ) — Owner-selected, never inferred. */
  instrument: string | null;
  /** Catalog asset class — locked with instrument. */
  assetClass: string | null;
  /**
   * True after first image attach or after one-time legacy selection with images.
   * Cleared never (monotonic). Exported drafts are fully readonly separately.
   * Hard invariant: any image ⇒ instrumentLocked.
   */
  instrumentLocked: boolean;
  /**
   * True only when draft was migrated from pre-instrument storage shape
   * (missing instrument key). Fresh drafts with instrument:null are NOT legacy.
   */
  instrumentLegacy: boolean;
  /** ISO instant when the draft was first created. */
  created: string;
  /** ISO instant of last draft save (local). */
  updatedAt: string;
  /** Stage-1: true after Owner clicked 匯出 (package recorded in store). */
  exported: boolean;
  exportedAt: string | null;
}

export interface SketchPackageChartImage {
  fileName: string;
  /** data URL from Owner upload; null if no image yet. */
  dataUrl: string | null;
}

export interface SketchPackagePreview {
  sketchId: string;
  /** Repo-relative path ending with slash: data/sketches/workshop/<id>/ */
  relativeDir: string;
  files: string[];
  metaYaml: string;
  instructionsMd: string;
  terminalOpener: string;
  /** Chart binaries carried for download (same content as Dialog tree). */
  chartImages: SketchPackageChartImage[];
}

export const SKETCH_STORAGE_KEY = "futures-research.sketches.v1";
