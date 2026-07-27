# Morning Handoff — live system ready for the open (updated 2026-07-12 by the P0-implementation session)

## 🟢 2026-07-27 — /paper Trade history filters by session, with that day's net result
The evening-review question ("what did today actually cost me?") needed summing a column by eye.
Trade history now has a **Day** picker listing only sessions that have trades (17 of them, newest
first, each with its trade count), and picking one shows four tiles for that session: net result
(₹ + % of capital), trades closed with W/L, gross P&L, and charges. A trade is booked on the day it
CLOSED, matching the ledger's realized-per-day rule. Client-side over the already-fetched rows, so it
composes with the From/To range filter above and costs no request. Days whose rows predate the ₹1L
ledger carry a caveat naming how many are modelled — those totals mix two bases and are not ledger
figures. Verified against the DB for 2026-07-22: net −₹2,531.47, charges ₹332.39, gross −₹2,199.08,
1W/7L — exact match. Good example of the charge bill on 2026-07-02: gross **+₹554** → net **−₹683**
after **₹1,237** of charges.

## 🟢 2026-07-26 — API CPU BURN ROOT-CAUSED AND CURED (the "selector busy-loop" was our scans)
The spinning workers below were **not** an asyncio selector bug. `/api/leaderboard`, `/api/premarket`
and `/api/backtest` ran their scans inside the API process: measured **74 s** for a cold corpus scan
and **623 s** for `/api/backtest?days=10` on the 40-name watchlist. One such CPU-bound thread is enough
to starve every DB-backed endpoint — reproduced 2026-07-26: `/healthz` answered in 0.5 s while
`/api/portfolio` timed out past **60 s**, exactly the reported symptom. The startup pre-warm ran the
same 74 s scan in-process, which is why a *freshly started* worker also spun. And because uvicorn's
graceful shutdown waits for in-flight requests, a multi-minute scan meant the supervisor's SIGTERM
never landed — the orphan survived, the next launchd start added another worker, and three
accumulated.
- **Fix (`signal_engine/api/scans.py` + `api/app.py`)**: the three scans now run in a dedicated
  **subprocess** (one worker), behind a TTL cache that is **single-flight** (two tabs on the same cold
  key no longer run the scan twice — that was real, observed as duplicate scan log lines at 150% CPU)
  and **serves the stale payload while a refresh runs**. Only a caller with nothing cached waits, and
  only for 20 s, then gets a 503 + `Retry-After` instead of holding a connection past the Cloudflare
  quick tunnel's own ~100 s ceiling. TTLs: leaderboard 15 min, pre-market 5 min, backtest 6 h.
- **Fix (`run-with-tunnel.sh`)**: `stop_children()` on every exit path escalates SIGTERM → SIGKILL
  after 5 s, and `reap_port()` clears whatever still owns `:8000` at startup — including a wedged
  worker that already dropped its listener socket. Scoped to the port, so a `cli serve` on another
  port is left alone.
- **Verified live**: exactly one `cli serve`; with a full scan at 100% CPU in the child, the API
  worker sits at 0.1% and `/api/portfolio` answers in **11–23 ms** (was: timeout). Three concurrent
  `/api/leaderboard` calls returned in 14–21 ms with **zero** extra scans.
- **Follow-up 2026-07-27 — scan lanes split.** The single shared worker above meant a 623 s backtest
  starved the read path: every cold `/api/leaderboard`, `/api/premarket` and `/api/movers` key queued
  behind it and 503'd for the whole run. There are now **two lanes, one worker each** (`_SCAN_POOLS`
  in `api/app.py`) — read-path scans on `"scan"`, `/api/backtest` on `"backtest"`. Each lane's worker
  spawns on first use, so the backtest worker exists only once someone runs one, and an idle worker
  costs 0% CPU. Verified: with a backtest pinned at 100% in its own child, `/api/premarket` still
  answered **200 in 5.9 s** and a warm `/api/leaderboard` in **2 ms**. So expect **one or two**
  `multiprocessing.spawn` children at 100%, not just one.
