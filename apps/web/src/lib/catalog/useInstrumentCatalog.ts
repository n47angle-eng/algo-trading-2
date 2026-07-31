import { useEffect, useRef, useState } from "react";

import { fetchInstrumentCatalog } from "../../api/client";
import { parseInstrumentCatalog } from "./parseCatalog";
import type { CatalogState } from "./types";

/**
 * Load and parse the normal P2 instrument catalog once per provider mount.
 * The promise ref also prevents React StrictMode's effect replay from issuing a
 * second request. Owner-review passes `enabled=false` and uses its fixture
 * state in WorkbenchContext instead.
 */
export function useInstrumentCatalog(enabled = true): CatalogState {
  const [state, setState] = useState<CatalogState>({ status: "loading" });
  const requestRef = useRef<Promise<CatalogState> | null>(null);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    let active = true;
    requestRef.current ??= fetchInstrumentCatalog()
      .then((body) => parseInstrumentCatalog(body))
      .catch(
        (err): CatalogState => ({
          status: "error",
          message:
            err instanceof Error
              ? `暫時讀唔到合約清單：${err.message}`
              : "暫時讀唔到合約清單。",
        }),
      );
    void requestRef.current.then((next) => {
      if (active) {
        setState(next);
      }
    });
    return () => {
      active = false;
    };
  }, [enabled]);

  return state;
}
