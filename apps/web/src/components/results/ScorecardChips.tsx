import type { ScorecardItem, ScorecardStatusChip } from "../../api/types";
import { shortDim } from "./format";

type ChipSource = ScorecardStatusChip | ScorecardItem;

interface ScorecardChipsProps {
  items: ChipSource[];
  /** docs/03: 八維度記分卡 chips on compare + detail */
  emptyLabel?: string;
  /**
   * Compact mode ([072] polish 1): show only the dimensions that need attention
   * plus a "N ✓" roll-up, so a compare-table row stays one line high instead of
   * wrapping eleven chips across four.
   */
  compact?: boolean;
}

function statusClass(status: string | null | undefined): string {
  switch (status) {
    case "pass":
      return "chip chip--pass";
    case "warn":
      return "chip chip--warn";
    case "insufficient_sample":
      return "chip chip--insufficient_sample";
    case "not_available_p2":
      return "chip chip--not_available_p2";
    default:
      return "chip";
  }
}

/**
 * Compact mode rolls up the statuses that say "nothing to act on" and keeps
 * `warn` / `fail` spelled out, because those name a specific dimension the
 * Owner has to judge. Order is the display order of the roll-up chips.
 */
const ROLLUP_STATUSES: ReadonlyArray<{ status: string; label: string }> = [
  { status: "pass", label: "✓" },
  { status: "insufficient_sample", label: "樣本不足" },
  { status: "not_available_p2", label: "P2 未支援" },
];

export function ScorecardChips({
  items,
  emptyLabel = "無 scorecard",
  compact = false,
}: ScorecardChipsProps) {
  if (!items.length) {
    return <span className="empty-cell">{emptyLabel}</span>;
  }

  if (!compact) {
    return (
      <div className="chip-row" role="list" aria-label="記分卡">
        {items.map((item) => {
          const dim = item.dim ?? "?";
          const status = item.status ?? "—";
          return (
            <span
              key={`${dim}-${status}`}
              className={statusClass(status)}
              role="listitem"
              title={`${dim}: ${status}`}
            >
              {shortDim(dim)} · {status}
            </span>
          );
        })}
      </div>
    );
  }

  const rolledUp = new Set(ROLLUP_STATUSES.map((entry) => entry.status));
  const attention = items.filter((item) => !rolledUp.has(item.status ?? ""));

  return (
    <div className="chip-row chip-row--compact" role="list" aria-label="記分卡">
      {ROLLUP_STATUSES.map(({ status, label }) => {
        const matching = items.filter((item) => item.status === status);
        if (!matching.length) {
          return null;
        }
        return (
          <span
            key={status}
            className={statusClass(status)}
            role="listitem"
            title={matching.map((item) => `${item.dim}: ${status}`).join("\n")}
          >
            {matching.length} {label}
          </span>
        );
      })}
      {attention.map((item) => {
        const dim = item.dim ?? "?";
        const status = item.status ?? "—";
        return (
          <span
            key={`${dim}-${status}`}
            className={statusClass(status)}
            role="listitem"
            title={`${dim}: ${status}`}
          >
            {shortDim(dim)} · {status}
          </span>
        );
      })}
    </div>
  );
}
