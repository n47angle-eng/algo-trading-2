import { describe, expect, it } from "vitest";

import {
  claimReloadForBuild,
  parseBuildMeta,
  shouldReloadForBuild,
} from "./autoUpdate";

describe("parseBuildMeta", () => {
  it("accepts a valid payload", () => {
    expect(parseBuildMeta({ buildId: "abc", builtAt: "2026-07-31T00:00:00Z" })).toEqual({
      buildId: "abc",
      builtAt: "2026-07-31T00:00:00Z",
    });
  });

  it("rejects missing buildId", () => {
    expect(parseBuildMeta({})).toBeNull();
    expect(parseBuildMeta(null)).toBeNull();
    expect(parseBuildMeta({ buildId: 1 })).toBeNull();
  });
});

describe("shouldReloadForBuild", () => {
  it("reloads only when both ids are real and differ", () => {
    expect(shouldReloadForBuild("a", "b")).toBe(true);
    expect(shouldReloadForBuild("a", "a")).toBe(false);
    expect(shouldReloadForBuild("dev", "b")).toBe(false);
    expect(shouldReloadForBuild("a", "dev")).toBe(false);
    expect(shouldReloadForBuild("", "b")).toBe(false);
  });
});

describe("claimReloadForBuild", () => {
  it("claims a build only once per session", () => {
    sessionStorage.clear();
    expect(claimReloadForBuild("build-1")).toBe(true);
    expect(claimReloadForBuild("build-1")).toBe(false);
    expect(claimReloadForBuild("build-2")).toBe(true);
  });
});
