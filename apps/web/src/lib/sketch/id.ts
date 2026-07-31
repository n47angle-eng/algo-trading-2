/**
 * sketch_id = sketch-YYYYMMDD-NN (docs/05 §3.5.0).
 * Date stamp uses the browser local calendar day — Owner labels the pack by
 * the day they made it, not a trading-session label.
 */

export function localYyyymmdd(now: Date = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}${m}${d}`;
}

export function parseSketchSeq(
  sketchId: string,
  yyyymmdd: string,
): number | null {
  const prefix = `sketch-${yyyymmdd}-`;
  if (!sketchId.startsWith(prefix)) {
    return null;
  }
  const rest = sketchId.slice(prefix.length);
  if (!/^\d{2}$/.test(rest)) {
    return null;
  }
  return Number(rest);
}

/** Next id for today among existing ids (max seq + 1, starting at 01). */
export function nextSketchId(
  existingIds: readonly string[],
  now: Date = new Date(),
): string {
  const day = localYyyymmdd(now);
  let max = 0;
  for (const id of existingIds) {
    const seq = parseSketchSeq(id, day);
    if (seq !== null && seq > max) {
      max = seq;
    }
  }
  const next = max + 1;
  if (next > 99) {
    throw new Error(`sketch id sequence overflow for ${day}`);
  }
  return `sketch-${day}-${String(next).padStart(2, "0")}`;
}
