import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Link, useSearchParams } from "react-router-dom";

import { InfoButton } from "../components/ui/InfoButton";
import { PageHeader } from "../components/ui/PageHeader";
import { PageTabsPortal } from "../components/ui/PageTabBar";

import {
  PaperTransportError,
  fetchPaperBaselines,
  fetchPaperLedgerOrigin,
  fetchPaperReviewDownload,
  fetchPaperReviewRequest,
  fetchPaperReviewTerminalOpener,
  postPaperReviewSnapshot,
  fetchPaperContracts,
  fetchStrategies,
  fetchPaperTrader,
  fetchPaperTraderRequest,
  fetchPaperTraders,
  postPaperProvisioningReadiness,
  postPaperTrader,
  type PaperHttpResult,
} from "../api/client";
import { PaperFleetBoard } from "../components/paper/PaperFleetBoard";
import { PaperRuntimePanel } from "../components/paper/PaperRuntimePanel";
import { useOptionalToast } from "../components/ui/toastContext";
import { useWritesBlocked } from "../lib/net/online";
import {
  buildPaperCreateRequest,
  buildPaperProvisioningRequest,
  isPaperGenerationCurrent,
  paperContractSelectionEquals,
  paperErrorCopy,
  paperSelectionEquals,
  paperStrategySelectionEquals,
  paperTraderEquals,
  parsePaperApiError,
  parsePaperBaselineList,
  parsePaperContractList,
  parsePaperCreatedTrader,
  parsePaperProvisioningReadiness,
  parsePaperTraderDetail,
  parsePaperTraderList,
  parsePaperTraderRequestStatus,
} from "../lib/paper/normalContract";
import {
  buildPaperReviewCreateRequest,
  paperReviewMemberPaths,
  parsePaperLedgerOrigin,
  parsePaperReviewErrorEnvelope,
  parsePaperReviewStatus,
  parsePaperReviewTerminalOpenerClaim,
} from "../lib/paper/reviewContract";
import {
  verifyPaperReviewArtifact,
  verifyPaperReviewOpener,
} from "../lib/paper/reviewDownload";
import {
  isPaperReviewLocked,
  paperReviewUrlIdentityMatches,
  readPaperReviewUrlIdentity,
  writePaperReviewUrlIdentity,
  type PaperReviewPhase,
} from "../lib/paper/reviewState";
import type { PaperReviewErrorExpectation } from "../lib/paper/reviewContract";
import type {
  PaperBaseline,
  PaperCheckStatus,
  PaperContractRow,
  PaperContractSelection,
  PaperEligibleStrategy,
  PaperErrorCode,
  PaperMarketSession,
  PaperProvisioningReadiness,
  PaperProvisioningState,
  PaperReadinessCheckKey,
  PaperReviewError,
  PaperSelection,
  PaperStrategySelection,
  PaperLedgerOrigin,
  PaperReviewCreateRequest,
  PaperReviewErrorCode,
  PaperReviewStatus,
  PaperTrader,
  PaperTraderCreateRequest,
} from "../lib/paper/types";
import { PAPER_REVIEW_ERROR_HTTP } from "../lib/paper/types";

/*
 * P6 模擬盤 — Stage A provisioning + runtime panel (v4 routes).
 * Runtime panel consumes registered /runtime and review-v2 APIs only.
 * IB is market-data only; UI copy never claims IB paper fills.
 */

type LoadPhase = "idle" | "loading" | "ready" | "empty" | "error";

interface ListState<T> {
  phase: LoadPhase;
  rows: T[];
  error: string | null;
}

/*
 * Option A: one atomic preflight answer carries both truths — whether this
 * approved selection may persist a not-running trader, and what the four
 * external checks actually say. They are rendered separately and never mixed.
 */
interface PreflightState {
  phase: "idle" | "loading" | "ready" | "error";
  data: PaperProvisioningReadiness | null;
  error: string | null;
}

type CreatePhase =
  | "idle"
  | "submitting"
  | "recovering"
  | "created"
  | "unknown"
  | "absent"
  | "not-activated"
  | "error";

interface CreateState {
  phase: CreatePhase;
  error: string | null;
  detail: string | null;
}

interface CreateIntent {
  request: PaperTraderCreateRequest;
  generation: number;
}

interface LedgerState {
  phase: "idle" | "loading" | "ready" | "error";
  data: PaperLedgerOrigin | null;
  error: string | null;
}

interface ReviewState {
  phase: PaperReviewPhase;
  traderId: string | null;
  request: PaperReviewCreateRequest | null;
  status: PaperReviewStatus | null;
  /**
   * A terminal HTTP failure for this request. It is kept as the typed error
   * rather than folded into a fake `PaperReviewStatus`, because an HTTP error
   * carries no trader id and no captured instant and must never pretend to.
   */
  failure: PaperReviewError | null;
  error: string | null;
  detail: string | null;
}

const IDLE_REVIEW: ReviewState = {
  phase: "idle",
  traderId: null,
  request: null,
  status: null,
  failure: null,
  error: null,
  detail: null,
};

/** §7: the artifact is only saved after all ten checks pass. */
interface DownloadState {
  phase: "idle" | "working" | "saved" | "error";
  filename: string | null;
  error: string | null;
  detail: string | null;
}

/** §8: the persisted backend text is the only thing ever copied. */
interface OpenerState {
  phase: "idle" | "working" | "copied" | "error";
  error: string | null;
  detail: string | null;
}

const IDLE_DOWNLOAD: DownloadState = {
  phase: "idle",
  filename: null,
  error: null,
  detail: null,
};

const IDLE_OPENER: OpenerState = {
  phase: "idle",
  error: null,
  detail: null,
};

interface DetailState {
  phase: "idle" | "loading" | "ready" | "error";
  data: PaperTrader | null;
  error: string | null;
}

function idleList<T>(): ListState<T> {
  return { phase: "idle", rows: [], error: null };
}

const CHECK_LABEL: Record<PaperReadinessCheckKey, string> = {
  ib_realtime: "IB 實時價格",
  exchange_calendar: "交易所行事曆",
  telegram: "Telegram 通知",
  baseline_integrity: "對照基準完整性",
};

/**
 * Owner-facing copy per check and state. The backend `reason` string is part of
 * the strict contract but is engineering text, so it is validated and never
 * rendered (banned-vocabulary rule, p4 稿 constraint #16).
 */
const CHECK_COPY: Record<
  PaperReadinessCheckKey,
  Record<PaperCheckStatus, string>
> = {
  /*
   * The three external providers describe whether the engine could ever start,
   * not whether a not-running trader may be saved. Their blocked and unknown
   * copy therefore never claims that creation is refused — only the
   * authorization block and the baseline package can say that.
   */
  ib_realtime: {
    ready: "已經攞到實時價格。",
    blocked: "而家攞唔到實時價格，所以建立之後引擎唔會開始。",
    unknown: "而家確認唔到有冇實時價格，所以建立之後引擎唔會開始。",
  },
  exchange_calendar: {
    ready: "已經知道邊日開市、邊日休市。",
    blocked: "讀唔到交易日資料，所以建立之後引擎唔會開始。",
    unknown: "確認唔到交易日資料，所以建立之後引擎唔會開始。",
  },
  telegram: {
    ready: "重要事件通知得到你。",
    blocked: "通知測試唔通過，所以建立之後引擎唔會開始。",
    unknown: "確認唔到通知係咪通得到，所以建立之後引擎唔會開始。",
  },
  // The locked baseline is different: without it there is nothing to lock.
  baseline_integrity: {
    ready: "你揀嗰次回測嘅記錄完整可讀。",
    blocked: "你揀嗰次回測嘅記錄唔完整，所以唔可以建立。",
    unknown: "確認唔到你揀嗰次回測嘅記錄，所以唔可以建立。",
  },
};

const CHECK_STATUS_LABEL: Record<PaperCheckStatus, string> = {
  ready: "就緒",
  blocked: "未通過",
  unknown: "未能確認",
};

const SESSION_COPY: Record<PaperMarketSession, string> = {
  open: "而家係交易時段。",
  closed: "而家係休市。唔係錯誤；建立咗之後會等下一個可交易時段。",
  unknown: "而家係咪交易時段暫時確認唔到；呢項本身唔會阻止建立。",
};

/** D17 safety-net policy shown before creation; the record carries the truth. */
const SAFEGUARD_SUMMARY = "最大回撤 8R · 連續蝕 8 單 · 失明 5 分鐘";

const EXECUTION_NOTE =
  "app 自家模擬成交 · 只攞 Interactive Brokers 嘅實時價格，唔會向 IB 落任何訂單。";

const PROVISIONED_TRUTH = "已建立；模擬引擎尚未啟用";

const UNKNOWN_CREATE_TITLE = "建立結果未知。";

const PREFLIGHT_UNREADABLE = "開始前檢查嘅回覆讀唔到，所以而家唔可以建立。";

/*
 * Two different truths, never merged (Option A §5). The authorization line only
 * ever says whether one not-running trader may be saved; the runtime block
 * keeps saying what IB, the calendar, Telegram and the baseline really are.
 */
const AUTHORIZATION_ALLOWED =
  "已授權：可以建立一個未啟用嘅交易員。";
const AUTHORIZATION_SCOPE =
  "呢個授權只准保存一個未啟用嘅交易員，唔代表可以開始模擬交易、連 IB 或落單。";
const AUTHORIZATION_COPY: Record<PaperProvisioningState, string> = {
  disabled: "未授權：而家唔可以建立交易員。",
  armed: "而家未可以建立交易員。",
  claimed: "呢個授權已經用嚟建立一個交易員，唔可以再用。",
  consumed: "呢個授權已經用完，唔可以再用。",
  expired: "授權已經過期，所以而家唔可以建立交易員。",
};
const RUNTIME_SPLIT_NOTE =
  "以下係實際啟動條件，同上面嘅建立授權係兩件事；未齊都可以建立一個未啟用嘅交易員。";
const RUNTIME_BLOCKED_NOTE =
  "實際啟動條件仲未齊，所以建立之後模擬引擎唔會開始，亦唔會有成交、持倉或盈虧。";

/** Owner-facing copy per Stage B error code; raw codes never reach the screen. */
const REVIEW_ERROR_COPY: Record<PaperReviewErrorCode, string> = {
  trader_not_found: "搵唔到呢個交易員。",
  ledger_not_ready:
    "呢個交易員未有已保存嘅初始帳戶記錄，所以而家建立唔到偏離包。",
  ledger_integrity_failed:
    "初始帳戶記錄嘅完整性核對唔通過，已經停低。",
  store_schema_upgrade_required:
    "模擬盤記錄格式需要先升級，而家讀唔到。",
  request_not_found: "搵唔到今次請求。",
  request_id_conflict:
    "同一個請求對應咗唔同內容，已經停低，唔會建立第二份。",
  snapshot_not_found: "搵唔到今次偏離包。",
  snapshot_not_ready: "偏離包仲準備緊。",
  snapshot_failed: "今次偏離包建立失敗。",
  snapshot_integrity_failed: "偏離包嘅完整性核對唔通過。",
  artifact_build_failed: "建立偏離包嗰陣失敗。",
  artifact_unavailable: "偏離包暫時讀唔到。",
  build_interrupted:
    "建立過程中斷咗；系統唔會自動重建，要你明確再建立一份新嘅。",
};

const LEDGER_UNREADABLE =
  "初始帳戶記錄未能通過完整驗證，所以而家建立唔到偏離包。";
const LEDGER_IDENTITY_DRIFT =
  "初始帳戶記錄同呢個交易員嘅鎖定內容對唔上，已經停低。";
