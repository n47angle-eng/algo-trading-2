/**
 * Owner-review fixture — completely isolated from live API and normal storage.
 * Never imported by live submit path.
 */

import { capitalPerUnit, cloneAssumptions } from "./assumptions";
import { formIdentity, sameIdentity } from "./identity";
import {
  DEFAULT_ASSUMPTIONS,
  type AssumptionSnapshot,
  type BacktestSession,
  type FormSnapshot,
  type JobStatus,
  type PrecheckState,
  type RunUnit,
} from "./types";

const FIXTURE_NS = "__owner_review_p4_v1__";

/** How long units 3–4 stay queued so Owner can cancel (D15: 10s). */
export const FIXTURE_QUEUE_HOLD_MS = 10_000;

export interface FixtureStrategy {
  strategy_id: string;
  name: string;
  status: "confirmed";
  universe: { contracts: string[]; session: string };
  spec: {
    regime_separation_percentile: number;
    pullback_ema_period: number;
    universe_session: string;
    risk_pct: number;
    daily_loss_limit_r: number;
  };
}

/**
 * Both strategies authorize NQ + YM so default 2×2 = 4 units.
 * (D1: previous strategy-0002 only had NQ+GC → YM selected+disabled → 3 units.)
 */
export const FIXTURE_STRATEGIES: FixtureStrategy[] = [
  {
    strategy_id: "strategy-0001",
    name: "Trend 回踩 18EMA",
    status: "confirmed",
    universe: { contracts: ["NQ", "YM"], session: "eth" },
    spec: {
      regime_separation_percentile: 50,
      pullback_ema_period: 18,
      universe_session: "eth",
      risk_pct: 1.0,
      daily_loss_limit_r: 3,
    },
  },
  {
    strategy_id: "strategy-0002",
    name: "Trend 回踩 90EMA",
    status: "confirmed",
    universe: { contracts: ["NQ", "YM"], session: "rth" },
    spec: {
      regime_separation_percentile: 65,
      pullback_ema_period: 90,
      universe_session: "rth",
      risk_pct: 0.75,
      daily_loss_limit_r: 2,
    },
  },
];

interface FixtureState {
  sessions: BacktestSession[];
  activeSessionId: string | null;
  cancelled: string[];
}

function emptyState(): FixtureState {
  return { sessions: [], activeSessionId: null, cancelled: [] };
}

/** In-memory only for the SPA lifetime of owner-review — not localStorage. */
let memory: FixtureState = emptyState();

/** Injectable delay for tests (fake timers). Browser timer ids are numbers. */
const pendingTimeouts = new Set<number>();
/** Generation token — advanceFixture aborts if generation changes (D33). */
let fixtureEpoch = 0;
const abortedSessions = new Set<string>();

function defaultDelay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    if (typeof window === "undefined" || typeof window.setTimeout !== "function") {
      // Teardown after jsdom gone — resolve quietly, never touch window.
      resolve();
      return;
    }
    const id = window.setTimeout(() => {
      pendingTimeouts.delete(id);
      resolve();
    }, ms);
    pendingTimeouts.add(id);
  });
}

let delayImpl: (ms: number) => Promise<void> = defaultDelay;

export function __setFixtureDelay(
  impl: ((ms: number) => Promise<void>) | null,
): void {
  delayImpl = impl ?? defaultDelay;
}

/** Cancel pending fixture timers / mark in-flight advances aborted (D33). */
export function __abortFixtureTasks(): void {
  fixtureEpoch += 1;
  if (typeof window !== "undefined" && typeof window.clearTimeout === "function") {
    for (const id of pendingTimeouts) {
      window.clearTimeout(id);
    }
  }
  pendingTimeouts.clear();
  abortedSessions.clear();
}

export function resetFixtureState(): void {
  __abortFixtureTasks();
  memory = emptyState();
}

export function getFixtureState(): FixtureState {
  return memory;
}

/** Intersection of authorized contracts across selected strategies. */
export function authorizedContractIntersection(
  strategyIds: string[],
  catalog: FixtureStrategy[] = FIXTURE_STRATEGIES,
): Set<string> {
  if (strategyIds.length === 0) {
    return new Set<string>(["NQ", "YM", "GC"]);
  }
  let acc: string[] | null = null;
  for (const id of strategyIds) {
    const st = catalog.find((s) => s.strategy_id === id);
    const list = (st?.universe.contracts ?? []).map((c) => c.toUpperCase());
    acc = acc ? acc.filter((c) => list.includes(c)) : list;
  }
  return new Set<string>(acc ?? []);
}

/** Count units for strategy×symbol cartesian over authorized pairs only. */
export function countAuthorizedUnits(
  strategyIds: string[],
  symbols: string[],
  catalog: FixtureStrategy[] = FIXTURE_STRATEGIES,
): number {
  let n = 0;
  for (const strategyId of strategyIds) {
    const st = catalog.find((s) => s.strategy_id === strategyId);
    const allowed = new Set(
      (st?.universe.contracts ?? []).map((c) => c.toUpperCase()),
    );
    for (const sym of symbols) {
      if (allowed.has(sym.toUpperCase())) {
        n += 1;
      }
    }
  }
  return n;
}

