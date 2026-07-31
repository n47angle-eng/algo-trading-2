#!/usr/bin/env bash
# Rebuild + sync production dist for nq.happybala.com (does not touch qms).
# Open clients auto-reload via SW update + /build-meta.json poll (~15s).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WEB="$ROOT/apps/web"
SUPPORT="$HOME/Library/Application Support/alog-trading"
DIST_DST="$SUPPORT/public-web/dist"
BIN_DST="$SUPPORT/bin"
cd "$WEB"
npm run build
mkdir -p "$DIST_DST" "$BIN_DST"
rsync -a --delete "$WEB/dist/" "$DIST_DST/"
# Keep public static server in sync (no-cache headers for sw/meta)
install -m 755 "$WEB/scripts/serve_nq_public.py" "$BIN_DST/serve_nq_public.py"
# Restart public server only
launchctl kickstart -k "gui/$(id -u)/com.alog.public-web"
echo "deployed. build-meta:"
cat "$DIST_DST/build-meta.json" 2>/dev/null || true
echo "verify: curl -sI https://nq.happybala.com/ | head -5"
echo "        curl -s https://nq.happybala.com/build-meta.json"
