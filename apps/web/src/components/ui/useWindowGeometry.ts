import { useCallback, useRef, useState } from "react";

export interface Geometry {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Which edge or corner a resize handle drives. */
export type ResizeDir = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

const MIN_W = 320;
const MIN_H = 220;
/** Keep at least this much of the title bar reachable when dragged to an edge. */
const KEEP_VISIBLE = 64;

const storageKeyFor = (key: string) => `fr.window.${key}`;

function clampToViewport(g: Geometry): Geometry {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = Math.min(Math.max(g.w, MIN_W), vw);
  const h = Math.min(Math.max(g.h, MIN_H), vh);
  return {
    w,
    h,
    x: Math.min(Math.max(g.x, KEEP_VISIBLE - w), vw - KEEP_VISIBLE),
    y: Math.min(Math.max(g.y, 0), vh - KEEP_VISIBLE),
  };
}

function readStored(key: string): Geometry | null {
  try {
    const raw = window.localStorage.getItem(storageKeyFor(key));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      ["x", "y", "w", "h"].every(
        (k) => typeof (parsed as Record<string, unknown>)[k] === "number",
      )
    ) {
      /*
       * Dragging may leave a window hanging off an edge on purpose, but
       * *opening* one should never hand back a window you have to fish out of
       * the corner — so a restored geometry is pulled fully on-screen.
       */
      const g = clampToViewport(parsed as Geometry);
      return {
        ...g,
        x: Math.max(0, Math.min(g.x, window.innerWidth - g.w)),
        y: Math.max(0, Math.min(g.y, window.innerHeight - g.h)),
      };
    }
  } catch {
    /* private mode, or a stale shape we simply ignore */
  }
  return null;
}

/**
 * Position and size for a floating window, with pointer-driven move/resize.
 *
 * Geometry is remembered per window key: once the owner has put a window where
 * they want it, re-opening should not undo that. Everything is clamped so a
 * window can never be dragged fully off-screen and stranded.
 */
export function useWindowGeometry(key: string, initial: () => Geometry) {
  const [geom, setGeom] = useState<Geometry>(
    () => readStored(key) ?? clampToViewport(initial()),
  );
  /** Set once the owner has actually moved or resized — drives the coach mark. */
  const [touched, setTouched] = useState(false);
  const startRef = useRef<{ px: number; py: number; g: Geometry } | null>(null);

  const persist = useCallback(
    (next: Geometry) => {
      try {
        window.localStorage.setItem(storageKeyFor(key), JSON.stringify(next));
      } catch {
        /* ignore */
      }
    },
    [key],
  );

  const beginDrag = useCallback(
    (event: React.PointerEvent, dir?: ResizeDir) => {
      // Left button / touch only, and never from a control inside the bar.
      if (event.button !== 0) return;
      /*
       * Capture is a nicety — the move/up listeners live on window, so the drag
       * works without it. It must never be able to abort the gesture, which is
       * exactly what an unguarded call did whenever the id was not a live
       * pointer.
       */
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        /* no live pointer to capture; window listeners still drive the drag */
      }
      startRef.current = { px: event.clientX, py: event.clientY, g: geom };

      const onMove = (move: PointerEvent) => {
        const start = startRef.current;
        if (!start) return;
        const dx = move.clientX - start.px;
        const dy = move.clientY - start.py;
        const g = start.g;

        let next: Geometry;
        if (!dir) {
          next = { ...g, x: g.x + dx, y: g.y + dy };
        } else {
          let { x, y, w, h } = g;
          if (dir.includes("e")) w = g.w + dx;
          if (dir.includes("s")) h = g.h + dy;
          if (dir.includes("w")) {
            w = g.w - dx;
            x = g.x + dx;
          }
          if (dir.includes("n")) {
            h = g.h - dy;
            y = g.y + dy;
          }
          // A west/north drag past the minimum must pin the edge, not push it.
          if (w < MIN_W && dir.includes("w")) x = g.x + (g.w - MIN_W);
          if (h < MIN_H && dir.includes("n")) y = g.y + (g.h - MIN_H);
          next = { x, y, w, h };
        }
        setGeom(clampToViewport(next));
        setTouched(true);
      };

      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        startRef.current = null;
        setGeom((current) => {
          persist(current);
          return current;
        });
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [geom, persist],
  );

  const reset = useCallback(() => {
    const next = clampToViewport(initial());
    setGeom(next);
    persist(next);
  }, [initial, persist]);

  return { geom, beginDrag, reset, touched };
}
