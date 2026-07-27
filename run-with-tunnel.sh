#!/usr/bin/env bash
# run-with-tunnel.sh — SELF-HEALING supervisor for the dashboard's backend path:
#   local FastAPI (:8000)  →  Cloudflare Quick Tunnel  →  Vercel frontend env + redeploy.
#
# Rewritten 2026-07-13 after repeated "dashboard doesn't open" incidents. Root causes fixed:
#   1. cloudflared died mid-day while the API lived → the old script only `wait`ed on the API,
#      so launchd saw a healthy service while the public URL was a corpse. NOW: a supervision
#      loop probes BOTH processes AND the public URL end-to-end every 30s; any hard failure
#      exits the wrapper so launchd (KeepAlive) restarts the whole stack with a fresh URL.
#   2. The Vercel env-update + redeploy ran ONCE, silently (`2>/dev/null || true`) — a failed
#      deploy (e.g. broken-IPv6 hotspot at boot) left the frontend pointing at an old URL with
#      no error anywhere. NOW: sync_vercel() logs loudly to logs/tunnel-sync.log, retries 3x,
#      verifies the env var by reading it back, and exits nonzero if the frontend can't be
#      re-pointed (so launchd keeps retrying instead of pretending everything is fine).
#   3. Broken-IPv6 networks (iPhone hotspot) hang dual-stack connects. NOW: cloudflared runs
#      with --edge-ip-version 4 and node tools get NODE_OPTIONS=--dns-result-order=ipv4first
#      (the Python API already prefers IPv4 via signal_engine/net.py in cli.main).
#   4. Every heal action is Telegram-alerted, so a URL rotation is visible — and the message
#      carries the fresh backend URL as a direct fallback if trycloudflare DNS is blocked on
#      the client device.
#
# Managed by ~/Library/LaunchAgents/com.vikrant.signal-engine-tunnel.plist (KeepAlive).
# Manual run: ./run-with-tunnel.sh

set -uo pipefail   # NOT -e: the supervision loop must reach its own exit decisions
REPO="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$REPO/logs"; mkdir -p "$LOG_DIR" "$REPO/data"
SYNC_LOG="$LOG_DIR/tunnel-sync.log"
TUNNEL_LOG="$LOG_DIR/cloudflared.log"
STATE_FILE="$REPO/data/tunnel_url.txt"
DASHBOARD_URL="https://web-beta-beige-60.vercel.app"

log() { echo "$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S IST') $*" | tee -a "$SYNC_LOG"; }

# Secrets from gitignored .env — never hardcoded.
# shellcheck source=/dev/null
[ -f "$REPO/.env" ] && set -a && . "$REPO/.env" && set +a
VERCEL_TOKEN="${VERCEL_TOKEN:?Set VERCEL_TOKEN in .env}"
VERCEL_SCOPE="${VERCEL_SCOPE:-vikrantdeshmukh}"
PORT="${PORT:-8000}"

# IPv4-first for node tooling (vercel CLI) on broken-IPv6 networks.
export NODE_OPTIONS="--dns-result-order=ipv4first ${NODE_OPTIONS:-}"

source "$REPO/.venv/bin/activate"
export NVM_DIR="$HOME/.nvm"
# shellcheck source=/dev/null
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

telegram() {  # best-effort, never fatal
    python - "$1" <<'PY' 2>/dev/null || true
import sys
from signal_engine.config import load_config
from signal_engine.factory import build_alerter
from signal_engine.alerts import send_alert
send_alert(build_alerter(load_config()), sys.argv[1], level="warning")
PY
}

probe() {  # end-to-end: public tunnel URL must answer /healthz (IPv4, 10s budget)
    curl -4 -sS -m 10 -o /dev/null -w "%{http_code}" "$1/healthz" 2>/dev/null
}

