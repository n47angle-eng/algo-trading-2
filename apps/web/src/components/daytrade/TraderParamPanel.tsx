import { useId, useState } from "react";

import {
  buildParamChips,
  buildParamExplainRows,
  strategyShortLabel,
  type DaytradeParamSource,
} from "../../lib/daytrade/paramExplain";

type TraderParamPanelProps = {
  source: DaytradeParamSource;
  /** denser layout for roster cards */
  compact?: boolean;
  /** default open on profile page */
  defaultOpen?: boolean;
  className?: string;
};

/**
 * Shows key session params as chips + a toggle button for full explanation.
 */
export function TraderParamPanel({
  source,
  compact = false,
  defaultOpen = false,
  className,
}: TraderParamPanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  const chips = buildParamChips(source);
  const rows = buildParamExplainRows(source);
  const notes = source.notes?.trim() ?? "";

  return (
    <div
      className={[
        "daytrade-params",
        compact ? "daytrade-params--compact" : null,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="daytrade-params__head">
        <p className="daytrade-params__strategy muted">
          {strategyShortLabel(source.strategy_id)}
        </p>
        <button
          type="button"
          className="daytrade-params__toggle"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "收起說明" : "查看說明"}
        </button>
      </div>

      {chips.length > 0 ? (
        <ul className="daytrade-params__chips" aria-label="交易參數">
          {chips.map((c) => (
            <li key={c.key} className="daytrade-params__chip">
              <span className="daytrade-params__chip-label">{c.label}</span>
              <strong className="daytrade-params__chip-value">{c.value}</strong>
            </li>
          ))}
        </ul>
      ) : null}

      <div
        id={panelId}
        className="daytrade-params__detail"
        hidden={!open}
        role="region"
        aria-label="參數詳細說明"
      >
        {notes ? (
          <div className="daytrade-params__notes">
            <h4>設計備註</h4>
            <p>{notes}</p>
          </div>
        ) : null}

        <ol className="daytrade-params__explain">
          {rows.map((row) => (
            <li key={row.key}>
              <div className="daytrade-params__explain-title">
                <span>{row.title}</span>
                <strong>{row.value}</strong>
              </div>
              <p>{row.body}</p>
            </li>
          ))}
        </ol>

        <p className="daytrade-params__footnote muted">
          止盈約 1R（風險對等）；同一根 bar 同時觸及上下沿就唔入倉。Live 用
          1m_close 成交尺，唔好比對 1s_worst 回測。
        </p>
      </div>
    </div>
  );
}
