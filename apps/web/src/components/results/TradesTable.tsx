import { formatNumber } from "./format";

interface TradesTableProps {
  trades: Array<Record<string, unknown>>;
}

/** docs/03 P5: 交易列表 — fields from trades sidecar only. */
export function TradesTable({ trades }: TradesTableProps) {
  if (!trades.length) {
    return (
      <section className="panel" role="region" aria-label="交易列表">
        <h2 className="panel__title">交易列表</h2>
        <p className="state-msg">0 筆成交（trades sidecar 為空）。</p>
      </section>
    );
  }

  return (
    <section className="panel" role="region" aria-label="交易列表">
      <h2 className="panel__title">交易列表 · {trades.length}</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>方向</th>
              <th>入場</th>
              <th>離場</th>
              <th>淨 PnL</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade, index) => (
              <tr key={String(trade.trade_id ?? index)}>
                <td className="table__mono">{index + 1}</td>
                <td>{String(trade.direction ?? "—")}</td>
                <td className="table__mono">
                  {formatNumber(
                    typeof trade.entry_price === "number" ? trade.entry_price : null,
                    2,
                  )}
                </td>
                <td className="table__mono">
                  {formatNumber(
                    typeof trade.exit_price === "number" ? trade.exit_price : null,
                    2,
                  )}
                </td>
                <td className="table__mono">
                  {formatNumber(
                    typeof trade.net_pnl === "number" ? trade.net_pnl : null,
                    2,
                  )}
                </td>
                <td>{String(trade.exit_reason ?? "—")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