sync_vercel() {  # $1 = public URL. Loud, retried, verified. Returns 0 only on full success.
    local url="$1" attempt readback
    for attempt in 1 2 3; do
        log "vercel sync attempt $attempt: NEXT_PUBLIC_API_BASE -> $url"
        echo "y" | vercel env rm NEXT_PUBLIC_API_BASE production \
            --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" --cwd "$REPO/web" --yes \
            >>"$SYNC_LOG" 2>&1 || log "  (env rm failed — may not exist yet, continuing)"
        if ! echo "$url" | vercel env add NEXT_PUBLIC_API_BASE production \
                --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" --cwd "$REPO/web" \
                >>"$SYNC_LOG" 2>&1; then
            log "  env add FAILED"; sleep 5; continue
        fi
        # NOTE: the var lands as type "Sensitive" (team setting), so `env pull` cannot read
        # its value back. Verify existence via `env ls`, then prove the REAL thing after the
        # deploy: the tunnel hostname must appear in the deployed site's built JS chunks
        # (NEXT_PUBLIC_* is inlined at build time — if it's in the shipped bundle, the
        # browser will call the right backend; nothing weaker counts as verified).
        if ! vercel env ls --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" --cwd "$REPO/web" \
                2>>"$SYNC_LOG" | grep -q "NEXT_PUBLIC_API_BASE"; then
            log "  env ls does not show NEXT_PUBLIC_API_BASE — retrying"; sleep 5; continue
        fi
        if ! vercel deploy --prod --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" \
                --cwd "$REPO/web" >>"$SYNC_LOG" 2>&1; then
            log "  deploy FAILED"; sleep 10; continue
        fi
        # End-to-end bake verification against the LIVE deployment.
        local host chunks chunk found
        host=$(echo "$url" | sed 's|https://||')
        found=""
        for page in /paper /; do
            chunks=$(curl -4 -sS -m 15 "$DASHBOARD_URL$page" 2>/dev/null \
                | grep -oE '/_next/static/chunks/[^"]+\.js' | sort -u | head -30)
            for chunk in $chunks; do
                if curl -4 -sS -m 15 "$DASHBOARD_URL$chunk" 2>/dev/null | grep -q "$host"; then
                    found=1; break
                fi
            done
            [ -n "$found" ] && break
        done
        if [ -n "$found" ]; then
            log "  deploy OK + bake VERIFIED — shipped JS references $host"
            return 0
        fi
        log "  deploy succeeded but $host NOT found in shipped JS — retrying"; sleep 10
    done
    return 1
}

# ---- Child lifecycle ----------------------------------------------------------------
# 2026-07-26: found THREE `cli serve` workers alive at once, each pinned at 100% CPU with
# ZERO open sockets. Root cause: uvicorn's graceful shutdown waits for in-flight requests, and
# /api/backtest took 623 s (/api/leaderboard 74 s), so the SIGTERM this script sends on its way
# out never landed. The orphan kept spinning, the next launchd start added ANOTHER worker, and
# each heal made the box slower. The scans now run in a separate process (see
# signal_engine/api/scans.py), and these two helpers close the remaining gap: every exit path
# escalates to SIGKILL, and startup reaps whatever still owns the port first.

