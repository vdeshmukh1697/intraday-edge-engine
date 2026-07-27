# Ops — Dashboard backend (API + Cloudflare tunnel) keep-alive

The hosted dashboard (https://web-beta-beige-60.vercel.app) talks to the **local** FastAPI engine
through a **Cloudflare Quick Tunnel**. The tunnel URL changes every run, and `run-with-tunnel.sh`
repoints the Vercel `NEXT_PUBLIC_API_BASE` env + redeploys on each start. If the engine dies (laptop
sleep, crash, logout) the dashboard goes dark and the Dhan **Reconnect / token-reset card disappears**
(`AuthGate` falls back to its red "can't reach backend" banner — it is *not* a UI bug).

## Keep-alive (installed 2026-06-28)
A per-user **launchd agent** runs the engine+tunnel at login and auto-restarts it if it dies:

- `scripts/run-engine-service.sh` — launchd entrypoint: sets a sane env (nvm default node so
  `vercel`/`cloudflared` resolve), then execs `run-with-tunnel.sh`.
- `scripts/com.vikrant.signal-engine-tunnel.plist` — the agent (`RunAtLoad` + `KeepAlive`,
  `ThrottleInterval` 120s). Installed copy lives at
  `~/Library/LaunchAgents/com.vikrant.signal-engine-tunnel.plist`.
- Logs: `logs/launchd-engine.out.log` (tunnel URL + Vercel deploy) and `logs/launchd-engine.err.log`
  (uvicorn).

### Operate it
```bash
UID=$(id -u)
# start / stop / restart
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.vikrant.signal-engine-tunnel.plist   # start (install)
launchctl bootout    gui/$UID/com.vikrant.signal-engine-tunnel                                # stop (uninstall from runtime)
launchctl kickstart -k gui/$UID/com.vikrant.signal-engine-tunnel                              # force restart
launchctl print    gui/$UID/com.vikrant.signal-engine-tunnel | grep -E 'state|pid|last exit' # status
# current public tunnel URL
grep -oE 'https://[a-z0-9.-]+\.trycloudflare\.com' logs/launchd-engine.out.log | tail -1
```
After a (re)start it takes ~30–90s for the tunnel + Vercel redeploy. When the Dhan token is expired
the dashboard then shows **Reconnect Dhan** → OTP → token written back to `.env`.

## Known fragilities (and the real fix)
1. **Quick-tunnel URL churn** — every restart = new `*.trycloudflare.com` URL = a Vercel redeploy.
2. **Local DNS can't resolve `*.trycloudflare.com`** — this router's resolver (192.168.29.1) returns
   NXDOMAIN for trycloudflare hostnames (public resolvers 1.1.1.1/8.8.8.8 resolve fine). Browsers with
   DNS-over-HTTPS (Chrome/Firefox default-ish) bypass this; if the dashboard ever shows the red banner
   while the engine is up, set the OS/browser DNS to **1.1.1.1 or 8.8.8.8** (or enable DoH).
3. **Quick-tunnel edge timeout (~100s)** — any request slower than that dies at Cloudflare before the
   API answers. The three scan endpoints are cached behind a subprocess for exactly this reason
   (below); don't add a new endpoint that computes for minutes on the request path.

## Child lifecycle (2026-07-26)
`run-with-tunnel.sh` owns two children (the API and `cloudflared`) and supervises the public URL
end-to-end every 30s. Two rules keep a self-heal from *adding* a worker instead of replacing one:
- `stop_children()` runs on **every** exit path (including the INT/TERM trap): SIGTERM, then SIGKILL
  after a 5s grace. uvicorn's graceful shutdown waits for in-flight requests, so a slow request used
  to outlive the supervisor entirely.
- `reap_port "$PORT"` runs **before** binding: it kills whatever holds `:8000`, found via both `lsof`
  and a `--port 8000`-scoped `pgrep` (a wedged worker can still be spinning after it has dropped its
  listener socket). Scoped to the port on purpose — a `cli serve` on another port belongs to someone
  else.

The heavy scans (`/api/leaderboard`, `/api/premarket`, `/api/backtest`) run in **separate processes**
(`signal_engine/api/scans.py`) behind a single-flight, serve-stale TTL cache. There are two lanes with
one worker each — read-path scans on one, `/api/backtest` (minutes long) on its own so it cannot starve
them — and each spawns on first use. If you see the API process at ~0% CPU and one or two
`multiprocessing.spawn` children at 100%, that is working as designed.

**The durable upgrade (manual, one-time):** replace the Quick Tunnel with a **named Cloudflare tunnel**
on a domain you control → stable hostname that resolves everywhere and never changes, so the dashboard's
`NEXT_PUBLIC_API_BASE` is fixed and no per-run Vercel redeploy is needed. Needs `cloudflared tunnel login`
(interactive) + a Cloudflare-managed domain — can't be scripted headlessly.

## Note
The live **scheduler** (`cli schedule`, the 08:30/09:15 trading day) and **live feed** (`cli live`) are
SEPARATE processes — this agent only keeps the **dashboard API + tunnel** alive. Add them to keep-alive
separately if desired.
