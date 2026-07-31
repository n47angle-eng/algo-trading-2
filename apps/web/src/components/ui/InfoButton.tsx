import { useEffect, useId, useRef, useState } from "react";

import { Icon } from "./Icon";

interface InfoButtonProps {
  /** Accessible name, e.g. 「關於 回測」. Never rendered as visible text. */
  label: string;
  /** The explanation that used to sit on the page as a paragraph. */
  children: React.ReactNode;
  /** Anchor the popover to the right edge when the trigger is near the end. */
  align?: "start" | "end";
  className?: string;
}

/**
 * The click-to-read disclosure that lets every screen stay quiet.
 *
 * Instead of printing an explanatory paragraph under each title, the paragraph
 * moves in here and the title keeps a single small glyph. The content stays
 * mounted (only `hidden` toggles), so screen readers and the test suite can
 * still reach it while the default view shows almost no prose.
 */
export function InfoButton({
  label,
  children,
  align = "start",
  className,
}: InfoButtonProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const popId = useId();

  useEffect(() => {
    if (!open) {
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    const onPointer = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  return (
    <span
      ref={wrapRef}
      className={["info", className].filter(Boolean).join(" ")}
    >
      <button
        type="button"
        className="info-btn"
        aria-expanded={open}
        aria-controls={popId}
        aria-label={label}
        onClick={() => {
          setOpen((v) => !v);
        }}
      >
        <Icon name="info" />
      </button>
      <span
        id={popId}
        role="note"
        hidden={!open}
        className={
          align === "end" ? "info-pop info-pop--end selectable" : "info-pop selectable"
        }
      >
        {children}
      </span>
    </span>
  );
}
