import { useContext } from "react";
import { createPortal } from "react-dom";

import { PageTabsSlotContext } from "./pageTabsSlot";

/**
 * Shared in-page section switcher.
 *
 * Pages keep declaring their tabs where the content lives, but when the app
 * shell is present the bar is portalled up into the top bar, so every screen
 * has exactly one row of chrome instead of a title, then a tab strip, then the
 * content. Rendered standalone (no shell, e.g. a page under test) it falls back
 * to rendering in place, so the markup contract never changes.
 */

/**
 * Portal wrapper for pages whose tab strip is hand-rolled (the workbench, the
 * paper trader tabs, the day-trade roster) rather than a <PageTabBar>.
 */
export function PageTabsPortal({ children }: { children: React.ReactNode }) {
  const slot = useContext(PageTabsSlotContext);
  return slot ? createPortal(children, slot) : <>{children}</>;
}

export interface PageTabItem<T extends string = string> {
  id: T;
  label: string;
  disabled?: boolean;
}

interface PageTabBarProps<T extends string = string> {
  tabs: readonly PageTabItem<T>[];
  active: T;
  onChange: (id: T) => void;
  /** Accessible name for the tablist */
  ariaLabel: string;
  className?: string;
}

export function PageTabBar<T extends string>({
  tabs,
  active,
  onChange,
  ariaLabel,
  className,
}: PageTabBarProps<T>) {
  const slot = useContext(PageTabsSlotContext);

  const bar = (
    <div
      className={["page-tabs", className].filter(Boolean).join(" ")}
      role="tablist"
      aria-label={ariaLabel}
    >
      {tabs.map((t) => {
        const on = t.id === active;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`page-tab-${t.id}`}
            aria-selected={on}
            aria-controls={`page-panel-${t.id}`}
            disabled={t.disabled === true}
            className={
              on ? "page-tabs__btn page-tabs__btn--on" : "page-tabs__btn"
            }
            onClick={() => {
              if (!t.disabled) {
                onChange(t.id);
              }
            }}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );

  return slot ? createPortal(bar, slot) : bar;
}