const LEDGER_UNREACHABLE = "初始帳戶記錄暫時讀唔到。";
const REVIEW_UNKNOWN_TITLE = "今次請求嘅結果未知。";
const REVIEW_DIALOG_LABEL = "偏離包";
const DOWNLOAD_UNAVAILABLE = "呢個瀏覽器儲存唔到檔案，所以冇下載。";
const DOWNLOAD_REFUSED = "偏離包下載唔到。";
const COPY_UNAVAILABLE = "呢個瀏覽器複製唔到文字，所以冇複製。";
const COPY_REFUSED = "複製唔到 Terminal 開場白。";
const OPENER_UNREADABLE = "Terminal 開場白讀唔到。";

/** Plain-language banner wording for the card when the dialog is closed. */
const REVIEW_BANNER: Record<PaperReviewPhase, string> = {
  idle: "",
  submitting: "準備緊",
  recovering: "準備緊",
  preparing: "準備緊",
  ready: "已經準備好",
  failed: "建立失敗",
  unknown: "結果未知",
};

const ISSUE_KIND_COPY: Record<string, string> = {
  missing_member: "缺少檔案",
  unsafe_path: "路徑唔安全",
  unresolved_ref: "追唔到來源",
  hash_mismatch: "內容核對唔通過",
  identity_mismatch: "身份對唔上",
  count_mismatch: "數量對唔上",
  cutoff_unavailable: "擷取時點確認唔到",
  artifact_build_failed: "建立失敗",
  artifact_unavailable: "暫時讀唔到",
  build_interrupted: "建立中斷",
};
const ENGINE_NOT_ENABLED = "模擬引擎尚未啟用。";
const NOT_EVALUABLE_COPY =
  "模擬引擎未啟用，所以而家未判斷得到真實偏離；呢個唔等於「冇偏離」。";

function formatMoney(currency: string, amount: number): string {
  return `${currency} ${new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(amount)}`;
}

