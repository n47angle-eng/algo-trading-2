/** Read chart colors from CSS tokens — never hardcode theme hex in ChartGrid. */

export interface ChartTokenColors {
  background: string;
  text: string;
  textMuted: string;
  grid: string;
  hairline: string;
  up: string;
  down: string;
  ema18: string;
  ema50: string;
  ema90: string;
  markerEntry: string;
  markerExit: string;
  positive: string;
  negative: string;
  /** Vertical-line / annotation accent (normalized classic form). */
  verticalLine: string;
  /** Trend-day volume shade (normalized classic form). */
  trendShade: string;
  reject: string;
  warning: string;
}

/**
 * lightweight-charts cannot parse modern CSS Color 4 slash-alpha:
 * `rgb(255 255 255 / 5.2%)` → must become classic `rgba(255, 255, 255, 0.052)`.
 */
export function normalizeCssColorForChart(input: string): string {
  const raw = input.trim();
  if (!raw) {
    return raw;
  }

  // Already classic hex / named / classic rgb()/rgba() with commas — pass through.
  if (
    raw.startsWith("#") ||
    raw === "transparent" ||
    raw === "currentColor" ||
    /^rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+/i.test(raw)
  ) {
    return raw;
  }

  // Modern: rgb(r g b / a%) or rgb(r g b / 0.08) or rgb(r g b)
  const modern =
    /^rgba?\(\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)(?:\s*\/\s*([0-9.]+%?))?\s*\)$/i.exec(
      raw,
    );
  if (modern) {
    const r = Math.round(Number(modern[1]));
    const g = Math.round(Number(modern[2]));
    const b = Math.round(Number(modern[3]));
    const alphaPart = modern[4];
    if (alphaPart == null) {
      return `rgb(${r}, ${g}, ${b})`;
    }
    let a: number;
    if (alphaPart.endsWith("%")) {
      a = Number(alphaPart.slice(0, -1)) / 100;
    } else {
      a = Number(alphaPart);
    }
    if (!Number.isFinite(a)) {
      return `rgb(${r}, ${g}, ${b})`;
    }
    // trim trailing zeros for stable snapshots
    const aStr = String(Number(a.toFixed(6)));
    return `rgba(${r}, ${g}, ${b}, ${aStr})`;
  }

  return raw;
}

/** True when string is already a form LWC accepts (classic). */
export function isClassicChartColor(input: string): boolean {
  const s = input.trim();
  if (!s) {
    return false;
  }
  if (s.startsWith("#") || s === "transparent" || s === "currentColor") {
    return true;
  }
  if (/^rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+/i.test(s)) {
    return true;
  }
  // modern slash form is NOT classic
  if (/\//.test(s) && /^rgba?\(/i.test(s)) {
    return false;
  }
  return false;
}

/**
 * Resolve a CSS custom property to a concrete color string.
 * Walks nested `var(--x)` chains (e.g. --chart-grid → --color-grid → rgb(... / %)).
 * Always normalizes so lightweight-charts never sees Color-4 slash syntax.
 */
function resolveCssCustomProperty(
  name: string,
  fallback: string,
  depth = 0,
): string {
  if (typeof document === "undefined" || depth > 8) {
    return normalizeCssColorForChart(fallback);
  }
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  if (!raw) {
    return normalizeCssColorForChart(fallback);
  }
  if (raw.startsWith("var(")) {
    const nested = /^var\(\s*(--[A-Za-z0-9-_]+)/.exec(raw);
    if (nested) {
      return resolveCssCustomProperty(nested[1], fallback, depth + 1);
    }
    return normalizeCssColorForChart(fallback);
  }
  return normalizeCssColorForChart(raw);
}

function readToken(name: string, fallback: string): string {
  return resolveCssCustomProperty(name, fallback);
}

export function readChartTokenColors(): ChartTokenColors {
  const background = readToken("--color-bg", "#0c0e13");
  const text = readToken("--color-text-primary", "#f1f3f8");
  const textMuted = readToken("--color-text-muted", "#59616f");
  const grid = readToken("--chart-grid", "rgba(255, 255, 255, 0.05)");
  const hairline = readToken("--color-hairline", "rgba(255, 255, 255, 0.085)");
  const up = readToken("--chart-up", "#30d158");
  const down = readToken("--chart-down", "#ff453a");
  const ema18 = readToken("--chart-ema-18", "#0a84ff");
  const ema50 = readToken("--chart-ema-50", "#98989d");
  const ema90 = readToken("--chart-ema-90", "#c77700");
  const markerEntry = readToken("--chart-marker-entry", "#0a84ff");
  const markerExit = readToken("--chart-marker-exit", "#9aa3b4");
  const positive = readToken("--color-positive", "#30d158");
  const negative = readToken("--color-negative", "#ff453a");
  const accent = readToken("--color-accent", "#0a84ff");
  const warning = readToken("--color-warning", "#ffd60a");
  return {
    background,
    text,
    textMuted,
    grid,
    hairline,
    up,
    down,
    ema18,
    ema50,
    ema90,
    markerEntry,
    markerExit,
    positive,
    negative,
    verticalLine: accent,
    // soft accent for trend-day volume bars
    trendShade: normalizeCssColorForChart("rgb(10 132 255 / 0.25)"),
    reject: warning,
    warning,
  };
}

export function resolveTokenColor(
  token: string,
  colors: ChartTokenColors,
): string {
  switch (token) {
    case "chart-marker-entry":
      return colors.markerEntry;
    case "chart-marker-exit":
      return colors.markerExit;
    case "color-positive":
      return colors.positive;
    case "color-negative":
      return colors.negative;
    case "chart-ema-18":
      return colors.ema18;
    case "chart-ema-50":
      return colors.ema50;
    case "chart-ema-90":
      return colors.ema90;
    case "chart-reject":
    case "color-warning":
      return colors.reject;
    case "chart-vertical":
      return colors.verticalLine;
    default:
      return colors.textMuted;
  }
}
