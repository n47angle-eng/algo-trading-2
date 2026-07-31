import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { DaytradeEquityChart } from "../components/daytrade/DaytradeEquityChart";
import { DaytradeMarketChart } from "../components/daytrade/DaytradeMarketChart";
import { TraderParamPanel } from "../components/daytrade/TraderParamPanel";
import { TraderStatsPanel } from "../components/daytrade/TraderStatsPanel";
import { PageHeader } from "../components/ui/PageHeader";
import { PageTabsPortal } from "../components/ui/PageTabBar";

import {
  fetchDaytradeActivity,
  fetchDaytradeEquitySeries,
  fetchDaytradeLive,
  fetchDaytradeMarketChart,
  fetchDaytradePositions,
  fetchDaytradeScorecard,
  fetchDaytradeStats,
  fetchDaytradeTrader,
  postDaytradeOperatorCommand,
  postDaytradeStep,
} from "../api/daytradeClient";
import type {
  DaytradeActivity,
  DaytradeEquitySeries,
  DaytradeLive,
  DaytradeMarketChart as MarketChartData,
  DaytradePositions,
  DaytradeScorecard,
  DaytradeStats,
  DaytradeTraderDetail,
} from "../lib/daytrade/types";

type Tab = "profile" | "live" | "positions" | "scorecard" | "stats" | "activity";
type StatsWindow = "today" | "7d" | "30d" | "all";

