import { useCallback, useState } from "react";
import { Outlet } from "react-router-dom";

import { OfflineBanner } from "../components/ui/OfflineBanner";
import { PageTabsSlotContext } from "../components/ui/pageTabsSlot";
import { ToastProvider } from "../components/ui/Toast";
import { InstallPrompt } from "../pwa/InstallPrompt";
import { BottomNav } from "./BottomNav";
import { MoreSheet } from "./MoreSheet";
import { RouteMeta } from "./RouteMeta";
import { ShellBar } from "./ShellBar";
import { Sidebar } from "./Sidebar";
import { readSidebarCollapsed, writeSidebarCollapsed } from "./sidebarState";
import { TradersSheet } from "./TradersSheet";
import { useRailAutoCollapse } from "./useRailAutoCollapse";

/**
 * Shell layout.
 *
 *  >= 64rem  sidebar rail + a sticky bar carrying the page's section tabs
 *  <  64rem  the same sticky bar as the PWA top bar + a fixed bottom tab bar
 *            (the sidebar stays mounted but hidden, so its live IB probe keeps
 *            feeding the same state)
 *
 * The tab slot is handed down by context: pages render <PageTabBar> where
 * their content lives, and it portals into the bar.
 */
export function AppShell() {
  const [tabSlot, setTabSlot] = useState<HTMLDivElement | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [tradersOpen, setTradersOpen] = useState(false);
  const rail = useRailAutoCollapse(readSidebarCollapsed(), writeSidebarCollapsed);

  const closeMore = useCallback(() => {
    setMoreOpen(false);
  }, []);

  const closeTraders = useCallback(() => {
    setTradersOpen(false);
  }, []);

  // Two sheets over one screen is one too many — opening either closes the
  // other rather than stacking scrims.
  const toggleMore = useCallback(() => {
    setTradersOpen(false);
    setMoreOpen((v) => !v);
  }, []);

  const toggleTraders = useCallback(() => {
    setMoreOpen(false);
    setTradersOpen((v) => !v);
  }, []);

  return (
    <ToastProvider>
      <RouteMeta />
      {/* The grid column follows the *stored* choice, never the hover — a page
          that reflows under the cursor every time you brush the rail is worse
          than no rail at all. Peeking overlays instead. */}
      <div
        className="app-shell"
        data-rail={rail.collapsed ? "collapsed" : "expanded"}
      >
        {/* First tab stop on every page: jump the rail and the bar. */}
        <a className="skip-link" href="#main-content">
          跳去主要內容
        </a>
        <Sidebar
          collapsed={rail.collapsed}
          expandedNow={rail.expandedNow}
          peeking={rail.peeking}
          onToggle={rail.toggle}
          onPointerEnter={rail.onPointerEnter}
          onPointerLeave={rail.onPointerLeave}
        />
        <main className="main">
          <ShellBar
            tabSlotRef={setTabSlot}
            moreOpen={moreOpen}
            onOpenMore={toggleMore}
            tradersOpen={tradersOpen}
            onOpenTraders={toggleTraders}
          />
          {/* Directly under the bar and above the content it qualifies — the
              owner reads "this is old" before reading the numbers. */}
          <OfflineBanner />
          <div className="main__inner" id="main-content" tabIndex={-1}>
            <PageTabsSlotContext.Provider value={tabSlot}>
              <Outlet />
            </PageTabsSlotContext.Provider>
          </div>
        </main>
        <BottomNav
          moreOpen={moreOpen}
          onToggleMore={toggleMore}
          tradersOpen={tradersOpen}
          onToggleTraders={toggleTraders}
        />
        <MoreSheet open={moreOpen} onClose={closeMore} />
        <TradersSheet open={tradersOpen} onClose={closeTraders} />
        <InstallPrompt />
      </div>
    </ToastProvider>
  );
}
