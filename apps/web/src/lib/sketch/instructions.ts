import insightTemplateSource from "./instructions-v1-insight.template.md?raw";
import strategyTemplateSource from "./instructions-v1.template.md?raw";

import { sketchRelativeDir } from "./paths";
import type { SketchDraft } from "./types";

/** Placeholder id in the strategy design-sample template. */
const SAMPLE_SKETCH_ID = "sketch-20260725-01";

function stripSampleBlockquote(text: string): string {
  return text.replace(
    /> \*\*呢份係樣本／模板\*\*[\s\S]*?(?:\n\n|\n(?=---))/,
    "",
  );
}

function fillPlaceholders(text: string, draft: SketchDraft): string {
  const instrument = draft.instrument?.trim() || "UNSET_INSTRUMENT";
  const assetClass = draft.assetClass?.trim() || "UNSET_ASSET_CLASS";
  const origin = draft.origin || "workshop";
  return text
    .split("{{instrument}}")
    .join(instrument)
    .split("{{asset_class}}")
    .join(assetClass)
    .split("{{sketch_origin}}")
    .join(origin)
    .split("{{sketch_id}}")
    .join(draft.sketchId);
}

/**
 * Self-contained INSTRUCTIONS.md (constraint #8 + docs/05 §3.5.2).
 * Strategy vs insight templates are different — never cross them.
 */
export function emitInstructionsMd(
  draft: SketchDraft,
  options?: { insightId?: string },
): string {
  if (draft.kind === "insight") {
    return emitInsightInstructions(draft, options?.insightId);
  }
  return emitStrategyInstructions(draft);
}

function emitStrategyInstructions(draft: SketchDraft): string {
  let text = strategyTemplateSource.replace(/\r\n/g, "\n");
  text = stripSampleBlockquote(text);
  text = text.split(SAMPLE_SKETCH_ID).join(draft.sketchId);
  const dir = sketchRelativeDir(draft.sketchId);
  if (!text.includes(dir)) {
    text = text.replace(
      new RegExp(`data/sketches/${draft.sketchId}/`, "g"),
      dir,
    );
  }
  text = fillPlaceholders(text, draft);
  return text;
}

function emitInsightInstructions(
  draft: SketchDraft,
  insightId?: string,
): string {
  const dir = sketchRelativeDir(draft.sketchId);
  const id =
    insightId ??
    `insight-${localSeqFromSketch(draft.sketchId)}`;
  let text = insightTemplateSource.replace(/\r\n/g, "\n");
  text = stripSampleBlockquote(text);
  text = text
    .split("{{sketch_dir}}")
    .join(dir)
    .split("{{insight_id}}")
    .join(id)
    .split("{{title}}")
    .join(draft.title.trim() || draft.sketchId);
  text = fillPlaceholders(text, draft);
  return text;
}

function localSeqFromSketch(sketchId: string): string {
  const m = sketchId.match(/(\d{2})$/);
  return m ? m[1] : "01";
}

/** Next insight id for browser store (insight-NNN). */
export function nextInsightId(existing: readonly string[]): string {
  let max = 0;
  for (const id of existing) {
    const m = id.match(/^insight-(\d+)$/);
    if (m) {
      max = Math.max(max, Number(m[1]));
    }
  }
  return `insight-${String(max + 1).padStart(3, "0")}`;
}

export function assertInstructionsSelfContained(text: string): void {
  if (/docs\/0\d/i.test(text)) {
    throw new Error("INSTRUCTIONS.md must not reference docs/0X paths");
  }
}

export function assertInstructionsEmbedsSchema(
  text: string,
  expected?: { sketchId?: string; origin?: string; instrument?: string },
): void {
  const required = [
    "strategy.v1",
    "pullback_lifecycle",
    "signal_bar",
    "provenance",
    "unquantified_notes",
    "primary_instrument",
    "asset_class",
    "expansion_rationale",
    "based_on_sketch:",
    "based_on_sketch_origin:",
  ] as const;
  for (const token of required) {
    if (!text.includes(token)) {
      throw new Error(`INSTRUCTIONS.md missing required token: ${token}`);
    }
  }
  if (expected?.sketchId && !text.includes(expected.sketchId)) {
    throw new Error(`INSTRUCTIONS.md missing sketch_id ${expected.sketchId}`);
  }
  if (expected?.origin && !text.includes(`based_on_sketch_origin: ${expected.origin}`)) {
    throw new Error(
      `INSTRUCTIONS.md missing based_on_sketch_origin: ${expected.origin}`,
    );
  }
  if (expected?.instrument && !text.includes(expected.instrument)) {
    throw new Error(`INSTRUCTIONS.md missing instrument ${expected.instrument}`);
  }
  const sectionHeads = text.match(/^## \d+\. /gm) ?? [];
  if (sectionHeads.length < 6) {
    throw new Error(
      `INSTRUCTIONS.md needs six ## N. sections, found ${sectionHeads.length}`,
    );
  }
}

/** Insight pack must embed insight.v1 and must NOT embed strategy.v1. */
export function assertInsightInstructions(
  text: string,
  expected?: { sketchId?: string; origin?: string; instrument?: string },
): void {
  if (!text.includes("insight.v1")) {
    throw new Error("insight INSTRUCTIONS.md missing insight.v1");
  }
  if (text.includes("strategy.v1")) {
    throw new Error(
      "insight INSTRUCTIONS.md must not contain strategy.v1 (wrong template)",
    );
  }
  if (!text.includes("instrument:") || !text.includes("asset_class:")) {
    throw new Error(
      "insight INSTRUCTIONS.md missing instrument/asset_class skeleton",
    );
  }
  if (!text.includes("based_on_sketch:") || !text.includes("based_on_sketch_origin:")) {
    throw new Error(
      "insight INSTRUCTIONS.md missing composite lineage keys",
    );
  }
  if (expected?.origin && !text.includes(`based_on_sketch_origin: ${expected.origin}`)) {
    throw new Error(
      `insight INSTRUCTIONS missing based_on_sketch_origin: ${expected.origin}`,
    );
  }
  if (expected?.sketchId && !text.includes(expected.sketchId)) {
    throw new Error(`insight INSTRUCTIONS missing sketch_id ${expected.sketchId}`);
  }
  if (expected?.instrument && !text.includes(`instrument: ${expected.instrument}`)) {
    throw new Error(
      `insight INSTRUCTIONS missing instrument: ${expected.instrument}`,
    );
  }
  const sectionHeads = text.match(/^## \d+\. /gm) ?? [];
  if (sectionHeads.length < 6) {
    throw new Error(
      `insight INSTRUCTIONS.md needs six ## N. sections, found ${sectionHeads.length}`,
    );
  }
}
