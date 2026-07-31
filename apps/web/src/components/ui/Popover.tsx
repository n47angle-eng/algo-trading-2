/**
 * A small card that opens next to the control that summoned it.
 *
 * This exists so a date or a setting can be changed *in place*. Sending the
 * owner to another screen to pick a value loses the surrounding context, and
 * a full-screen modal for a four-line form is heavier than the decision.
 *
 * Positioned `fixed` against the trigger's measured rect rather than absolute
 * inside it — a card ancestor with `overflow: hidden` would otherwise clip the
 * panel, and that failure only shows up on the one page nobody re-tested.
 *
 * Below 40rem the panel becomes a bottom sheet (see overlays.css): near the
 * thumb, and wide enough for 44px targets.
 */

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

export interface PopoverProps {
  /** Accessible name of the panel. */
  label: string;
  /** Content of the trigger button. */
  trigger: ReactNode;
  triggerClassName?: string;
  triggerTestId?: string;
  disabled?: boolean;
  /** Panel content. Receives `close` so an action can dismiss the panel. */
  children: (close: () => void) => ReactNode;
  /** Which trigger edge the panel lines up with. */
  align?: "start" | "end";
}

const GAP_PX = 6;
const VIEWPORT_MARGIN_PX = 8;
const MOBILE_MAX_PX = 640;

interface PanelPosition {
  top: number;
  left: number;
}

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => !el.hasAttribute("disabled"));
}

export function Popover({
  label,
  trigger,
  triggerClassName,
  triggerTestId,
  disabled = false,
  children,
  align = "start",
}: PopoverProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<PanelPosition | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  const place = useCallback(() => {
    const anchor = triggerRef.current;
    const panel = panelRef.current;
    if (!anchor || !panel) return;
    if (window.innerWidth <= MOBILE_MAX_PX) {
      // The sheet layout is driven entirely by CSS; no measuring needed.
      setPos(null);
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();

    let top = rect.bottom + GAP_PX;
    if (top + panelRect.height > window.innerHeight - VIEWPORT_MARGIN_PX) {
      // Not enough room below — flip above the trigger rather than overflow.
      top = Math.max(VIEWPORT_MARGIN_PX, rect.top - GAP_PX - panelRect.height);
    }

    let left = align === "end" ? rect.right - panelRect.width : rect.left;
    left = Math.min(
      left,
      window.innerWidth - panelRect.width - VIEWPORT_MARGIN_PX,
    );
    left = Math.max(VIEWPORT_MARGIN_PX, left);

    setPos({ top, left });
  }, [align]);

  useLayoutEffect(() => {
    if (!open) return;
    place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onScrollOrResize = () => {
      place();
    };
    window.addEventListener("resize", onScrollOrResize);
    window.addEventListener("scroll", onScrollOrResize, true);
    return () => {
      window.removeEventListener("resize", onScrollOrResize);
      window.removeEventListener("scroll", onScrollOrResize, true);
    };
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;
    const first = focusableIn(panel)[0];
    // Fall back to the panel itself so focus never escapes to the page body.
    (first ?? panel).focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const items = focusableIn(panel);
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      setOpen(false);
    };

    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [open, close]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName ?? "popover-trigger"}
        // The trigger's content is a formatted value, which does not say what
        // the control is; screen readers need the field name here.
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        disabled={disabled}
        data-testid={triggerTestId}
        onClick={() => {
          setOpen((v) => !v);
        }}
      >
        {trigger}
      </button>

      {open ? (
        <>
          {/* Catches the tap that dismisses the sheet on a phone, where there
              is no cursor to click "outside" with. */}
          <div className="popover-scrim" aria-hidden="true" />
          <div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-modal="false"
            aria-label={label}
            tabIndex={-1}
            className="popover-panel"
            style={
              pos ? { top: `${String(pos.top)}px`, left: `${String(pos.left)}px` } : undefined
            }
            data-placed={pos ? "anchored" : "sheet"}
          >
            {children(close)}
          </div>
        </>
      ) : null}
    </>
  );
}
