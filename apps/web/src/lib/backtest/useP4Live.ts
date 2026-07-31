import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  cancelP4Queued,
  fetchP4Batch,
  fetchP4Batches,
  precheckP4Batch,
  submitP4Batch,
} from "../../api/client";
import { processBacktestTerminalNotification } from "../notifications";
import {
  duplicateAcknowledgementKey,
  exactDuplicateAcknowledgements,
  isMonotonicP4BatchUpdate,
  isP4BatchTerminal,
  p4RequestIdentity,
  parseP4Batch,
  parseP4BatchList,
  parseP4Precheck,
  parseP4StandardRequest,
  type P4Batch,
  type P4DuplicateAcknowledgement,
  type P4Precheck,
  type P4StandardRequest,
} from "./liveContract";

export type P4LoadState = "loading" | "ready" | "error" | "empty";

export type P4PrecheckState =
  | { kind: "idle" }
  | { kind: "loading"; requestKey: string }
  | { kind: "ready"; requestKey: string; document: P4Precheck }
  | { kind: "error"; requestKey: string; message: string };

const PRECHECK_UNAVAILABLE =
  "開始前檢查暫時核實唔到，未確認前唔會開始。";
const HISTORY_UNAVAILABLE =
  "最近回測暫時讀取唔到；已經保留畫面上其他資料。";
const SUBMIT_UNAVAILABLE =
  "今次未能送出；未有任何新回測開始，請核對後再試。";
const POLL_UNAVAILABLE =
  "最新進度暫時讀取唔到；以下保留最後一份已確認資料，系統會每秒再試。";
const CANCEL_UNAVAILABLE =
  "未能確認取消結果；以下狀態冇改動，正在跑嘅回測亦冇被當成已取消。";

function sameAcknowledgements(
  left: readonly P4DuplicateAcknowledgement[],
  right: readonly P4DuplicateAcknowledgement[],
): boolean {
  const a = left.map(duplicateAcknowledgementKey).sort();
  const b = right.map(duplicateAcknowledgementKey).sort();
  return JSON.stringify(a) === JSON.stringify(b);
}

function accepted409Acknowledgements(
  request: P4StandardRequest,
  document: P4Precheck,
): P4DuplicateAcknowledgement[] {
  const accepted = new Set(
    document.units
      .filter(
        (unit) =>
          unit.duplicate.acknowledged &&
          unit.duplicate.reason_codes.includes("duplicate_acknowledged") &&
          (unit.duplicate.exact_match_count ?? 0) > 0,
      )
      .map((unit) =>
        duplicateAcknowledgementKey({
          strategy_version: unit.strategy_version,
          symbol: unit.symbol,
          range_start: unit.range_start,
          range_end: unit.range_end,
        }),
      ),
  );
  return request.duplicate_acknowledgements.filter((acknowledgement) =>
    accepted.has(duplicateAcknowledgementKey(acknowledgement)),
  );
}

export interface UseP4LiveResult {
  precheck: P4PrecheckState;
  active: P4Batch | null;
  history: P4Batch[];
  historyLoad: P4LoadState;
  historyError: string | null;
  submitting: boolean;
  submitError: string | null;
  pollError: string | null;
  cancelBusy: boolean;
  cancelError: string | null;
  submitCurrent: () => Promise<void>;
  cancelQueued: () => Promise<void>;
  refreshHistory: () => Promise<void>;
}

