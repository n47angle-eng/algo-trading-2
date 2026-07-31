import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  dataMayBeStale,
  formatFreshness,
  writesBlocked,
} from "../../lib/net/online";
import { resetNetStateForTest, trackedFetch } from "../../lib/net/transport";
import { OfflineBanner } from "./OfflineBanner";

function setBrowserOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", {
    configurable: true,
    get: () => value,
  });
}

describe("reachability rules", () => {
  it("blocks writes only when the browser proves there is no network", () => {
    expect(writesBlocked("offline")).toBe(true);
    // `unreachable` is the state every retry button is pressed in — blocking
    // it would break the app's own byte-exact single-retry recovery paths.
    expect(writesBlocked("unreachable")).toBe(false);
    expect(writesBlocked("ok")).toBe(false);
    // `unknown` must not block: nothing has failed, we simply have not asked.
    expect(writesBlocked("unknown")).toBe(false);
  });

  it("warns about stale data in both failure states", () => {
    expect(dataMayBeStale("offline")).toBe(true);
    expect(dataMayBeStale("unreachable")).toBe(true);
    expect(dataMayBeStale("ok")).toBe(false);
    expect(dataMayBeStale("unknown")).toBe(false);
  });

  it("says 未有資料 rather than inventing a time", () => {
    expect(formatFreshness(null)).toBe("未有資料");
  });

  it("stamps a wall-clock time in the reader's own zone", () => {
    const at = new Date(2026, 4, 5, 14, 32, 0).getTime();
    expect(formatFreshness(at)).toBe("資料停喺 14:32");
  });
});

describe("OfflineBanner", () => {
  beforeEach(() => {
    resetNetStateForTest();
    setBrowserOnline(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    resetNetStateForTest();
    setBrowserOnline(true);
  });

  it("stays out of the way until something actually fails", () => {
    render(<OfflineBanner />);
    expect(screen.queryByTestId("offline-bar")).not.toBeInTheDocument();
  });

  it("appears when a request never reaches the backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    render(<OfflineBanner />);

    await act(async () => {
      await trackedFetch("/api/v1/runs").catch(() => undefined);
    });

    const bar = await screen.findByTestId("offline-bar");
    // Wi-Fi is up, so the honest wording is "the service", not "you are offline".
    expect(bar).toHaveTextContent("連唔到資料服務");
    expect(bar).toHaveTextContent("顯示緊之前拎到嘅資料");
    expect(bar).toHaveTextContent("改嘢可能唔成功");
  });

  it("says 離線 when the browser itself reports no network", async () => {
    setBrowserOnline(false);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    render(<OfflineBanner />);

    await act(async () => {
      await trackedFetch("/api/v1/runs").catch(() => undefined);
    });

    expect(await screen.findByTestId("offline-bar")).toHaveTextContent(
      "而家離線",
    );
  });

  it("does not appear for an HTTP error — the service answered", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("no", { status: 503 })),
    );
    render(<OfflineBanner />);

    await act(async () => {
      await trackedFetch("/api/v1/runs");
    });
    expect(screen.queryByTestId("offline-bar")).not.toBeInTheDocument();
  });

  it("clears itself once a request comes back, via 再試", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<OfflineBanner />);

    await act(async () => {
      await trackedFetch("/api/v1/runs").catch(() => undefined);
    });
    await screen.findByTestId("offline-bar");

    await user.click(screen.getByRole("button", { name: "再試" }));

    await waitFor(() => {
      expect(screen.queryByTestId("offline-bar")).not.toBeInTheDocument();
    });
  });
});
