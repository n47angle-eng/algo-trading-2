/**
 * Real PNG readiness for sketch export (Live Seam Correction A D1).
 * Rejects missing / non-png mime / fake signature (e.g. JPEG re-labeled).
 */

/** 1×1 PNG — valid signature; used in fixtures and tests. */
export const TINY_PNG_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

/** PNG magic as base64 prefix (89 50 4E 47 0D 0A 1A 0A). */
const PNG_B64_PREFIX = "iVBORw0KGgo";

export function isPngDataUrl(dataUrl: string | null | undefined): boolean {
  if (!dataUrl || typeof dataUrl !== "string") {
    return false;
  }
  const trimmed = dataUrl.trim();
  if (!trimmed.startsWith("data:image/png")) {
    return false;
  }
  const comma = trimmed.indexOf(",");
  if (comma < 0) {
    return false;
  }
  const b64 = trimmed.slice(comma + 1).replace(/\s/g, "");
  return b64.startsWith(PNG_B64_PREFIX);
}

export function imageSlotBlocker(
  slotLabel: string,
  dataUrl: string | null | undefined,
): string | null {
  if (!dataUrl) {
    return `${slotLabel} 圖未上載`;
  }
  if (!dataUrl.startsWith("data:image/png")) {
    return `${slotLabel} 圖唔係 PNG（只接受 PNG）`;
  }
  if (!isPngDataUrl(dataUrl)) {
    return `${slotLabel} 圖唔係合法 PNG（簽名唔啱）`;
  }
  return null;
}
