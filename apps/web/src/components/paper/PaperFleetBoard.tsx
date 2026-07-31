import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  fetchPaperFleetOverview,
  type PaperHttpResult,
} from "../../api/client";

export interface FleetTraderRow {
  trader_id: string;
  strategy_id?: string | null;
  contract_id?: string | null;
  baseline_run_id?: string | null;
  lifecycle: string;
  lifecycle_reason?: string | null;
  cash?: number | null;
  equity?: number | null;
  realized_pnl?: number | null;
  unrealized_pnl?: number | null;
  realized_r?: number | null;
  unrealized_r?: number | null;
  position_quantity?: number | null;
  decision_count?: number | null;
  trade_count?: number | null;
  drawdown_r?: number | null;
  losing_streak?: number | null;
  pending_intent_count?: number | null;
  runtime_available?: boolean;
  currency?: string | null;
  initial_capital?: number | null;
}

interface FleetOverview {
  schema: string;
  gateway: {
    status: string;
    message: string;
    port_reachable?: boolean;
    host?: string;
    port?: number;
    last_bar_at?: string | null;
  };
  totals: {
    trader_count: number;
    running_count: number;
    open_position_count: number;
    total_realized_pnl: number;
    total_unrealized_pnl: number;
    total_equity: number;
  };
  traders: FleetTraderRow[];
  closed_loop?: {
    from_results?: string;
    from_strategies?: string;
    data?: string;
    backtest?: string;
  };
}

/**
 * P6 multi-trader board — all traders' lifecycle, entry/position, PnL, R multiple.
 * Design: design §14.1 overview cards + closed-loop links to other pages.
 */
