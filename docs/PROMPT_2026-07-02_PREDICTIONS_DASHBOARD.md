# Working Prompt — 2026-07-02 overnight session (predictions dashboard · latency · morning readiness)

> The user's request, expanded into a self-contained, executable prompt. Written at 03:30 IST;
> market opens 09:15 IST today. Everything must be done autonomously, paper-only, no real orders.

## Context
Repo `/Users/vikrantdeshmukh/Personal projects`, branch `feat/full-nse-realtime-pipeline`,
Python `.venv/bin/python`, frontend `web/` (Next.js, Vercel), API `signal_engine/api/app.py`
(FastAPI :8000 behind a Cloudflare quick tunnel, launchd-kept-alive). Master context:
`docs/SESSION_CONTEXT_2026-06.md`, ops: `docs/MORNING_HANDOFF.md`, latency history:
`docs/LATENCY_FINDINGS_2026-06.md`. Telegram is the alert channel (memory: alerter-telegram-preferred).

## Task 1 — Predictions → Telegram → live dashboard log
Every "prediction" the system produces and sends to Telegram (live paper-trade entries/exits with
their plan, pre-market briefing picks, scan results — whatever call sites exist) must be:
1. **Persisted** at send time to a `predictions` log (SQLite, same DB/repository pattern as
   `trade_plans`/`paper_trades`) with: timestamp (IST), kind (entry/exit/premarket/scan/other),
   symbol, side, entry/stop/target, confidence, strategy, quantity/size, regime/context params,
   outcome fields where known, and the exact Telegram message text + delivery status.
2. **Exposed** via the API (`GET /api/predictions` with filters + a live push path — reuse the
   existing WS pattern or a cheap poll) without touching the live paper feed path.
3. **Displayed** on a new dashboard page (`web/app/predictions/`) — a live-updating table, newest
   first, with all parameters visible, symbols clickable to `/stock/<SYM>`, added to the Nav.
Constraints: do NOT double-send Telegram messages; hooking must be non-fatal (a DB failure must
never block an alert); tests for the repository + endpoint; verify live in the browser.

## Task 2 — Latency audit (agent) + safe fixes
Launch a dedicated agent to measure end-to-end latency NOW (API local vs tunnel, REST vs WS first-tick,
quote cadence, dashboard load) and compare against `docs/LATENCY_FINDINGS_2026-06.md`. It should
rank remaining bottlenecks and apply ONLY changes that cannot affect the live paper engine's WS
binary feed or order-of-events (that path is off-limits). Known candidates from last audit: named
Cloudflare tunnel (ops, ~110–145ms hop variance), scrip-master warm-up, uvicorn workers/loop,
Next.js bundle. Every fix: measure before → change → measure after → keep or revert. Full test
suite + tsc must stay green.

## Task 3 — Morning readiness (hard deadline 09:15 IST, checks by 08:45)
1. Root-cause anything red: token (fixed 03:27 — TOTP renew; lapse was Mac asleep all of 07-01),
   launchd agents, tunnel, WS quotes, dashboard pages, scheduler jobs incl. 08:45 healthcheck.
2. Battery is 52% discharging — send a Telegram alert asking the user to plug in (can't be fixed
   programmatically); re-check before the open.
3. After all work: restart affected services cleanly (tunnel restart rotates the URL → verify
   Vercel repoint), re-run the end-to-end verify, update `docs/MORNING_HANDOFF.md` for 07-02, and
   leave a Telegram ✅ summary so the user wakes up to a one-glance status.

## Definition of done
- Predictions flow: a test prediction visibly lands in Telegram AND appears on the new dashboard
  page live with parameters + timestamp.
- Latency: measured numbers before/after documented in `docs/LATENCY_FINDINGS_2026-06.md` (or a
  07-02 addendum); no regression in the live feed.
- Platform verified green end-to-end before 08:45 IST; handoff doc + Telegram summary sent.
- All changes committed in small, labeled commits on `feat/full-nse-realtime-pipeline`.
