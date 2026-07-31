/**
 * Is the data actually reaching us right now?
 *
 * `navigator.onLine` alone lies in exactly the case that matters here: the
 * laptop is on Wi-Fi, so the browser says online, while the local backend is
 * not running. So the browser flag only ever *proves offline* — proving
 * reachable takes a real response, which `trackedFetch` already reports.
 *
 * The state itself lives in `transport.ts`, because the write block has to be
 * enforced there. This module is the React view of it.
 */

import { useCallback, useEffect, useState } from "react";

import {
  browserSaysOffline,
  currentReachability,
  lastSuccessAt,
  markReachabilityUnknown,
  subscribeNet,
  trackedFetch,
  type Reachability,
} from "./transport";

export type { Reachability };

export interface ReachabilityState {
  state: Reachability;
  /** Epoch ms of the last successful response; null when never. */
  lastOkAt: number | null;
  /** Probe once, on demand (the banner's 再試 button). */
  recheck: () => void;
}

const HEALTH_PATH = "/health";

export function useReachability(): ReachabilityState {
  const [state, setState] = useState<Reachability>(() => currentReachability());
  const [lastOkAt, setLastOkAt] = useState<number | null>(() => lastSuccessAt());

  useEffect(
    () =>
      subscribeNet((outcome) => {
        setState(currentReachability());
        if (outcome.ok) {
          setLastOkAt(outcome.at);
        }
      }),
    [],
  );

  const recheck = useCallback(() => {
    if (browserSaysOffline()) {
      setState("offline");
      return;
    }
    // The probe reports itself through trackedFetch, so the subscriber above
    // moves the state. A rejection is expected while the service is down.
    void trackedFetch(HEALTH_PATH).catch(() => undefined);
  }, []);

  useEffect(() => {
    const onOffline = () => {
      setState("offline");
    };
    const onOnline = () => {
      markReachabilityUnknown();
      setState("unknown");
      recheck();
    };
    window.addEventListener("offline", onOffline);
    window.addEventListener("online", onOnline);
    return () => {
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("online", onOnline);
    };
  }, [recheck]);

  return { state, lastOkAt, recheck };
}

/** Grey out a write control while the browser reports no network. */
export function useWritesBlocked(): boolean {
  const { state } = useReachability();
  return writesBlocked(state);
}

/**
 * Mirrors `writesCurrentlyBlocked` in transport.ts: only a proven-offline
 * browser blocks. `unreachable` still shows the banner, but leaves retry
 * buttons live — that is the state a retry is pressed in.
 */
export function writesBlocked(state: Reachability): boolean {
  return state === "offline";
}

/** The banner is shown for both states; only the wording differs. */
export function dataMayBeStale(state: Reachability): boolean {
  return state === "offline" || state === "unreachable";
}

/**
 * "資料停喺 14:32" — a clock time, so it must carry the reader's own zone.
 * This is a *moment*, not a trading date, so local conversion is correct
 * (docs/PROJECT_STATE §4 ①).
 */
export function formatFreshness(at: number | null): string {
  if (at === null) return "未有資料";
  const d = new Date(at);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `資料停喺 ${hh}:${mm}`;
}
