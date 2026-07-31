import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ChangeEvent } from "react";

import {
  archiveLiveInsight,
  fetchLiveInsightArchives,
  fetchLiveInsightDetail,
  fetchLiveInsights,
  importLiveInsight,
  InsightTransportError,
  restoreLiveInsight,
  type InsightHttpResult,
} from "../../api/client";
import { assetClassLabel } from "../../lib/catalog/types";
import {
  type InsightRecord,
  type InsightStatus,
  deleteInsight,
  importInsight,
  listInsights,
  parseInsightYaml,
  updateInsightStatus,
  validateInsightCandidate,
} from "../../lib/insightStore";
import {
  insightArchiveIdentityKey,
  insightIdentityKey,
  parseInsightActiveList,
  parseInsightArchive,
  parseInsightArchiveList,
  parseInsightDetail,
  parseInsightImport,
  parseInsightRestore,
  type InsightArchiveActionSnapshot,
  type InsightContractResult,
  type InsightRestoreActionSnapshot,
  type LiveInsightArchive,
  type LiveInsightArchiveSummary,
  type LiveInsightDetail,
  type LiveInsightRestore,
  type LiveInsightSummary,
} from "../../lib/insights/liveContract";
import { useWorkbench } from "../../lib/p2/WorkbenchContext";
import { validateDraftAgainstCatalog } from "../../lib/sketch/catalogExportGate";
import {
  applyChartChange,
  canExportSketch,
  draftGapSummary,
  duplicateDraftAsNew,
  exportBlockingReasons,
  exportSketchPackage,
  listDrafts,
  saveDraft,
  selectDraft,
  selectInstrument,
  createEmptyDraft,
  readSketchStore,
  writeSketchStore,
} from "../../lib/sketch/store";
import type {
  SketchDraft,
  SketchPackagePreview,
} from "../../lib/sketch/types";
import { useOwnerSketchLoad } from "../../lib/sketch/useOwnerSketchLoad";
import {
  sketchRefFromLoadState,
  type SketchRefContext,
} from "../../lib/strategy/universe";
import type { SketchLineage } from "../../lib/strategyYaml";
import type { InsightImportContext } from "../../lib/insightStore";
import { ExportDialog } from "./ExportDialog";
import { InstrumentPicker } from "./InstrumentPicker";
import { SketchCell } from "./SketchCell";

/** Build import context from origin-aware sketch ref (D17). */
// eslint-disable-next-line react-refresh/only-export-components -- pure helper for tests + import path
export function insightCtxFromSketchRef(
  sketchRef: SketchRefContext,
  catalogRows: import("../../lib/catalog/types").InstrumentCatalogRow[] | null,
  catalogReady: boolean,
): InsightImportContext {
  const base: InsightImportContext = {
    catalogRows,
    catalogReady,
  };
  switch (sketchRef.kind) {
    case "loading":
      return { ...base, sketchLoading: true };
    case "error":
      return { ...base, sketchError: sketchRef.message };
    case "legacy_incomplete":
      return { ...base, sketchLegacyIncomplete: sketchRef.reason };
    case "lineage_incomplete":
      return { ...base, sketchError: sketchRef.reason };
    case "missing":
      return { ...base, sketchMissing: true };
    case "ready":
      return {
        ...base,
        sketchMatch: {
          instrument: sketchRef.instrument,
          assetClass: sketchRef.assetClass,
        },
      };
    default:
      return base;
  }
}

const STATUSES: InsightStatus[] = [
  "unverified",
  "recording",
  "supported",
  "rejected",
];

export const INSIGHT_ARCHIVE_GRACE_MS = 5000;

type LiveResourceStatus = "loading" | "ready-empty" | "ready" | "error";

interface LiveProblem {
  headline: string;
  technical: string;
}

interface LiveResource<T> {
  status: LiveResourceStatus;
  data: T;
  problem: LiveProblem | null;
}

interface LiveArchiveSnapshot extends InsightArchiveActionSnapshot {
  mode: "normal";
  generation: number;
  fixtureEpoch: number;
  versions: number[];
}

interface LiveRestoreSnapshot extends InsightRestoreActionSnapshot {
  mode: "normal";
  generation: number;
  fixtureEpoch: number;
}

interface PendingArchiveAction {
  stage: "grace" | "request";
  snapshot: LiveArchiveSnapshot;
}

interface LiveSelection {
  key: string;
  summary: LiveInsightSummary;
  status: "loading" | "ready" | "error";
  detail: LiveInsightDetail | null;
  problem: LiveProblem | null;
}

function emptyResource<T>(data: T): LiveResource<T> {
  return { status: "loading", data, problem: null };
}

function isAbortError(error: unknown): boolean {
  return (
    error !== null &&
    typeof error === "object" &&
    "name" in error &&
    error.name === "AbortError"
  );
}

function httpProblem(
  action: string,
  result: InsightHttpResult,
  outcome: string,
  contractError?: string,
): LiveProblem {
  const raw = result.rawText || "(empty response body)";
  const contract = contractError ? `\nContract: ${contractError}` : "";
  return {
    headline: `${action}${outcome}`,
    technical:
      `${result.method} ${result.path}\nHTTP ${result.status}` +
      `${contract}\n${raw}`,
  };
}

function transportProblem(
  action: string,
  error: InsightTransportError,
  outcome: string,
): LiveProblem {
  return {
    headline: `${action}${outcome}`,
    technical: `${error.method} ${error.path}\n${error.message}`,
  };
}

function parseStrictHttp<T>(
  result: InsightHttpResult,
  parser: () => InsightContractResult<T>,
): InsightContractResult<T> {
  if (result.status !== 200 || !result.ok) {
    return {
      ok: false,
      error: `HTTP ${result.status}; strict success requires HTTP 200`,
    };
  }
  if (!result.jsonParsed) {
    return { ok: false, error: "response body is not JSON" };
  }
  return parser();
}

function sortArchiveSummaries(
  rows: LiveInsightArchiveSummary[],
): LiveInsightArchiveSummary[] {
  return [...rows].sort((left, right) => {
    if (left.deleted_at !== right.deleted_at) {
      return left.deleted_at > right.deleted_at ? -1 : 1;
    }
    return (
      left.origin.localeCompare(right.origin) ||
      left.insight_id.localeCompare(right.insight_id) ||
      left.archive_id.localeCompare(right.archive_id)
    );
  });
}