export function PaperFleetBoard({
  onOpenTrader,
}: {
  onOpenTrader: (traderId: string) => void;
}) {
  const [data, setData] = useState<FleetOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const dataRef = useRef<FleetOverview | null>(null);
  dataRef.current = data;

  const reload = useCallback(async (opts?: { soft?: boolean }) => {
    const soft = opts?.soft === true || dataRef.current !== null;
    setError(null);
    if (soft) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    try {
      const result: PaperHttpResult = await fetchPaperFleetOverview();
      if (!result.ok || !result.body || typeof result.body !== "object") {
        // Soft: keep prior board; only hard-empty on first load.
        if (!soft) {
          setData(null);
        }
        setError("模擬盤總覽暫時讀唔到。");
        return;
      }
      const body = result.body as FleetOverview;
      if (body.schema !== "paper_fleet_overview.v1") {
        if (!soft) {
          setData(null);
        }
        setError("總覽回傳格式唔正確，已停低唔顯示假數。");
        return;
      }
      setData(body);
      setError(null);
    } catch {
      if (!soft) {
        setData(null);
      }
      setError("模擬盤總覽暫時連唔上。");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void reload({ soft: false });
    // Poll only while the tab is visible — soft, no blanking.
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void reload({ soft: true });
      }
    }, 8_000);
    const onVis = () => {
      if (document.visibilityState === "visible") {
        void reload({ soft: true });
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [reload]);

  if (loading && !data) {
    return <p className="paper-hint">載入所有交易員狀態…</p>;
  }
  if (error) {
    return (
      <p className="state-msg state-msg--error" role="alert">
        {error}
      </p>
    );
  }
  if (!data) {
    return null;
  }

  const gw = data.gateway;
  const gwTone =
    gw.status === "live" || gw.status === "test_delayed"
      ? "ok"
      : gw.port_reachable
        ? "warn"
        : "muted";

  return (
    <div className="paper-fleet">
      <header className="paper-fleet__header">
        <div>
          <h2 className="paper-step" style={{ marginTop: 0 }}>
            模擬盤總覽
          </h2>
          <p className="paper-hint">
            所有交易員一頁睇晒：狀態、入市情況、盈虧、風險回報。App
            只向 IB 攞行情，永遠唔會代你發單。
          </p>
        </div>
        <button
          type="button"
          className="paper-button paper-button--ghost"
          disabled={refreshing}
          onClick={() => {
            void reload({ soft: true });
          }}
        >
          {refreshing ? "更新緊…" : "重新整理"}
        </button>
      </header>

      {data.traders.length > 0 ? (
        <div className="paper-fleet__metrics" role="group" aria-label="整體數字">
          <div className="paper-fleet__metric">
            <span className="paper-fleet__metric-label">交易員</span>
            <span className="paper-fleet__metric-value">
              {data.totals.trader_count}
            </span>
            <span className="paper-fleet__metric-foot">
              運行中 {data.totals.running_count}
            </span>
          </div>
          <div className="paper-fleet__metric">
            <span className="paper-fleet__metric-label">開倉中</span>
            <span className="paper-fleet__metric-value">
              {data.totals.open_position_count}
            </span>
          </div>
          <div className="paper-fleet__metric">
            <span className="paper-fleet__metric-label">已實現盈虧</span>
            <span
              className={`paper-fleet__metric-value ${pnlClass(data.totals.total_realized_pnl)}`}
            >
              {fmtMoney(data.totals.total_realized_pnl)}
            </span>
          </div>
          <div className="paper-fleet__metric">
            <span className="paper-fleet__metric-label">未實現盈虧</span>
            <span
              className={`paper-fleet__metric-value ${pnlClass(data.totals.total_unrealized_pnl)}`}
            >
              {fmtMoney(data.totals.total_unrealized_pnl)}
            </span>
          </div>
          <div className="paper-fleet__metric">
            <span className="paper-fleet__metric-label">合計權益</span>
            <span className="paper-fleet__metric-value">
              {fmtMoney(data.totals.total_equity)}
            </span>
          </div>
        </div>
      ) : null}

      <section
        className={`paper-gateway paper-gateway--${gwTone}`}
        aria-label="IB 行情狀態"
      >
        <div className="paper-gateway__row">
          <strong>IB 行情（Gateway）</strong>
          <span className="chip">{gatewayLabel(gw.status)}</span>
        </div>
        <p className="paper-hint">{gw.message}</p>
        <p className="paper-hint">
          {gw.host ?? "127.0.0.1"}:{gw.port ?? 7498}
          {gw.port_reachable ? " · port 可達" : " · port 未開"}
          {gw.last_bar_at ? ` · 最近一根 ${gw.last_bar_at}` : ""}
          {" · 零 IB 落單路徑"}
        </p>
        {!gw.port_reachable ? (
          <p className="paper-hint">
            開 <b>IBKR Gateway（Simulated Trading）</b>、勾 Read-Only API、port
            7498 後，喺交易員詳情撳「開始模擬交易」就會自動連行情。Gateway
            未開時可用「餵示範行情」測策略路徑。
          </p>
        ) : null}
      </section>

      <nav className="paper-loop" aria-label="相關頁捷徑">
        <Link className="paper-button paper-button--ghost" to="/strategies">
          策略工作台
        </Link>
        <Link className="paper-button paper-button--ghost" to="/backtest">
          回測
        </Link>
        <Link className="paper-button paper-button--ghost" to="/results">
          結果
        </Link>
        <Link className="paper-button paper-button--ghost" to="/data">
          數據覆蓋
        </Link>
      </nav>

      {data.traders.length === 0 ? (
        <div className="paper-empty">
          <p>
            <b>你而家冇任何交易員。</b>
          </p>
          <p>模擬引擎尚未啟用。</p>
          <p>
            而家做得到嘅係喺「＋
            新增交易員」建立一個交易員身份同佢自己嘅獨立帳戶起點。
          </p>
          <p>
            每頁獨立讀資料庫：喺「＋新增交易員」直接揀已確認策略（唔使先過結果頁批准）。
            若策略未有可用回測對照基準，會顯示暫時冇選項——唔係上一頁交接失敗。
          </p>
        </div>
      ) : (
        <ul className="paper-trader-cards paper-trader-cards--fleet">
          {data.traders.map((row) => (
            <li key={row.trader_id} className="paper-trader-card">
              <div className="paper-trader-card__top">
                <p className="paper-trader-card__name">
                  {row.strategy_id ?? "—"} · {row.contract_id ?? "—"}
                </p>
                <span className={`chip ${lifecycleChip(row.lifecycle)}`}>
                  {lifecycleLabel(row.lifecycle)}
                </span>
              </div>
              <p className="paper-trader-card__line">
                入市情況：{positionLabel(row.position_quantity)}
                {row.pending_intent_count
                  ? ` · 待成交意圖 ${row.pending_intent_count}`
                  : ""}
              </p>
              <p className="paper-trader-card__line">
                權益：{fmtOpt(row.equity)} · 已實現{" "}
                <span className={pnlClass(row.realized_pnl)}>
                  {fmtOpt(row.realized_pnl)}
                </span>{" "}
                · 未實現{" "}
                <span className={pnlClass(row.unrealized_pnl)}>
                  {fmtOpt(row.unrealized_pnl)}
                </span>
              </p>
              <p className="paper-trader-card__line">
                風險回報：已實現 R {fmtR(row.realized_r)} · 未實現 R{" "}
                {fmtR(row.unrealized_r)} · 回撤 {fmtR(row.drawdown_r)} · 連蝕{" "}
                {row.losing_streak ?? "—"}
              </p>
              <p className="paper-trader-card__line">
                決定／成交：{row.decision_count ?? "—"} / {row.trade_count ?? "—"}
                {!row.runtime_available
                  ? " · runtime 未同步（建立後會自動接）"
                  : ""}
              </p>
              <p className="paper-trader-card__line paper-trader-card__muted">
                對照基準：{row.baseline_run_id ?? "—"}
              </p>
              <button
                type="button"
                className="paper-button paper-button--primary"
                onClick={() => {
                  onOpenTrader(row.trader_id);
                }}
              >
                打開詳情 · 開始／暫停／匯出
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function fmtMoney(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function fmtOpt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) {
    return "—";
  }
  return fmtMoney(n);
}

function fmtR(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) {
    return "—";
  }
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}R`;
}

function pnlClass(n: number | null | undefined): string {
  if (n === null || n === undefined || n === 0) {
    return "";
  }
  return n > 0 ? "paper-pnl--pos" : "paper-pnl--neg";
}

function positionLabel(q: number | null | undefined): string {
  if (q === null || q === undefined) {
    return "未有 runtime";
  }
  if (q === 0) {
    return "空手";
  }
  return q > 0 ? `好倉 ${q}` : `淡倉 ${Math.abs(q)}`;
}

function lifecycleLabel(lifecycle: string): string {
  const map: Record<string, string> = {
    provisioned: "已建立 · 未開始",
    starting: "啟動中",
    running: "運行中",
    pausing: "暫停中",
    paused: "已暫停",
    tripped: "安全網觸發",
    stopping: "停止中",
    recovery_required: "要你手動恢復",
    permanently_stopped: "已永久停止",
  };
  return map[lifecycle] ?? lifecycle;
}

function lifecycleChip(lifecycle: string): string {
  if (lifecycle === "running") {
    return "chip--pass";
  }
  if (lifecycle === "tripped" || lifecycle === "permanently_stopped") {
    return "chip--warn";
  }
  return "";
}

function gatewayLabel(status: string): string {
  const map: Record<string, string> = {
    live: "LIVE 行情",
    test_delayed: "延遲測試行情",
    connecting: "連接中",
    disconnected: "未連接",
    unavailable: "Gateway 未開",
    error: "連接失敗",
  };
  return map[status] ?? status;
}
