import { chartSlotFileName } from "./paths";
import type { SketchDraft } from "./types";

/**
 * Emit meta.yaml for sketch.v1.
 *
 * Hand-rolled for this closed shape only (stage 1 has no yaml dependency).
 * Rules from docs/05 §3.5:
 * - indicators_shown always present (even [])
 * - optional fields omitted when empty (no null)
 * - multiline owner_view / rationale as block scalars
 */

function indentBlock(text: string, spaces: number): string {
  const pad = " ".repeat(spaces);
  const normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  // Trailing newline on block content is fine; strip trailing spaces per line.
  const lines = normalized.split("\n").map((line) => line.replace(/\s+$/u, ""));
  // YAML block scalar: empty owner_view still needs a line under |
  if (lines.length === 1 && lines[0] === "") {
    return `${pad}\n`;
  }
  return lines.map((line) => (line.length === 0 ? pad : `${pad}${line}`)).join("\n") + "\n";
}

function quoteIfNeeded(value: string): string {
  if (value === "") {
    return '""';
  }
  // Simple scalars without special YAML punctuation.
  if (/^[\w.\-/\u4e00-\u9fff]+$/u.test(value)) {
    return value;
  }
  return JSON.stringify(value);
}

function indicatorsLine(tokens: readonly string[]): string {
  if (tokens.length === 0) {
    return "[]";
  }
  return `[${tokens.join(", ")}]`;
}

/** ISO instant → date label YYYY-MM-DD for meta.created (schema example). */
export function createdDateLabel(isoInstant: string): string {
  // created is a package time anchor; use the UTC calendar day of the instant
  // so the label is stable across local TZ for the same instant. This is NOT a
  // trading_date — do not confuse with constraint #25 trading-day rules.
  const d = new Date(isoInstant);
  if (Number.isNaN(d.getTime())) {
    return isoInstant.slice(0, 10);
  }
  return d.toISOString().slice(0, 10);
}

export function emitMetaYaml(draft: SketchDraft): string {
  const lines: string[] = [];
  lines.push("schema: sketch.v1");
  lines.push(`sketch_id: ${draft.sketchId}`);
  lines.push(`kind: ${draft.kind}`);
  lines.push(`origin: ${draft.origin}`);
  lines.push(`chart_source: ${draft.chartSource}`);
  lines.push(`instructions_template: ${draft.instructionsTemplate}`);
  lines.push(`created: ${createdDateLabel(draft.created)}`);
  // [107]: no sketchId fallback — export is gated on non-empty title.
  lines.push(`title: ${quoteIfNeeded(draft.title.trim())}`);
  // P2 instrument closed loop: both required at export (gate enforces).
  if (draft.instrument) {
    lines.push(`instrument: ${quoteIfNeeded(draft.instrument)}`);
  }
  if (draft.assetClass) {
    lines.push(`asset_class: ${quoteIfNeeded(draft.assetClass)}`);
  }
  if (draft.kind === "strategy") {
    lines.push("rationale: |");
    lines.push(indentBlock(draft.rationale, 2).replace(/\n$/, ""));
  }
  lines.push("charts:");
  draft.charts.forEach((chart, slotIndex) => {
    // Canonical slot filename; timeframe is Owner-editable and may duplicate.
    lines.push(`  - file: ${chartSlotFileName(slotIndex)}`);
    lines.push(`    timeframe: ${chart.timeframe}`);
    lines.push(`    role: ${chart.role}`);
    lines.push(`    indicators_shown: ${indicatorsLine(chart.indicatorsShown)}`);
    lines.push("    owner_view: |");
    const body = indentBlock(chart.ownerView, 6).replace(/\n$/, "");
    lines.push(body);
  });
  return lines.join("\n") + "\n";
}

/** All four owner_view fields non-empty after trim. */
export function allOwnerViewsFilled(draft: SketchDraft): boolean {
  if (draft.charts.length < 4) {
    return false;
  }
  return draft.charts.every((c) => c.ownerView.trim().length > 0);
}

