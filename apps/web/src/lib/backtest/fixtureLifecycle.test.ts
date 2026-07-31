import { afterEach, describe, expect, it, vi } from "vitest";

import { cloneAssumptions } from "./assumptions";
import {
  __abortFixtureTasks,
  __setFixtureDelay,
  resetFixtureState,
  startFixtureSession,
} from "./fixtureStore";
import { DEFAULT_ASSUMPTIONS, type FormSnapshot } from "./types";

function baseForm(): FormSnapshot {
  return {
    strategyIds: ["strategy-0001", "strategy-0002"],
    symbols: ["NQ", "YM"],
    rangeStartLocal: "2026-05-06T22:00",
    rangeEndLocal: "2026-05-08T21:00",
    rangeStartUtc: "2026-05-06T14:00:00Z",
    rangeEndUtc: "2026-05-08T13:00:00Z",
    assumptions: cloneAssumptions(DEFAULT_ASSUMPTIONS),
  };
}

describe("D33/D37 P4 fixture async lifecycle", () => {
  afterEach(() => {
    __abortFixtureTasks();
    resetFixtureState();
    __setFixtureDelay(null);
  });

  it("abort stops in-flight advance without unhandled rejection", async () => {
    const resolvers: Array<() => void> = [];
    __setFixtureDelay(
      () =>
        new Promise<void>((resolve) => {
          resolvers.push(resolve);
        }),
    );
    startFixtureSession(baseForm());
    await Promise.resolve();
    expect(resolvers.length).toBeGreaterThanOrEqual(1);

    const rejections: unknown[] = [];
    const onRej = (ev: PromiseRejectionEvent) => {
      rejections.push(ev.reason);
      ev.preventDefault();
    };
    window.addEventListener("unhandledrejection", onRej);
    __abortFixtureTasks();
    for (const r of resolvers) {
      r();
    }
    await Promise.resolve();
    await Promise.resolve();
    window.removeEventListener("unhandledrejection", onRej);
    expect(rejections).toHaveLength(0);
  });

  it("defaultDelay is safe after abort without relying on Node process", async () => {
    __setFixtureDelay(null);
    startFixtureSession(baseForm());
    __abortFixtureTasks();
    resetFixtureState();
    await Promise.resolve();
    // no throw
    expect(true).toBe(true);
    void vi;
  });
});
