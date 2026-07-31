import { createContext } from "react";

/**
 * Where a page's section tabs get rendered.
 *
 * The app shell provides the element; pages keep declaring <PageTabBar> next to
 * the content it switches, and the bar portals up into the top bar. `null`
 * means "no shell" (a page rendered standalone), in which case the bar renders
 * in place and the markup contract is unchanged.
 */
export const PageTabsSlotContext = createContext<HTMLElement | null>(null);
