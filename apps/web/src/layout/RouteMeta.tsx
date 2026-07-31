import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { NAV_ITEMS } from "../nav";

const APP_NAME = "Futures Research";

/** Routes that are not top-level nav entries still deserve a real title. */
const EXTRA_TITLES: { match: RegExp; title: string }[] = [
  { match: /^\/guide$/, title: "系統說明書" },
  { match: /^\/results\/[^/]+$/, title: "回測詳情" },
  { match: /^\/daytrade\/traders\/[^/]+$/, title: "模擬交易員" },
];

function titleFor(pathname: string): string {
  const nav = NAV_ITEMS.find((item) => item.path === pathname);
  if (nav) {
    return nav.label;
  }
  const extra = EXTRA_TITLES.find((entry) => entry.match.test(pathname));
  return extra?.title ?? "";
}

/**
 * Route side effects the shell owes the browser.
 *
 *  1. Reset the scroll position. Without this, walking from the bottom of a
 *     long 結果 list into a new page drops you halfway down the new one.
 *  2. Name the tab. Installed as a PWA the title is what the OS task switcher
 *     shows, so "Futures Research" on every screen is a wasted label.
 */
export function RouteMeta() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0 });

    const page = titleFor(pathname);
    document.title = page ? `${page} · ${APP_NAME}` : APP_NAME;
  }, [pathname]);

  return null;
}
