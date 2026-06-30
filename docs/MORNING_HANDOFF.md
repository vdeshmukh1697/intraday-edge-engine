# Morning Handoff — live system ready for the open (updated 2026-06-30, ~14:35 IST by morning-verify)

> Self-contained context to continue in a fresh session with zero re-derivation. Repo:
> `/Users/vikrantdeshmukh/Personal projects`. Branch: `feat/full-nse-realtime-pipeline`.
> Python: `.venv/bin/python`. **Next session: 2026-07-01 (Wed) 09:15 IST.** Paper-only — no live orders.

## 🔄 2026-06-30 morning-verify run (end-of-day state) — all GREEN
Verified mid-session at 14:35 IST (task fired in the afternoon, not at open):
- launchd **tunnel + scheduler RUNNING**; API:8000 (pid 42436), scheduler (44664), caffeinate (44666) all UP.
- **Dhan token VALID → 2026-07-01 08:30** (TOTP auto-login working). Tunnel `east-gaps-silver-participated`:
  `/healthz` ok, `/api/auth/status` connected:true. WS `/ws/quotes` streaming (a 429 in testing was just the
  verify colliding with the live feed — DH-904 throttle, transient, not a fault).
- **Live paper session ran fine**: live_status heartbeat fresh (14:30, last bar 14:29), run_id ties to the
  scheduler proc. **13 trades today, all closed**, none after 11:46.
- **Battery alert SENT via Telegram** — Mac is on BATTERY (61%, ~20h). Fine for today; **must be plugged in
  overnight** or tonight's 06:00 token-renew + tomorrow's 08:30/08:45/09:15 jobs miss. (User action; caffeinate
  can't save a dead battery.) ⚠️ This is still THE one manual action.
- **Session 4 folded** into `docs/PAPER_TRADING_ANALYSIS_2026-06.md` (dataset `scratchpad/session_2026-06-30.json`):
  net +1.88% but **100% from one name (OLAELEC, 3/3 targets); strip it → 0/10, −3.58%.** Confidence still
  non-predictive (every conf=1.00 trade lost), 09:42 fired a 4-entry burst (frequency-cap #1 still the only
  justified fix), universe gate #3 re-confirmed UNjustified (OLAELEC is on the drop-list and was the sole
  winner). Noise, not edge. Gated config changes remain DEFAULT OFF. **Tomorrow's run: confirm final trade
  count (top-up if any 14:35–15:30 entries landed) — conclusion is robust regardless.**

## 🚀 2026-06-30 dashboard + latency upgrade (afternoon, shipped + verified live) — see `docs/LATENCY_FINDINGS_2026-06.md`
Four changes, committed (`8b2d72b`, `875343f`, `4d17cff`) and **deployed** (tunnel restarted → Vercel
redeployed local `web/`). All verified in Chrome at market open; tsc + tests green. Paper-only, no order paths.
- **Latency:** `/ws/quotes` was polling Dhan REST per connection (Dhan quote REST ≈1 req/s → a 2nd
  consumer got 429'd + dropped ticks; ~3.4s cold start each). New shared **`_QuoteHub`** = one poll for
  all connections. Before→after (live): 2nd consumer throttled 16/16 → **3 concurrent 0/30 throttled,
  ~0.12s first tick, 1 Dhan poll for N**. Live paper engine untouched (separate WS binary feed).
- **Stock live graph** now **seeds with today's intraday from the 09:15 open** (`/api/intraday/{symbol}`,
  Dhan historical → Yahoo/archive fallback, TTL-cached), then streams live — was blank-from-page-open.
- **Paper page = broker account**: account value, ROC, realized+unrealized P&L (₹+%), exposure, win/loss,
  max drawdown (`/api/paper/analytics` now returns `account_capital`). **Stocks are clickable → /stock/<SYM>.**
- NOTE the tunnel URL changed twice today (two restarts) — current is read dynamically (grep recipe below).
  Recommended next ops upgrade: **named Cloudflare tunnel** (kills the ~110–145ms hop variance + per-restart
  churn). Did NOT swap `/ws/quotes` to the Dhan WS binary feed (would contend with the live paper feed).

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
