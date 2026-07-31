/**
 * Icon set — one stroke weight, one 24px grid, `currentColor` throughout.
 *
 * Deliberately geometric and label-free so the nav reads at a glance on a phone
 * without adding another vocabulary the owner has to learn.
 */

export type IconName =
  | "overview"
  | "strategies"
  | "data"
  | "backtest"
  | "results"
  | "paper"
  | "daytrade"
  | "settings"
  | "more"
  | "info"
  | "close"
  | "reset"
  | "guide"
  | "search"
  | "chevron"
  | "brand";

interface IconProps {
  name: IconName;
  className?: string;
}

const PATHS: Record<IconName, React.ReactNode> = {
  // Overview — four panes of a dashboard.
  overview: (
    <>
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.6" />
      <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6" />
      <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6" />
    </>
  ),
  // Strategy workbench — stacked layers being shaped.
  strategies: (
    <>
      <path d="M12 3 3 7.5l9 4.5 9-4.5L12 3Z" />
      <path d="m3 12.5 9 4.5 9-4.5" />
      <path d="m3 17 9 4.5 9-4.5" />
    </>
  ),
  // Data — stored series.
  data: (
    <>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v6c0 1.66 3.58 3 8 3s8-1.34 8-3V6" />
      <path d="M4 12v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
    </>
  ),
  // Backtest — run it.
  backtest: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M10 8.5v7l6-3.5-6-3.5Z" />
    </>
  ),
  // Results — the equity curve.
  results: (
    <>
      <path d="M3 20h18" />
      <path d="m4 15 5-5.5 4 3.5 6.5-7" />
      <path d="M15 6h4.5v4.5" />
    </>
  ),
  // Paper runtime — live pulse.
  paper: (
    <>
      <path d="M3 12h4l2.5-6.5 4 13L16 12h5" />
    </>
  ),
  // Day traders — the roster.
  daytrade: (
    <>
      <circle cx="9" cy="8" r="3.4" />
      <path d="M3 20a6.2 6.2 0 0 1 12 0" />
      <path d="M16.5 5.2a3.4 3.4 0 0 1 0 6.5" />
      <path d="M18 14.3a6.2 6.2 0 0 1 3 5.7" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3.1" />
      <path d="M19.4 14.5a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-2.9 1.2v.17a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-3-1.24l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.24-3l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 2.9-1.2V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 3 1.24l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0 1.2 2.9H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1.44Z" />
    </>
  ),
  more: (
    <>
      <circle cx="5" cy="12" r="1.4" />
      <circle cx="12" cy="12" r="1.4" />
      <circle cx="19" cy="12" r="1.4" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9.2" />
      <path d="M12 11v5.5" />
      <path d="M12 7.6h.01" />
    </>
  ),
  close: (
    <>
      <path d="m6 6 12 12" />
      <path d="M18 6 6 18" />
    </>
  ),
  // Reset — a arrow curling back to where it started.
  reset: (
    <>
      <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1" />
      <path d="M3.2 4.6v4.6h4.6" />
    </>
  ),
  // Manual — an open book.
  guide: (
    <>
      <path d="M12 6.4C10.4 5 8.5 4.3 6 4.3H3.4v13.4H6c2.5 0 4.4.7 6 2.1 1.6-1.4 3.5-2.1 6-2.1h2.6V4.3H18c-2.5 0-4.4.7-6 2.1Z" />
      <path d="M12 6.4V20" />
    </>
  ),
  search: (
    <>
      <circle cx="10.8" cy="10.8" r="6.8" />
      <path d="m20 20-4.4-4.4" />
    </>
  ),
  chevron: (
    <>
      <path d="m9 5 7 7-7 7" />
    </>
  ),
  // Brand — a candle inside the frame: research on price.
  brand: (
    <>
      <path d="M12 3.5v17" />
      <rect x="8.4" y="7.2" width="7.2" height="9.6" rx="1.4" />
    </>
  ),
};

const FILLED_STROKE: Partial<Record<IconName, number>> = {
  brand: 2.2,
  more: 0,
};

export function Icon({ name, className }: IconProps) {
  const strokeWidth = FILLED_STROKE[name] ?? 1.6;
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill={name === "more" ? "currentColor" : "none"}
      stroke={name === "more" ? "none" : "currentColor"}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
