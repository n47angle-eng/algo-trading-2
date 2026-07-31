import { Link } from "react-router-dom";

import type { RunSummary } from "../../api/types";
import { formatNumber, formatPct, toneForSigned } from "./format";
import { ScorecardChips } from "./ScorecardChips";

interface RunsCompareTableProps {
  runs: RunSummary[];
}

/**
 * docs/03 P5: Batch 對比總表 — 每 run 一行淨利R/勝率/PF/MaxDD/記分卡 chips。
 * Matrix row: Batch 對比總表（P5 入口層）.
 */
export function RunsCompareTable({ runs }: RunsCompareTableProps) {
  if (!runs.length) {
    return <p className="state-msg">呢個 batch 冇 run（或 results 目錄空白）。</p>;
  }

  return (
    <div className="table-wrap">
      <table className="table" aria-label="Run 對比總表">
        <thead>
          <tr>
            <th>Run</th>
            <th>合約</th>
            <th>驗證</th>
            <th>交易</th>
            <th>淨利 R</th>
            <th>勝率</th>
            <th>PF</th>
            <th>MaxDD</th>
            <th>記分卡</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const netTone = toneForSigned(run.net_r);
            const netClass =
              netTone === "pos"
                ? "table__mono metric-card__value--pos"
                : netTone === "neg"
                  ? "table__mono metric-card__value--neg"
                  : "table__mono";
            return (
              <tr key={run.run_id}>
                <td>
                  <Link className="table__link" to={`/results/${run.run_id}`}>
                    {run.run_id}
                  </Link>
                </td>
                <td className="table__mono table__cell--nowrap">
                  {run.contract_id ?? "—"}
                </td>
                <td>{run.validation_run ? "val" : "—"}</td>
                <td className="table__mono">
                  {formatNumber(run.trade_count, 0)}
                </td>
                <td className={netClass}>{formatNumber(run.net_r)}</td>
                <td className="table__mono">{formatPct(run.win_rate)}</td>
                <td className="table__mono">{formatNumber(run.profit_factor)}</td>
                <td className="table__mono">
                  {formatNumber(run.max_drawdown_pnl)}
                </td>
                <td>
                  {/* [072] polish 1: one-line roll-up; full grid lives on the detail page. */}
                  <ScorecardChips items={run.scorecard_statuses} compact />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