- **Follow-up 2026-07-27 — backtest day-count capped at 30** (was 120), with a matching `max` on the
  dashboard's Days box (`web/app/backtest/page.tsx`). A session-day costs ~62 s of one core, so a
  stray extra zero in that box used to buy a ~2 h burn with no feedback; 30 caps an accidental run
  at ~30 min. Raise both together if a longer window is ever wanted.

## 🟢 2026-07-26 — NIGHTLY QUANT-DESK REVIEW AGENT ("the desk") built + scheduled
Full design: `docs/DESK_AGENT.md`. Code `signal_engine/desk/`, tests `tests/test_desk.py`, new job
**`desk_review` @ 17:15 IST mon-fri (14 scheduler jobs now)**, `GET /api/desk`, reports in
`docs/desk/DESK_REVIEW_<day>.md`, tables `desk_hypotheses`/`desk_proposals`/`desk_gap`/`desk_reviews`.
- **PROPOSALS ONLY — the desk never applies a config or strategy change.** Enforced structurally:
  `applied` is hard-coded 0 on insert with no method to flip it, the package contains no YAML write
  / `os.environ[]=` / `setattr(cfg)`, `risk.max_cost_r` + `live_universe.restrict_to_allowlist` +
  the movers labels are ring-fenced, and a test scans the source to prove it.
- **Two clocks:** nightly = analysis + hypotheses + written diffs (deterministic, no LLM, cannot
  break the scheduler); gated = a HUMAN applies changes only after a pre-registered evidence bar
  (P0 precedent). LLM layer is opt-in (`SE_DESK_LLM=1`), one structured call, ₹ cost meter.
- **Session-integrity guard first:** a session with <500 bars is `DEAD` and the review REFUSES to
  conclude — the attribution/forensics/bucket/gap sections are omitted, not emptied. Verified on
  2026-07-23 (0 bars, 1,426 reconnects, cause: **Dhan Data-API subscription showing NOT SUBSCRIBED**
  at the 08:45 healthcheck — worth checking the Dhan portal).
