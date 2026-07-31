import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  type StrategyVersion,
  fetchStrategies,
} from "../api/client";
import { ComputeArchitecturePanel } from "../components/system/ComputeArchitecturePanel";
import { DateField } from "../components/ui/DateField";
import { InfoButton } from "../components/ui/InfoButton";
import { useWritesBlocked } from "../lib/net/online";
import { PageHeader } from "../components/ui/PageHeader";
import { PageTabBar } from "../components/ui/PageTabBar";
import {
  parseHandoffSearchParams,
  tradingDayToLocalRangeEnd,
  tradingDayToLocalRangeStart,
} from "../lib/data/coverageHandoff";
import {
  assumptionsSummary,
  capitalPerUnit,
  cloneAssumptions,
  hasAssumptionErrors,
  validateAssumptions,
} from "../lib/backtest/assumptions";
import {
  buildStrategyNameCatalog,
  displayStrategyName,
  estimateMinutes,
  jobStatusLabel,
  resolveStrategyLabel,
  sessionLabel,
} from "../lib/backtest/format";
import {
  FIXTURE_STRATEGIES,
  cancelFixtureQueued,
  fixturePrechecks,
  getFixtureSession,
  listFixtureSessions,
  resetFixtureState,
  startFixtureSession,
} from "../lib/backtest/fixtureStore";
import { formIdentity, identityKey } from "../lib/backtest/identity";
import {
  buildP4StandardRequest,
  exactDuplicateAcknowledgements,
  isP4BatchTerminal,
  p4FormIdentity,
  p4RequestIdentity,
  parseP4StandardRequest,
  requestToAssumptionSnapshot,
  type P4Batch,
  type P4BatchJob,
  type P4DuplicateAcknowledgement,
  type P4PrecheckUnit,
} from "../lib/backtest/liveContract";
import {
  countProgress,
  formatElapsed,
  formatProgressSummary,
} from "../lib/backtest/progress";
import {
  defaultLocalRange,
  formatLocalAndUtc,
  formatUtcCounterpart,
  localDatetimeToUtcIso,
  utcIsoToDatetimeLocal,
} from "../lib/backtest/time";
import {
  useP4Live,
  type P4PrecheckState,
} from "../lib/backtest/useP4Live";
import {
  DEFAULT_ASSUMPTIONS,
  SYMBOLS,
  type AssumptionSnapshot,
  type BacktestMode,
  type BacktestSession,
  type FormSnapshot,
  type PrecheckState,
} from "../lib/backtest/types";

type LoadState = "loading" | "ready" | "error" | "empty";
const NO_LIVE_ACKNOWLEDGEMENTS: readonly P4DuplicateAcknowledgement[] = [];

interface StrategyOption {
  strategy_id: string;
  name: string;
  contracts: string[];
  session: string;
  /** null = backend did not provide (must not fabricate). */
  risk_pct: number | null;
  daily_loss_limit_r: number | null;
  pullback: number;
}

function useBacktestMode(): BacktestMode {
  const [params] = useSearchParams();
  return params.get("scenario") === "owner-review" ? "owner-review" : "live";
}

/**
 * P4 回測 — full page per docs/ui/designs/p4-backtest.html (17 constraints).
 * Live path: real API only, fail-closed gaps.
 * Owner-review: /backtest?scenario=owner-review isolated fixture.
 * Correction [123]: D1–D5.
 * P3 handoff: ?symbol=&startDate=&endDate=&from=data prefill form once.
 */
type BacktestTab = "setup" | "progress" | "history";

const BACKTEST_TABS: { id: BacktestTab; label: string }[] = [
  { id: "setup", label: "設定" },
  { id: "progress", label: "進度" },
  { id: "history", label: "最近" },
];

