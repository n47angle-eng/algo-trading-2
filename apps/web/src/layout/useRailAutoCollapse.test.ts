import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AUTO_COLLAPSE_MS, useRailAutoCollapse } from "./useRailAutoCollapse";

describe("useRailAutoCollapse", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const advance = (ms: number) => {
    act(() => {
      vi.advanceTimersByTime(ms);
    });
  };

  it("puts an expanded rail away ten seconds after the pointer leaves", () => {
    const persist = vi.fn();
    const { result } = renderHook(() => useRailAutoCollapse(false, persist));

    act(() => {
      result.current.onPointerEnter();
    });
    advance(AUTO_COLLAPSE_MS + 100);
    // Reading the menu counts as using it — the clock only runs once you leave.
    expect(result.current.collapsed).toBe(false);

    act(() => {
      result.current.onPointerLeave();
    });
    advance(AUTO_COLLAPSE_MS - 100);
    expect(result.current.collapsed).toBe(false);

    advance(200);
    expect(result.current.collapsed).toBe(true);
    expect(persist).toHaveBeenCalledWith(true);
  });

  it("cancels the countdown when the pointer comes back", () => {
    const { result } = renderHook(() =>
      useRailAutoCollapse(false, () => undefined),
    );

    act(() => {
      result.current.onPointerLeave();
    });
    advance(AUTO_COLLAPSE_MS - 500);
    act(() => {
      result.current.onPointerEnter();
    });
    advance(AUTO_COLLAPSE_MS * 2);

    expect(result.current.collapsed).toBe(false);
  });

  it("shows the full menu again on hover, without changing the stored choice", () => {
    const persist = vi.fn();
    const { result } = renderHook(() => useRailAutoCollapse(true, persist));

    expect(result.current.expandedNow).toBe(false);

    act(() => {
      result.current.onPointerEnter();
    });
    expect(result.current.peeking).toBe(true);
    expect(result.current.expandedNow).toBe(true);
    // Peeking is not a preference; nothing is written.
    expect(result.current.collapsed).toBe(true);
    expect(persist).not.toHaveBeenCalled();

    act(() => {
      result.current.onPointerLeave();
    });
    expect(result.current.expandedNow).toBe(false);
  });

  it("does not re-collapse a rail that is already collapsed", () => {
    const persist = vi.fn();
    const { result } = renderHook(() => useRailAutoCollapse(true, persist));

    act(() => {
      result.current.onPointerLeave();
    });
    advance(AUTO_COLLAPSE_MS * 3);

    expect(persist).not.toHaveBeenCalled();
  });

  it("toggling records the choice and restarts from a settled state", () => {
    const persist = vi.fn();
    const { result } = renderHook(() => useRailAutoCollapse(true, persist));

    act(() => {
      result.current.toggle();
    });
    expect(result.current.collapsed).toBe(false);
    expect(persist).toHaveBeenLastCalledWith(false);

    // Manually expanding does not exempt it: leave it alone and it tidies up.
    act(() => {
      result.current.onPointerLeave();
    });
    advance(AUTO_COLLAPSE_MS + 100);
    expect(result.current.collapsed).toBe(true);
  });
});
