import { Link } from "react-router-dom";

import { FloatingWindow } from "../ui/FloatingWindow";
import {
  dayTone,
  formatMoney,
  formatPercent,
  traderStatusLabel,
} from "../../lib/daytrade/traderDisplay";
import type { DaytradeTraderCard } from "../../lib/daytrade/types";
import { TraderParamPanel } from "./TraderParamPanel";

interface TraderWindowProps {
  trader: DaytradeTraderCard | null;
  origin: { x: number; y: number } | null;
  onClose: () => void;
}

/**
 * One trader, in a window you can move and resize.
 *
 * The roster answers "who is trading"; this answers "how is this one doing"
 * without leaving the roster — so comparing two traders is opening two
 * windows, not two round trips through a page load.
 */
export function TraderWindow({ trader, origin, onClose }: TraderWindowProps) {
  if (!trader) {
    return null;
  }

  const base = `/daytrade/traders/${trader.trader_id}`;

  return (
    <FloatingWindow
      open
      onClose={onClose}
      storageKey="trader"
      origin={origin}
      title={trader.display_name}
      subtitle={`${trader.symbol} · ${traderStatusLabel(trader.status)}`}
      footer={
        <>
          <Link className="btn btn--sm btn--primary" to={base}>
            打開完整檔案
          </Link>
          <Link className="btn btn--sm" to={`${base}?tab=positions`}>
            倉位
          </Link>
          <Link className="btn btn--sm" to={`${base}?tab=scorecard`}>
            成績表
          </Link>
          <Link className="btn btn--sm" to={`${base}?tab=live`}>
            即市
          </Link>
        </>
      }
    >
      <div className="metrics-grid">
        <div className="metric-card">
          <span className="metric-card__label">身家</span>
          <span className="metric-card__value">
            {formatMoney(trader.equity)}
          </span>
        </div>
        <div className="metric-card">
          <span className="metric-card__label">今日</span>
          <span
            className={
              dayTone(trader.day_return) === "up"
                ? "metric-card__value metric-card__value--pos"
                : dayTone(trader.day_return) === "down"
                  ? "metric-card__value metric-card__value--neg"
                  : "metric-card__value"
            }
          >
            {formatPercent(trader.day_return)}
          </span>
        </div>
        <div className="metric-card">
          <span className="metric-card__label">持倉</span>
          <span className="metric-card__value">{trader.positions_count}</span>
          <small className="metric-card__foot">
            {trader.open_position
              ? `${trader.open_position.side} ${String(trader.open_position.qty)} 張`
              : "而家冇揸嘢"}
          </small>
        </div>
      </div>

      <TraderParamPanel
        source={{
          symbol: trader.symbol,
          strategy_id: trader.strategy_id,
          quantity: trader.quantity,
          or_minutes: trader.or_minutes,
          no_new_entry_after: trader.no_new_entry_after,
          force_flat_time: trader.force_flat_time,
          rth_start: trader.rth_start,
          rth_end: trader.rth_end,
          timezone: trader.timezone,
          max_daily_loss_r: trader.max_daily_loss_r,
          starting_equity: trader.starting_equity,
          notes: trader.notes,
        }}
      />
    </FloatingWindow>
  );
}
