#!/usr/bin/env bash
# Wire nq.happybala.com → local Futures Research (Vite 5173) via OWN named tunnel.
# Does NOT touch qms.happybala.com / com.bpss.cloudflared.
set -euo pipefail

TUNNEL_NAME="${NQ_TUNNEL_NAME:-nq-futures}"
PUBLIC_HOST="${NQ_PUBLIC_HOST:-nq.happybala.com}"
LOCAL_PORT="${NQ_LOCAL_PORT:-5173}"
CF="${CLOUDFLARED_BIN:-/opt/homebrew/bin/cloudflared}"
CF_DIR="${HOME}/.cloudflared"
CONFIG="${CF_DIR}/nq-futures-config.yml"
LABEL="com.alog.cloudflared-nq"
LAUNCH_AGENTS="${HOME}/Library/LaunchAgents"
PLIST="${LAUNCH_AGENTS}/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/alog"
UID_NUM="$(id -u)"

die() { echo "ERROR: $*" >&2; exit 1; }

need_cert() {
  [[ -f "${CF_DIR}/cert.pem" ]] || die "missing ${CF_DIR}/cert.pem — run: cloudflared tunnel login"
}

cmd_status() {
  echo "=== cert ==="
  if [[ -f "${CF_DIR}/cert.pem" ]]; then
    stat -f 'cert.pem mode=%Sp size=%z' "${CF_DIR}/cert.pem"
  else
    echo "missing cert.pem"
  fi
  echo "=== tunnel list ==="
  "$CF" tunnel list 2>&1 || true
  echo "=== local ==="
  curl -s -o /dev/null -w "vite 5173: %{http_code}\n" --max-time 3 "http://127.0.0.1:${LOCAL_PORT}/" || true
  curl -s -o /dev/null -w "host nq: %{http_code}\n" --max-time 3 -H "Host: ${PUBLIC_HOST}" "http://127.0.0.1:${LOCAL_PORT}/" || true
  echo "=== public ==="
  curl -s -o /dev/null -w "nq public: %{http_code}\n" --max-time 12 "https://${PUBLIC_HOST}/" || true
  curl -s -o /dev/null -w "qms public: %{http_code}\n" --max-time 12 "https://qms.happybala.com/api/health" || true
  echo "=== agents ==="
  launchctl print "gui/${UID_NUM}/${LABEL}" 2>&1 | head -8 || echo "${LABEL}: absent"
  launchctl print "gui/${UID_NUM}/com.bpss.cloudflared" 2>&1 | head -4 || true
}

write_config() {
  local tunnel_id="$1"
  local cred="${CF_DIR}/${tunnel_id}.json"
  [[ -f "$cred" ]] || die "credentials missing: $cred"
  cat >"$CONFIG" <<EOF
# alog Futures Research — nq.happybala.com only (do not list qms here)
tunnel: ${tunnel_id}
credentials-file: ${cred}
ingress:
  - hostname: ${PUBLIC_HOST}
    service: http://127.0.0.1:${LOCAL_PORT}
  - service: http_status:404
EOF
  chmod 600 "$CONFIG"
  echo "wrote $CONFIG"
}

cmd_create() {
  need_cert
  if "$CF" tunnel list 2>/dev/null | awk 'NR>1 {print $2}' | grep -qx "$TUNNEL_NAME"; then
    echo "tunnel already exists: $TUNNEL_NAME"
  else
    "$CF" tunnel create "$TUNNEL_NAME"
  fi
  local tunnel_id
  tunnel_id="$("$CF" tunnel list | awk -v n="$TUNNEL_NAME" '$2==n {print $1; exit}')"
  [[ -n "$tunnel_id" ]] || die "could not resolve tunnel id for $TUNNEL_NAME"
  echo "tunnel_id=${tunnel_id}"
  write_config "$tunnel_id"

  echo "Routing DNS ${PUBLIC_HOST} → tunnel (may fail if Vercel A records still exist)..."
  if ! "$CF" tunnel route dns "$TUNNEL_NAME" "$PUBLIC_HOST" 2>&1; then
    cat <<EOF

DNS route failed. In Cloudflare DNS for happybala.com:
  1) Delete A records for nq (Vercel 216.150.*)
  2) Re-run: $0 route
Or create CNAME nq → ${tunnel_id}.cfargotunnel.com (Proxied)

EOF
  fi
}

cmd_route() {
  need_cert
  "$CF" tunnel route dns "$TUNNEL_NAME" "$PUBLIC_HOST"
}

cmd_install_agent() {
  need_cert
  mkdir -p "$LOG_DIR" "$LAUNCH_AGENTS"
  [[ -f "$CONFIG" ]] || die "missing $CONFIG — run: $0 create"
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${CF}</string>
    <string>tunnel</string>
    <string>--config</string>
    <string>${CONFIG}</string>
    <string>run</string>
    <string>${TUNNEL_NAME}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${LOG_DIR}/cloudflared-nq.out.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/cloudflared-nq.err.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
  launchctl bootstrap "gui/${UID_NUM}" "$PLIST"
  launchctl kickstart -k "gui/${UID_NUM}/${LABEL}"
  sleep 2
  launchctl print "gui/${UID_NUM}/${LABEL}" 2>&1 | head -10
  echo "installed ${LABEL}"
}

cmd_uninstall_agent() {
  launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed ${LABEL}"
}

cmd_verify() {
  local l p q
  l="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${LOCAL_PORT}/" || echo 000)"
  p="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://${PUBLIC_HOST}/" || echo 000)"
  q="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://qms.happybala.com/api/health" || echo 000)"
  echo "local=${l} public_nq=${p} qms=${q}"
  if [[ "$l" == "200" && "$p" == "200" && "$q" == "200" ]]; then
    echo "VERIFY_OK https://${PUBLIC_HOST}  (qms untouched)"
    return 0
  fi
  echo "VERIFY_FAIL"
  return 1
}

cmd_all() {
  cmd_create
  cmd_install_agent
  echo "Waiting 15s for DNS / edge..."
  sleep 15
  cmd_verify || true
  cmd_status
}

usage() {
  cat <<EOF
Usage: $0 <create|route|install-agent|uninstall-agent|verify|status|all>
  create          Create named tunnel + config + DNS route
  route           Retry DNS CNAME only
  install-agent   LaunchAgent KeepAlive for nq tunnel only
  verify          local 5173 + nq public + qms still 200
  status          Full probe
  all             create + install + verify
EOF
}

case "${1:-}" in
  create) cmd_create ;;
  route) cmd_route ;;
  install-agent) cmd_install_agent ;;
  uninstall-agent) cmd_uninstall_agent ;;
  verify) cmd_verify ;;
  status) cmd_status ;;
  all) cmd_all ;;
  *) usage; exit 1 ;;
esac
