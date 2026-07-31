import { describe, expect, it } from "vitest";

import { humanizeApiError, humanizeThrown } from "./errorMessage";

/**
 * The banned-vocabulary rule is the point of this module, so the tests assert
 * the *absence* of engineering words in the owner-facing half — a title that
 * quietly starts passing the raw string through would go unnoticed otherwise.
 */
const FORBIDDEN_IN_TITLE = [
  "IB_HOST",
  "IB_PORT",
  "environment",
  "run",
  "batch",
  "rth",
  "eth",
  "None",
  "Traceback",
  "null",
  "undefined",
  "api/v1",
];

function expectOwnerSafe(title: string) {
  for (const word of FORBIDDEN_IN_TITLE) {
    expect(title.toLowerCase()).not.toContain(word.toLowerCase());
  }
}

describe("humanizeApiError", () => {
  it("turns the real IB env-var failure into an owner sentence and keeps the原文 in detail", () => {
    const raw =
      "500 /api/v1/system/ib-status: missing required IB environment " +
      "variables: IB_HOST, IB_PORT, IB_CLIENT_ID";
    const human = humanizeApiError(500, raw);

    expect(human.title).toBe("IB 連接未設定");
    expectOwnerSafe(human.title);
    expectOwnerSafe(human.hint);
    // Nothing is discarded — the engineer still gets the exact bytes.
    expect(human.detail).toBe(raw);
  });

  it("status 0 means nothing ever answered, not a server error", () => {
    const human = humanizeApiError(0, "TypeError: Failed to fetch");
    expect(human.title).toBe("連唔到資料服務");
    expect(human.title).not.toContain("出錯");
  });

  it("separates the owner's input (4xx) from the service's problem (5xx)", () => {
    expect(humanizeApiError(422, "").title).toBe("填入嘅內容唔合格式");
    expect(humanizeApiError(409, "").title).toBe("同現有紀錄有衝突，冇改到");
    expect(humanizeApiError(503, "").title).toBe("資料服務出錯");
    expect(humanizeApiError(404, "").title).toBe("搵唔到呢項資料");
  });

  it("keeps every status family free of engineering vocabulary", () => {
    for (const status of [0, 401, 403, 404, 409, 418, 422, 429, 500, 503]) {
      const human = humanizeApiError(status, `raw ${String(status)} body`);
      expectOwnerSafe(human.title);
      expectOwnerSafe(human.hint);
    }
  });

  it("prefers a recognised phrase over the status family", () => {
    // A 500 carrying a refused connection is a reachability problem, and
    // saying "服務出錯" would send the owner looking in the wrong place.
    const human = humanizeApiError(500, "ECONNREFUSED 127.0.0.1:8000");
    expect(human.title).toBe("連唔到資料服務");
  });

  it("never invents a detail the caller did not supply", () => {
    expect(humanizeApiError(500, "").detail).toBe("");
  });
});

describe("humanizeThrown", () => {
  it("reads a leading HTTP status out of an Error message", () => {
    const human = humanizeThrown(new Error("422 /api/v1/strategies: bad"));
    expect(human.title).toBe("填入嘅內容唔合格式");
    expect(human.detail).toBe("422 /api/v1/strategies: bad");
  });

  it("treats a thrown non-Error as an unreachable service, not a 500", () => {
    const human = humanizeThrown("boom");
    expect(human.title).toBe("連唔到資料服務");
  });
});
