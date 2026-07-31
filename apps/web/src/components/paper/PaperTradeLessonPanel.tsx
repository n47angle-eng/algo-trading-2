import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchPaperTradeLesson } from "../../api/client";
import {
  parsePaperTradeLesson,
  type PaperTradeLesson,
} from "../../lib/paper/tradeLessonTypes";
import { FloatingWindow } from "../ui/FloatingWindow";
import { InfoButton } from "../ui/InfoButton";
import { PaperTradeChart } from "./PaperTradeChart";

type LessonTab = "position" | "replay" | "conditions" | "teach";

const TAB_LABEL: Record<LessonTab, string> = {
  position: "倉位",
  replay: "重播",
  conditions: "成交條件",
  teach: "教學",
};

interface Props {
  traderId: string;
  open: boolean;
  onClose: () => void;
  /** Click origin for desktop floating window. */
  origin?: { x: number; y: number } | null;
  /** Inline mode (inside runtime panel) without floating chrome. */
  variant?: "window" | "inline";
}

/**
 * Click-to-inspect: what the trader bought, chart marks, condition list,
 * teaching copy, and bar-by-bar replay — desktop window + mobile sheet.
 */
export function PaperTradeLessonPanel({
  traderId,
  open,
  onClose,
  origin = null,
  variant = "window",
}: Props) {
  const [data, setData] = useState<PaperTradeLesson | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<LessonTab>("position");
  const [replayIndex, setReplayIndex] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const playTimer = useRef<number | null>(null);
  const dataRef = useRef<PaperTradeLesson | null>(null);
  dataRef.current = data;

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchPaperTradeLesson(traderId);
      if (!result.ok) {
        setData(null);
        setError(
          result.status === 404
            ? "呢個交易員暫時未有模擬紀錄，未開得教學面板。"
            : "倉位教學資料暫時讀唔到。",
        );
        return;
      }
      const parsed = parsePaperTradeLesson(result.body);
      if (!parsed) {
        setData(null);
        setError("回傳格式唔正確，已停低唔顯示假數。");
        return;
      }
      setData(parsed);
      setReplayIndex(
        parsed.bars.length > 0 ? parsed.bars.length - 1 : null,
      );
    } catch {
      setData(null);
      setError("倉位教學暫時連唔上。");
    } finally {
      setLoading(false);
    }
  }, [traderId]);

  useEffect(() => {
    if (!open) {
      setPlaying(false);
      return;
    }
    void reload();
  }, [open, reload]);

  useEffect(() => {
    if (!playing || !data || data.bars.length === 0) {
      if (playTimer.current != null) {
        window.clearInterval(playTimer.current);
        playTimer.current = null;
      }
      return;
    }
    playTimer.current = window.setInterval(() => {
      setReplayIndex((prev) => {
        const max = dataRef.current?.bars.length
          ? dataRef.current.bars.length - 1
          : 0;
        const cur = prev ?? 0;
        if (cur >= max) {
          setPlaying(false);
          return max;
        }
        return cur + 1;
      });
    }, 450);
    return () => {
      if (playTimer.current != null) {
        window.clearInterval(playTimer.current);
        playTimer.current = null;
      }
    };
  }, [playing, data]);

  const subtitle = useMemo(() => {
    if (!data) return traderId.slice(-8);
    const pos = data.open_position;
    const posLabel = pos ? pos.label : "空手";
    return `${data.selection.contract_id || "—"} · ${posLabel}`;
  }, [data, traderId]);

  const body = (
    <div className="paper-lesson">
      {loading && !data ? (
        <p className="paper-hint">載入倉位同走勢…</p>
      ) : null}
      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}

      {data ? (
        <>
          <header className="paper-lesson__header">
            <div>
              <p className="paper-hint" style={{ marginBottom: "0.25rem" }}>
                {data.teaching.headline}
              </p>
              <p className="paper-hint">{data.teaching.intro}</p>
            </div>
            <button
              type="button"
              className="paper-button paper-button--ghost"
              disabled={loading}
              onClick={() => {
                void reload();
              }}
            >
              {loading ? "更新緊…" : "重新整理"}
            </button>
          </header>

          <div
            className="paper-lesson__tabs"
            role="tablist"
            aria-label="教學分頁"
          >
            {(Object.keys(TAB_LABEL) as LessonTab[]).map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                className={
                  tab === key
                    ? "paper-tab paper-tab--active"
                    : "paper-tab"
                }
                onClick={() => {
                  setTab(key);
                }}
              >
                {TAB_LABEL[key]}
              </button>
            ))}
          </div>

          {tab === "position" ? (
            <PositionSection data={data} replayIndex={replayIndex} />
          ) : null}
          {tab === "replay" ? (
            <ReplaySection
              data={data}
              replayIndex={replayIndex}
              playing={playing}
              onPlay={() => {
                if (!data.bars.length) return;
                if (
                  replayIndex != null &&
                  replayIndex >= data.bars.length - 1
                ) {
                  setReplayIndex(0);
                }
                setPlaying(true);
              }}
              onPause={() => {
                setPlaying(false);
              }}
              onSeek={(index) => {
                setPlaying(false);
                setReplayIndex(index);
              }}
            />
          ) : null}
          {tab === "conditions" ? <ConditionsSection data={data} /> : null}
          {tab === "teach" ? <TeachSection data={data} /> : null}
        </>
      ) : null}
    </div>
  );

  if (variant === "inline") {
    if (!open) return null;
    return (
      <section className="paper-panel paper-lesson-inline" aria-label="倉位教學">
        <div className="paper-lesson-inline__bar">
          <h3 className="paper-step" style={{ marginTop: 0 }}>
            倉位 · 走勢 · 教學
          </h3>
          <button
            type="button"
            className="paper-button paper-button--ghost"
            onClick={onClose}
          >
            收起
          </button>
        </div>
        {body}
      </section>
    );
  }

  return (
    <FloatingWindow
      open={open}
      onClose={onClose}
      storageKey="paper-trade-lesson"
      origin={origin}
      title="倉位詳情 · 教學重播"
      subtitle={subtitle}
      footer={
        <>
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => {
              setTab("replay");
            }}
          >
            去重播
          </button>
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => {
              setTab("conditions");
            }}
          >
            成交條件
          </button>
          <button type="button" className="btn btn--sm btn--primary" onClick={onClose}>
            關閉
          </button>
        </>
      }
    >
      {body}
    </FloatingWindow>
  );
}

