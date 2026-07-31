import { NavLink, useLocation } from "react-router-dom";

import { Icon } from "../components/ui/Icon";
import { MOBILE_NAV_IDS, MOBILE_OVERFLOW_ITEMS, NAV_ITEMS } from "../nav";

const mobileItems = NAV_ITEMS.filter((item) =>
  (MOBILE_NAV_IDS as readonly string[]).includes(item.id),
);

interface BottomNavProps {
  moreOpen: boolean;
  onToggleMore: () => void;
  tradersOpen: boolean;
  onToggleTraders: () => void;
}

/**
 * Phone tab bar. Four destinations, the 交易員 glance sheet and 更多.
 *
 * 交易員 sits third — the middle of the bar, where the thumb rests — because
 * it is opened more often than anything else during market hours. It opens a
 * sheet rather than navigating, so a glance never costs you the page you were
 * reading.
 */
export function BottomNav({
  moreOpen,
  onToggleMore,
  tradersOpen,
  onToggleTraders,
}: BottomNavProps) {
  const location = useLocation();

  const overflowActive =
    location.pathname === "/guide" ||
    MOBILE_OVERFLOW_ITEMS.some((item) => item.path === location.pathname);

  const link = (isActive: boolean) =>
    ["bottom-nav__link", isActive ? "bottom-nav__link--active" : ""]
      .filter(Boolean)
      .join(" ");

  return (
    <nav className="bottom-nav" aria-label="手機主選單">
      {mobileItems.slice(0, 2).map((item) => (
        <NavLink
          key={item.id}
          to={item.path}
          end={item.path === "/"}
          className={({ isActive }) => link(isActive)}
        >
          <span className="bottom-nav__icon" aria-hidden="true">
            <Icon name={item.icon} />
          </span>
          <span>{item.short}</span>
        </NavLink>
      ))}

      <button
        type="button"
        className={link(tradersOpen)}
        aria-expanded={tradersOpen}
        onClick={onToggleTraders}
      >
        <span className="bottom-nav__icon" aria-hidden="true">
          <Icon name="daytrade" />
        </span>
        <span>交易員</span>
      </button>

      {mobileItems.slice(2).map((item) => (
        <NavLink
          key={item.id}
          to={item.path}
          end={item.path === "/"}
          className={({ isActive }) => link(isActive)}
        >
          <span className="bottom-nav__icon" aria-hidden="true">
            <Icon name={item.icon} />
          </span>
          <span>{item.short}</span>
        </NavLink>
      ))}

      <button
        type="button"
        className={link(overflowActive || moreOpen)}
        aria-expanded={moreOpen}
        onClick={onToggleMore}
      >
        <span className="bottom-nav__icon" aria-hidden="true">
          <Icon name="more" />
        </span>
        <span>更多</span>
      </button>
    </nav>
  );
}
