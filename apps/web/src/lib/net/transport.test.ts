import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  OfflineWriteBlocked,
  lastSuccessAt,
  resetNetStateForTest,
  subscribeNet,
  trackedFetch,
  writesCurrentlyBlocked,
  type NetOutcome,
} from "./transport";

function collect(): { seen: NetOutcome[]; stop: () => void } {
  const seen: NetOutcome[] = [];
  const stop = subscribeNet((o) => seen.push(o));
  return { seen, stop };
}

function setBrowserOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", {
    configurable: true,
    get: () => value,
  });
}

describe("trackedFetch", () => {
  beforeEach(() => {
    resetNetStateForTest();
    setBrowserOnline(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    resetNetStateForTest();
    setBrowserOnline(true);
  });

  it("returns the response untouched — a wrapper must not change the payload", async () => {
    const body = JSON.stringify({ schema: "x.v1" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(body, { status: 200 })),
    );
    const response = await trackedFetch("/api/v1/runs");
    expect(response.status).toBe(200);
    await expect(response.text()).resolves.toBe(body);
  });

  it("reports a 2xx and stamps the freshness clock", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    const { seen, stop } = collect();

    expect(lastSuccessAt()).toBeNull();
    await trackedFetch("/api/v1/runs");
    stop();

    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({
      path: "/api/v1/runs",
      method: "GET",
      ok: true,
      status: 200,
      kind: null,
    });
    expect(lastSuccessAt()).not.toBeNull();
  });

  it("reports a non-2xx as http — the service answered, so it is reachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 503 })));
    const { seen, stop } = collect();

    await trackedFetch("/api/v1/runs");
    stop();

    expect(seen[0].kind).toBe("http");
    expect(seen[0].status).toBe(503);
    // A failed response must never move the freshness clock forward.
    expect(lastSuccessAt()).toBeNull();
  });

  it("reports a rejected fetch as transport, and still rejects", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    const { seen, stop } = collect();

    await expect(trackedFetch("/api/v1/runs")).rejects.toThrow("Failed to fetch");
    stop();

    expect(seen[0]).toMatchObject({ ok: false, status: 0, kind: "transport" });
  });

  it("stays silent on an aborted request", async () => {
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(abort)));
    const { seen, stop } = collect();

    // The app aborts its own probes on unmount; surfacing those as failures
    // would put 連唔到 on screen for a request nobody was waiting for.
    await expect(trackedFetch("/api/v1/runs")).rejects.toBe(abort);
    stop();

    expect(seen).toHaveLength(0);
  });

  it("carries the method through so writes can be told from reads", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    const { seen, stop } = collect();

    await trackedFetch("/api/v1/paper/traders", { method: "POST" });
    stop();

    expect(seen[0].method).toBe("POST");
  });

  it("survives a subscriber that throws", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    subscribeNet(() => {
      throw new Error("bad subscriber");
    });
    const { seen, stop } = collect();

    await expect(trackedFetch("/api/v1/runs")).resolves.toBeInstanceOf(Response);
    stop();
    expect(seen).toHaveLength(1);
  });

  it("refuses to send a write while the browser reports no network", async () => {
    setBrowserOnline(false);
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { seen, stop } = collect();

    await expect(
      trackedFetch("/api/v1/paper/traders", { method: "POST" }),
    ).rejects.toBeInstanceOf(OfflineWriteBlocked);
    stop();

    // Provably never sent — not "sent and failed".
    expect(fetchMock).not.toHaveBeenCalled();
    expect(seen[0].kind).toBe("blocked");
  });

  it("still lets a retry through after one failed request", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(trackedFetch("/health")).rejects.toThrow();
    // A single failure is exactly the state a retry button is pressed in; the
    // paper and results flows depend on resending the same identity once.
    expect(writesCurrentlyBlocked()).toBe(false);

    await expect(
      trackedFetch("/api/v1/paper/traders", { method: "POST" }),
    ).resolves.toBeInstanceOf(Response);
  });

  it("lets reads through even while offline, so recovery is possible", async () => {
    setBrowserOnline(false);
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(trackedFetch("/health")).resolves.toBeInstanceOf(Response);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reads globalThis.fetch at call time so a later stub still intercepts", async () => {
    const first = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", first);
    await trackedFetch("/a");

    const second = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", second);
    await trackedFetch("/b");

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });
});
