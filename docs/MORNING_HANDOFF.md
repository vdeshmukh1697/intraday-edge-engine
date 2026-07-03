# Morning Handoff — live system ready for the open (updated 2026-07-03, ~07:00 IST by overnight session)

> Self-contained context to continue in a fresh session with zero re-derivation. Repo:
> `/Users/vikrantdeshmukh/Personal projects`. Branch: `feat/full-nse-realtime-pipeline`.
> Python: `.venv/bin/python`. **Session today: 2026-07-03 (Fri) 09:15 IST.** Paper-only — no live orders.

## 🟢 2026-07-03 overnight session — ₹1,00,000 PAPER PORTFOLIO MANAGER (the night's feature)
Working prompt: `docs/PROMPT_2026-07-03_PORTFOLIO_MANAGER.md`. Built by a multi-agent workflow +
hand-finished (the fan-out hit the 5:30am usage-limit mid-run; core ledger/reasons/web landed, the
runner+API wiring + tests + a found bug were completed directly after the limit reset). All committed
on the branch (3 feat commits d76acdf, 6a5c2fc, adc63d5) + the earlier DH-904 fix 7a9d657.

**What the user now has:** ONE persistent ₹1,00,000 paper book the whole platform shares.
- **PortfolioLedger** (`signal_engine/portfolio/ledger.py`): entries block the fill notional as cash
  (margin model — LONG and SHORT alike, NO leverage), exits credit it back + realized ₹ net of the REAL
  modeled charges (`CostModel.charges`). Equity == cash + Σ open(notional+unrealized). `rebuild()`
  re-derives cash from the tables so a restart can never double-spend (runs after warm-start).
- **Money-aware live engine**: `_surface` sizes each entry against the book's LIVE equity (compounds),
  caps by free cash reserved at the adverse-slipped fill price (a bug caught in testing: reserving at the
  plan price let two ~50% positions breach no-leverage once slippage lifted the fill). Unaffordable setups
  → a `skip` alert (plain reason), not a phantom entry. Entry/exit/halt alerts carry qty, ₹ notional,
  % of book, the book's new value, and a plain-English `Why:` line.
- **Layman reasons everywhere** (`signal_engine/alerts/plain.py`): strategy/premarket/scan/halt codes →
  one or two jargon-free sentences, Indian-format rupees (₹1,00,000). Premarket (08:30) + scan (15:45)
  predictions are sized against the book (read-only) and phrased as suggestions.
- **NEW `/portfolio` dashboard page** (first in nav) + `GET /api/portfolio` & `/api/portfolio/equity`:
  total value / cash / invested / today's & overall P&L cards, equity curve, live open-positions table,
  today's closed trades each with its plain "why", per-strategy table. `/predictions` shows the Why line +
  a `skip` chip; `/paper` prefers real ₹ with a `modeled` badge on legacy rows.
- Honesty unchanged: paper money, no edge implied (vwap_ema_adx still a gross coin-flip), confidence never
  called win-rate, no live orders ever.

### ✅ VERIFIED (2026-07-03 ~07:00 IST)
- **Full pytest suite GREEN** (incl. 60+ new portfolio/plain/runner/api tests); `tsc --noEmit` + `next
  build` GREEN (`/portfolio` route compiled). Web `PortfolioResponse` type matches the API field-for-field.
- **Scheduler RESTARTED (06:38) on the new code** — all 9 jobs registered; so 08:30 premarket (plain
  reason), 08:45 healthcheck (DH-904 fix — below), 09:15 live (ledger engine) all use tonight's work.
- **Dhan token VALID to 2026-07-04 00:30 UTC** (fresh TOTP mint at 06:38) — covers the whole 09:15–15:30
  session, zero mid-session renewals. `/api/auth/status` connected:true.
- **API live**: `/api/portfolio` serves a fresh ₹1,00,000 book locally AND through the tunnel; all prior
  trades are legacy (pre-ledger, pnl_inr NULL) so correctly excluded — the book starts fresh today and
  accrues real ₹ trades from the 09:15 session on.