export function BacktestPage() {
  const mode = useBacktestMode();
  const isReview = mode === "owner-review";
  const [params] = useSearchParams();
  const [pageTab, setPageTab] = useState<BacktestTab>("setup");
  const dataHandoff = useMemo(
    () => (isReview ? null : parseHandoffSearchParams(params)),
    [isReview, params],
  );

  const defaults = defaultLocalRange();
  const [strategies, setStrategies] = useState<StrategyVersion[]>([]);
  const [strategiesLoad, setStrategiesLoad] = useState<LoadState>("loading");
  const [strategiesError, setStrategiesError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<string[]>(() =>
    dataHandoff ? [dataHandoff.symbol] : ["NQ"],
  );
  const [symbolNotice, setSymbolNotice] = useState<string | null>(null);
  const [rangeStartLocal, setRangeStartLocal] = useState(() =>
    dataHandoff
      ? tradingDayToLocalRangeStart(dataHandoff.rangeStartDate)
      : defaults.start,
  );
  const [rangeEndLocal, setRangeEndLocal] = useState(() =>
    dataHandoff
      ? tradingDayToLocalRangeEnd(dataHandoff.rangeEndDate)
      : defaults.end,
  );
  const [handoffNotice, setHandoffNotice] = useState<string | null>(() =>
    dataHandoff
      ? `已由數據頁帶入 ${dataHandoff.symbol} · ${dataHandoff.rangeStartDate} → ${dataHandoff.rangeEndDate}（可再改）`
      : null,
  );
  const handoffAppliedKey = useRef<string | null>(
    dataHandoff
      ? `${dataHandoff.symbol}|${dataHandoff.rangeStartDate}|${dataHandoff.rangeEndDate}`
      : null,
  );

  // Re-apply when Owner navigates with a new handoff query (same page instance).
  useEffect(() => {
    if (!dataHandoff || isReview) {
      return;
    }
    const key = `${dataHandoff.symbol}|${dataHandoff.rangeStartDate}|${dataHandoff.rangeEndDate}`;
    if (handoffAppliedKey.current === key) {
      return;
    }
    handoffAppliedKey.current = key;
    setSymbols([dataHandoff.symbol]);
    setRangeStartLocal(tradingDayToLocalRangeStart(dataHandoff.rangeStartDate));
    setRangeEndLocal(tradingDayToLocalRangeEnd(dataHandoff.rangeEndDate));
    setHandoffNotice(
      `已由數據頁帶入 ${dataHandoff.symbol} · ${dataHandoff.rangeStartDate} → ${dataHandoff.rangeEndDate}（可再改）`,
    );
  }, [dataHandoff, isReview]);
  const [assumptions, setAssumptions] = useState<AssumptionSnapshot>(() =>
    cloneAssumptions(DEFAULT_ASSUMPTIONS),
  );
  const [assumptionsOpen, setAssumptionsOpen] = useState(false);
  /** Acknowledgement only valid for this identity key. */
  const [forceDupIdentity, setForceDupIdentity] = useState<string | null>(
    null,
  );
  const [liveAcknowledgements, setLiveAcknowledgements] = useState<{
    formKey: string;
    items: P4DuplicateAcknowledgement[];
  } | null>(null);
  const [rerunSymbolAuthorization, setRerunSymbolAuthorization] = useState<{
    strategyKey: string;
    symbols: string[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fixtureTick, setFixtureTick] = useState(0);
  const [nowTick, setNowTick] = useState(() => Date.now());
  const [expandedErrors, setExpandedErrors] = useState<Record<string, boolean>>(
    {},
  );
  const [progressOpen, setProgressOpen] = useState(true);
  /** D13: auto-scroll to progress once per new session id. */
  const scrolledSessionRef = useRef<string | null>(null);
  const progressAnchorRef = useRef<HTMLElement | null>(null);
  const previousModeRef = useRef<BacktestMode>(mode);

  // Owner-review: never touch live storage; reset fixture on enter.
  useEffect(() => {
    const previous = previousModeRef.current;
    if (isReview) {
      resetFixtureState();
      setSelectedIds(["strategy-0001", "strategy-0002"]);
      setSymbols(["NQ", "YM"]);
      setAssumptions(cloneAssumptions(DEFAULT_ASSUMPTIONS));
      setForceDupIdentity(null);
      setLiveAcknowledgements(null);
      setRerunSymbolAuthorization(null);
      setSymbolNotice(null);
      setStrategiesLoad("ready");
    } else if (previous === "owner-review") {
      setSelectedIds([]);
      setSymbols(["NQ"]);
      setAssumptions(cloneAssumptions(DEFAULT_ASSUMPTIONS));
      setForceDupIdentity(null);
      setLiveAcknowledgements(null);
      setRerunSymbolAuthorization(null);
      setSymbolNotice(null);
    }
    previousModeRef.current = mode;
  }, [isReview, mode]);

  // Live: load confirmed strategies
  useEffect(() => {
    if (isReview) {
      return;
    }
    let cancelled = false;
    setStrategiesLoad("loading");
    setStrategiesError(null);
    void (async () => {
      try {
        const list = await fetchStrategies("confirmed");
        if (cancelled) {
          return;
        }
        setStrategies(list.versions);
        setStrategiesLoad(list.versions.length === 0 ? "empty" : "ready");
      } catch (err) {
        if (!cancelled) {
          setStrategies([]);
          setStrategiesLoad("error");
          setStrategiesError(
            err instanceof Error ? err.message : String(err),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isReview]);

  // Fixture poll tick + elapsed clock
  useEffect(() => {
    if (!isReview) {
      return;
    }
    const id = window.setInterval(() => {
      setFixtureTick((t) => t + 1);
      setNowTick(Date.now());
    }, 200);
    return () => {
      window.clearInterval(id);
    };
  }, [isReview]);

  const strategyOptions: StrategyOption[] = useMemo(() => {
    if (isReview) {
      return FIXTURE_STRATEGIES.map((s) => ({
        strategy_id: s.strategy_id,
        name: s.name,
        contracts: s.universe.contracts,
        session: s.universe.session,
        risk_pct: s.spec.risk_pct,
        daily_loss_limit_r: s.spec.daily_loss_limit_r,
        pullback: s.spec.pullback_ema_period,
      }));
    }
    // D4: never fabricate risk_pct / daily_loss_limit_r
    return strategies.map((s) => ({
      strategy_id: s.strategy_id,
      name: s.name,
      contracts: s.universe.contracts,
      session: s.universe.session,
      risk_pct: null,
      daily_loss_limit_r: null,
      pullback: s.spec.pullback_ema_period,
    }));
  }, [isReview, strategies]);

  /** D11: strategy_id → Owner-facing name for progress / history. */
  const strategyCatalog = useMemo(
    () => buildStrategyNameCatalog(strategyOptions),
    [strategyOptions],
  );

  const selected = strategyOptions.filter((s) =>
    selectedIds.includes(s.strategy_id),
  );
  const unresolvedSelectedIds = selectedIds.filter(
    (strategyId) =>
      !strategyOptions.some((option) => option.strategy_id === strategyId),
  );
  const selectedStrategyKey = [...selectedIds].sort().join("\u0000");

  const authorizedSymbols = useMemo(() => {
    const allowed =
      selected.length === 0
        ? new Set<string>(SYMBOLS)
        : selected.reduce<Set<string>>((acc, st) => {
            const permitted = new Set(
              st.contracts.map((contract) => contract.toUpperCase()),
            );
            return new Set([...acc].filter((symbol) => permitted.has(symbol)));
          }, new Set<string>(SYMBOLS));
    if (
      !isReview &&
      rerunSymbolAuthorization?.strategyKey === selectedStrategyKey
    ) {
      for (const symbol of rerunSymbolAuthorization.symbols) {
        allowed.add(symbol);
      }
    }
    return allowed;
  }, [
    isReview,
    rerunSymbolAuthorization,
    selected,
    selectedStrategyKey,
  ]);

  // D1: drop selected contracts that are no longer authorized — never selected+disabled
  useEffect(() => {
    setSymbols((prev) => {
      const next = prev.filter((s) => authorizedSymbols.has(s));
      if (next.length !== prev.length) {
        const removed = prev.filter((s) => !authorizedSymbols.has(s));
        setSymbolNotice(
          `已揀策略冇授權 ${removed.join("、")}，已自動移除（唔會保留停用勾選）。`,
        );
        return next;
      }
      return prev;
    });
  }, [authorizedSymbols]);

  const rangeStartUtc = localDatetimeToUtcIso(rangeStartLocal);
  const rangeEndUtc = localDatetimeToUtcIso(rangeEndLocal);

  const formSnapshot: FormSnapshot = useMemo(
    () => ({
      strategyIds: selectedIds,
      symbols,
      rangeStartLocal,
      rangeEndLocal,
      rangeStartUtc,
      rangeEndUtc,
      assumptions: cloneAssumptions(assumptions),
    }),
    [
      selectedIds,
      symbols,
      rangeStartLocal,
      rangeEndLocal,
      rangeStartUtc,
      rangeEndUtc,
      assumptions,
    ],
  );

  const currentIdentityKey = identityKey(formIdentity(formSnapshot));

  // D3/D5: forceDuplicate only for exact current identity
  const forceDuplicate = forceDupIdentity === currentIdentityKey;

  // Clear stale acknowledgement when identity drifts (checkbox stays false)
  useEffect(() => {
    if (forceDupIdentity && forceDupIdentity !== currentIdentityKey) {
      setForceDupIdentity(null);
    }
  }, [forceDupIdentity, currentIdentityKey]);

  const fieldErrors = validateAssumptions(assumptions, symbols);
  const fieldBad = hasAssumptionErrors(fieldErrors);

  const unitCount = useMemo(() => {
    if (!isReview) {
      return selectedIds.length * symbols.length;
    }
    let n = 0;
    for (const st of selected) {
      for (const sym of symbols) {
        if (st.contracts.map((c) => c.toUpperCase()).includes(sym)) {
          n += 1;
        }
      }
    }
    return n;
  }, [isReview, selected, selectedIds.length, symbols]);

  const perUnitCapital = capitalPerUnit(
    assumptions.initialCapital,
    unitCount || 1,
  );

  // Fixture prechecks stay entirely in-memory and are never used by normal mode.
  const fixturePrecheckState: PrecheckState = useMemo(() => {
    if (!isReview) {
      return {
        coverage: {
          status: "unknown",
          detail: "",
          gapDates: [],
        },
        warmup: {
          status: "unknown",
          detail: "",
          neededDays: null,
          availableDays: null,
          evaluableDays: null,
          suggestedStart: null,
        },
        duplicate: {
          status: "unknown",
          detail: "",
          priorSessionId: null,
          priorRunId: null,
          forceRun: false,
        },
      };
    }
    return fixturePrechecks(formSnapshot, forceDuplicate);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fixtureTick re-reads memory
  }, [formSnapshot, forceDuplicate, fixtureTick, isReview]);

  const baseLiveRequest = useMemo(
    () =>
      buildP4StandardRequest({
        strategyVersions: selectedIds,
        symbols,
        rangeStartUtc,
        rangeEndUtc,
        assumptions,
      }),
    [assumptions, rangeEndUtc, rangeStartUtc, selectedIds, symbols],
  );
  const baseLiveFormKey = baseLiveRequest
    ? p4FormIdentity(baseLiveRequest)
    : null;
  const activeAcknowledgements =
    liveAcknowledgements?.formKey === baseLiveFormKey
      ? liveAcknowledgements.items
      : NO_LIVE_ACKNOWLEDGEMENTS;
  const liveRequest = useMemo(
    () =>
      buildP4StandardRequest({
        strategyVersions: selectedIds,
        symbols,
        rangeStartUtc,
        rangeEndUtc,
        assumptions,
        duplicateAcknowledgements: activeAcknowledgements,
      }),
    [
      activeAcknowledgements,
      assumptions,
      rangeEndUtc,
      rangeStartUtc,
      selectedIds,
      symbols,
    ],
  );

  useEffect(() => {
    if (
      liveAcknowledgements &&
      liveAcknowledgements.formKey !== baseLiveFormKey
    ) {
      setLiveAcknowledgements(null);
    }
  }, [baseLiveFormKey, liveAcknowledgements]);

  const fixtureSessions = isReview ? listFixtureSessions() : [];
  const activeFixture =
    isReview && fixtureSessions[0]
      ? getFixtureSession(fixtureSessions[0].sessionId)
      : null;
  void fixtureTick;
  void nowTick;

  const scrollToProgressOnce = useCallback((sessionKey: string) => {
    if (scrolledSessionRef.current === sessionKey) {
      return;
    }
    scrolledSessionRef.current = sessionKey;
    const reduceMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    // Defer to next frame so the progress region is mounted
    window.requestAnimationFrame(() => {
      const el = progressAnchorRef.current;
      if (el && typeof el.scrollIntoView === "function") {
        el.scrollIntoView({
          behavior: reduceMotion ? "auto" : "smooth",
          block: "start",
        });
      }
    });
  }, []);

  const handleLiveAcknowledgements = useCallback(
    (items: P4DuplicateAcknowledgement[]) => {
      setLiveAcknowledgements(
        items.length > 0 && baseLiveFormKey
          ? { formKey: baseLiveFormKey, items }
          : null,
      );
    },
    [baseLiveFormKey],
  );

  const live = useP4Live({
    enabled: !isReview,
    request: liveRequest,
    onAcknowledgementsChanged: handleLiveAcknowledgements,
    onSubmitted: scrollToProgressOnce,
  });
  const liveRequestKey = liveRequest
    ? p4RequestIdentity(liveRequest)
    : null;
  const currentLivePrecheck =
    live.precheck.kind === "ready" &&
    live.precheck.requestKey === liveRequestKey
      ? live.precheck.document
      : null;

  useEffect(() => {
    if (
      isReview ||
      !live.active ||
      isP4BatchTerminal(live.active.status)
    ) {
      return;
    }
    setNowTick(Date.now());
    const id = window.setInterval(() => {
      setNowTick(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(id);
    };
  }, [isReview, live.active]);

  const startBlockers = useMemo(() => {
    const reasons: string[] = [];
    if (selectedIds.length === 0) {
      reasons.push("未揀策略");
    }
    if (symbols.length === 0) {
      reasons.push("未揀合約");
    }
    if (!rangeStartUtc || !rangeEndUtc) {
      reasons.push("時間範圍唔完整");
    }
    if (rangeStartUtc && rangeEndUtc && rangeStartUtc >= rangeEndUtc) {
      reasons.push("「由」必須早過「到」");
    }
    if (fieldBad) {
      reasons.push("資金或成交假設有錯誤");
    }
    if (unitCount === 0 && selectedIds.length > 0 && symbols.length > 0) {
      reasons.push("所揀合約唔喺策略授權範圍");
    }
    if (!isReview && !baseLiveRequest && reasons.length === 0) {
      reasons.push("設定資料未完整或格式有誤");
    }
    if (isReview) {
      for (const key of ["coverage", "warmup", "duplicate"] as const) {
        const check = fixturePrecheckState[key];
        if (
          check.status === "block" &&
          !(key === "duplicate" && forceDuplicate)
        ) {
          reasons.push(check.detail);
        }
      }
    } else if (baseLiveRequest) {
      if (
        live.precheck.kind === "loading" ||
        live.precheck.kind === "idle" ||
        live.precheck.requestKey !== liveRequestKey
      ) {
        reasons.push("正在核實開始前檢查");
      } else if (live.precheck.kind === "error") {
        reasons.push(live.precheck.message);
      } else if (!live.precheck.document.can_submit) {
        reasons.push("開始前檢查未通過，請先處理下面項目");
      }
    }
    return reasons;
  }, [
    baseLiveRequest,
    fieldBad,
    fixturePrecheckState,
    forceDuplicate,
    isReview,
    live.precheck,
    liveRequestKey,
    rangeEndUtc,
    rangeStartUtc,
    selectedIds.length,
    symbols.length,
    unitCount,
  ]);

  const canStart =
    startBlockers.length === 0 &&
    unitCount > 0 &&
    (isReview ||
      (currentLivePrecheck !== null && currentLivePrecheck.can_submit));

  // Submitting a backtest is a write; the transport layer would refuse it
  // anyway, so the button says so rather than letting the owner find out.
  const offline = useWritesBlocked();

  const onStart = () => {
    if (!canStart) {
      return;
    }
    setError(null);
    if (isReview) {
      const session = startFixtureSession(formSnapshot);
      setForceDupIdentity(null);
      setFixtureTick((tick) => tick + 1);
      scrollToProgressOnce(session.sessionId);
      return;
    }
    void live.submitCurrent();
  };

  const sessionsForHistory: BacktestSession[] = isReview
    ? fixtureSessions
    : [];

  return (
    <div className="detail-stack" data-backtest-mode={mode}>
      <PageHeader
        title="回測"
        info={
          <>
            揀已確認嘅策略版本同合約 → 系統先做檢查 → 開始 → 睇進度 → 去結果。
            日期用你本地時間顯示，送去後台之前一律轉做世界時間，兩者唔會撈亂；交易時段由策略文件決定，喺呢頁改唔到。
            檢查有問題會列出原因並攔住，唔會靜靜哋跑一個冇意義嘅回測。
          </>
        }
      />

      <PageTabBar
        ariaLabel="回測分頁"
        className="page-tabs--sticky"
        active={pageTab}
        onChange={setPageTab}
        tabs={BACKTEST_TABS}
      />

      {!isReview && pageTab === "setup" ? (
        <ComputeArchitecturePanel compact />
      ) : null}

      {isReview ? (
        <p className="backtest-banner" role="status">
          <strong>Owner 驗收入口</strong>
          （<code>?scenario=owner-review</code>
          ）——用隔離示範資料，完整操作三個區段；
          <strong>唔寫後端、唔污染真記錄</strong>。正常{" "}
          <code>/backtest</code> 永遠唔會讀呢份資料。
        </p>
      ) : null}
      {handoffNotice ? (
        <p className="state-msg" role="status" data-testid="data-handoff-notice">
          {handoffNotice}
        </p>
      ) : null}

      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}
      {!isReview && live.submitError ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="live-submit-error"
        >
          {live.submitError}
        </p>
      ) : null}

      {/* —— 設定 —— */}
      {pageTab === "setup" ? (
      <section
        className="panel"
        role="tabpanel"
        id="page-panel-setup"
        aria-labelledby="page-tab-setup"
        aria-label="設定與開始"
      >
        <div className="panel__head">
          <h2 className="panel__title">設定回測</h2>
          <InfoButton label="設定回測點填" align="end">
            由上到下填：策略版本（只列已確認嘅——揀唔到就去 策略工作台
            確認先）、合約（可多選）、日期範圍。填齊之後撳開始，系統會先跑一次檢查。
          </InfoButton>
        </div>

        <div className="field">
          <span>策略（只列已確認 · 可多選）</span>
          {!isReview && strategiesLoad === "loading" ? (
            <p className="state-msg" data-testid="strategies-loading">
              正在載入已確認策略…
            </p>
          ) : null}
          {!isReview && strategiesLoad === "error" ? (
            <p
              className="state-msg state-msg--error"
              data-testid="strategies-error"
              role="alert"
            >
              策略列表載入失敗：{strategiesError}
            </p>
          ) : null}
          {strategyOptions.length === 0 &&
          (isReview || strategiesLoad === "empty" || strategiesLoad === "ready") ? (
            <p className="panel__note" data-testid="strategies-empty">
              仲未有已確認策略。請去{" "}
              <Link className="text-link" to="/strategies">
                策略工作台
              </Link>{" "}
              確認版本。
            </p>
          ) : strategyOptions.length > 0 ? (
            <div className="chip-row">
              {strategyOptions.map((st) => {
                const on = selectedIds.includes(st.strategy_id);
                return (
                  <button
                    key={st.strategy_id}
                    type="button"
                    className={on ? "chip-btn chip-btn--on" : "chip-btn"}
                    aria-pressed={on}
                    title={st.strategy_id}
                    data-testid={`strategy-chip-${st.strategy_id}`}
                    onClick={() => {
                      setRerunSymbolAuthorization(null);
                      setSelectedIds((prev) =>
                        prev.includes(st.strategy_id)
                          ? prev.filter((id) => id !== st.strategy_id)
                          : [...prev, st.strategy_id],
                      );
                    }}
                  >
                    <span className="chip-btn__name">
                      {displayStrategyName(st.name)}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : null}
          {unresolvedSelectedIds.length > 0 ? (
            <div className="chip-row">
              {unresolvedSelectedIds.map((strategyId) => (
                <span
                  className="chip-btn chip-btn--on"
                  data-testid={`unresolved-strategy-${strategyId}`}
                  key={strategyId}
                  title={strategyId}
                >
                  策略名稱暫未取得
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="field">
          <span>合約（可多選）</span>
          {symbolNotice ? (
            <p className="panel__note" data-testid="symbol-notice">
              {symbolNotice}
            </p>
          ) : null}
          <div className="chip-row">
            {SYMBOLS.map((symbol) => {
              const allowed = authorizedSymbols.has(symbol);
              const on = symbols.includes(symbol);
              // D1 invariant: never selected + disabled
              const pressed = on && allowed;
              return (
                <button
                  key={symbol}
                  type="button"
                  disabled={!allowed}
                  className={
                    pressed
                      ? "chip-btn chip-btn--on"
                      : allowed
                        ? "chip-btn"
                        : "chip-btn chip-btn--blocked"
                  }
                  aria-pressed={pressed}
                  data-selected={pressed ? "true" : "false"}
                  title={
                    allowed ? undefined : "已揀策略冇授權呢個合約"
                  }
                  onClick={() => {
                    if (!allowed) {
                      return;
                    }
                    setRerunSymbolAuthorization(null);
                    setSymbolNotice(null);
                    setSymbols((prev) =>
                      prev.includes(symbol)
                        ? prev.filter((s) => s !== symbol)
                        : [...prev, symbol],
                    );
                  }}
                >
                  {symbol}
                </button>
              );
            })}
          </div>
        </div>

        {/* The picker changed, the contract did not: DateField still speaks
            local wall-clock `YYYY-MM-DDTHH:mm:ss`, and the UTC counterpart
            below each field is still what actually goes to the backend. */}
        <div className="form-grid">
          <div className="field">
            <span>由（本地時間）</span>
            <DateField
              label="由 本地時間"
              value={rangeStartLocal}
              testId="range-start-field"
              onChange={setRangeStartLocal}
            />
            <div className="utc-hint" aria-label="由 UTC 對照">
              UTC：{formatUtcCounterpart(rangeStartLocal)}
            </div>
            {!rangeStartUtc ? (
              <p className="field-error">請揀有效嘅開始時間</p>
            ) : null}
          </div>
          <div className="field">
            <span>到（本地時間）</span>
            <DateField
              label="到 本地時間"
              value={rangeEndLocal}
              testId="range-end-field"
              onChange={setRangeEndLocal}
            />
            <div className="utc-hint" aria-label="到 UTC 對照">
              UTC：{formatUtcCounterpart(rangeEndLocal)}
            </div>
            {!rangeEndUtc ? (
              <p className="field-error">請揀有效嘅結束時間</p>
            ) : rangeStartUtc && rangeStartUtc >= rangeEndUtc ? (
              <p className="field-error">結束時間必須遲過開始時間</p>
            ) : null}
          </div>
        </div>

        <div className="field">
          <span>交易時段（由策略文件決定 · 唯讀）</span>
          {selectedIds.length === 0 ? (
            <p className="state-msg">揀策略後顯示</p>
          ) : selected.length === 0 ? (
            <p className="state-msg">
              策略資料暫未取得；交易時段會由開始前檢查顯示
            </p>
          ) : (
            <ul className="precheck-list">
              {selected.map((st) => (
                <li key={st.strategy_id} className="precheck-item">
                  {displayStrategyName(st.name)}：{sessionLabel(st.session)}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* ④ 資金與成交假設 */}
        <div className="panel" style={{ padding: "var(--space-3)" }}>
          <button
            type="button"
            className="fold-header"
            aria-expanded={assumptionsOpen}
            onClick={() => {
              setAssumptionsOpen((o) => !o);
            }}
          >
            ④ 資金與成交假設 {assumptionsOpen ? "（收起）" : "（展開調整）"}
          </button>
          <p className="assum-summary" data-testid="capital-per-unit">
            {assumptionsSummary(assumptions)}
            {" · "}
            每個「策略 × 合約」各自用完整 USD{" "}
            {perUnitCapital.toLocaleString("en-US")} 起步
            {unitCount > 0
              ? `（今次 ${unitCount} 個獨立帳戶，唔共同攤分）`
              : ""}
          </p>
          {assumptionsOpen ? (
            <div className="form-grid" style={{ marginTop: "var(--space-3)" }}>
              <label className="field">
                <span>每次回測初始資金（USD）</span>
                <input
                  className="inp"
                  type="number"
                  min={1}
                  step={1000}
                  aria-label="初始資金"
                  value={assumptions.initialCapital}
                  onChange={(e) => {
                    setAssumptions((a) => ({
                      ...a,
                      initialCapital: Number(e.target.value),
                    }));
                  }}
                />
                {fieldErrors.initialCapital ? (
                  <p className="field-error">{fieldErrors.initialCapital}</p>
                ) : null}
              </label>
              {symbols.map((sym) => (
                <label key={sym} className="field">
                  <span>{sym} 每邊每張手續費（USD）</span>
                  <input
                    className="inp"
                    type="number"
                    min={0}
                    step={0.25}
                    aria-label={`${sym} 手續費`}
                    value={assumptions.fees[sym] ?? 0}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      setAssumptions((a) => ({
                        ...a,
                        fees: { ...a.fees, [sym]: v },
                      }));
                    }}
                  />
                  {fieldErrors.fees?.[sym] ? (
                    <p className="field-error">{fieldErrors.fees[sym]}</p>
                  ) : null}
                </label>
              ))}
              {(
                [
                  ["breakout", "突破"],
                  ["stop", "止蝕"],
                  ["target", "目標"],
                  ["dayEnd", "日終"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="field">
                  <span>{label} 滑點（整數 tick）</span>
                  <input
                    className="inp"
                    type="number"
                    min={0}
                    step={1}
                    aria-label={`${label} 滑點`}
                    value={assumptions.slippageTicks[key]}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      setAssumptions((a) => ({
                        ...a,
                        slippageTicks: { ...a.slippageTicks, [key]: v },
                      }));
                    }}
                  />
                  {fieldErrors.slippage?.[key] ? (
                    <p className="field-error">{fieldErrors.slippage[key]}</p>
                  ) : null}
                </label>
              ))}
              <div
                className="field"
                style={{ gridColumn: "1 / -1" }}
                data-testid="risk-readonly"
              >
                <span>策略風險／成交原則（唯讀）</span>
                <ul className="precheck-list">
                  {selected.map((st) => (
                    <li key={st.strategy_id} className="precheck-item">
                      {displayStrategyName(st.name)}：
                      {st.risk_pct == null || st.daily_loss_limit_r == null
                        ? "後端未提供風險／每日上限，暫未可核對"
                        : `風險 ${st.risk_pct}% · 每日上限 ${st.daily_loss_limit_r}R`}
                    </li>
                  ))}
                  <li className="precheck-item">
                    系統：同分鐘止蝕優先 · 跳空按開市價 · 一分鐘精度（唔可改）
                  </li>
                </ul>
              </div>
            </div>
          ) : null}
        </div>

        {/* 開始前檢查 */}
        <div className="field">
          <span>開始前檢查</span>
          {isReview ? (
            <ul className="precheck-list">
              <li
                className={`precheck-item precheck-item--${fixturePrecheckState.coverage.status}`}
              >
                <strong>數據覆蓋</strong> ·{" "}
                {fixturePrecheckState.coverage.detail}
                {fixturePrecheckState.coverage.gapDates.length > 0 ? (
                  <span>
                    {" "}
                    缺口日：
                    {fixturePrecheckState.coverage.gapDates.join("、")}
                  </span>
                ) : null}
              </li>
              <li
                className={`precheck-item precheck-item--${fixturePrecheckState.warmup.status}`}
              >
                <strong>暖機</strong> · {fixturePrecheckState.warmup.detail}
                {fixturePrecheckState.warmup.suggestedStart ? (
                  <span>
                    {" "}
                    建議起點：{fixturePrecheckState.warmup.suggestedStart}
                  </span>
                ) : null}
              </li>
              <li
                className={`precheck-item precheck-item--${fixturePrecheckState.duplicate.status}`}
                data-testid="duplicate-check"
              >
                <strong>相同組合</strong> ·{" "}
                {fixturePrecheckState.duplicate.detail}
                {fixturePrecheckState.duplicate.status === "block" ? (
                  <div className="form-actions">
                    {fixturePrecheckState.duplicate.priorRunId ? (
                      <Link
                        className="btn"
                        to={`/results/${fixturePrecheckState.duplicate.priorRunId}`}
                      >
                        去睇舊結果
                      </Link>
                    ) : null}
                    <label className="toggle-row">
                      <input
                        type="checkbox"
                        data-testid="force-duplicate"
                        checked={forceDuplicate}
                        onChange={(event) => {
                          setForceDupIdentity(
                            event.target.checked ? currentIdentityKey : null,
                          );
                        }}
                      />
                      <span>我知，照跑</span>
                    </label>
                  </div>
                ) : null}
              </li>
            </ul>
          ) : (
            <LivePrecheckPanel
              state={live.precheck}
              currentRequestKey={liveRequestKey}
              catalog={strategyCatalog}
              selectedSymbol={symbols[0] ?? "NQ"}
              acknowledgementCount={activeAcknowledgements.length}
              onAcknowledge={() => {
                if (!currentLivePrecheck || !baseLiveFormKey) {
                  return;
                }
                const items =
                  exactDuplicateAcknowledgements(currentLivePrecheck);
                setLiveAcknowledgements(
                  items.length > 0
                    ? { formKey: baseLiveFormKey, items }
                    : null,
                );
              }}
              onSuggestedStart={(utcValue) => {
                const localValue = utcIsoToDatetimeLocal(utcValue);
                if (localValue) {
                  setRangeStartLocal(localValue);
                }
              }}
            />
          )}
        </div>

        <p className="state-msg" data-testid="preview-line">
          預覽：{isReview ? selected.length : selectedIds.length} 個策略 ×{" "}
          {symbols.length} 個合約 ＝ {unitCount} 次回測
          {unitCount > 0
            ? `${isReview ? ` · 約 ${estimateMinutes(unitCount)} 分鐘` : ""} · 每次各自由 USD ${perUnitCapital.toLocaleString("en-US")} 起步`
            : ""}
        </p>
        {!canStart && startBlockers.length > 0 ? (
          <p className="state-msg state-msg--error" role="status">
            未可開始：{startBlockers.join(" · ")}
          </p>
        ) : null}

        <div className="form-actions">
          <button
            type="button"
            className="btn btn--primary"
            disabled={!canStart || live.submitting || offline}
            data-testid="start-backtest"
            onClick={() => {
              onStart();
              setPageTab("progress");
            }}
          >
            ▶ 開始回測
          </button>
          {offline ? (
            <span className="state-msg">離線 · 暫時開始唔到</span>
          ) : null}
        </div>
      </section>
      ) : null}

      {/* —— 進度 —— */}
      {pageTab === "progress" ? (
        <>
      {isReview && activeFixture ? (
        <ProgressFixture
          session={activeFixture}
          open={progressOpen}
          nowMs={nowTick}
          sectionRef={progressAnchorRef}
          onToggle={() => {
            setProgressOpen((o) => !o);
          }}
          onCancelQueued={() => {
            cancelFixtureQueued(activeFixture.sessionId);
            setFixtureTick((t) => t + 1);
          }}
        />
      ) : null}

      {!isReview && live.active ? (
        <LiveProgressPanel
          batch={live.active}
          catalog={strategyCatalog}
          open={progressOpen}
          nowMs={nowTick}
          sectionRef={progressAnchorRef}
          pollError={live.pollError}
          cancelBusy={live.cancelBusy}
          cancelError={live.cancelError}
          onToggle={() => {
            setProgressOpen((open) => !open);
          }}
          onCancelQueued={() => {
            void live.cancelQueued();
          }}
        />
      ) : null}

      {!isReview && !live.active && !(isReview && activeFixture) ? (
        <p className="state-msg" role="status">
          暫時冇進行中嘅回測。去「① 設定」開始一次。
        </p>
      ) : null}
        </>
      ) : null}

      {/* —— 最近 —— */}
      {pageTab === "history" ? (
      <section
        className="panel"
        role="tabpanel"
        id="page-panel-history"
        aria-labelledby="page-tab-history"
        aria-label="最近嘅回測"
      >
        <div className="panel__head">
          <h2 className="panel__title">最近嘅回測</h2>
          <InfoButton label="最近嘅回測點睇" align="end">
            由呢個介面交出去嘅回測記錄。撳一行就會去到嗰次嘅結果頁。想睇進度就切去「進度」分頁。
          </InfoButton>
        </div>
        {isReview ? (
          sessionsForHistory.length === 0 ? (
            <p className="state-msg">仲未有——喺上面開始一次。</p>
          ) : (
            <HistoryFixture
              sessions={sessionsForHistory}
              expanded={expandedErrors}
              onToggleError={(id) => {
                setExpandedErrors((prev) => ({ ...prev, [id]: !prev[id] }));
              }}
              onRerun={(snap) => {
                // D3: restore snapshot only — never auto-ack duplicate
                setSelectedIds([...snap.strategyIds]);
                setSymbols([...snap.symbols]);
                setRangeStartLocal(snap.rangeStartLocal);
                setRangeEndLocal(snap.rangeEndLocal);
                setAssumptions(cloneAssumptions(snap.assumptions));
                setForceDupIdentity(null);
                setFixtureTick((t) => t + 1);
                try {
                  window.scrollTo({ top: 0, behavior: "smooth" });
                } catch {
                  /* jsdom may not implement scrollTo */
                }
              }}
            />
          )
        ) : live.historyLoad === "loading" ? (
          <p className="state-msg" data-testid="history-loading">
            正在載入最近回測…
          </p>
        ) : live.historyLoad === "error" ? (
          <p
            className="state-msg state-msg--error"
            data-testid="history-error"
            role="alert"
          >
            {live.historyError}
          </p>
        ) : live.historyLoad === "empty" || live.history.length === 0 ? (
          <p className="state-msg" data-testid="history-empty">
            仲未有提交記錄。
          </p>
        ) : (
          <LiveHistoryTable
            batches={live.history}
            catalog={strategyCatalog}
            expanded={expandedErrors}
            onToggleError={(id) => {
              setExpandedErrors((prev) => ({ ...prev, [id]: !prev[id] }));
            }}
            onRerun={(request) => {
              setSelectedIds([...request.strategy_versions]);
              setSymbols([...request.symbols]);
              setRangeStartLocal(
                utcIsoToDatetimeLocal(request.range_start),
              );
              setRangeEndLocal(utcIsoToDatetimeLocal(request.range_end));
              setAssumptions(requestToAssumptionSnapshot(request));
              setLiveAcknowledgements(null);
              setRerunSymbolAuthorization({
                strategyKey: [...request.strategy_versions]
                  .sort()
                  .join("\u0000"),
                symbols: [...request.symbols],
              });
              try {
                window.scrollTo({ top: 0, behavior: "smooth" });
              } catch {
                /* jsdom may not implement scrollTo */
              }
            }}
          />
        )}
      </section>
      ) : null}
    </div>
  );
}

function reasonCopy(reason: string): string {
  const copy: Record<string, string> = {
    coverage_complete: "所選交易日覆蓋完整",
    coverage_owner_trusted: "包括 Owner 已確認可用嘅問題日",
    coverage_owner_excluded: "已扣除 Owner 決定唔用嘅交易日",
    coverage_roll_blackout: "已扣除轉倉封鎖日",
    coverage_pending_problem: "仍有問題日等緊裁決",
    coverage_minute_missing: "一分鐘數據有缺口",
    coverage_native_daily_missing: "原生日線數據未齊",
    coverage_all_dates_excluded: "所選範圍冇可用交易日",
    coverage_unknown: "數據覆蓋暫時核實唔到",
    warmup_sufficient: "暖機歷史足夠",
    warmup_short: "暖機歷史不足",
    warmup_unknown: "暖機狀況暫時核實唔到",
    warmup_no_evaluable_dates: "範圍內冇可評估交易日",
    duplicate_none: "未發現完全相同組合",
    duplicate_exact_match: "發現完全相同嘅舊回測",
    duplicate_acknowledged: "已記錄今次精確相同組合確認",
    duplicate_acknowledgement_stale: "先前確認已失效，請重新核對",
    duplicate_index_unavailable: "舊回測索引暫時核實唔到",
    duplicate_identity_unproven: "部分舊回測身份未能證實",
    duplicate_catalog_integrity_error: "舊回測索引暫時唔可信",
    strategy_unavailable: "策略版本暫時唔可用",
    strategy_symbol_not_authorized: "策略未授權呢個合約",
  };
  return copy[reason] ?? "呢項資料暫時核實唔到";
}

function ReasonFacts({ reasons }: { reasons: readonly string[] }) {
  const unique = [...new Set(reasons)];
  return unique.length > 0 ? (
    <div className="precheck-reasons">
      {unique.map((reason) => (
        <span key={reason}>{reasonCopy(reason)}</span>
      ))}
    </div>
  ) : null;
}

function DateFacts({
  label,
  dates,
}: {
  label: string;
  dates: readonly string[];
}) {
  return dates.length > 0 ? (
    <div>
      <strong>{label}：</strong>
      <span className="table__mono">{dates.join("、")}</span>
    </div>
  ) : null;
}

function UnitHeading({
  unit,
  catalog,
}: {
  unit: P4PrecheckUnit;
  catalog: ReadonlyMap<string, string>;
}) {
  const resolved = resolveStrategyLabel(unit.strategy_version, catalog);
  return (
    <div className="precheck-unit__heading">
      <strong title={resolved.versionId ?? undefined}>{resolved.label}</strong>
      <span>{unit.symbol}</span>
      <span>
        {unit.session_name
          ? sessionLabel(unit.session_name)
          : "交易時段暫時未能核實"}
      </span>
    </div>
  );
}

function LivePrecheckPanel({
  state,
  currentRequestKey,
  catalog,
  selectedSymbol,
  acknowledgementCount,
  onAcknowledge,
  onSuggestedStart,
}: {
  state: P4PrecheckState;
  currentRequestKey: string | null;
  catalog: ReadonlyMap<string, string>;
  selectedSymbol: string;
  acknowledgementCount: number;
  onAcknowledge: () => void;
  onSuggestedStart: (utcValue: string) => void;
}) {
  if (!currentRequestKey || state.kind === "idle") {
    return (
      <p className="state-msg" data-testid="live-precheck-idle">
        填妥策略、合約、時間同資金假設後，系統會自動核實。
      </p>
    );
  }
  if (
    state.requestKey !== currentRequestKey ||
    state.kind === "loading"
  ) {
    return (
      <p className="state-msg" data-testid="live-precheck-loading">
        正在核實最新設定…
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <p
        className="state-msg state-msg--error"
        role="alert"
        data-testid="live-precheck-error"
      >
        {state.message}
      </p>
    );
  }

  const document = state.document;
  const exactDuplicates = exactDuplicateAcknowledgements(document);
  return (
    <div data-testid="live-precheck-ready">
      <p className="state-msg">
        已核實 {document.unit_count} 次回測 ·{" "}
        {document.can_submit ? "可以開始" : "有項目要先處理"}
      </p>
      <ul className="precheck-list">
        {document.units.map((unit) => (
          <li
            className="precheck-unit"
            key={`${unit.strategy_version}-${unit.symbol}`}
            data-testid={`precheck-unit-${unit.strategy_version}-${unit.symbol}`}
          >
            <UnitHeading unit={unit} catalog={catalog} />
            <div
              className={`precheck-item precheck-item--${unit.coverage.status}`}
            >
              <strong>數據覆蓋</strong>
              <div>
                要求 {unit.coverage.requested_trading_date_count} 日 · 可用{" "}
                {unit.coverage.admitted_trading_date_count} 日
              </div>
              <DateFacts
                label="可用完整日"
                dates={unit.coverage.complete_trading_dates}
              />
              <DateFacts
                label="Owner 已確認可用"
                dates={unit.coverage.owner_trusted_problem_trading_dates}
              />
              <DateFacts
                label="Owner 已排除"
                dates={unit.coverage.owner_excluded_trading_dates}
              />
              <DateFacts
                label="轉倉封鎖日"
                dates={unit.coverage.roll_blackout_trading_dates}
              />
              <DateFacts
                label="仍然阻塞"
                dates={unit.coverage.blocking_problem_trading_dates}
              />
              <DateFacts
                label="欠原生日線"
                dates={unit.coverage.missing_native_daily_trading_dates}
              />
              <ReasonFacts reasons={unit.coverage.reason_codes} />
              {unit.coverage.status === "block" ||
              unit.coverage.status === "unknown" ? (
                <div className="form-actions">
                  <Link
                    className="btn"
                    to={`/data?symbol=${encodeURIComponent(unit.symbol || selectedSymbol)}`}
                  >
                    去數據頁
                  </Link>
                </div>
              ) : null}
            </div>

            <div
              className={`precheck-item precheck-item--${unit.warmup.status}`}
            >
              <strong>暖機</strong>
              <div>
                需要{" "}
                {unit.warmup.required_prior_trading_date_count ?? "暫未核實"}{" "}
                個之前交易日 · 可用{" "}
                {unit.warmup.available_prior_trading_date_count ??
                  "暫未核實"}{" "}
                · 可評估{" "}
                {unit.warmup.evaluable_trading_date_count ?? "暫未核實"} 日
              </div>
              <div>
                首個可評估交易日：
                <span className="table__mono">
                  {unit.warmup.first_evaluable_trading_date ?? "暫未有"}
                </span>
              </div>
              {unit.warmup.suggested_range_start ? (
                <div>
                  <div>
                    後端建議將開始日推後到：
                    {formatLocalAndUtc(unit.warmup.suggested_range_start)}
                  </div>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => {
                      onSuggestedStart(unit.warmup.suggested_range_start!);
                    }}
                  >
                    採用建議起點
                  </button>
                </div>
              ) : null}
              {unit.warmup.reason_codes.includes("warmup_short") &&
              !unit.warmup.suggested_range_start ? (
                <div>可先去數據頁補齊更早歷史，再重新核實。</div>
              ) : null}
              <ReasonFacts reasons={unit.warmup.reason_codes} />
            </div>

            <div
              className={`precheck-item precheck-item--${unit.duplicate.status}`}
            >
              <strong>相同組合</strong>
              <div>
                {unit.duplicate.count_known
                  ? `已知完全相同 ${unit.duplicate.exact_match_count ?? 0} 次`
                  : "相同組合數量暫時核實唔到"}
                {unit.duplicate.unindexed_candidate_count
                  ? ` · 另有 ${unit.duplicate.unindexed_candidate_count} 份舊記錄未能證實`
                  : ""}
              </div>
              {unit.duplicate.prior_run_ids.length > 0 ? (
                <div className="form-actions">
                  {unit.duplicate.prior_run_ids.map((runId, index) => (
                    <Link
                      className="btn"
                      key={runId}
                      to={`/results/${runId}`}
                      title={runId}
                    >
                      睇舊結果 {index + 1}
                    </Link>
                  ))}
                </div>
              ) : null}
              <ReasonFacts reasons={unit.duplicate.reason_codes} />
            </div>
            <ReasonFacts reasons={unit.reason_codes} />
          </li>
        ))}
      </ul>
      {exactDuplicates.length > 0 ? (
        <div className="form-actions precheck-ack">
          <button
            type="button"
            className="btn"
            disabled={acknowledgementCount > 0}
            data-testid="acknowledge-live-duplicates"
            onClick={onAcknowledge}
          >
            照跑，我要再試一次
          </button>
          {acknowledgementCount > 0 ? (
            <span className="state-msg">
              已確認 {acknowledgementCount} 個精確相同組合；後端已重新核實。
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function warningCopy(_warning: string): string {
  void _warning;
  return "有附加提醒；結果仍然有效，請到結果頁核對。";
}

async function copyExactText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

function LiveProgressPanel({
  batch,
  catalog,
  open,
  nowMs,
  sectionRef,
  pollError,
  cancelBusy,
  cancelError,
  onToggle,
  onCancelQueued,
}: {
  batch: P4Batch;
  catalog: ReadonlyMap<string, string>;
  open: boolean;
  nowMs: number;
  sectionRef: RefObject<HTMLElement | null>;
  pollError: string | null;
  cancelBusy: boolean;
  cancelError: string | null;
  onToggle: () => void;
  onCancelQueued: () => void;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [copyState, setCopyState] = useState<
    Record<string, "copied" | "failed">
  >({});
  const display = batch.summary;
  const terminal =
    display.completed + display.failed + display.cancelled;
  const elapsedSeconds = Math.max(
    0,
    Math.floor(
      ((isP4BatchTerminal(batch.status)
        ? Date.parse(batch.updated_at)
        : nowMs) -
        Date.parse(batch.created_at)) /
        1000,
    ),
  );
  return (
    <section
      className="panel"
      role="region"
      aria-label="正在回測"
      ref={sectionRef as RefObject<HTMLDivElement>}
      data-testid="live-progress-region"
    >
      <h2 className="panel__title">正在回測</h2>
      <p className="state-msg" data-testid="live-progress-summary">
        {formatProgressSummary(display)}
      </p>
      <p className="utc-hint" data-testid="live-progress-timing">
        建立：{formatLocalAndUtc(batch.created_at)} · 已用：
        {formatElapsed(elapsedSeconds)}
      </p>
      <div className="progress-bar" aria-hidden>
        <div
          className="progress-bar__fill"
          style={{
            width: `${
              display.total ? (100 * terminal) / display.total : 0
            }%`,
          }}
        />
      </div>
      {pollError ? (
        <p
          className="state-msg state-msg--error"
          role="status"
          data-testid="live-poll-error"
        >
          {pollError}
        </p>
      ) : null}
      {cancelError ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="live-cancel-error"
        >
          {cancelError}
        </p>
      ) : null}
      <div className="form-actions form-actions--progress">
        <button type="button" className="btn btn--quiet" onClick={onToggle}>
          {open ? "收起逐項" : "展開逐項"}
        </button>
        <button
          type="button"
          className={
            display.queued > 0
              ? "btn btn--cancel-queued"
              : "btn btn--cancel-queued btn--cancel-queued-disabled"
          }
          disabled={display.queued === 0 || cancelBusy}
          aria-busy={cancelBusy}
          data-testid="live-cancel-queued"
          onClick={onCancelQueued}
        >
          取消未開始嘅回測（正在跑嗰個會跑完）
        </button>
      </div>
      {open ? (
        <div className="table-wrap">
          <table className="table" data-testid="live-progress-table">
            <thead>
              <tr>
                <th>策略</th>
                <th>合約</th>
                <th>狀態</th>
                <th>交易日</th>
                <th>已處理日</th>
                <th>交易數</th>
                <th>實現盈虧</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {batch.jobs.map((job) => {
                const resolved = resolveStrategyLabel(
                  job.strategy_version,
                  catalog,
                );
                const progress = job.progress;
                const preparing =
                  progress?.current_trading_date === null &&
                  progress.processed_trading_date_count === 0;
                return (
                  <tr
                    key={job.job_id}
                    className={
                      job.status === "running"
                        ? "job-row--running"
                        : undefined
                    }
                    data-testid={`live-job-${job.job_id}`}
                  >
                    <td title={resolved.versionId ?? undefined}>
                      <span data-testid={`live-job-name-${job.job_id}`}>
                        {resolved.label}
                      </span>
                      {resolved.versionId ? (
                        <div className="utc-hint">{resolved.versionId}</div>
                      ) : null}
                    </td>
                    <td>{job.symbol}</td>
                    <td>{jobStatusLabel(job.status)}</td>
                    <td className="table__mono">
                      {preparing
                        ? "準備中"
                        : progress?.current_trading_date ?? "—"}
                    </td>
                    <td>
                      {progress
                        ? `${progress.processed_trading_date_count}/${progress.total_trading_date_count}`
                        : "—"}
                    </td>
                    <td>{progress?.trade_count ?? "—"}</td>
                    <td className="table__mono">
                      {progress
                        ? `${progress.realized_net_r}R / USD ${progress.realized_net_pnl_usd}`
                        : "—"}
                    </td>
                    <td>
                      {job.status === "completed" ? (
                        <Link
                          className="table__link"
                          to={`/results/${job.run_id}`}
                        >
                          看結果
                        </Link>
                      ) : null}
                      {job.warnings.map((warning, index) => (
                        <span
                          className="job-warning"
                          key={`${job.job_id}-warning-${index}`}
                        >
                          {warningCopy(warning)}
                        </span>
                      ))}
                      {job.status === "failed" && job.error_full ? (
                        <div>
                          <button
                            type="button"
                            className="btn"
                            onClick={() => {
                              setExpanded((current) => ({
                                ...current,
                                [job.job_id]: !current[job.job_id],
                              }));
                            }}
                          >
                            看原因 · {job.symbol}
                          </button>
                          {expanded[job.job_id] ? (
                            <div className="error-expand">
                              <pre
                                data-testid={`live-error-full-${job.job_id}`}
                              >
                                {job.error_full}
                              </pre>
                              <button
                                type="button"
                                className="btn"
                                onClick={() => {
                                  void copyExactText(job.error_full!).then(
                                    (ok) => {
                                      setCopyState((current) => ({
                                        ...current,
                                        [job.job_id]: ok
                                          ? "copied"
                                          : "failed",
                                      }));
                                    },
                                  );
                                }}
                              >
                                複製全文
                              </button>
                              <span role="status">
                                {copyState[job.job_id] === "copied"
                                  ? "已複製"
                                  : copyState[job.job_id] === "failed"
                                    ? "複製失敗，請手動選取"
                                    : ""}
                              </span>
                              <Link
                                className="btn"
                                to={`/data?symbol=${encodeURIComponent(job.symbol)}&date=${encodeURIComponent(job.progress?.current_trading_date ?? "")}`}
                              >
                                去數據頁
                              </Link>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function LiveHistoryTable({
  batches,
  catalog,
  expanded,
  onToggleError,
  onRerun,
}: {
  batches: P4Batch[];
  catalog: ReadonlyMap<string, string>;
  expanded: Record<string, boolean>;
  onToggleError: (id: string) => void;
  onRerun: (request: NonNullable<ReturnType<typeof parseP4StandardRequest>>) => void;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>內容</th>
            <th>狀態</th>
            <th>摘要</th>
            <th>建立</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {batches.map((batch) => {
            const request = parseP4StandardRequest(batch.request);
            if (!request) {
              return null;
            }
            const resolvedList = request.strategy_versions.map((strategy) =>
              resolveStrategyLabel(strategy, catalog),
            );
            const strategies = resolvedList
              .map((resolved) => resolved.label)
              .join("、");
            const versionIds = resolvedList
              .map((resolved) => resolved.versionId)
              .filter(Boolean)
              .join("、");
            return (
              <tr
                key={batch.batch_id}
                data-testid={`live-history-${batch.batch_id}`}
              >
                <td>
                  <div data-testid={`live-history-names-${batch.batch_id}`}>
                    {strategies}
                  </div>
                  <div className="utc-hint">
                    {request.symbols.join("、")}
                    {versionIds ? ` · ${versionIds}` : ""}
                  </div>
                  <div className="utc-hint">
                    由 {formatLocalAndUtc(request.range_start)}
                  </div>
                  <div className="utc-hint">
                    到 {formatLocalAndUtc(request.range_end)}
                  </div>
                </td>
                <td>{jobStatusLabel(batch.status)}</td>
                <td className="state-msg">
                  完成 {batch.summary.completed}/{batch.summary.total}
                  {batch.summary.failed
                    ? ` · 失敗 ${batch.summary.failed}`
                    : ""}
                  {batch.summary.cancelled
                    ? ` · 已取消 ${batch.summary.cancelled}`
                    : ""}
                </td>
                <td className="table__mono">
                  {formatLocalAndUtc(batch.created_at)}
                </td>
                <td>
                  <div className="form-actions">
                    {batch.jobs
                      .filter((job) => job.status === "completed")
                      .slice(0, 1)
                      .map((job) => (
                        <Link
                          key={job.run_id}
                          className="btn"
                          to={`/results/${job.run_id}`}
                        >
                          看結果
                        </Link>
                      ))}
                    <button
                      type="button"
                      className="btn"
                      data-testid={`live-rerun-${batch.batch_id}`}
                      onClick={() => {
                        onRerun(request);
                      }}
                    >
                      再跑一次
                    </button>
                  </div>
                  {batch.jobs.some((job) => job.status === "failed") ? (
                    <FailedJobs
                      jobs={batch.jobs.filter(
                        (job) => job.status === "failed",
                      )}
                      expanded={expanded}
                      onToggle={onToggleError}
                    />
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProgressFixture({
  session,
  open,
  nowMs,
  sectionRef,
  onToggle,
  onCancelQueued,
}: {
  session: BacktestSession;
  open: boolean;
  nowMs: number;
  sectionRef: RefObject<HTMLElement | null>;
  onToggle: () => void;
  onCancelQueued: () => void;
}) {
  const counts = countProgress(session.units);
  const hasQueued = counts.queued > 0;
  const startedMs = session.startedAt
    ? new Date(session.startedAt).getTime()
    : null;
  const elapsedSec =
    startedMs != null ? Math.max(0, Math.floor((nowMs - startedMs) / 1000)) : 0;
  const terminal = counts.completed + counts.failed + counts.cancelled;
  const remaining = counts.total - terminal;
  const etaSec =
    terminal > 0 && remaining > 0
      ? Math.floor((elapsedSec * remaining) / terminal)
      : null;

  return (
    <section
      className="panel"
      role="region"
      aria-label="正在回測"
      ref={sectionRef as RefObject<HTMLDivElement>}
      data-testid="progress-region"
    >
      <h2 className="panel__title">正在回測</h2>
      <p className="state-msg" data-testid="progress-summary">
        {formatProgressSummary(counts)}
      </p>
      <p className="utc-hint" data-testid="progress-timing">
        開始：
        {session.startedAt
          ? session.startedAt.replace("T", " ").replace("Z", " UTC")
          : "—"}
        {" · "}
        已用：{formatElapsed(elapsedSec)}
        {etaSec != null ? ` · 估計剩餘：${formatElapsed(etaSec)}` : ""}
      </p>
      <div className="progress-bar" aria-hidden>
        <div
          className="progress-bar__fill"
          style={{
            width: `${counts.total ? (100 * terminal) / counts.total : 0}%`,
          }}
        />
      </div>
      <div className="form-actions form-actions--progress">
        <button type="button" className="btn btn--quiet" onClick={onToggle}>
          {open ? "收起逐項" : "展開逐項"}
        </button>
        <button
          type="button"
          className={
            hasQueued
              ? "btn btn--cancel-queued"
              : "btn btn--cancel-queued btn--cancel-queued-disabled"
          }
          disabled={!hasQueued}
          data-testid="cancel-queued"
          title={
            hasQueued ? "只取消等緊、唔動進行中" : "冇等緊嘅項目"
          }
          onClick={onCancelQueued}
        >
          取消未開始嘅回測（正在跑嗰個會跑完）
        </button>
      </div>
      {open ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>策略</th>
                <th>合約</th>
                <th>狀態</th>
                <th>交易日</th>
                <th>已處理日</th>
                <th>交易數</th>
                <th>盈虧</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {session.units.map((u) => (
                <tr
                  key={u.unitId}
                  className={
                    u.status === "running" ? "job-row--running" : undefined
                  }
                  data-status={u.status}
                  data-testid={`unit-row-${u.unitId}`}
                >
                  <td>{displayStrategyName(u.strategyName)}</td>
                  <td>{u.symbol}</td>
                  <td>{jobStatusLabel(u.status)}</td>
                  <td className="table__mono">{u.tradingDay ?? "—"}</td>
                  <td>{u.daysProcessed ?? "—"}</td>
                  <td>{u.tradeCount ?? "—"}</td>
                  <td className="table__mono">
                    {u.pnlR != null ? `${u.pnlR}R` : "—"}
                    {u.pnlUsd != null ? ` / $${u.pnlUsd}` : ""}
                  </td>
                  <td>
                    {u.status === "completed" && u.runId ? (
                      <Link
                        className="table__link"
                        to={
                          u.resultPath ??
                          `/results/${u.runId}?scenario=owner-review`
                        }
                      >
                        看結果
                      </Link>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function HistoryFixture({
  sessions,
  expanded,
  onToggleError,
  onRerun,
}: {
  sessions: BacktestSession[];
  expanded: Record<string, boolean>;
  onToggleError: (id: string) => void;
  onRerun: (snap: FormSnapshot) => void;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>內容</th>
            <th>狀態</th>
            <th>結果摘要</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => {
            const failed = s.units.filter((u) => u.status === "failed");
            const counts = countProgress(s.units);
            const label = s.units
              .map((u) => `${displayStrategyName(u.strategyName)}·${u.symbol}`)
              .slice(0, 4)
              .join("、");
            return (
              <tr key={s.sessionId} data-testid={`history-${s.sessionId}`}>
                <td>
                  {label}
                  <div className="utc-hint">
                    {s.formSnapshot.rangeStartLocal} →{" "}
                    {s.formSnapshot.rangeEndLocal}
                  </div>
                </td>
                <td data-testid={`history-status-${s.sessionId}`}>
                  {jobStatusLabel(s.status)}
                </td>
                <td className="state-msg">
                  完成 {counts.completed}/{counts.total}
                  {counts.failed ? ` · 失敗 ${counts.failed}` : ""}
                  {counts.cancelled ? ` · 已取消 ${counts.cancelled}` : ""}
                </td>
                <td>
                  <div className="form-actions">
                    {s.units
                      .filter((u) => u.status === "completed" && u.runId)
                      .slice(0, 1)
                      .map((u) => (
                        <Link
                          key={u.runId}
                          className="btn"
                          to={
                            u.resultPath ??
                            `/results/${u.runId}?scenario=owner-review`
                          }
                        >
                          看結果
                        </Link>
                      ))}
                    <button
                      type="button"
                      className="btn"
                      data-testid={`rerun-${s.sessionId}`}
                      onClick={() => {
                        onRerun(s.formSnapshot);
                      }}
                    >
                      再跑一次
                    </button>
                  </div>
                  {failed.map((u) => (
                    <div key={u.unitId}>
                      <button
                        type="button"
                        className="btn"
                        data-testid={`show-error-${u.unitId}`}
                        onClick={() => {
                          onToggleError(u.unitId);
                        }}
                      >
                        看原因 · {u.symbol}
                      </button>
                      {expanded[u.unitId] && u.errorFull ? (
                        <div className="error-expand">
                          <pre data-testid={`error-full-${u.unitId}`}>
                            {u.errorFull}
                          </pre>
                          <button
                            type="button"
                            className="btn"
                            data-testid={`copy-error-${u.unitId}`}
                            onClick={() => {
                              void navigator.clipboard.writeText(
                                u.errorFull ?? "",
                              );
                            }}
                          >
                            複製全文
                          </button>
                          <Link
                            className="btn"
                            data-testid={`data-link-${u.unitId}`}
                            to={`/data?symbol=${u.symbol}&date=${u.tradingDay ?? ""}`}
                          >
                            去數據頁
                          </Link>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FailedJobs({
  jobs,
  expanded,
  onToggle,
}: {
  jobs: P4BatchJob[];
  expanded: Record<string, boolean>;
  onToggle: (id: string) => void;
}) {
  const [copyState, setCopyState] = useState<
    Record<string, "copied" | "failed">
  >({});
  return (
    <div>
      {jobs.map((j) => (
        <div key={j.job_id}>
          <button
            type="button"
            className="btn"
            onClick={() => {
              onToggle(j.job_id);
            }}
          >
            看原因 · {j.symbol}
          </button>
          {expanded[j.job_id] ? (
            <div className="error-expand">
              <pre data-testid={`history-error-full-${j.job_id}`}>
                {j.error_full}
              </pre>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  void copyExactText(j.error_full ?? "").then((ok) => {
                    setCopyState((current) => ({
                      ...current,
                      [j.job_id]: ok ? "copied" : "failed",
                    }));
                  });
                }}
              >
                複製全文
              </button>
              <span role="status">
                {copyState[j.job_id] === "copied"
                  ? "已複製"
                  : copyState[j.job_id] === "failed"
                    ? "複製失敗，請手動選取"
                    : ""}
              </span>
              <Link
                className="btn"
                to={`/data?symbol=${encodeURIComponent(j.symbol)}&date=${encodeURIComponent(j.progress?.current_trading_date ?? "")}`}
              >
                去數據頁
              </Link>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
