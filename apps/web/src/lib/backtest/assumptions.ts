import {
  DEFAULT_ASSUMPTIONS,
  type AssumptionSnapshot,
} from "./types";

export function cloneAssumptions(
  base: AssumptionSnapshot = DEFAULT_ASSUMPTIONS,
): AssumptionSnapshot {
  return {
    initialCapital: base.initialCapital,
    fees: { ...base.fees },
    slippageTicks: { ...base.slippageTicks },
  };
}

export interface AssumptionFieldErrors {
  initialCapital?: string;
  fees?: Record<string, string>;
  slippage?: Partial<Record<keyof AssumptionSnapshot["slippageTicks"], string>>;
}

export function validateAssumptions(
  a: AssumptionSnapshot,
  symbols: string[],
): AssumptionFieldErrors {
  const errors: AssumptionFieldErrors = { fees: {}, slippage: {} };
  if (!Number.isFinite(a.initialCapital)) {
    errors.initialCapital = "初始資金必須係有限數字";
  } else if (!(a.initialCapital > 0)) {
    errors.initialCapital = "初始資金必須大過 0";
  }
  for (const symbol of symbols) {
    const fee = a.fees[symbol];
    if (!Number.isFinite(fee)) {
      errors.fees![symbol] = "手續費必須係有限數字";
    } else if (fee < 0) {
      errors.fees![symbol] = "手續費不得為負數";
    }
  }
  for (const key of Object.keys(a.slippageTicks) as Array<
    keyof AssumptionSnapshot["slippageTicks"]
  >) {
    const v = a.slippageTicks[key];
    if (!Number.isFinite(v)) {
      errors.slippage![key] = "滑點必須係有限數字";
    } else if (v < 0) {
      errors.slippage![key] = "滑點不得為負數";
    } else if (!Number.isInteger(v)) {
      errors.slippage![key] = "滑點只收整數 tick";
    }
  }
  if (Object.keys(errors.fees!).length === 0) {
    delete errors.fees;
  }
  if (Object.keys(errors.slippage!).length === 0) {
    delete errors.slippage;
  }
  return errors;
}

export function hasAssumptionErrors(e: AssumptionFieldErrors): boolean {
  return Boolean(
    e.initialCapital ||
      (e.fees && Object.keys(e.fees).length) ||
      (e.slippage && Object.keys(e.slippage).length),
  );
}

/**
 * Each strategy×symbol unit gets the full capital — never split.
 * Mutation target for "shared pot" bugs.
 */
export function capitalPerUnit(totalFormCapital: number, _unitCount: number): number {
  void _unitCount;
  return totalFormCapital;
}

export function assumptionsSummary(a: AssumptionSnapshot): string {
  return `每次回測完整初始資金 USD ${a.initialCapital.toLocaleString("en-US")} · 合約實際手續費 · 保守滑點 · 保守成交`;
}
