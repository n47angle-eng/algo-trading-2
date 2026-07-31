/** Set-semantic identity for strategy versions × contracts × UTC range. */

export interface RunIdentity {
  strategyIds: string[];
  symbols: string[];
  rangeStartUtc: string;
  rangeEndUtc: string;
}

/** Stable key — order of strategyIds/symbols does not matter. */
export function identityKey(id: RunIdentity): string {
  const strategies = [...id.strategyIds].map(String).sort().join("\0");
  const symbols = [...id.symbols].map((s) => s.toUpperCase()).sort().join("\0");
  return `${strategies}|${symbols}|${id.rangeStartUtc}|${id.rangeEndUtc}`;
}

export function sameIdentity(a: RunIdentity, b: RunIdentity): boolean {
  return identityKey(a) === identityKey(b);
}

export function formIdentity(form: {
  strategyIds: string[];
  symbols: string[];
  rangeStartUtc: string;
  rangeEndUtc: string;
}): RunIdentity {
  return {
    strategyIds: form.strategyIds,
    symbols: form.symbols,
    rangeStartUtc: form.rangeStartUtc,
    rangeEndUtc: form.rangeEndUtc,
  };
}
