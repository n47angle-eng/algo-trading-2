import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { Icon } from "./Icon";
import {
  useWindowGeometry,
  type Geometry,
  type ResizeDir,
} from "./useWindowGeometry";

const COACH_KEY = "fr.window.coached";
const HANDLES: ResizeDir[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

function coachSeen(): boolean {
  try {
    return window.localStorage.getItem(COACH_KEY) === "1";
  } catch {
    return true;
  }
}

interface FloatingWindowProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Quiet line under the title — a symbol, an id, a state. */
  subtitle?: React.ReactNode;
  /**
   * Distinct per kind of window. Geometry is remembered under it, so every
   * trader window opens where the owner last left *that kind* of window.
   */
  storageKey: string;
  /** Viewport point the window grows out of — usually the clicked card. */
  origin?: { x: number; y: number } | null;
  children: React.ReactNode;
  footer?: React.ReactNode;
}

/**
 * A real window: drag the title bar to move it, drag any edge or corner to
 * resize, and it stays where you put it next time.
 *
 * On a phone this collapses to a bottom sheet — dragging a 320px window around
 * a 390px screen is a worse answer than the sheet everyone already knows, so
 * the CSS drops the chrome instead of shipping a gesture nobody can hit.
 */
export function FloatingWindow({
  open,
  onClose,
  title,
  subtitle,
  storageKey,
  origin,
  children,
  footer,
}: FloatingWindowProps) {
  const initial = useCallback((): Geometry => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const w = Math.min(560, vw - 48);
    const h = Math.min(520, vh - 96);
    /*
     * Grow from the click, but land fully on screen. Clicking a card low on a
     * long roster used to open a window whose bottom half was below the fold,
     * so the resize corner — and half the numbers — were unreachable.
     */
    const fit = (want: number, size: number, viewport: number) =>
      Math.min(Math.max(16, want), Math.max(16, viewport - size - 16));
    return {
      w,
      h,
      x: origin ? fit(origin.x - w / 2, w, vw) : (vw - w) / 2,
      y: origin ? fit(origin.y - 80, h, vh) : (vh - h) / 2,
    };
  }, [origin]);

  const { geom, beginDrag, reset, touched } = useWindowGeometry(
    storageKey,
    initial,
  );
  const [coach, setCoach] = useState(false);

  useEffect(() => {
    if (open && !coachSeen()) {
      setCoach(true);
    }
  }, [open]);

  const dismissCoach = useCallback(() => {
    setCoach(false);
    try {
      window.localStorage.setItem(COACH_KEY, "1");
    } catch {
      /* ignore */
    }
  }, []);

  // The first real drag or resize teaches it better than the coach mark does.
  useEffect(() => {
    if (touched) {
      dismissCoach();
    }
  }, [touched, dismissCoach]);

  useEffect(() => {
    if (!open) return;
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

  return createPortal(
    <>
      <button
        type="button"
        className="fwin-scrim"
        aria-label="關閉視窗"
        onClick={onClose}
      />

      <section
        className="fwin"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={{
          left: `${String(geom.x)}px`,
          top: `${String(geom.y)}px`,
          width: `${String(geom.w)}px`,
          height: `${String(geom.h)}px`,
        }}
      >
        <header
          className="fwin__bar"
          onPointerDown={(event) => {
            beginDrag(event);
          }}
        >
          <span className="fwin__grip" aria-hidden="true" />
          <span className="fwin__titles">
            <span className="fwin__title">{title}</span>
            {subtitle ? (
              <span className="fwin__subtitle">{subtitle}</span>
            ) : null}
          </span>
          <button
            type="button"
            className="icon-btn fwin__action"
            aria-label="還原大細同位置"
            title="還原大細同位置"
            onPointerDown={(event) => {
              event.stopPropagation();
            }}
            onClick={reset}
          >
            <Icon name="reset" />
          </button>
          <button
            type="button"
            className="icon-btn fwin__action"
            aria-label="關閉"
            onPointerDown={(event) => {
              event.stopPropagation();
            }}
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </header>

        <div className="fwin__body">{children}</div>
        {footer ? <footer className="fwin__foot">{footer}</footer> : null}

        {HANDLES.map((dir) => (
          <span
            key={dir}
            className={`fwin__resize fwin__resize--${dir}`}
            data-dir={dir}
            aria-hidden="true"
            onPointerDown={(event) => {
              event.stopPropagation();
              beginDrag(event, dir);
            }}
          />
        ))}

        {coach ? (
          <div className="fwin-coach" role="note">
            <span className="fwin-coach__ring fwin-coach__ring--move" />
            <span className="fwin-coach__tip fwin-coach__tip--move">
              拖住呢條橫條可以移動
            </span>

            <span className="fwin-coach__ring fwin-coach__ring--size" />
            <span className="fwin-coach__tip fwin-coach__tip--size">
              拉邊或者角落改大細
            </span>

            <button
              type="button"
              className="btn btn--sm btn--primary fwin-coach__ok"
              onClick={dismissCoach}
            >
              明白
            </button>
          </div>
        ) : null}
      </section>
    </>,
    document.body,
  );
}