export function useP4Live(input: {
  enabled: boolean;
  request: P4StandardRequest | null;
  onAcknowledgementsChanged: (
    acknowledgements: P4DuplicateAcknowledgement[],
  ) => void;
  onSubmitted: (batchId: string) => void;
}): UseP4LiveResult {
  const { enabled, request, onAcknowledgementsChanged, onSubmitted } = input;
  const [precheck, setPrecheck] = useState<P4PrecheckState>({ kind: "idle" });
  const [active, setActive] = useState<P4Batch | null>(null);
  const [history, setHistory] = useState<P4Batch[]>([]);
  const [historyLoad, setHistoryLoad] = useState<P4LoadState>("loading");
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const activeRef = useRef<P4Batch | null>(null);
  const precheckSequenceRef = useRef(0);
  const submitSequenceRef = useRef(0);
  const historySequenceRef = useRef(0);
  const updateSequenceRef = useRef(0);
  const submitInFlightRef = useRef(false);
  const cancelInFlightRef = useRef(false);

  const acceptActive = useCallback((batch: P4Batch) => {
    activeRef.current = batch;
    setActive(batch);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      precheckSequenceRef.current += 1;
      submitSequenceRef.current += 1;
      historySequenceRef.current += 1;
      updateSequenceRef.current += 1;
      submitInFlightRef.current = false;
      cancelInFlightRef.current = false;
    };
  }, []);

  const requestKey = request ? p4RequestIdentity(request) : null;

  useEffect(() => {
    setSubmitError(null);
    if (!enabled || !request || !requestKey) {
      precheckSequenceRef.current += 1;
      setPrecheck({ kind: "idle" });
      return;
    }
    const sequence = ++precheckSequenceRef.current;
    const controller = new AbortController();
    const frozen = parseP4StandardRequest(request);
    if (!frozen) {
      setPrecheck({
        kind: "error",
        requestKey,
        message: PRECHECK_UNAVAILABLE,
      });
      return;
    }
    setPrecheck({ kind: "loading", requestKey });
    void (async () => {
      try {
        const response = await precheckP4Batch(frozen, controller.signal);
        if (
          !mountedRef.current ||
          sequence !== precheckSequenceRef.current
        ) {
          return;
        }
        const document =
          response.status === 200
            ? parseP4Precheck(response.body, frozen)
            : null;
        setPrecheck(
          document
            ? { kind: "ready", requestKey, document }
            : {
                kind: "error",
                requestKey,
                message: PRECHECK_UNAVAILABLE,
              },
        );

        if (document) {
          const exactDuplicates = exactDuplicateAcknowledgements(document);
          const acknowledged = frozen.duplicate_acknowledgements;
          if (
            acknowledged.length > 0 &&
            !sameAcknowledgements(acknowledged, exactDuplicates)
          ) {
            onAcknowledgementsChanged([]);
          }
        }
      } catch (error) {
        if (
          !controller.signal.aborted &&
          mountedRef.current &&
          sequence === precheckSequenceRef.current
        ) {
          void error;
          setPrecheck({
            kind: "error",
            requestKey,
            message: PRECHECK_UNAVAILABLE,
          });
        }
      }
    })();
    return () => {
      controller.abort();
    };
  }, [
    enabled,
    onAcknowledgementsChanged,
    request,
    requestKey,
  ]);

  const refreshHistory = useCallback(async () => {
    if (!enabled) {
      return;
    }
    const sequence = ++historySequenceRef.current;
    setHistoryLoad("loading");
    setHistoryError(null);
    try {
      const response = await fetchP4Batches();
      if (
        !mountedRef.current ||
        sequence !== historySequenceRef.current
      ) {
        return;
      }
      const parsed =
        response.status === 200 ? parseP4BatchList(response.body) : null;
      if (!parsed) {
        setHistory([]);
        setHistoryLoad("error");
        setHistoryError(HISTORY_UNAVAILABLE);
        return;
      }
      const standard = parsed.batches.filter(
        (batch) => parseP4StandardRequest(batch.request) !== null,
      );
      setHistory(standard.slice(0, 15));
      setHistoryLoad(standard.length === 0 ? "empty" : "ready");
    } catch (error) {
      if (
        mountedRef.current &&
        sequence === historySequenceRef.current
      ) {
        void error;
        setHistory([]);
        setHistoryLoad("error");
        setHistoryError(HISTORY_UNAVAILABLE);
      }
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) {
      historySequenceRef.current += 1;
      setHistory([]);
      setHistoryLoad("loading");
      setHistoryError(null);
      return;
    }
    void refreshHistory();
  }, [enabled, refreshHistory]);

  useEffect(() => {
    if (
      !enabled ||
      !active ||
      isP4BatchTerminal(active.status)
    ) {
      return;
    }
    let inFlight = false;
    let controller: AbortController | null = null;
    const poll = async () => {
      if (inFlight || cancelInFlightRef.current || !activeRef.current) {
        return;
      }
      const previous = activeRef.current;
      const batchId = previous.batch_id;
      const sequence = ++updateSequenceRef.current;
      inFlight = true;
      controller = new AbortController();
      try {
        const response = await fetchP4Batch(batchId, controller.signal);
        if (
          !mountedRef.current ||
          sequence !== updateSequenceRef.current ||
          activeRef.current?.batch_id !== batchId
        ) {
          return;
        }
        const next =
          response.status === 200
            ? parseP4Batch(response.body, batchId)
            : null;
        if (
          !next ||
          !isMonotonicP4BatchUpdate(previous, next, "poll")
        ) {
          setPollError(POLL_UNAVAILABLE);
          return;
        }
        setPollError(null);
        const previousStatus = previous.status;
        acceptActive(next);
        if (isP4BatchTerminal(next.status)) {
          void processBacktestTerminalNotification({
            batchId: next.batch_id,
            status: next.status as
              | "completed"
              | "failed"
              | "cancelled"
              | "partial",
            previousStatus,
            completed: next.summary?.completed,
            failed: next.summary?.failed,
            total: next.summary?.total,
          });
          void refreshHistory();
        }
      } catch (error) {
        if (
          !controller?.signal.aborted &&
          mountedRef.current &&
          sequence === updateSequenceRef.current
        ) {
          void error;
          setPollError(POLL_UNAVAILABLE);
        }
      } finally {
        inFlight = false;
      }
    };
    const timer = window.setInterval(() => {
      void poll();
    }, 1000);
    return () => {
      window.clearInterval(timer);
      controller?.abort();
    };
  }, [
    acceptActive,
    active,
    enabled,
    refreshHistory,
  ]);

  const submitCurrent = useCallback(async () => {
    if (!enabled || !request || !requestKey || submitInFlightRef.current) {
      return;
    }
    if (
      precheck.kind !== "ready" ||
      precheck.requestKey !== requestKey ||
      !precheck.document.can_submit
    ) {
      return;
    }
    const frozen = parseP4StandardRequest(request);
    if (!frozen) {
      return;
    }
    const sequence = ++submitSequenceRef.current;
    submitInFlightRef.current = true;
    setSubmitting(true);
    setSubmitError(null);
    setCancelError(null);
    try {
      const response = await submitP4Batch(frozen);
      if (
        !mountedRef.current ||
        sequence !== submitSequenceRef.current
      ) {
        return;
      }
      if (response.status === 409) {
        const document = parseP4Precheck(response.body, frozen);
        if (!document) {
          setSubmitError(SUBMIT_UNAVAILABLE);
          return;
        }
        setPrecheck({ kind: "ready", requestKey, document });
        const accepted = accepted409Acknowledgements(frozen, document);
        if (
          !sameAcknowledgements(
            frozen.duplicate_acknowledgements,
            accepted,
          )
        ) {
          onAcknowledgementsChanged(accepted);
        }
        setSubmitError(
          "提交時檢查結果已更新；未有新回測開始，請重新核對下面三項。",
        );
        return;
      }
      const batch =
        (response.status === 200 || response.status === 201)
          ? parseP4Batch(response.body)
          : null;
      const storedRequest = batch
        ? parseP4StandardRequest(batch.request)
        : null;
      if (
        !batch ||
        !storedRequest ||
        p4RequestIdentity(storedRequest) !== p4RequestIdentity(frozen)
      ) {
        setSubmitError(SUBMIT_UNAVAILABLE);
        return;
      }
      updateSequenceRef.current += 1;
      setPollError(null);
      acceptActive(batch);
      onAcknowledgementsChanged([]);
      onSubmitted(batch.batch_id);
      void refreshHistory();
    } catch (error) {
      if (
        mountedRef.current &&
        sequence === submitSequenceRef.current
      ) {
        void error;
        setSubmitError(SUBMIT_UNAVAILABLE);
      }
    } finally {
      if (sequence === submitSequenceRef.current) {
        submitInFlightRef.current = false;
        if (mountedRef.current) {
          setSubmitting(false);
        }
      }
    }
  }, [
    acceptActive,
    enabled,
    onAcknowledgementsChanged,
    onSubmitted,
    precheck,
    refreshHistory,
    request,
    requestKey,
  ]);

  const cancelQueued = useCallback(async () => {
    const previous = activeRef.current;
    if (
      !enabled ||
      !previous ||
      previous.summary.queued === 0 ||
      cancelInFlightRef.current
    ) {
      return;
    }
    const batchId = previous.batch_id;
    const sequence = ++updateSequenceRef.current;
    cancelInFlightRef.current = true;
    setCancelBusy(true);
    setCancelError(null);
    try {
      const response = await cancelP4Queued(batchId);
      if (
        !mountedRef.current ||
        sequence !== updateSequenceRef.current ||
        activeRef.current?.batch_id !== batchId
      ) {
        return;
      }
      const next =
        response.status === 200
          ? parseP4Batch(response.body, batchId)
          : null;
      if (
        !next ||
        !isMonotonicP4BatchUpdate(previous, next, "cancel")
      ) {
        setCancelError(CANCEL_UNAVAILABLE);
        return;
      }
      acceptActive(next);
      if (isP4BatchTerminal(next.status)) {
        void processBacktestTerminalNotification({
          batchId: next.batch_id,
          status: next.status as
            | "completed"
            | "failed"
            | "cancelled"
            | "partial",
          previousStatus: previous.status,
          completed: next.summary?.completed,
          failed: next.summary?.failed,
          total: next.summary?.total,
        });
        void refreshHistory();
      }
    } catch (error) {
      if (
        mountedRef.current &&
        sequence === updateSequenceRef.current
      ) {
        void error;
        setCancelError(CANCEL_UNAVAILABLE);
      }
    } finally {
      cancelInFlightRef.current = false;
      if (mountedRef.current) {
        setCancelBusy(false);
      }
    }
  }, [
    acceptActive,
    enabled,
    refreshHistory,
  ]);

  useEffect(() => {
    if (!enabled) {
      precheckSequenceRef.current += 1;
      submitSequenceRef.current += 1;
      updateSequenceRef.current += 1;
      submitInFlightRef.current = false;
      cancelInFlightRef.current = false;
      activeRef.current = null;
      setActive(null);
      setPrecheck({ kind: "idle" });
      setSubmitting(false);
      setSubmitError(null);
      setPollError(null);
      setCancelBusy(false);
      setCancelError(null);
    }
  }, [enabled]);

  return {
    precheck,
    active,
    history,
    historyLoad,
    historyError,
    submitting,
    submitError,
    pollError,
    cancelBusy,
    cancelError,
    submitCurrent,
    cancelQueued,
    refreshHistory,
  };
}
