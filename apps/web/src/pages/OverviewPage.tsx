import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { type BatchJobRecord, fetchBatchJobs } from "../api/client";
import { ibStatusLabel, useIbStatus } from "../api/useIbStatus";
import { ComputeArchitecturePanel } from "../components/system/ComputeArchitecturePanel";
import { InfoButton } from "../components/ui/InfoButton";
import { PageHeader } from "../components/ui/PageHeader";
import { PageTabBar } from "../components/ui/PageTabBar";
import { jobStatusLabel } from "../lib/backtest/format";
import { plainIbDetail } from "../lib/humanTerms";
import { humanizeThrown } from "../lib/net/errorMessage";

type OverviewTab = "status" | "runs" | "paper" | "plan";

const TABS: { id: OverviewTab; label: string }[] = [
  { id: "status", label: "連接狀態" },
  { id: "runs", label: "進行緊嘅回測" },
  { id: "paper", label: "模擬盤" },
  { id: "plan", label: "盤前計劃" },
];

/**
 * P1 總覽 — docs/03 four cards: IB 連接 / 行緊嘅 run / 模擬盤 / 今日盤前計劃.
 */
export function OverviewPage() {
  const { status: ibStatus, loading: ibLoading } = useIbStatus();
  const [batches, setBatches] = useState<BatchJobRecord[]>([]);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [tab, setTab] = useState<OverviewTab>("status");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await fetchBatchJobs();
        if (!cancelled) {
          setBatches(list.batches);
        }
      } catch (err) {
        if (!cancelled) {
          setBatchError(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const active = batches.find((batch) =>
    ["queued", "running"].includes(batch.status),
  );
  const latest = batches[0];
  const ib = ibStatusLabel(ibStatus);

  return (
    <div className="detail-stack">
      <PageHeader
        title="總覽"
        info={
          <>
            開工第一頁。頂欄四個分頁分別答：接唔接到（連接狀態）、有冇嘢行緊
            （進行緊嘅回測）、模擬盤點（模擬盤）、今日點打算（盤前計劃）。IB
            欄只講個 port 通唔通，因為通到唔代表已經傾得掂。
          </>
        }
      />

      <PageTabBar
        ariaLabel="總覽分頁"
        className="page-tabs--sticky"
        active={tab}
        onChange={setTab}
        tabs={TABS}
      />

      {tab === "status" ? (
        <div
          id="page-panel-status"
          role="tabpanel"
          aria-labelledby="page-tab-status"
          className="detail-stack"
        >
          <section className="panel" aria-label="IB 連接">
            <div className="panel__head">
              <h2 className="panel__title">IB 連接</h2>
              <span
                className={
                  ib.tone === "ok"
                    ? "chip chip--pass"
                    : ib.tone === "warn"
                      ? "chip chip--warn"
                      : "chip"
                }
              >
                {ibLoading ? "探測中…" : ib.label}
              </span>
              <InfoButton label="IB 連接點睇" align="end">
                呢度只講個 port 通唔通。通到唔代表已經傾得掂，所以系統唔會寫
                「已連接」。IB
                喺呢個系統入面淨係做行情來源，唔會替你落單。見到「未設定」即係未讀到連接設定，唔係故障。
              </InfoButton>
            </div>
            <div className="metrics-grid">
              <div className="metric-card">
                <span className="metric-card__label">位址</span>
                <span className="metric-card__value metric-card__value--sm table__mono">
                  {ibStatus?.host && ibStatus.port
                    ? `${ibStatus.host}:${ibStatus.port}`
                    : "—"}
                </span>
                <small className="metric-card__foot">
                  {ibStatus?.host ? "由連接設定讀入" : "未讀到連接設定"}
                </small>
              </div>
              <div className="metric-card">
                <span className="metric-card__label">等幾耐先當唔通</span>
                <span className="metric-card__value metric-card__value--sm">
                  {ibStatus?.probe_timeout_seconds
                    ? `${String(ibStatus.probe_timeout_seconds)} 秒`
                    : "—"}
                </span>
                <small className="metric-card__foot">只試個 port 通唔通</small>
              </div>
            </div>
            {/* The backend detail is an English engineering sentence; it is
                translated, and an unrecognised one is not printed at all. */}
            {plainIbDetail(ibStatus?.detail).text ? (
              <p className="panel__note">
                {plainIbDetail(ibStatus?.detail).text}
              </p>
            ) : null}
          </section>

          <ComputeArchitecturePanel />
        </div>
      ) : null}

      {tab === "runs" ? (
        <div id="page-panel-runs" role="tabpanel" aria-labelledby="page-tab-runs">
          <div className="metrics-grid">
            <section className="metric-card" aria-label="進行緊嘅回測">
              <span className="metric-card__label">回測</span>
              {batchError ? (
                <span className="metric-card__value metric-card__value--sm metric-card__value--muted">
                  讀唔到隊列
                </span>
              ) : active ? (
                <>
                  <span className="metric-card__value metric-card__value--sm">
                    {active.summary.completed}/{active.summary.total} 行緊
                  </span>
                  <span className="meter" aria-hidden="true">
                    <i
                      style={{
                        width: `${Math.round(
                          (active.summary.completed /
                            Math.max(1, active.summary.total)) *
                            100,
                        )}%`,
                      }}
                    />
                  </span>
                  <small className="metric-card__foot">
                    {jobStatusLabel(active.status)}
                  </small>
                </>
              ) : (
                <>
                  <span className="metric-card__value metric-card__value--sm metric-card__value--muted">
                    冇嘢行緊
                  </span>
                  <small className="metric-card__foot">
                    {latest
                      ? `最近一次：${jobStatusLabel(latest.status)}`
                      : "仲未由呢度交過回測"}
                  </small>
                </>
              )}
              <small className="metric-card__foot">
                <Link className="text-link" to="/backtest">
                  去回測 →
                </Link>
              </small>
            </section>
          </div>

          <section className="panel" role="region" aria-label="最近提交">
            <div className="panel__head">
              <h2 className="panel__title">最近提交</h2>
              <InfoButton label="最近提交點睇" align="end">
                由呢個介面交出去嘅回測，最近五次。撳「進度」欄可以知邊個行緊、邊個做完。想睇成績就去
                結果 頁。
              </InfoButton>
            </div>
            {batchError ? (
              <p className="state-msg state-msg--error" role="alert">
                {humanizeThrown(batchError).title}
              </p>
            ) : batches.length === 0 ? (
              <p className="state-msg">仲未由呢度交過回測。</p>
            ) : (
              /* The queue identity is engineering vocabulary and told the
                 owner nothing; the submit time distinguishes the rows. */
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>幾時交</th>
                      <th>狀態</th>
                      <th>做咗幾多</th>
                    </tr>
                  </thead>
                  <tbody>
                    {batches.slice(0, 5).map((batch) => (
                      <tr key={batch.batch_id}>
                        <td className="table__mono table__cell--nowrap">
                          {batch.created_at
                            ? batch.created_at.slice(0, 19).replace("T", " ")
                            : "—"}
                        </td>
                        <td>{jobStatusLabel(batch.status)}</td>
                        <td className="table__mono">
                          {batch.summary.completed}/{batch.summary.total}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      ) : null}

      {tab === "paper" ? (
        <div id="page-panel-paper" role="tabpanel" aria-labelledby="page-tab-paper">
          <section className="panel" aria-label="模擬盤">
            <div className="panel__head">
              <h2 className="panel__title">模擬盤 · 今日</h2>
              <InfoButton label="模擬盤點睇" align="end">
                模擬盤攞 IB
                行情做輸入，成交、虛擬帳戶、持倉同盈虧全部由本系統自己計，唔會向任何
                IB 帳戶發單。
              </InfoButton>
            </div>
            <p className="panel__note">
              IB 行情驅動嘅 app 自家模擬 · 唔會落真錢單
            </p>
            <div className="form-actions">
              <Link className="btn btn--primary" to="/paper">
                去模擬盤
              </Link>
              <Link className="btn" to="/daytrade">
                日內模擬
              </Link>
            </div>
          </section>
        </div>
      ) : null}

      {tab === "plan" ? (
        <div id="page-panel-plan" role="tabpanel" aria-labelledby="page-tab-plan">
          <section className="panel" aria-label="今日盤前計劃">
            <div className="panel__head">
              <h2 className="panel__title">今日盤前計劃</h2>
              <span className="chip">未接通</span>
              <InfoButton label="盤前計劃點睇" align="end">
                接通之後呢度會顯示今日嘅方向判斷、波動判斷同關鍵價位數目。而家係真空白，唔係
                0——系統寧願留白都唔會填個假數俾你。
              </InfoButton>
            </div>
            <p className="state-msg">呢一格嘅資料來源仲未建，所以暫時空白。</p>
          </section>
        </div>
      ) : null}
    </div>
  );
}
