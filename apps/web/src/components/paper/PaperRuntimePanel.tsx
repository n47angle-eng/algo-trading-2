import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchPaperTraderActivity,
  fetchPaperTraderChart,
  fetchPaperTraderRuntime,
  fetchPaperTraderTimeline,
  postPaperReviewV2Download,
  postPaperRuntimeCommand,
  postPaperRuntimeReplay,
} from "../../api/client";
import {
  processPaperActivityNotifications,
  processPaperSnapshotNotifications,
  type PaperActivityItem,
} from "../../lib/notifications";
import { optimisticLifecycleAfter } from "../../lib/optimistic";
import {
  parsePaperRuntimeSnapshot,
  type RuntimeParseResult,
} from "../../lib/paper/runtimeContract";
import type { PaperRuntimeSnapshot } from "../../lib/paper/runtimeTypes";
import { invalidateGetCache } from "../../lib/httpCache";

interface TimelineItem {
  cursor: number;
  created_at: string;
  payload: {
    lifecycle?: string;
    reason?: string;
  };
}

interface ChartBar {
  cursor: number;
  event_at: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

const POLL_MS = 2_000;
const LIVE_LIFECYCLES = new Set([
  "running",
  "starting",
  "pausing",
  "stopping",
  "tripped",
]);

/**
 * Runtime status / controls / timeline / chart / paper-review.v2.
 * Soft revalidation + optimistic lifecycle — no blank flash on refresh.
 * Polls snapshot + activity while live so trade notifications surface.
 */
export function PaperRuntimePanel({ traderId }: { traderId: string }) {
  const [snap, setSnap] = useState<PaperRuntimeSnapshot | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [bars, setBars] = useState<ChartBar[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reviewMsg, setReviewMsg] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const snapRef = useRef<PaperRuntimeSnapshot | null>(null);
  const activityCursorRef = useRef<number>(0);
  const seededActivityRef = useRef(false);
  const pollInFlightRef = useRef(false);
  snapRef.current = snap;

  const reload = useCallback(async (opts?: { soft?: boolean }) => {
    const soft = opts?.soft === true || snapRef.current !== null;
    if (soft) {
      setRefreshing(true);
    }
    setError(null);
    // Cursor-aware activity page: never re-fetch only the first 100 forever.
    // Seed advances the cursor without notifying; live polls use after_cursor.
    const activityAfter = activityCursorRef.current;
    const activityLimit = 200;
    try {
      const [runtimeResult, timelineResult, chartResult, activityResult] =
        await Promise.all([
          fetchPaperTraderRuntime(traderId),
          fetchPaperTraderTimeline(traderId),
          fetchPaperTraderChart(traderId),
          fetchPaperTraderActivity(traderId, activityAfter, activityLimit),
        ]);
      if (!runtimeResult.ok) {
        if (!soft) {
          setSnap(null);
          setTimeline([]);
          setBars([]);
        }
        setError(
          runtimeResult.status === 404
            ? "呢個交易員暫時未有模擬 runtime 記錄（可能建立時未同步到 runtime 庫）。"
            : "模擬 runtime 暫時核實唔到。",
        );
        return;
      }
      const parsed: RuntimeParseResult<PaperRuntimeSnapshot> =
        parsePaperRuntimeSnapshot(runtimeResult.body);
      if (!parsed.ok) {
        if (!soft) {
          setSnap(null);
        }
        setError("模擬 runtime 回傳格式唔正確，已停低唔顯示假數。");
        return;
      }
      const previous = snapRef.current;
      setSnap(parsed.value);
      setError(null);

      // Snapshot path: position / decision / trade_count deltas.
      if (previous && previous.trader_id === parsed.value.trader_id) {
        void processPaperSnapshotNotifications({
          traderId: parsed.value.trader_id,
          previousPositionQty: previous.position_quantity,
          nextPositionQty: parsed.value.position_quantity,
          previousTradeCount: previous.trade_count,
          nextTradeCount: parsed.value.trade_count,
          previousDecisionCount: previous.decision_count,
          nextDecisionCount: parsed.value.decision_count,
          realizedPnl: parsed.value.realized_pnl,
          realizedR: parsed.value.realized_r,
          asOf: parsed.value.as_of,
        });
      }

      if (
        timelineResult.ok &&
        timelineResult.body &&
        typeof timelineResult.body === "object"
      ) {
        const body = timelineResult.body as { items?: TimelineItem[] };
        setTimeline(Array.isArray(body.items) ? body.items : []);
      }

      // Activity path: real intents/fills/trades (not lifecycle timeline).
      if (
        activityResult.ok &&
        activityResult.body &&
        typeof activityResult.body === "object"
      ) {
        const body = activityResult.body as {
          items?: PaperActivityItem[];
          next_cursor?: number;
        };
        const items = Array.isArray(body.items) ? body.items : [];
        const pageMax = items.reduce(
          (m, it) => Math.max(m, it.cursor),
          activityAfter,
        );
        const nextCursor =
          typeof body.next_cursor === "number" && body.next_cursor > pageMax
            ? body.next_cursor
            : pageMax;

        if (!seededActivityRef.current) {
          // Drain history without spam: advance cursor; if page was full,
          // leave unseeded so the next soft poll continues without notify.
          activityCursorRef.current = nextCursor;
          if (items.length < activityLimit) {
            seededActivityRef.current = true;
          }
        } else {
          // Live: process only items after the cursor we requested.
          void processPaperActivityNotifications(
            traderId,
            items,
            activityAfter,
          ).then((advanced) => {
            activityCursorRef.current = Math.max(advanced, nextCursor);
          });
        }
      }

      if (
        chartResult.ok &&
        chartResult.body &&
        typeof chartResult.body === "object"
      ) {
        const body = chartResult.body as { bars?: ChartBar[] };
        setBars(Array.isArray(body.bars) ? body.bars : []);
      }
    } catch {
      if (!soft) {
        setSnap(null);
      }
      setError("模擬 runtime 暫時連唔上。");
    } finally {
      setRefreshing(false);
    }
  }, [traderId]);

  useEffect(() => {
    activityCursorRef.current = 0;
    seededActivityRef.current = false;
    void reload({ soft: false });
  }, [reload]);

  // Continuous soft poll while lifecycle is live so notifications fire
  // without requiring Owner to click refresh / command.
  useEffect(() => {
    const lifecycle = snap?.lifecycle;
    if (!lifecycle || !LIVE_LIFECYCLES.has(lifecycle)) {
      return;
    }
    const timer = window.setInterval(() => {
      if (pollInFlightRef.current || busy) {
        return;
      }
      pollInFlightRef.current = true;
      void reload({ soft: true }).finally(() => {
        pollInFlightRef.current = false;
      });
    }, POLL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [snap?.lifecycle, busy, reload]);

  async function runCommand(
    command: "start" | "pause" | "resume" | "permanent-stop",
  ) {
    if (!snap) {
      return;
    }
    setBusy(true);
    setError(null);
    const prior = snap;
    // Optimistic: paint new lifecycle immediately (no blank).
    setSnap({
      ...prior,
      lifecycle: optimisticLifecycleAfter(
        command,
        prior.lifecycle,
      ) as PaperRuntimeSnapshot["lifecycle"],
      lifecycle_version: prior.lifecycle_version + 1,
      lifecycle_reason: "本地樂觀更新 · 等伺服器確認",
    });
    try {
      const result = await postPaperRuntimeCommand(traderId, command, {
        request_id: crypto.randomUUID(),
        expected_lifecycle_version: prior.lifecycle_version,
        selection_fingerprint: prior.selection_fingerprint,
      });
      if (!result.ok) {
        setSnap(prior);
        setError("操作未成功；已還原畫面。");
        return;
      }
      invalidateGetCache("/api/v1/paper/fleet-overview");
      await reload({ soft: true });
    } catch {
      setSnap(prior);
      setError("操作暫時失敗；已還原畫面。");
    } finally {
      setBusy(false);
    }
  }

  async function runDemoReplay() {
    if (!snap) {
      return;
    }
    setBusy(true);
    setError(null);
    const prior = snap;
    // Optimistic: bump decision/trade hints while bars process.
    setSnap({
      ...prior,
      lifecycle: "running",
      lifecycle_reason: "示範行情處理中…",
    });
    try {
      if (prior.lifecycle === "provisioned" || prior.lifecycle === "paused") {
        const started = await postPaperRuntimeCommand(traderId, "start", {
          request_id: crypto.randomUUID(),
          expected_lifecycle_version: prior.lifecycle_version,
          selection_fingerprint: prior.selection_fingerprint,
        });
        if (!started.ok) {
          setSnap(prior);
          setError("開始模擬未成功，未有餵行情。");
          return;
        }
      }
      const result = await postPaperRuntimeReplay(traderId, {
        use_demo_bars: true,
        mode: "replay_test",
      });
      if (!result.ok) {
        setSnap(prior);
        setError("示範行情未成功寫入；未有假成交。");
        return;
      }
      // Apply server snapshot from replay body if present (instant, no flash).
      if (result.body && typeof result.body === "object") {
        const body = result.body as {
          snapshot?: {
            lifecycle?: string;
            lifecycle_version?: number;
            cash?: number;
            equity?: number;
            realized_pnl?: number;
            unrealized_pnl?: number;
            position_quantity?: number;
            decision_count?: number;
            trade_count?: number;
          };
        };
        if (body.snapshot) {
          setSnap((cur) =>
            cur
              ? {
                  ...cur,
                  lifecycle: (body.snapshot!.lifecycle ??
                    cur.lifecycle) as PaperRuntimeSnapshot["lifecycle"],
                  lifecycle_version:
                    body.snapshot!.lifecycle_version ?? cur.lifecycle_version,
                  cash: body.snapshot!.cash ?? cur.cash,
                  equity: body.snapshot!.equity ?? cur.equity,
                  realized_pnl: body.snapshot!.realized_pnl ?? cur.realized_pnl,
                  unrealized_pnl:
                    body.snapshot!.unrealized_pnl ?? cur.unrealized_pnl,
                  position_quantity:
                    body.snapshot!.position_quantity ?? cur.position_quantity,
                  decision_count:
                    body.snapshot!.decision_count ?? cur.decision_count,
                  trade_count: body.snapshot!.trade_count ?? cur.trade_count,
                  lifecycle_reason: "示範行情已套用",
                }
              : cur,
          );
        }
      }
      invalidateGetCache("/api/v1/paper/fleet-overview");
      await reload({ soft: true });
    } catch {
      setSnap(prior);
      setError("示範行情暫時失敗。");
    } finally {
      setBusy(false);
    }
  }

  async function exportReviewV2() {
    setBusy(true);
    setReviewMsg(null);
    try {
      const result = await postPaperReviewV2Download(traderId, {
        request_id: crypto.randomUUID(),
      });
      if (!result.ok || result.schema !== "paper-review.v2") {
        setReviewMsg("匯出模擬對照包失敗（已 fail-closed，未下載假包）。");
        return;
      }
      const blob = new Blob([result.bytes], { type: "application/zip" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `paper-review-v2-${traderId.slice(-8)}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      setReviewMsg("已下載 paper-review.v2 不可變快照。");
    } catch {
      setReviewMsg("匯出暫時失敗。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="paper-panel" aria-label="模擬 runtime">
      <h3 className="paper-step">模擬 runtime（IB 行情驅動嘅 app 自家模擬）</h3>
      <p className="paper-hint">
        建立交易員同開始模擬係兩步。撳「開始模擬交易」會自動試連 IB Gateway
        （127.0.0.1:7498 · client 7 · 只讀行情 · 永不發單）。Gateway
        未開時仍可「餵示範行情」測策略路徑；你開咗 Gateway 再開始就會收真 bar。
      </p>
      {refreshing ? (
        <p className="paper-hint" role="status">
          背景更新中…
        </p>
      ) : null}
      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}
      {snap ? (
        <>
          <dl className="paper-confirm">
            <div>
              <dt>狀態</dt>
              <dd>{lifecycleCopy(snap.lifecycle)}</dd>
            </div>
            <div>
              <dt>權益</dt>
              <dd>
                {snap.equity.toFixed(2)}（已實現 {snap.realized_pnl.toFixed(2)} ·
                未實現 {snap.unrealized_pnl.toFixed(2)}）
              </dd>
            </div>
            <div>
              <dt>持倉</dt>
              <dd>{snap.position_quantity}</dd>
            </div>
            <div>
              <dt>安全網</dt>
              <dd>
                回撤 {snap.safety.drawdown_r.toFixed(2)}R /{" "}
                {snap.safety.max_drawdown_r}R · 連蝕{" "}
                {snap.safety.losing_streak} / {snap.safety.max_losing_streak}
              </dd>
            </div>
            <div>
              <dt>決定 / 成交單</dt>
              <dd>
                {snap.decision_count} / {snap.trade_count}
              </dd>
            </div>
          </dl>

          <h4 className="paper-step">時間線</h4>
          {timeline.length === 0 ? (
            <p className="paper-hint">暫時未有 lifecycle 事件。</p>
          ) : (
            <ul className="paper-checks">
              {timeline.map((item) => (
                <li key={item.cursor} className="paper-check">
                  <span className="paper-check__name">
                    {item.payload.lifecycle ?? "事件"} · cursor {item.cursor}
                  </span>
                  <span>{item.payload.reason ?? item.created_at}</span>
                </li>
              ))}
            </ul>
          )}

          <h4 className="paper-step">圖表（1 分鐘）</h4>
          {bars.length === 0 ? (
            <p className="paper-hint">
              暫時未有行情 bar（開始後餵示範行情或接 Gateway）。
            </p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>時間</th>
                    <th>開</th>
                    <th>高</th>
                    <th>低</th>
                    <th>收</th>
                  </tr>
                </thead>
                <tbody>
                  {bars.map((bar) => (
                    <tr key={bar.cursor}>
                      <td className="table__mono">{bar.event_at}</td>
                      <td className="table__mono">{bar.open}</td>
                      <td className="table__mono">{bar.high}</td>
                      <td className="table__mono">{bar.low}</td>
                      <td className="table__mono">{bar.close}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="paper-modal__actions">
            <button
              type="button"
              className="paper-button paper-button--primary"
              disabled={busy || snap.lifecycle === "running"}
              onClick={() => {
                void runCommand("start");
              }}
            >
              開始模擬交易
            </button>
            <button
              type="button"
              className="paper-button"
              disabled={busy}
              onClick={() => {
                void runDemoReplay();
              }}
            >
              餵示範行情（app 自家模擬）
            </button>
            <button
              type="button"
              className="paper-button"
              disabled={busy || snap.lifecycle !== "running"}
              onClick={() => {
                void runCommand("pause");
              }}
            >
              暫停
            </button>
            <button
              type="button"
              className="paper-button"
              disabled={busy || snap.lifecycle !== "paused"}
              onClick={() => {
                void runCommand("resume");
              }}
            >
              繼續
            </button>
            <button
              type="button"
              className="paper-button paper-button--danger"
              disabled={busy || snap.lifecycle === "permanently_stopped"}
              onClick={() => {
                void runCommand("permanent-stop");
              }}
            >
              永久停止
            </button>
            <button
              type="button"
              className="paper-button"
              disabled={busy}
              onClick={() => {
                void exportReviewV2();
              }}
            >
              匯出 paper-review.v2
            </button>
          </div>
        </>
      ) : null}
      {reviewMsg ? <p className="paper-hint">{reviewMsg}</p> : null}
    </section>
  );
}

function lifecycleCopy(lifecycle: string): string {
  const map: Record<string, string> = {
    provisioned: "已建立 · 未開始",
    starting: "啟動中",
    running: "運行中",
    pausing: "暫停中（等可信價）",
    paused: "已暫停",
    tripped: "安全網觸發",
    stopping: "永久停止中",
    recovery_required: "需要你手動恢復",
    permanently_stopped: "已永久停止",
  };
  return map[lifecycle] ?? "狀態暫時核實唔到";
}
