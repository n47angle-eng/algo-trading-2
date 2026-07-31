#!/usr/bin/env bash
# Wrapper: production web for nq tunnel (see apps/web/scripts/run_public_web.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec bash "$ROOT/apps/web/scripts/run_public_web.sh"