- **Tunnel `bios-minor-helped-zen.trycloudflare.com`** serves `/healthz` 200 (occasional quick-tunnel
  latency is pre-existing — named tunnel remains the ops upgrade). **Vercel `/portfolio` returns 200**
  (brand-new route → new deploy is live; built from the main checkout with NEXT_PUBLIC_API_BASE repointed).
- Also shipped earlier tonight: **DH-904/429 on the 08:45 healthcheck quote probe is now treated as
  transient** (retry once, else ✅-with-note) instead of the false "health check FAILED" it sent 07-02.

### ⚠️ ONE MORNING SPOT-CHECK (everything else is autonomous)
Open **https://web-beta-beige-60.vercel.app/portfolio** and confirm the ₹1,00,000 money cards render and
data loads. If the tables stay blank (tunnel rotated/flaky), the one-liner fix is
`launchctl kickstart -k gui/$(id -u)/com.vikrant.signal-engine-tunnel` (repoints Vercel + redeploys).
Keep the laptop plugged in. Known pre-existing: `/api/backtest` can 500 on cold recompute (>60s) — unrelated.

---


## 🔄 2026-07-02 overnight session (03:26–05:00 IST) — what was done
Working prompt: `docs/PROMPT_2026-07-02_PREDICTIONS_DASHBOARD.md`. All committed on the branch.

### 1. Ops triage at 03:26 (why the token was dead)
- The Mac was **asleep/off the whole of 07-01** (no scheduler log entries 06-30 14:00 → 07-02 03:18),
  so the token expired 07-01 08:30 UTC and nothing renewed it. The 03:18 wake's startup renew hit a
  transient **"Invalid TOTP"** (single-attempt path). Manually re-minted 03:27 → GREEN.
- **Fix shipped:** `renew_token_job` now retries once in the next 30s TOTP window on that specific
  rejection (test added). The 08:45 healthcheck self-heal uses the same path, so it inherits the retry.
- Token now valid to **2026-07-02 22:57 UTC** — covers the whole session with zero renewals; 06:00/14:00
  crons + 09:15 live_job env-refresh + 08:45 self-heal all layered on top.

### 2. NEW: Predictions log + dashboard page (the night's main feature)
Every alert pushed to Telegram is now persisted at send time and browsable live:
- **`predictions` table** (same SQLite DB): ts (IST), kind (entry/exit/halt/premarket/scan/health/
  advice/error), symbol, direction, strategy, entry/stop/target (+%), R:R, expected move, confidence,
  qty, ₹risk, exit P&L (net % + R + reason), reasons JSON, the exact message text, delivery status, run_id.
- **Writer:** `RecordingAlerter` (signal_engine/alerts/recording.py) wraps the real Telegram alerter in
  `build_alerter()`; call sites attach structured `meta` (runner entries/exits/halts, scheduler
  premarket/scan/healthcheck, CLI). Logging is best-effort — can never block an alert. Console alerter
  stays unwrapped (tests don't pollute the DB); `SE_PREDICTIONS_LOG=1` opts it in.
- **API:** `GET /api/predictions?limit&kind&symbol&since_id` (newest first).
- **Dashboard:** `/predictions` page — live table (5s delta-poll via since_id, dedup-by-id merge),
  kind filter chips, clickable symbols, all parameters. In the Nav.
- **Verified end-to-end in a real browser**: 3 test alerts → Telegram + page, live append <5s without
  reload, filters work. (The 3 smoke rows kinds entry/exit/premarket, strategy `smoke_test`/
  `orb_breakout`, are TEST data from 03:56–04:38 — ignore or delete.)

### 3. Reliability fix found during verification (matters for the ENGINE, not just the page)
A prediction write lost a 5s **"database is locked"** race while the dashboard polled. Root fixes:
- **`PRAGMA journal_mode=WAL`** (+synchronous=NORMAL) set by `SignalRepository.init_db` — persistent,
  DB-level. Readers no longer block writers → protects the live engine's per-bar trade persistence
  from dashboard read pressure (this exposure predated today).
- `fetch_predictions` is read-only (no DDL per poll); missing table → [].

### 4. Latency follow-up audit (agent-run, measured; full report in `docs/LATENCY_FINDINGS_2026-06.md` §2026-07-02)
- `/api/premarket` was the real outlier: re-fetched Yahoo cues + RSS per call, 2.3–3.6s → **~1.5ms**
  on TTL-cache hit (300s, keyed by full params, bounded 32).
- `AuthGate` no longer blanks first paint behind the auth-status round-trip (~120ms warm/~1s cold);
  renders optimistically, gate appears only if truly disconnected.
- Rejected with numbers: gzip middleware (CF edge already gzips), uvicorn workers (would break the
  single-poller _QuoteHub), micro-TTLs on live views. Tunnel hop ~75–120ms warm (named tunnel still
  the recommended manual ops upgrade). Flagged: `/api/backtest` recomputes >60s per request (chip
  spawned to cache/precompute it).

## ✅ VERIFIED STATUS (05:00 IST)
- launchd **tunnel + scheduler RUNNING** (restarted on the new code; caffeinate up).
- **Token valid to 22:57 UTC**; API `/api/auth/status` connected:true.
- Tunnel **`fold-tuition-nursery-linda.trycloudflare.com`** — healthz + `/api/predictions` OK through it.
- Scheduler jobs registered: renew_token_6/14/22, archive_morning, premarket (08:30), healthcheck
  (08:45), live (09:15), scan (15:45), archive (16:10).
- Vercel redeploy with the new tunnel URL was in flight at write time — verify
  https://web-beta-beige-60.vercel.app/predictions loads data (deploy takes ~2–4 min; the launchd
  script auto-repointed NEXT_PUBLIC_API_BASE).