function PositionSection({
  data,
  replayIndex,
}: {
  data: PaperTradeLesson;
  replayIndex: number | null;
}) {
  const pos = data.open_position;
  return (
    <div className="paper-lesson__section">
      <div className="paper-lesson__metrics" role="group" aria-label="倉位數字">
        <Metric
          label="品種"
          value={data.selection.contract_id || "—"}
          foot={data.selection.strategy_id || undefined}
        />
        <Metric
          label="持倉"
          value={pos ? pos.label : "空手"}
          foot={
            pos
              ? `未實現 ${fmtMoney(pos.unrealized_pnl)} · ${fmtR(pos.unrealized_r)}`
              : `已實現 ${fmtMoney(data.account.realized_pnl)}`
          }
          tone={
            pos
              ? pos.unrealized_pnl > 0
                ? "pos"
                : pos.unrealized_pnl < 0
                  ? "neg"
                  : undefined
              : undefined
          }
        />
        <Metric
          label="買入價"
          value={fmtPrice(pos?.average_entry_price ?? data.levels.entry)}
        />
        <Metric
          label="止蝕"
          value={fmtPrice(pos?.stop_price ?? data.levels.stop)}
        />
        <Metric
          label="止賺"
          value={fmtPrice(pos?.target_price ?? data.levels.target)}
        />
        <Metric
          label="現價"
          value={fmtPrice(pos?.last_price ?? data.levels.last)}
        />
      </div>

      {pos ? (
        <p className="paper-hint paper-lesson__callout">{pos.teach}</p>
      ) : (
        <p className="paper-hint paper-lesson__callout">
          而家空手。若有歷史成交，可以喺「成交條件」同「重播」睇返點入點出。
        </p>
      )}

      <PaperTradeChart
        bars={data.bars}
        markers={data.markers}
        levels={data.levels}
        replayIndex={replayIndex}
        contractLabel={data.selection.contract_id}
        height={260}
      />

      {data.trades.length > 0 ? (
        <>
          <h4 className="paper-step">
            已完成交易
            <InfoButton label="已完成交易說明">
              每一筆係完整開倉＋平倉。淨盈虧已扣模擬手續費同滑價。R
              以開倉時止蝕距離做單位。
            </InfoButton>
          </h4>
          <ul className="paper-lesson__list">
            {data.trades.map((t) => (
              <li key={t.trade_id} className="paper-lesson__list-item">
                <div className="paper-lesson__list-top">
                  <strong>
                    {t.label} {t.quantity} 張
                  </strong>
                  <span
                    className={
                      t.profitable ? "paper-pnl--pos" : "paper-pnl--neg"
                    }
                  >
                    {fmtMoney(t.net_pnl)} · {fmtR(t.net_r)}
                  </span>
                </div>
                <p className="paper-hint">
                  買入 {fmtPrice(t.entry_price)} → 離場{" "}
                  {fmtPrice(t.exit_price)}
                </p>
                <p className="paper-hint">{t.teach}</p>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {data.fills.length > 0 ? (
        <>
          <h4 className="paper-step">成交明細</h4>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>類型</th>
                  <th>方向</th>
                  <th>數量</th>
                  <th>價位</th>
                  <th>時間</th>
                </tr>
              </thead>
              <tbody>
                {data.fills.map((f) => (
                  <tr key={f.fill_id}>
                    <td>{f.label}</td>
                    <td>{f.side === "long" ? "好倉" : "淡倉"}</td>
                    <td className="table__mono">{f.quantity}</td>
                    <td className="table__mono">{fmtPrice(f.price)}</td>
                    <td className="table__mono">{fmtTime(f.event_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="paper-hint">暫時未有模擬成交。</p>
      )}
    </div>
  );
}

function ReplaySection({
  data,
  replayIndex,
  playing,
  onPlay,
  onPause,
  onSeek,
}: {
  data: PaperTradeLesson;
  replayIndex: number | null;
  playing: boolean;
  onPlay: () => void;
  onPause: () => void;
  onSeek: (index: number) => void;
}) {
  const max = Math.max(0, data.bars.length - 1);
  const index = replayIndex ?? max;
  const bar = data.bars[index];
  const visibleConditions = data.conditions.filter((c) => {
    if (!bar) return true;
    const at = Date.parse(c.at);
    const barAt = Date.parse(bar.event_at);
    if (!Number.isFinite(at) || !Number.isFinite(barAt)) return true;
    return at <= barAt;
  });

  return (
    <div className="paper-lesson__section">
      <p className="paper-hint">{data.replay.hint}</p>
      {data.bars.length === 0 ? (
        <div className="paper-empty">
          <p>
            <b>未有 K 線可以重播。</b>
          </p>
          <p>
            喺交易員詳情撳「開始模擬交易」，再「餵示範行情」或開 IB
            Gateway 收真行情。
          </p>
        </div>
      ) : (
        <>
          <PaperTradeChart
            bars={data.bars}
            markers={data.markers}
            levels={data.levels}
            replayIndex={index}
            contractLabel={data.selection.contract_id}
            height={240}
          />
          <div className="paper-lesson-replay">
            <div className="paper-lesson-replay__controls">
              <button
                type="button"
                className="paper-button paper-button--primary"
                onClick={() => {
                  if (playing) onPause();
                  else onPlay();
                }}
              >
                {playing ? "暫停重播" : "播放重播"}
              </button>
              <button
                type="button"
                className="paper-button"
                onClick={() => {
                  onSeek(0);
                }}
              >
                去開頭
              </button>
              <button
                type="button"
                className="paper-button"
                onClick={() => {
                  onSeek(max);
                }}
              >
                去最新
              </button>
            </div>
            <label className="paper-lesson-replay__slider">
              <span className="paper-hint">
                第 {index + 1} / {data.bars.length} 根
                {bar ? ` · 收 ${fmtPrice(bar.close)} · ${fmtTime(bar.event_at)}` : ""}
              </span>
              <input
                type="range"
                min={0}
                max={max}
                value={index}
                onChange={(e) => {
                  onSeek(Number(e.target.value));
                }}
              />
            </label>
          </div>
          <h4 className="paper-step">呢一刻之前達成嘅條件</h4>
          {visibleConditions.length === 0 ? (
            <p className="paper-hint">呢一刻之前未有條件紀錄。</p>
          ) : (
            <ol className="paper-lesson__conditions">
              {visibleConditions.slice(-12).map((c, i) => (
                <li key={`${c.kind}-${c.at}-${i}`}>
                  <strong>{c.title}</strong>
                  <span className="paper-hint">{c.summary}</span>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </div>
  );
}

function ConditionsSection({ data }: { data: PaperTradeLesson }) {
  return (
    <div className="paper-lesson__section">
      <div className="panel__head">
        <h4 className="paper-step" style={{ marginTop: 0 }}>
          全部達成成交條件
        </h4>
        <InfoButton label="成交條件點讀">
          由舊到新排列：策略睇盤 → 入市意圖 → 模擬成交 → 倉位更新。每一行解釋當時點解做／唔做。全部係 app
          自家模擬，唔係 IB 成交。
        </InfoButton>
      </div>
      <p className="paper-hint">
        共 {data.counts.condition_count} 項 · 成交 {data.counts.fill_count} ·
        完整交易 {data.counts.trade_count} · 待成交意圖{" "}
        {data.counts.pending_intent_count}
      </p>
      {data.conditions.length === 0 ? (
        <div className="paper-empty">
          <p>暫時未有條件紀錄。</p>
          <p>開始模擬並餵行情後，策略每一次睇盤同成交都會列喺度。</p>
        </div>
      ) : (
        <ol className="paper-lesson__conditions paper-lesson__conditions--full">
          {data.conditions.map((c, i) => (
            <li key={`${c.kind}-${c.at}-${i}`} className="paper-lesson__cond">
              <div className="paper-lesson__cond-top">
                <span className={`paper-lesson__badge paper-lesson__badge--${c.kind}`}>
                  {kindLabel(c.kind)}
                </span>
                <time className="paper-hint">{fmtTime(c.at)}</time>
              </div>
              <strong>{c.title}</strong>
              <p className="paper-hint">{c.summary}</p>
              {c.detail ? <p className="paper-hint">{c.detail}</p> : null}
              <p className="paper-lesson__teach-line">{c.teach}</p>
              {c.price != null ? (
                <p className="paper-hint">
                  相關價 {fmtPrice(c.price)}
                  {c.stop_price != null ? ` · 止蝕 ${fmtPrice(c.stop_price)}` : ""}
                  {c.target_price != null
                    ? ` · 止賺 ${fmtPrice(c.target_price)}`
                    : ""}
                </p>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function TeachSection({ data }: { data: PaperTradeLesson }) {
  return (
    <div className="paper-lesson__section">
      <h4 className="paper-step" style={{ marginTop: 0 }}>
        {data.teaching.headline}
      </h4>
      <p className="paper-hint">{data.teaching.intro}</p>
      <ul className="paper-lesson__tips">
        {data.teaching.tips.map((tip) => (
          <li key={tip.id} className="paper-lesson__tip">
            <strong>{tip.title}</strong>
            <p className="paper-hint">{tip.body}</p>
          </li>
        ))}
      </ul>
      <div className="paper-notice">
        <p style={{ margin: 0 }}>
          <b>記住：</b>
          模擬盤只服務研究。App 永遠唔會向 IB
          發單；所有「成交」都係自家模擬引擎寫入帳本。
        </p>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  foot,
  tone,
}: {
  label: string;
  value: string;
  foot?: string;
  tone?: "pos" | "neg";
}) {
  return (
    <div className="paper-fleet__metric">
      <span className="paper-fleet__metric-label">{label}</span>
      <span
        className={
          tone === "pos"
            ? "paper-fleet__metric-value paper-pnl--pos"
            : tone === "neg"
              ? "paper-fleet__metric-value paper-pnl--neg"
              : "paper-fleet__metric-value"
        }
      >
        {value}
      </span>
      {foot ? <span className="paper-fleet__metric-foot">{foot}</span> : null}
    </div>
  );
}

function kindLabel(kind: string): string {
  const map: Record<string, string> = {
    decision: "睇盤",
    intent: "意圖",
    entry: "開倉",
    exit: "平倉",
    position: "倉位",
  };
  return map[kind] ?? kind;
}

function fmtMoney(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function fmtR(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}R`;
}

function fmtPrice(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function fmtTime(iso: string): string {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return iso;
  try {
    return new Date(ms).toLocaleString("zh-HK", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return iso;
  }
}