function findDuplicateSession(form: FormSnapshot): BacktestSession | null {
  const current = formIdentity(form);
  for (const session of memory.sessions) {
    if (sameIdentity(current, formIdentity(session.formSnapshot))) {
      return session;
    }
  }
  return null;
}

export function fixturePrechecks(
  form: FormSnapshot,
  forceDuplicate: boolean,
): PrecheckState {
  const prior = findDuplicateSession(form);
  const same = prior != null;

  const priorRunId =
    prior?.units.find((u) => u.status === "completed" && u.runId)?.runId ??
    null;

  const coverageSymbols =
    form.symbols.length > 0
      ? form.symbols.map((s) => s.toUpperCase()).join("／")
      : "所選合約";

  return {
    coverage: {
      status: "pass",
      detail: `所選範圍內 ${coverageSymbols} 覆蓋完整，冇未裁決缺口日。`,
      gapDates: [],
    },
    warmup: {
      // D0 ([175]): copy must match p4-backtest.html #6 — exact 95 run-prior
      // settled native daily dates, and the only fixes are backfilling earlier
      // data or pushing the start date LATER.  "提前" is forbidden in any UI.
      status: "warn",
      detail:
        "「回踩 90EMA」嘅 Daily regime 實際需要 95 個已收市交易日。今次開始日前可用 64 日 → 呢個策略實際可評估嘅日子係 0。建議回填更早原生日線，或將開始日推後至 2026-06-16。",
      neededDays: 95,
      availableDays: 64,
      evaluableDays: 0,
      suggestedStart: "2026-06-16",
    },
    duplicate: {
      status: same && !forceDuplicate ? "block" : "pass",
      detail: same
        ? "已有相同策略版本＋合約＋時間範圍嘅回測。"
        : "未發現完全相同組合。",
      priorSessionId: same ? prior!.sessionId : null,
      priorRunId,
      forceRun: forceDuplicate,
    },
  };
}

export function startFixtureSession(form: FormSnapshot): BacktestSession {
  const capital = capitalPerUnit(
    form.assumptions.initialCapital,
    form.strategyIds.length * form.symbols.length,
  );
  const assumptions: AssumptionSnapshot = {
    ...cloneAssumptions(form.assumptions),
    initialCapital: capital,
  };
  const units: RunUnit[] = [];
  for (const strategyId of form.strategyIds) {
    const st = FIXTURE_STRATEGIES.find((s) => s.strategy_id === strategyId);
    for (const symbol of form.symbols) {
      if (!st?.universe.contracts.includes(symbol)) {
        continue;
      }
      units.push({
        unitId: `${FIXTURE_NS}-${strategyId}-${symbol}-${Date.now()}-${units.length}`,
        strategyId,
        strategyName: st?.name ?? strategyId,
        symbol,
        status: "queued",
        tradingDay: null,
        daysProcessed: null,
        tradeCount: null,
        pnlR: null,
        pnlUsd: null,
        message: "等緊開始",
        errorFull: null,
        runId: null,
        resultPath: null,
        assumptions: {
          ...assumptions,
          fees: { ...assumptions.fees },
          slippageTicks: { ...assumptions.slippageTicks },
        },
        rangeStartUtc: form.rangeStartUtc,
        rangeEndUtc: form.rangeEndUtc,
      });
    }
  }
  const session: BacktestSession = {
    sessionId: `${FIXTURE_NS}-session-${Date.now()}`,
    status: "running",
    createdAt: new Date().toISOString(),
    startedAt: new Date().toISOString(),
    units,
    formSnapshot: {
      ...form,
      assumptions: cloneAssumptions(form.assumptions),
    },
  };
  memory.sessions = [session, ...memory.sessions];
  memory.activeSessionId = session.sessionId;
  void advanceFixture(session.sessionId);
  return session;
}

function markRunning(unit: RunUnit): void {
  unit.status = "running";
  unit.message = "進行中";
  unit.tradingDay = "2026-05-07";
  unit.daysProcessed = 1;
  unit.tradeCount = 0;
  unit.pnlR = 0;
  unit.pnlUsd = 0;
}

function markCompleted(unit: RunUnit, index: number): void {
  unit.status = "completed";
  unit.message = "完成";
  unit.runId = `fixture-run-${index + 1}`;
  // D16: keep owner-review scenario when jumping to P5 fixture detail
  unit.resultPath = `/results/fixture-run-${index + 1}?scenario=owner-review`;
  unit.tradingDay = "2026-05-08";
  unit.daysProcessed = 2;
  unit.tradeCount = index === 0 ? 0 : 2;
  unit.pnlR = index === 0 ? 0 : 1.2;
  unit.pnlUsd = index === 0 ? 0 : 540;
}

