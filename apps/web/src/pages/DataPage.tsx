import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  type CoverageContract,
  fetchDataCoverage,
  fetchQualityReports,
  postBlacklist,
} from "../api/client";
import {
  backtestHandoffHref,
  buildBacktestHandoff,
  coverageStatusMessage,
  parseTradingDayCoverage,
  type TradingDayCoverageFacts,
} from "../lib/data/coverageHandoff";
import { InfoButton } from "../components/ui/InfoButton";
import { PageHeader } from "../components/ui/PageHeader";
import { PageTabBar } from "../components/ui/PageTabBar";

interface ContractView {
  row: CoverageContract;
  coverage: TradingDayCoverageFacts;
  handoffHref: string | null;
  failClosedMsg: string | null;
}

type DataTab = "coverage" | "quality" | "download";

const DATA_TABS: { id: DataTab; label: string }[] = [
  { id: "coverage", label: "覆蓋" },
  { id: "quality", label: "體檢" },
  { id: "download", label: "補數據" },
];

/**
 * P3 數據頁 — 覆蓋夠唔夠用、可用交易日、一鍵帶去回測。
 * 用真 coverage API nested facts；loading/error/unknown fail-closed。
 */
export function DataPage() {
  const [searchParams] = useSearchParams();
  const focusSymbol = (searchParams.get("symbol") ?? "").trim().toUpperCase();
  const focusDate = searchParams.get("date") ?? "";

  const [contracts, setContracts] = useState<CoverageContract[]>([]);
  const [reports, setReports] = useState<
    Array<{
      report_id: string;
      contract_id: string;
      issue_count: number;
      error_count: number;
      checked_at: string | null;
    }>
  >([]);
  const [error, setError] = useState<string | null>(null);
  /** Full-page load only on first paint; soft reloads keep prior rows. */
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [filterFocus, setFilterFocus] = useState(Boolean(focusSymbol));
  const [tab, setTab] = useState<DataTab>("coverage");
  const hasLoadedRef = useRef(false);

  const reload = useCallback(async () => {
    const soft = hasLoadedRef.current;
    if (soft) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);
    try {
      const [cov, qr] = await Promise.all([
        fetchDataCoverage(),
        fetchQualityReports(undefined),
      ]);
      setContracts(cov.contracts);
      setReports(qr.reports.slice(0, 15));
      hasLoadedRef.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      // Soft refresh: keep previous contracts on screen (no flash to empty).
      if (!hasLoadedRef.current) {
        setContracts([]);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const views: ContractView[] = useMemo(() => {
    return contracts.map((row) => {
      const coverage = parseTradingDayCoverage(row.trading_day_coverage);
      const handoff = buildBacktestHandoff(row.symbol, coverage);
      return {
        row,
        coverage,
        handoffHref: handoff ? backtestHandoffHref(handoff) : null,
        failClosedMsg: coverageStatusMessage(coverage, false, null),
      };
    });
  }, [contracts]);

  const visible = useMemo(() => {
    if (!filterFocus || !focusSymbol) {
      return views;
    }
    return views.filter((v) => v.row.symbol.toUpperCase() === focusSymbol);
  }, [views, filterFocus, focusSymbol]);

  return (
    <div className="detail-stack">
      <PageHeader
        title="數據"
        info={
          <>
            回測之前嚟呢頁行一次，可以慳返「跑完先發現冇數據」。覆蓋
            = 每個合約實際有數據嘅日期範圍；體檢 = 質量報告同已知有問題嘅日子；補數據
            = 系統出一條指令俾你自己喺終端機跑，唔會靜靜雞喺背後下載。
          </>
        }
      />

      <PageTabBar
        ariaLabel="數據分頁"
        className="page-tabs--sticky"
        active={tab}
        onChange={setTab}
        tabs={DATA_TABS}
      />

      {focusSymbol && filterFocus ? (
        <div className="panel" role="status">
          <p>
            由其他頁帶入篩選：<strong>{focusSymbol}</strong>
            {focusDate ? ` · 交易日 ${focusDate}` : ""}
          </p>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setFilterFocus(false);
            }}
          >
            睇全部
          </button>
        </div>
      ) : null}

      {loading && contracts.length === 0 ? (
        <p className="state-msg" role="status">
          覆蓋資料載入中…暫時唔可以當所有交易日都乾淨。
        </p>
      ) : null}
      {refreshing ? (
        <p className="state-msg" role="status" data-testid="data-soft-refresh">
          更新緊最新覆蓋…（畫面唔會閃空）
        </p>
      ) : null}
      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          覆蓋資料暫時核實唔到，唔會顯示假嘅完整日清單。（{error}）
        </p>
      ) : null}
      {actionMsg ? <p className="state-msg">{actionMsg}</p> : null}

      {tab === "coverage" ? (
        <section
          className="panel"
          role="tabpanel"
          id="page-panel-coverage"
          aria-labelledby="page-tab-coverage"
          aria-label="夠唔夠用"
        >
          <div className="panel__head">
            <h2 className="panel__title">夠唔夠用（按合約）</h2>
            <InfoButton label="覆蓋點睇" align="end">
              呢度只講每個合約有幾多個完整交易日。夠唔夠跑某個策略，係由
              回測 頁按暖機需求去計，唔喺呢度判斷。
            </InfoButton>
          </div>
          {!loading && !error && visible.length === 0 ? (
            <p className="state-msg">暫時冇合約覆蓋資料。</p>
          ) : null}
          <div className="detail-stack">
            {visible.map((view) => (
              <CoverageCard
                key={view.row.contract_id}
                view={view}
                onDecide={async (tradingDate, decision) => {
                  try {
                    await postBlacklist({
                      contract_id: view.row.contract_id,
                      trading_date: tradingDate,
                      decision,
                      note: decision === "trust" ? "UI trust" : "UI exclude",
                    });
                    setActionMsg(
                      decision === "trust"
                        ? `已信 ${view.row.symbol} ${tradingDate} 嘅數據`
                        : `已決定唔回測 ${view.row.symbol} ${tradingDate}`,
                    );
                    await reload();
                  } catch (err) {
                    setActionMsg(
                      err instanceof Error ? err.message : String(err),
                    );
                  }
                }}
              />
            ))}
          </div>
        </section>
      ) : null}

      {tab === "quality" ? (
        <section
          className="panel"
          role="tabpanel"
          id="page-panel-quality"
          aria-labelledby="page-tab-quality"
          aria-label="體檢記錄"
        >
          <div className="panel__head">
            <h2 className="panel__title">體檢記錄（最近）</h2>
            <InfoButton label="體檢點睇" align="end">
              每次數據體檢嘅結果。issue 係要留意嘅地方，error
              係一定要處理嘅問題。有 error 嘅合約，回測開始之前會被攔住。
            </InfoButton>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>合約</th>
                  <th>問題數</th>
                  <th>錯誤數</th>
                  <th>幾時檢查</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((report) => (
                  <tr key={report.report_id}>
                    <td className="table__mono">{report.contract_id}</td>
                    <td className="table__mono">{report.issue_count}</td>
                    <td className="table__mono">{report.error_count}</td>
                    <td className="table__mono">
                      {report.checked_at
                        ? `${report.checked_at.slice(0, 19)} UTC`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {tab === "download" ? (
        <section
          className="panel"
          role="tabpanel"
          id="page-panel-download"
          aria-labelledby="page-tab-download"
          aria-label="補數據"
        >
          <div className="panel__head">
            <h2 className="panel__title">補數據</h2>
            <InfoButton label="補數據點用" align="end">
              系統唔會自己向 Interactive Brokers
              攞數據。撳複製攞條指令，去終端機自己執行。呢個係刻意設計——下載會寫入真數據，所以由你按掣。
            </InfoButton>
          </div>
          {visible.map((view) => {
            const day =
              view.coverage.pending_problem_trading_dates?.[0] ??
              view.coverage.problem_trading_dates?.[0] ??
              focusDate;
            if (!day) {
              return null;
            }
            const cmd = `futures-research download --symbol ${view.row.symbol} --start ${day}T00:00:00Z --end ${day}T23:59:59Z`;
            return (
              <div key={`dl-${view.row.contract_id}`} className="form-grid">
                <p>
                  <strong>{view.row.symbol}</strong> · 交易日 {day}
                </p>
                <code className="table__mono">{cmd}</code>
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    void navigator.clipboard.writeText(cmd).then(
                      () => {
                        setActionMsg(
                          `已複製 ${view.row.symbol} ${day} 補數據指令`,
                        );
                      },
                      () => {
                        setActionMsg("複製失敗，請手動揀指令");
                      },
                    );
                  }}
                >
                  複製補數據指令
                </button>
              </div>
            );
          })}
        </section>
      ) : null}
    </div>
  );
}

function CoverageCard({
  view,
  onDecide,
}: {
  view: ContractView;
  onDecide: (
    tradingDate: string,
    decision: "trust" | "exclude",
  ) => Promise<void>;
}) {
  const { row, coverage, handoffHref, failClosedMsg } = view;
  const known = coverage.status === "known";

  return (
    <article
      className="panel"
      aria-label={`${row.symbol} 覆蓋`}
      data-symbol={row.symbol}
    >
      <header className="hd">
        <strong>{row.symbol}</strong>
        <span className="table__mono">{row.contract_id}</span>
        {row.display_name ? <em>{row.display_name}</em> : null}
      </header>

      {failClosedMsg ? (
        <p className="state-msg state-msg--error" role="alert">
          {failClosedMsg}
        </p>
      ) : null}

      {known ? (
        <ul className="detail-stack" style={{ listStyle: "none", padding: 0 }}>
          <li>
            有數據嘅日子：
            <strong>
              {coverage.trading_date_count ?? "—"} 日
            </strong>
          </li>
          <li>
            <span className="chip chip--pass">完整</span> 可以直接回測：
            <strong>
              {coverage.complete_trading_date_count ?? 0} 日
            </strong>
          </li>
          <li>
            <span className="chip chip--warn">有問題</span>：
            <strong>
              {coverage.problem_trading_date_count ?? 0} 日
            </strong>
            （等你裁決{" "}
            {coverage.pending_problem_trading_date_count ?? 0} · 你已信{" "}
            {coverage.owner_trusted_problem_trading_date_count ?? 0}）
          </li>
          <li>
            你決定唔回測：
            <strong>
              {coverage.owner_excluded_trading_date_count ?? 0} 日
            </strong>
          </li>
          <li>
            原生日線：
            <strong>
              {typeof row.native_daily_coverage?.available_trading_date_count ===
              "number"
                ? `${row.native_daily_coverage.available_trading_date_count} 日有`
                : "暫時核實唔到"}
            </strong>
          </li>
          <li>
            連續完整最長一段：
            <strong>
              {coverage.longest_complete_segment
                ? `${coverage.longest_complete_segment.start_trading_date} → ${coverage.longest_complete_segment.end_trading_date}（${coverage.longest_complete_segment.trading_date_count} 日）`
                : "冇"}
            </strong>
          </li>
        </ul>
      ) : null}

      {known && (coverage.complete_trading_dates?.length ?? 0) > 0 ? (
        <details>
          <summary>
            可用完整交易日（{coverage.complete_trading_dates!.length}）
          </summary>
          <p className="table__mono" style={{ wordBreak: "break-all" }}>
            {coverage.complete_trading_dates!.join("、")}
          </p>
        </details>
      ) : null}

      {known &&
      (coverage.pending_problem_trading_dates?.length ?? 0) > 0 ? (
        <div>
          <h3 className="panel__title">等你裁決</h3>
          {coverage.pending_problem_trading_dates!.map((day) => (
            <div key={day} className="form-grid" data-trading-date={day}>
              <p>
                交易日 <strong>{day}</strong>
              </p>
              <p className="panel__note">
                未裁決前，呢一日嘅回測會被拒絕執行。
              </p>
              <div className="form-actions">
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    void onDecide(day, "trust");
                  }}
                >
                  信呢日數據
                </button>
                <button
                  type="button"
                  className="btn btn--danger"
                  onClick={() => {
                    void onDecide(day, "exclude");
                  }}
                >
                  唔好回測呢日
                </button>
              </div>
              <p className="panel__note">
                「信」：回測會照用呢日（有缺口風險）。「唔好回測」：呢日完全跳過。
              </p>
            </div>
          ))}
        </div>
      ) : null}

      <div className="form-actions">
        {handoffHref ? (
          <Link className="btn btn--primary" to={handoffHref}>
            帶可用日子去回測
          </Link>
        ) : (
          <button type="button" className="btn" disabled>
            暫時帶唔到去回測（覆蓋未核實或冇完整日）
          </button>
        )}
      </div>
    </article>
  );
}