function formatR(value: number): string {
  const rounded = Number(value.toFixed(2));
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded}R`;
}

/** Instants may be shown locally but must always name a timezone (G-07). */
function formatInstant(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return `${value}（時間核實唔到）`;
  }
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const local = new Intl.DateTimeFormat("zh-HK", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
  return `${local}（${zone}） · ${value.replace("T", " ").replace("Z", " UTC")}`;
}

function shortSha(value: string): string {
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}

/** §5: the Owner also needs the same instant in their own zone, named. */
function formatLocalWithZone(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return `${value}（時間核實唔到）`;
  }
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const local = new Intl.DateTimeFormat("zh-HK", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
  return `${local}（${zone}）`;
}

function isAbortError(error: unknown): boolean {
  return (
    error !== null &&
    typeof error === "object" &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

function transportMessage(error: unknown): string {
  if (error instanceof PaperTransportError) {
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

type Failure =
  | { kind: "known"; code: PaperErrorCode; message: string }
  | { kind: "unknown"; message: string };

/**
 * Fail-closed classification: a response is either a parsed success, a known
 * `paper_api_error.v1`, or unknown. It is never silently success or empty.
 */
function classifyFailure(
  response: PaperHttpResult,
  parserError: string,
): Failure {
  const parsed = parsePaperApiError(response);
  if (parsed.ok) {
    return {
      kind: "known",
      code: parsed.value.code,
      message: paperErrorCopy(parsed.value.code),
    };
  }
  return {
    kind: "unknown",
    message: `HTTP ${String(response.status)}｜${parserError}`,
  };
}

type ReviewFailure =
  | {
      kind: "known";
      code: PaperReviewErrorCode;
      message: string;
      /** The complete typed error, so progress and issues stay available. */
      error: PaperReviewError;
    }
  | { kind: "unknown"; message: string };

interface ReviewErrorSource {
  status: number;
  body: unknown;
}

/**
 * Stage B non-2xx bodies are only ever the exact outer wrapper. A FastAPI 422,
 * an unknown code, a status that disagrees with the code, an identity drift or
 * any extra key is unknown — never a quiet success.
 *
 * The caller passes the closed set of shapes it may accept: a response that
 * matches none of them is unknown even when it is a perfectly valid error for
 * some other call.
 */
function classifyReviewFailure(
  response: ReviewErrorSource,
  expectations: readonly PaperReviewErrorExpectation[],
): ReviewFailure {
  for (const expectation of expectations) {
    const parsed = parsePaperReviewErrorEnvelope(response.body, expectation);
    if (
      parsed.ok &&
      PAPER_REVIEW_ERROR_HTTP[parsed.value.detail.code] === response.status
    ) {
      return {
        kind: "known",
        code: parsed.value.detail.code,
        message: REVIEW_ERROR_COPY[parsed.value.detail.code],
        error: parsed.value.detail,
      };
    }
  }
  return {
    kind: "unknown",
    message: `HTTP ${String(response.status)}`,
  };
}

/** The ledger must describe the exact trader record already on screen. */
function ledgerMatchesTrader(
  ledger: PaperLedgerOrigin,
  trader: PaperTrader,
): boolean {
  return (
    ledger.trader_id === trader.trader_id &&
    ledger.strategy.strategy_id === trader.strategy.strategy_id &&
    ledger.strategy.content_sha256 === trader.strategy.content_sha256 &&
    ledger.contract.contract_id === trader.contract_id &&
    ledger.baseline.run_id === trader.baseline.run_id &&
    ledger.baseline.result_sha256 === trader.baseline.result_sha256 &&
    ledger.baseline.range_start === trader.baseline.range_start &&
    ledger.baseline.range_end === trader.baseline.range_end &&
    ledger.account.account_id === trader.account.account_id &&
    ledger.account.currency === trader.account.currency &&
    ledger.account.initial_capital === trader.account.initial_capital
  );
}

function PaperError({ title, detail }: { title: string; detail: string | null }) {
  return (
    <div className="paper-error" role="alert">
      <p className="paper-error__title">{title}</p>
      {detail ? <p className="paper-error__detail">{detail}</p> : null}
    </div>
  );
}

function ConfirmRow({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="paper-confirm__row">
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function PaperPage() {
  const { showSuccess } = useOptionalToast();
  const [params, setParams] = useSearchParams();
  const highlightStrategy = params.get("strategy");
  const highlightRun = params.get("run");

  const [activeTab, setActiveTab] = useState<string>("overview");

  const [traders, setTraders] = useState<ListState<PaperTrader>>(idleList);
  const [detail, setDetail] = useState<DetailState>({
    phase: "idle",
    data: null,
    error: null,
  });
  const [ledger, setLedger] = useState<LedgerState>({
    phase: "idle",
    data: null,
    error: null,
  });
  const [review, setReview] = useState<ReviewState>(IDLE_REVIEW);
  const [dialogOpen, setDialogOpen] = useState(true);
  const [download, setDownload] = useState<DownloadState>(IDLE_DOWNLOAD);
  const [opener, setOpener] = useState<OpenerState>(IDLE_OPENER);

  const [eligible, setEligible] =
    useState<ListState<PaperEligibleStrategy>>(idleList);
  const [strategy, setStrategy] = useState<PaperEligibleStrategy | null>(null);

  const [contracts, setContracts] =
    useState<ListState<PaperContractRow>>(idleList);
  const [contract, setContract] = useState<PaperContractRow | null>(null);

  const [baselines, setBaselines] = useState<ListState<PaperBaseline>>(idleList);
  const [baseline, setBaseline] = useState<PaperBaseline | null>(null);

  const [preflight, setPreflight] = useState<PreflightState>({
    phase: "idle",
    data: null,
    error: null,
  });
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [create, setCreate] = useState<CreateState>({
    phase: "idle",
    error: null,
    detail: null,
  });

  const mountedRef = useRef(true);
  const controllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const preflightInFlightRef = useRef(false);
  const createInFlightRef = useRef(false);
  const pendingLookupRef = useRef(false);
  /** Exactly one canonical UUID4 per create intent (§6.5 / §6.6). */
  const createRequestRef = useRef<PaperTraderCreateRequest | null>(null);
  /**
   * A create intent stays locked from the first POST until a known terminal
   * outcome. While it is locked no selection may change, no second UUID may be
   * minted, and only a response for this exact intent may reach state.
   */
  const intentRef = useRef<CreateIntent | null>(null);
  /*
   * Synchronous mirror of the lock. React state is only settled on the next
   * render, so a selection handler dispatched in the same batch as the confirm
   * click would still read `createLocked === false`. Handlers read this ref.
   */
  const createLockedRef = useRef(false);
  /** Latest-request authority for the trader list and the open trader tab. */
  const listTokenRef = useRef<object | null>(null);
  const detailTokenRef = useRef<object | null>(null);
  const activeTabRef = useRef<string>("overview");
  /*
   * Stage B review request authority. `reviewLockedRef` is the synchronous
   * mirror of the lock, so a same-batch tab click cannot slip through on stale
   * render state; `reviewRequestRef` holds the single UUID for one intent.
   */
  const reviewLockedRef = useRef(false);
  const reviewInFlightRef = useRef(false);
  const statusInFlightRef = useRef(false);
  const reviewRequestRef = useRef<PaperReviewCreateRequest | null>(null);
  const recoveryStartedRef = useRef(false);
  /** Synchronous mirror of "this request id belongs to another intent". */
  const conflictRef = useRef(false);
  const bootstrapRef = useRef(false);
  /*
   * Stage B Phase 3 authority. `readySnapshotRef` mirrors the snapshot the
   * Owner can currently act on, so a download or copy answer that arrives for
   * a superseded snapshot can never reach a Blob or the clipboard.
   */
  const readySnapshotRef = useRef<string | null>(null);
  const downloadInFlightRef = useRef(false);
  const openerInFlightRef = useRef(false);
  const objectUrlRef = useRef<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    controllerRef.current = controller;
    return () => {
      mountedRef.current = false;
      // Abort is cleanup only; generation + identity guards are the real ones.
      controller.abort();
      controllerRef.current = null;
      createInFlightRef.current = false;
      preflightInFlightRef.current = false;
      pendingLookupRef.current = false;
      intentRef.current = null;
      createLockedRef.current = false;
      listTokenRef.current = null;
      detailTokenRef.current = null;
      reviewLockedRef.current = false;
      reviewInFlightRef.current = false;
      statusInFlightRef.current = false;
      reviewRequestRef.current = null;
      readySnapshotRef.current = null;
      conflictRef.current = false;
      downloadInFlightRef.current = false;
      openerInFlightRef.current = false;
      // A real unmount must not leak an object URL this page created.
      if (
        objectUrlRef.current !== null &&
        typeof URL.revokeObjectURL === "function"
      ) {
        URL.revokeObjectURL(objectUrlRef.current);
      }
      objectUrlRef.current = null;
    };
  }, []);

  const currentSignal = () => controllerRef.current?.signal;

  /** Selection-scoped guard: mounted plus unchanged selection generation. */
  const live = useCallback((generation: number): boolean => {
    return (
      mountedRef.current &&
      isPaperGenerationCurrent(generationRef.current, generation)
    );
  }, []);

  // --- selection-independent loads -----------------------------------------

  const loadTraders = useCallback(() => {
    // A slower earlier list must never overwrite a newer refresh.
    const token = {};
    listTokenRef.current = token;
    const latest = () => mountedRef.current && listTokenRef.current === token;
    // Soft refresh: keep prior rows on screen (no flash to empty loading).
    setTraders((prior) => ({
      ...prior,
      phase:
        prior.phase === "ready" || prior.phase === "empty"
          ? prior.phase
          : "loading",
      error: null,
    }));
    void fetchPaperTraders(currentSignal())
      .then((response) => {
        if (!latest()) {
          return;
        }
        const parsed = parsePaperTraderList(response);
        if (!parsed.ok) {
          const failure = classifyFailure(response, parsed.error);
          setTraders((prior) => ({
            phase: prior.rows.length > 0 ? "ready" : "error",
            rows: prior.rows,
            error: failure.message,
          }));
          return;
        }
        setTraders({
          phase: parsed.value.count === 0 ? "empty" : "ready",
          rows: parsed.value.traders,
          error: null,
        });
      })
      .catch((caught: unknown) => {
        if (!latest() || isAbortError(caught)) {
          return;
        }
        setTraders((prior) => ({
          phase: prior.rows.length > 0 ? "ready" : "error",
          rows: prior.rows,
          error: transportMessage(caught),
        }));
      });
  }, []);

  const loadEligible = useCallback(() => {
    // Page independence (docs/10): load confirmed strategies from DB directly.
    // PromotionDecision is history only — never the selection source.
    setEligible((prior) => ({
      ...prior,
      phase:
        prior.phase === "ready" || prior.phase === "empty"
          ? prior.phase
          : "loading",
      error: null,
    }));
    void fetchStrategies("confirmed")
      .then((list) => {
        if (!mountedRef.current) {
          return;
        }
        const rows: PaperEligibleStrategy[] = list.versions.map((version) => ({
          strategy_id: version.strategy_id,
          content_sha256: version.content_sha256,
          name: version.name,
          supporting_decisions: [],
        }));
        setEligible({
          phase: rows.length === 0 ? "empty" : "ready",
          rows,
          error: null,
        });
      })
      .catch((caught: unknown) => {
        if (!mountedRef.current || isAbortError(caught)) {
          return;
        }
        setEligible((prior) => ({
          phase: prior.rows.length > 0 ? "ready" : "error",
          rows: prior.rows,
          error: transportMessage(caught),
        }));
      });
  }, []);

  useEffect(() => {
    loadTraders();
    loadEligible();
  }, [loadTraders, loadEligible]);

  // --- selection-scoped loads ----------------------------------------------

  const loadContracts = useCallback(
    (wanted: PaperStrategySelection, generation: number) => {
      setContracts({ phase: "loading", rows: [], error: null });
      void fetchPaperContracts(wanted, currentSignal())
        .then((response) => {
          if (!live(generation)) {
            return;
          }
          const parsed = parsePaperContractList(response, wanted);
          if (!parsed.ok) {
            const failure = classifyFailure(response, parsed.error);
            setContracts({ phase: "error", rows: [], error: failure.message });
            return;
          }
          if (!paperStrategySelectionEquals(parsed.value.selection, wanted)) {
            return;
          }
          setContracts({
            phase: parsed.value.count === 0 ? "empty" : "ready",
            rows: parsed.value.contracts,
            error: null,
          });
        })
        .catch((caught: unknown) => {
          if (!live(generation) || isAbortError(caught)) {
            return;
          }
          setContracts({
            phase: "error",
            rows: [],
            error: transportMessage(caught),
          });
        });
    },
    [live],
  );

  const loadBaselines = useCallback(
    (wanted: PaperContractSelection, generation: number) => {
      setBaselines({ phase: "loading", rows: [], error: null });
      void fetchPaperBaselines(wanted, currentSignal())
        .then((response) => {
          if (!live(generation)) {
            return;
          }
          const parsed = parsePaperBaselineList(response, wanted);
          if (!parsed.ok) {
            const failure = classifyFailure(response, parsed.error);
            setBaselines({ phase: "error", rows: [], error: failure.message });
            return;
          }
          if (!paperContractSelectionEquals(parsed.value.selection, wanted)) {
            return;
          }
          setBaselines({
            phase: parsed.value.count === 0 ? "empty" : "ready",
            rows: parsed.value.baselines,
            error: null,
          });
        })
        .catch((caught: unknown) => {
          if (!live(generation) || isAbortError(caught)) {
            return;
          }
          setBaselines({
            phase: "error",
            rows: [],
            error: transportMessage(caught),
          });
        });
    },
    [live],
  );

  const loadPreflight = useCallback(
    (wanted: PaperSelection, generation: number) => {
      if (preflightInFlightRef.current) {
        return;
      }
      preflightInFlightRef.current = true;
      setPreflight({ phase: "loading", data: null, error: null });
      void postPaperProvisioningReadiness(
        buildPaperProvisioningRequest(wanted),
        currentSignal(),
      )
        .then((response) => {
          // A superseded selection's answer may never reach state, so it can
          // never re-enable the action for a selection nobody is looking at.
          if (!live(generation)) {
            return;
          }
          if (!response.ok || response.status !== 200) {
            /*
             * A known business refusal is stated in the Owner's words; an
             * unknown envelope fails closed with the same plain sentence, so
             * no raw code, schema or HTTP status ever reaches the screen.
             */
            const failure = classifyFailure(
              response,
              `HTTP ${String(response.status)}`,
            );
            setPreflight({
              phase: "error",
              data: null,
              error:
                failure.kind === "known" ? failure.message : PREFLIGHT_UNREADABLE,
            });
            return;
          }
          const parsed = parsePaperProvisioningReadiness(response.body, wanted);
          if (!parsed.ok) {
            setPreflight({
              phase: "error",
              data: null,
              error: PREFLIGHT_UNREADABLE,
            });
            return;
          }
          setPreflight({ phase: "ready", data: parsed.value, error: null });
        })
        .catch((caught: unknown) => {
          if (!live(generation) || isAbortError(caught)) {
            return;
          }
          setPreflight({
            phase: "error",
            data: null,
            error: transportMessage(caught),
          });
        })
        .finally(() => {
          preflightInFlightRef.current = false;
        });
    },
    [live],
  );

  // --- selection transitions ------------------------------------------------

  const resetCreateIntent = () => {
    createRequestRef.current = null;
    createInFlightRef.current = false;
    pendingLookupRef.current = false;
    intentRef.current = null;
    createLockedRef.current = false;
    setConfirmOpen(false);
    setCreate({ phase: "idle", error: null, detail: null });
  };

  /*
   * Locked while an intent has no known terminal outcome. Each handler refuses
   * on its own; the disabled attribute is only the second layer.
   */
  const createLocked =
    create.phase === "submitting" ||
    create.phase === "recovering" ||
    create.phase === "unknown";

  const selectStrategy = (row: PaperEligibleStrategy) => {
    if (createLockedRef.current || createLocked) {
      return;
    }
    generationRef.current += 1;
    // A superseded preflight must not block the new selection's own.
    preflightInFlightRef.current = false;
    const generation = generationRef.current;
    setStrategy(row);
    setContract(null);
    setBaseline(null);
    setContracts(idleList());
    setBaselines(idleList());
    setPreflight({ phase: "idle", data: null, error: null });
    resetCreateIntent();
    loadContracts(
      { strategy_id: row.strategy_id, content_sha256: row.content_sha256 },
      generation,
    );
  };

  const selectContract = (row: PaperContractRow) => {
    if (!strategy || createLockedRef.current || createLocked) {
      return;
    }
    generationRef.current += 1;
    preflightInFlightRef.current = false;
    const generation = generationRef.current;
    setContract(row);
    setBaseline(null);
    setBaselines(idleList());
    setPreflight({ phase: "idle", data: null, error: null });
    resetCreateIntent();
    loadBaselines(
      {
        strategy_id: strategy.strategy_id,
        content_sha256: strategy.content_sha256,
        contract_id: row.contract_id,
      },
      generation,
    );
  };

  const selectBaseline = (row: PaperBaseline) => {
    if (!strategy || !contract || createLockedRef.current || createLocked) {
      return;
    }
    generationRef.current += 1;
    preflightInFlightRef.current = false;
    const generation = generationRef.current;
    setBaseline(row);
    setPreflight({ phase: "idle", data: null, error: null });
    resetCreateIntent();
    loadPreflight(
      {
        strategy_id: strategy.strategy_id,
        content_sha256: strategy.content_sha256,
        contract_id: contract.contract_id,
        baseline_run_id: row.run_id,
        baseline_result_sha256: row.result_sha256,
      },
      generation,
    );
  };

  const selection: PaperSelection | null = useMemo(() => {
    if (!strategy || !contract || !baseline) {
      return null;
    }
    return {
      strategy_id: strategy.strategy_id,
      content_sha256: strategy.content_sha256,
      contract_id: contract.contract_id,
      baseline_run_id: baseline.run_id,
      baseline_result_sha256: baseline.result_sha256,
    };
  }, [strategy, contract, baseline]);

  const recheckPreflight = () => {
    if (selection && !createLockedRef.current && !createLocked) {
      loadPreflight(selection, generationRef.current);
    }
  };

  // --- create / recovery ----------------------------------------------------

  /** Only a response for the exact locked intent may reach state. */
  const intentCurrent = useCallback(
    (request: PaperTraderCreateRequest): boolean => {
      const intent = intentRef.current;
      return (
        mountedRef.current &&
        intent !== null &&
        intent.request === request &&
        intent.request.request_id === request.request_id &&
        intent.generation === generationRef.current
      );
    },
    [],
  );

  const adoptTrader = useCallback(
    (trader: PaperTrader) => {
      intentRef.current = null;
      createLockedRef.current = false;
      setCreate({ phase: "created", error: null, detail: null });
      setConfirmOpen(false);
      // The confirm sheet closes on success, so without this the owner is
      // left looking at the list wondering whether it went through.
      showSuccess("已建立交易員");
      setTraders((prior) => ({
        phase: "ready",
        rows: prior.rows.some((item) => item.trader_id === trader.trader_id)
          ? prior.rows
          : [...prior.rows, trader],
        error: null,
      }));
      setActiveTab(trader.trader_id);
      loadTraders();
    },
    [loadTraders, showSuccess],
  );

  const lookupRequest = useCallback(
    (request: PaperTraderCreateRequest) => {
      if (createInFlightRef.current) {
        return;
      }
      createInFlightRef.current = true;
      setCreate({ phase: "recovering", error: null, detail: null });
      void fetchPaperTraderRequest(request.request_id, currentSignal())
        .then((response) => {
          if (!intentCurrent(request)) {
            return;
          }
          const parsed = parsePaperTraderRequestStatus(response, request);
          if (parsed.ok) {
            adoptTrader(parsed.value.trader);
            return;
          }
          const failure = classifyFailure(response, parsed.error);
          setConfirmOpen(false);
          if (failure.kind === "known" && failure.code === "request_not_found") {
            // Backend positively confirms nothing was created: only then may
            // the selection unlock, and only the same request identity may be
            // resent — never a fresh one.
            intentRef.current = null;
            createLockedRef.current = false;
            setCreate({ phase: "absent", error: failure.message, detail: null });
            return;
          }
          setCreate({
            phase: "unknown",
            error: "查唔到今次請求嘅結果。",
            detail: failure.kind === "unknown" ? failure.message : null,
          });
        })
        .catch((caught: unknown) => {
          if (!intentCurrent(request) || isAbortError(caught)) {
            return;
          }
          setConfirmOpen(false);
          setCreate({
            phase: "unknown",
            error: "查唔到今次請求嘅結果。",
            detail: transportMessage(caught),
          });
        })
        .finally(() => {
          createInFlightRef.current = false;
        });
    },
    [adoptTrader, intentCurrent],
  );

  const sendCreate = useCallback(
    (request: PaperTraderCreateRequest) => {
      if (createInFlightRef.current) {
        return;
      }
      createInFlightRef.current = true;
      pendingLookupRef.current = false;
      // Locked synchronously, before the POST leaves and before any render.
      createLockedRef.current = true;
      intentRef.current = { request, generation: generationRef.current };
      setCreate({ phase: "submitting", error: null, detail: null });
      void postPaperTrader(request, currentSignal())
        .then((response) => {
          if (!intentCurrent(request)) {
            return;
          }
          const parsed = parsePaperCreatedTrader(response, request);
          if (parsed.ok) {
            adoptTrader(parsed.value.trader);
            return;
          }
          const failure = classifyFailure(response, parsed.error);
          setConfirmOpen(false);
          if (failure.kind === "known") {
            // A known business error is a definitive no-create, so the
            // selection unlocks; the same request id stays reusable.
            intentRef.current = null;
            createLockedRef.current = false;
            if (failure.code === "activation_not_authorized") {
              setCreate({
                phase: "not-activated",
                error: failure.message,
                detail: null,
              });
              return;
            }
            setCreate({ phase: "error", error: failure.message, detail: null });
            return;
          }
          // Outcome unknown: never retry the POST, look the same request up.
          pendingLookupRef.current = true;
          setCreate({
            phase: "unknown",
            error: UNKNOWN_CREATE_TITLE,
            detail: failure.message,
          });
        })
        .catch((caught: unknown) => {
          if (!intentCurrent(request) || isAbortError(caught)) {
            return;
          }
          setConfirmOpen(false);
          pendingLookupRef.current = true;
          setCreate({
            phase: "unknown",
            error: UNKNOWN_CREATE_TITLE,
            detail: transportMessage(caught),
          });
        })
        .finally(() => {
          createInFlightRef.current = false;
          if (pendingLookupRef.current && intentCurrent(request)) {
            pendingLookupRef.current = false;
            lookupRequest(request);
          }
        });
    },
    [adoptTrader, intentCurrent, lookupRequest],
  );

  // Creating a trader is a write; the transport layer refuses it while the
  // service is unreachable, so the button says so up front.
  const offline = useWritesBlocked();

  const confirmCreate = () => {
    // `sendCreate` owns the single in-flight authority; duplicating the guard
    // here would hide a regression in it.
    if (!selection || create.phase === "created") {
      return;
    }
    if (createRequestRef.current === null) {
      createRequestRef.current = buildPaperCreateRequest(
        crypto.randomUUID(),
        selection,
      );
    }
    sendCreate(createRequestRef.current);
  };

  const retryLookup = () => {
    if (createRequestRef.current) {
      lookupRequest(createRequestRef.current);
    }
  };

  const resendSameRequest = () => {
    if (createRequestRef.current) {
      sendCreate(createRequestRef.current);
    }
  };

  // --- Stage B ledger origin ------------------------------------------------

  const loadLedger = useCallback(
    (trader: PaperTrader, token: object) => {
      const traderId = trader.trader_id;
      const latest = () =>
        mountedRef.current &&
        detailTokenRef.current === token &&
        activeTabRef.current === traderId;
      setLedger({ phase: "loading", data: null, error: null });
      void fetchPaperLedgerOrigin(traderId, currentSignal())
        .then((response) => {
          if (!latest()) {
            return;
          }
          if (response.ok && response.status === 200) {
            const parsed = parsePaperLedgerOrigin(response.body, {
              trader_id: traderId,
            });
            if (!parsed.ok) {
              setLedger({
                phase: "error",
                data: null,
                error: LEDGER_UNREADABLE,
              });
              return;
            }
            if (!ledgerMatchesTrader(parsed.value, trader)) {
              setLedger({
                phase: "error",
                data: null,
                error: LEDGER_IDENTITY_DRIFT,
              });
              return;
            }
            setLedger({ phase: "ready", data: parsed.value, error: null });
            return;
          }
          const failure = classifyReviewFailure(response, [
            { mode: "pre_acceptance", request_id: null },
          ]);
          setLedger({
            phase: "error",
            data: null,
            error:
              failure.kind === "known" ? failure.message : LEDGER_UNREADABLE,
          });
        })
        .catch((caught: unknown) => {
          if (!latest() || isAbortError(caught)) {
            return;
          }
          setLedger({
            phase: "error",
            data: null,
            error: LEDGER_UNREACHABLE,
          });
        });
    },
    [],
  );

  // --- trader detail --------------------------------------------------------

  useEffect(() => {
    activeTabRef.current = activeTab;
    // §4.9: another trader invalidates any download or copy presentation.
    setDownload(IDLE_DOWNLOAD);
    setOpener(IDLE_OPENER);
    if (activeTab === "overview" || activeTab === "new") {
      detailTokenRef.current = null;
      setDetail({ phase: "idle", data: null, error: null });
      setLedger({ phase: "idle", data: null, error: null });
      return;
    }
    const traderId = activeTab;
    /*
     * Each opened tab owns its own request sequence: a slower earlier tab must
     * not land on the tab that is open now, neither on success nor on error.
     */
    const token = {};
    detailTokenRef.current = token;
    const latest = () =>
      mountedRef.current &&
      detailTokenRef.current === token &&
      activeTabRef.current === traderId;
    setDetail({ phase: "loading", data: null, error: null });
    setLedger({ phase: "idle", data: null, error: null });
    void fetchPaperTrader(traderId, controllerRef.current?.signal)
      .then((response) => {
        if (!latest()) {
          return;
        }
        const parsed = parsePaperTraderDetail(response, traderId);
        if (!parsed.ok) {
          const failure = classifyFailure(response, parsed.error);
          setDetail({ phase: "error", data: null, error: failure.message });
          return;
        }
        setDetail({ phase: "ready", data: parsed.value, error: null });
        // The ledger is only requested once an exact trader record exists.
        loadLedger(parsed.value, token);
      })
      .catch((caught: unknown) => {
        if (!latest() || isAbortError(caught)) {
          return;
        }
        setDetail({
          phase: "error",
          data: null,
          error: transportMessage(caught),
        });
      });
  }, [activeTab, loadLedger]);

  // --- Stage B review request state machine ---------------------------------

  const reviewLocked = isPaperReviewLocked(review.phase);

  /*
   * Production mounts this page inside `<StrictMode>`, so React deliberately
   * replays setup → cleanup → setup. The mount cleanup clears the review
   * identity refs, which is exactly right for a real unmount, but the second
   * setup cannot rebuild them: `bootstrapRef` has already advanced and the
   * recovered identity now only lives in render state. Re-establishing the
   * mirror after every committed change keeps the synchronous authority and
   * the rendered truth in step — no second URL authority, no new identity, no
   * retry, and after a real unmount nothing commits again so the refs stay
   * cleared.
   */
  useEffect(() => {
    reviewRequestRef.current = review.request;
    reviewLockedRef.current = isPaperReviewLocked(review.phase);
  }, [review.phase, review.request]);

  const applyReviewStatus = useCallback(
    (
      response: PaperHttpResult,
      trader: PaperTrader,
      request: PaperReviewCreateRequest,
      allowedStatuses: readonly number[],
    ) => {
      if (response.ok && allowedStatuses.includes(response.status)) {
        const parsed = parsePaperReviewStatus(response.body, {
          request_id: request.request_id,
          trader_id: trader.trader_id,
          baseline_run_id: trader.baseline.run_id,
        });
        if (!parsed.ok) {
          reviewLockedRef.current = false;
          setReview({
            phase: "unknown",
            traderId: trader.trader_id,
            request,
            status: null,
            failure: null,
            error: REVIEW_UNKNOWN_TITLE,
            detail: parsed.error,
          });
          return;
        }
        const status = parsed.value;
        const phase: PaperReviewPhase =
          status.status === "preparing"
            ? "preparing"
            : status.status === "ready"
              ? "ready"
              : "failed";
        reviewLockedRef.current = phase === "preparing";
        setReview({
          phase,
          traderId: trader.trader_id,
          request,
          status,
          failure: null,
          error: null,
          detail: null,
        });
        return;
      }
      /*
       * §10 gives exactly two shapes a failure for our own request may take: a
       * pre-acceptance failure with no snapshot at all, or an accepted failure
       * whose snapshot id only the backend knows. Anything else is unknown.
       */
      const failure = classifyReviewFailure(response, [
        { mode: "pre_acceptance", request_id: request.request_id },
        { mode: "accepted_assigned", request_id: request.request_id },
      ]);
      reviewLockedRef.current = false;
      conflictRef.current =
        failure.kind === "known" && failure.code === "request_id_conflict";
      setReview({
        phase: failure.kind === "known" ? "failed" : "unknown",
        traderId: trader.trader_id,
        request,
        status: null,
        failure: failure.kind === "known" ? failure.error : null,
        error:
          failure.kind === "known" ? failure.message : REVIEW_UNKNOWN_TITLE,
        detail: failure.kind === "unknown" ? failure.message : null,
      });
    },
    [],
  );

  const reviewCurrent = useCallback(
    (trader: PaperTrader, request: PaperReviewCreateRequest): boolean =>
      mountedRef.current &&
      reviewRequestRef.current === request &&
      activeTabRef.current === trader.trader_id,
    [],
  );

  const sendReviewSnapshot = useCallback(
    (trader: PaperTrader, request: PaperReviewCreateRequest) => {
      if (reviewInFlightRef.current) {
        return;
      }
      reviewInFlightRef.current = true;
      reviewLockedRef.current = true;
      void postPaperReviewSnapshot(trader.trader_id, request, currentSignal())
        .then((response) => {
          if (!reviewCurrent(trader, request)) {
            return;
          }
          applyReviewStatus(response, trader, request, [200, 201, 202]);
        })
        .catch((caught: unknown) => {
          if (!reviewCurrent(trader, request) || isAbortError(caught)) {
            return;
          }
          // Unknown outcome: never a second POST and never a second UUID.
          reviewLockedRef.current = false;
          setReview({
            phase: "unknown",
            traderId: trader.trader_id,
            request,
            status: null,
            failure: null,
            error: REVIEW_UNKNOWN_TITLE,
            detail: transportMessage(caught),
          });
        })
        .finally(() => {
          reviewInFlightRef.current = false;
        });
    },
    [applyReviewStatus, reviewCurrent],
  );

  const readReviewStatus = useCallback(
    (trader: PaperTrader, request: PaperReviewCreateRequest) => {
      if (statusInFlightRef.current) {
        return;
      }
      statusInFlightRef.current = true;
      void fetchPaperReviewRequest(request.request_id, currentSignal())
        .then((response) => {
          if (!reviewCurrent(trader, request)) {
            return;
          }
          applyReviewStatus(response, trader, request, [200]);
        })
        .catch((caught: unknown) => {
          if (!reviewCurrent(trader, request) || isAbortError(caught)) {
            return;
          }
          reviewLockedRef.current = false;
          setReview({
            phase: "unknown",
            traderId: trader.trader_id,
            request,
            status: null,
            failure: null,
            error: REVIEW_UNKNOWN_TITLE,
            detail: transportMessage(caught),
          });
        })
        .finally(() => {
          statusInFlightRef.current = false;
        });
    },
    [applyReviewStatus, reviewCurrent],
  );

  /*
   * §7.1: one canonical UUID4, written into the URL first, and only POSTed
   * once that URL identity is observable. The synchronous lock is taken before
   * anything else so a same-batch second click cannot mint a second request.
   */
  const startReviewSnapshot = () => {
    if (reviewLockedRef.current || reviewInFlightRef.current) {
      return;
    }
    const trader = detail.data;
    if (!trader || detail.phase !== "ready" || detailMismatch) {
      return;
    }
    if (ledger.phase !== "ready" || ledger.data === null) {
      return;
    }
    const built = buildPaperReviewCreateRequest(crypto.randomUUID());
    if (!built.ok) {
      return;
    }
    const identity = {
      trader_id: trader.trader_id,
      request_id: built.value.request_id,
    };
    const nextParams = writePaperReviewUrlIdentity(params, identity);
    if (nextParams === null) {
      return;
    }
    reviewLockedRef.current = true;
    reviewRequestRef.current = built.value;
    readySnapshotRef.current = null;
    conflictRef.current = false;
    setDownload(IDLE_DOWNLOAD);
    setOpener(IDLE_OPENER);
    setDialogOpen(true);
    setReview({
      phase: "submitting",
      traderId: trader.trader_id,
      request: built.value,
      status: null,
      failure: null,
      error: null,
      detail: null,
    });
    setParams(nextParams);
  };

  // The POST waits until the URL identity is observable (§7.1 step 4).
  useEffect(() => {
    if (review.phase !== "submitting") {
      return;
    }
    const request = review.request;
    const trader = detail.data;
    if (!request || !trader || trader.trader_id !== review.traderId) {
      return;
    }
    if (
      !paperReviewUrlIdentityMatches(params, {
        trader_id: trader.trader_id,
        request_id: request.request_id,
      })
    ) {
      return;
    }
    sendReviewSnapshot(trader, request);
  }, [review.phase, review.request, review.traderId, params, detail.data, sendReviewSnapshot]);

  // §15.1 reload recovery: read both halves once, never mint a new identity.
  useEffect(() => {
    if (bootstrapRef.current) {
      return;
    }
    bootstrapRef.current = true;
    const identity = readPaperReviewUrlIdentity(params);
    if (identity === null) {
      return;
    }
    const built = buildPaperReviewCreateRequest(identity.request_id);
    if (!built.ok) {
      return;
    }
    reviewLockedRef.current = true;
    reviewRequestRef.current = built.value;
    setActiveTab(identity.trader_id);
    setReview({
      phase: "recovering",
      traderId: identity.trader_id,
      request: built.value,
      status: null,
      failure: null,
      error: null,
      detail: null,
    });
  }, [params]);

  // Recovery only reads status, and only after the exact trader is loaded.
  useEffect(() => {
    if (review.phase !== "recovering" || recoveryStartedRef.current) {
      return;
    }
    const request = review.request;
    const trader = detail.data;
    if (
      !request ||
      !trader ||
      detail.phase !== "ready" ||
      trader.trader_id !== review.traderId
    ) {
      return;
    }
    recoveryStartedRef.current = true;
    readReviewStatus(trader, request);
  }, [review.phase, review.request, review.traderId, detail.phase, detail.data, readReviewStatus]);

  // --- Stage B download and Terminal opener ---------------------------------

  /*
   * The snapshot the Owner may act on right now. A response for any other
   * snapshot — an older request, another trader, a superseded status — is
   * dropped before it can build a Blob or touch the clipboard.
   */
  useEffect(() => {
    readySnapshotRef.current =
      review.phase === "ready" && review.status !== null
        ? review.status.snapshot_id
        : null;
  }, [review.phase, review.status]);

  const readySnapshot =
    review.phase === "ready" && review.status?.ready ? review.status : null;

  const artifactCurrent = useCallback(
    (
      trader: PaperTrader,
      request: PaperReviewCreateRequest,
      snapshotId: string,
    ): boolean =>
      reviewCurrent(trader, request) && readySnapshotRef.current === snapshotId,
    [reviewCurrent],
  );

  /**
   * §7 steps 10–12. Only verified bytes reach this function, and it owns the
   * whole browser side effect: exactly one Blob, one object URL, one click and
   * one revoke.
   */
  const saveVerifiedArtifact = (
    bytes: Uint8Array,
    filename: string,
  ): { ok: true } | { ok: false; detail: string } => {
    if (
      typeof URL.createObjectURL !== "function" ||
      typeof URL.revokeObjectURL !== "function"
    ) {
      return { ok: false, detail: "createObjectURL 唔存在" };
    }
    const blob = new Blob([bytes], { type: "application/zip" });
    let url: string;
    try {
      url = URL.createObjectURL(blob);
    } catch (caught: unknown) {
      return { ok: false, detail: transportMessage(caught) };
    }
    /*
     * Correction C: from here on this page owns the URL and the anchor. Any
     * step may throw — a browser can refuse the click, the removal or the
     * revoke — so every cleanup is still attempted, the ref is always cleared,
     * nothing escapes into the React event, and a throw is reported as a
     * failure rather than a download.
     */
    objectUrlRef.current = url;
    let anchor: HTMLAnchorElement | null = null;
    let failure: string | null = null;
    try {
      anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.rel = "noopener";
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
    } catch (caught: unknown) {
      failure = transportMessage(caught);
    } finally {
      try {
        anchor?.remove();
      } catch (caught: unknown) {
        failure ??= transportMessage(caught);
      }
      try {
        // Exactly one attempt; a refusal is never reported as a real revoke.
        URL.revokeObjectURL(url);
      } catch (caught: unknown) {
        failure ??= transportMessage(caught);
      }
      objectUrlRef.current = null;
    }
    return failure === null ? { ok: true } : { ok: false, detail: failure };
  };

  const startReviewDownload = () => {
    if (downloadInFlightRef.current) {
      return;
    }
    const trader = detail.data;
    const request = reviewRequestRef.current;
    const status = readySnapshot;
    if (!trader || !request || status === null || status.ready === null) {
      return;
    }
    const snapshotId = status.snapshot_id;
    const ready = status.ready;
    if (readySnapshotRef.current !== snapshotId) {
      return;
    }
    const expectedPaths = paperReviewMemberPaths(trader.baseline.run_id);
    if (expectedPaths === null) {
      return;
    }
    downloadInFlightRef.current = true;
    setDownload({
      phase: "working",
      filename: null,
      error: null,
      detail: null,
    });
    void fetchPaperReviewDownload(snapshotId, currentSignal())
      .then(async (response) => {
        if (response.status !== 200) {
          /*
           * A refusal is an exact `paper_review_error.v1` for this very
           * request and snapshot; the binary pipeline never runs, so no Blob,
           * no object URL and no click can exist on this path.
           */
          if (!artifactCurrent(trader, request, snapshotId)) {
            return;
          }
          const failure = classifyReviewFailure(response, [
            {
              mode: "accepted_known",
              request_id: request.request_id,
              snapshot_id: snapshotId,
            },
          ]);
          setDownload({
            phase: "error",
            filename: null,
            error:
              failure.kind === "known" ? failure.message : DOWNLOAD_REFUSED,
            detail:
              failure.kind === "unknown"
                ? failure.message
                : `HTTP ${String(response.status)}`,
          });
          return;
        }
        const verdict = await verifyPaperReviewArtifact({
          status: response.status,
          contentType: response.contentType,
          contentDisposition: response.contentDisposition,
          bytes: response.bytes,
          ready,
          expectedPaths,
        });
        // Identity is re-checked after the await: a superseded snapshot must
        // never produce a Blob, a URL or a click.
        if (!artifactCurrent(trader, request, snapshotId)) {
          return;
        }
        if (!verdict.ok) {
          setDownload({
            phase: "error",
            filename: null,
            error: verdict.error,
            detail: verdict.detail,
          });
          return;
        }
        const saved = saveVerifiedArtifact(
          verdict.value.bytes,
          verdict.value.filename,
        );
        if (!saved.ok) {
          setDownload({
            phase: "error",
            filename: null,
            error: DOWNLOAD_UNAVAILABLE,
            detail: saved.detail,
          });
          return;
        }
        setDownload({
          phase: "saved",
          filename: verdict.value.filename,
          error: null,
          detail: null,
        });
      })
      .catch((caught: unknown) => {
        if (
          !artifactCurrent(trader, request, snapshotId) ||
          isAbortError(caught)
        ) {
          return;
        }
        setDownload({
          phase: "error",
          filename: null,
          error: "偏離包下載唔到。",
          detail: transportMessage(caught),
        });
      })
      .finally(() => {
        downloadInFlightRef.current = false;
      });
  };

  const startReviewOpenerCopy = () => {
    if (openerInFlightRef.current) {
      return;
    }
    const trader = detail.data;
    const request = reviewRequestRef.current;
    const status = readySnapshot;
    if (!trader || !request || status === null || status.ready === null) {
      return;
    }
    const snapshotId = status.snapshot_id;
    const ready = status.ready;
    if (readySnapshotRef.current !== snapshotId) {
      return;
    }
    openerInFlightRef.current = true;
    setOpener({ phase: "working", error: null, detail: null });
    void fetchPaperReviewTerminalOpener(snapshotId, currentSignal())
      .then(async (response) => {
        if (!response.ok || response.status !== 200) {
          if (!artifactCurrent(trader, request, snapshotId)) {
            return;
          }
          // Same closed shape as the download refusal: this request, this
          // snapshot, nothing guessed. The clipboard is never touched here.
          const failure = classifyReviewFailure(response, [
            {
              mode: "accepted_known",
              request_id: request.request_id,
              snapshot_id: snapshotId,
            },
          ]);
          setOpener({
            phase: "error",
            error:
              failure.kind === "known" ? failure.message : OPENER_UNREADABLE,
            detail:
              failure.kind === "unknown"
                ? failure.message
                : `HTTP ${String(response.status)}`,
          });
          return;
        }
        const parsed = parsePaperReviewTerminalOpenerClaim(response.body, {
          snapshot_id: snapshotId,
        });
        if (!parsed.ok) {
          if (!artifactCurrent(trader, request, snapshotId)) {
            return;
          }
          setOpener({
            phase: "error",
            error: OPENER_UNREADABLE,
            detail: parsed.error,
          });
          return;
        }
        const verdict = await verifyPaperReviewOpener({
          claim: parsed.value,
          ready,
          snapshotId,
        });
        if (!artifactCurrent(trader, request, snapshotId)) {
          return;
        }
        if (!verdict.ok) {
          setOpener({
            phase: "error",
            error: verdict.error,
            detail: verdict.detail,
          });
          return;
        }
        const clipboard = navigator.clipboard as Clipboard | undefined;
        if (typeof clipboard?.writeText !== "function") {
          setOpener({
            phase: "error",
            error: COPY_UNAVAILABLE,
            detail: null,
          });
          return;
        }
        try {
          // Exactly the persisted backend text, written once.
          await clipboard.writeText(verdict.value.text);
        } catch (caught: unknown) {
          if (artifactCurrent(trader, request, snapshotId)) {
            setOpener({
              phase: "error",
              error: COPY_REFUSED,
              detail: transportMessage(caught),
            });
          }
          return;
        }
        if (!artifactCurrent(trader, request, snapshotId)) {
          return;
        }
        setOpener({ phase: "copied", error: null, detail: null });
      })
      .catch((caught: unknown) => {
        if (
          !artifactCurrent(trader, request, snapshotId) ||
          isAbortError(caught)
        ) {
          return;
        }
        setOpener({
          phase: "error",
          error: OPENER_UNREADABLE,
          detail: transportMessage(caught),
        });
      })
      .finally(() => {
        openerInFlightRef.current = false;
      });
  };

  const openReviewDialog = () => {
    setDialogOpen(true);
  };

  /** §4.2: closing changes presentation only. */
  const closeReviewDialog = () => {
    setDialogOpen(false);
  };

  const recheckReviewStatus = () => {
    const request = reviewRequestRef.current;
    const trader = detail.data;
    // A conflicting identity is never re-read, not even once.
    if (!request || !trader || statusInFlightRef.current || conflictRef.current) {
      return;
    }
    readReviewStatus(trader, request);
  };

  /** §15.2: a brand new snapshot needs a fresh UUID and a fresh URL. */
  const startNewReviewSnapshot = () => {
    /*
     * Correction A: the rendered `disabled` attribute settles only on the next
     * render, so a click dispatched in the same batch as a download or copy
     * would still see an enabled button. These synchronous refs are the
     * authority; the attribute stays as the second, visual layer.
     */
    if (
      reviewLockedRef.current ||
      downloadInFlightRef.current ||
      openerInFlightRef.current
    ) {
      return;
    }
    reviewRequestRef.current = null;
    recoveryStartedRef.current = false;
    setReview(IDLE_REVIEW);
    startReviewSnapshot();
  };

  /** §7.2: the active trader tab is locked while a request is unresolved. */
  const selectTab = (next: string) => {
    if (reviewLockedRef.current && next !== activeTabRef.current) {
      return;
    }
    setActiveTab(next);
  };

  const listRecord = useMemo(
    () => traders.rows.find((row) => row.trader_id === activeTab) ?? null,
    [traders.rows, activeTab],
  );

  const detailMismatch =
    detail.phase === "ready" &&
    detail.data !== null &&
    listRecord !== null &&
    !paperTraderEquals(detail.data, listRecord);

  /*
   * A review only belongs to the trader it was created for. Another tab shows
   * no banner, no dialog and no download or copy control for it.
   */
  const activeReview =
    review.phase !== "idle" &&
    detail.data !== null &&
    review.traderId === detail.data.trader_id
      ? review
      : null;

  /*
   * A terminal failure reaches the screen either inside a `failed` status body
   * or as a standalone HTTP error for this request. Both are the same typed
   * object, so the dialog reads one value and never invents progress.
   */
  const reviewError: PaperReviewError | null =
    activeReview === null
      ? null
      : (activeReview.status?.error ?? activeReview.failure);

  /*
   * §9.5: a conflicting request id is bound to somebody else's intent. The
   * snapshot in that body is never adopted, never rechecked and never turned
   * into a ready or preparing state — only an explicit new request may follow.
   */
  const reviewConflict = reviewError?.code === "request_id_conflict";

  // --- gating ---------------------------------------------------------------

  /*
   * The preflight only counts for the selection actually on screen. Identity
   * equality is checked here as well as by the generation guard: a stale answer
   * must never be the thing that enables creation.
   */
  const preflightData =
    preflight.phase === "ready" && preflight.data !== null
      ? preflight.data
      : null;
  const preflightCurrent =
    preflightData !== null &&
    selection !== null &&
    paperSelectionEquals(preflightData.selection, selection);
  const baselineIntegrityReady =
    preflightData?.runtime_readiness.checks.find(
      (item) => item.key === "baseline_integrity",
    )?.status === "ready";
  /*
   * §10.4: authorization is the only gate for a provision-only create. IB,
   * calendar and Telegram are still shown exactly as they are, but they no
   * longer block an authorized create on their own — and they are never
   * painted as ready.
   */
  const provisionAuthorized =
    preflightCurrent &&
    preflightData.can_provision &&
    preflightData.authorization.state === "armed" &&
    baselineIntegrityReady;

  const missing: string[] = [];
  if (!strategy) {
    missing.push("策略版本");
  } else if (!contract) {
    missing.push("合約");
  } else if (!baseline) {
    missing.push("對照基準");
  } else if (!provisionAuthorized) {
    missing.push("建立授權");
  }

  const createBusy =
    create.phase === "submitting" || create.phase === "recovering";
  const createDisabled =
    missing.length > 0 || createBusy || create.phase === "created";

  return (
    <>
      <PageHeader
        title="模擬盤"
        actions={<span className="chip chip--warn">模擬引擎未啟用</span>}
        info={
          <>
            模擬盤攞 IB
            行情做輸入，成交、虛擬帳戶、持倉同盈虧全部由本系統自己計，唔會向任何
            IB 帳戶發單。
            <br />
            呢一批只做「建立交易員」嘅接線：鎖定策略版本、合約、對照基準同一個獨立帳戶起點。模擬引擎、實時價格、模擬成交、持倉同盈虧全部未啟用，所以呢頁唔會顯示任何未真正發生過嘅數字。
          </>
        }
      />

      <PageTabsPortal>
        <div className="paper-tabs" role="tablist" aria-label="模擬盤分頁">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "overview"}
          disabled={reviewLocked && activeTab !== "overview"}
          className={`paper-tab${activeTab === "overview" ? " paper-tab--active" : ""}`}
          onClick={() => selectTab("overview")}
        >
          總覽
        </button>
        {traders.rows.map((row) => (
          <button
            key={row.trader_id}
            type="button"
            role="tab"
            aria-selected={activeTab === row.trader_id}
            disabled={reviewLocked && activeTab !== row.trader_id}
            className={`paper-tab${activeTab === row.trader_id ? " paper-tab--active" : ""}`}
            onClick={() => selectTab(row.trader_id)}
          >
            {row.strategy.strategy_id} · {row.contract_id}
          </button>
        ))}
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "new"}
          disabled={reviewLocked && activeTab !== "new"}
          className={`paper-tab paper-tab--add${activeTab === "new" ? " paper-tab--active" : ""}`}
          onClick={() => selectTab("new")}
        >
          ＋ 新增交易員
          </button>
        </div>
      </PageTabsPortal>

      {create.phase === "created" ? (
        <p className="paper-success">
          已建立 · 新分頁已經加咗喺上面。{PROVISIONED_TRUTH}。
        </p>
      ) : null}

      {activeTab === "overview" ? (
        <section className="paper-panel" aria-label="模擬盤總覽">
          {traders.phase === "error" ? (
            <PaperError title="交易員清單讀唔到。" detail={traders.error} />
          ) : null}
          <PaperFleetBoard
            onOpenTrader={(traderId) => {
              selectTab(traderId);
            }}
          />
          {/* Keep legacy empty-state copy discoverable for Stage A tests when fleet is empty */}
          {traders.phase === "empty" ? (
            <p className="paper-hint" hidden>
              {ENGINE_NOT_ENABLED} {PROVISIONED_TRUTH}
            </p>
          ) : null}
        </section>
      ) : null}

      {activeTab === "new" ? (
        <section className="paper-panel" aria-label="新增交易員">
          <div className="panel__head">
            <h2 className="paper-step">1. 揀一個已確認策略版本</h2>
            <InfoButton label="策略版本點揀" align="end">
              只列出喺 策略工作台 確認咗嘅版本。揀唔到嘢？即係仲未有版本——去策略工作台行完「量化確認」先。版本一經揀定就會鎖死喺呢個交易員身上。
            </InfoButton>
          </div>
          {eligible.phase === "loading" ? <p>載入緊…</p> : null}
          {eligible.phase === "error" ? (
            <PaperError
              title="已確認策略列表讀唔到。"
              detail={eligible.error}
            />
          ) : null}
          {eligible.phase === "empty" ? (
            <div className="paper-empty">
              <p>而家資料庫入面未有已確認策略，所以暫時冇得揀。</p>
              <p>
                呢頁唔會因為其他頁未批准而鎖死——只係後端暫時冇可用策略選項。
                確認策略之後再返嚟呢度。
              </p>
              <Link
                className="paper-button paper-button--ghost"
                to="/strategies"
              >
                去策略工作台
              </Link>
            </div>
          ) : null}
          {eligible.phase === "ready" ? (
            <ul className="paper-options">
              {eligible.rows.map((row) => {
                const chosen = strategy?.strategy_id === row.strategy_id;
                const hinted = highlightStrategy === row.strategy_id;
                const label = row.name?.trim() || row.strategy_id;
                return (
                  <li key={`${row.strategy_id}@${row.content_sha256}`}>
                    <button
                      type="button"
                      aria-pressed={chosen}
                      disabled={createLocked}
                      className={`paper-option${chosen ? " paper-option--chosen" : ""}`}
                      onClick={() => selectStrategy(row)}
                    >
                      <span className="paper-option__main">
                        {label}
                        {hinted ? (
                          <span className="paper-badge">你啱啱睇嗰個</span>
                        ) : null}
                      </span>
                      <span className="paper-option__meta">
                        {row.strategy_id} · 版本內容識別碼{" "}
                        {shortSha(row.content_sha256)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}

          <h2 className="paper-step">2. 揀合約</h2>
          {!strategy ? (
            <p className="paper-hint">揀咗策略版本先會列出合約。</p>
          ) : null}
          {strategy && contracts.phase === "loading" ? <p>載入緊…</p> : null}
          {strategy && contracts.phase === "error" ? (
            <PaperError title="合約清單讀唔到。" detail={contracts.error} />
          ) : null}
          {strategy && contracts.phase === "empty" ? (
            <p className="paper-hint">
              後端暫時冇呢個策略嘅可用對照基準（需要先有完成嘅回測結果）。
              你可以去回測頁跑一次，或者揀另一個策略。
            </p>
          ) : null}
          {strategy && contracts.phase === "ready" ? (
            <ul className="paper-options">
              {contracts.rows.map((row) => {
                const chosen = contract?.contract_id === row.contract_id;
                return (
                  <li key={row.contract_id}>
                    <button
                      type="button"
                      aria-pressed={chosen}
                      disabled={createLocked}
                      className={`paper-option${chosen ? " paper-option--chosen" : ""}`}
                      onClick={() => selectContract(row)}
                    >
                      <span className="paper-option__main">
                        {row.symbol} · {row.display_name}
                      </span>
                      <span className="paper-option__meta">
                        到期合約 {row.contract_id}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}

          <div className="panel__head">
            <h2 className="paper-step">3. 親手揀一個對照基準</h2>
            <InfoButton label="對照基準做咩" align="end">
              對照基準係用嚟答「贏咗大市定係輸咗」。系統唔會幫你自動揀，一定要你親手指定，因為揀邊個基準會直接影響你之後點解讀成績。
            </InfoButton>
          </div>
          {!contract ? (
            <p className="paper-hint">揀咗合約先會列出可揀嘅對照基準。</p>
          ) : null}
          {contract && baselines.phase === "loading" ? <p>載入緊…</p> : null}
          {contract && baselines.phase === "error" ? (
            <PaperError title="對照基準讀唔到。" detail={baselines.error} />
          ) : null}
          {contract && baselines.phase === "empty" ? (
            <p className="paper-hint">
              呢個策略版本同合約暫時冇可用嘅對照基準，請先完成一次相符嘅回測。
            </p>
          ) : null}
          {contract && baselines.phase === "ready" ? (
            <>
              <ul className="paper-options">
                {baselines.rows.map((row) => {
                  const chosen = baseline?.run_id === row.run_id;
                  const hinted = highlightRun === row.run_id;
                  return (
                    <li key={row.run_id}>
                      <button
                        type="button"
                        aria-pressed={chosen}
                        disabled={createLocked}
                        className={`paper-option${chosen ? " paper-option--chosen" : ""}`}
                        onClick={() => selectBaseline(row)}
                      >
                        <span className="paper-option__main">
                          {formatInstant(row.range_start)} 至{" "}
                          {formatInstant(row.range_end)}
                          {hinted ? (
                            <span className="paper-badge">你啱啱睇嗰次</span>
                          ) : null}
                        </span>
                        <span className="paper-option__meta">
                          {row.trade_count} 筆成交 · {formatR(row.net_r)} · 初始資金{" "}
                          {formatMoney(row.currency, row.initial_capital)}
                        </span>
                        <span className="paper-option__meta">
                          回測識別碼 {row.run_id}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              <p className="paper-hint">
                零成交嘅回測一樣可以做對照基準；系統唔會幫你揀，要你親手撳。
              </p>
            </>
          ) : null}

          <h2 className="paper-step">呢個交易員自己嘅獨立帳戶</h2>
          {baseline ? (
            <div className="paper-account">
              <p className="paper-account__amount">
                {formatMoney(baseline.currency, baseline.initial_capital)}
              </p>
              <p className="paper-hint">
                由你揀嗰次回測帶入 · 唔會同其他交易員攤分。
              </p>
            </div>
          ) : (
            <p className="paper-hint">未揀對照基準，所以帳戶金額仲未決定。</p>
          )}

          <div className="panel__head">
            <h2 className="paper-step">4. 開始前檢查</h2>
            <InfoButton label="開始前檢查睇咩" align="end">
              建立之前跑一次嘅檢查：數據夠唔夠、設定齊唔齊、有冇衝突。任何一項唔過就會攔住，唔會俾你建立一個由頭就唔會行得好嘅交易員。
            </InfoButton>
          </div>
          {!baseline ? (
            <p className="paper-hint">揀咗對照基準先會做開始前檢查。</p>
          ) : null}
          {baseline && preflight.phase === "loading" ? <p>檢查緊…</p> : null}
          {baseline && preflight.phase === "error" ? (
            <PaperError title="開始前檢查做唔到。" detail={preflight.error} />
          ) : null}
          {baseline && preflightData ? (
            <>
              <h3 className="paper-step">建立授權</h3>
              <div
                className={`paper-authorization paper-authorization--${
                  provisionAuthorized ? "allowed" : "denied"
                }`}
              >
                <p className="paper-authorization__state">
                  {provisionAuthorized
                    ? AUTHORIZATION_ALLOWED
                    : AUTHORIZATION_COPY[preflightData.authorization.state]}
                </p>
                {provisionAuthorized &&
                preflightData.authorization.expires_at !== null ? (
                  <p className="paper-hint">
                    授權有效至{" "}
                    {formatInstant(preflightData.authorization.expires_at)}
                  </p>
                ) : null}
                <p className="paper-hint">{AUTHORIZATION_SCOPE}</p>
              </div>

              <h3 className="paper-step">實際啟動條件</h3>
              <p className="paper-hint">{RUNTIME_SPLIT_NOTE}</p>
              <ul className="paper-checks">
                {preflightData.runtime_readiness.checks.map((item) => (
                  <li
                    key={item.key}
                    className={`paper-check paper-check--${item.status}`}
                  >
                    <span className="paper-check__name">
                      {CHECK_LABEL[item.key]}
                    </span>
                    <span className="paper-check__status">
                      {CHECK_STATUS_LABEL[item.status]}
                    </span>
                    <span className="paper-check__copy">
                      {CHECK_COPY[item.key][item.status]}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="paper-hint">
                {SESSION_COPY[preflightData.runtime_readiness.market_session]}
              </p>
              <p className="paper-hint">
                檢查時間：
                {formatInstant(preflightData.runtime_readiness.checked_at)}
              </p>
              {preflightData.runtime_readiness.overall === "blocked" ? (
                <p className="paper-hint">{RUNTIME_BLOCKED_NOTE}</p>
              ) : null}
            </>
          ) : null}
          {baseline ? (
            <button
              type="button"
              className="paper-button paper-button--ghost"
              onClick={recheckPreflight}
              disabled={preflight.phase === "loading" || createLocked}
            >
              重新檢查
            </button>
          ) : null}

          <h2 className="paper-step">5. 建立</h2>
          {missing.length > 0 ? (
            <p className="paper-hint">仲欠：{missing.join("、")}</p>
          ) : null}
          <button
            type="button"
            className="paper-button paper-button--primary"
            disabled={createDisabled}
            onClick={() => setConfirmOpen(true)}
          >
            建立交易員
          </button>
          <p className="paper-hint">{EXECUTION_NOTE}</p>

          {create.phase === "not-activated" ? (
            <PaperError
              title="模擬盤建立功能尚未啟用。你已經揀咗嘅嘢會保留。"
              detail={null}
            />
          ) : null}
          {create.phase === "error" ? (
            <PaperError title={create.error ?? "建立唔到。"} detail={null} />
          ) : null}
          {create.phase === "recovering" ? (
            <p className="paper-hint">查緊今次請求嘅結果…</p>
          ) : null}
          {createLocked ? (
            <p className="paper-hint">
              今次建立請求未有明確結果之前，策略版本、合約同對照基準會鎖住，唔可以轉。
            </p>
          ) : null}
          {create.phase === "unknown" ? (
            <div className="paper-error" role="alert">
              <p className="paper-error__title">
                建立結果未知。系統唔會自己再建立多一個，只會查返今次同一個請求嘅結果。
              </p>
              {create.detail ? (
                <p className="paper-error__detail">{create.detail}</p>
              ) : null}
              <button
                type="button"
                className="paper-button paper-button--ghost"
                onClick={retryLookup}
                disabled={createBusy}
              >
                再查一次今次請求
              </button>
            </div>
          ) : null}
          {create.phase === "absent" ? (
            <div className="paper-error" role="alert">
              <p className="paper-error__title">
                系統確認今次請求未曾建立過任何交易員，可以用同一個請求再送一次。
              </p>
              <button
                type="button"
                className="paper-button paper-button--ghost"
                onClick={resendSameRequest}
                disabled={createBusy}
              >
                用同一個請求再送一次
              </button>
            </div>
          ) : null}
          {confirmOpen && selection && baseline && contract ? (
            <div
              className="paper-modal"
              role="dialog"
              aria-modal="true"
              aria-label="確認建立交易員"
            >
              <h3 className="paper-modal__title">確認建立交易員</h3>
              <dl className="paper-confirm">
                <ConfirmRow term="策略版本">
                  {selection.strategy_id} · 內容識別碼{" "}
                  {shortSha(selection.content_sha256)}
                </ConfirmRow>
                <ConfirmRow term="合約">
                  {contract.symbol} · {contract.display_name} ·{" "}
                  {contract.contract_id}
                </ConfirmRow>
                <ConfirmRow term="對照基準">
                  {baseline.run_id} · {formatInstant(baseline.range_start)} 至{" "}
                  {formatInstant(baseline.range_end)}
                </ConfirmRow>
                <ConfirmRow term="獨立初始資金">
                  {formatMoney(baseline.currency, baseline.initial_capital)}
                </ConfirmRow>
                <ConfirmRow term="安全網">{SAFEGUARD_SUMMARY}</ConfirmRow>
                <ConfirmRow term="而家狀態">
                  {preflightData
                    ? SESSION_COPY[
                        preflightData.runtime_readiness.market_session
                      ]
                    : "未能確認"}
                </ConfirmRow>
                <ConfirmRow term="建立授權">
                  {provisionAuthorized
                    ? "已授權建立一個未啟用嘅交易員"
                    : "未授權"}
                </ConfirmRow>
              </dl>
              <p className="paper-hint">{EXECUTION_NOTE}</p>
              <p className="paper-hint">
                確認之後策略版本、合約、對照基準同帳戶起點會鎖死；要改就另建一個交易員。
              </p>
              <div className="paper-modal__actions">
                <button
                  type="button"
                  className="paper-button paper-button--ghost"
                  disabled={createBusy}
                  onClick={() => setConfirmOpen(false)}
                >
                  返回修改
                </button>
                <button
                  type="button"
                  className="paper-button paper-button--primary"
                  disabled={createBusy || offline}
                  onClick={confirmCreate}
                >
                  確認並建立交易員
                </button>
              </div>
              {offline ? (
                <p className="paper-hint">離線 · 而家建立唔到，冇任何嘢送出咗。</p>
              ) : null}
              {createBusy ? <p className="paper-hint">建立中…</p> : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {activeTab !== "overview" && activeTab !== "new" ? (
        <section className="paper-panel" aria-label="交易員詳情">
          {detail.phase === "loading" ? <p>載入緊…</p> : null}
          {detail.phase === "error" ? (
            <PaperError title="呢個交易員讀唔到。" detail={detail.error} />
          ) : null}
          {detailMismatch ? (
            <PaperError
              title="呢個交易員嘅清單同詳情記錄唔一致，已經停低唔顯示。"
              detail={null}
            />
          ) : null}
          {detail.phase === "ready" && detail.data && !detailMismatch ? (
            <div className="paper-detail">
              <h2 className="paper-step">
                {detail.data.strategy.strategy_id} · {detail.data.contract_id}
              </h2>
              <p className="paper-detail__state">{PROVISIONED_TRUTH}</p>
              <PaperRuntimePanel traderId={detail.data.trader_id} />
              <dl className="paper-confirm">
                <ConfirmRow term="鎖定策略版本">
                  {detail.data.strategy.strategy_id} · 內容識別碼{" "}
                  {shortSha(detail.data.strategy.content_sha256)}
                </ConfirmRow>
                <ConfirmRow term="鎖定合約">{detail.data.contract_id}</ConfirmRow>
                <ConfirmRow term="鎖定對照基準">
                  {detail.data.baseline.run_id} ·{" "}
                  {formatInstant(detail.data.baseline.range_start)} 至{" "}
                  {formatInstant(detail.data.baseline.range_end)}
                </ConfirmRow>
                <ConfirmRow term="獨立帳戶">
                  {formatMoney(
                    detail.data.account.currency,
                    detail.data.account.initial_capital,
                  )}{" "}
                  · {detail.data.account.account_id}
                </ConfirmRow>
                <ConfirmRow term="安全網">
                  最大回撤 {detail.data.safeguards.max_drawdown_r}R · 連續蝕{" "}
                  {detail.data.safeguards.max_losing_streak} 單 · 失明{" "}
                  {detail.data.safeguards.blind_minutes} 分鐘
                </ConfirmRow>
                <ConfirmRow term="建立時間">
                  {formatInstant(detail.data.created_at)}
                </ConfirmRow>
              </dl>
              <h3 className="paper-step">建立嗰刻嘅開始前檢查</h3>
              <ul className="paper-checks">
                {detail.data.readiness_snapshot.checks.map((item) => (
                  <li
                    key={item.key}
                    className={`paper-check paper-check--${item.status}`}
                  >
                    <span className="paper-check__name">
                      {CHECK_LABEL[item.key]}
                    </span>
                    <span className="paper-check__status">
                      {CHECK_STATUS_LABEL[item.status]}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="paper-hint">
                {SESSION_COPY[detail.data.readiness_snapshot.market_session]}
              </p>

              <h3 className="paper-step">初始帳戶記錄</h3>
              {ledger.phase === "loading" ? <p>載入緊初始帳戶記錄…</p> : null}
              {ledger.phase === "error" ? (
                <PaperError
                  title={ledger.error ?? LEDGER_UNREADABLE}
                  detail={null}
                />
              ) : null}
              {ledger.phase === "ready" && ledger.data ? (
                <div className="paper-ledger">
                  <dl className="paper-confirm">
                    <ConfirmRow term="記錄建立時間">
                      {formatInstant(ledger.data.origin_at)}
                    </ConfirmRow>
                    <ConfirmRow term="獨立帳戶">
                      {ledger.data.account.account_id} ·{" "}
                      {ledger.data.account.currency}
                    </ConfirmRow>
                    <ConfirmRow term="初始資金">
                      {formatMoney(
                        ledger.data.account.currency,
                        ledger.data.account.initial_capital,
                      )}
                    </ConfirmRow>
                    <ConfirmRow term="現金 ／ 權益">
                      {formatMoney(
                        ledger.data.account.currency,
                        ledger.data.balances.cash,
                      )}{" "}
                      ／{" "}
                      {formatMoney(
                        ledger.data.account.currency,
                        ledger.data.balances.equity,
                      )}
                    </ConfirmRow>
                    <ConfirmRow term="已實現 ／ 未實現盈虧">
                      {formatMoney(
                        ledger.data.account.currency,
                        ledger.data.balances.realized_pnl,
                      )}{" "}
                      ／{" "}
                      {formatMoney(
                        ledger.data.account.currency,
                        ledger.data.balances.unrealized_pnl,
                      )}
                    </ConfirmRow>
                    <ConfirmRow term="已記錄成交 ／ 權益點 ／ 事件">
                      {ledger.data.high_water_marks.trades} ／{" "}
                      {ledger.data.high_water_marks.equity} ／{" "}
                      {ledger.data.high_water_marks.events}
                    </ConfirmRow>
                    <ConfirmRow term="持倉 ／ 訂單">
                      {ledger.data.positions.length} ／{" "}
                      {ledger.data.orders.length}
                    </ConfirmRow>
                  </dl>

                  <h3 className="paper-step">鎖定對照基準嘅原始證據</h3>
                  <ul className="paper-members">
                    {ledger.data.baseline.members.map((member) => (
                      <li key={member.path} className="paper-member">
                        <span className="paper-member__path">{member.path}</span>
                        <span className="paper-member__meta">
                          {member.bytes} bytes · {shortSha(member.sha256)}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="paper-hint">
                    對照基準有 {ledger.data.baseline.rejection_count} 筆未成交記錄
                    （唔係 {ledger.data.baseline.rejection_count} 筆漏做交易）。
                  </p>
                  {ledger.data.baseline.closest_rejection_refs.length > 0 ? (
                    <ul className="paper-members">
                      {ledger.data.baseline.closest_rejection_refs.map((ref) => (
                        <li key={ref} className="paper-member">
                          <span className="paper-member__path">
                            最接近觸發：{ref}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <p className="paper-hint">{NOT_EVALUABLE_COPY}</p>
                </div>
              ) : null}

              <h3 className="paper-step">帶偏離證據返 Terminal</h3>
              <div className="paper-review-card">
                <p className="paper-hint">
                  建立一份不可變偏離包，交畀 Terminal 分析；建立唔會改變呢個交易員。
                </p>
                {ledger.phase !== "ready" ? (
                  <p className="paper-hint">
                    {ledger.error ??
                      "等初始帳戶記錄載入完先可以建立偏離包。"}
                  </p>
                ) : null}
                {activeReview === null || activeReview.phase === "unknown" ? (
                  <button
                    type="button"
                    className="paper-button paper-button--primary"
                    disabled={ledger.phase !== "ready" || reviewLocked}
                    onClick={startReviewSnapshot}
                  >
                    建立 review snapshot
                  </button>
                ) : null}
                {activeReview !== null ? (
                  <div className="paper-review-banner">
                    <p className="paper-hint">
                      偏離包狀態：{REVIEW_BANNER[activeReview.phase]}
                    </p>
                    {!dialogOpen ? (
                      <button
                        type="button"
                        className="paper-button paper-button--ghost"
                        onClick={openReviewDialog}
                      >
                        查看偏離包狀態
                      </button>
                    ) : null}
                  </div>
                ) : null}
                {activeReview !== null && dialogOpen ? (
                  <div
                    className="paper-dialog"
                    role="dialog"
                    aria-modal="true"
                    aria-label={REVIEW_DIALOG_LABEL}
                  >
                    {activeReview.phase === "submitting" ||
                    activeReview.phase === "recovering" ? (
                      <p className="paper-hint">準備緊今次偏離包…</p>
                    ) : null}
                    {activeReview.phase === "preparing" &&
                    activeReview.status ? (
                      <div className="paper-review-status">
                        <p className="paper-hint">
                          準備緊：已完成{" "}
                          {activeReview.status.progress.completed_parts} ／{" "}
                          {activeReview.status.progress.total_parts} 部分。
                        </p>
                        <p className="paper-hint">
                          擷取時間：
                          {formatInstant(activeReview.status.captured_at)}
                        </p>
                        <p className="paper-hint">
                          呢段期間唔可以切去另一個交易員；關閉呢張卡唔會取消請求。
                        </p>
                      </div>
                    ) : null}
                    {activeReview.phase === "failed" ? (
                      <div className="paper-review-status">
                        <PaperError
                          title={
                            activeReview.error ??
                            (reviewError
                              ? REVIEW_ERROR_COPY[reviewError.code]
                              : "今次偏離包建立失敗。")
                          }
                          detail={null}
                        />
                        {reviewError?.progress ? (
                          <p className="paper-hint">
                            最後已核實：
                            {reviewError.progress.completed_parts}{" "}
                            ／ {reviewError.progress.total_parts}{" "}
                            部分。
                          </p>
                        ) : null}
                        {reviewError?.issues.length ? (
                          <ul className="paper-members">
                            {reviewError.issues.map(
                              (issue, index) => (
                                <li
                                  key={`${issue.kind}-${String(index)}`}
                                  className="paper-member"
                                >
                                  <span className="paper-member__path">
                                    {ISSUE_KIND_COPY[issue.kind] ?? issue.kind}
                                    {issue.path === null
                                      ? ""
                                      : `：${issue.path}`}
                                  </span>
                                  {issue.source_ref === null ? null : (
                                    <span className="paper-member__meta">
                                      來源：{issue.source_ref}
                                    </span>
                                  )}
                                  {issue.expected_sha256 === null ||
                                  issue.actual_sha256 === null ? null : (
                                    <span className="paper-member__meta">
                                      應該係 {issue.expected_sha256}；而家係{" "}
                                      {issue.actual_sha256}
                                    </span>
                                  )}
                                  {issue.ref_chain.length > 0 ? (
                                    <span className="paper-member__meta">
                                      追溯：{issue.ref_chain.join(" → ")}
                                    </span>
                                  ) : null}
                                </li>
                              ),
                            )}
                          </ul>
                        ) : null}
                        {reviewConflict ? (
                          <p className="paper-hint">
                            呢個請求識別碼已經綁咗另一次內容，所以唔會再查佢，
                            亦唔會沿用嗰份偏離包；要繼續就要你明確建立一份新嘅。
                          </p>
                        ) : (
                          <button
                            type="button"
                            className="paper-button paper-button--ghost"
                            onClick={recheckReviewStatus}
                          >
                            重新檢查狀態
                          </button>
                        )}
                        <button
                          type="button"
                          className="paper-button paper-button--ghost"
                          disabled={ledger.phase !== "ready"}
                          onClick={startNewReviewSnapshot}
                        >
                          建立新嘅偏離包
                        </button>
                      </div>
                    ) : null}
                    {activeReview.phase === "unknown" ? (
                      <div className="paper-review-status">
                        <PaperError
                          title={`${activeReview.error ?? REVIEW_UNKNOWN_TITLE}系統唔會自己重試，亦唔會建立第二份。`}
                          detail={activeReview.detail}
                        />
                        <button
                          type="button"
                          className="paper-button paper-button--ghost"
                          onClick={recheckReviewStatus}
                        >
                          重新檢查狀態
                        </button>
                      </div>
                    ) : null}
                    {activeReview.phase === "ready" &&
                    activeReview.status?.ready ? (
                      <div className="paper-review-status">
                        <p className="paper-success">偏離包已經準備好。</p>
                        <dl className="paper-confirm">
                          <ConfirmRow term="今次請求識別碼">
                            {activeReview.status.request_id}
                          </ConfirmRow>
                          <ConfirmRow term="偏離包識別碼">
                            {activeReview.status.snapshot_id}
                          </ConfirmRow>
                          <ConfirmRow term="擷取時間（UTC 原文）">
                            {activeReview.status.captured_at}
                          </ConfirmRow>
                          <ConfirmRow term="你嘅本地時間">
                            {formatLocalWithZone(
                              activeReview.status.captured_at,
                            )}
                          </ConfirmRow>
                          <ConfirmRow term="交易員狀態">
                            {ledger.data
                              ? `${PROVISIONED_TRUTH}（${ledger.data.lifecycle.status}）`
                              : PROVISIONED_TRUTH}
                          </ConfirmRow>
                          <ConfirmRow term="偏離判斷">
                            {ledger.data
                              ? `${ledger.data.interpretation.evaluation_status} · ${ledger.data.interpretation.reason}`
                              : "初始帳戶記錄未載入，所以未讀到判斷。"}
                          </ConfirmRow>
                        </dl>
                        <p className="paper-hint">{NOT_EVALUABLE_COPY}</p>
                        {ledger.data ? (
                          <dl className="paper-confirm">
                            <ConfirmRow term="現金 ／ 權益">
                              {formatMoney(
                                ledger.data.account.currency,
                                ledger.data.balances.cash,
                              )}{" "}
                              ／{" "}
                              {formatMoney(
                                ledger.data.account.currency,
                                ledger.data.balances.equity,
                              )}
                            </ConfirmRow>
                            <ConfirmRow term="已實現 ／ 未實現盈虧">
                              {formatMoney(
                                ledger.data.account.currency,
                                ledger.data.balances.realized_pnl,
                              )}{" "}
                              ／{" "}
                              {formatMoney(
                                ledger.data.account.currency,
                                ledger.data.balances.unrealized_pnl,
                              )}
                            </ConfirmRow>
                            <ConfirmRow term="已記錄成交">
                              {ledger.data.high_water_marks.trades}
                            </ConfirmRow>
                            <ConfirmRow term="持倉 ／ 訂單">
                              {ledger.data.positions.length} ／{" "}
                              {ledger.data.orders.length}
                            </ConfirmRow>
                          </dl>
                        ) : (
                          <p className="paper-hint">
                            初始帳戶記錄未載入，所以呢度唔會補任何數字。
                          </p>
                        )}
                        <dl className="paper-confirm">
                          <ConfirmRow term="檔案名">
                            {activeReview.status.ready.display_filename}
                          </ConfirmRow>
                          <ConfirmRow term="整包大小">
                            {activeReview.status.ready.artifact_bytes} bytes
                          </ConfirmRow>
                          <ConfirmRow term="整包內容識別碼">
                            {activeReview.status.ready.artifact_sha256}
                          </ConfirmRow>
                        </dl>
                        <ol className="paper-members">
                          {activeReview.status.ready.members.map(
                            (member, index) => (
                              <li key={member.path} className="paper-member">
                                <span className="paper-member__path">
                                  {index + 1}. {member.path}
                                </span>
                                <span className="paper-member__meta">
                                  {member.bytes} bytes · {member.sha256}
                                </span>
                              </li>
                            ),
                          )}
                        </ol>
                        {download.phase === "working" ? (
                          <p className="paper-hint">核對緊偏離包內容…</p>
                        ) : null}
                        {download.phase === "saved" ? (
                          <p className="paper-success">
                            已經下載：{download.filename}
                          </p>
                        ) : null}
                        {download.phase === "error" ? (
                          <PaperError
                            title={download.error ?? "偏離包下載唔到。"}
                            detail={download.detail}
                          />
                        ) : null}
                        {opener.phase === "working" ? (
                          <p className="paper-hint">
                            核對緊 Terminal 開場白…
                          </p>
                        ) : null}
                        {opener.phase === "copied" ? (
                          <p className="paper-success">
                            已複製 Terminal 開場白。
                          </p>
                        ) : null}
                        {opener.phase === "error" ? (
                          <PaperError
                            title={opener.error ?? COPY_REFUSED}
                            detail={opener.detail}
                          />
                        ) : null}
                        <button
                          type="button"
                          className="paper-button paper-button--primary"
                          disabled={download.phase === "working"}
                          onClick={startReviewDownload}
                        >
                          下載偏離包
                        </button>
                        <button
                          type="button"
                          className="paper-button paper-button--ghost"
                          disabled={opener.phase === "working"}
                          onClick={startReviewOpenerCopy}
                        >
                          複製 Terminal 開場白
                        </button>
                        <button
                          type="button"
                          className="paper-button paper-button--ghost"
                          disabled={
                            ledger.phase !== "ready" ||
                            download.phase === "working" ||
                            opener.phase === "working"
                          }
                          onClick={startNewReviewSnapshot}
                        >
                          建立新嘅偏離包
                        </button>
                      </div>
                    ) : null}
                    <button
                      type="button"
                      className="paper-button paper-button--ghost"
                      onClick={closeReviewDialog}
                    >
                      關閉
                    </button>
                  </div>
                ) : null}
              </div>

              <h3 className="paper-step">{ENGINE_NOT_ENABLED}</h3>
              <p className="paper-hint">
                模擬引擎未啟用，所以未有實時價格、成交、持倉、盈虧或者實時圖表；
                暫停、永久停止同偏離記錄等控制要等引擎啟用先會出現。
              </p>
              <p className="paper-hint">{EXECUTION_NOTE}</p>
            </div>
          ) : null}
        </section>
      ) : null}
    </>
  );
}
