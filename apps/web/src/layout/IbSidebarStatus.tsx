import { useEffect, useState } from "react";

import { fetchComputeStatus } from "../api/client";
import { ibStatusLabel, useIbStatus } from "../api/useIbStatus";
import { ENGINE_SUMMARY, engineLabel, plainIbDetail } from "../lib/humanTerms";

/**
 * Sidebar foot IB line — replaces the hardcoded "離線 stub" text with the real
 * three-state probe (channel [083] Q4). Wording never claims a verified API
 * session, because a TCP connect cannot prove one.
 *
 * Phase E: also shows a one-line compute backend tip (Python authority / Rust compute).
 */
export function IbSidebarStatus() {
  const { status } = useIbStatus();
  const { short, tone } = ibStatusLabel(status);
  const [computeTip, setComputeTip] = useState("計算 · 探測中…");
  // Port only: the 196px sidebar cannot hold host:port plus the state wording.
  // The full sentence stays in the tooltip and on the P1 card.
  const endpoint = status?.port ? String(status.port) : "—";

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const s = await fetchComputeStatus();
        if (cancelled) return;
        const bt = engineLabel(s.effective_backtest_backend ?? "python");
        const ch = engineLabel(s.effective_chart_backend);
        const errN = s.error_count ?? 0;
        setComputeTip(
          errN > 0
            ? `計算 · 有${String(errN)}個問題 · 回測用${bt}`
            : `計算 · 圖${ch}／回測${bt}`,
        );
      } catch {
        if (!cancelled) setComputeTip("計算 · 狀態不可用");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="sidebar__status" title={plainIbDetail(status?.detail).text}>
      <span
        className={`sidebar__live-dot sidebar__live-dot--${tone}`}
        aria-hidden="true"
      />
      <span>
        IB · {endpoint} · {short}
      </span>
      <span
        className="sidebar__status-compute"
        data-testid="sidebar-compute-tip"
        title={ENGINE_SUMMARY}
      >
        {computeTip}
      </span>
    </div>
  );
}
