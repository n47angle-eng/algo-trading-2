/**
 * The rail tidies itself away when you stop using it.
 *
 * Ten seconds after the pointer leaves an expanded rail it collapses, and
 * hovering the collapsed rail brings it straight back. That pairing is the
 * whole design: auto-collapse is only tolerable because getting the labels
 * back costs one mouse movement, and hover-to-expand is only safe because it
 * overlays the content instead of reflowing the page under the cursor.
 *
 * The timer is deliberately not started while the pointer is inside — reading
 * the menu is using it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export const AUTO_COLLAPSE_MS = 10_000;

export interface RailState {
  /** Persisted preference: is the rail put away? */
  collapsed: boolean;
  /** Collapsed underneath, but shown expanded because the pointer is on it. */
  peeking: boolean;
  /** What the rail looks like right now. */
  expandedNow: boolean;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
  toggle: () => void;
}

export function useRailAutoCollapse(
  initialCollapsed: boolean,
  persist: (collapsed: boolean) => void,
): RailState {
  const [collapsed, setCollapsed] = useState(initialCollapsed);
  const [inside, setInside] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const onPointerEnter = useCallback(() => {
    clearTimer();
    setInside(true);
  }, [clearTimer]);

  const onPointerLeave = useCallback(() => {
    setInside(false);
  }, []);

  const toggle = useCallback(() => {
    clearTimer();
    setCollapsed((prev) => {
      const next = !prev;
      persist(next);
      return next;
    });
  }, [clearTimer, persist]);

  useEffect(() => {
    // Nothing to tidy away when it is already away, and nothing to tidy while
    // the pointer is still on it.
    if (collapsed || inside) {
      clearTimer();
      return;
    }
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setCollapsed(true);
      persist(true);
    }, AUTO_COLLAPSE_MS);
    return clearTimer;
  }, [collapsed, inside, clearTimer, persist]);

  useEffect(() => clearTimer, [clearTimer]);

  const peeking = collapsed && inside;

  return {
    collapsed,
    peeking,
    expandedNow: !collapsed || peeking,
    onPointerEnter,
    onPointerLeave,
    toggle,
  };
}
