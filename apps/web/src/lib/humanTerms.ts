/**
 * Engineering vocabulary → the owner's words.
 *
 * The banned-word table in `docs/ui/designs/p4-backtest.html` applies to every
 * page, and the compute/IB panels were leaking straight through it: `rust`,
 * `Native dylib`, `已 admit`, `product trade seal 優先 Rust kernel`, a full
 * `.dylib` filesystem path, and the backend's own
 * `missing required IB environment variables: …` sentence were all on screen.
 *
 * Two rules hold everywhere below:
 *   1. an unknown value becomes "—", never the raw token — a passthrough is
 *      exactly how the words got out in the first place;
 *   2. nothing is destroyed. The raw text stays available for 詳情.
 */

/** Which engine actually ran the work. */
export function engineLabel(backend: string | null | undefined): string {
  if (backend === "rust") return "快速引擎";
  if (backend === "python") return "備用引擎";
  return "—";
}

/** Whether the optional accelerator loaded. */
export function acceleratorLabel(admitted: boolean | undefined): string {
  return admitted ? "已就緒" : "未就緒";
}

export function severityLabel(severity: string | undefined): string {
  if (severity === "error") return "錯誤";
  if (severity === "warn" || severity === "warning") return "警告";
  return "訊息";
}

/** Fixed owner-facing description of how the two engines relate. */
export const ENGINE_SUMMARY =
  "主程式負責記帳同落實決定；計算交俾快速引擎做，快速引擎唔得就自動轉用備用引擎，結果一樣。";

const IB_DETAIL_RULES: ReadonlyArray<{ match: RegExp; text: string }> = [
  {
    match: /missing required ib environment variables/i,
    text: "IB 連接資料未填好，所以未開始探測。",
  },
  {
    match: /refused|unreachable|timed? ?out/i,
    text: "行情程式未開，或者個 port 唔通。",
  },
  {
    match: /狀態 API 無法讀取/,
    text: "讀唔到連接狀態。",
  },
];

/**
 * Plain sentence for the IB status line, plus the untouched original.
 * A detail with no recognised pattern is *not* printed — an unmapped English
 * sentence on screen is the failure this function exists to prevent.
 */
export function plainIbDetail(detail: string | null | undefined): {
  text: string;
  raw: string;
} {
  const raw = detail ?? "";
  for (const rule of IB_DETAIL_RULES) {
    if (rule.match.test(raw)) {
      return { text: rule.text, raw };
    }
  }
  // Chinese-only details are already owner-facing and pass through as-is.
  const hasLatinWords = /[A-Za-z]{4,}/.test(raw);
  return { text: hasLatinWords ? "" : raw, raw };
}
