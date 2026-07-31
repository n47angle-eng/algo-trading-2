/**
 * P2 workbench context — owner-review uses isolated in-memory storage + fixtures.
 */

import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  SketchFetchError,
  type SketchDetailResponse,
} from "../../api/client";
import type { CatalogState } from "../catalog/types";
import { useInstrumentCatalog } from "../catalog/useInstrumentCatalog";
import { createMemoryStorage } from "../storage/memoryStorage";
import type { SketchDetailLookup } from "../sketch/useOwnerSketchLoad";
import {
  type OwnerReviewFixtureId,
  applyOwnerReviewFixture,
  ownerReviewCatalogState,
} from "./ownerReviewSession";

export interface WorkbenchContextValue {
  ownerReview: boolean;
  /** Sketch + insight storage (localStorage normal; memory owner-review). */
  storage: Storage;
  /** One parsed truth shared by tabs ①, ② and ④ for this provider mount. */
  catalog: CatalogState;
  /** When set, sketch detail comes from fixture (no fetch). */
  sketchDetailLookup: SketchDetailLookup | null;
  activeFixture: OwnerReviewFixtureId | null;
  /**
   * Monotonic revision bumped on every loadFixture (including re-press same id).
   * Never use string-length epoch — same-length fixture ids must not collide (D18).
   */
  fixtureRevision: number;
  loadFixture: (id: OwnerReviewFixtureId) => void;
  /** Quantify tab seed YAML from last fixture load (if any). */
  quantifySeedYaml: string | null;
  setQuantifySeedYaml: (yaml: string | null) => void;
}

const WorkbenchContext = createContext<WorkbenchContextValue | null>(null);

/**
 * Test-only deferred sketch lookups (Correction C D22 late-response path).
 * Production code never arms these; tests call arm/resolve/reject.
 */
type DeferredEntry = {
  promise: Promise<SketchDetailResponse>;
  resolve: (d: SketchDetailResponse) => void;
  reject: (e: unknown) => void;
};
const deferredLookups = new Map<string, DeferredEntry>();

// eslint-disable-next-line react-refresh/only-export-components -- test harness co-located with provider
export const sketchLookupTestHooks = {
  arm(origin: string, sketchId: string): void {
    const key = `${origin}::${sketchId}`;
    let resolve!: (d: SketchDetailResponse) => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<SketchDetailResponse>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    deferredLookups.set(key, { promise, resolve, reject });
  },
  resolve(origin: string, sketchId: string, detail: SketchDetailResponse): void {
    const key = `${origin}::${sketchId}`;
    const e = deferredLookups.get(key);
    if (e) {
      e.resolve(detail);
      deferredLookups.delete(key);
    }
  },
  reject(origin: string, sketchId: string, err: unknown): void {
    const key = `${origin}::${sketchId}`;
    const e = deferredLookups.get(key);
    if (e) {
      e.reject(err);
      deferredLookups.delete(key);
    }
  },
  clear(): void {
    deferredLookups.clear();
  },
};

export function WorkbenchProvider({
  ownerReview,
  children,
}: {
  ownerReview: boolean;
  children: ReactNode;
}) {
  // Stable memory storage for the lifetime of this provider mount.
  const memoryRef = useRef<Storage | null>(null);
  if (ownerReview && !memoryRef.current) {
    memoryRef.current = createMemoryStorage();
  }
  if (!ownerReview) {
    memoryRef.current = null;
  }

  const storage = ownerReview
    ? (memoryRef.current as Storage)
    : localStorage;

  const [activeFixture, setActiveFixture] =
    useState<OwnerReviewFixtureId | null>(null);
  const [fixtureRevision, setFixtureRevision] = useState(0);
  const [fixtureCatalog, setFixtureCatalog] = useState<CatalogState | null>(
    ownerReview ? ownerReviewCatalogState("ready") : null,
  );
  const liveCatalog = useInstrumentCatalog(!ownerReview);
  const catalog = ownerReview
    ? (fixtureCatalog ?? ownerReviewCatalogState("ready"))
    : liveCatalog;
  const [detailMap, setDetailMap] = useState<
    Record<string, SketchDetailResponse | "404" | "500">
  >({});
  const [quantifySeedYaml, setQuantifySeedYaml] = useState<string | null>(null);

  const sketchDetailLookup = useMemo<SketchDetailLookup | null>(() => {
    if (!ownerReview) {
      return null;
    }
    return async (origin, sketchId) => {
      const key = `${origin}::${sketchId}`;
      const deferred = deferredLookups.get(key);
      if (deferred) {
        return deferred.promise;
      }
      const entry = detailMap[key];
      if (entry === "404") {
        throw new SketchFetchError(404, "not found");
      }
      if (entry === "500") {
        throw new SketchFetchError(500, "server error");
      }
      if (entry && typeof entry === "object") {
        return entry;
      }
      // Default exact 404 for unknown fixture keys
      throw new SketchFetchError(404, "not found");
    };
  }, [ownerReview, detailMap]);

  const loadFixture = (id: OwnerReviewFixtureId) => {
    if (!ownerReview || !memoryRef.current) {
      return;
    }
    const result = applyOwnerReviewFixture(id, memoryRef.current);
    setActiveFixture(id);
    setFixtureCatalog(result.catalog);
    setDetailMap(result.detailMap);
    setQuantifySeedYaml(result.quantifyYaml);
    // Always bump — same fixture re-press resets to deterministic state
    setFixtureRevision((n) => n + 1);
  };

  const value: WorkbenchContextValue = {
    ownerReview,
    storage,
    catalog,
    sketchDetailLookup,
    activeFixture,
    fixtureRevision,
    loadFixture,
    quantifySeedYaml,
    setQuantifySeedYaml,
  };

  return (
    <WorkbenchContext.Provider value={value}>
      {children}
    </WorkbenchContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- context hook co-located
export function useWorkbench(): WorkbenchContextValue {
  const ctx = useContext(WorkbenchContext);
  if (!ctx) {
    return {
      ownerReview: false,
      storage: localStorage,
      catalog: { status: "loading" },
      sketchDetailLookup: null,
      activeFixture: null,
      fixtureRevision: 0,
      loadFixture: () => undefined,
      quantifySeedYaml: null,
      setQuantifySeedYaml: () => undefined,
    };
  }
  return ctx;
}