stop_children() {  # SIGTERM our own children, then SIGKILL anything still alive.
    local pid grace alive
    for pid in "${API_PID:-}" "${TUNNEL_PID:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    done
    for grace in 1 2 3 4 5; do
        alive=""
        for pid in "${API_PID:-}" "${TUNNEL_PID:-}"; do
            [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && alive=1
        done
        [ -z "$alive" ] && return 0
        sleep 1
    done
    for pid in "${API_PID:-}" "${TUNNEL_PID:-}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            log "child $pid ignored SIGTERM after 5s — SIGKILL"
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
}

reap_port() {  # $1 = port. Clear anything that would fight us for it — a leftover worker from a
               # previous supervisor, or a wedged one that already dropped its listener socket.
               # Scoped to this port on purpose: a `cli serve` on another port is someone else's.
    local port="$1" stale
    stale=$(
        { lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true
          pgrep -f "signal_engine\.cli serve.*--port $port" 2>/dev/null || true
        } | sort -u
    )
    [ -z "$stale" ] && return 0
    log "reaping stale API worker(s) on :$port: $(echo "$stale" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill -9 $stale 2>/dev/null || true
    sleep 1
}

echo "=== Signal Engine — API + Tunnel supervisor (self-healing) ==="

# ---- 0. Reap whatever still owns our port -------------------------------------------
reap_port "$PORT"

# ---- 1. FastAPI backend -------------------------------------------------------------
log "[1/3] starting FastAPI on :$PORT"
python -m signal_engine.cli serve --host 127.0.0.1 --port "$PORT" >>"$LOG_DIR/tunnel.log" 2>&1 &
API_PID=$!
API_OK=""
for i in $(seq 1 20); do
    sleep 1
    if [ "$(curl -sS -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/healthz" 2>/dev/null)" = "200" ]; then
        API_OK=1; break
    fi
done
if [ -z "$API_OK" ]; then
    log "ERROR: API did not come up on :$PORT in 20s — exiting for launchd restart"
    stop_children
    exit 1
fi

# ---- 2. Cloudflare quick tunnel (IPv4 edge) ------------------------------------------
log "[2/3] starting cloudflared quick tunnel"
: > "$TUNNEL_LOG"
cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate --edge-ip-version 4 \
    >>"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

PUBLIC_URL=""
for i in $(seq 1 40); do
    sleep 1
    PUBLIC_URL=$(grep -oE 'https://[a-z0-9.-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
    [ -n "$PUBLIC_URL" ] && break
done
if [ -z "$PUBLIC_URL" ]; then
    log "ERROR: no tunnel URL after 40s — exiting for launchd restart"
    telegram "Dashboard backend: cloudflared failed to start (no URL). Restarting automatically."
    stop_children
    exit 1
fi
log "tunnel up: $PUBLIC_URL"

# ---- 3. Point the Vercel frontend at this tunnel (only when it changed) ---------------
PREV_URL=$(cat "$STATE_FILE" 2>/dev/null || true)
if [ "$PUBLIC_URL" != "$PREV_URL" ]; then
    log "[3/3] URL changed (${PREV_URL:-<none>} -> $PUBLIC_URL): syncing Vercel"
    if sync_vercel "$PUBLIC_URL"; then
        echo "$PUBLIC_URL" > "$STATE_FILE"
        telegram "Dashboard healed: backend re-pointed to $PUBLIC_URL — $DASHBOARD_URL is live again. (Direct backend fallback: $PUBLIC_URL/docs)"
    else
        log "FATAL: could not re-point Vercel after 3 attempts — exiting for launchd retry"
        telegram "Dashboard NOT healed: Vercel sync failed 3x (see logs/tunnel-sync.log). Will keep retrying automatically."
        stop_children
        exit 1
    fi
else
    log "[3/3] URL unchanged — no Vercel sync needed"
fi

echo "============================================"
echo "  Dashboard : $DASHBOARD_URL"
echo "  Backend   : $PUBLIC_URL"
echo "============================================"

# ---- 4. Supervision loop: end-to-end probe every 30s ---------------------------------
# Any hard failure exits nonzero => launchd KeepAlive restarts the WHOLE stack fresh
# (new tunnel URL => step 3 re-points the frontend automatically).
FAILS=0
trap 'log "stopping (signal)"; stop_children; exit 0' INT TERM
while :; do
    sleep 30
    if ! kill -0 "$API_PID" 2>/dev/null; then
        log "API process died — exiting for restart"
        telegram "Dashboard backend: API process died — restarting automatically."
        stop_children
        exit 1
    fi
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        log "cloudflared died — exiting for restart"
        telegram "Dashboard backend: tunnel died — restarting with a fresh URL automatically."
        stop_children
        exit 1
    fi
    code=$(probe "$PUBLIC_URL")
    if [ "$code" = "200" ]; then
        FAILS=0
    else
        FAILS=$((FAILS+1))
        log "public probe failed (HTTP ${code:-000}) — strike $FAILS/4"
        if [ "$FAILS" -ge 4 ]; then
            log "public URL dead for 4 consecutive probes (~2min) — exiting for restart"
            telegram "Dashboard backend: public tunnel URL stopped responding — restarting with a fresh URL automatically."
            stop_children
            exit 1
        fi
    fi
done
