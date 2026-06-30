# Session Context — full record of work done (2026-06, NSE signal engine)

Master index of everything accomplished in this work arc. Repo: `/Users/vikrantdeshmukh/Personal
projects`, branch `feat/full-nse-realtime-pipeline`, Python `.venv/bin/python`. Paper/research only —
the engine never places real orders. For the operational "is it running / what fires today" view, read
**`docs/MORNING_HANDOFF.md`** first; this doc is the broader recap with pointers to every detail.

---
## 1. Alpha research — FINAL VERDICT: no cleanly-tradeable edge found (and we know exactly why)
Full arc + caveats: `docs/RESEARCH_HANDOFF.md`, `docs/PEAD_FINDINGS.md`, `docs/PEAD_SPREAD_FINDINGS.md`.
Modules: `signal_engine/research/{pead_spread,pead_robustness,overfitting}.py`.
- Intraday/swing OHLCV, low-vol/momentum, overnight drift: all NO cost-surviving edge (settled earlier).
- **PEAD / earnings drift** (this session's focus, Option C): built the market-neutral spread + SUE-with-jump.
  Decisive finding — **the EPS surprise is inert; the announcement-day price JUMP is the real signal**
  (earnings-specific, not generic momentum; full-universe WR 52.5% / PF 1.18 / t≈2.9 net of futures cost).
- **But it does NOT clear as a tradeable edge, proven 3 independent ways:** (a) leg-decomposition — the alpha
  is the SHORT leg of mid-caps; (b) F&O-restriction — the shortable subset fails the gate; (c) **Deflated
  Sharpe + multiple-testing (Bailey-LdP 2014, Harvey-Liu-Zhu 2016)** — every tradeable variant collapses
  (DSR 0.01–0.42) and the headline t=2.91 fails the Bonferroni bar; only the un-shortable mid-cap variant
  survives. Alpha lives where India's short-sale constraints wall it off — the textbook reason it persists.
- **Status: the most real signal of the arc, but PEAD/jump-drift is honestly EXHAUSTED.** No paper sleeve.

## 2. Deep research (web, cited, adversarially verified)
- **Open-source tools + literature** (`memory: research-tools-and-literature-2026-06`): validated jump-drift
  = EAR (Brandt et al., JF 2008) > SUE; drift concentrates in hard-to-short small caps; costs eat 70–100%
  (Chordia et al., FAJ 2009). Named our one harness gap → multiple-testing/Deflated-Sharpe (now built).
  Tool disciplines to borrow: Qlib (Alpha158/360, seed-variance), ML4T overfitting guards.
- **Option B feasibility + ROI** (`memory: option-b-feasibility-2026-06`): @ ₹1–10L capital / <₹50k-yr —
  build survivorship-clean DATA free (self-build; high research ROI), but do NOT fund short-side execution
  (only ~38 SLB-borrowable names, SSF lots > book, costs eat it). Phase 0 = bhavcopy-delisted feasibility spike.

## 3. Live-system OPS — made robust + self-healing
Detail: `docs/OPS_ENGINE_TUNNEL.md`; memory `dashboard-tunnel-keepalive`.
- Root-caused "dashboard down / no Dhan reset" → the engine had no keep-alive and silently died.
- **launchd agents** `com.vikrant.signal-engine-tunnel` (API + Cloudflare quick tunnel + Vercel redeploy) and
  `com.vikrant.signal-engine-scheduler` (trading jobs, runs under `caffeinate -i -s` so the Mac stays awake).
  Both RunAtLoad + KeepAlive → self-restart on sleep/crash/logout.
- **Dhan auth is now PERMANENT via TOTP auto-login** (no daily OTP): `dhan_auth.generate_token_via_totp`
  mints from `DHAN_TOTP_SECRET`+`DHAN_PIN` (.env); scheduler renews 06:00/14:00/22:00 + startup. Static-IP
  setting does NOT help (order-API only); RenewToken/DH-905 path dead for consent tokens. Verified working.
- **08:45 pre-open healthcheck** (`scheduler.healthcheck_job`): verifies token+feed, self-heals via TOTP,
  Telegram ✅/⚠️ before the 09:15 open.

## 4. Dashboard LIVE features (deployed; verified in Chrome)
- **Watchlist**: live LTP "Live ₹" column + "Trend" sparklines + ●LIVE indicator, streaming ~1s over
  `/ws/quotes` (WebSocket); auto-reconnect; falls back to 15s poll.
- **Stock pages** `/stock/<SYM>`: always-on live price line graph (`/ws/quotes?symbols=`), auto-starts;
  removed the old synthetic "Go live" replay; historical candles remain below.
- Backend: `signal_engine/api/app.py` `/ws/quotes` (Dhan REST LTP batch on a thread). Frontend in `web/`.

## 5. Live paper-trading analysis (gap analysis)
`docs/PAPER_TRADING_ANALYSIS_2026-06.md`; memory `live-strategy-paper-analysis`. Strategy `vwap_ema_adx` is
a GROSS coin-flip; the ~8 bps/trade cost tax is the leak. 3-session gap analysis: **no at-entry feature
separates winners from losers** (same stock wins & loses under identical rules); cohort/time/direction
splits are one-session noise. Only the **frequency cap** fix is justified — implemented but **config-gated,
DEFAULT OFF** (`config/risk.yaml` max_entries_per_minute / _per_symbol_per_day; `config/settings.yaml`
live_universe.restrict_to_allowlist). Need ~30 sessions before enabling anything.

## 6. Autonomy set up for going forward
- launchd + TOTP + caffeinate + 08:45 healthcheck = the system trades + self-verifies + Telegram-alerts
  with zero human involvement (given AC power).
- Scheduled one-time Claude session **`nse-engine-morning-verify`** (08:50 IST, `~/.claude/scheduled-tasks/`)
  to verify/fix/watch/analyze — runs when the Claude app is open (bonus layer on top of the deterministic one).

## 7. Commit trail (this arc)
`52e81ed` jump-drift spread · `5aa2fde` Deflated-Sharpe/multiple-testing · `b10ca50` launchd keep-alive ·
`fb48f4b` paper analysis + gated freq/universe caps · `56c066c` caffeinate + premarket grace ·
`d49136a` 3-session gap analysis (universe gate NOT justified) · `7952dd2` TOTP auto-login ·
`466d399` live watchlist (WS) · `27a070a` stock live graph · `3a2b426` 08:45 healthcheck + morning handoff.

## 8. Where everything lives
- Ops/today: `docs/MORNING_HANDOFF.md`, `docs/OPS_ENGINE_TUNNEL.md`.
- Research: `docs/RESEARCH_HANDOFF.md`, `docs/PEAD_SPREAD_FINDINGS.md`, `docs/PEAD_FINDINGS.md`,
  `docs/PAPER_TRADING_ANALYSIS_2026-06.md`, `docs/{SIGNAL,SWING,EDGE_EXPERIMENTS}_*FINDINGS.md`, `docs/EDGE_ROADMAP.md`.
- Memory (auto-recalled): `MEMORY.md` index → `edge-research-arc-and-next-step`, `research-tools-and-
  literature-2026-06`, `option-b-feasibility-2026-06`, `live-strategy-paper-analysis`,
  `dashboard-tunnel-keepalive`, `dhan-data-api-live-verified`, `nse-signal-engine-project-state`.

## 9. Current state + the one open action
Everything verified GREEN. **Only manual action: keep the laptop OPEN + PLUGGED IN** (it was on battery;
caffeinate stops idle-sleep but the battery still drains). Also rotate the password pasted in chat earlier.
Next session: 2026-06-30 09:15 IST — fully automated.