function LiveProblemPanel({
  problem,
  testId,
}: {
  problem: LiveProblem;
  testId?: string;
}) {
  return (
    <div data-testid={testId}>
      <p className="state-msg state-msg--error" role="alert">
        {problem.headline}
      </p>
      <details>
        <summary>技術詳情</summary>
        <pre className="code-block">{problem.technical}</pre>
      </details>
    </div>
  );
}

interface InsightTabProps {
  fixtureEpoch?: number;
}

export function InsightTab({ fixtureEpoch = 0 }: InsightTabProps) {
  const { storage, catalog, ownerReview, sketchDetailLookup } =
    useWorkbench();
  const [draft, setDraft] = useState<SketchDraft | null>(null);
  const [drafts, setDrafts] = useState<SketchDraft[]>([]);
  const [exportPreview, setExportPreview] =
    useState<SketchPackagePreview | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importText, setImportText] = useState("");
  const [ownerInsights, setOwnerInsights] = useState<InsightRecord[]>([]);
  const [ownerSelected, setOwnerSelected] = useState<InsightRecord | null>(
    null,
  );
  const [activeResource, setActiveResource] = useState<
    LiveResource<LiveInsightSummary[]>
  >(emptyResource([]));
  const [archiveResource, setArchiveResource] = useState<
    LiveResource<LiveInsightArchiveSummary[]>
  >(emptyResource([]));
  const [liveSelection, setLiveSelection] = useState<LiveSelection | null>(
    null,
  );
  const [liveMutationProblem, setLiveMutationProblem] =
    useState<LiveProblem | null>(null);
  const [importPending, setImportPending] = useState(false);
  const [pendingArchives, setPendingArchives] = useState<
    Record<string, PendingArchiveAction>
  >({});
  const [pendingRestores, setPendingRestores] = useState<
    Record<string, LiveRestoreSnapshot>
  >({});

  const mountedRef = useRef(false);
  const liveModeRef = useRef<"normal" | "owner-review">(
    ownerReview ? "owner-review" : "normal",
  );
  const liveGenerationRef = useRef(0);
  const liveFixtureEpochRef = useRef(fixtureEpoch);
  const listRevisionRef = useRef(0);
  const detailRevisionRef = useRef(0);
  const activeListControllerRef = useRef<AbortController | null>(null);
  const archiveListControllerRef = useRef<AbortController | null>(null);
  const detailControllerRef = useRef<AbortController | null>(null);
  const importControllerRef = useRef<AbortController | null>(null);
  const archiveTimersRef = useRef<Map<string, number>>(new Map());
  const archiveActionsRef = useRef<Map<string, PendingArchiveAction>>(
    new Map(),
  );
  const restoreActionsRef = useRef<Map<string, LiveRestoreSnapshot>>(
    new Map(),
  );
  const mutationControllersRef = useRef<Map<string, AbortController>>(
    new Map(),
  );
  const selectedLiveKeyRef = useRef<string | null>(null);

  const refreshOwnerInsights = useCallback(() => {
    if (ownerReview) {
      setOwnerInsights(listInsights(storage));
    }
  }, [ownerReview, storage]);

  const refreshDrafts = useCallback(() => {
    setDrafts(listDrafts(storage).filter((d) => d.kind === "insight"));
  }, [storage]);

  const isLiveSnapshotCurrent = useCallback(
    (generation: number, expectedFixtureEpoch: number): boolean =>
      mountedRef.current &&
      liveModeRef.current === "normal" &&
      liveGenerationRef.current === generation &&
      liveFixtureEpochRef.current === expectedFixtureEpoch,
    [],
  );

  const publishArchiveActions = useCallback(() => {
    setPendingArchives(Object.fromEntries(archiveActionsRef.current));
  }, []);

  const publishRestoreActions = useCallback(() => {
    setPendingRestores(Object.fromEntries(restoreActionsRef.current));
  }, []);

  const invalidateLiveLists = useCallback(() => {
    listRevisionRef.current += 1;
    activeListControllerRef.current?.abort();
    archiveListControllerRef.current?.abort();
    activeListControllerRef.current = null;
    archiveListControllerRef.current = null;
  }, []);

  const refreshLive = useCallback(
    (
      generation: number,
      expectedFixtureEpoch: number,
      background = false,
    ) => {
      if (!isLiveSnapshotCurrent(generation, expectedFixtureEpoch)) {
        return;
      }
      const revision = listRevisionRef.current + 1;
      listRevisionRef.current = revision;
      activeListControllerRef.current?.abort();
      archiveListControllerRef.current?.abort();
      const activeController = new AbortController();
      const archiveController = new AbortController();
      activeListControllerRef.current = activeController;
      archiveListControllerRef.current = archiveController;
      if (!background) {
        setActiveResource(emptyResource([]));
        setArchiveResource(emptyResource([]));
      }

      void (async () => {
        try {
          const result = await fetchLiveInsights(activeController.signal);
          if (
            !isLiveSnapshotCurrent(generation, expectedFixtureEpoch) ||
            listRevisionRef.current !== revision ||
            activeListControllerRef.current !== activeController
          ) {
            return;
          }
          const parsed = parseStrictHttp(result, () =>
            parseInsightActiveList(result.body),
          );
          if (!parsed.ok) {
            setActiveResource((current) => ({
              status: "error",
              data: background ? current.data : [],
              problem: httpProblem(
                "未能讀取洞察庫；",
                result,
                "唔會用本機舊資料代替。請稍後重試。",
                parsed.error,
              ),
            }));
            return;
          }
          setActiveResource({
            status:
              parsed.value.insights.length === 0 ? "ready-empty" : "ready",
            data: parsed.value.insights,
            problem: null,
          });
        } catch (caught) {
          if (
            isAbortError(caught) ||
            !isLiveSnapshotCurrent(generation, expectedFixtureEpoch) ||
            listRevisionRef.current !== revision ||
            activeListControllerRef.current !== activeController
          ) {
            return;
          }
          const problem =
            caught instanceof InsightTransportError
              ? transportProblem(
                  "未能讀取洞察庫；",
                  caught,
                  "唔會用本機舊資料代替。請檢查連線後再試。",
                )
              : {
                  headline:
                    "未能讀取洞察庫；唔會用本機舊資料代替。請檢查連線後再試。",
                  technical: String(caught),
                };
          setActiveResource((current) => ({
            status: "error",
            data: background ? current.data : [],
            problem,
          }));
        }
      })();

      void (async () => {
        try {
          const result = await fetchLiveInsightArchives(
            archiveController.signal,
          );
          if (
            !isLiveSnapshotCurrent(generation, expectedFixtureEpoch) ||
            listRevisionRef.current !== revision ||
            archiveListControllerRef.current !== archiveController
          ) {
            return;
          }
          const parsed = parseStrictHttp(result, () =>
            parseInsightArchiveList(result.body),
          );
          if (!parsed.ok) {
            setArchiveResource((current) => ({
              status: "error",
              data: background ? current.data : [],
              problem: httpProblem(
                "未能讀取已歸檔洞察；",
                result,
                "唔會猜測歸檔狀態。請稍後重試。",
                parsed.error,
              ),
            }));
            return;
          }
          setArchiveResource({
            status:
              parsed.value.archives.length === 0 ? "ready-empty" : "ready",
            data: parsed.value.archives,
            problem: null,
          });
        } catch (caught) {
          if (
            isAbortError(caught) ||
            !isLiveSnapshotCurrent(generation, expectedFixtureEpoch) ||
            listRevisionRef.current !== revision ||
            archiveListControllerRef.current !== archiveController
          ) {
            return;
          }
          const problem =
            caught instanceof InsightTransportError
              ? transportProblem(
                  "未能讀取已歸檔洞察；",
                  caught,
                  "唔會猜測歸檔狀態。請檢查連線後再試。",
                )
              : {
                  headline:
                    "未能讀取已歸檔洞察；唔會猜測歸檔狀態。請檢查連線後再試。",
                  technical: String(caught),
                };
          setArchiveResource((current) => ({
            status: "error",
            data: background ? current.data : [],
            problem,
          }));
        }
      })();
    },
    [isLiveSnapshotCurrent],
  );

  const cancelAllLiveWork = useCallback(() => {
    listRevisionRef.current += 1;
    detailRevisionRef.current += 1;
    activeListControllerRef.current?.abort();
    archiveListControllerRef.current?.abort();
    detailControllerRef.current?.abort();
    importControllerRef.current?.abort();
    activeListControllerRef.current = null;
    archiveListControllerRef.current = null;
    detailControllerRef.current = null;
    importControllerRef.current = null;
    for (const controller of mutationControllersRef.current.values()) {
      controller.abort();
    }
    mutationControllersRef.current.clear();
    for (const timer of archiveTimersRef.current.values()) {
      window.clearTimeout(timer);
    }
    archiveTimersRef.current.clear();
    archiveActionsRef.current.clear();
    restoreActionsRef.current.clear();
    selectedLiveKeyRef.current = null;
  }, []);

  useEffect(() => {
    const snap = readSketchStore(storage);
    const existing = snap.drafts.find(
      (d) => d.sketchId === snap.activeId && d.kind === "insight",
    );
    if (existing) {
      setDraft(existing);
    } else {
      const insightIds = snap.drafts.map((d) => d.sketchId);
      const created = createEmptyDraft(insightIds, new Date(), "insight");
      writeSketchStore(
        {
          ...snap,
          drafts: [...snap.drafts, created],
          activeId: created.sketchId,
        },
        storage,
      );
      setDraft(created);
    }
    refreshDrafts();
    if (ownerReview) {
      refreshOwnerInsights();
    } else {
      setOwnerInsights([]);
    }
    setExportPreview(null);
    setError(null);
    setNotice(null);
    setImportText("");
    setOwnerSelected(null);
  }, [
    storage,
    fixtureEpoch,
    ownerReview,
    refreshDrafts,
    refreshOwnerInsights,
  ]);

  useEffect(() => {
    mountedRef.current = true;
    cancelAllLiveWork();
    liveGenerationRef.current += 1;
    const generation = liveGenerationRef.current;
    liveModeRef.current = ownerReview ? "owner-review" : "normal";
    liveFixtureEpochRef.current = fixtureEpoch;
    setLiveMutationProblem(null);
    setLiveSelection(null);
    setPendingArchives({});
    setPendingRestores({});
    setImportPending(false);
    if (ownerReview) {
      setActiveResource({
        status: "ready-empty",
        data: [],
        problem: null,
      });
      setArchiveResource({
        status: "ready-empty",
        data: [],
        problem: null,
      });
    } else {
      refreshLive(generation, fixtureEpoch);
    }

    return () => {
      if (liveGenerationRef.current === generation) {
        mountedRef.current = false;
      }
      cancelAllLiveWork();
    };
  }, [
    cancelAllLiveWork,
    fixtureEpoch,
    ownerReview,
    refreshLive,
    storage,
  ]);

  /** Parse candidate lineage for origin-aware sketch load (D17). */
  const importLineage = useMemo((): SketchLineage | null => {
    if (!importText.trim()) {
      return null;
    }
    try {
      const c = parseInsightYaml(importText);
      return {
        status: "complete",
        origin: c.based_on_sketch_origin,
        sketchId: c.based_on_sketch,
      };
    } catch {
      // Incomplete parse — load stays idle; click will surface parse error
      return null;
    }
  }, [importText]);

  const sketchLoad = useOwnerSketchLoad(
    importLineage,
    Boolean(importText.trim() && importLineage),
    sketchDetailLookup,
  );
  const sketchRef = useMemo(
    () => sketchRefFromLoadState(sketchLoad),
    [sketchLoad],
  );

  const blockers = useMemo(() => {
    if (!draft) return [];
    return [
      ...exportBlockingReasons(draft),
      ...validateDraftAgainstCatalog(draft, catalog),
    ];
  }, [draft, catalog]);
  const canExport =
    draft &&
    canExportSketch(draft) &&
    validateDraftAgainstCatalog(draft, catalog).length === 0;

  const importDisabled =
    !importText.trim() ||
    catalog.status !== "ready" ||
    sketchLoad.kind === "loading" ||
    importPending;

  const legacyIncomplete = Boolean(
    draft &&
      draft.instrumentLegacy &&
      !draft.exported &&
      (!draft.instrument || !draft.assetClass),
  );

  const openLiveDetail = (summary: LiveInsightSummary) => {
    if (ownerReview || liveModeRef.current !== "normal") {
      return;
    }
    const generation = liveGenerationRef.current;
    const expectedFixtureEpoch = liveFixtureEpochRef.current;
    if (!isLiveSnapshotCurrent(generation, expectedFixtureEpoch)) {
      return;
    }
    const key = insightIdentityKey(summary.origin, summary.insight_id);
    selectedLiveKeyRef.current = key;
    detailRevisionRef.current += 1;
    const revision = detailRevisionRef.current;
    detailControllerRef.current?.abort();
    const controller = new AbortController();
    detailControllerRef.current = controller;
    setLiveSelection({
      key,
      summary,
      status: "loading",
      detail: null,
      problem: null,
    });

    void (async () => {
      try {
        const result = await fetchLiveInsightDetail(
          summary.origin,
          summary.insight_id,
          controller.signal,
        );
        if (
          !isLiveSnapshotCurrent(generation, expectedFixtureEpoch) ||
          detailRevisionRef.current !== revision ||
          detailControllerRef.current !== controller ||
          selectedLiveKeyRef.current !== key
        ) {
          return;
        }
        const parsed = parseStrictHttp(result, () =>
          parseInsightDetail(
            result.body,
            { origin: summary.origin, insight_id: summary.insight_id },
            summary,
          ),
        );
        if (!parsed.ok) {
          setLiveSelection({
            key,
            summary,
            status: "error",
            detail: null,
            problem: httpProblem(
              "未能核實洞察詳情；",
              result,
              "列表冇被改動。請重試。",
              parsed.error,
            ),
          });
          return;
        }
        setLiveSelection({
          key,
          summary,
          status: "ready",
          detail: parsed.value,
          problem: null,
        });
      } catch (caught) {
        if (
          isAbortError(caught) ||
          !isLiveSnapshotCurrent(generation, expectedFixtureEpoch) ||
          detailRevisionRef.current !== revision ||
          detailControllerRef.current !== controller ||
          selectedLiveKeyRef.current !== key
        ) {
          return;
        }
        setLiveSelection({
          key,
          summary,
          status: "error",
          detail: null,
          problem:
            caught instanceof InsightTransportError
              ? transportProblem(
                  "未能讀取洞察詳情；",
                  caught,
                  "列表冇被改動。請檢查連線後重試。",
                )
              : {
                  headline:
                    "未能讀取洞察詳情；列表冇被改動。請檢查連線後重試。",
                  technical: String(caught),
                },
        });
      }
    })();
  };

  const handleImportClick = () => {
    let candidate: InsightRecord;
    let context: InsightImportContext;
    try {
      candidate = parseInsightYaml(importText);
      context = insightCtxFromSketchRef(
        sketchRef,
        catalog.status === "ready" ? catalog.rows : null,
        catalog.status === "ready",
      );
      const errors = validateInsightCandidate(candidate, context);
      if (errors.length > 0) {
        setError(errors.join(" · "));
        setNotice(null);
        return;
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setNotice(null);
      return;
    }

    if (ownerReview) {
      try {
        const record = importInsight(importText, storage, context);
        refreshOwnerInsights();
        setOwnerSelected(record);
        setNotice(
          `已入洞察庫 ${record.insight_id} v${record.version}`,
        );
        setError(null);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
        setNotice(null);
      }
      return;
    }
    if (importControllerRef.current) {
      return;
    }
    const generation = liveGenerationRef.current;
    const expectedFixtureEpoch = liveFixtureEpochRef.current;
    if (!isLiveSnapshotCurrent(generation, expectedFixtureEpoch)) {
      return;
    }
    const sourceSnapshot = importText;
    const candidateSnapshot = {
      origin: candidate.origin,
      insight_id: candidate.insight_id,
      version: candidate.version,
    };
    invalidateLiveLists();
    const controller = new AbortController();
    importControllerRef.current = controller;
    setImportPending(true);
    setLiveMutationProblem(null);
    setError(null);
    setNotice(null);

    void (async () => {
      try {
        const result = await importLiveInsight(
          sourceSnapshot,
          controller.signal,
        );
        if (
          !isLiveSnapshotCurrent(generation, expectedFixtureEpoch) ||
          importControllerRef.current !== controller
        ) {
          return;
        }
        const parsed = parseStrictHttp(result, () =>
          parseInsightImport(result.body, candidateSnapshot),
        );
        if (!parsed.ok) {
          const uncertain = result.status === 200;
          setLiveMutationProblem(
            httpProblem(
              "",
              result,
              uncertain
                ? "入庫結果未能核實；原文已保留，系統會重讀兩邊清單。請先核對再試。"
                : "未能入庫；原文已保留。請按完整錯誤修正後再試。",
              parsed.error,
            ),
          );
          if (uncertain) {
            refreshLive(generation, expectedFixtureEpoch, true);
          }
          return;
        }
        setNotice(
          parsed.value.deduplicated
            ? `洞察庫已有相同版本 ${parsed.value.insight.insight_id} v${parsed.value.insight.version}`
            : `已入洞察庫 ${parsed.value.insight.insight_id} v${parsed.value.insight.version}`,
        );
        setLiveMutationProblem(null);
        refreshLive(generation, expectedFixtureEpoch, true);
      } catch (caught) {
        if (
          isAbortError(caught) ||
          !isLiveSnapshotCurrent(generation, expectedFixtureEpoch) ||
          importControllerRef.current !== controller
        ) {
          return;
        }
        setLiveMutationProblem(
          caught instanceof InsightTransportError
            ? transportProblem(
                "",
                caught,
                "入庫結果未知；原文已保留，系統會重讀兩邊清單。請先核對再試，唔會自動重送。",
              )
            : {
                headline:
                  "入庫結果未知；原文已保留，系統會重讀兩邊清單。請先核對再試，唔會自動重送。",
                technical: String(caught),
              },
        );
        refreshLive(generation, expectedFixtureEpoch, true);
      } finally {
        if (importControllerRef.current === controller) {
          importControllerRef.current = null;
          if (isLiveSnapshotCurrent(generation, expectedFixtureEpoch)) {
            setImportPending(false);
          }
        }
      }
    })();
  };

  const clearArchiveAction = (key: string) => {
    const timer = archiveTimersRef.current.get(key);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      archiveTimersRef.current.delete(key);
    }
    archiveActionsRef.current.delete(key);
    publishArchiveActions();
  };

  const executeLiveArchive = async (
    key: string,
    snapshot: LiveArchiveSnapshot,
  ) => {
    const current = archiveActionsRef.current.get(key);
    if (
      current?.stage !== "grace" ||
      current.snapshot !== snapshot ||
      !isLiveSnapshotCurrent(snapshot.generation, snapshot.fixtureEpoch)
    ) {
      return;
    }
    archiveTimersRef.current.delete(key);
    archiveActionsRef.current.set(key, { stage: "request", snapshot });
    publishArchiveActions();
    if (mutationControllersRef.current.has(`archive:${key}`)) {
      return;
    }
    invalidateLiveLists();
    const controller = new AbortController();
    mutationControllersRef.current.set(`archive:${key}`, controller);
    setLiveMutationProblem(null);

    try {
      const result = await archiveLiveInsight(
        snapshot.origin,
        snapshot.insight_id,
        controller.signal,
      );
      const latest = archiveActionsRef.current.get(key);
      if (
        !isLiveSnapshotCurrent(snapshot.generation, snapshot.fixtureEpoch) ||
        mutationControllersRef.current.get(`archive:${key}`) !== controller ||
        latest?.stage !== "request" ||
        latest.snapshot !== snapshot
      ) {
        return;
      }
      const parsed = parseStrictHttp<LiveInsightArchive>(result, () =>
        parseInsightArchive(result.body, snapshot),
      );
      if (!parsed.ok) {
        clearArchiveAction(key);
        setLiveMutationProblem(
          httpProblem(
            "",
            result,
            result.status === 200
              ? "歸檔結果未能核實；洞察仍保留，系統會重讀兩邊清單。請先核對再試。"
              : "未有歸檔；洞察仍保留，系統會重讀兩邊清單。請按完整錯誤再試。",
            parsed.error,
          ),
        );
        refreshLive(snapshot.generation, snapshot.fixtureEpoch, true);
        return;
      }

      clearArchiveAction(key);
      setActiveResource((resource) => {
        const data = resource.data.filter(
          (row) =>
            insightIdentityKey(row.origin, row.insight_id) !== key,
        );
        return {
          status: data.length === 0 ? "ready-empty" : "ready",
          data,
          problem: null,
        };
      });
      const archived: LiveInsightArchiveSummary = {
        archive_id: parsed.value.archive_id,
        origin: parsed.value.origin,
        insight_id: parsed.value.insight_id,
        deleted_at: parsed.value.deleted_at,
        version_count: parsed.value.version_count,
      };
      setArchiveResource((resource) => {
        const archiveKey = insightArchiveIdentityKey(
          archived.origin,
          archived.insight_id,
          archived.archive_id,
        );
        const data = sortArchiveSummaries([
          archived,
          ...resource.data.filter(
            (row) =>
              insightArchiveIdentityKey(
                row.origin,
                row.insight_id,
                row.archive_id,
              ) !== archiveKey,
          ),
        ]);
        return { status: "ready", data, problem: null };
      });
      if (selectedLiveKeyRef.current === key) {
        selectedLiveKeyRef.current = null;
        detailControllerRef.current?.abort();
        detailControllerRef.current = null;
        setLiveSelection(null);
      }
      setNotice("已歸檔，可在已歸檔區恢復。");
      setLiveMutationProblem(null);
      refreshLive(snapshot.generation, snapshot.fixtureEpoch, true);
    } catch (caught) {
      const latest = archiveActionsRef.current.get(key);
      if (
        isAbortError(caught) ||
        !isLiveSnapshotCurrent(snapshot.generation, snapshot.fixtureEpoch) ||
        mutationControllersRef.current.get(`archive:${key}`) !== controller ||
        latest?.snapshot !== snapshot
      ) {
        return;
      }
      clearArchiveAction(key);
      setLiveMutationProblem(
        caught instanceof InsightTransportError
          ? transportProblem(
              "",
              caught,
              "歸檔結果未知；洞察仍保留，系統會重讀兩邊清單。請先核對再試，唔會自動重送。",
            )
          : {
              headline:
                "歸檔結果未知；洞察仍保留，系統會重讀兩邊清單。請先核對再試，唔會自動重送。",
              technical: String(caught),
            },
      );
      refreshLive(snapshot.generation, snapshot.fixtureEpoch, true);
    } finally {
      if (
        mutationControllersRef.current.get(`archive:${key}`) === controller
      ) {
        mutationControllersRef.current.delete(`archive:${key}`);
      }
    }
  };

  const beginLiveArchive = (summary: LiveInsightSummary) => {
    if (ownerReview || liveModeRef.current !== "normal") {
      return;
    }
    const key = insightIdentityKey(summary.origin, summary.insight_id);
    if (
      archiveActionsRef.current.has(key) ||
      mutationControllersRef.current.has(`archive:${key}`)
    ) {
      return;
    }
    const snapshot: LiveArchiveSnapshot = {
      mode: "normal",
      generation: liveGenerationRef.current,
      fixtureEpoch: liveFixtureEpochRef.current,
      origin: summary.origin,
      insight_id: summary.insight_id,
      versions: [...summary.versions],
      version_count: summary.versions.length,
    };
    if (!isLiveSnapshotCurrent(snapshot.generation, snapshot.fixtureEpoch)) {
      return;
    }
    archiveActionsRef.current.set(key, { stage: "grace", snapshot });
    publishArchiveActions();
    setLiveMutationProblem(null);
    setNotice(null);
    const timer = window.setTimeout(() => {
      void executeLiveArchive(key, snapshot);
    }, INSIGHT_ARCHIVE_GRACE_MS);
    archiveTimersRef.current.set(key, timer);
  };

  const undoLiveArchive = (key: string) => {
    const action = archiveActionsRef.current.get(key);
    if (action?.stage !== "grace") {
      return;
    }
    clearArchiveAction(key);
    setNotice("已復原，未有發出歸檔要求。");
    setLiveMutationProblem(null);
  };

  const restoreLiveArchive = (row: LiveInsightArchiveSummary) => {
    if (ownerReview || liveModeRef.current !== "normal") {
      return;
    }
    const key = insightArchiveIdentityKey(
      row.origin,
      row.insight_id,
      row.archive_id,
    );
    if (restoreActionsRef.current.has(key)) {
      return;
    }
    const snapshot: LiveRestoreSnapshot = {
      mode: "normal",
      generation: liveGenerationRef.current,
      fixtureEpoch: liveFixtureEpochRef.current,
      origin: row.origin,
      insight_id: row.insight_id,
      archive_id: row.archive_id,
      version_count: row.version_count,
    };
    if (!isLiveSnapshotCurrent(snapshot.generation, snapshot.fixtureEpoch)) {
      return;
    }
    restoreActionsRef.current.set(key, snapshot);
    publishRestoreActions();
    invalidateLiveLists();
    const controller = new AbortController();
    mutationControllersRef.current.set(`restore:${key}`, controller);
    setLiveMutationProblem(null);
    setNotice(null);

    void (async () => {
      try {
        const result = await restoreLiveInsight(
          snapshot.origin,
          snapshot.insight_id,
          snapshot.archive_id,
          controller.signal,
        );
        if (
          !isLiveSnapshotCurrent(snapshot.generation, snapshot.fixtureEpoch) ||
          mutationControllersRef.current.get(`restore:${key}`) !== controller ||
          restoreActionsRef.current.get(key) !== snapshot
        ) {
          return;
        }
        const parsed = parseStrictHttp<LiveInsightRestore>(result, () =>
          parseInsightRestore(result.body, snapshot),
        );
        if (!parsed.ok) {
          restoreActionsRef.current.delete(key);
          publishRestoreActions();
          setLiveMutationProblem(
            httpProblem(
              "",
              result,
              result.status === 200
                ? "恢復結果未能核實；已歸檔項目仍保留，系統會重讀兩邊清單。請先核對再試。"
                : "未有恢復；已歸檔項目仍保留，系統會重讀兩邊清單。請按完整錯誤再試。",
              parsed.error,
            ),
          );
          refreshLive(snapshot.generation, snapshot.fixtureEpoch, true);
          return;
        }
        restoreActionsRef.current.delete(key);
        publishRestoreActions();
        setArchiveResource((resource) => {
          const data = resource.data.filter(
            (item) =>
              insightArchiveIdentityKey(
                item.origin,
                item.insight_id,
                item.archive_id,
              ) !== key,
          );
          return {
            status: data.length === 0 ? "ready-empty" : "ready",
            data,
            problem: null,
          };
        });
        setNotice(
          `已恢復 ${parsed.value.origin}/${parsed.value.insight_id}；洞察庫正重新核對。`,
        );
        setLiveMutationProblem(null);
        refreshLive(snapshot.generation, snapshot.fixtureEpoch, true);
      } catch (caught) {
        if (
          isAbortError(caught) ||
          !isLiveSnapshotCurrent(snapshot.generation, snapshot.fixtureEpoch) ||
          mutationControllersRef.current.get(`restore:${key}`) !== controller ||
          restoreActionsRef.current.get(key) !== snapshot
        ) {
          return;
        }
        restoreActionsRef.current.delete(key);
        publishRestoreActions();
        setLiveMutationProblem(
          caught instanceof InsightTransportError
            ? transportProblem(
                "",
                caught,
                "恢復結果未知；已歸檔項目仍保留，系統會重讀兩邊清單。請先核對再試，唔會自動重送。",
              )
            : {
                headline:
                  "恢復結果未知；已歸檔項目仍保留，系統會重讀兩邊清單。請先核對再試，唔會自動重送。",
                technical: String(caught),
              },
        );
        refreshLive(snapshot.generation, snapshot.fixtureEpoch, true);
      } finally {
        if (
          mutationControllersRef.current.get(`restore:${key}`) === controller
        ) {
          mutationControllersRef.current.delete(`restore:${key}`);
        }
      }
    })();
  };

  if (!draft) {
    return <p className="state-msg">載入洞察編輯…</p>;
  }

  const fieldsReadonly = draft.exported;

  return (
    <div className="detail-stack">
      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="state-msg" role="status">
          {notice}
        </p>
      ) : null}
      {liveMutationProblem ? (
        <LiveProblemPanel
          problem={liveMutationProblem}
          testId="insight-mutation-error"
        />
      ) : null}

      <p className="panel__note">
        洞察係<strong>純記錄</strong>
        ：唔連策略、唔連回測（#22）。一包一 primary；import
        先驗證後先寫入。
        {ownerReview ? " Owner-review：in-memory store。" : null}
      </p>

      <section className="panel" role="region" aria-label="洞察草稿列表">
        <h2 className="panel__title">洞察草圖草稿</h2>
        <ul className="draft-list">
          {drafts.map((item) => {
            const active = item.sketchId === draft.sketchId;
            return (
              <li key={item.sketchId}>
                <button
                  type="button"
                  className={
                    active
                      ? "draft-list__btn draft-list__btn--active"
                      : "draft-list__btn"
                  }
                  onClick={() => {
                    if (active) return;
                    const opened = selectDraft(item.sketchId, draft, storage);
                    if (opened) {
                      setDraft(opened);
                      refreshDrafts();
                    }
                  }}
                >
                  <span className="draft-list__id">{item.sketchId}</span>
                  <span className="draft-list__status">
                    {item.exported ? "已匯出" : "草稿"}
                    {item.instrument ? ` · ${item.instrument}` : ""}
                  </span>
                  <span className="draft-list__gaps">
                    {draftGapSummary(item)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="panel" role="region" aria-label="新洞察">
        <header className="sketch-head">
          <h2 className="panel__title">新洞察</h2>
          <span className="sketch-head__id">
            {draft.sketchId} · kind: insight
            {draft.instrument ? ` · ${draft.instrument}` : ""}
          </span>
          <button
            type="button"
            className="btn"
            onClick={() => {
              saveDraft(draft, storage);
              const snap = readSketchStore(storage);
              const created = createEmptyDraft(
                snap.drafts.map((d) => d.sketchId),
                new Date(),
                "insight",
              );
              writeSketchStore(
                {
                  ...snap,
                  drafts: [...snap.drafts, created],
                  activeId: created.sketchId,
                },
                storage,
              );
              setDraft(created);
              refreshDrafts();
              setNotice(`已開新洞察草圖 ${created.sketchId}`);
            }}
          >
            新洞察
          </button>
          {draft.exported ? (
            <button
              type="button"
              className="btn"
              onClick={() => {
                saveDraft(draft, storage);
                const snap = readSketchStore(storage);
                const copy = duplicateDraftAsNew(
                  draft,
                  snap.drafts.map((d) => d.sketchId),
                );
                writeSketchStore(
                  {
                    ...snap,
                    drafts: [...snap.drafts, copy],
                    activeId: copy.sketchId,
                  },
                  storage,
                );
                setDraft(copy);
                refreshDrafts();
                setNotice(
                  `已複製 ${copy.sketchId}（已清圖片／判斷；可改 instrument）`,
                );
              }}
            >
              複製成新草圖
            </button>
          ) : null}
        </header>

        <label className="field">
          <span>洞察標題（必填）</span>
          <input
            className={draft.title.trim() ? "inp" : "inp inp--required-empty"}
            type="text"
            aria-label="洞察標題"
            value={draft.title}
            placeholder="例如「開市頭 30 分鐘假突破多」"
            disabled={fieldsReadonly}
            onChange={(e) => {
              setDraft({ ...draft, title: e.target.value });
            }}
          />
        </label>

        <InstrumentPicker
          catalog={catalog}
          instrument={draft.instrument}
          assetClass={draft.assetClass}
          locked={draft.instrumentLocked}
          exported={draft.exported}
          legacyIncomplete={legacyIncomplete}
          onSelect={(symbol, assetClass) => {
            setDraft(selectInstrument(draft, symbol, assetClass));
          }}
        />

        <div className="sketch-grid" role="region" aria-label="四格圖文">
          {draft.charts.map((chart, index) => (
            <SketchCell
              key={chart.slotId}
              chart={chart}
              index={index}
              onChange={(nextChart) => {
                if (fieldsReadonly) return;
                setDraft(applyChartChange(draft, nextChart));
              }}
            />
          ))}
        </div>

        <div className="form-actions">
          <button
            type="button"
            className="btn btn--primary"
            disabled={!canExport}
            title={canExport ? "匯出" : blockers.join(" · ")}
            onClick={() => {
              try {
                const result = exportSketchPackage(
                  draft,
                  storage,
                  new Date(),
                  catalog,
                );
                setDraft(result.draft);
                setExportPreview(result.package);
                refreshDrafts();
                setNotice(
                  `已準備洞察圖文包（${result.package.relativeDir}）`,
                );
                setError(null);
              } catch (err) {
                setError(err instanceof Error ? err.message : String(err));
              }
            }}
          >
            ⬇ 匯出圖文包（kind: insight）
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              const saved = saveDraft(draft, storage);
              setDraft(saved);
              refreshDrafts();
              setNotice("洞察草稿已存");
            }}
          >
            存草稿
          </button>
          {!canExport ? (
            <span className="state-msg">{blockers.join(" · ")}</span>
          ) : null}
        </div>
      </section>

      <section className="panel" role="region" aria-label="匯入洞察">
        <h2 className="panel__title">⬆ 匯入 insight.v1</h2>
        <p className="panel__note">
          必須 based_on_sketch＋based_on_sketch_origin＋instrument＋asset_class。
          全部驗證完成先寫入；錯配唔會「已入庫」。
        </p>
        <label className="btn" htmlFor="insight-file">
          揀 insight YAML
        </label>
        <input
          id="insight-file"
          className="visually-hidden"
          type="file"
          accept=".yaml,.yml,text/yaml"
          onChange={(e: ChangeEvent<HTMLInputElement>) => {
            const file = e.target.files?.[0];
            if (!file) return;
            void file.text().then((t) => {
              setImportText(t);
            });
            e.target.value = "";
          }}
        />
        <label className="field">
          <span>insight.v1 YAML</span>
          <textarea
            className="inp inp--area"
            rows={8}
            spellCheck={false}
            aria-label="insight.v1 YAML"
            value={importText}
            onChange={(e) => {
              setImportText(e.target.value);
            }}
          />
        </label>
        {sketchLoad.kind === "loading" ? (
          <p className="state-msg" data-testid="insight-sketch-loading">
            原草圖載入中…
          </p>
        ) : null}
        {sketchLoad.kind === "not_found" ? (
          <p className="state-msg" data-testid="insight-sketch-missing">
            本機缺原草圖 {sketchLoad.sketchId}（{sketchLoad.origin}
            ）——仍須過 catalog 先可入庫。
          </p>
        ) : null}
        {sketchLoad.kind === "error" ? (
          <p
            className="state-msg state-msg--error"
            data-testid="insight-sketch-error"
          >
            {sketchLoad.message}
          </p>
        ) : null}
        <button
          type="button"
          className="btn btn--primary"
          data-testid="insight-import"
          disabled={importDisabled}
          onClick={handleImportClick}
        >
          {importPending ? "入庫中…" : "入庫"}
        </button>
      </section>

      <section className="panel" role="region" aria-label="洞察庫">
        <h2 className="panel__title">洞察庫 · 純記錄 · 市場語境</h2>
        {ownerReview ? (
          ownerInsights.length === 0 ? (
            <p className="state-msg">仲未有洞察記錄。</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>洞察</th>
                    <th>市場</th>
                    <th>標題</th>
                    <th>狀態</th>
                    <th>動作</th>
                  </tr>
                </thead>
                <tbody>
                  {ownerInsights.map((item) => (
                    <tr
                      key={`${item.origin}@${item.insight_id}@${item.version}`}
                    >
                      <td className="table__mono">
                        {item.origin}/{item.insight_id} v{item.version}
                      </td>
                      <td>
                        <strong>{item.instrument}</strong>
                        <br />
                        <span className="state-msg">
                          {assetClassLabel(item.asset_class)}
                        </span>
                      </td>
                      <td>{item.title}</td>
                      <td>
                        <select
                          className="inp"
                          aria-label={`狀態 ${item.insight_id}`}
                          value={item.validation_status}
                          onChange={(event) => {
                            updateInsightStatus(
                              item.insight_id,
                              item.version,
                              event.target.value as InsightStatus,
                              storage,
                              item.origin,
                            );
                            refreshOwnerInsights();
                          }}
                        >
                          {STATUSES.map((status) => (
                            <option key={status} value={status}>
                              {status}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn"
                          onClick={() => {
                            setOwnerSelected(item);
                          }}
                        >
                          過目
                        </button>
                        <button
                          type="button"
                          className="btn btn--danger"
                          onClick={() => {
                            deleteInsight(
                              item.insight_id,
                              item.version,
                              storage,
                              item.origin,
                            );
                            setOwnerSelected(null);
                            refreshOwnerInsights();
                          }}
                        >
                          刪除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (
          <>
            {activeResource.problem ? (
              <LiveProblemPanel
                problem={activeResource.problem}
                testId="active-insights-error"
              />
            ) : null}
            {activeResource.status === "loading" ? (
              <p className="state-msg">載入洞察庫…</p>
            ) : null}
            {activeResource.status !== "loading" &&
            activeResource.data.length === 0 ? (
              <p className="state-msg">仲未有活躍洞察記錄。</p>
            ) : null}
            {activeResource.data.length > 0 ? (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>洞察</th>
                      <th>市場</th>
                      <th>標題</th>
                      <th>狀態</th>
                      <th>動作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeResource.data.map((item) => {
                      const key = insightIdentityKey(
                        item.origin,
                        item.insight_id,
                      );
                      const action = pendingArchives[key];
                      return (
                        <tr key={key}>
                          <td className="table__mono">
                            {item.origin}/{item.insight_id}
                            <br />
                            <span className="state-msg">
                              最新 v{item.latest_version} · 共{" "}
                              {item.versions.length} 個版本
                            </span>
                          </td>
                          <td>
                            <strong>{item.instrument}</strong>
                            <br />
                            <span className="state-msg">
                              {assetClassLabel(item.asset_class)}
                            </span>
                          </td>
                          <td>{item.title}</td>
                          <td>
                            <span>{item.validation_status}</span>
                            <br />
                            <span className="state-msg">
                              狀態來自不可變洞察版本，只讀
                            </span>
                          </td>
                          <td>
                            <button
                              type="button"
                              className="btn"
                              onClick={() => {
                                openLiveDetail(item);
                              }}
                            >
                              過目
                            </button>
                            {action?.stage === "grace" ? (
                              <button
                                type="button"
                                className="btn"
                                data-testid={`undo-archive-${item.origin}-${item.insight_id}`}
                                onClick={() => {
                                  undoLiveArchive(key);
                                }}
                              >
                                復原
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="btn btn--danger"
                                data-testid={`archive-${item.origin}-${item.insight_id}`}
                                disabled={action?.stage === "request"}
                                onClick={() => {
                                  beginLiveArchive(item);
                                }}
                              >
                                {action?.stage === "request"
                                  ? "歸檔中…"
                                  : "歸檔"}
                              </button>
                            )}
                            <br />
                            <span className="state-msg">
                              {action?.stage === "grace"
                                ? "待歸檔 · 約 5 秒內可復原"
                                : `會歸檔此洞察的全部 ${item.versions.length} 個版本；之後可在「已歸檔」恢復。`}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : null}
          </>
        )}
      </section>

      {!ownerReview ? (
        <section className="panel" role="region" aria-label="已歸檔">
          <h2 className="panel__title">已歸檔</h2>
          {archiveResource.problem ? (
            <LiveProblemPanel
              problem={archiveResource.problem}
              testId="archived-insights-error"
            />
          ) : null}
          {archiveResource.status === "loading" ? (
            <p className="state-msg">載入已歸檔洞察…</p>
          ) : null}
          {archiveResource.status !== "loading" &&
          archiveResource.data.length === 0 ? (
            <p className="state-msg">未有已歸檔洞察。</p>
          ) : null}
          {archiveResource.data.length > 0 ? (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>洞察</th>
                    <th>版本</th>
                    <th>歸檔時間</th>
                    <th>動作</th>
                  </tr>
                </thead>
                <tbody>
                  {archiveResource.data.map((item) => {
                    const key = insightArchiveIdentityKey(
                      item.origin,
                      item.insight_id,
                      item.archive_id,
                    );
                    const pending = pendingRestores[key] !== undefined;
                    return (
                      <tr key={key}>
                        <td className="table__mono">
                          {item.origin}/{item.insight_id}
                        </td>
                        <td>共 {item.version_count} 個版本</td>
                        <td className="table__mono">{item.deleted_at}</td>
                        <td>
                          <button
                            type="button"
                            className="btn"
                            data-testid={`restore-${item.origin}-${item.insight_id}`}
                            disabled={pending}
                            onClick={() => {
                              restoreLiveArchive(item);
                            }}
                          >
                            {pending ? "恢復中…" : "恢復"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}

      {ownerReview && ownerSelected ? (
        <section className="panel" role="region" aria-label="洞察詳情">
          <h2 className="panel__title">
            {ownerSelected.insight_id} · {ownerSelected.title}
          </h2>
          <dl className="meta-grid">
            <div>
              <dt>instrument</dt>
              <dd className="table__mono">{ownerSelected.instrument}</dd>
            </div>
            <div>
              <dt>asset_class</dt>
              <dd>{assetClassLabel(ownerSelected.asset_class)}</dd>
            </div>
            <div>
              <dt>based_on_sketch</dt>
              <dd className="table__mono">
                {ownerSelected.based_on_sketch_origin}/
                {ownerSelected.based_on_sketch}
              </dd>
            </div>
          </dl>
          <p className="prose">{ownerSelected.measurement.hypothesis}</p>
        </section>
      ) : null}

      {!ownerReview && liveSelection ? (
        <section className="panel" role="region" aria-label="洞察詳情">
          <h2 className="panel__title">
            {liveSelection.summary.insight_id} ·{" "}
            {liveSelection.summary.title}
          </h2>
          {liveSelection.status === "loading" ? (
            <p className="state-msg">載入完整版本記錄…</p>
          ) : null}
          {liveSelection.problem ? (
            <LiveProblemPanel problem={liveSelection.problem} />
          ) : null}
          {liveSelection.status === "ready" && liveSelection.detail
            ? (() => {
                const latest =
                  liveSelection.detail.versions[
                    liveSelection.detail.versions.length - 1
                  ];
                let hypothesis = "";
                try {
                  hypothesis =
                    parseInsightYaml(latest.source_text).measurement.hypothesis;
                } catch {
                  hypothesis =
                    "原始版本已由 wire contract 核實；展示 parser 未能讀出 hypothesis。";
                }
                return (
                  <>
                    <dl className="meta-grid">
                      <div>
                        <dt>instrument</dt>
                        <dd className="table__mono">{latest.instrument}</dd>
                      </div>
                      <div>
                        <dt>asset_class</dt>
                        <dd>{assetClassLabel(latest.asset_class)}</dd>
                      </div>
                      <div>
                        <dt>based_on_sketch</dt>
                        <dd className="table__mono">
                          {latest.based_on_sketch_origin}/
                          {latest.based_on_sketch}
                        </dd>
                      </div>
                      <div>
                        <dt>版本</dt>
                        <dd>
                          最新 v{liveSelection.detail.latest_version} · 共{" "}
                          {liveSelection.detail.version_count} 個版本
                        </dd>
                      </div>
                    </dl>
                    <p className="prose">{hypothesis}</p>
                  </>
                );
              })()
            : null}
        </section>
      ) : null}

      <div className="panel">
        <b>呢一頁刻意冇嘅嘢</b>
        <pre className="code-block">
{`✗ 冇「用呢個洞察跑回測」
✗ 冇連去策略版本庫
狀態唔會自動變`}
        </pre>
      </div>

      {exportPreview ? (
        <ExportDialog
          packagePreview={exportPreview}
          onClose={() => {
            setExportPreview(null);
          }}
          onGoQuantify={() => {
            setExportPreview(null);
          }}
        />
      ) : null}
    </div>
  );
}
