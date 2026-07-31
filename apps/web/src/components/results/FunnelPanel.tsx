import type { FunnelV1 } from "../../api/types";
import { formatNumber } from "./format";

interface FunnelPanelProps {
  funnel: FunnelV1 | null | undefined;
}

interface FunnelRow {
  key: string;
  label: string;
  value: number | undefined;
}

/**
 * docs/03 + WO-004 funnel.v1 dual units — display only, no recompute.
 *
 * [072] polish 3: day-level and evaluation-level counts are **different units**
 * and are therefore drawn as two separate groups, each scaled to its own
 * maximum. Sharing one 0–N scale made `26 trend days` look like an upstream
 * stage of `294 evaluations`, which reads either as "the funnel grew" or as a
 * 9% pass rate — both wrong.
 */
export function FunnelPanel({ funnel }: FunnelPanelProps) {
  if (!funnel) {
    return (
      <section className="panel" role="region" aria-label="機會漏斗">
        <h2 className="panel__title">機會漏斗</h2>
        <p className="state-msg">此 run 未寫入 funnel 段。</p>
      </section>
    );
  }

  const dayRows: FunnelRow[] = [
    {
      key: "daily_trend_days",
      label: "Daily TREND 日",
      value: funnel.daily_trend_days,
    },
  ];

  const evaluationRows: FunnelRow[] = [
    {
      key: "evaluations_passing_daily_gate",
      label: "過 Daily 閘",
      value: funnel.evaluations_passing_daily_gate,
    },
    {
      key: "evaluations_passing_mid_gate",
      label: "過 mid 閘",
      value: funnel.evaluations_passing_mid_gate,
    },
    {
      key: "signals_created",
      label: "Signals",
      value: funnel.signals_created,
    },
    { key: "fills", label: "Fills", value: funnel.fills },
  ];

  return (
    <section className="panel" role="region" aria-label="機會漏斗">
      <h2 className="panel__title">機會漏斗 · {funnel.status}</h2>

      <FunnelGroup
        heading="日級（單位：交易日）"
        note="由 daily_regime_changed 事件砌 step function 得出"
        rows={dayRows}
        variant="days"
      />
      <FunnelGroup
        heading="評估級（單位：5m 訊號評估次數）"
        note="每次 signal_created / signal_rejected 算一次評估"
        rows={evaluationRows}
        variant="evaluations"
      />
      <p className="funnel-group__warning">
        兩組單位唔同，唔可以直接相除或者當成同一條漏斗嘅上下游——一個 TREND
        日入面本身就有幾十至幾百次 5m 評估。
      </p>

      {funnel.reject_reasons && Object.keys(funnel.reject_reasons).length > 0 ? (
        <>
          <h3 className="panel__title">Reject reasons</h3>
          <ul className="reject-list">
            {Object.entries(funnel.reject_reasons)
              .sort((a, b) => b[1] - a[1])
              .map(([reason, count]) => (
                <li key={reason}>
                  {reason}: {count}
                </li>
              ))}
          </ul>
        </>
      ) : null}
      {funnel.notes ? <p className="state-msg">{funnel.notes}</p> : null}
    </section>
  );
}

function FunnelGroup({
  heading,
  note,
  rows,
  variant,
}: {
  heading: string;
  note: string;
  rows: FunnelRow[];
  variant: "days" | "evaluations";
}) {
  // Each group is scaled to its own maximum — never across units.
  const max = Math.max(
    1,
    ...rows.map((row) => (typeof row.value === "number" ? row.value : 0)),
  );

  return (
    <div className={`funnel-group funnel-group--${variant}`}>
      <h3 className="funnel-group__heading">{heading}</h3>
      <p className="funnel-group__note">{note}</p>
      <ul className="funnel-list">
        {rows.map((row) => {
          const value = typeof row.value === "number" ? row.value : 0;
          const pct = Math.round((value / max) * 100);
          return (
            <li key={row.key} className="funnel-list__row">
              <span className="funnel-list__label">{row.label}</span>
              <span className="funnel-list__bar" aria-hidden="true">
                <i style={{ width: `${pct}%` }} />
              </span>
              <span className="funnel-list__value">
                {formatNumber(row.value, 0)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
