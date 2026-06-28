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
3. **cloudflared-only death** isn't self-healed (the script waits on the API pid, not the tunnel pid);
   a full crash/sleep is healed, a lone tunnel drop is not.

**The durable upgrade (manual, one-time):** replace the Quick Tunnel with a **named Cloudflare tunnel**
on a domain you control → stable hostname that resolves everywhere and never changes, so the dashboard's
`NEXT_PUBLIC_API_BASE` is fixed and no per-run Vercel redeploy is needed. Needs `cloudflared tunnel login`
(interactive) + a Cloudflare-managed domain — can't be scripted headlessly.

## Note
The live **scheduler** (`cli schedule`, the 08:30/09:15 trading day) and **live feed** (`cli live`) are
SEPARATE processes — this agent only keeps the **dashboard API + tunnel** alive. Add them to keep-alive
separately if desired.
