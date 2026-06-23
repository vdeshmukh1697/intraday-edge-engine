#!/usr/bin/env bash
# run-with-tunnel.sh — Start the signal engine API locally + expose it via a
# free Cloudflare Quick Tunnel, then update the Vercel dashboard env var so
# the hosted frontend at https://web-beta-beige-60.vercel.app points at it.
#
# Usage:  ./run-with-tunnel.sh
# Requires: .venv activated (or run from project root), nvm (for vercel CLI)

set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"

# Secrets come from the environment / gitignored .env — never hardcode them here.
# shellcheck source=/dev/null
[ -f "$REPO/.env" ] && set -a && . "$REPO/.env" && set +a
VERCEL_TOKEN="${VERCEL_TOKEN:?Set VERCEL_TOKEN in .env (see .env.example)}"
VERCEL_SCOPE="${VERCEL_SCOPE:-vikrantdeshmukh}"
PORT="${PORT:-8000}"   # override (e.g. PORT=8001) if 8000 is taken

echo "=== Signal Engine — API + Cloudflare Tunnel ==="

# 1. Activate venv
source "$REPO/.venv/bin/activate"

# 2. Load nvm so vercel + cloudflared are on PATH
export NVM_DIR="$HOME/.nvm"
# shellcheck source=/dev/null
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# 3. Start the FastAPI backend in the background
echo "[1/3] Starting FastAPI backend on http://127.0.0.1:$PORT ..."
python -m signal_engine.cli serve --host 127.0.0.1 --port "$PORT" &
API_PID=$!
sleep 3  # give uvicorn a moment to bind

# 4. Start cloudflared quick tunnel (no account needed)
echo "[2/3] Creating Cloudflare Quick Tunnel ..."
TUNNEL_LOG=$(mktemp)
cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate 2>"$TUNNEL_LOG" &
TUNNEL_PID=$!

# Wait for the public URL to appear in the log.
# NOTE: use -oE (POSIX ERE) not -oP (PCRE) — macOS/BSD grep has no -P, so -P silently
# matches nothing and the tunnel looks like it "failed" even though it connected.
PUBLIC_URL=""
for i in $(seq 1 30); do
    sleep 1
    PUBLIC_URL=$(grep -oE 'https://[a-z0-9.-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
    [ -n "$PUBLIC_URL" ] && break
done

if [ -z "$PUBLIC_URL" ]; then
    echo "ERROR: Could not determine tunnel URL. Check $TUNNEL_LOG"
    kill "$API_PID" "$TUNNEL_PID" 2>/dev/null || true
    exit 1
fi

echo ""
echo "  Public tunnel URL: $PUBLIC_URL"

# 5. Update the Vercel env var so the dashboard uses this tunnel
echo "[3/3] Updating Vercel NEXT_PUBLIC_API_BASE ..."
# Remove existing and re-add. The linked project lives in web/, so all vercel commands
# must run with --cwd "$REPO/web" (the repo root is not a linked Vercel project).
echo "y" | vercel env rm NEXT_PUBLIC_API_BASE production \
    --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" --cwd "$REPO/web" --yes 2>/dev/null || true
echo "$PUBLIC_URL" | vercel env add NEXT_PUBLIC_API_BASE production \
    --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" --cwd "$REPO/web" 2>/dev/null

# Trigger a new Vercel deploy so the env var takes effect
vercel deploy --prod \
    --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" \
    --cwd "$REPO/web" 2>/dev/null | grep -E "Production|Alias" || true

echo ""
echo "============================================"
echo "  Dashboard : https://web-beta-beige-60.vercel.app"
echo "  Backend   : $PUBLIC_URL"
echo "  Docs      : $PUBLIC_URL/docs"
echo ""
echo "Press Ctrl+C to stop the backend and tunnel."
echo "============================================"

# Keep running until the user hits Ctrl+C
trap 'echo ""; echo "Stopping..."; kill "$API_PID" "$TUNNEL_PID" 2>/dev/null; rm -f "$TUNNEL_LOG"; exit 0' INT TERM
wait "$API_PID"