function markFailed(unit: RunUnit): void {
  unit.status = "failed";
  unit.message = "數據缺口";
  // D8: error text must match the failing unit's symbol (not hard-coded NQ).
  unit.errorFull =
    `coverage: ${unit.symbol} 2026-05-08 missing bars — gap 09:30–10:15 UTC\n` +
    `fix: extend download or exclude trading day`;
  unit.tradingDay = "2026-05-08";
  unit.daysProcessed = 2;
  unit.tradeCount = 1;
  unit.pnlR = 0.4;
  unit.pnlUsd = 180;
}

function isCancelled(unit: RunUnit): boolean {
  return (
    unit.status === "cancelled" || memory.cancelled.includes(unit.unitId)
  );
}

/**
 * Deterministic Owner-handtest sequence (D5):
 * 1. unit0 completes quickly
 * 2. unit1 (fail target) enters running
 * 3. unit2/unit3 stay queued ≥ FIXTURE_QUEUE_HOLD_MS
 * 4. Owner may cancel queued
 * 5. unit1 fails
 * 6. remaining queued (if any) complete
 * Final can show completed + failed + cancelled together.
 */
async function advanceFixture(sessionId: string): Promise<void> {
  const epoch = fixtureEpoch;
  const stillLive = () =>
    epoch === fixtureEpoch && !abortedSessions.has(sessionId);

  const session = memory.sessions.find((s) => s.sessionId === sessionId);
  if (!session) {
    return;
  }
  const units = session.units;
  if (units.length === 0) {
    session.status = "completed";
    return;
  }

  // --- unit 0: complete quickly ---
  const u0 = units[0];
  if (!isCancelled(u0)) {
    markRunning(u0);
    await delayImpl(80);
    if (!stillLive()) {
      return;
    }
    if (!isCancelled(u0) && u0.status === "running") {
      markCompleted(u0, 0);
    }
  } else {
    u0.status = "cancelled";
    u0.message = "未開始已被取消";
  }

  // --- unit 1: start running (will fail after queue hold) ---
  const u1 = units[1];
  if (u1 && !isCancelled(u1)) {
    markRunning(u1);
    u1.tradingDay = "2026-05-08";
    u1.daysProcessed = 2;
    u1.tradeCount = 1;
    u1.pnlR = 0.4;
    u1.pnlUsd = 180;
  } else if (u1) {
    u1.status = "cancelled";
    u1.message = "未開始已被取消";
  }

  // --- hold remaining queued so Owner can cancel ---
  await delayImpl(FIXTURE_QUEUE_HOLD_MS);
  if (!stillLive()) {
    return;
  }

  // --- unit 1 fails (if still running) ---
  if (u1 && u1.status === "running") {
    markFailed(u1);
  }

  // --- process any still-queued units to completion ---
  for (let i = 2; i < units.length; i++) {
    if (!stillLive()) {
      return;
    }
    const unit = units[i];
    if (isCancelled(unit) || unit.status === "cancelled") {
      unit.status = "cancelled";
      unit.message = "未開始已被取消";
      continue;
    }
    if (unit.status !== "queued") {
      continue;
    }
    markRunning(unit);
    await delayImpl(80);
    if (!stillLive()) {
      return;
    }
    if (isCancelled(unit)) {
      unit.status = "cancelled";
      unit.message = "未開始已被取消";
      continue;
    }
    markCompleted(unit, i);
  }

  if (stillLive()) {
    finalizeSession(session);
  }
}

function finalizeSession(session: BacktestSession): void {
  const anyFailed = session.units.some((u) => u.status === "failed");
  const anyCancelled = session.units.some((u) => u.status === "cancelled");
  const allTerminal = session.units.every((u) =>
    ["completed", "failed", "cancelled"].includes(u.status),
  );
  if (!allTerminal) {
    return;
  }
  if (anyFailed || anyCancelled) {
    const allOk = session.units.every((u) => u.status === "completed");
    session.status = allOk ? "completed" : "partial";
  } else {
    session.status = "completed";
  }
}

export function cancelFixtureQueued(sessionId: string): void {
  const session = memory.sessions.find((s) => s.sessionId === sessionId);
  if (!session) {
    return;
  }
  for (const unit of session.units) {
    if (unit.status === "queued") {
      memory.cancelled.push(unit.unitId);
      unit.status = "cancelled";
      unit.message = "未開始已被取消";
    }
    // running / completed / failed must NOT be touched
  }
  finalizeSession(session);
}

export function getFixtureSession(id: string): BacktestSession | null {
  return memory.sessions.find((s) => s.sessionId === id) ?? null;
}

export function listFixtureSessions(): BacktestSession[] {
  return memory.sessions;
}

export function fixtureAssumptionsDefault(): AssumptionSnapshot {
  return cloneAssumptions(DEFAULT_ASSUMPTIONS);
}

/** For tests: mark unit status without waiting. */
export function __testSetUnitStatus(
  sessionId: string,
  unitId: string,
  status: JobStatus,
): void {
  const s = memory.sessions.find((x) => x.sessionId === sessionId);
  const u = s?.units.find((x) => x.unitId === unitId);
  if (u) {
    u.status = status;
  }
}
