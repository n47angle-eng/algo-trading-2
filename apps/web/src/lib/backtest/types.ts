/** P4 domain types — live API vs owner-review fixture are never mixed. */

export type BacktestMode = "live" | "owner-review";

export type JobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type CheckStatus = "pass" | "warn" | "block" | "unknown";

export interface AssumptionSnapshot {
  initialCapital: number;
  /** Per-symbol fee, per side per contract. */
  fees: Record<string, number>;
  slippageTicks: {
    breakout: number;
    stop: number;
    target: number;
    dayEnd: number;
  };
}

export interface RunUnit {
  unitId: string;
  strategyId: string;
  strategyName: string;
  symbol: string;
  status: JobStatus;
  /** Trading day label — never TZ-shifted. */
  tradingDay: string | null;
  daysProcessed: number | null;
  tradeCount: number | null;
  pnlR: number | null;
  pnlUsd: number | null;
  message: string;
  errorFull: string | null;
  runId: string | null;
  resultPath: string | null;
  assumptions: AssumptionSnapshot;
  rangeStartUtc: string;
  rangeEndUtc: string;
}

export interface BacktestSession {
  sessionId: string;
  status: "running" | "completed" | "partial" | "failed";
  createdAt: string;
  startedAt: string | null;
  units: RunUnit[];
  /** Locked form snapshot for exact re-run. */
  formSnapshot: FormSnapshot;
}

export interface FormSnapshot {
  strategyIds: string[];
  symbols: string[];
  rangeStartLocal: string;
  rangeEndLocal: string;
  rangeStartUtc: string;
  rangeEndUtc: string;
  assumptions: AssumptionSnapshot;
}

export interface PrecheckState {
  coverage: { status: CheckStatus; detail: string; gapDates: string[] };
  warmup: {
    status: CheckStatus;
    detail: string;
    neededDays: number | null;
    availableDays: number | null;
    evaluableDays: number | null;
    suggestedStart: string | null;
  };
  duplicate: {
    status: CheckStatus;
    detail: string;
    priorSessionId: string | null;
    /** First completed run id of prior session, if any — for「去睇舊結果」. */
    priorRunId: string | null;
    forceRun: boolean;
  };
}

export const DEFAULT_ASSUMPTIONS: AssumptionSnapshot = {
  initialCapital: 100_000,
  fees: { NQ: 2.5, YM: 2.5, GC: 2.8 },
  slippageTicks: { breakout: 1, stop: 2, target: 0, dayEnd: 1 },
};

export const SYMBOLS = ["NQ", "YM", "GC"] as const;
