import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import JSZip from "jszip";

import {
  fetchP5ConfirmedStrategies,
  fetchP5Run,
  fetchP5RunChart,
  fetchP5RunEvents,
  fetchP5RunExport,
  fetchP5RunNarrative,
  fetchP5RunPromotionDecisions,
  fetchP5RunTrades,
  postP5RunPromotionDecision,
  type P5PromotionDecisionRequest,
} from "../api/client";
import { ChartGrid } from "../components/chart/ChartGrid";
import { InfoButton } from "../components/ui/InfoButton";
import { PageHeader } from "../components/ui/PageHeader";
import { ChartShadowPanel } from "../components/chart/ChartShadowPanel";
import { NarrativePanel } from "../components/chart/NarrativePanel";
import {
  appendFixtureDecision,
  buildResultZipPayload,
  chartsWithActiveTrade,
  decisionTypeLabel,
  getFixtureDetail,
  listFixtureDecisions,
} from "../lib/results/fixtureStore";
import {
  formatOptionalNumber,
} from "../lib/results/metrics";
import { markRunRead } from "../lib/results/readState";
import {
  enrichP5ChartPanes,
  hasP5Stage2Capability,
  isP5GenerationCurrent,
  mapP5Chart,
  mapP5NearMisses,
  mapP5ResultDetail,
  mapP5Trades,
  parseP5Chart,
  parseP5Events,
  parseP5Narrative,
  parseP5PromotionDecisionHistory,
  parseP5PromotionDecisionRecord,
  parseP5ResultMain,
  parseP5ResultExport,
  parseP5StrategyLabels,
  parseP5Trades,
  shouldCommitP5DecisionHistory,
  type P5ChartTimeframe,
  type P5Narrative,
  type P5PromotionDecisionRecordWire,
  type P5ResultMain,
} from "../lib/results/normalContract";
import type {
  ChartPaneData,
  DecisionType,
  NearMiss,
  PromotionDecisionRecord,
  ResultDetail,
  TradeCausal,
} from "../lib/results/types";

function useResultsMode(): "live" | "owner-review" {
  const [params] = useSearchParams();
  return params.get("scenario") === "owner-review" ? "owner-review" : "live";
}

type ChildPhase =
  | "idle"
  | "loading"
  | "ready"
  | "empty"
  | "unavailable"
  | "error";

interface ChildState<T> {
  phase: ChildPhase;
  data: T | null;
  error: string | null;
}

type HistoryPhase =
  | "idle"
  | "loading"
  | "ready-empty"
  | "ready"
  | "error";

interface HistoryState {
  phase: HistoryPhase;
  error: string | null;
}

interface DisplayDecision extends PromotionDecisionRecord {
  requestId?: string;
}

interface NormalDecisionSnapshot {
  mode: "live";
  runId: string;
  generation: number;
  request: P5PromotionDecisionRequest;
  main: P5ResultMain;
  strategyHash: string;
  rawScorecard: unknown[];
}

function idleChild<T>(): ChildState<T> {
  return { phase: "idle", data: null, error: null };
}

function loadingChild<T>(): ChildState<T> {
  return { phase: "loading", data: null, error: null };
}

function idleCharts(): Record<
  P5ChartTimeframe,
  ChildState<ChartPaneData>
> {
  return {
    D: idleChild<ChartPaneData>(),
    "1H": idleChild<ChartPaneData>(),
    "5m": idleChild<ChartPaneData>(),
  };
}

function loadingCharts(): Record<
  P5ChartTimeframe,
  ChildState<ChartPaneData>
> {
  return {
    D: loadingChild<ChartPaneData>(),
    "1H": loadingChild<ChartPaneData>(),
    "5m": loadingChild<ChartPaneData>(),
  };
}

function abortError(error: unknown): boolean {
  return (
    error !== null &&
    typeof error === "object" &&
    "name" in error &&
    error.name === "AbortError"
  );
}

const CHART_TIMEFRAMES: P5ChartTimeframe[] = ["D", "1H", "5m"];

function displayNormalDecision(
  record: P5PromotionDecisionRecordWire,
  main: P5ResultMain,
): DisplayDecision {
  return {
    id: record.decision_id,
    requestId: record.request_id,
    type: record.decision,
    runId: record.run_id,
    strategyVersion: record.strategy.strategy_id,
    reason: record.reason,
    scorecardSnapshot: main.scorecard.map((row) => ({ ...row })),
    at: record.created_at,
  };
}

function completeHttpError(
  prefix: string,
  response: { status: number; rawText: string },
  parserError: string,
): string {
  return [
    prefix,
    `HTTP ${response.status}`,
    parserError,
    response.rawText,
  ]
    .filter((part) => part.length > 0)
    .join("\n");
}

/**
 * P5 run detail — charts, causal trades, funnel, export, decisions.
 */
