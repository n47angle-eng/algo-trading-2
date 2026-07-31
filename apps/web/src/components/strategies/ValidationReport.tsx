import { useState } from "react";

import type { StrategyValidationResponse } from "../../api/client";

interface ValidationReportProps {
  report: StrategyValidationResponse;
}

const LAYER_LABELS: Record<string, string> = {
  format: "格式層",
  references: "引用層",
  semantics: "語義層",
  provenance: "溯源層",
};

/**
 * Validation failures, rendered for the Owner and copyable for the AI.
 *
 * D2 (no AI API) makes this string the app's only return channel to the
 * terminal agent, so the copy button yields `format_report()` verbatim — plain
 * text, no markup, no line numbers, no UI prefixes — for paste-back unchanged.
 */
export function ValidationReport({ report }: ValidationReportProps) {
  const [copied, setCopied] = useState(false);

  const layers = [...new Set(report.issues.map((issue) => issue.layer))];

  return (
    <section
      className="panel panel--alert"
      role="region"
      aria-label="驗證失敗"
    >
      <h2 className="panel__title">
        驗證失敗 · {report.issue_count} 個問題 ·{" "}
        {layers.map((layer) => LAYER_LABELS[layer] ?? layer).join(" / ")}
      </h2>
      <p className="panel__note">
        以下每一行嘅格式係 <code>&lt;路徑&gt;: &lt;乜嘢錯&gt; — &lt;點修&gt;</code>
        ，可以原句貼返俾 terminal AI 改。
        {layers.includes("format")
          ? "（格式層有錯，後面幾層未行過——修完格式再驗一次。）"
          : null}
      </p>
      <div className="form-actions">
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => {
            void (async () => {
              try {
                await navigator.clipboard.writeText(report.report_text);
                setCopied(true);
              } catch {
                // Clipboard permission denied — the textarea below is still selectable.
                setCopied(false);
              }
            })();
          }}
        >
          {copied ? "✓ 已複製" : "⧉ 複製全部錯誤"}
        </button>
        <span className="state-msg">複製出嚟係純文字原文，冇任何 UI 裝飾。</span>
      </div>
      <ul className="issue-list">
        {report.issues.map((issue) => (
          <li key={`${issue.layer}-${issue.path}-${issue.message}`}>
            <span className="chip chip--warn">
              {LAYER_LABELS[issue.layer] ?? issue.layer}
            </span>
            <code className="issue-list__line">{issue.line}</code>
          </li>
        ))}
      </ul>
      <label className="field">
        <span>貼返俾 AI 嘅原文</span>
        <textarea
          className="inp inp--area"
          readOnly
          rows={Math.min(12, report.issues.length + 1)}
          value={report.report_text}
        />
      </label>
    </section>
  );
}
