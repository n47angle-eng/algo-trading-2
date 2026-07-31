/**
 * One place every API request passes through.
 *
 * The app already had per-page error states, but nothing knew — app-wide —
 * whether the last request actually reached the backend. Toasts and the
 * offline banner both need that single fact, so the API clients call
 * `trackedFetch` instead of `fetch` and this module reports the outcome.
 *
 * Deliberately thin: it does not retry, cache, or rewrite a response. A caller
 * that fails today must fail exactly the same way after this wrapper.
 */

export type NetFailureKind =
  /** fetch itself rejected — no HTTP response ever arrived. */
  | "transport"
  /** A response arrived carrying a non-2xx status. */
  | "http"
  /** A write we refused to send while the service was unreachable. */
  | "blocked";

export interface NetOutcome {
  path: string;
  method: string;
  ok: boolean;
  /** 0 when no response arrived at all. */
  status: number;
  kind: NetFailureKind | null;
  /** Epoch ms, from the caller's clock. */
  at: number;
}

export type Reachability =
  /** A request came back. Data on screen is current. */
  | "ok"
  /** The browser reports no network at all. */
  | "offline"
  /** There is a network, but the data service did not answer. */
  | "unreachable"
  /** Nothing has been proven yet. */
  | "unknown";

type NetListener = (outcome: NetOutcome) => void;

const listeners = new Set<NetListener>();

/** Epoch ms of the last 2xx response, or null when nothing has succeeded yet. */
let lastOkAt: number | null = null;
/** Fail-closed: nothing is proven until a request actually comes back. */
let reachability: Reachability = "unknown";

export function subscribeNet(listener: NetListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function lastSuccessAt(): number | null {
  return lastOkAt;
}

export function currentReachability(): Reachability {
  return browserSaysOffline() ? "offline" : reachability;
}

/**
 * Only a browser that reports no network at all blocks a write.
 *
 * `unreachable` deliberately does *not*: it means one request failed, which is
 * also the state every retry button is pressed in. Blocking there would break
 * the app's own recovery paths — including the byte-exact single retry the
 * paper and results flows depend on — and would turn a transient blip into a
 * dead screen.
 */
export function writesCurrentlyBlocked(): boolean {
  return browserSaysOffline();
}

/** True only when the browser is certain there is no network. */
export function browserSaysOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

/** The browser regained a network; reachability still has to be re-earned. */
export function markReachabilityUnknown(): void {
  reachability = "unknown";
}

/** Test seam only — production code never resets transport history. */
export function resetNetStateForTest(): void {
  lastOkAt = null;
  reachability = "unknown";
  listeners.clear();
}

export class OfflineWriteBlocked extends Error {
  readonly path: string;

  constructor(path: string) {
    super(`${path}: 離線，冇送出`);
    this.name = "OfflineWriteBlocked";
    this.path = path;
  }
}

function emit(outcome: NetOutcome): void {
  if (outcome.ok) {
    lastOkAt = outcome.at;
    reachability = "ok";
  } else if (outcome.kind === "transport") {
    // An HTTP error proves the service answered — that is not unreachable.
    reachability = browserSaysOffline() ? "offline" : "unreachable";
  }
  for (const listener of [...listeners]) {
    try {
      listener(outcome);
    } catch {
      // A broken subscriber must never take down the request that fed it.
    }
  }
}

/** Requests reach the URL as a string everywhere in this app; stay defensive. */
function pathOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.pathname + input.search;
  return input.url;
}

function methodOf(input: RequestInfo | URL, init?: RequestInit): string {
  if (init?.method) return init.method.toUpperCase();
  if (typeof input !== "string" && !(input instanceof URL)) {
    return input.method.toUpperCase();
  }
  return "GET";
}

function isAbort(error: unknown): boolean {
  return (
    error !== null &&
    typeof error === "object" &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

/**
 * `fetch` plus an outcome report. Resolves and rejects exactly like `fetch`,
 * including for non-2xx responses — the caller still owns error handling.
 *
 * `globalThis.fetch` is read at call time so a test that stubs the global
 * still intercepts every request.
 */
export async function trackedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const path = pathOf(input);
  const method = methodOf(input, init);
  // A write attempted while the service is unreachable would fail anyway —
  // but failing here means it is provably never sent, and the owner gets one
  // clear sentence instead of a browser network error.
  if (method !== "GET" && writesCurrentlyBlocked()) {
    emit({ path, method, ok: false, status: 0, kind: "blocked", at: Date.now() });
    throw new OfflineWriteBlocked(path);
  }
  try {
    const response = await globalThis.fetch(input, init);
    emit({
      path,
      method,
      ok: response.ok,
      status: response.status,
      kind: response.ok ? null : "http",
      at: Date.now(),
    });
    return response;
  } catch (error) {
    // An aborted request is the app's own doing (unmount, superseded probe).
    // Reporting it would put "連唔到" on screen for a request nobody wanted.
    if (!isAbort(error)) {
      emit({
        path,
        method,
        ok: false,
        status: 0,
        kind: "transport",
        at: Date.now(),
      });
    }
    throw error;
  }
}