export function RunDetailPage() {
  const { runId = "" } = useParams();
  const mode = useResultsMode();
  const isReview = mode === "owner-review";
  const [params] = useSearchParams();

  const [detail, setDetail] = useState<ResultDetail | null>(null);
  const [load, setLoad] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [focusTime, setFocusTime] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [expandedTrade, setExpandedTrade] = useState<number | null>(null);
  /** Active trade for per-trade levels/markers on 5m (D29); default #1. */
  const [activeTrade, setActiveTrade] = useState<number | null>(1);
  const [reason, setReason] = useState("");
  const [decisions, setDecisions] = useState<DisplayDecision[]>([]);
  const [normalMain, setNormalMain] = useState<P5ResultMain | null>(null);
  const [historyState, setHistoryState] = useState<HistoryState>({
    phase: "idle",
    error: null,
  });
  const [decisionPhase, setDecisionPhase] = useState<
    "idle" | "pending" | "unknown"
  >("idle");
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportReady, setExportReady] = useState<{
    blob: Blob;
    name: string;
    opener: string;
    url: string;
  } | null>(null);
  const [decisionNote, setDecisionNote] = useState<string | null>(null);
  const [tradesChild, setTradesChild] = useState<
    ChildState<TradeCausal[]>
  >(() => idleChild());
  const [eventsChild, setEventsChild] = useState<ChildState<NearMiss[]>>(() =>
    idleChild(),
  );
  const [narrativeChild, setNarrativeChild] = useState<
    ChildState<P5Narrative>
  >(() => idleChild());
  const [chartChildren, setChartChildren] = useState<
    Record<P5ChartTimeframe, ChildState<ChartPaneData>>
  >(() => idleCharts());
  const mountedRef = useRef(true);
  const generationRef = useRef(0);
  const identityRef = useRef({ mode, runId });
  const controllerRef = useRef<AbortController | null>(null);
  const exportReadyRef = useRef<typeof exportReady>(null);
  const exportInFlightRef = useRef<object | null>(null);
  const decisionSnapshotRef = useRef<NormalDecisionSnapshot | null>(null);
  const historyRevisionRef = useRef(0);

  // D20: return with full filter query preserved
  const backTo = (() => {
    const q = new URLSearchParams(params);
    if (isReview) {
      q.set("scenario", "owner-review");
    }
    const qs = q.toString();
    return qs ? `/results?${qs}` : "/results";
  })();

  const loadNormalHistory = useCallback(
    (
      main: P5ResultMain,
      expectedGeneration: number,
      signal: AbortSignal,
    ) => {
      if (!hasP5Stage2Capability(main)) {
        return;
      }
      const revisionAtStart = historyRevisionRef.current;
      setHistoryState({ phase: "loading", error: null });
      void fetchP5RunPromotionDecisions(main.runId, signal)
        .then((response) => {
          const current =
            mountedRef.current &&
            isP5GenerationCurrent(
              generationRef.current,
              expectedGeneration,
            ) &&
            identityRef.current.mode === "live" &&
            identityRef.current.runId === main.runId &&
            !signal.aborted;
          if (
            !current ||
            !shouldCommitP5DecisionHistory(
              revisionAtStart,
              historyRevisionRef.current,
            )
          ) {
            return;
          }
          const parsed = parseP5PromotionDecisionHistory(response, main);
          if (!parsed.ok) {
            setHistoryState({
              phase: "error",
              error: completeHttpError(
                "決定紀錄未能通過完整驗證，可以重試。",
                response,
                parsed.error,
              ),
            });
            return;
          }
          const records = parsed.value.decisions.map((record) =>
            displayNormalDecision(record, main),
          );
          setDecisions(records);
          setHistoryState({
            phase: records.length === 0 ? "ready-empty" : "ready",
            error: null,
          });
        })
        .catch((caught: unknown) => {
          const current =
            mountedRef.current &&
            isP5GenerationCurrent(
              generationRef.current,
              expectedGeneration,
            ) &&
            identityRef.current.mode === "live" &&
            identityRef.current.runId === main.runId &&
            !signal.aborted;
          if (current && !abortError(caught)) {
            setHistoryState({
              phase: "error",
              error:
                caught instanceof Error ? caught.message : String(caught),
            });
          }
        });
    },
    [],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
      const ready = exportReadyRef.current;
      if (ready?.url) {
        URL.revokeObjectURL(ready.url);
      }
      exportReadyRef.current = null;
      exportInFlightRef.current = null;
      decisionSnapshotRef.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    identityRef.current = { mode, runId };

    const oldExport = exportReadyRef.current;
    if (oldExport?.url) {
      URL.revokeObjectURL(oldExport.url);
    }
    exportReadyRef.current = null;
    exportInFlightRef.current = null;
    decisionSnapshotRef.current = null;
    historyRevisionRef.current = 0;

    setDetail(null);
    setNormalMain(null);
    setLoad("loading");
    setError(null);
    setTradesChild(idleChild());
    setEventsChild(idleChild());
    setNarrativeChild(idleChild());
    setChartChildren(idleCharts());
    setFocusTime(null);
    setHighlight(null);
    setExpandedTrade(null);
    setActiveTrade(null);
    setReason("");
    setDecisions([]);
    setHistoryState({ phase: "idle", error: null });
    setDecisionPhase("idle");
    setDecisionError(null);
    setDecisionNote(null);
    setExportOpen(false);
    setExportBusy(false);
    setExportError(null);
    setExportReady(null);

    const current = () =>
      mountedRef.current &&
      isP5GenerationCurrent(generationRef.current, generation) &&
      identityRef.current.mode === mode &&
      identityRef.current.runId === runId &&
      !controller.signal.aborted;

    if (!runId) {
      setError("結果識別碼未提供");
      setLoad("error");
      return () => {
        controller.abort();
      };
    }

    if (isReview) {
      const fixture = getFixtureDetail(runId);
      if (!fixture) {
        setError(`示範結果不存在：${runId}`);
        setLoad("error");
      } else {
        setDetail(fixture);
        setDecisions(listFixtureDecisions(runId));
        setActiveTrade(fixture.trades[0]?.tradeIndex ?? null);
        markRunRead(runId, mode);
        setLoad("ready");
      }
      return () => {
        controller.abort();
      };
    }

    void fetchP5Run(runId, controller.signal)
      .then((response) => {
        if (!current()) {
          return;
        }
        const parsed = parseP5ResultMain(response, runId);
        if (!parsed.ok) {
          setError(parsed.error);
          setLoad("error");
          return;
        }

        const main = parsed.value;
        setNormalMain(main);
        const initialDetail = mapP5ResultDetail(
          main,
          main.strategyVersion,
        );
        setDetail(initialDetail);
        setLoad("ready");
        markRunRead(runId, mode);
        setTradesChild(loadingChild());
        setEventsChild(loadingChild());
        setNarrativeChild(loadingChild());
        setChartChildren(loadingCharts());
        if (hasP5Stage2Capability(main)) {
          loadNormalHistory(main, generation, controller.signal);
        }

        const catalogRequest = fetchP5ConfirmedStrategies(controller.signal);
        const tradesRequest = fetchP5RunTrades(runId, controller.signal);
        const eventsRequest = fetchP5RunEvents(runId, controller.signal);
        const narrativeRequest = fetchP5RunNarrative(
          runId,
          controller.signal,
        );
        const chartRequests = CHART_TIMEFRAMES.map(
          (timeframe) =>
            [
              timeframe,
              fetchP5RunChart(runId, timeframe, controller.signal),
            ] as const,
        );

        void catalogRequest
          .then((catalogResponse) => {
            const labels = parseP5StrategyLabels(catalogResponse);
            if (!current() || !labels.ok) {
              return;
            }
            const label =
              labels.value.get(main.strategyVersion) ?? main.strategyVersion;
            setDetail((previous) =>
              previous?.runId === runId
                ? { ...previous, strategyLabel: label }
                : previous,
            );
          })
          .catch(() => {
            // Optional label catalog never changes main ready/error state.
          });

        void tradesRequest
          .then((tradesResponse) => {
            if (!current()) {
              return;
            }
            const child = parseP5Trades(
              tradesResponse,
              runId,
              main.contractId,
              main.metrics.trade_count,
            );
            if (!child.ok) {
              setTradesChild({
                phase: "error",
                data: null,
                error: child.error,
              });
              return;
            }
            if (child.value.kind === "unavailable") {
              setTradesChild({
                phase: "unavailable",
                data: null,
                error: "歷史 run 無結構化逐筆因果證據",
              });
              return;
            }
            const mapped = mapP5Trades(child.value.trades);
            setTradesChild({
              phase: mapped.length === 0 ? "empty" : "ready",
              data: mapped,
              error: null,
            });
            setDetail((previous) =>
              previous?.runId === runId
                ? { ...previous, trades: mapped }
                : previous,
            );
            setActiveTrade(mapped[0]?.tradeIndex ?? null);
          })
          .catch((caught: unknown) => {
            if (current() && !abortError(caught)) {
              setTradesChild({
                phase: "error",
                data: null,
                error:
                  caught instanceof Error ? caught.message : String(caught),
              });
            }
          });

        void eventsRequest
          .then((eventsResponse) => {
            if (!current()) {
              return;
            }
            const child = parseP5Events(
              eventsResponse,
              runId,
              main.metrics.trade_count,
            );
            if (!child.ok) {
              setEventsChild({
                phase: "error",
                data: null,
                error: child.error,
              });
              return;
            }
            if (child.value.kind === "unavailable") {
              setEventsChild({
                phase: "unavailable",
                data: null,
                error: "歷史 run 無結構化拒絕證據",
              });
              return;
            }
            const selected = mapP5NearMisses(child.value.rejections);
            if (!selected.supported) {
              setEventsChild({
                phase: "unavailable",
                data: null,
                error: selected.reason,
              });
              return;
            }
            const nearMisses = selected.nearMisses ?? [];
            setEventsChild({
              phase: nearMisses.length === 0 ? "empty" : "ready",
              data: nearMisses,
              error: null,
            });
            setDetail((previous) =>
              previous?.runId === runId
                ? { ...previous, nearMisses }
                : previous,
            );
          })
          .catch((caught: unknown) => {
            if (current() && !abortError(caught)) {
              setEventsChild({
                phase: "error",
                data: null,
                error:
                  caught instanceof Error ? caught.message : String(caught),
              });
            }
          });

        void narrativeRequest
          .then((narrativeResponse) => {
            if (!current()) {
              return;
            }
            const child = parseP5Narrative(narrativeResponse, runId);
            if (!child.ok) {
              setNarrativeChild({
                phase: "error",
                data: null,
                error: child.error,
              });
              return;
            }
            setNarrativeChild({
              phase: child.value.steps.length === 0 ? "empty" : "ready",
              data: child.value,
              error: null,
            });
          })
          .catch((caught: unknown) => {
            if (current() && !abortError(caught)) {
              setNarrativeChild({
                phase: "error",
                data: null,
                error:
                  caught instanceof Error ? caught.message : String(caught),
              });
            }
          });

        for (const [timeframe, chartRequest] of chartRequests) {
          void chartRequest
            .then((chartResponse) => {
              if (!current()) {
                return;
              }
              const child = parseP5Chart(
                chartResponse,
                runId,
                timeframe,
                main.contractId,
              );
              setChartChildren((previous) => ({
                ...previous,
                [timeframe]: child.ok
                  ? {
                      phase: "ready",
                      data: mapP5Chart(child.value),
                      error: null,
                    }
                  : {
                      phase: "error",
                      data: null,
                      error: child.error,
                    },
              }));
            })
            .catch((caught: unknown) => {
              if (current() && !abortError(caught)) {
                setChartChildren((previous) => ({
                  ...previous,
                  [timeframe]: {
                    phase: "error",
                    data: null,
                    error:
                      caught instanceof Error
                        ? caught.message
                        : String(caught),
                  },
                }));
              }
            });
        }
      })
      .catch((caught: unknown) => {
        if (current() && !abortError(caught)) {
          setError(caught instanceof Error ? caught.message : String(caught));
          setLoad("error");
        }
      });

    return () => {
      controller.abort();
    };
  }, [isReview, loadNormalHistory, mode, runId]);

  const trimmedReason = reason.trim();
  const reasonOk = isReview
    ? trimmedReason.length > 0
    : trimmedReason.length > 0 && trimmedReason.length <= 4_000;
  const stage2Capable =
    !isReview &&
    normalMain !== null &&
    hasP5Stage2Capability(normalMain);
  const historyReady =
    historyState.phase === "ready" ||
    historyState.phase === "ready-empty";
  const decisionControlsDisabled = isReview
    ? !reasonOk
    : !stage2Capable ||
      !historyReady ||
      !reasonOk ||
      decisionPhase !== "idle";

  const decisionSnapshotCurrent = (
    snapshot: NormalDecisionSnapshot,
  ): boolean =>
    mountedRef.current &&
    isP5GenerationCurrent(
      generationRef.current,
      snapshot.generation,
    ) &&
    identityRef.current.mode === snapshot.mode &&
    identityRef.current.runId === snapshot.runId;

  const sendNormalDecision = async (
    snapshot: NormalDecisionSnapshot,
  ): Promise<void> => {
    if (
      decisionSnapshotRef.current !== snapshot ||
      !decisionSnapshotCurrent(snapshot)
    ) {
      return;
    }
    setDecisionPhase("pending");
    setDecisionError(null);
    setDecisionNote("正在記錄決定，請稍候。");
    try {
      const response = await postP5RunPromotionDecision(
        snapshot.runId,
        snapshot.request,
        controllerRef.current?.signal,
      );
      if (
        decisionSnapshotRef.current !== snapshot ||
        !decisionSnapshotCurrent(snapshot)
      ) {
        return;
      }
      const parsed = parseP5PromotionDecisionRecord(
        response,
        snapshot.main,
        snapshot.request,
      );
      if (!parsed.ok) {
        decisionSnapshotRef.current = null;
        setDecisionPhase("idle");
        setDecisionNote(
          "決定未獲確認；理由已保留，請核對完整錯誤後再提交。",
        );
        setDecisionError(
          completeHttpError(
            "PromotionDecision 未能通過完整驗證。",
            response,
            parsed.error,
          ),
        );
        return;
      }
      historyRevisionRef.current += 1;
      const record = displayNormalDecision(
        parsed.value,
        snapshot.main,
      );
      setDecisions((previous) => {
        const duplicate = previous.some(
          (item) =>
            item.id === record.id ||
            item.requestId === record.requestId,
        );
        return duplicate ? previous : [...previous, record];
      });
      setHistoryState({ phase: "ready", error: null });
      setReason("");
      setDecisionPhase("idle");
      setDecisionError(null);
      decisionSnapshotRef.current = null;
      setDecisionNote(
        record.type === "use"
          ? `已記錄「用得」決定（${record.id}）。呢個係歷史意圖，唔會鎖死模擬盤；可隨時去模擬盤直接揀已確認策略。`
          : record.type === "return"
            ? `已記錄「打回」決定（${record.id}）。`
            : `已記錄「放棄」決定（${record.id}）。`,
      );
    } catch (caught) {
      if (
        decisionSnapshotRef.current !== snapshot ||
        !decisionSnapshotCurrent(snapshot) ||
        abortError(caught)
      ) {
        return;
      }
      setDecisionPhase("unknown");
      setDecisionNote(
        "結果未知：伺服器可能已記錄。理由同 request id 已凍結，只可用同一請求重試。",
      );
      setDecisionError(
        caught instanceof Error ? caught.message : String(caught),
      );
    }
  };

  const onDecision = (type: DecisionType) => {
    if (isReview) {
      if (!detail || !reasonOk) {
        return;
      }
      const rec = appendFixtureDecision({
        type,
        runId: detail.runId,
        strategyVersion: detail.strategyVersion,
        reason: trimmedReason,
        // deep copy so later scorecard edits cannot rewrite history
        scorecardSnapshot: detail.scorecard.map((row) => ({ ...row })),
      });
      setDecisions(listFixtureDecisions(detail.runId));
      setDecisionNote(
        type === "use"
          ? `已記錄「用得」示範決定（${rec.id}）。歷史意圖而已；模擬盤可直接揀已確認策略。`
          : type === "return"
            ? `已記錄「打回」示範決定（${rec.id}）。`
            : `已記錄「放棄」示範決定（${rec.id}）。`,
      );
      return;
    }
    if (
      !normalMain ||
      !stage2Capable ||
      !historyReady ||
      !reasonOk ||
      decisionSnapshotRef.current
    ) {
      return;
    }
    const strategyHash =
      normalMain.strategyBinding?.content_sha256 ?? "";
    const capturedMain: P5ResultMain = {
      ...normalMain,
      strategyBinding: normalMain.strategyBinding
        ? {
            ...normalMain.strategyBinding,
            universe_contracts: [
              ...normalMain.strategyBinding.universe_contracts,
            ],
            overrides: normalMain.strategyBinding.overrides.map(
              (item) => ({ ...item }),
            ),
          }
        : null,
      rawScorecard: structuredClone(normalMain.rawScorecard),
    };
    const snapshot: NormalDecisionSnapshot = {
      mode: "live",
      runId: normalMain.runId,
      generation: generationRef.current,
      request: {
        schema: "promotion_decision_request.v1",
        request_id: crypto.randomUUID(),
        decision: type,
        reason: trimmedReason,
      },
      main: capturedMain,
      strategyHash,
      rawScorecard: structuredClone(normalMain.rawScorecard),
    };
    decisionSnapshotRef.current = snapshot;
    void sendNormalDecision(snapshot);
  };

  const retryUnknownDecision = () => {
    const snapshot = decisionSnapshotRef.current;
    if (!snapshot || decisionPhase !== "unknown") {
      return;
    }
    void sendNormalDecision(snapshot);
  };

  const retryHistory = () => {
    const controller = controllerRef.current;
    if (
      !normalMain ||
      !stage2Capable ||
      historyState.phase !== "error" ||
      !controller ||
      controller.signal.aborted
    ) {
      return;
    }
    loadNormalHistory(
      normalMain,
      generationRef.current,
      controller.signal,
    );
  };

  const prepareExport = useCallback(async () => {
    if (
      !detail ||
      exportInFlightRef.current !== null ||
      (!isReview &&
        (!normalMain || !hasP5Stage2Capability(normalMain)))
    ) {
      return;
    }
    const requestToken = {};
    exportInFlightRef.current = requestToken;
    setExportBusy(true);
    setExportError(null);
    const prior = exportReadyRef.current;
    if (prior?.url) {
      URL.revokeObjectURL(prior.url);
    }
    exportReadyRef.current = null;
    setExportReady(null);
    const exportGeneration = generationRef.current;
    const exportRunId = detail.runId;
    const exportMode = isReview ? "owner-review" : "live";
    const exportCurrent = () =>
      mountedRef.current &&
      generationRef.current === exportGeneration &&
      identityRef.current.mode === exportMode &&
      identityRef.current.runId === exportRunId;
    try {
      let blob: Blob;
      let name: string;
      let opener: string;
      if (isReview) {
        const payload = buildResultZipPayload(detail.runId);
        const zip = new JSZip();
        for (const [path, body] of Object.entries(payload.files)) {
          zip.file(path, body);
        }
        blob = await zip.generateAsync({ type: "blob" });
        name = payload.zipName;
        opener = payload.opener;
      } else {
        const main = normalMain as P5ResultMain;
        const response = await fetchP5RunExport(
          exportRunId,
          controllerRef.current?.signal,
        );
        if (!exportCurrent()) {
          return;
        }
        const verified = await parseP5ResultExport(response, main);
        if (!verified.ok) {
          throw new Error(
            completeHttpError(
              "結果包未能通過完整驗證，可以重試。",
              response,
              verified.error,
            ),
          );
        }
        blob = new Blob([verified.value.arrayBuffer], {
          type: "application/zip",
        });
        name = verified.value.fileName;
        opener = [
          `請用不可變結果包 ${name} 繼續分析，四個已驗證 member：`,
          ...verified.value.members.map((member) => `- ${member}`),
        ].join("\n");
      }
      const url = URL.createObjectURL(blob);
      if (!exportCurrent()) {
        URL.revokeObjectURL(url);
        return;
      }
      const ready = {
        blob,
        name,
        opener,
        url,
      };
      exportReadyRef.current = ready;
      setExportReady(ready);
    } catch (err) {
      if (exportCurrent()) {
        setExportError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (exportInFlightRef.current === requestToken) {
        exportInFlightRef.current = null;
      }
      if (exportCurrent()) {
        setExportBusy(false);
      }
    }
  }, [detail, isReview, normalMain]);

  const strategyHref = detail
    ? `/strategies?tab=library&strategy=${encodeURIComponent(detail.strategyVersion)}`
    : "/strategies";

  const fmt = formatOptionalNumber;

  const normalChartPanes: ChartPaneData[] = [
    ...(["D", "1H"] as const).map((timeframe) => {
      const state = chartChildren[timeframe];
      return (
        state.data ?? {
          timeframe,
          roleLabel: timeframe === "D" ? "大框架" : "中框架",
          available: false,
          candles: [],
          unavailableReason:
            state.phase === "loading"
              ? `正在載入 ${timeframe} 圖表…`
              : state.error ?? `${timeframe} 圖表資料暫未提供`,
        }
      );
    }),
    {
      timeframe: "30m",
      roleLabel: "輔助",
      available: false,
      candles: [],
      unavailableReason:
        "後端尚未提供 30m 圖表（唔會用 1H／5m 冒充）。",
    },
    chartChildren["5m"].data ?? {
      timeframe: "5m",
      roleLabel: "入市",
      available: false,
      candles: [],
      unavailableReason:
        chartChildren["5m"].phase === "loading"
          ? "正在載入 5m 圖表…"
          : chartChildren["5m"].error ?? "5m 圖表資料暫未提供",
    },
  ];

  const displayCharts = isReview
    ? detail && detail.trades.length > 0
      ? chartsWithActiveTrade(detail.charts, detail.trades, activeTrade)
      : (detail?.charts ?? [])
    : enrichP5ChartPanes(
        normalChartPanes,
        detail?.trades ?? [],
        detail?.nearMisses ?? [],
        activeTrade,
      );

  return (
    <div className="detail-stack" data-results-mode={mode}>
      <Link className="back-link" to={backTo}>
        ← 返回所有結果
      </Link>

      {isReview ? (
        <p className="backtest-banner" role="status">
          <strong>Owner 驗收入口</strong> — 詳情示範；唔寫後端。
        </p>
      ) : null}

      {load === "loading" ? (
        <p className="state-msg">正在載入結果詳情…</p>
      ) : null}
      {load === "error" ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}

      {detail ? (
        <>
          <PageHeader
            title={`${detail.strategyLabel} · ${detail.contractId}`}
            actions={
              <Link className="btn btn--sm" to={strategyHref}>
                用咗策略版本
              </Link>
            }
            info={
              <>
                呢一次回測嘅完整記錄。上面五個數係總結，下面四格圖同因果面板解釋
                「點解會咁」。零成交唔一定係故障——睇因果面板搵出卡喺邊一關，先再決定改咩。
                <br />
                期間 {detail.rangeStartLocal} → {detail.rangeEndLocal}（
                {detail.timezone}）· 鎖定版本 {detail.strategyVersion}
              </>
            }
          />

          {/* The five numbers that answer "點呀" before anything else loads. */}
          <div className="metrics-grid">
            <div className="metric-card">
              <span className="metric-card__label">交易</span>
              <span className="metric-card__value">
                {detail.tradeCount === null
                  ? "—"
                  : String(detail.tradeCount)}
              </span>
              <small className="metric-card__foot">
                交易日{" "}
                {detail.tradingDays === null ||
                detail.tradingDays === undefined
                  ? "未提供"
                  : detail.tradingDays}
              </small>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">勝率</span>
              <span className="metric-card__value">
                {detail.winRate == null
                  ? "—"
                  : `${(detail.winRate * 100).toFixed(0)}%`}
              </span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">R</span>
              <span
                className={
                  (detail.netR ?? 0) >= 0
                    ? "metric-card__value metric-card__value--pos"
                    : "metric-card__value metric-card__value--neg"
                }
              >
                {fmt(detail.netR)}
              </span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">USD</span>
              <span
                className={
                  (detail.netUsd ?? 0) >= 0
                    ? "metric-card__value metric-card__value--pos"
                    : "metric-card__value metric-card__value--neg"
                }
              >
                {fmt(detail.netUsd, 0)}
              </span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">最大回撤</span>
              <span className="metric-card__value">
                {fmt(detail.maxDrawdownUsd, 0)}
              </span>
              <small className="metric-card__foot table__mono">
                {detail.runId}
              </small>
            </div>
          </div>

          <section className="panel" role="region" aria-label="四格圖表">
            <div className="panel__head">
              <h2 className="panel__title">四格圖表</h2>
              <InfoButton label="四格圖表點睇" align="end">
                同一段時間、四個唔同時間框架嘅圖並排睇。撳成交列表入面嘅一筆，四格會一齊跳去嗰一刻，方便對照當時大框架同細框架各自見到咩。
              </InfoButton>
            </div>
            <ChartGrid
              panes={displayCharts}
              focusTimeUtc={focusTime}
              highlightLabel={highlight}
            />
            {!isReview ? (
              <p className="panel__note" data-testid="volume-unavailable">
                成交量：後端未提供（唔會造零 volume）。
              </p>
            ) : null}
            {!isReview && runId ? (
              <ChartShadowPanel runId={runId} timeframe="5m" transport="p5" />
            ) : null}
          </section>

          {!isReview ? (
            <NarrativePanel
              steps={narrativeChild.data?.steps ?? []}
              note={narrativeChild.data?.note}
              loading={narrativeChild.phase === "loading"}
              error={
                narrativeChild.phase === "error"
                  ? narrativeChild.error
                  : null
              }
            />
          ) : null}

          {detail.tradeCount === 0 ? (
            <section className="panel" role="region" aria-label="零成交分析">
              <h2 className="panel__title">0 成交：漏斗同三次近失</h2>
              {detail.funnel ? (
                <div className="funnel-split" data-testid="funnel-split">
                  <div>
                    <h3>日級</h3>
                    <p>
                      {detail.funnel.dailyPass === null
                        ? "未提供"
                        : detail.funnel.dailyPass}{" "}
                      日 / 共{" "}
                      {detail.funnel.dailyTotal === null
                        ? "未提供"
                        : detail.funnel.dailyTotal}{" "}
                      個交易日
                    </p>
                  </div>
                  <div>
                    <h3>評估級</h3>
                    <p>
                      {detail.funnel.evalPass === null
                        ? "未提供"
                        : detail.funnel.evalPass}{" "}
                      次
                    </p>
                  </div>
                  <div>
                    <h3>成交</h3>
                    <p>
                      {detail.funnel.fills === null
                        ? "未提供"
                        : detail.funnel.fills}{" "}
                      筆
                    </p>
                  </div>
                </div>
              ) : (
                <p className="state-msg">漏斗資料暫未提供</p>
              )}
              {!isReview && eventsChild.phase === "loading" ? (
                <p className="state-msg" data-testid="events-loading">
                  正在載入拒絕證據…
                </p>
              ) : null}
              {!isReview && eventsChild.phase === "error" ? (
                <p
                  className="state-msg state-msg--error"
                  data-testid="events-error"
                  role="alert"
                >
                  拒絕證據載入失敗：{eventsChild.error}
                </p>
              ) : null}
              {!isReview && eventsChild.phase === "unavailable" ? (
                <p className="state-msg" data-testid="events-unavailable">
                  {eventsChild.error}
                </p>
              ) : null}
              {!isReview && eventsChild.phase === "empty" ? (
                <p className="state-msg" data-testid="events-empty">
                  拒絕證據完整，呢個 run 真係零次 signal rejection。
                </p>
              ) : null}
              {(isReview || eventsChild.phase === "ready") &&
              detail.nearMisses.length > 0 ? (
                <ul className="near-miss-list" data-testid="near-miss-list">
                  {detail.nearMisses.map((n) => (
                    <li key={n.index}>
                      <button
                        type="button"
                        className="btn"
                        data-testid={`near-miss-${n.index}`}
                        onClick={() => {
                          setFocusTime(n.focusTimeUtc);
                          setHighlight(`近失 #${n.index}`);
                        }}
                      >
                        #{n.index} · {n.timeLocal}（{n.timezone}）
                      </button>
                      <p>
                        到 {n.layerReached} · 差 {n.stepsAway} 步 · {n.missing}
                      </p>
                      <p className="utc-hint">{n.values}</p>
                    </li>
                  ))}
                </ul>
              ) : isReview ? (
                <p className="state-msg">
                  後端未提供帶時間嘅三次近失；唔會由總數捏造日期。
                </p>
              ) : null}
              {detail.funnel ? (
                <p className="state-msg">{detail.funnel.judgment}</p>
              ) : null}
              {detail.whyZero ? (
                <p className="state-msg">{detail.whyZero}</p>
              ) : null}
            </section>
          ) : detail.tradeCount === null ? (
            <section className="panel" role="region" aria-label="成交分析">
              <h2 className="panel__title">成交分析</h2>
              <p className="state-msg" data-testid="trades-missing">
                成交筆數未提供；唔會當成 0 成交。
              </p>
            </section>
          ) : (
            <section className="panel" role="region" aria-label="逐筆因果">
              <h2 className="panel__title">逐筆因果</h2>
              {!isReview && tradesChild.phase === "loading" ? (
                <p className="state-msg" data-testid="trades-loading">
                  正在載入逐筆因果證據…
                </p>
              ) : null}
              {!isReview && tradesChild.phase === "unavailable" ? (
                <p className="state-msg" data-testid="trades-unavailable">
                  歷史 run 無結構化逐筆因果證據
                </p>
              ) : null}
              {!isReview && tradesChild.phase === "error" ? (
                <p
                  className="state-msg state-msg--error"
                  data-testid="trades-error"
                  role="alert"
                >
                  逐筆因果證據載入失敗：{tradesChild.error}
                </p>
              ) : null}
              {!isReview && tradesChild.phase === "empty" ? (
                <p className="state-msg" data-testid="trades-empty">
                  逐筆證據完整，成交清單為空。
                </p>
              ) : null}
              {isReview && detail.trades.length === 0 ? (
                <p className="state-msg" data-testid="causal-missing">
                  完整逐筆因果資料暫未提供
                </p>
              ) : isReview || tradesChild.phase === "ready" ? (
                <ul className="trade-causal-list">
                  {detail.trades.map((t) => (
                    <li key={t.tradeIndex}>
                      <button
                        type="button"
                        className="btn"
                        data-testid={`trade-${t.tradeIndex}`}
                        onClick={() => {
                          setExpandedTrade(
                            expandedTrade === t.tradeIndex
                              ? null
                              : t.tradeIndex,
                          );
                          setActiveTrade(t.tradeIndex);
                          setFocusTime(t.focusTimeUtc);
                          setHighlight(`#${t.tradeIndex}`);
                        }}
                      >
                        #{t.tradeIndex} · {t.side === "long" ? "買" : "賣"} ·{" "}
                        {t.entryTimeLocal} ·{" "}
                        {t.rMultiple === null
                          ? "R 未提供"
                          : `${t.rMultiple}R`}{" "}
                        / ${t.pnlUsd}
                      </button>
                      {expandedTrade === t.tradeIndex ? (
                        <div
                          className="error-expand"
                          data-testid={`trade-detail-${t.tradeIndex}`}
                        >
                          <p>
                            <strong>1. 為何入市</strong>
                            <br />D：{t.whyEntry.d}
                            <br />1H：{t.whyEntry.h1}
                            <br />5m：{t.whyEntry.m5}
                          </p>
                          <p>
                            <strong>2. 止損</strong> — {t.stop.reason}（錨{" "}
                            {t.stop.anchor} · {t.stop.offsetTicks} tick · 價{" "}
                            {t.stop.finalPrice}）
                          </p>
                          <p>
                            <strong>3. 離場</strong> — {t.exit.kind}；
                            {t.exit.whichFirst}；{t.exit.detail}
                          </p>
                          <p>
                            <strong>4. 保守假設</strong> — {t.conservative}
                          </p>
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          )}

          <section className="panel" role="region" aria-label="記分卡">
            <h2 className="panel__title">記分卡</h2>
            <ul className="scorecard-list" data-testid="scorecard">
              {detail.scorecard.map((s) => (
                <li key={s.dim}>
                  <strong>{s.label}</strong> · {s.statusLabel}
                  <div className="utc-hint">{s.detail}</div>
                </li>
              ))}
            </ul>
            {detail.warnings.length > 0 ? (
              <div>
                <h3 className="panel__title">警告</h3>
                <ul>
                  {detail.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          <section className="panel" role="region" aria-label="匯出結果">
            <h2 className="panel__title">匯出結果俾 Terminal</h2>
            {isReview || stage2Capable ? (
              <>
                <button
                  type="button"
                  className="btn btn--primary"
                  data-testid="open-export"
                  onClick={() => {
                    setExportOpen(true);
                    void prepareExport();
                  }}
                >
                  匯出結果俾 Terminal
                </button>
                {exportOpen ? (
                  <div className="export-dialog" role="dialog">
                    <h3>結果包</h3>
                    {exportBusy ? (
                      <p className="state-msg">準備中…</p>
                    ) : null}
                    {exportError ? (
                      <>
                        <pre className="error-expand">{exportError}</pre>
                        {!isReview && !exportBusy ? (
                          <button
                            type="button"
                            className="btn"
                            data-testid="retry-export"
                            onClick={() => {
                              void prepareExport();
                            }}
                          >
                            重試下載結果包
                          </button>
                        ) : null}
                      </>
                    ) : null}
                    {exportReady ? (
                      <>
                        <p className="state-msg">
                          已準備 {exportReady.name}
                        </p>
                        <div className="form-actions">
                          <a
                            className="btn btn--primary"
                            data-testid="download-zip"
                            href={exportReady.url}
                            download={exportReady.name}
                          >
                            下載結果包
                          </a>
                          <button
                            type="button"
                            className="btn"
                            data-testid="copy-opener"
                            onClick={() => {
                              void navigator.clipboard.writeText(
                                exportReady.opener,
                              );
                            }}
                          >
                            複製 Terminal 開場白
                          </button>
                          <button
                            type="button"
                            className="btn"
                            onClick={() => {
                              setExportOpen(false);
                            }}
                          >
                            關閉
                          </button>
                        </div>
                        <pre className="error-expand">{exportReady.opener}</pre>
                      </>
                    ) : null}
                  </div>
                ) : null}
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="btn"
                  disabled
                  title="歷史結果缺完整不可變證據"
                >
                  匯出結果俾 Terminal
                </button>
                <p className="panel__note">
                  歷史結果缺完整不可變證據，未能安全匯出；唔會用零散 API
                  估包或顯示假成功。
                </p>
              </>
            )}
          </section>

          <section className="panel" role="region" aria-label="你嘅決定">
            <h2 className="panel__title">你嘅決定</h2>
            <label className="field">
              <span>一句理由（必填）</span>
              <textarea
                className="inp"
                rows={3}
                data-testid="decision-reason"
                value={reason}
                onChange={(e) => {
                  setReason(e.target.value);
                }}
                disabled={
                  !isReview &&
                  (!stage2Capable || decisionPhase !== "idle")
                }
              />
            </label>
            {!isReview && reason.trim().length > 4_000 ? (
              <p className="panel__note">
                理由最多 4000 字；請縮短後再提交。
              </p>
            ) : null}
            <div className="form-actions">
              <button
                type="button"
                className="btn btn--primary"
                data-testid="decision-use"
                disabled={decisionControlsDisabled}
                title={
                  isReview
                    ? undefined
                    : !stage2Capable
                      ? "歷史結果缺完整不可變證據"
                      : !historyReady
                        ? "先完成決定紀錄驗證"
                        : undefined
                }
                onClick={() => {
                  onDecision("use");
                }}
              >
                {isReview ? "✓ 用得，去模擬盤" : "✓ 用得，記錄決定"}
              </button>
              <button
                type="button"
                className="btn"
                data-testid="decision-return"
                disabled={decisionControlsDisabled}
                onClick={() => {
                  onDecision("return");
                }}
              >
                ↩ 打回，返 Terminal 改
              </button>
              <button
                type="button"
                className="btn"
                data-testid="decision-abandon"
                disabled={decisionControlsDisabled}
                onClick={() => {
                  onDecision("abandon");
                }}
              >
                ✕ 放棄呢個方向
              </button>
            </div>
            {!isReview ? (
              <>
                {!stage2Capable ? (
                  <p className="panel__note">
                    歷史結果缺完整不可變證據，未能讀取或記錄決定。
                  </p>
                ) : null}
                {historyState.phase === "loading" ? (
                  <p className="state-msg" data-testid="decision-history-loading">
                    正在核對既有決定紀錄…
                  </p>
                ) : null}
                {historyState.phase === "ready-empty" ? (
                  <p className="state-msg" data-testid="decision-history-empty">
                    尚未有決定。
                  </p>
                ) : null}
                {historyState.phase === "error" ? (
                  <>
                    <p className="panel__note">
                      決定紀錄未能確認；重試成功前不會接受新決定。
                    </p>
                    <button
                      type="button"
                      className="btn"
                      data-testid="retry-decision-history"
                      onClick={retryHistory}
                    >
                      重試核對決定紀錄
                    </button>
                    {historyState.error ? (
                      <pre className="error-expand">
                        {historyState.error}
                      </pre>
                    ) : null}
                  </>
                ) : null}
              </>
            ) : null}
            {decisionNote ? (
              <p className="state-msg" data-testid="decision-note">
                {decisionNote}
              </p>
            ) : null}
            {!isReview && decisionPhase === "unknown" ? (
              <button
                type="button"
                className="btn"
                data-testid="retry-unknown-decision"
                onClick={retryUnknownDecision}
              >
                用同一 request id 重試
              </button>
            ) : null}
            {!isReview && decisionError ? (
              <pre className="error-expand" data-testid="decision-error">
                {decisionError}
              </pre>
            ) : null}
            {decisions.length > 0 ? (
              <ul data-testid="decision-history">
                {decisions.map((d) => (
                  <li key={d.id}>
                    {decisionTypeLabel(d.type)} · {d.reason} · {d.at}
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        </>
      ) : null}
    </div>
  );
}
