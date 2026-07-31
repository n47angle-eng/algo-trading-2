/**
 * Phase F: on-demand shadow compare report (Python MTF vs Rust).
 */
import { useState } from "react";

import {
  fetchP5RunChartShadowCompare,
  fetchRunChartShadowCompare,
} from "../../api/client";
import type {
  ChartShadowCompareResponse,
  ChartTimeframe,
} from "../../api/chartTypes";

interface ChartShadowPanelProps {
  runId: string;
  /** Default timeframe for the compare (product path uses 5m first). */
  timeframe?: ChartTimeframe;
  /**
   * Use raw JSON client (ChartViewer) vs P5 envelope client (RunDetail).
   * Default: p5 envelope.
   */
  transport?: "p5" | "json";
}

function verdictClass(verdict: string): string {
  const v = verdict.toUpperCase();
  if (v === "PASS") return "chart-shadow-panel__verdict--pass";
  if (v === "DIFF") return "chart-shadow-panel__verdict--diff";
  return "chart-shadow-panel__verdict--skip";
}

function verdictLabel(verdict: string): string {
  const v = verdict.toUpperCase();
  if (v === "PASS") return "PASS · 同 window 近似一致";
  if (v === "DIFF") return "DIFF · 有差異（見 max abs）";
  if (v === "SKIPPED") return "SKIPPED · 未跑到 Rust";
  if (v === "INCOMPARABLE") return "INCOMPARABLE · 無共用 bar";
  return verdict;
}

export function ChartShadowPanel({
  runId,
  timeframe = "5m",
  transport = "p5",
}: ChartShadowPanelProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ChartShadowCompareResponse | null>(null);

  async function runCompare() {
    setBusy(true);
    setError(null);
    try {
      if (transport === "json") {
        const body = await fetchRunChartShadowCompare(runId, timeframe);
        setReport(body);
        return;
      }
      const http = await fetchP5RunChartShadowCompare(runId, timeframe);
      if (!http.ok || !http.jsonParsed || !http.body || typeof http.body !== "object") {
        setError(`shadow compare 失敗（HTTP ${http.status}）`);
        setReport(null);
        return;
      }
      setReport(http.body as ChartShadowCompareResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setReport(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chart-shadow-panel" data-testid="chart-shadow-panel">
      <div className="chart-shadow-panel__head">
        <h3 className="panel__title" style={{ margin: 0, fontSize: "0.95rem" }}>
          Shadow compare（{timeframe}）
        </h3>
        <button
          type="button"
          className="btn"
          data-testid="chart-shadow-run"
          disabled={busy}
          onClick={() => {
            void runCompare();
          }}
        >
          {busy ? "對比中…" : "跑 Python MTF vs Rust"}
        </button>
        {report ? (
          <span
            className={`chart-shadow-panel__verdict ${verdictClass(String(report.verdict))}`}
            data-testid="chart-shadow-verdict"
          >
            {verdictLabel(String(report.verdict))}
          </span>
        ) : null}
      </div>
      <p className="panel__note" style={{ marginTop: 0 }}>
        同一 lookback / visible window：reference = Python MTF，candidate = Rust。
        用嚟做 value gate，唔會改帳本。
      </p>
      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}
      {report ? (
        <div className="chart-shadow-panel__stats" data-testid="chart-shadow-stats">
          <span>
            ref candles: {report.reference?.candle_count ?? "—"} ·{" "}
            {report.reference?.compute_provenance?.effective_backend ?? "python"}
          </span>
          <span>
            cand candles: {report.candidate?.candle_count ?? "—"} ·{" "}
            {report.candidate?.compute_provenance?.effective_backend ?? "—"}
          </span>
          <span>
            shared: {report.timeline?.shared_count ?? "—"} · only-ref:{" "}
            {report.timeline?.only_in_reference_count ?? "—"} · only-cand:{" "}
            {report.timeline?.only_in_candidate_count ?? "—"}
          </span>
          <span>
            max |Δ OHLC|:{" "}
            {report.summary?.max_abs_ohlc_diff != null
              ? report.summary.max_abs_ohlc_diff.toPrecision(4)
              : report.ohlc?.max_abs_diff != null
                ? report.ohlc.max_abs_diff.toPrecision(4)
                : "—"}
          </span>
          <span>
            max |Δ EMA|:{" "}
            {report.summary?.max_abs_ema_diff != null
              ? report.summary.max_abs_ema_diff.toPrecision(4)
              : "—"}
          </span>
          {report.reason ? <span>reason: {report.reason}</span> : null}
          {report.note ? <span>{report.note}</span> : null}
        </div>
      ) : null}
    </div>
  );
}
