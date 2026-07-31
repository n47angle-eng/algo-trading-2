/**
 * App-wide toasts.
 *
 * Two tones, two lifetimes, on purpose:
 *   error   — stays until dismissed. A failure that vanishes on its own is a
 *             failure the owner can miss, and a missed failure reads exactly
 *             like success.
 *   success — 3s. It only confirms something the owner just did.
 *
 * Failures also arrive here on their own: the provider subscribes to the
 * transport layer, so a request that never reached the backend, or a write
 * that came back non-2xx, surfaces even on a page with no error state of its
 * own. Read failures with a real HTTP status are left to the page — it already
 * renders them in place, and duplicating those would train the owner to swipe
 * toasts away without reading.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { humanizeApiError } from "../../lib/net/errorMessage";
import { subscribeNet } from "../../lib/net/transport";
import {
  ToastContext,
  type ToastApi,
  type ToastRecord,
} from "./toastContext";

/** Only three fit on a phone before they cover the content they describe. */
const MAX_VISIBLE = 3;
const SUCCESS_MS = 3000;
/** Repeating the same failure every poll tick is noise, not information. */
const DEDUPE_MS = 8000;

let toastSeq = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const recentRef = useRef(new Map<string, number>());
  const timersRef = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timersRef.current.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (toast: Omit<ToastRecord, "id">) => {
      const key = `${toast.tone}:${toast.title}`;
      const now = Date.now();
      const seenAt = recentRef.current.get(key);
      if (seenAt !== undefined && now - seenAt < DEDUPE_MS) {
        return;
      }
      recentRef.current.set(key, now);

      toastSeq += 1;
      const id = `toast-${String(toastSeq)}`;
      setToasts((prev) => [...prev, { ...toast, id }].slice(-MAX_VISIBLE));

      if (toast.tone === "success") {
        const timer = setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.id !== id));
          timersRef.current.delete(id);
        }, SUCCESS_MS);
        timersRef.current.set(id, timer);
      }
    },
    [],
  );

  const showError = useCallback<ToastApi["showError"]>(
    (error, action) => {
      push({
        tone: "error",
        title: error.title,
        hint: error.hint,
        detail: error.detail,
        action,
      });
    },
    [push],
  );

  const showSuccess = useCallback<ToastApi["showSuccess"]>(
    (title) => {
      push({ tone: "success", title, hint: "", detail: "" });
    },
    [push],
  );

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
      timers.clear();
    };
  }, []);

  useEffect(
    () =>
      subscribeNet((outcome) => {
        if (outcome.ok) return;
        // Toasts are for writes only. A read that fails is already covered:
        // an HTTP error by the page's own error state, an unreachable service
        // by the offline bar — and on a phone a toast would sit on top of
        // that bar saying the same thing twice.
        const isWrite = outcome.method !== "GET";
        if (!isWrite) return;
        const human =
          outcome.kind === "blocked"
            ? {
                // The owner's first question is whether it half-happened.
                title: "而家離線，冇改到嘢",
                hint: "呢個動作完全冇送出去。等連得返再試。",
              }
            : humanizeApiError(outcome.status, "");
        push({
          tone: "error",
          title: human.title,
          hint: human.hint,
          // The path is engineering vocabulary, so it lives behind 詳情 only.
          detail: `${outcome.method} ${outcome.path}${
            outcome.status ? ` → ${String(outcome.status)}` : ""
          }`,
        });
      }),
    [push],
  );

  const api = useMemo<ToastApi>(
    () => ({ showError, showSuccess, dismiss }),
    [showError, showSuccess, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: ToastRecord;
  onDismiss: (id: string) => void;
}) {
  const [detailOpen, setDetailOpen] = useState(false);
  const isError = toast.tone === "error";

  return (
    <div
      className={`toast toast--${toast.tone}`}
      // Errors interrupt; confirmations wait their turn.
      role={isError ? "alert" : "status"}
      data-testid={`toast-${toast.tone}`}
    >
      <div className="toast__body">
        <p className="toast__title">{toast.title}</p>
        {toast.hint ? <p className="toast__hint">{toast.hint}</p> : null}
        {toast.detail ? (
          <>
            <button
              type="button"
              className="toast__detail-toggle"
              aria-expanded={detailOpen}
              onClick={() => {
                setDetailOpen((v) => !v);
              }}
            >
              {detailOpen ? "收起詳情" : "詳情"}
            </button>
            {detailOpen ? (
              <pre className="toast__detail">{toast.detail}</pre>
            ) : null}
          </>
        ) : null}
      </div>

      <div className="toast__actions">
        {toast.action ? (
          <button
            type="button"
            className="toast__action"
            onClick={() => {
              toast.action?.run();
              onDismiss(toast.id);
            }}
          >
            {toast.action.label}
          </button>
        ) : null}
        <button
          type="button"
          className="toast__close"
          aria-label="關閉通知"
          onClick={() => {
            onDismiss(toast.id);
          }}
        >
          ✕
        </button>
      </div>
    </div>
  );
}

export function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: ToastRecord[];
  onDismiss: (id: string) => void;
}) {
  if (toasts.length === 0) return null;
  return (
    <div className="toast-viewport" aria-live="polite" aria-label="通知">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}
