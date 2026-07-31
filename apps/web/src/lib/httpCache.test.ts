import { afterEach, describe, expect, it } from "vitest";

import {
  __resetHttpCacheForTests,
  getCachedGet,
  invalidateGetCache,
  setCachedGet,
} from "./httpCache";

afterEach(() => {
  __resetHttpCacheForTests();
});

describe("httpCache", () => {
  it("returns cached GET body within TTL", () => {
    setCachedGet("/api/v1/paper/fleet-overview", {
      status: 200,
      body: { schema: "paper_fleet_overview.v1", count: 0 },
      rawText: "{}",
    });
    const hit = getCachedGet("/api/v1/paper/fleet-overview");
    expect(hit?.body).toEqual({ schema: "paper_fleet_overview.v1", count: 0 });
  });

  it("does not cache error statuses", () => {
    setCachedGet("/api/v1/paper/x", {
      status: 503,
      body: { error: true },
      rawText: "{}",
    });
    expect(getCachedGet("/api/v1/paper/x")).toBeNull();
  });

  it("invalidateGetCache drops matching prefixes", () => {
    setCachedGet("/api/v1/paper/traders", {
      status: 200,
      body: { count: 1 },
      rawText: "{}",
    });
    setCachedGet("/api/v1/runs", {
      status: 200,
      body: { count: 2 },
      rawText: "{}",
    });
    invalidateGetCache("/api/v1/paper/");
    expect(getCachedGet("/api/v1/paper/traders")).toBeNull();
    expect(getCachedGet("/api/v1/runs")?.body).toEqual({ count: 2 });
  });
});
