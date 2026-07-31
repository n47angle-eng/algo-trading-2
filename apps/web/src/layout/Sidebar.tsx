import { NavLink } from "react-router-dom";

import { Icon } from "../components/ui/Icon";
import { NAV_GROUPS, NAV_ITEMS } from "../nav";
import { IbSidebarStatus } from "./IbSidebarStatus";

/**
 * Desktop navigation rail. Hidden below 64rem, where the top bar + tab bar
 * take over — but it stays mounted so the live IB probe keeps running.
 *
 * Two things carry the modern feel and both are structural, not decoration:
 *
 *  - **Grouped rows.** Eight equal-weight links read as one list you re-scan
 *    every time. 研究 / 模擬 / 系統 lets the eye land in the right third first.
 *  - **Collapse to a rail.** Gives 232px back to the content on a laptop, and
 *    is remembered across visits. Collapsed rows keep their full accessible
 *    name and show it as a flyout on hover, so the icons never become a
 *    memory test.
 */
export function Sidebar({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <aside
      className="sidebar"
      aria-label="主選單"
      data-collapsed={collapsed ? "true" : "false"}
    >
      <div className="sidebar__logo">
        <span className="sidebar__logo-mark" aria-hidden="true">
          <Icon name="brand" />
        </span>
        <span className="sidebar__wordmark">Futures Research</span>
      </div>

      <nav className="sidebar__nav" aria-label="頁面">
        {NAV_GROUPS.map((group) => {
          const items = NAV_ITEMS.filter((item) => item.group === group.id);
          if (items.length === 0) return null;
          return (
            <div className="sidebar__group" key={group.id}>
              {/* Hidden in the rail, where the rule above each group carries
                  the same separation without the width. */}
              <span className="sidebar__group-label">{group.label}</span>
              {items.map((item) => (
                <NavLink
                  key={item.id}
                  to={item.path}
                  end={item.path === "/"}
                  className={({ isActive }) =>
                    [
                      "sidebar__link",
                      isActive ? "sidebar__link--active" : "",
                      item.placeholder ? "sidebar__link--placeholder" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")
                  }
                >
                  <Icon name={item.icon} />
                  <span className="sidebar__link-label">{item.label}</span>
                  {item.placeholder ? (
                    <span className="sidebar__badge">soon</span>
                  ) : null}
                </NavLink>
              ))}
            </div>
          );
        })}
      </nav>

      {/* The foot carries live state only — no marketing, no version string. */}
      <div className="sidebar__foot">
        <IbSidebarStatus />
        <button
          type="button"
          className="sidebar__collapse"
          aria-label={collapsed ? "展開側欄" : "收埋側欄"}
          aria-pressed={collapsed}
          title={collapsed ? "展開側欄" : "收埋側欄"}
          onClick={onToggle}
        >
          <span className="sidebar__collapse-icon" aria-hidden="true">
            <Icon name="chevron" />
          </span>
          {/* Also the hover flyout once collapsed, so it has to name what the
              next click does — not what the last one did. */}
          <span className="sidebar__link-label">
            {collapsed ? "展開側欄" : "收埋側欄"}
          </span>
        </button>
      </div>
    </aside>
  );
}
