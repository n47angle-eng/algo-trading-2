/** Display helpers for P5 metrics — no strategy math, only presentation. */

export function formatNumber(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  });
}

export function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return `${(value * 100).toFixed(1)}%`;
}

export function toneForSigned(
  value: number | null | undefined,
): "pos" | "neg" | "muted" | "neutral" {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "muted";
  }
  if (value > 0) {
    return "pos";
  }
  if (value < 0) {
    return "neg";
  }
  return "neutral";
}

export function shortDim(dim: string): string {
  // "1_樣本量" → keep readable; strip only if very long
  return dim.length > 18 ? `${dim.slice(0, 16)}…` : dim;
}