- Full pytest suite green (incl. new predictions + TOTP-retry tests); tsc + next build green.

## ⚠️ THE ONE MANUAL ACTION
**Laptop on BATTERY (35% at 04:55, draining fast).** It will NOT reach 09:15 unplugged. Telegram
alerts sent 03:30 + 03:58. **Plug it in.** Everything else is autonomous.

## What runs AUTOMATICALLY today (IST)
06:00/14:00/22:00 + startup TOTP token renew (now with retry) · 08:00 archive_morning · 08:30
pre-market briefing (→ Telegram → predictions log + page) · 08:45 pre-open healthcheck (self-heals
token, → Telegram) · 09:15–15:30 live paper session (entries/exits → Telegram → predictions page
live) · 15:45 scan (→ Telegram + log) · 16:10 archive.

## How to operate / verify
- Status: `launchctl print gui/$(id -u)/com.vikrant.signal-engine-{tunnel,scheduler} | grep state`
- Restart: `launchctl kickstart -k gui/$(id -u)/com.vikrant.signal-engine-{tunnel|scheduler}`
  (tunnel restart rotates the URL and auto-redeploys Vercel)
- Current tunnel: `grep -oE 'https://[a-z0-9.-]+\.trycloudflare\.com' logs/launchd-engine.out.log | tail -1`
- Predictions: `curl localhost:8000/api/predictions?limit=20` or the dashboard `/predictions`.
- Full ops recipes: `docs/OPS_ENGINE_TUNNEL.md`. Master context: `docs/SESSION_CONTEXT_2026-06.md`.

## Caveats (known, not blockers)
- Local router DNS can't resolve `*.trycloudflare.com` (browser DoH works; else DNS 1.1.1.1).
- Quick-tunnel URL rotates per restart (Vercel auto-repointed). Named tunnel = next ops upgrade.
- Rotate the `sycfyb-…` password pasted in chat earlier (still pending).
- Research state unchanged: no tradeable edge; `vwap_ema_adx` is a gross coin-flip with an ~8bps cost
  leak; gated config fixes remain DEFAULT OFF (need ~30 sessions). See `docs/RESEARCH_HANDOFF.md`.

## For the next session
1. Confirm the 08:30/08:45 Telegram pings arrived and rows appeared on `/predictions`.
2. After 09:15: live entries/exits should stream onto the page; spot-check params vs Telegram text.
3. Optionally delete the 3 smoke rows: `DELETE FROM predictions WHERE strategy IN ('smoke_test','orb_breakout') AND date(ts)='2026-07-02';`
4. Fold today's session into the running gap analysis (pattern in `docs/PAPER_TRADING_ANALYSIS_2026-06.md`).
