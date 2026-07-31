/**
 * Whether the desktop rail is collapsed, remembered across visits.
 *
 * A collapse that resets on every reload is worse than none — the owner sets
 * the width once and expects it to stay. Storage access is guarded because
 * private-mode Safari throws on read as well as write, and a layout
 * preference is never worth an exception.
 */

export const SIDEBAR_STORAGE_KEY = "fr-sidebar-collapsed";

export function readSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeSidebarCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // A preference we cannot persist is still a preference we can honour now.
  }
}