export function DaytradeTraderPage() {
  const { traderId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = (searchParams.get("tab") as Tab) || "profile";
  const [detail, setDetail] = useState<DaytradeTraderDetail | null>(null);
  const [live, setLive] = useState<DaytradeLive | null>(null);
  const [positions, setPositions] = useState<DaytradePositions | null>(null);
  const [scorecard, setScorecard] = useState<DaytradeScorecard | null>(null);
  const [activity, setActivity] = useState<DaytradeActivity | null>(null);
  const [market, setMarket] = useState<MarketChartData | null>(null);
  const [equity, setEquity] = useState<DaytradeEquitySeries | null>(null);
  const [stats, setStats] = useState<DaytradeStats | null>(null);
  const [statsWindow, setStatsWindow] = useState<StatsWindow>("today");
  const [chartError, setChartError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chartLoading, setChartLoading] = useState(false);

  const reload = useCallback(async () => {
    if (!traderId) return;
    setError(null);
    try {
      const d = await fetchDaytradeTrader(traderId);
      setDetail(d);
      const [lv, pos, sc] = await Promise.all([
        fetchDaytradeLive(traderId),
        fetchDaytradePositions(traderId),
        fetchDaytradeScorecard(traderId),
      ]);
      setLive(lv);
      setPositions(pos);
      setScorecard(sc);

      setChartLoading(true);
      setChartError(null);
      try {
        const [mc, eq, st] = await Promise.all([
          fetchDaytradeMarketChart(traderId),
          fetchDaytradeEquitySeries(traderId),
          fetchDaytradeStats(traderId, statsWindow),
        ]);
        setMarket(mc);
        setEquity(eq);
        setStats(st);
      } catch (ce) {
        setChartError(ce instanceof Error ? ce.message : String(ce));
      } finally {
        setChartLoading(false);
      }

      if (tab === "activity") {
        setActivity(await fetchDaytradeActivity(traderId));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [traderId, tab, statsWindow]);

  useEffect(() => {
    void reload();
    const id = window.setInterval(() => void reload(), 5000);
    return () => window.clearInterval(id);
  }, [reload]);

  function setTab(next: Tab) {
    const p = new URLSearchParams(searchParams);
    p.set("tab", next);
    setSearchParams(p);
  }

  async function stepOnce() {
    setBusy(true);
    try {
      await postDaytradeStep({ trader_id: traderId });
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function forceFlat() {
    setBusy(true);
    try {
      await postDaytradeOperatorCommand(traderId, "force_flat");
      await postDaytradeStep({ trader_id: traderId });
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const profile = detail?.profile;

  return (
    <div className="daytrade-page">
      <p className="muted">
        <Link className="text-link" to="/daytrade">
          ← 全部模擬交易員
        </Link>
      </p>

      <PageHeader
        title={profile?.display_name ?? traderId}
        info={
          <>
            呢位模擬交易員嘅個人檔案、標的走勢、權益曲線同邏輯統計。帳本獨立，成交用每分鐘收市價計，唔可以直接同「最差一秒」回測比。
          </>
        }
      />

      <p className="daytrade-page__identity">
        <span>{profile?.symbol}</span>
        <span className="muted">{profile?.strategy_id}</span>
        <span className="muted">{traderId}</span>
      </p>

      {profile ? (
        <section
          className="daytrade-page__panel daytrade-page__panel--params"
          aria-label="交易參數"
        >
          <div className="panel__head">
            <h2>交易參數</h2>
          </div>
          <TraderParamPanel
            source={{
              symbol: profile.symbol,
              strategy_id: profile.strategy_id,
              quantity: profile.quantity,
              or_minutes: profile.or_minutes,
              no_new_entry_after: profile.no_new_entry_after,
              force_flat_time: profile.force_flat_time,
              rth_start: profile.rth_start,
              rth_end: profile.rth_end,
              timezone: profile.timezone,
              max_daily_loss_r: profile.max_daily_loss_r,
              starting_equity: profile.starting_equity,
              notes: profile.notes,
            }}
          />
        </section>
      ) : null}

      <p className="daytrade-page__scale-banner" role="status">
        {detail?.live_scale_label ??
          "成交尺：1m_close（穩定 live paper）— 唔可比對 1s_worst 回測"}
      </p>

      {error ? (
        <div className="daytrade-page__error" role="alert">
          {error}
        </div>
      ) : null}

      <section className="daytrade-page__health">
        <span>
          權益 <strong>{fmt(live?.equity ?? detail?.summary.equity)}</strong>
        </span>
        <span>
          今日 {pct(live?.day_return ?? detail?.summary.day_return)}
        </span>
        <span>
          持倉{" "}
          {positions?.positions.length ?? detail?.summary.positions_count ?? 0}
        </span>
        <span>
          勝率{" "}
          <strong>
            {stats?.win_rate == null
              ? "—"
              : `${(stats.win_rate * 100).toFixed(0)}%`}
          </strong>
        </span>
        <span>
          Exp.R{" "}
          <strong>
            {stats?.expectancy_r == null ? "—" : stats.expectancy_r.toFixed(2)}
          </strong>
        </span>
        <div className="daytrade-page__actions">
          <button type="button" disabled={busy} onClick={() => void stepOnce()}>
            推進一步
          </button>
          <button type="button" disabled={busy} onClick={() => void forceFlat()}>
            強制平倉
          </button>
        </div>
      </section>

      {/* Always-visible market + equity charts */}
      <section className="daytrade-page__panel daytrade-page__panel--charts">
        <DaytradeMarketChart
          data={market}
          loading={chartLoading}
          error={chartError}
        />
        <DaytradeEquityChart data={equity} loading={chartLoading} />
      </section>

      <PageTabsPortal>
        <nav className="daytrade-page__tabs" aria-label="交易員分頁">
          {(
            [
              ["profile", "個人檔案"],
              ["live", "即市"],
              ["positions", "倉位報告"],
              ["scorecard", "成績表"],
              ["stats", "統計"],
              ["activity", "動態"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={tab === id ? "is-active" : undefined}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </nav>
      </PageTabsPortal>

      {tab === "profile" && profile ? (
        <section className="daytrade-page__panel">
          <h2>個人檔案</h2>
          <dl className="daytrade-profile">
            <div>
              <dt>名稱</dt>
              <dd>{profile.display_name}</dd>
            </div>
            <div>
              <dt>標的</dt>
              <dd>
                {profile.symbol} · {profile.contract_id}
              </dd>
            </div>
            <div>
              <dt>策略 ID</dt>
              <dd>{profile.strategy_id}</dd>
            </div>
            <div>
              <dt>起始資金</dt>
              <dd>{fmt(profile.starting_equity)}</dd>
            </div>
            <div>
              <dt>手數</dt>
              <dd>{profile.quantity}</dd>
            </div>
            <div>
              <dt>開盤區間</dt>
              <dd>{profile.or_minutes} 分鐘</dd>
            </div>
            <div>
              <dt>RTH</dt>
              <dd>
                {profile.rth_start}–{profile.rth_end}（{profile.timezone}）
              </dd>
            </div>
            <div>
              <dt>停新倉 / 強平</dt>
              <dd>
                {profile.no_new_entry_after} / {profile.force_flat_time}
              </dd>
            </div>
            <div>
              <dt>日損上限 (R)</dt>
              <dd>{profile.max_daily_loss_r}</dd>
            </div>
            <div className="daytrade-profile__wide">
              <dt>備註</dt>
              <dd>{profile.notes || "—"}</dd>
            </div>
          </dl>
          <p className="muted">
            參數解讀喺上方「交易參數」撳「查看說明」；走勢圖同統計喺頁面中段。
          </p>
          <div className="daytrade-card__links">
            <Link to={`/daytrade/traders/${traderId}?tab=positions`}>睇倉位</Link>
            <Link to={`/daytrade/traders/${traderId}?tab=scorecard`}>睇成績表</Link>
            <Link to={`/daytrade/traders/${traderId}?tab=stats`}>睇統計</Link>
            <Link to={`/daytrade/traders/${traderId}?tab=live`}>睇即市</Link>
          </div>
        </section>
      ) : null}

      {tab === "live" && live ? (
        <section className="daytrade-page__panel">
          <h2>即市</h2>
          <div className="daytrade-page__kpis">
            <div>
              <span>權益</span>
              <strong>{fmt(live.equity)}</strong>
            </div>
            <div>
              <span>現金</span>
              <strong>{fmt(live.cash)}</strong>
            </div>
            <div>
              <span>今日回報</span>
              <strong>{pct(live.day_return)}</strong>
            </div>
            <div>
              <span>已實現</span>
              <strong>{fmt(live.realized_pnl)}</strong>
            </div>
          </div>
          {live.block_reasons?.length ? (
            <p className="daytrade-page__error">
              阻擋：{live.block_reasons.join(" · ")}
            </p>
          ) : null}
          <h3>今日事件</h3>
          <ul className="daytrade-page__events">
            {(live.events_today ?? []).map((ev) => (
              <li key={`${ev.ordinal}-${ev.kind}-${ev.ts}`}>
                <code>#{ev.ordinal}</code> {ev.kind}{" "}
                {ev.price != null ? `@ ${ev.price}` : ""} — {ev.note}
              </li>
            ))}
          </ul>
          {!live.events_today?.length ? <p className="muted">未有事件。</p> : null}
        </section>
      ) : null}

      {tab === "positions" && positions ? (
        <section className="daytrade-page__panel">
          <h2>倉位報告</h2>
          <p className="muted">
            來源：{positions.source} · {positions.formula_version}
          </p>
          <div className="daytrade-page__kpis">
            <div>
              <span>權益</span>
              <strong>{fmt(positions.equity)}</strong>
            </div>
            <div>
              <span>現金</span>
              <strong>{fmt(positions.cash)}</strong>
            </div>
            <div>
              <span>已實現</span>
              <strong>{fmt(positions.realized_pnl)}</strong>
            </div>
            <div>
              <span>事件數</span>
              <strong>{positions.event_count}</strong>
            </div>
          </div>
          {positions.positions.length === 0 ? (
            <p>而家無持倉（flat）。</p>
          ) : (
            <table className="daytrade-page__table">
              <thead>
                <tr>
                  <th>方向</th>
                  <th>數量</th>
                  <th>入場</th>
                  <th>止蝕</th>
                  <th>目標</th>
                </tr>
              </thead>
              <tbody>
                {positions.positions.map((p, i) => (
                  <tr key={i}>
                    <td>{p.side}</td>
                    <td>{p.qty}</td>
                    <td>{p.entry_price}</td>
                    <td>{p.stop}</td>
                    <td>{p.target}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ) : null}

      {tab === "scorecard" && scorecard ? (
        <section className="daytrade-page__panel">
          <h2>成績表</h2>
          <p className="muted">
            {scorecard.source} · {scorecard.formula_version} ·{" "}
            {scorecard.trading_date}
          </p>
          <div className="daytrade-page__kpis">
            <div>
              <span>已平倉筆數</span>
              <strong>{scorecard.closed_trade_count}</strong>
            </div>
            <div>
              <span>勝 / 負</span>
              <strong>
                {scorecard.wins} / {scorecard.losses}
              </strong>
            </div>
            <div>
              <span>勝率</span>
              <strong>
                {scorecard.win_rate == null
                  ? "—"
                  : `${(scorecard.win_rate * 100).toFixed(1)}%`}
              </strong>
            </div>
            <div>
              <span>已平倉盈虧</span>
              <strong>{fmt(scorecard.total_closed_pnl)}</strong>
            </div>
            <div>
              <span>權益</span>
              <strong>{fmt(scorecard.equity)}</strong>
            </div>
            <div>
              <span>日終清倉</span>
              <strong>{scorecard.flat_compliance ? "係" : "否"}</strong>
            </div>
          </div>
          <h3>成交明細</h3>
          {scorecard.trades.length === 0 ? (
            <p className="muted">未有已平倉交易。</p>
          ) : (
            <table className="daytrade-page__table">
              <thead>
                <tr>
                  <th>方向</th>
                  <th>入場</th>
                  <th>離場</th>
                  <th>原因</th>
                  <th>R</th>
                  <th>盈虧</th>
                </tr>
              </thead>
              <tbody>
                {scorecard.trades.map((t, i) => (
                  <tr key={i}>
                    <td>{t.side}</td>
                    <td>{t.entry_price}</td>
                    <td>{t.exit_price}</td>
                    <td>{t.exit_kind}</td>
                    <td>
                      {t.r_multiple == null ? "—" : t.r_multiple.toFixed(2)}
                    </td>
                    <td className={t.pnl >= 0 ? "is-up" : "is-down"}>
                      {fmt(t.pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ) : null}

      {tab === "stats" ? (
        <section className="daytrade-page__panel">
          <TraderStatsPanel
            stats={stats}
            window={statsWindow}
            loading={chartLoading}
            onWindowChange={setStatsWindow}
          />
          {stats?.trades && stats.trades.length > 0 ? (
            <>
              <h3>窗口內成交</h3>
              <table className="daytrade-page__table">
                <thead>
                  <tr>
                    <th>方向</th>
                    <th>入場</th>
                    <th>離場</th>
                    <th>原因</th>
                    <th>R</th>
                    <th>盈虧</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.trades.map((t, i) => (
                    <tr key={i}>
                      <td>{t.side}</td>
                      <td>{t.entry_price}</td>
                      <td>{t.exit_price}</td>
                      <td>{t.exit_kind}</td>
                      <td>
                        {t.r_multiple == null ? "—" : t.r_multiple.toFixed(2)}
                      </td>
                      <td className={t.pnl >= 0 ? "is-up" : "is-down"}>
                        {fmt(t.pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </section>
      ) : null}

      {tab === "activity" ? (
        <section className="daytrade-page__panel">
          <h2>動態</h2>
          <p className="muted">個人帳本事件 · 人話版</p>
          <div className="daytrade-chat">
            {(activity?.bubbles ?? []).map((b, i) => (
              <div key={i} className="daytrade-chat__bubble">
                <span className="muted">{b.ts}</span>
                <p>{b.text}</p>
              </div>
            ))}
            {!activity?.bubbles?.length ? <p>未有事件。</p> : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function fmt(n: number | undefined | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function pct(n: number | undefined | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(2)}%`;
}
