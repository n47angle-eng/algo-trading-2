import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { humanizeApiError } from "../../lib/net/errorMessage";
import { resetNetStateForTest, trackedFetch } from "../../lib/net/transport";
import { ToastProvider } from "./Toast";
import { useToast } from "./toastContext";

function Harness() {
  const { showError, showSuccess } = useToast();
  return (
    <div>
      <button
        type="button"
        onClick={() => {
          showError(humanizeApiError(503, "boom detail"));
        }}
      >
        出錯
      </button>
      <button
        type="button"
        onClick={() => {
          showSuccess("已建立交易員");
        }}
      >
        成功
      </button>
    </div>
  );
}

describe("ToastProvider", () => {
  beforeEach(() => {
    resetNetStateForTest();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    resetNetStateForTest();
  });

  it("keeps an error on screen and only removes it when dismissed", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );

    await user.click(screen.getByRole("button", { name: "出錯" }));
    const toast = await screen.findByTestId("toast-error");
    // role=alert, because a failure the owner misses reads like success.
    expect(toast).toHaveAttribute("role", "alert");
    expect(toast).toHaveTextContent("資料服務出錯");

    await user.click(screen.getByRole("button", { name: "關閉通知" }));
    expect(screen.queryByTestId("toast-error")).not.toBeInTheDocument();
  });

  it("hides the raw技術原文 until 詳情 is opened", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );

    await user.click(screen.getByRole("button", { name: "出錯" }));
    expect(screen.queryByText("boom detail")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "詳情" }));
    expect(screen.getByText("boom detail")).toBeInTheDocument();
  });

  it("auto-dismisses a success after 3s", async () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );

    // fireEvent, not userEvent: userEvent runs its own timers, which deadlock
    // against vi.useFakeTimers in this file.
    fireEvent.click(screen.getByRole("button", { name: "成功" }));
    expect(screen.getByTestId("toast-success")).toHaveTextContent(
      "已建立交易員",
    );

    await act(async () => {
      vi.advanceTimersByTime(3100);
      await Promise.resolve();
    });
    expect(screen.queryByTestId("toast-success")).not.toBeInTheDocument();
  });

  it("collapses a repeated failure instead of stacking it every poll", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );

    const trigger = screen.getByRole("button", { name: "出錯" });
    await user.click(trigger);
    await user.click(trigger);
    await user.click(trigger);

    expect(screen.getAllByTestId("toast-error")).toHaveLength(1);
  });

  it("surfaces a failed write without any page wiring", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    render(
      <ToastProvider>
        <span>內容</span>
      </ToastProvider>,
    );

    await act(async () => {
      await trackedFetch("/api/v1/paper/traders", { method: "POST" }).catch(
        () => undefined,
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId("toast-error")).toHaveTextContent(
        "連唔到資料服務",
      );
    });
  });

  it("leaves every failed read alone — the page and the offline bar own those", async () => {
    render(
      <ToastProvider>
        <span>內容</span>
      </ToastProvider>,
    );

    // An HTTP error on a read is already rendered in place by the page that
    // asked for it.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("no", { status: 500 })),
    );
    await act(async () => {
      await trackedFetch("/api/v1/runs");
    });
    expect(screen.queryByTestId("toast-error")).not.toBeInTheDocument();

    // An unreachable service on a read is the offline bar's job; a toast here
    // would sit on top of that bar on a phone, saying the same thing twice.
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    await act(async () => {
      await trackedFetch("/api/v1/runs").catch(() => undefined);
    });
    expect(screen.queryByTestId("toast-error")).not.toBeInTheDocument();

    await act(async () => {
      await trackedFetch("/api/v1/paper/traders", { method: "POST" }).catch(
        () => undefined,
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("toast-error")).toBeInTheDocument();
    });
  });

  it("never prints the request path in the headline", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("no", { status: 500 })),
    );
    render(
      <ToastProvider>
        <span>內容</span>
      </ToastProvider>,
    );

    await act(async () => {
      await trackedFetch("/api/v1/paper/traders", { method: "POST" });
    });

    const title = await screen.findByTestId("toast-error");
    expect(
      title.querySelector(".toast__title")?.textContent ?? "",
    ).not.toContain("api/v1");
  });
});
