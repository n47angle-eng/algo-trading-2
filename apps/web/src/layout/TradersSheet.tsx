/**
 * 交易員快覽 — the roster, one tap from anywhere, without leaving the page.
 *
 * Checking how the simulated traders are doing is the single most repeated
 * action during market hours, and it used to cost two taps into the overflow
 * panel plus a full page load, losing whatever you were reading. So the cards
 * come to you instead: a sheet on the phone, a side panel on desktop.
 *
 * It polls only while open. A glance surface that keeps fetching after it is
 * closed is a battery drain the owner never asked for.
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { fetchDaytradeTraders } from "../api/daytradeClient";
import { TraderQuickCard } from "../components/daytrade/TraderCard";
import { Icon } from "../components/ui/Icon";
import type { DaytradeTradersList } from "../lib/daytrade/types";
import { formatFreshness, useReachability } from "../lib/net/online";

const POLL_MS = 5000;

interface TradersSheetProps {
  open: boolean;
  onClose: () => void;
}

type LoadPhase = "loading" | "ready" | "error";

export function TradersSheet({ open, onClose }: TradersSheetProps) {
  const [list, setList] = useState<DaytradeTradersList | null>(null);
  const [phase, setPhase] = useState<LoadPhase>("loading");
  const { lastOkAt } = useReachability();

  const reload = useCallback(async () => {
    try {
      const next = await fetchDaytradeTraders();
      setList(next);
      setPhase("ready");
    } catch {
      // Keep whatever was already on screen; the phase drives the notice.
      setPhase("error");
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void reload();
    const id = window.setInterval(() => void reload(), POLL_MS);
    return () => {
      window.clearInterval(id);
    };
  }, [open, reload]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  const traders = list?.traders ?? [];

  return (
    <>
      <button
        type="button"
        className="sheet-scrim"
        aria-label="收起交易員快覽"
        onClick={onClose}
      />
      <div className="traders-sheet" role="dialog" aria-label="交易員快覽">
        <span className="sheet__grip" aria-hidden="true" />

        <div className="traders-sheet__head">
          <h2 className="traders-sheet__title">交易員</h2>
          {/* Old numbers must say so; a stale roster read as live is the
              worst failure this surface can have. */}
          <span className="traders-sheet__stamp">
            {phase === "error" ? formatFreshness(lastOkAt) : "即時"}
          </span>
          <button
            type="button"
            className="icon-btn"
            aria-label="關閉"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>

        {phase === "error" ? (
          <p className="traders-sheet__note" role="status">
            拎唔到最新狀態，下面係之前收到嘅。
          </p>
        ) : null}

        <div className="traders-sheet__cards">
          {traders.map((trader) => (
            <TraderQuickCard
              key={trader.trader_id}
              trader={trader}
              onNavigate={onClose}
            />
          ))}
        </div>

        {phase === "loading" && traders.length === 0 ? (
          <p className="traders-sheet__note">載入緊…</p>
        ) : null}
        {phase !== "loading" && traders.length === 0 ? (
          <p className="traders-sheet__note">
            {phase === "error" ? "未有收過交易員資料。" : "未有交易員。"}
          </p>
        ) : null}

        <Link className="traders-sheet__all" to="/daytrade" onClick={onClose}>
          管理交易員（新增 · 設定 · 推進）
        </Link>
      </div>
    </>
  );
}
