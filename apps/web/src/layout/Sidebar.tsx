import { NavLink } from "react-router-dom";

import { Icon } from "../components/ui/Icon";
import { NAV_GROUPS, NAV_ITEMS } from "../nav";
import { IbSidebarStatus } from "./IbSidebarStatus";

/**
 * Desktop navigation rail. Hidden below 64rem, where the top bar + tab bar
 * take over — but it stays mounted so the live IB probe keeps running.
 *
 * Three things carry the design, and all three are structural:
 *
 *  - **Grouped rows.** Eight equal-weight links read as one list you re-scan
 *    every time. 研究 / 模擬 / 系統 lets the eye land in the right third first.
 *  - **Collapse to a rail**, remembered across visits, and put away on its own
 *    ten seconds after you stop using it.
 *  - **Hover to bring it back.** The collapsed rail expands *over* the content
 *    while the pointer is on it. An earlier build floated each label out beside
 *    its icon instead; the rail's own scroll container clipped those to a
 *    single character, and the fix is to show the real menu rather than a
 *    tooltip that has to escape its parent.
 *
 * `expandedNow` is what it looks like; `collapsed` is what the owner chose.
 * They differ exactly while peeking.
 */
export function Sidebar({
  collapsed,
  expandedNow,
  peeking,
  onToggle,
  onPointerEnter,
  onPointerLeave,
}: {
  collapsed: boolean;
  expandedNow: boolean;
  peeking: boolean;
  onToggle: () => void;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
}) {
  return (
    <aside
      className="sidebar"
      aria-label="主選單"
      data-rail={expandedNow ? "expanded" : "collapsed"}
      data-peek={peeking ? "true" : "false"}
      /* The stored choice, separate from the look: while peeking the menu is
         open but the button still offers to pin it open, so its arrow has to
         follow the choice rather than the appearance. */
      data-collapsed={collapsed ? "true" : "false"}
      onMouseEnter={onPointerEnter}
      onMouseLeave={onPointerLeave}
      onFocus={onPointerEnter}
      onBlur={onPointerLeave}
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
                  title={item.label}
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
                  <span className="sidebar__link-icon" aria-hidden="true">
                    <Icon name={item.icon} />
                  </span>
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

      {/* The foot carries live state only — no marketing, no version string.
          It keeps a readable word in the rail too: a bare dot and a chevron
          told the owner nothing about either the connection or the button. */}
      <div className="sidebar__foot">
        <IbSidebarStatus />
        <button
          type="button"
          className="sidebar__collapse"
          aria-label={collapsed ? "展開側欄" : "收埋側欄"}
          aria-pressed={collapsed}
          onClick={onToggle}
        >
          <span className="sidebar__collapse-icon" aria-hidden="true">
            <Icon name="chevron" />
          </span>
          {/* Names what the next click does, not what the last one did. */}
          <span className="sidebar__collapse-label">
            {collapsed ? "展開側欄" : "收埋側欄"}
          </span>
          <span className="sidebar__collapse-short" aria-hidden="true">
            {collapsed ? "展開" : "收埋"}
          </span>
        </button>
      </div>
    </aside>
  );
}
