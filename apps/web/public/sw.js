/* Futures Research PWA service worker
 * - App shell offline cache
 * - Push notification display
 * - Versioned cache for updates (CACHE_VERSION patched at build time)
 * - Optimistic updates: skipWaiting on install + claim on activate
 */
/* eslint-disable no-restricted-globals */

// Vite build patches this string to a unique buildId each deploy.
const CACHE_VERSION = "fr-pwa-dev";
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;

const PRECACHE_URLS = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/favicon.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png",
  "/build-meta.json",
];

self.addEventListener("install", (event) => {
  // Install new SW immediately so open tabs can switch without waiting.
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS).catch(() => undefined))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== SHELL_CACHE && key !== RUNTIME_CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

function isNavigationRequest(request) {
  return (
    request.mode === "navigate" ||
    (request.method === "GET" &&
      request.headers.get("accept") &&
      request.headers.get("accept").includes("text/html"))
  );
}

function isBypassCache(url) {
  const p = url.pathname;
  return (
    p.startsWith("/api/") ||
    p === "/health" ||
    p.startsWith("/health") ||
    p === "/sw.js" ||
    p === "/build-meta.json" ||
    p.endsWith("/build-meta.json")
  );
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  // Never cache API / health / SW / build meta (always network)
  if (isBypassCache(url)) {
    event.respondWith(
      fetch(request, { cache: "no-store" }).catch(() =>
        caches.match(request).then((c) => c || Response.error()),
      ),
    );
    return;
  }

  // App shell: network-first with cache fallback for offline
  if (isNavigationRequest(request)) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          void caches.open(SHELL_CACHE).then((cache) => cache.put("/", copy));
          return response;
        })
        .catch(() =>
          caches.match("/").then((cached) => cached || caches.match("/index.html")),
        ),
    );
    return;
  }

  // Same-origin static assets: stale-while-revalidate (hashed assets are immutable)
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(request).then((cached) => {
        const network = fetch(request)
          .then((response) => {
            if (response && response.status === 200) {
              const copy = response.clone();
              void caches
                .open(RUNTIME_CACHE)
                .then((cache) => cache.put(request, copy));
            }
            return response;
          })
          .catch(() => cached);
        // Hashed /assets/* prefer cache; other static prefer network when online
        if (url.pathname.startsWith("/assets/") && cached) {
          void network;
          return cached;
        }
        return cached || network;
      }),
    );
  }
});

/** Web Push → system notification (installed PWA path). */
self.addEventListener("push", (event) => {
  let title = "Futures Research";
  let options = {
    body: "有新通知",
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: {},
  };

  if (event.data) {
    try {
      const payload = event.data.json();
      title = payload.title || title;
      options = {
        body: payload.body || options.body,
        icon: payload.icon || options.icon,
        badge: payload.badge || options.badge,
        tag: payload.tag,
        data: payload.data || payload,
        renotify: true,
      };
    } catch {
      options.body = event.data.text();
    }
  }

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  let path = "/";
  if (data.type === "backtest_complete" && data.batchId) {
    path = "/backtest";
  } else if (
    data.type === "buy_opportunity" ||
    data.type === "entry" ||
    data.type === "exit_profit" ||
    data.type === "exit_loss"
  ) {
    path = "/paper";
  } else if (data.url && typeof data.url === "string") {
    path = data.url;
  }

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if ("focus" in client) {
            void client.navigate?.(path);
            return client.focus();
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow(path);
        }
        return undefined;
      }),
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    void self.skipWaiting();
  }
});
