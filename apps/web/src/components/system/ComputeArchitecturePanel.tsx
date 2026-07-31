/**
 * Phase E: show Python authority / Rust compute architecture + error drill-down.
 */
import { useCallback, useEffect, useState } from "react";

import {
  fetchComputeErrorDetail,
  fetchComputeStatus,
  type ComputeErrorRecord,
  type ComputeStatus,
} from "../../api/client";
import {
  ENGINE_SUMMARY,
  acceleratorLabel,
  engineLabel,
  severityLabel,
} from "../../lib/humanTerms";
import { InfoButton } from "../ui/InfoButton";

function backendChip(backend: string | undefined): string {
  if (backend === "rust") return "chip chip--pass";
  if (backend === "python") return "chip chip--warn";
  return "chip";
}

function healthChip(health: string | undefined): string {
  if (health === "ok") return "chip chip--pass";
  if (health === "warn") return "chip chip--warn";
  if (health === "error") return "chip chip--fail";
  return "chip";
}

export function ComputeArchitecturePanel({
  compact = false,
}: {
  compact?: boolean;
}) {
  const [status, setStatus] = useState<ComputeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ComputeErrorRecord | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchComputeStatus();
      setStatus(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function openError(id: string) {
    setDetailLoading(true);
    try {
      const detail = await fetchComputeErrorDetail(id);
      setSelected(detail);
    } catch (err) {
      setSelected({
        id,
        feature: "unknown",
        severity: "error",
        message: "讀取錯誤詳情失敗",
        detail: err instanceof Error ? err.message : String(err),
        tip: "重新整理頁面再試一次。",
        at: new Date().toISOString(),
      });
    } finally {
      setDetailLoading(false);
    }
  }

  if (loading && !status) {
    return (
      <section className="panel" aria-label="計算架構">
        <p className="state-msg">載入計算架構狀態…</p>
      </section>
    );
  }

  if (error && !status) {
    return (
      <section className="panel" aria-label="計算架構">
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      </section>
    );
  }

  if (!status) return null;

  const errors = status.recent_errors ?? [];

  /*
   * Compact form: a single status line. On pages where the engine is context
   * rather than the task (回測 設定), four cards of backend detail push the
   * actual work below the fold for no benefit — the health chip plus the glyph
   * carries everything that matters, and the full panel still lives on 總覽.
   */
  if (compact) {
    return (
      <section
        className="compute-strip"
        aria-label="計算架構"
        data-testid="compute-architecture-panel"
      >
        <span className={healthChip(status.health)} data-testid="compute-health">
          {status.health === "ok"
            ? "系統正常"
            : status.health === "warn"
              ? "有警告"
              : status.health === "error"
                ? "有錯誤"
                : status.health ?? "—"}
        </span>
        <span className="compute-strip__facts">
          圖表{" "}
          <b data-testid="compute-chart-backend">
            {engineLabel(status.effective_chart_backend)}
          </b>{" "}
          · 回測{" "}
          <b data-testid="compute-backtest-backend">
            {engineLabel(status.effective_backtest_backend ?? "python")}
          </b>
        </span>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => void reload()}
        >
          重新探測
        </button>
        <InfoButton label="系統狀態點睇" align="end">
          <span data-testid="compute-summary">{ENGINE_SUMMARY}</span>
          <br />
          任何一項顯示「備用引擎」，即係快速引擎今次冇用到。完整四格喺 總覽 頁。
        </InfoButton>
      </section>
    );
  }

  return (
    <section
      className="panel compute-arch-panel"
      aria-label="計算架構"
      data-testid="compute-architecture-panel"
    >
      <div className="panel__head">
        <h2 className="panel__title">系統狀態</h2>
        <span
          className={healthChip(status.health)}
          data-testid="compute-health"
        >
          {status.health === "ok"
            ? "正常"
            : status.health === "warn"
              ? "有警告"
              : status.health === "error"
                ? "有錯誤"
                : status.health ?? "—"}
        </span>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => void reload()}
        >
          重新探測
        </button>
        {/* Architecture reference sits behind the glyph at the card's right
            edge — it is background, not something to re-read every visit. */}
        <InfoButton label="系統狀態點睇" align="end">
          {/* The backend's own summary string is engineering prose; the owner
              reads a fixed sentence instead. */}
          <span data-testid="compute-summary">{ENGINE_SUMMARY}</span>
          <br />
          四格分別係：邊個話事、圖表用邊個引擎計、回測用邊個、加速模組載咗未。
          任何一格顯示「備用引擎」，即係快速引擎嗰邊今次冇用到。
          {status.dylib_path ? (
            <>
              <br />
              加速模組位置：{status.dylib_path}
            </>
          ) : null}
        </InfoButton>
      </div>

      <div className="metrics-grid">
        <div className="metric-card">
          <span className="metric-card__label">邊個話事</span>
          <span className="metric-card__value metric-card__value--sm">
            <span className="chip chip--warn">主程式</span>
          </span>
          <small className="metric-card__foot">
            行情連接 · 模擬盤帳本 · 回測封存 · 拍板紀錄
          </small>
        </div>
        <div className="metric-card">
          <span className="metric-card__label">圖表計算</span>
          <span className="metric-card__value metric-card__value--sm">
            <span
              className={backendChip(status.effective_chart_backend)}
              data-testid="compute-chart-backend"
            >
              {engineLabel(status.effective_chart_backend)}
            </span>
          </span>
          <small className="metric-card__foot">
            {status.product_chart_will_try_rust
              ? "會優先用快速引擎"
              : "只用備用引擎"}
          </small>
        </div>
        <div className="metric-card">
          <span className="metric-card__label">回測計算</span>
          <span className="metric-card__value metric-card__value--sm">
            <span
              className={backendChip(
                status.effective_backtest_backend ?? "python",
              )}
              data-testid="compute-backtest-backend"
            >
              {engineLabel(status.effective_backtest_backend ?? "python")}
            </span>
          </span>
          <small className="metric-card__foot">
            {status.product_backtest_will_try_rust
              ? "會優先用快速引擎；唔得就自動轉備用"
              : "只用備用引擎"}
          </small>
        </div>
        <div className="metric-card">
          <span className="metric-card__label">加速模組</span>
          <span className="metric-card__value metric-card__value--sm">
            <span
              className={
                status.rust_admitted ? "chip chip--pass" : "chip chip--warn"
              }
            >
              {acceleratorLabel(status.rust_admitted)}
            </span>
          </span>
          {/* The install path is engineering detail — it belongs behind the
              glyph, not printed across the card in monospace. */}
          <small className="metric-card__foot">
            {status.rust_admitted ? "已載入，計算行得快啲" : "未載入，會用備用引擎"}
          </small>
        </div>
      </div>

      {/* No heading when there is nothing wrong — a clean run should look
          clean, not like an empty section someone forgot to fill. */}
      {!compact ? (
        <>
          {errors.length === 0 ? (
            <p className="state-msg" data-testid="compute-errors-empty">
              目前冇計算錯誤，亦冇轉用備用引擎嘅紀錄。
            </p>
          ) : (
            <ul className="compute-error-list" data-testid="compute-error-list">
              {errors.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="compute-error-list__btn"
                    onClick={() => void openError(item.id)}
                    data-testid={`compute-error-${item.id}`}
                  >
                    <span
                      className={
                        item.severity === "error"
                          ? "chip chip--fail"
                          : "chip chip--warn"
                      }
                    >
                      {severityLabel(item.severity)}
                    </span>
                    <span className="compute-error-list__msg">
                      {item.message}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}

      {selected ? (
        <div
          className="compute-error-detail"
          role="dialog"
          aria-label="計算錯誤詳情"
          data-testid="compute-error-detail"
        >
          <div className="compute-error-detail__head">
            <strong>
              {detailLoading ? "載入中…" : selected.message}
            </strong>
            <button
              type="button"
              className="btn"
              onClick={() => setSelected(null)}
            >
              關閉
            </button>
          </div>
          <p>
            <span className="chip">{severityLabel(selected.severity)}</span>
            {selected.fallback_used ? (
              <span className="chip chip--warn">已轉用備用引擎</span>
            ) : null}
          </p>
          <pre className="compute-error-detail__pre">{selected.detail}</pre>
          <p className="panel__note">
            <strong>提示：</strong>
            {selected.tip}
          </p>
          {selected.context && Object.keys(selected.context).length > 0 ? (
            <pre className="compute-error-detail__pre">
              {JSON.stringify(selected.context, null, 2)}
            </pre>
          ) : null}
          <small className="metric-card__foot">{selected.at}</small>
        </div>
      ) : null}
    </section>
  );
}
