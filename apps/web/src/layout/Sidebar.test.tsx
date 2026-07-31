import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NAV_GROUPS, NAV_ITEMS } from "../nav";
import { Sidebar } from "./Sidebar";
import {
  SIDEBAR_STORAGE_KEY,
  readSidebarCollapsed,
  writeSidebarCollapsed,
} from "./sidebarState";

function sidebar(
  collapsed: boolean,
  onToggle: () => void = () => undefined,
  peeking = false,
) {
  return (
    <MemoryRouter>
      <Sidebar
        collapsed={collapsed}
        expandedNow={!collapsed || peeking}
        peeking={peeking}
        onToggle={onToggle}
        onPointerEnter={() => undefined}
        onPointerLeave={() => undefined}
      />
    </MemoryRouter>
  );
}

function renderSidebar(collapsed: boolean, onToggle?: () => void) {
  return render(sidebar(collapsed, onToggle));
}

describe("sidebar collapse preference", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("defaults to expanded — a rail nobody asked for is a rail nobody finds", () => {
    expect(readSidebarCollapsed()).toBe(false);
  });

  it("round-trips through storage so the choice survives a reload", () => {
    writeSidebarCollapsed(true);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe("1");
    expect(readSidebarCollapsed()).toBe(true);

    writeSidebarCollapsed(false);
    expect(readSidebarCollapsed()).toBe(false);
  });

  it("survives storage being unavailable", () => {
    // Private-mode Safari throws on both read and write; a layout preference
    // must never take the app down with it.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    expect(readSidebarCollapsed()).toBe(false);
    expect(() => {
      writeSidebarCollapsed(true);
    }).not.toThrow();
  });
});

describe("Sidebar", () => {
  beforeEach(() => {
    // The foot mounts the live IB probe; these tests are about the rail, so
    // the probe is stubbed rather than left to resolve mid-assertion.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("unavailable", { status: 503 })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("groups every page under a heading, losing none of them", () => {
    renderSidebar(false);
    const nav = screen.getByRole("navigation", { name: "頁面" });

    for (const group of NAV_GROUPS) {
      expect(within(nav).getByText(group.label)).toBeInTheDocument();
    }
    for (const item of NAV_ITEMS) {
      expect(
        within(nav).getByRole("link", { name: new RegExp(item.label) }),
      ).toBeInTheDocument();
    }
  });

  it("keeps every link named when collapsed — icons must not become a quiz", () => {
    renderSidebar(true);
    const nav = screen.getByRole("navigation", { name: "頁面" });

    // CSS hides the label in the rail; it stays in the tree, and hovering
    // the rail brings the whole menu back.
    for (const item of NAV_ITEMS) {
      expect(
        within(nav).getByRole("link", { name: new RegExp(item.label) }),
      ).toBeInTheDocument();
    }
  });

  it("marks its state on the element the layout reads", () => {
    const { rerender } = renderSidebar(false);
    expect(screen.getByRole("complementary", { name: "主選單" })).toHaveAttribute(
      "data-rail",
      "expanded",
    );

    rerender(sidebar(true));
    expect(screen.getByRole("complementary", { name: "主選單" })).toHaveAttribute(
      "data-rail",
      "collapsed",
    );
  });

  it("names what the next click does, not what the last one did", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();

    const { rerender } = renderSidebar(false, onToggle);
    await user.click(screen.getByRole("button", { name: "收埋側欄" }));
    expect(onToggle).toHaveBeenCalledTimes(1);

    rerender(sidebar(true, onToggle));
    const button = screen.getByRole("button", { name: "展開側欄" });
    expect(
      screen.queryByRole("button", { name: "收埋側欄" }),
    ).not.toBeInTheDocument();
    // The accessible name and the visible caption are two different strings
    // in the DOM; asserting only the first let a hardcoded label slip through.
    expect(within(button).getByText("展開側欄")).toBeInTheDocument();
    expect(within(button).queryByText("收埋側欄")).not.toBeInTheDocument();
  });
});
