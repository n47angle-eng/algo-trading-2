/** Repo-relative sketch package paths (constraint #4 / P2 origin-aware). */

/** This app always publishes under workshop origin. */
export const SKETCH_APP_ORIGIN = "workshop" as const;

export type SketchOrigin = "workshop" | "journal-app";

export function isSketchOrigin(value: string | null | undefined): value is SketchOrigin {
  return value === "workshop" || value === "journal-app";
}

/**
 * Repo path for an accepted package: `data/sketches/<origin>/<sketch-id>/`.
 * ZIP root remains `<sketch-id>/` only (not nested under origin).
 */
export function sketchRelativeDir(
  sketchId: string,
  origin: string = SKETCH_APP_ORIGIN,
): string {
  return `data/sketches/${origin}/${sketchId}/`;
}

/**
 * Canonical package member names by slot index (0..3).
 * Owner-editable timeframe is independent — filenames never follow free timeframe.
 */
export const CANONICAL_CHART_FILES = [
  "chart-D.png",
  "chart-1H.png",
  "chart-30m.png",
  "chart-5m.png",
] as const;

export type CanonicalChartFile = (typeof CANONICAL_CHART_FILES)[number];

/** Slot index 0..3 → fixed package member name. */
export function chartSlotFileName(slotIndex: number): string {
  if (slotIndex >= 0 && slotIndex < CANONICAL_CHART_FILES.length) {
    return CANONICAL_CHART_FILES[slotIndex];
  }
  return `chart-slot-${slotIndex + 1}.png`;
}

/**
 * @deprecated Prefer chartSlotFileName(slotIndex). Kept for non-package display only.
 * Package ZIP/meta MUST use chartSlotFileName.
 */
export function chartFileName(timeframe: string): string {
  const safe = timeframe.trim() || "unknown";
  return `chart-${safe}.png`;
}

export function packageFileList(timeframes?: string[]): string[] {
  void timeframes; // historical API; members are slot-canonical, not TF-derived
  return [...CANONICAL_CHART_FILES, "meta.yaml", "INSTRUCTIONS.md"];
}
