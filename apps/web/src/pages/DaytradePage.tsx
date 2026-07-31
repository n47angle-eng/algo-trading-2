import { useCallback, useEffect, useState, type FormEvent } from "react";

import { TraderEntryCard } from "../components/daytrade/TraderCard";
import { TraderWindow } from "../components/daytrade/TraderWindow";
import { InfoButton } from "../components/ui/InfoButton";
import { PageHeader } from "../components/ui/PageHeader";

import {
  createDaytradeTrader,
  fetchDaytradeHealth,
  fetchDaytradeTraders,
  postDaytradeRunner,
  postDaytradeStep,
} from "../api/daytradeClient";
import type {
  DaytradeHealth,
  DaytradeTradersList,
} from "../lib/daytrade/types";

export function DaytradePage() {
  const [list, setList] = useState<DaytradeTradersList | null>(null);
  const [health, setHealth] = useState<DaytradeHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [symbol, setSymbol] = useState<"NQ" | "YM" | "GC">("NQ");
  const [notes, setNotes] = useState("");
  /** Which trader's window is open, and where it should grow from. */
  const [openTrader, setOpenTrader] = useState<{
    id: string;
    origin: { x: number; y: number };
  } | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const [traders, h] = await Promise.all([
        fetchDaytradeTraders(),
        fetchDaytradeHealth(),
      ]);
      setList(traders);
      setHealth(h);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
    const id = window.setInterval(() => void reload(), 5000);
    return () => window.clearInterval(id);
  }, [reload]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createDaytradeTrader({
        display_name: name.trim(),
        symbol,
        notes: notes.trim() || undefined,
      });
      setName("");
      setNotes("");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggleRunner(enabled: boolean) {
    setBusy(true);
    try {
      await postDaytradeRunner(enabled);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function stepAll() {
    setBusy(true);
    try {
      await postDaytradeStep({});
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="daytrade-page">
      <PageHeader
        title="日內模擬交易員"
        info={
          <>
            一班模擬交易員嘅名單，每位有自己獨立嘅帳本。撳卡片入去睇個人檔案、倉位同成績表。
            <br />
            成交用每分鐘收市價計，所以呢度嘅成績唔可以直接同用「最差一秒」口徑跑嘅回測比較。
          </>
        }
      />

      <p className="daytrade-page__scale-banner" role="status">
        成交尺：1m_close（穩定 live paper）— 唔可比對 1s_worst 回測
      </p>

      {error ? (
        <div className="daytrade-page__error" role="alert">
          {error}
        </div>
      ) : null}

      <section className="daytrade-page__health">
        <span>
          Runner：
          <strong>{health?.runner_enabled ? "已開" : "關閉（預設）"}</strong>
        </span>
        <span>交易員數：{list?.count ?? health?.trader_count ?? "—"}</span>
        <span>事故：{health?.pending_incidents ?? "—"}</span>
        <div className="daytrade-page__actions">
          <button
            type="button"
            disabled={busy}
            onClick={() => void toggleRunner(true)}
          >
            啟用 Runner
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void toggleRunner(false)}
          >
            停用 Runner
          </button>
          <button type="button" disabled={busy} onClick={() => void stepAll()}>
            推進全部
          </button>
        </div>
      </section>

      <section className="daytrade-page__panel">
        <div className="panel__head">
          <h2>新增模擬交易員</h2>
          <InfoButton label="新增交易員點做" align="end">
            填個名、揀個標的就得，其餘用預設：開盤區間突破策略、起始資金
            100,000。建立完之後可以再擴參數。每位交易員有自己獨立嘅帳本。
          </InfoButton>
        </div>
        <form className="daytrade-create" onSubmit={(e) => void onCreate(e)}>
          <label>
            名稱
            <input
              value={name}
              onChange={(ev) => setName(ev.target.value)}
              placeholder="例如：NQ 早盤一號"
              required
              maxLength={80}
            />
          </label>
          <label>
            標的
            <select
              value={symbol}
              onChange={(ev) => setSymbol(ev.target.value as "NQ" | "YM" | "GC")}
            >
              {(list?.supported_symbols ?? ["NQ", "YM", "GC"]).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            備註
            <input
              value={notes}
              onChange={(ev) => setNotes(ev.target.value)}
              placeholder="可選"
            />
          </label>
          <button type="submit" disabled={busy || !name.trim()}>
            建立
          </button>
        </form>
        <p className="muted">
          策略預設：開盤區間突破 · 起始資金 100,000 · 可之後再擴參數
        </p>
      </section>

      <section className="daytrade-page__panel">
        <div className="panel__head">
          <h2>交易員全貌</h2>
          <InfoButton label="交易員全貌點用" align="end">
            每張卡係一位模擬交易員嘅完整設定。想淨係快速睇身家、今日同持倉，撳底下（電腦版係頂欄）嘅「交易員」就得，唔使離開你而家嗰頁。
          </InfoButton>
        </div>
        <p className="muted">
          淨係想睇成績？撳「交易員」快覽就得，唔使入呢頁。
        </p>
        <div className="daytrade-page__cards">
          {(list?.traders ?? []).map((t) => (
            <TraderEntryCard
              key={t.trader_id}
              trader={t}
              onOpen={(origin) => {
                setOpenTrader({ id: t.trader_id, origin });
              }}
            />
          ))}
        </div>
        {!list?.traders.length ? (
          <p className="muted">未有交易員 — 上面建立第一個。</p>
        ) : null}
      </section>

      <TraderWindow
        trader={
          (list?.traders ?? []).find((t) => t.trader_id === openTrader?.id) ??
          null
        }
        origin={openTrader?.origin ?? null}
        onClose={() => {
          setOpenTrader(null);
        }}
      />
    </div>
  );
}
