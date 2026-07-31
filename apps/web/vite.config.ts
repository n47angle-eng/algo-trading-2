import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/** Unique per production build — drives SW cache bust + client auto-update. */
function makeBuildId(): string {
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
  const salt = createHash("sha1")
    .update(`${stamp}-${process.pid}-${Math.random()}`)
    .digest("hex")
    .slice(0, 8);
  return `${stamp}-${salt}`;
}

const BUILD_ID = process.env.ALOG_BUILD_ID?.trim() || makeBuildId();

/** Shared reverse-proxy: public tunnel + preview both hit FastAPI on :8000. */
const apiProxy = {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
  },
  "/health": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
  },
} as const;

/**
 * Writes dist/build-meta.json and patches dist/sw.js CACHE_VERSION so each
 * deploy is uniquely discoverable by open clients.
 */
function buildMetaPlugin(buildId: string): Plugin {
  return {
    name: "fr-build-meta",
    apply: "build",
    writeBundle(outputOptions) {
      const outDir = outputOptions.dir ?? join(process.cwd(), "dist");
      const meta = {
        buildId,
        builtAt: new Date().toISOString(),
        app: "futures-research",
      };
      writeFileSync(
        join(outDir, "build-meta.json"),
        `${JSON.stringify(meta, null, 2)}\n`,
        "utf8",
      );

      const swPath = join(outDir, "sw.js");
      if (existsSync(swPath)) {
        const raw = readFileSync(swPath, "utf8");
        const next = raw.replace(
          /const CACHE_VERSION = ["'][^"']*["']/,
          `const CACHE_VERSION = "fr-${buildId}"`,
        );
        if (next !== raw) {
          writeFileSync(swPath, next, "utf8");
        }
      }
      // eslint-disable-next-line no-console
      console.log(`[fr-build-meta] buildId=${buildId}`);
    },
  };
}

export default defineConfig({
  define: {
    __APP_BUILD_ID__: JSON.stringify(BUILD_ID),
  },
  plugins: [react(), buildMetaPlugin(BUILD_ID)],
  // Relative-safe asset base for reverse-proxy / HTTPS PWA hosting.
  base: "/",
  server: {
    // Local dev only (fast). Public nq tunnel should use production dist :4173.
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    allowedHosts: ["nq.happybala.com", "localhost", "127.0.0.1"],
    hmr: {
      host: "nq.happybala.com",
      protocol: "wss",
      clientPort: 443,
    },
    proxy: { ...apiProxy },
  },
  preview: {
    // Production assets for Cloudflare Tunnel (few hashed files, not 200 modules).
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    allowedHosts: ["nq.happybala.com", "localhost", "127.0.0.1"],
    proxy: { ...apiProxy },
  },
  build: {
    // Ensure public/sw.js + manifest land in dist unchanged (then patched).
    assetsInlineLimit: 4096,
    target: "es2022",
    cssCodeSplit: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        // Split heavy vendor / pages for parallel download over tunnel.
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("lightweight-charts")) return "charts";
            if (id.includes("react-dom") || id.includes("react-router")) {
              return "react-vendor";
            }
            if (id.includes("react")) return "react-vendor";
            if (id.includes("jszip") || id.includes("js-yaml")) return "utils";
            return "vendor";
          }
          return undefined;
        },
      },
    },
  },
});
