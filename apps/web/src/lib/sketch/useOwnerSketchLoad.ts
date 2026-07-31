/**
 * Shared origin-aware sketch load state for left panel + universe gates.
 * Single truth: never substitute browser localStorage by sketch_id alone.
 */

import { useEffect, useRef, useState } from "react";

import {
  fetchSketchDetail,
  SketchFetchError,
  type SketchDetailResponse,
} from "../../api/client";
import type { SketchLineage } from "../strategyYaml";

export type OwnerSketchLoadState =
  | { kind: "idle" }
  | { kind: "lineage"; reason: string }
  | { kind: "loading"; origin: string; sketchId: string }
  | { kind: "ready"; detail: SketchDetailResponse }
  | { kind: "not_found"; origin: string; sketchId: string }
  | { kind: "error"; message: string; isNotFound: boolean };

export type SketchDetailLookup = (
  origin: string,
  sketchId: string,
) => Promise<SketchDetailResponse>;

/**
 * @param fixtureLookup — owner-review: in-memory fixture, no fetch.
 *   When provided, always used instead of fetchSketchDetail.
 */
export function useOwnerSketchLoad(
  lineage: SketchLineage | null,
  active: boolean,
  fixtureLookup?: SketchDetailLookup | null,
): OwnerSketchLoadState {
  const [state, setState] = useState<OwnerSketchLoadState>({ kind: "idle" });
  const reqSeq = useRef(0);

  useEffect(() => {
    const seq = ++reqSeq.current;

    if (!active) {
      setState({ kind: "idle" });
      return () => {
        reqSeq.current += 1;
      };
    }
    if (!lineage) {
      setState({ kind: "idle" });
      return () => {
        reqSeq.current += 1;
      };
    }
    if (lineage.status === "incomplete") {
      setState({ kind: "lineage", reason: lineage.reason });
      return () => {
        reqSeq.current += 1;
      };
    }

    const { origin, sketchId } = lineage;
    setState({ kind: "loading", origin, sketchId });
    void (async () => {
      try {
        const detail = fixtureLookup
          ? await fixtureLookup(origin, sketchId)
          : await fetchSketchDetail(origin, sketchId);
        if (seq !== reqSeq.current) {
          return;
        }
        setState({ kind: "ready", detail });
      } catch (err) {
        if (seq !== reqSeq.current) {
          return;
        }
        if (err instanceof SketchFetchError) {
          if (err.status === 404) {
            setState({ kind: "not_found", origin, sketchId });
            return;
          }
          setState({
            kind: "error",
            isNotFound: false,
            message:
              err.status === 0
                ? "草圖回應格式無效，暫時讀唔到原始圖文包。"
                : `暫時讀唔到原始圖文包（HTTP ${err.status}）。`,
          });
          return;
        }
        setState({
          kind: "error",
          isNotFound: false,
          message: "暫時讀唔到原始圖文包（網絡或連線問題）。",
        });
      }
    })();

    return () => {
      if (reqSeq.current === seq) {
        reqSeq.current += 1;
      }
    };
  }, [active, lineage, fixtureLookup]);

  return state;
}
