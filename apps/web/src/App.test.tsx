import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { NAV_ITEMS } from "./nav";
import { THEME_STORAGE_KEY, type ThemeId } from "./theme/theme";

describe("App shell (WO-006 / 6-1 → WO-010 batch 1)", () => {
  beforeEach(() => {
    // BrowserRouter reads the shared jsdom history, so a test that navigated
    // would otherwise decide where the next test starts.
    window.history.pushState({}, "", "/");
    // The shell now mounts P1, which probes IB status and the batch queue.
    // Shell-level tests only care that an unavailable API degrades quietly.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("unavailable", { status: 503 })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** Flush the shell's status probes so their state lands inside act(). */
  const settle = async () => {
    await waitFor(() => {
      expect(screen.getByText(/^IB · /)).toBeInTheDocument();
    });
  };

  it("renders sidebar items with no placeholder left (工房 removed; 設定 added for PWA)", async () => {
    render(<App />);
    await settle();
    const nav = screen.getByRole("navigation", { name: "頁面" });
    for (const item of NAV_ITEMS) {
      expect(
        within(nav).getByRole("link", { name: new RegExp(item.label) }),
      ).toBeInTheDocument();
    }
    // Product pages + daytrade + settings (notifications / PWA)
    expect(NAV_ITEMS.length).toBeGreaterThanOrEqual(7);
    expect(NAV_ITEMS.some((item) => item.id === "settings")).toBe(true);
    expect(within(nav).queryByRole("link", { name: /工房/ })).not.toBeInTheDocument();
    // [205] §6.1: 模擬盤 is no longer a deferred placeholder.
    expect(within(nav).queryAllByText("soon")).toHaveLength(0);
    expect(NAV_ITEMS.filter((item) => item.placeholder)).toHaveLength(0);
  });

  it("nav subtitle for 模擬盤 stays honest about the disabled engine", () => {
    const paper = NAV_ITEMS.find((item) => item.id === "paper");
    expect(paper?.placeholder).toBeUndefined();
    expect(paper?.subtitle).toContain("模擬");
    expect(paper?.subtitle).not.toContain("placeholder");
    expect(paper?.subtitle).not.toContain("未啟用");
  });

  /*
   * [205] §6.1: /paper must reach the real P6 Stage A page, never the generic
   * `route: /paper` stub.
   */
  it("routes from the sidebar into the real P6 模擬盤 page, not a stub", async () => {
    const user = userEvent.setup();
    render(<App />);
    await settle();

    const nav = screen.getByRole("navigation", { name: "頁面" });
    await user.click(within(nav).getByRole("link", { name: /模擬盤/ }));
    // Routes are code-split, so the page arrives after the chunk resolves.
    expect(
      await screen.findByRole("heading", { name: "模擬盤" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "＋ 新增交易員" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("route: /paper")).not.toBeInTheDocument();
    expect(screen.queryByText("Placeholder")).not.toBeInTheDocument();
  });

  /*
   * P2 workbench route guard: sidebar → /strategies lands on the real page
   * (heading 策略工作台 + tab ①), not a stub.
   */
  it("routes from the sidebar into the real P2 workbench, not a stub", async () => {
    const user = userEvent.setup();
    render(<App />);
    await settle();

    const nav = screen.getByRole("navigation", { name: "頁面" });
    await user.click(within(nav).getByRole("link", { name: /策略工作台/ }));

    expect(
      await screen.findByRole("heading", { name: "策略工作台" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("region", { name: "草圖編輯" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/route: \/strategies/)).not.toBeInTheDocument();
    expect(screen.queryByText("Placeholder")).not.toBeInTheDocument();
  });

  /*
   * The page's section tabs are portalled up into the top bar (owner request:
   * "每一頁的 tab bar 都要改成為 top bar"). Asserting the DOM ancestor — not just
   * that the tabs exist — is what makes this fail if the portal ever breaks and
   * the strip silently falls back to rendering inside the page body.
   */
  it("renders the page's section tabs inside the top bar, not the page body", async () => {
    render(<App />);
    await settle();

    const bar = document.querySelector("header.shell-bar");
    expect(bar).not.toBeNull();

    const tab = await screen.findByRole("tab", { name: "連接狀態" });
    expect(bar).toContainElement(tab);

    const pageBody = document.querySelector(".main__inner");
    expect(pageBody).not.toBeNull();
    expect(pageBody).not.toContainElement(tab);
  });

  /*
   * The theme control moved out of the chrome and into the 更多 overflow panel
   * (owner request), so it must be opened before the buttons exist. Exactly one
   * control is mounted app-wide — hence getByRole, not getAllByRole.
   */
  it("switches data-theme across dark / light / nature from the 更多 panel", async () => {
    const user = userEvent.setup();
    render(<App />);
    await settle();

    expect(
      screen.queryByRole("group", { name: "主題切換" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "更多選項" }));

    const group = screen.getByRole("group", { name: "主題切換" });
    const light = within(group).getByRole("button", { name: "淺色" });
    const nature = within(group).getByRole("button", { name: "自然" });
    const dark = within(group).getByRole("button", { name: "深色" });

    await user.click(light);
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    await user.click(nature);
    expect(document.documentElement.getAttribute("data-theme")).toBe(
      "nature" satisfies ThemeId,
    );

    await user.click(dark);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("exposes touch-min token at 44px", async () => {
    render(<App />);
    await settle();
    const rootStyles = getComputedStyle(document.documentElement);
    expect(rootStyles.getPropertyValue("--touch-min").trim()).toBe("2.75rem");
  });
});
