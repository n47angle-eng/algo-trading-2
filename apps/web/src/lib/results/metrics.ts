/** D22: explicit 0 ≠ missing. */

export function parseOptionalNumber(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    if (Number.isFinite(n)) {
      return n;
    }
  }
  return null;
}

/** Display: null → 未提供; 0 → "0" */
export function formatOptionalNumber(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "未提供";
  }
  return value.toFixed(digits);
}

export function isExplicitZero(value: number | null | undefined): boolean {
  return value === 0;
}
