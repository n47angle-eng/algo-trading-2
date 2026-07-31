/**
 * Service worker registration + real-time optimistic auto-update.
 * Hand-rolled SW at /sw.js (no vite-plugin-pwa dependency).
 */

import {
  AUTO_RELOAD_PAINT_MS,
  UPDATE_CHECK_MS,
  claimReloadForBuild,
  currentAppBuildId,
  fetchRemoteBuildMeta,
  hardReload,
  shouldReloadForBuild,
} from "./autoUpdate";

export type SwUpdateHandler = (reload: () => void) => void;

export interface RegisterSwResult {
  registered: boolean;
  updateAvailable: boolean;
}

export interface RegisterSwOptions {
  /**
   * When true (default), apply updates immediately without a "稍後" gate.
   * Still fires onNeedRefresh so UI can show a brief "正在更新" banner.
   */
  autoReload?: boolean;
  /** Poll SW update + build-meta.json. Default UPDATE_CHECK_MS. */
  checkIntervalMs?: number;
  /** Injected for tests. */
  fetchImpl?: typeof fetch;
}

let updateLoopStarted = false;
let reloadArmed = false;

function dispatchNeedRefresh(reload: () => void): void {
  window.dispatchEvent(
    new CustomEvent("fr-sw-need-refresh", { detail: { reload } }),
  );
}

function armOptimisticReload(
  apply: () => void,
  onNeedRefresh?: SwUpdateHandler,
  autoReload = true,
): void {
  if (reloadArmed) {
    return;
  }
  reloadArmed = true;

  const run = () => {
    try {
      apply();
    } finally {
      // Fallback if apply only posts SKIP_WAITING
      window.setTimeout(() => hardReload(), 1200);
    }
  };

  onNeedRefresh?.(run);
  dispatchNeedRefresh(run);

  if (autoReload) {
    window.setTimeout(run, AUTO_RELOAD_PAINT_MS);
  }
}

/**
 * Register /sw.js, poll for updates, optimistically reload when a new build lands.
 */
export async function registerAppServiceWorker(
  onNeedRefresh?: SwUpdateHandler,
  onOfflineReady?: () => void,
  options: RegisterSwOptions = {},
): Promise<RegisterSwResult> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) {
    startBuildMetaWatch(onNeedRefresh, options);
    return { registered: false, updateAvailable: false };
  }

  // Skip SW on Vite pure-dev unless explicitly opted in
  if (import.meta.env.DEV && import.meta.env.VITE_PWA_DEV !== "1") {
    return { registered: false, updateAvailable: false };
  }

  const autoReload = options.autoReload !== false;
  const checkMs = options.checkIntervalMs ?? UPDATE_CHECK_MS;
  let updateAvailable = false;

  try {
    // Query bust so CDNs (Cloudflare) cannot keep serving a stale SW script.
    const buildId = currentAppBuildId();
    const swUrl =
      buildId && buildId !== "dev" ? `/sw.js?v=${encodeURIComponent(buildId)}` : "/sw.js";
    const registration = await navigator.serviceWorker.register(swUrl, {
      scope: "/",
      updateViaCache: "none",
    });

    const applyWaiting = () => {
      if (registration.waiting) {
        registration.waiting.postMessage({ type: "SKIP_WAITING" });
      }
    };

    const promptUpdate = () => {
      updateAvailable = true;
      armOptimisticReload(
        () => {
          applyWaiting();
          // controllerchange listener below will reload; hard fallback inside arm
        },
        onNeedRefresh,
        autoReload,
      );
    };

    if (registration.waiting) {
      promptUpdate();
    }

    registration.addEventListener("updatefound", () => {
      const worker = registration.installing;
      if (!worker) {
        return;
      }
      worker.addEventListener("statechange", () => {
        if (
          worker.state === "installed" &&
          navigator.serviceWorker.controller
        ) {
          promptUpdate();
        } else if (
          worker.state === "installed" &&
          !navigator.serviceWorker.controller
        ) {
          onOfflineReady?.();
        }
      });
    });

    // New SW took control → load new assets immediately
    let refreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshing) {
        return;
      }
      refreshing = true;
      hardReload();
    });

    if (navigator.serviceWorker.controller) {
      onOfflineReady?.();
    }

    // Periodic SW check + build-meta poll
    const tick = () => {
      void registration.update().catch(() => undefined);
      void checkBuildMeta(onNeedRefresh, options);
    };
    if (!updateLoopStarted) {
      updateLoopStarted = true;
      window.setInterval(tick, checkMs);
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
          tick();
        }
      });
      window.addEventListener("online", tick);
      // First check shortly after boot (covers already-open tabs after deploy)
      window.setTimeout(tick, 2_500);
    }

    return { registered: true, updateAvailable };
  } catch {
    startBuildMetaWatch(onNeedRefresh, options);
    return { registered: false, updateAvailable: false };
  }
}

function startBuildMetaWatch(
  onNeedRefresh: SwUpdateHandler | undefined,
  options: RegisterSwOptions,
): void {
  if (import.meta.env.DEV && import.meta.env.VITE_PWA_DEV !== "1") {
    return;
  }
  if (updateLoopStarted) {
    return;
  }
  updateLoopStarted = true;
  const checkMs = options.checkIntervalMs ?? UPDATE_CHECK_MS;
  const tick = () => {
    void checkBuildMeta(onNeedRefresh, options);
  };
  window.setInterval(tick, checkMs);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      tick();
    }
  });
  window.addEventListener("online", tick);
  window.setTimeout(tick, 2_500);
}

async function checkBuildMeta(
  onNeedRefresh: SwUpdateHandler | undefined,
  options: RegisterSwOptions,
): Promise<void> {
  try {
    const remote = await fetchRemoteBuildMeta(options.fetchImpl ?? fetch);
    if (!remote) {
      return;
    }
    const current = currentAppBuildId();
    if (!shouldReloadForBuild(current, remote.buildId)) {
      return;
    }
    if (!claimReloadForBuild(remote.buildId)) {
      return;
    }
    armOptimisticReload(
      () => hardReload(),
      onNeedRefresh,
      options.autoReload !== false,
    );
  } catch {
    /* offline / transient — ignore */
  }
}
