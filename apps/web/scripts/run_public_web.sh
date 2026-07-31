#!/usr/bin/env bash
# Serve production build for nq.happybala.com (Cloudflare Tunnel → :4173).
# Dev remains on :5173; public traffic must NOT use Vite module waterfall.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [[ ! -d node_modules ]]; then
  npm ci
fi

# Rebuild only when forced or dist missing (KeepAlive restarts should be instant).
if [[ "${ALOG_FORCE_BUILD:-0}" == "1" || ! -f dist/index.html ]]; then
  echo "[public-web] building production assets…"
  npm run build
else
  echo "[public-web] using existing dist/ (set ALOG_FORCE_BUILD=1 to rebuild)"
fi

echo "[public-web] preview on 127.0.0.1:4173 (API proxy → :8000)"
exec npm run preview -- --host 127.0.0.1 --port 4173 --strictPort
