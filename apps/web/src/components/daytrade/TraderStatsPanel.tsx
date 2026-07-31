import { InfoButton } from "../ui/InfoButton";
import type { DaytradeStats } from "../../lib/daytrade/types";

type WindowKey = "today" | "7d" | "30d" | "all";

type Props = {
  stats: DaytradeStats | null;
  window: WindowKey;
  onWindowChange: (w: WindowKey) => void;
  loading?: boolean;
};

const WINDOWS: { id: WindowKey; label: string }[] = [
  { id: "today", label: "今日" },
  { id: "7d", label: "7 日" },
  { id: "30d", label: "30 日" },
  { id: "all", label: "全部" },
];

export function TraderStatsPanel({
  stats,
  window,
  onWindowChange,
  loading,
}: Props) {
  return (
    <div className="daytrade-stats">
      <div className="daytrade-stats__head">
        <div className="panel__head">
          <h2>邏輯統計</h2>
          <InfoButton label="統計點計" align="end">
            全部由帳本事件重播：OPEN 配對 STOP/TARGET/FORCE_FLAT。勝率 = 贏 /
            (贏+輸)，唔計打和。期望值 = 每筆平均盈虧；期望 R = 盈虧 ÷ 入場 1R。
            Live 用 1m_close，唔好比對 1s_worst 回測。樣本少於 20 筆時勝率只供參考。
          </InfoButton>
        </div>
        <div className="daytrade-stats__windows" role="tablist" aria-label="統計窗口">
          {WINDOWS.map((w) => (
            <button
              key={w.id}
              type="button"
              role="tab"
              aria-selected={window === w.id}
              className={window === w.id ? "is-active" : undefined}
              onClick={() => onWindowChange(w.id)}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {loading && !stats ? <p className="muted">載入統計…</p> : null}

      {stats ? (
        <>
          {!stats.sample_sufficient && (stats.closed_trade_count ?? 0) > 0 ? (
            <p className="daytrade-stats__warn" role="status">
              樣本不足（已平倉 {stats.closed_trade_count} 筆 &lt; 20）— 勝率／期望值只供參考，唔好當最終結論。
            </p>
          ) : null}

          <div className="daytrade-page__kpis daytrade-stats__kpis">
            <Kpi label="已平倉" value={String(stats.closed_trade_count)} />
            <Kpi
              label="勝 / 負"
              value={`${stats.wins} / ${stats.losses}`}
            />
            <Kpi
              label="勝率"
              value={
                stats.win_rate == null
                  ? "—"
                  : `${(stats.win_rate * 100).toFixed(1)}%`
              }
            />
            <Kpi
              label="總盈虧"
              value={fmt(stats.total_closed_pnl)}
              tone={stats.total_closed_pnl >= 0 ? "up" : "down"}
            />
            <Kpi label="期望值 $" value={fmt(stats.expectancy)} />
            <Kpi
              label="期望 R"
              value={
                stats.expectancy_r == null
                  ? "—"
                  : stats.expectancy_r.toFixed(2)
              }
            />
            <Kpi
              label="Profit factor"
              value={
                stats.profit_factor_infinite
                  ? "∞"
                  : stats.profit_factor == null
                    ? "—"
                    : stats.profit_factor.toFixed(2)
              }
            />
            <Kpi
              label="最大回撤"
              value={`${(stats.max_drawdown * 100).toFixed(2)}%`}
            />
            <Kpi
              label="連勝 / 連敗"
              value={`${stats.max_win_streak} / ${stats.max_loss_streak}`}
            />
            <Kpi
              label="總回報"
              value={
                stats.total_return == null
                  ? "—"
                  : `${(stats.total_return * 100).toFixed(2)}%`
              }
              tone={
                stats.total_return == null
                  ? undefined
                  : stats.total_return >= 0
                    ? "up"
                    : "down"
              }
            />
            <Kpi
              label="交易日 / 活躍"
              value={`${stats.trading_days ?? "—"} / ${stats.active_days ?? "—"}`}
            />
            <Kpi
              label="日終清倉率"
              value={
                stats.flat_compliance_rate == null
                  ? "—"
                  : `${(stats.flat_compliance_rate * 100).toFixed(0)}%`
              }
            />
          </div>

          {stats.exit_mix && Object.keys(stats.exit_mix).length > 0 ? (
            <div className="daytrade-stats__mix">
              <h3>離場分佈</h3>
              <ul>
                {Object.entries(stats.exit_mix).map(([k, n]) => (
                  <li key={k}>
                    <span>{exitLabel(k)}</span>
                    <strong>{n}</strong>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <p className="muted">
            {stats.formula_version} · {stats.live_scale_label}
          </p>
        </>
      ) : null}
    </div>
  );
}

function Kpi({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  return (
    <div>
      <span>{label}</span>
      <strong className={tone === "up" ? "is-up" : tone === "down" ? "is-down" : undefined}>
        {value}
      </strong>
    </div>
  );
}

function fmt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function exitLabel(kind: string): string {
  switch (kind) {
    case "TARGET":
      return "目標";
    case "STOP":
      return "止蝕";
    case "FORCE_FLAT":
      return "強平";
    case "CLOSE":
      return "平倉";
    default:
      return kind;
  }
}
