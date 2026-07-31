import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  fetchP5ConfirmedStrategies,
  fetchP5Runs,
} from "../api/client";
import { InfoButton } from "../components/ui/InfoButton";
import { PageHeader } from "../components/ui/PageHeader";
import { PageTabBar } from "../components/ui/PageTabBar";
import { listFixtureResults } from "../lib/results/fixtureStore";
import { formatOptionalNumber } from "../lib/results/metrics";
import {
  mapP5RunListItem,
  parseP5RunList,
  parseP5StrategyLabels,
  type P5RunRow,
} from "../lib/results/normalContract";
import { isRunRead } from "../lib/results/readState";
import type { ResultListItem } from "../lib/results/types";

type ResultsTab = "list" | "filters";

const RESULTS_TABS: { id: ResultsTab; label: string }[] = [
  { id: "list", label: "結果列表" },
  { id: "filters", label: "篩選" },
];

function useResultsMode(): "live" | "owner-review" {
  const [params] = useSearchParams();
  return params.get("scenario") === "owner-review" ? "owner-review" : "live";
}

/**
 * P5 results main — Owner list (filters · count · unread).
 */
export function ResultsPage() {
  const mode = useResultsMode();
  const isReview = mode === "owner-review";
  const [params, setParams] = useSearchParams();
  // D20: filters live in URL
  const unreadOnly = params.get("unread") === "1";
  const filterStrategy = params.get("strategy") ?? "all";
  const filterSymbol = params.get("symbol") ?? "all";

  const [strategyLabels, setStrategyLabels] = useState<Map<string, string>>(
    () => new Map(),
  );
  const [liveRuns, setLiveRuns] = useState<P5RunRow[]>([]);
  const [load, setLoad] = useState<"loading" | "ready" | "error" | "empty">(
    "loading",
  );
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [pageTab, setPageTab] = useState<ResultsTab>("list");

  // D30: push history entries so browser back/forward rebuilds filter steps.
  const setFilter = (key: "strategy" | "symbol" | "unread", value: string) => {
    const next = new URLSearchParams(params);
    if (key === "unread") {
      if (value === "1") {
        next.set("unread", "1");
      } else {
        next.delete("unread");
      }
    } else if (value === "all") {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    if (isReview) {
      next.set("scenario", "owner-review");
    }
    setParams(next, { replace: false });
  };

  useEffect(() => {
    if (isReview) {
      // D21: do NOT reset decisions on list remount
      setLiveRuns([]);
      setStrategyLabels(new Map());
      setError(null);
      setLoad("ready");
      return;
    }
    const controller = new AbortController();
    let active = true;
    // Soft refresh: do not clear runs until new payload arrives (no list flash).
    setLoad((prior) => (prior === "ready" || prior === "empty" ? prior : "loading"));
    setError(null);
    void fetchP5Runs(controller.signal)
      .then((response) => {
        const parsed = parseP5RunList(response);
        if (!active || controller.signal.aborted) {
          return;
        }
        if (!parsed.ok) {
          setError(parsed.error);
          setLoad((prior) => (prior === "ready" ? "ready" : "error"));
          return;
        }
        const standard = parsed.value.runs.filter(
          (run) => run.validation_run === false,
        );
        setLiveRuns(standard);
        setLoad(standard.length === 0 ? "empty" : "ready");
      })
      .catch((caught: unknown) => {
        if (
          active &&
          !controller.signal.aborted &&
          !(
            caught !== null &&
            typeof caught === "object" &&
            "name" in caught &&
            caught.name === "AbortError"
          )
        ) {
          setError(caught instanceof Error ? caught.message : String(caught));
          setLoad((prior) => (prior === "ready" || prior === "empty" ? prior : "error"));
        }
      });
    void fetchP5ConfirmedStrategies(controller.signal)
      .then((response) => {
        const parsed = parseP5StrategyLabels(response);
        if (active && !controller.signal.aborted && parsed.ok) {
          setStrategyLabels(parsed.value);
        }
      })
      .catch(() => {
        // Optional label projection: exact strategy id remains the fallback.
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [isReview]);

  const catalog = useMemo(
    () =>
      isReview
        ? new Map(
            listFixtureResults().map((r) => [
              r.strategyVersion,
              r.strategyLabel,
            ]),
          )
        : strategyLabels,
    [isReview, strategyLabels],
  );

  const items: ResultListItem[] = useMemo(() => {
    void tick; // re-read unread flags after navigation marks
    if (isReview) {
      return listFixtureResults().map((r) => ({
        ...r,
        unread: !isRunRead(r.runId, "owner-review"),
      }));
    }
    return liveRuns.map((run) => {
      const strategyVersion = run.strategy_version ?? "";
      return mapP5RunListItem(
        run,
        catalog.get(strategyVersion) ?? strategyVersion,
        !isRunRead(run.run_id, "live"),
      );
    });
  }, [isReview, liveRuns, catalog, tick]);

  const strategiesOpts = useMemo(() => {
    const s = new Map<string, string>();
    for (const it of items) {
      s.set(it.strategyVersion, it.strategyLabel);
    }
    return [...s.entries()];
  }, [items]);

  const symbols = useMemo(() => {
    return [...new Set(items.map((i) => i.contractId))].filter(Boolean);
  }, [items]);

  const filtered = items.filter((it) => {
    if (unreadOnly && !it.unread) {
      return false;
    }
    if (filterStrategy !== "all" && it.strategyVersion !== filterStrategy) {
      return false;
    }
    if (filterSymbol !== "all" && it.contractId !== filterSymbol) {
      return false;
    }
    return true;
  });

  const detailHref = (runId: string) => {
    const q = new URLSearchParams(params);
    if (isReview) {
      q.set("scenario", "owner-review");
    }
    const qs = q.toString();
    return qs ? `/results/${runId}?${qs}` : `/results/${runId}`;
  };

  return (
    <div className="detail-stack" data-results-mode={mode}>
      <PageHeader
        title="結果"
        info={
          <>
            篩選 → 睇摘要 → 撳入去睇詳情（四格圖、因果、決定）。零成交唔一定係故障：因果面板會話你聽卡喺邊一關，睇完先決定改咩，唔好即刻放鬆條件。
          </>
        }
      />

      <PageTabBar
        ariaLabel="結果分頁"
        className="page-tabs--sticky"
        active={pageTab}
        onChange={setPageTab}
        tabs={RESULTS_TABS}
      />

      {isReview ? (
        <p className="backtest-banner" role="status">
          <strong>Owner 驗收入口</strong>
          （<code>?scenario=owner-review</code>
          ）——隔離示範資料，完整操作結果主頁／詳情／匯出／決定；
          <strong>唔寫後端、唔污染真結果</strong>。
        </p>
      ) : null}

      <section
        className="panel"
        role="tabpanel"
        id="page-panel-list"
        aria-labelledby="page-tab-list"
        aria-label="結果列表"
      >
        <div className="results-toolbar">
          <div className="panel__head">
            <p className="state-msg" data-testid="results-count">
              而家顯示 {filtered.length} 個結果
              {unreadOnly ? "（只未睇）" : ""}
            </p>
            <InfoButton label="結果列表點用" align="end">
              揀策略、揀合約、或者淨係睇未睇過嘅，三個篩選可以疊住用。篩選會寫入網址，所以撳返「上一頁」會逐步還原你篩過嘅步驟，分享條連結亦都會帶埋同一個篩選。
            </InfoButton>
          </div>
          {(pageTab === "filters" || pageTab === "list") && (
            <>
              <div className="chip-row" role="group" aria-label="策略篩選">
                <button
                  type="button"
                  className={
                    filterStrategy === "all"
                      ? "chip-btn chip-btn--on"
                      : "chip-btn"
                  }
                  onClick={() => {
                    setFilter("strategy", "all");
                  }}
                >
                  全部策略
                </button>
                {strategiesOpts.map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    className={
                      filterStrategy === id
                        ? "chip-btn chip-btn--on"
                        : "chip-btn"
                    }
                    title={id}
                    onClick={() => {
                      setFilter("strategy", id);
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="chip-row" role="group" aria-label="合約篩選">
                <button
                  type="button"
                  className={
                    filterSymbol === "all"
                      ? "chip-btn chip-btn--on"
                      : "chip-btn"
                  }
                  onClick={() => {
                    setFilter("symbol", "all");
                  }}
                >
                  全部合約
                </button>
                {symbols.map((sym) => (
                  <button
                    key={sym}
                    type="button"
                    className={
                      filterSymbol === sym
                        ? "chip-btn chip-btn--on"
                        : "chip-btn"
                    }
                    onClick={() => {
                      setFilter("symbol", sym);
                    }}
                  >
                    {sym}
                  </button>
                ))}
              </div>
              <div className="chip-row" role="group" aria-label="未讀篩選">
                <button
                  type="button"
                  className={unreadOnly ? "chip-btn chip-btn--on" : "chip-btn"}
                  data-testid="filter-unread"
                  aria-pressed={unreadOnly}
                  onClick={() => {
                    setFilter("unread", unreadOnly ? "0" : "1");
                  }}
                >
                  只睇未讀
                </button>
              </div>
            </>
          )}
        </div>

        {pageTab === "list" && load === "loading" ? (
          <p className="state-msg" data-testid="results-loading">
            正在載入結果…
          </p>
        ) : null}
        {pageTab === "list" && load === "error" ? (
          <p
            className="state-msg state-msg--error"
            data-testid="results-error"
            role="alert"
          >
            結果載入失敗：{error}
          </p>
        ) : null}
        {pageTab === "list" &&
        (load === "empty" || (load === "ready" && filtered.length === 0)) ? (
          <p className="state-msg" data-testid="results-empty">
            {isReview
              ? "仲未有示範結果。"
              : "仲未有標準回測結果（工程驗證用嘅記錄唔會出現喺呢度）。"}
          </p>
        ) : null}

        {pageTab === "list" && load === "ready" && filtered.length > 0 ? (
          <ul className="results-list" data-testid="results-list">
            {filtered.map((it) => (
              <li key={it.runId}>
                <Link
                  className={
                    it.unread
                      ? "results-row results-row--unread"
                      : "results-row"
                  }
                  to={detailHref(it.runId)}
                  data-testid={`result-row-${it.runId}`}
                  onClick={() => {
                    setTick((t) => t + 1);
                  }}
                >
                  <div className="results-row__main">
                    <strong>
                      {it.strategyLabel} · {it.contractId}
                    </strong>
                    {it.unread ? (
                      <span className="results-row__badge">未睇</span>
                    ) : null}
                    <div className="utc-hint" title={it.strategyVersion}>
                      {it.strategyVersion}
                    </div>
                  </div>
                  <div className="results-row__metrics">
                    交易{" "}
                    {it.tradeCount === null
                      ? "未提供"
                      : String(it.tradeCount)}
                    {" · "}
                    勝率{" "}
                    {it.winRate == null
                      ? "未提供"
                      : `${(it.winRate * 100).toFixed(0)}%`}
                    {" · "}
                    R {formatOptionalNumber(it.netR)}
                    {" · "}
                    USD {formatOptionalNumber(it.netUsd, 0)}
                    {" · "}
                    最大回撤 {formatOptionalNumber(it.maxDrawdownUsd, 0)}
                  </div>
                  <div className="results-row__why">{it.whyLine}</div>
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}
