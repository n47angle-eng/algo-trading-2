/**
 * One simulated trader, as a card.
 *
 * Two shapes of the same truth:
 *   full  — the roster page: every session parameter, four deep links.
 *   quick — the glance sheet: what the owner checks mid-session and nothing
 *           else. Equity, today, position, whether it is actually trading.
 *
 * The quick shape exists because the roster used to be two taps deep behind
 * the overflow panel, and checking "how are they doing" meant leaving the page
 * you were on.
 */

import { Link } from "react-router-dom";

import {
  dayTone,
  formatMoney,
  formatPercent,
  traderStatusLabel,
} from "../../lib/daytrade/traderDisplay";
import type { DaytradeTraderCard } from "../../lib/daytrade/types";
import { TraderParamPanel } from "./TraderParamPanel";

export function TraderQuickCard({
  trader,
  onNavigate,
}: {
  trader: DaytradeTraderCard;
  onNavigate: () => void;
}) {
  return (
    <Link
      to={`/daytrade/traders/${trader.trader_id}`}
      className="trader-quick"
      onClick={onNavigate}
    >
      <span className="trader-quick__head">
        <span className="trader-quick__name">{trader.display_name}</span>
        <span className="trader-quick__symbol">{trader.symbol}</span>
      </span>

      <span className="trader-quick__metrics">
        <span className="trader-quick__metric">
          <b>{formatMoney(trader.equity)}</b>
          <small>身家</small>
        </span>
        <span
          className="trader-quick__metric"
          data-tone={dayTone(trader.day_return)}
        >
          <b>{formatPercent(trader.day_return)}</b>
          <small>今日</small>
        </span>
        <span className="trader-quick__metric">
          <b>{trader.positions_count}</b>
          <small>持倉</small>
        </span>
      </span>

      <span className="trader-quick__foot">
        {traderStatusLabel(trader.status)}
        {trader.open_position
          ? ` · ${trader.open_position.side} ${String(trader.open_position.qty)} 張`
          : ""}
      </span>
    </Link>
  );
}

export function TraderEntryCard({
  trader,
  onOpen,
}: {
  trader: DaytradeTraderCard;
  /** Opens the trader's floating window, anchored to where you clicked. */
  onOpen?: (origin: { x: number; y: number }) => void;
}) {
  return (
    <article
      className={onOpen ? "daytrade-card daytrade-card--open" : "daytrade-card"}
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      aria-label={onOpen ? `打開 ${trader.display_name}` : undefined}
      onClick={
        onOpen
          ? (event) => {
              // The four deep links inside still navigate on their own.
              if ((event.target as HTMLElement).closest("a")) return;
              onOpen({ x: event.clientX, y: event.clientY });
            }
          : undefined
      }
      onKeyDown={
        onOpen
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                const r = event.currentTarget.getBoundingClientRect();
                onOpen({ x: r.left + r.width / 2, y: r.top });
              }
            }
          : undefined
      }
    >
      <header>
        <h2>{trader.display_name}</h2>
        <span className="daytrade-card__symbol">{trader.symbol}</span>
      </header>
      <p>
        身家 <strong>{formatMoney(trader.equity)}</strong>
        {" · "}
        今日 <strong>{formatPercent(trader.day_return)}</strong>
      </p>
      <p>
        持倉 <strong>{trader.positions_count}</strong> ·{" "}
        {traderStatusLabel(trader.status)}
      </p>

      <TraderParamPanel
        compact
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

      <nav className="daytrade-card__links">
        <Link to={`/daytrade/traders/${trader.trader_id}`}>個人檔案</Link>
        <Link to={`/daytrade/traders/${trader.trader_id}?tab=positions`}>
          倉位
        </Link>
        <Link to={`/daytrade/traders/${trader.trader_id}?tab=scorecard`}>
          成績表
        </Link>
        <Link to={`/daytrade/traders/${trader.trader_id}?tab=live`}>即市</Link>
      </nav>
    </article>
  );
}
