import JSZip from "jszip";

import type { SketchPackagePreview } from "./types";

/**
 * Build the downloadable zip from the **same** packagePreview the Dialog shows
 * (constraint #26: download content === on-screen pack).
 *
 * Layout inside the zip:
 *   <sketchId>/
 *     chart-….png
 *     meta.yaml
 *     INSTRUCTIONS.md
 *
 * so unpack → move the folder into repo `data/sketches/workshop/`.
 * ZIP root is still `<sketchId>/` only (not `workshop/<sketchId>/`).
 */
export async function buildSketchZipBlob(
  pkg: SketchPackagePreview,
): Promise<Blob> {
  const zip = new JSZip();
  const folder = zip.folder(pkg.sketchId);
  if (!folder) {
    throw new Error("zip folder create failed");
  }

  for (const chart of pkg.chartImages) {
    if (chart.dataUrl && chart.dataUrl.includes(",")) {
      const base64 = chart.dataUrl.split(",")[1] ?? "";
      folder.file(chart.fileName, base64, { base64: true });
    } else {
      // No upload yet — still emit the named file so the pack tree matches.
      folder.file(chart.fileName, new Uint8Array(0));
    }
  }

  folder.file("meta.yaml", pkg.metaYaml);
  folder.file("INSTRUCTIONS.md", pkg.instructionsMd);

  return zip.generateAsync({ type: "blob" });
}

/** Trigger a browser download. Returns the object URL (caller may revoke). */
export function triggerBlobDownload(blob: Blob, fileName: string): string {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = "noopener";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  return url;
}

export function zipFileName(sketchId: string): string {
  return `${sketchId}.zip`;
}
