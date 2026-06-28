#!/usr/bin/env bash
# launchd entrypoint for the Signal Engine dashboard API + Cloudflare tunnel.
#
# Why this exists: the dashboard (https://web-beta-beige-60.vercel.app) talks to the local
# FastAPI backend through a Cloudflare Quick Tunnel whose URL changes every run. If the engine
# dies (laptop sleep, crash, logout) nothing restarts it, so the dashboard goes dark and the
# Dhan "Reconnect" / token-reset card disappears (AuthGate falls back to its red banner).
#
# This wrapper establishes a login-shell-like environment (nvm default node so `vercel` and
# `cloudflared` resolve) and hands off to run-with-tunnel.sh, which (re)creates the tunnel,
# repoints the Vercel env var, and redeploys. It is managed by
#   ~/Library/LaunchAgents/com.vikrant.signal-engine-tunnel.plist
# with RunAtLoad + KeepAlive, so the service comes up at login and auto-restarts if it dies.
set -uo pipefail

export HOME="${HOME:-/Users/vikrantdeshmukh}"
export NVM_DIR="$HOME/.nvm"
# Activate the default node toolchain (puts node/npm/vercel/cloudflared on PATH).
# shellcheck source=/dev/null
if [ -s "$NVM_DIR/nvm.sh" ]; then
  . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true
  nvm use default >/dev/null 2>&1 || true
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

cd "/Users/vikrantdeshmukh/Personal projects" || exit 1
exec ./run-with-tunnel.sh
