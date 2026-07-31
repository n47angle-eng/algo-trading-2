/**
 * POST /api/v1/sketches — same ZIP bytes as browser download (Normal Live Seam A).
 */

import {
  parseSketchDetailResponse,
  type SketchDetailResponse,
} from "../../api/client";
import type { SketchPackagePreview } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export class SketchPostError extends Error {
  readonly status: number;
  /** Backend validation body when available (422). */
  readonly detailText: string;

  constructor(status: number, message: string, detailText = "") {
    super(message);
    this.name = "SketchPostError";
    this.status = status;
    this.detailText = detailText;
  }
}

export interface SketchPostExpected {
  origin: "workshop" | "journal-app";
  sketchId: string;
  instrument: string;
  assetClass: string;
  chartFileNames: string[];
}

/** Human error for non-201 responses. */
export function humanizeSketchPostFailure(
  status: number,
  bodyText: string,
): string {
  if (status === 409) {
    return "同一 origin＋sketch id 已存在，冇覆蓋；請複製成新草圖再匯出。";
  }
  if (status === 415) {
    return "伺服器唔接受呢個 Content-Type——需要 application/zip。";
  }
  if (status === 422) {
    const trimmed = bodyText.trim();
    if (trimmed) {
      return `後端驗證唔通過：${trimmed.slice(0, 800)}`;
    }
    return "後端驗證唔通過（422）。";
  }
  if (status >= 500) {
    return `伺服器暫時寫唔入草圖（HTTP ${status}）。本地草稿仍可編輯，請稍後重試。`;
  }
  if (status === 0) {
    // Network / read errors: outcome unknown — never assert "not written".
    return (
      bodyText ||
      "網絡或連線問題——結果未知，唔確定草圖有冇寫入；請查 backend／repo 再決定係咪重試。"
    );
  }
  return `匯出失敗（HTTP ${status}）${bodyText ? `：${bodyText.slice(0, 400)}` : ""}`;
}

/**
 * Exact identity gate: 201 body must match the package we POSTed.
 * Does not mutate payload (uses existing parseSketchDetailResponse).
 */
export function assertSketchPostMatchesPackage(
  detail: SketchDetailResponse,
  expected: SketchPostExpected,
): void {
  const meta = detail.meta;
  if (meta.origin !== expected.origin) {
    throw new SketchPostError(
      0,
      `回應 origin「${meta.origin}」同請求「${expected.origin}」唔一致——未標本地已匯出。`,
    );
  }
  if (meta.sketch_id !== expected.sketchId) {
    throw new SketchPostError(
      0,
      `回應 sketch_id「${meta.sketch_id}」同請求「${expected.sketchId}」唔一致——未標本地已匯出。`,
    );
  }
  if (meta.instrument !== expected.instrument) {
    throw new SketchPostError(
      0,
      `回應 instrument「${meta.instrument ?? ""}」同請求「${expected.instrument}」唔一致——未標本地已匯出。`,
    );
  }
  if (meta.asset_class !== expected.assetClass) {
    throw new SketchPostError(
      0,
      `回應 asset_class「${meta.asset_class ?? ""}」同請求「${expected.assetClass}」唔一致——未標本地已匯出。`,
    );
  }
  const files = meta.charts.map((c) => c.file).sort();
  const want = [...expected.chartFileNames].sort();
  if (files.length !== want.length || files.some((f, i) => f !== want[i])) {
    throw new SketchPostError(
      0,
      "回應 charts 檔名同請求圖文包唔一致——未標本地已匯出。",
    );
  }
  const imageFiles = detail.images.map((i) => i.file).sort();
  if (
    imageFiles.length !== want.length ||
    imageFiles.some((f, i) => f !== want[i])
  ) {
    throw new SketchPostError(
      0,
      "回應 images 檔名同請求圖文包唔一致——未標本地已匯出。",
    );
  }
}

export function expectedFromPackage(
  pkg: SketchPackagePreview,
  draft: {
    origin: "workshop" | "journal-app";
    instrument: string | null;
    assetClass: string | null;
  },
): SketchPostExpected {
  if (!draft.instrument || !draft.assetClass) {
    throw new SketchPostError(0, "draft 缺 instrument／asset_class——唔可以 POST。");
  }
  return {
    origin: draft.origin,
    sketchId: pkg.sketchId,
    instrument: draft.instrument,
    assetClass: draft.assetClass,
    chartFileNames: pkg.chartImages.map((c) => c.fileName),
  };
}

/**
 * POST ZIP to backend. Only 201 + exact identity → success.
 * Caller must not mark local export until this resolves successfully.
 */
export async function postSketchZip(
  zipBlob: Blob,
  expected: SketchPostExpected,
): Promise<SketchDetailResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/v1/sketches`, {
      method: "POST",
      headers: { "Content-Type": "application/zip" },
      body: zipBlob,
    });
  } catch {
    throw new SketchPostError(
      0,
      humanizeSketchPostFailure(
        0,
        "網絡或連線問題——結果未知，唔確定草圖有冇寫入；請查 backend／repo 再決定係咪重試。",
      ),
    );
  }

  const text = await response.text();
  if (response.status !== 201) {
    throw new SketchPostError(
      response.status,
      humanizeSketchPostFailure(response.status, text),
      text,
    );
  }

  let body: unknown;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new SketchPostError(
      0,
      "201 回應唔係合法 JSON——未標本地已匯出。",
      text,
    );
  }

  const parsed = parseSketchDetailResponse(
    body,
    expected.origin,
    expected.sketchId,
  );
  if (!parsed) {
    throw new SketchPostError(
      0,
      "201 回應唔係 exact-valid sketch_detail.v1——未標本地已匯出。",
      text,
    );
  }
  assertSketchPostMatchesPackage(parsed, expected);
  return parsed;
}

/** Pure helper for tests: package chart file names. */
export function packageChartFileNames(pkg: SketchPackagePreview): string[] {
  return pkg.chartImages.map((c) => c.fileName);
}