- **First-night findings (2026-07-22, the last session with trades)** — all mechanical, not fitted:
  book **−2.59%** (₹97,824→₹95,293) on 8 trades; gross −3.77% = tide +0.20% + **alpha −3.98%**;
  friction 0.66%. Largest position **85% of the book**, mean 42%, effective breadth ~2.4 names, and
  **18 affordability skips** → trade selection was decided by CASH EXHAUSTION, not signal quality
  (`max_concurrent_positions: 4` never bound). Cause is arithmetic: `risk_per_trade_pct 0.5% ÷
  P0 stop ≥0.57% ≈ 88% of equity per position`. The **daily drawdown breaker halted at 13:25 on a
  size-blind number** (−4.43% summed per-trade vs the book's real −2.59% against a 4.00% cap).
- **Gap to +1.00%/day (gated scope: 8 sessions, 41 trades): needs a 71% win rate at the realised
  1.33:1 payoff and 5.1 trades/day; realised is 27%. Gap 44pp — NOT closable by parameter work.**
  Friction alone eats 22% of a 1% day; all-in friction is 0.216R/trade at the median 0.66% stop.
- Top proposals written (NOT applied, each with a pre-registered bar): (1) gated
  `risk.max_position_pct_of_equity` default 0/OFF; (2) make `_LossBreaker` equity-weighted behind
  `risk.drawdown_uses_book_equity` default false; (3) persist gate rejections in
  `risk/manager.py` so "did the gates reject winners?" becomes answerable at all.
- Suite: **663 passed** in 8m30s (627 collected before this change; the 625 figure in the
  build prompt was 2 behind HEAD). **+36 tests** in `tests/test_desk.py`. Scheduler
  restarted off-hours (Sun 19:22 IST) — 14 jobs confirmed in `logs/launchd-scheduler.err.log`.

## 🟢 2026-07-26 — DASHBOARD FULLY REDESIGNED + 2 spinning cores reclaimed
**UI redesign shipped & deployed** (`docs/UI_DESIGN_SYSTEM.md`): `app/globals.css` 751→1,757 lines,
**1 → 16 media queries** (20 mobile-first `min-width`) — the dashboard was effectively desktop-only
and is now mobile-first; **light + dark** via `prefers-color-scheme` + a persisted manual toggle with
no first-paint flash. New shared layer `web/components/ui/` (DataTable with a 375px card-stack
fallback, StatTile, Callout, Badge, charts…) and `web/lib/format.ts` as the single number/date
formatter (Indian grouping). All 9 pages redesigned; `tsc --noEmit` clean; deployed and verified.
**Honesty labels survived and are MORE prominent** — verified on the live site: nav brand reads
"Paper money · no live orders" on every page; `/movers` leads with a three-caveat callout and splits
tradeable vs "Cannot fill at circuit"; "rule score (uncalibrated)" intact; losing figures render red
with ▼ (colour-blind safe). Verified 375px: `scrollWidth == clientWidth == 375`, `overflow-x: visible`
(not masked). NOT done: the `/desk` page for `GET /api/desk` (stretch, deferred).

## ⚠️ 2026-07-26 — API workers were burning 3 CPU cores (2 reclaimed; root cause filed)
Found **three** `signal_engine.cli serve` workers alive at once, **each pinned at ~100% CPU**
(70/63/60 min accumulated), load average 5.3 — on the laptop that must run Monday's live session on
battery. The two orphans had **zero open sockets** (not even a listener) yet still spun: wedged
asyncio event loops that **ignore SIGTERM** (the loop never runs Python's signal handler) and need
`kill -9`. Killed them. **A freshly started worker also spins immediately** (34s CPU in ~30s of life)
and `sample` shows only `select`/`kevent` frames — no application code — i.e. an event-loop selector
busy-loop, not our logic. **Mitigated, not cured**: `run-with-tunnel.sh` step "0. Reap stale API
workers" now `kill -9`s any pre-existing serve worker at startup, so they can never accumulate again.
The remaining single-worker spin is filed as a background task; it does NOT affect trading (the live
engine runs in the scheduler process, separately) but it does drain battery.
> **Superseded 2026-07-26 (later the same evening) — see the entry at the top of this file.** The
> "event-loop selector busy-loop" reading was wrong: `sample` showed only `select`/`kevent` because it
> caught the *main* thread while the CPU work sat on a Starlette threadpool worker. The spin was our
> own scan code (74 s leaderboard, 623 s backtest) running inside the API process, including from the
> startup pre-warm. Scans now run in a subprocess; root cause cured, not mitigated.

## 🔴 2026-07-23 INCIDENT — WS RECONNECT STORM got our Dhan client IP-BLOCKED (fixed 07-26)
The 07-23 live session started at 09:15, ran to 15:30, and produced **0 bars / 0 trades** while
logging **4,440 dhan_ws lines**: 1,426 × "subscribed" immediately followed by 1,426 × "stream error,
reconnecting: Connection to remote host was lost", ending in **HTTP 429 "Too many requests from this
IP hence client id is blocked"**. Dhan accepted the socket then dropped it instantly (stale token /
duplicate session), and our reconnect loop hammered it until the vendor blocked the client id.
**Root cause was ours**: `run_feed` did `attempts = 0` right after a successful *subscribe*, so a
connect-then-instantly-die cycle never escalated its backoff — it retried every ~2s for hours.
(Introduced/exacerbated by the 07-13 `reconnect_on_close=True` hardening.)
**FIX (2026-07-26, `brokers/dhan_ws.py`)**: (1) a connection only counts as healthy — and only then
resets the counter — once it has been PRODUCTIVE, i.e. survived `min_productive_s` (default 30s), so
instant-drop loops escalate; (2) **429-aware backoff**: a rate-limit response waits minutes
(`rate_limit_backoff_s` 60s/attempt, capped 600s) instead of seconds, because retrying gently keeps
the block alive. Tests: `test_instant_drop_loop_escalates_backoff_not_storm`,
`test_rate_limit_429_backs_off_hard`. **Verified 07-26: WS handshake now succeeds — client is no
longer blocked.** If a session ever logs 0 bars again, grep dhan_ws for "stream error" counts first.

## 🟡 2026-07-24 session MISSED — machine off/asleep from 07-23 evening to 07-26 17:39
No logs at all between 2026-07-23 16:40 and 2026-07-26 17:39, so Friday 07-24 never ran (07-25/26 =
weekend). Consequence: no movers predictions existed for Monday 07-27 (the 07-23 job had predicted
for 07-24) — **regenerated manually on 07-26; 20 predictions / 10 fillable are queued for 07-27**.
The 20 unresolved 07-24 predictions will be swept automatically by the self-healing `resolve_pending`
on the next movers run. Standing lesson: **the laptop being asleep is still the #1 cause of missed
sessions** — plugged in + lid open is the whole requirement.

## 🟢 2026-07-20 — movers tracking made GAPLESS + first real verdict
**Bug found & fixed:** `resolve_predictions` only ever ran for its OWN day, and the nightly archive
misses thin names (AARTECH, MAZDA, PPAP, DBSTOCKBRO, KAUSHALYA — 0 bars). So 12 predictions across
07-16/17/20 were stuck **unscored forever**, silently biasing the scoreboard. Fixes in
`movers/sleeve.py`: (1) `_yahoo_daily_open_close` FALLBACK price source when the archive lacks a
symbol; (2) `resolve_pending()` — a SELF-HEALING sweep that re-tries every unresolved day (30d
lookback), now wired into `movers_job`. Backlog swept: 12/12 resolved, tracking is 80/80 gapless.
**The bias mattered**: the un-archived thin names were disproportionately HITTERS, so the true hit
rate rose 30.9% -> 40.0% once they were scored.

**FIRST HONEST VERDICT from the movers sleeve (80 resolved predictions):**
- **Magnitude prediction WORKS**: 40.0% hit rate on ±5%+ moves vs the 4.9% base rate = **8.2x lift**.
- **Direction does NOT**: 33.3% correct on 18 directional calls — **worse than a coin flip**.
- **The tradeable LONG legs LOSE**: 31 legs, **avg −2.09%/leg**, −64.77% summed (net of costs).
This is exactly the SPIKE_HUNTER thesis confirmed live: you can predict THAT a name will move, not
WHICH WAY, and buying after the spike is negative-EV. The sleeve is doing its job — telling us the
truth. Keep it measure-only; do NOT allocate capital to the long legs.

## 🔴 2026-07-17 INCIDENT — transient morning DNS killed the 09:15 live start (fixed)
Symptom: user got the 08:30 premarket + 08:50 movers alerts, but NO live-trading notifs. Cause: a
TRANSIENT DNS/network blip that morning (laptop just-woken / WiFi switch off the hotspot to
192.168.29.x) — the 06:00 renew, 08:00 archive, 08:45 healthcheck AND **09:15 live_job** all threw
`nodename nor servname provided` (errno 8). Since cron jobs don't retry, the live session never
started and did not auto-recover. (Telegram hosts resolved fine, so premarket/movers alerts still sent.)
- **RECOVERY (manual, ~13:55): relaunched** `scripts/live_ipv4.py live --persist` + watchdog once DNS
  was back + token valid — warm-start re-derived the 3 morning trades (silently; warm-start suppresses
  alerts by design), live alerts resumed for the 14:00–15:30 tail.
- **DURABLE FIX**: `scheduler.live_job` now RETRIES build_broker with backoff (~15 min) so a morning
  network hiccup self-heals, and if it still can't start it **sends a Telegram "live session could NOT
  start" warning** instead of dying silently. Scheduler restarted with the fix. So next time: it either
  recovers on its own, or you get told.
- **Recovery runbook** (any day the 09:15 feed didn't start and DNS is back): `PYTHONUNBUFFERED=1 nohup
  .venv/bin/python scripts/live_ipv4.py live --persist > logs/manual_live_$(date +%F).log 2>&1 &` then
  `nohup ./scripts/live_watchdog.sh &`.

## 🟢 Movers alert moved to PRE-OPEN (08:50) — 2026-07-16
The BIG-MOVERS WATCH Telegram alert now fires at **08:50** (after the 08:45 healthcheck, ~25 min
before the 09:15 open) instead of 16:40 the prior evening — so it lands when it is actionable.
Split: `movers_job` @16:40 still COMPUTES + persists predictions (needs the 16:10 archive) but is
now SILENT (no Telegram); new `movers_alert_job` @08:50 reads today's persisted predictions from the
DB and sends them. 13 scheduler jobs now. The 15:50 `microstructure_score` job is and stays SILENT
(internal scoring, no Telegram — a common point of confusion). Telegram-sending jobs: premarket 08:30,
healthcheck 08:45, movers_alert 08:50, scan 15:45. (movers/alpha/archive/microstructure_score = silent.)

## 🟢 2026-07-15 (evening) — movers direction fix, fillability-first, + microstructure shadow signal
- **Movers #1 (direction) + #2 (fillability) SHIPPED & DEPLOYED** (see memory movers-research-sleeve):
  signed buckets (HARDWYN now "dn10+ bounce" not blind LONG), tail-split display, fillable-first ranking,
  /movers page split Tradeable vs Research-only. Web deployed + browser-verified. Scheduler restarted
  19:52 (loads new movers code + new job) — token re-minted, all 12 jobs registered.
- **NEW microstructure shadow signal** (`signal_engine/microstructure/`): per-1-min-bar order-book
  imbalance (FULL mode) + tick-rule volume-delta/CVD (QUOTE mode) → dir_score, logged live to its own
  `microstructure_signals` table, scored vs next-bar direction (hit-rate vs 50%). NEVER gates a trade.
  Runs automatically tomorrow (SE_MICROSTRUCTURE_SHADOW=1, quote mode → CVD half active; flip
  SE_DHAN_FEED_MODE=full to add order-book imbalance). New scheduler job `microstructure_score` @15:50;
  GET /api/microstructure; `python -m signal_engine.microstructure.scorer`. tests/test_microstructure.py.
- **Volume Q answered**: intraday vwap_ema_adx uses volume for VWAP+RVOL (0.15 wt) but NOT direction;
  movers direction used zero volume. This microstructure feature is the first VOLUME/ORDER-FLOW
  directional read — measured before it's ever trusted.

## 🟢 2026-07-14 — TELEGRAM NEWS INGESTION + an ops lesson
- **Telegram channel news is LIVE** (`signal_engine/news/telegram_channel.py`): public-channel
  preview scraping via telegram.me (t.me is ISP-DNS-blocked — NXDOMAIN even on DoH; telegram.me
  is the same service and works; pinned-IP fallback included). Wired into the news pipeline via
  `SE_NEWS_TELEGRAM_CHANNELS` (.env; currently `moneycontrolcom`, live-verified 20 msgs) through
  a CompositeNewsProvider alongside RSS. Movers picks now carry `tg_mentions` (last-24h tip-channel
  mentions) as a MEASURED shadow feature — displayed 📣 on /movers + in the 16:40 alert, never a
  probability adjustment until mention→outcome hit rates prove signal. The user's own channel
  ("STARBHAI NEWS/6", QR) could not be resolved to a handle (private or unknown) — awaiting the
  exact t.me link; if PRIVATE, preview scraping cannot read it (needs a user-session login, not built).
- ⚠️ **OPS LESSON (self-inflicted today): NEVER `launchctl kickstart` the scheduler during market
  hours** — it kills the in-flight live_job thread and the live cron won't re-fire until tomorrow
  (misfire grace 1h). Recovery (proven twice now): `PYTHONUNBUFFERED=1 nohup .venv/bin/python
  scripts/live_ipv4.py live --persist > logs/manual_live_$(date +%F).log 2>&1 &` then
  `./scripts/live_watchdog.sh &` (now date-generic). Warm-start re-derives the day; today's
  12:55 kill was fully recovered by 13:00 with the open position re-tracked.

## 🔴 2026-07-13 — ROOT CAUSE of the recurring "scheduler alive but jobs never fire" outage: BROKEN IPv6
The Jul-09/10 misses and today's dead scheduler were NOT (only) laptop sleep. Real cause: the machine is
on an **iPhone Personal Hotspot (172.20.10.x) with dead IPv6 routing**. Dhan's dual-stack hosts publish
AAAA records, Python tries IPv6 first, and every connection hangs in **SYN_SENT** — freezing token-renew,
the scheduler startup, and the live feed before they log a single line. (Diagnosis: `lsof -a -p <pid> -iTCP`
showed SYN_SENT to a `2600:9000::/…:443` CloudFront v6 addr; `api.dhan.co` is v4-only and connects fine.)
**FIX (durable):** `signal_engine/net.py::prefer_ipv4()` filters getaddrinfo to IPv4 when any A record
exists (falls back to v6-only hosts); called at the top of `cli.main()`, so the scheduler + live + all CLI
services get it. Safe no-op on healthy networks; disable with `SE_PREFER_IPV4=0`. Scheduler restarted with
the fix and boots cleanly. **If the feed ever hangs again, first suspect the network, not the code.**
- **Today's recovery (manual):** because 09:15 was already past when the fix landed, today's live session
  runs as a MANUAL process — `.venv/bin/python scripts/live_ipv4.py live --persist` — kept alive by
  `scripts/live_watchdog.sh` (relaunches if it dies, stops 15:31). The launchd scheduler was restored
  (fresh cron => won't double-fire live today; handles EOD scan/archive/alpha + tomorrow's 09:15).
  From tomorrow the scheduler runs live normally (no hang).
- **P0 gate CONFIRMED LIVE:** first 3 surfaced entries all have stops >= 0.57% (0.61/0.73/0.90%) vs the
  pre-gate min 0.33%. Affordability skips (book full at ₹3.22 after 3 sized positions) are normal, not gate rejects.

## 🟢 2026-07-12 session — STRATEGY_IMPROVEMENT_PLAN_2026-07 IMPLEMENTED (P0 gate LIVE from next session)
Full detail: `docs/STRATEGY_IMPROVEMENT_PLAN_2026-07.md` §5. Suite: 603 tests green. Next trading
session (Mon 2026-07-13) runs with:
- **P0 friction-in-R gate ON** (`risk.max_cost_r: 0.25` in config/risk.yaml): rejects any plan whose
  round-trip friction (charges+slippage, now correctly slippage-inclusive after the V3 regression fix
  in `engine/runner.py` — gates were pricing 8.2bps while fills pay ~14.2bps) exceeds a quarter of 1R.
  Effective floor: stop ≥ ~0.57%. Expect ~5-6 trades/day (was 15, cap-bound) and ~₹180/day charges (was ~₹500).
- **Alerts say "rule score N (uncalibrated)"** instead of "conf N" (P2; dashboard label + glossary changed
  too — needs a Vercel deploy of web/ to show there).
- **Every closed trade now persists** `pnl_pct_gross`/`cost_pct` (P3.1, all 136 historical rows backfilled
  exactly from fills; identity gross−cost=net holds on all) **and** `nifty_ret_pct`/`alpha_pct` (P3.2,
  backfilled 136/136; new 16:20 IST `alpha_job` resolves each day's trades vs ^NSEI, self-healing).
- **Book decomposition (all 136 trades): gross +6.43% = tide +2.69% + alpha +3.74%; cost 11.20% → net −4.77%.**
  Picks are microscopically positive; friction ate them 3×. P4 replay: trailing exits WORSE, hold-to-90m
  better only on the tight-stop trades P0 removes — **keep current exits** (plan doc §5).
- **ONE committed look:** `.venv/bin/python scripts/preregistered_eval.py` after 10 gated sessions
  (~session 18). Bars frozen: cost/|Σgross| <40%, avgR >−0.05, net ≥0. No peeking-and-tweaking between.
- ⚠️ **Ops gap found during verification: Jul-09 and Jul-10 sessions NEVER RAN** — the scheduler process
  was alive but no job fired after its Jul-08 16:25 boot (machine asleep; caffeinate can't stop lid-closed
  battery sleep). Scheduler was kickstarted fresh 2026-07-12 00:47 with all 10 jobs (incl. new `alpha`)
  + fresh Dhan token. **Keep the Mac plugged in + lid open for Monday.**

---


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
Keep the laptop plugged in. `/api/backtest` is cached for 6h now; the very first call after a restart
returns 503 "still computing" for a few minutes while the scan subprocess runs — reload and it's there.

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
  (08:45), live (09:15), scan (15:45), archive (16:10), alpha (16:20, added 2026-07-12),
  movers (16:40, added 2026-07-13 — big-movers research sleeve, /movers page).
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
