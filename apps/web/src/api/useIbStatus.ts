import { useEffect, useState } from "react";

import { type IbStatus, fetchIbStatus } from "./client";

/**
 * Shared IB reachability probe for the P1 card and the sidebar foot line.
 *
 * A failed probe is a normal daily state (Gateway not started), so it resolves
 * to a `port_unreachable`-style status rather than an error banner — but the
 * reason is always carried in `detail`, never swallowed.
 */
export function useIbStatus(): { status: IbStatus | null; loading: boolean } {
  const [status, setStatus] = useState<IbStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await fetchIbStatus();
        if (!cancelled) {
          setStatus(next);
        }
      } catch (err) {
        if (!cancelled) {
          setStatus({
            schema: "ib_status.v1",
            state: "unconfigured",
            host: null,
            port: null,
            detail: `狀態 API 無法讀取（${err instanceof Error ? err.message : String(err)}）`,
            probe_timeout_seconds: 0,
            checked_at: new Date().toISOString(),
          });
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { status, loading };
}

/**
 * Label + tone for one IB status state (single source for the card and sidebar).
 *
 * `short` fits the 196px sidebar; neither wording ever claims a verified API
 * session, because a TCP connect cannot prove one (channel [083] Q4).
 */
export function ibStatusLabel(status: IbStatus | null): {
  label: string;
  short: string;
  tone: "ok" | "warn" | "muted";
} {
  switch (status?.state) {
    case "port_reachable_unverified":
      return {
        label: "port 可達 · 未驗證 session",
        short: "port 可達",
        tone: "ok",
      };
    case "port_unreachable":
      return { label: "port 不可達", short: "port 不可達", tone: "warn" };
    case "unconfigured":
      return { label: "未設定", short: "未設定", tone: "muted" };
    default:
      return { label: "探測中…", short: "探測中…", tone: "muted" };
  }
}
