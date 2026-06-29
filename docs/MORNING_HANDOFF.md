# Morning Handoff — live system ready for the open (written 2026-06-30, ~02:40 IST)

> Self-contained context to continue in a fresh session with zero re-derivation. Repo:
> `/Users/vikrantdeshmukh/Personal projects`. Branch: `feat/full-nse-realtime-pipeline`.
> Python: `.venv/bin/python`. **Next session: 2026-06-30 (Tue) 09:15 IST.** Paper-only — no live orders.

## ✅ VERIFIED STATUS (end-to-end, just now) — everything green except battery
- launchd agents **tunnel + scheduler RUNNING**; procs API:8000, cloudflared, scheduler, **caffeinate** all UP.
- **Dhan token VALID** (TOTP auto-login working — `DHAN_TOTP_SECRET` + `DHAN_PIN` set in `.env`, `SE_DATA_SOURCE=dhan`).
- Scheduler jobs registered: renew_token_6/14/22, archive_morning, premarket, live, scan, archive (+ healthcheck, see below).
- Tunnel `east-gaps-silver-participated.trycloudflare.com`: `/healthz` ok, `/api/auth/status` connected:true.
- WS feeds OK: `/ws/quotes` → 40 symbols; `/ws/quotes?symbols=ADANIENT` → live LTP.
- **Dashboard verified in Chrome**: watchlist shows Live ₹ + Trend sparklines + green ●LIVE + real prices;
  `/stock/ADANIENT` shows the always-on live line graph rendering. Both work end-to-end.
- DB: paper_trades 63, trade_plans 115, open_positions 0, live_status 1.

## ⚠️ THE ONE MANUAL ACTION (only thing not auto-fixable)
**Laptop is on BATTERY.** caffeinate prevents idle-sleep but the battery drains over ~6.5h to the 09:15 open.
**Keep it OPEN and PLUGGED IN** or it sleeps/dies and the morning jobs miss. Everything else is autonomous.

## What runs AUTOMATICALLY today (no human needed, given AC power)
launchd keeps the scheduler + tunnel alive + caffeinate awake. IST jobs:
- **06:00 / 14:00 / 22:00 + on startup** — TOTP auto-login mints a fresh token (permanent, no OTP).
- **08:00** archive (prior session bars) · **08:30** pre-market briefing (Telegram) ·
  **08:45** pre-open health check (Telegram ✅/⚠️, self-heals token) ·
  **09:15→15:30** live Dhan feed → paper trades (persisted) · **15:45** scan · **16:10** archive.

## In-progress at handoff (FINISH THIS — small)
`scheduler.py healthcheck_job` (08:45 pre-open verify + Telegram alert + TOTP self-heal) was **added** but
the 4 final steps may be pending: (1) register it in `build_scheduler` (CronTrigger mon-fri 08:45,
misfire_grace 1800, coalesce); (2) update `tests/test_scheduler.py` job-IDs to include `"healthcheck"`;
(3) `launchctl kickstart -k gui/$(id -u)/com.vikrant.signal-engine-scheduler`; (4) commit.
**Check `git status` / `git log` first** — it may already be done (see final commit).

## How to operate / verify
- Status: `launchctl print gui/$(id -u)/com.vikrant.signal-engine-{tunnel,scheduler} | grep state`
- Restart: `launchctl kickstart -k gui/$(id -u)/com.vikrant.signal-engine-{tunnel|scheduler}`
- Current tunnel URL: `grep -oE 'https://[a-z0-9.-]+\.trycloudflare\.com' logs/launchd-engine.out.log | tail -1`
- Token test: `./run.sh renew-token` (✅ "via TOTP auto-login"; Dhan rate-limits mints to 1/2min — ignore that msg)
- Full verify recipe + ops: `docs/OPS_ENGINE_TUNNEL.md`.

## Caveats (known, not blockers)
- **Battery** (above). **Local router DNS can't resolve `*.trycloudflare.com`** — the browser's DoH does, so
  the dashboard works; if it ever shows the red "can't reach backend" banner while the engine is UP, set DNS
  to 1.1.1.1. Quick-tunnel URL changes on every restart (Vercel auto-repointed). Dashboard latency ~1s
  (tunnel ceiling). **Rotate the `sycfyb-…` password** the user pasted in chat earlier.

## Research state (separate from ops)
No tradeable edge found. PEAD/jump-drift is the most real signal but does NOT clear (alpha is short-side
mid-caps, walled off by short-sale constraints; fails Deflated-Sharpe on tradeable subset). Live strategy
`vwap_ema_adx` = gross coin-flip, ~8bps cost is the leak; only the frequency-cap fix is justified (config-
gated, DEFAULT OFF). See `docs/RESEARCH_HANDOFF.md`, `docs/PEAD_SPREAD_FINDINGS.md`,
`docs/PAPER_TRADING_ANALYSIS_2026-06.md`, and memory (`edge-research-arc-and-next-step`, `live-strategy-
paper-analysis`).

## For the new session
1. Re-run the verify (agents/token/tunnel/WS/dashboard); fix anything; confirm laptop is plugged in.
2. Finish `healthcheck_job` if pending (above).
3. After the 09:15 open: confirm paper trades land + dashboard ticks live; fold today's session into the
   running gap analysis (`scratchpad/gap_dataset.json` pattern). Don't enable the gated strategy changes yet
   (need ~30 sessions).
