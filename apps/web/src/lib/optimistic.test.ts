import { describe, expect, it } from "vitest";

import {
  hasPriorData,
  optimisticLifecycleAfter,
  optimisticPatch,
} from "./optimistic";

describe("optimistic helpers", () => {
  it("hasPriorData treats empty arrays as loaded", () => {
    expect(hasPriorData([])).toBe(true);
    expect(hasPriorData(null)).toBe(false);
  });

  it("optimisticPatch applies then can rollback", () => {
    let value = { n: 1 };
    const { next, rollback } = optimisticPatch(
      value,
      (prev) => ({ n: prev.n + 1 }),
      (v) => {
        value = v;
      },
    );
    expect(next.n).toBe(2);
    expect(value.n).toBe(2);
    rollback();
    expect(value.n).toBe(1);
  });

  it("maps runtime commands to lifecycle labels", () => {
    expect(optimisticLifecycleAfter("start", "provisioned")).toBe("running");
    expect(optimisticLifecycleAfter("pause", "running")).toBe("pausing");
    expect(optimisticLifecycleAfter("resume", "paused")).toBe("running");
    expect(optimisticLifecycleAfter("permanent-stop", "running")).toBe(
      "stopping",
    );
  });
});
