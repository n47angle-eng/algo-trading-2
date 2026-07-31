import { useEffect } from "react";
import { NavLink } from "react-router-dom";

import { Icon } from "../components/ui/Icon";
import { MOBILE_OVERFLOW_ITEMS } from "../nav";
import { ThemeSwitch } from "./ThemeSwitch";

interface MoreSheetProps {
  open: boolean;
  onClose: () => void;
}

/**
 * The overflow panel, shared by both layouts.
 *
 * It holds everything that is not a destination you switch to mid-session:
 * the remaining pages (phone only — the desktop rail already lists them), the
 * theme control, and the manual. Rendering it once means there is exactly one
 * theme control in the app at any time.
 *
 * Deliberately frameless: rows have no borders and no selected-state fill, so
 * the panel reads as a quiet list rather than a second navigation surface.
 */
export function MoreSheet({ open, onClose }: MoreSheetProps) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return (
    <>
      <button
        type="button"
        className="sheet-scrim"
        aria-label="收起更多"
        onClick={onClose}
      />
      <div className="sheet" role="dialog" aria-label="更多">
        <span className="sheet__grip" aria-hidden="true" />

        <nav className="sheet__nav" aria-label="其他頁面">
          {MOBILE_OVERFLOW_ITEMS.map((item) => (
            <NavLink
              key={item.id}
              to={item.path}
              className={({ isActive }) =>
                isActive ? "sheet__link sheet__link--active" : "sheet__link"
              }
              onClick={onClose}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          ))}

          <NavLink
            to="/guide"
            className={({ isActive }) =>
              isActive ? "sheet__link sheet__link--active" : "sheet__link"
            }
            onClick={onClose}
          >
            <Icon name="guide" />
            <span>系統說明書</span>
          </NavLink>
        </nav>

        <div className="sheet__row">
          <span className="sheet__row-label">主題</span>
          <ThemeSwitch />
        </div>
      </div>
    </>
  );
}
