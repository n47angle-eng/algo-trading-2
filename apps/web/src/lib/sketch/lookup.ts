import { readSketchStore } from "./store";
import type { SketchDraft } from "./types";

/** Look up a sketch by id in the browser store (constraint #9 left panel). */
export function findSketchById(
  sketchId: string,
  storage: Storage = localStorage,
): SketchDraft | null {
  const snap = readSketchStore(storage);
  return snap.drafts.find((d) => d.sketchId === sketchId) ?? null;
}
