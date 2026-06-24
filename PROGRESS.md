# PROGRESS

Implementation tracker for the Intraday Signal Engine (see PLAN.md).
Statuses: `todo` · `in-progress` · `done` · `blocked`.

---

## Deliberate divergences from PLAN.md (local MVP)
Recorded per the "explain, don't silently diverge" rule. Production targets preserved behind interfaces.

| Plan choice | MVP choice | Why | Revisit |
|---|---|---|---|
| Postgres | **SQLite** (behind repo interface) | No DB server needed for local/dev/test | Phase 2+ |
| Redis live state | **In-memory store** (behind interface) | Single-process MVP | Phase 2 (sharding/scale) |
| Polars | **pandas/numpy** | Small Phase-1 watchlist; simpler, ubiquitous | Phase 2 scan lane |
| pandas-ta / TA-Lib | **hand-rolled indicators** | Determinism, hand-verifiable tests, no native deps | optional |
| Python 3.12 | **3.9** (system interpreter) | Only interpreter available; code kept 3.9-compatible | when 3.12 available |
| Dhan SDK live feed | **MockBroker + synthetic data** | No live-market dependency; ground-rule safety | Phase 1+ (data-only Dhan, gated) |

**Safety:** no live order execution implemented anywhere. `BrokerAdapter` is market-data only; `SE_ALLOW_LIVE_ORDERS` is an explicit, audited, off-by-default no-op flag.

---

## Phase 0 — Foundations & data spine  ✅ DONE
- [x] `done` Repo scaffold (git, .gitignore, pyproject, requirements, .env.example)
- [x] `done` Config: YAML (settings.yaml, risk.yaml) + pydantic loader (`config.py`)
- [x] `done` Domain contracts: enums + models (`domain/`)
- [x] `done` Market calendar + clock + session state machine (`market/`)
- [x] `done` `BrokerAdapter` interface (data-only, no live orders) (`brokers/base.py`)
- [x] `done` `Alerter` interface + ConsoleAlerter + Telegram fallback (`alerts/`)
- [x] `done` `Strategy` interface + registry (`strategies/base.py`)
- [x] `done` MockBroker (synthetic replay) + synthetic data generator (`brokers/mock.py`, `data/`)
- [x] `done` Bar aggregator (tick -> 1m closed bars, roll-ups) (`ingestion/`)
- [x] `done` Parquet bar store + SQLite repository (`storage/`)
- [x] `done` Tests: calendar/session (8), aggregator (3)
- News/global-cues archive: **deferred to Phases 4–5** (not needed for MVP signal loop)

## Phase 1 — MVP: live -> trade plans -> paper-trade -> alert  ✅ DONE
- [x] `done` First strategy `vwap_ema_adx` (consumes feature-key contract)
- [x] `done` Indicator engine (VWAP, EMA, RSI, ADX, ATR, RVOL, ORB, MACD, Supertrend) + `compute_features`
- [x] `done` Risk layer: %-based CostModel, RiskManager (stop/target, R:R floor, edge-after-cost gate), size calculator
- [x] `done` Live paper-trader (entry trigger, stop/target/time-stop/square-off; net-of-cost P&L)
- [x] `done` Engine runner (feed -> aggregator -> features -> strategy -> risk -> plan -> paper-trader -> alerts)
- [x] `done` CLI entrypoint (`replay`, `info`)
- [x] `done` Minimal Streamlit dashboard (leaderboard + picks + paper P&L + per-stock chart)
- [x] `done` Tests: indicators (10), costs (7), risk (11, hand-verified), paper-trader (7), strategy (5), aggregator (3), calendar (5), end-to-end (6) — **54 total, green**
- [x] `done` README (setup, run, test, backtest note)

**Phase-gate check (end of Phase 1):** `pytest` 54 passed · `ruff check` clean · `signal-engine replay --demo` runs end-to-end and surfaces picks + paper trades. ✅

## Phase 2 — Full-universe scan + "best stocks" leaderboard  ✅ DONE
Scope agreed with user: synthetic ~2,000-symbol universe (no live feed); shard-manager
interface + mock driver (real Dhan wiring deferred/gated); Redis optional (in-memory
StateStore interface for now). Polars vectorization deferred as a perf optimization —
correctness-first with pandas, structured so a Polars backend can replace the inner loop.
- [x] `done` Universe: `InstrumentMeta` + `UniverseProvider` + mock ~2,000-symbol generator (8 tests)
- [x] `done` Liquidity + %cost filter (ban/penny/turnover/spread + cost-viability) (§4.0) (11 tests)
- [x] `done` WebSocket shard manager interface + mock driver (partition, health, reconnect) (§3.3) (12 tests)
- [x] `done` Ranking score (confidence × R:R × liquidity × catalyst, cost-penalized) (§4.9) (6 tests)
- [x] `done` Scanner: universe → features → filter → strategy → risk → ranked Top-N leaderboard (6 tests)
- [x] `done` StateStore interface + in-memory impl (Redis deferred)
- [x] `done` CLI `scan` command + dashboard leaderboard view
- [x] `done` Harness: static pre-screen → generate history for survivors only → scan (fast: ~0.4s for 2000)
- **Phase-gate check:** `pytest` 97 passed · `ruff` clean · `signal-engine scan --universe 2000` runs in ~0.4s,
  yields a ranked leaderboard. ✅
- Polars vectorization + real Redis + real Dhan sharding: **deferred** (perf/live infra) — interfaces in place.

## Phase 3 — Backtesting + Strategy Health Scorer  ✅ DONE
Event-driven multi-day backtest reusing the SAME engine core (anti-lookahead by
construction), full metrics suite (§6.2), walk-forward splits (§6.4), and the rolling
Strategy Health Scorer with degradation alerts (§6.6, A10). Synthetic multi-day data.
- [x] `done` Metrics suite (win rate, PF, expectancy, max DD, Sharpe, Sortino, equity) (§6.2) (6 tests, independently re-verified)
- [x] `done` Strategy Health Scorer (composite + Brier calibration + drift + degradation) (§6.6) (13 tests)
- [x] `done` Walk-forward splitter (time split + rolling windows) (§6.4) (13 tests)
- [x] `done` Backtest engine (multi-day replay via shared EngineRunner core -> ledger -> metrics + health) (4 tests)
- [x] `done` Multi-day data via per-day MockBroker (regime rotation) — reuses the live core
- [x] `done` CLI `backtest` + `health` commands; dashboard Backtest+Health view
- [x] `done` Wire health degradation -> Alerter (the `health` command fires an alert if below threshold)
- **Phase-gate check:** `pytest` 133 passed · `ruff` clean · `backtest`/`health` run end-to-end. ✅
- Note: health scorer already flags real issues — e.g. calibration component drops when the
  rules-confidence is overconfident vs the actual hit rate (working as designed, §6.6).

## Phase 4 — News & sentiment integration  ✅ DONE
Full news pipeline (ingest → symbol-map → sentiment + event → point-in-time features) wired
into the rules engine as gate/boost/cap/veto + event-guard, surfaced in the leaderboard "why".
- [x] `done` News domain models: `NewsItem` + `EventType` + frozen feature-key contract
- [x] `done` Sentiment + event classifier: `LexiconSentiment` (default) + `EventClassifier` + FinBERT stub (15 tests)
- [x] `done` Symbol mapper (ticker/alias dictionary matching; NER deferred) (11 tests)
- [x] `done` News feature engine (latest/decayed sentiment, count, volume spike, time-since, event flags) — **point-in-time** (13 tests)
- [x] `done` News providers: `MockNewsProvider` (synthetic) + `RSSNewsProvider` stub (gated)
- [x] `done` `NewsOverlay` rules (gate/boost/cap/veto + event-guard) (8 tests)
- [x] `done` Wired into Scanner + harness (per-symbol point-in-time news features + overlay); `news_vetoed` stat
- [x] `done` CLI: `scan --no-news` toggle + `news` preview command; dashboard leaderboard shows news in "why"
- [x] `done` Tests: overlay (8) + scan-with-news end-to-end (3); **183 total green**
- **Phase-gate check:** `pytest` 183 passed · `ruff` clean · news visibly boosts/vetoes picks. ✅

### Phase-4 divergences (recorded)
| Plan | MVP choice | Why | Revisit |
|---|---|---|---|
| FinBERT (transformers/torch) | **Lexicon sentiment** (zero-dep) behind `SentimentModel`; FinBERT optional stub | No torch/model-download/network; deterministic + testable | when running with GPU/transformers |
| Live RSS / NSE filings | **MockNewsProvider** (synthetic); RSS adapter stubbed | Offline, no live dependency; real feeds gated like Dhan | gated external integration |
| Full NER symbol mapping | **Ticker/alias dictionary** matching | Simple, deterministic; covers the watchlist | when scaling to full universe news |

## Phase 5 — Pre-market briefing & gap/bias predictor  ✅ DONE
Before the bell, fuse global cues + overnight news + prior-day technical state into an
index outlook + ranked pre-open watchlist; deliver via the alerter; validation helpers.
- [x] `done` Global-cues provider: `MockGlobalCuesProvider` (correlated GIFT/US/Asia/ADRs) + yfinance stub (7 tests)
- [x] `done` Gap/bias scoring: `index_outlook` + `stock_bias` (news×ADR×index×momentum) (11 tests, hand-verified)
- [x] `done` Open-validation: `validate_open`/`validate_index`/`validate_pick` (did the gap happen + volume confirm) (11 tests)
- [x] `done` Briefing orchestrator (cues + overnight news latest-catalyst + prior-day state → ranked picks) (5 tests)
- [x] `done` CLI `premarket` command (+ `--alert` delivers via Telegram/WhatsApp/console); dashboard Pre-market view
- **Phase-gate check:** `pytest` 217 passed · `ruff` clean · `premarket` runs + sends briefing. ✅

### Phase-5 divergences (recorded)
| Plan | MVP choice | Why | Revisit |
|---|---|---|---|
| `yfinance` live global cues | **MockGlobalCuesProvider** (synthetic, correlated) | Offline/free; real feed gated like Dhan/RSS | gated external integration |
| ML gap predictor | **rules-based bias score** | Start explainable (PLAN §4.8 says rules first, ML later) | Phase 7+ |
- Overnight news uses the LATEST item's sentiment (not the 20-min intraday decay) — a catalyst
  from yesterday still matters at the open. Point-in-time preserved (news ts < today's open).

## Phase 7 — ML signal scorer (shadow mode)  ✅ DONE
LightGBM-style scorer trained on labeled trades, evaluated vs the rules baseline
out-of-sample, run in SHADOW mode (logged alongside rules, never changes decisions).
- [x] `done` MLModel backends: `LogisticModel` (numpy, zero-dep default) + `LightGBMModel` (optional) + default_model (9 tests)
- [x] `done` Feature vectorization (stationary `FEATURE_COLUMNS` derivation, NaN/zero-safe) (11 tests)
- [x] `done` `MLScorer` + `evaluate`/`compare` (acc/AUC/brier vs baseline) (11 tests)
- [x] `done` Dataset builder (point-in-time features + forward first-touch label matching paper-trader) + train harness (time-split)
- [x] `done` CLI `train` (reports ML-vs-rules) + `scan --ml` SHADOW; dashboard scan ML_conf column
- [x] `done` Tests: dataset/train/save-load + shadow-doesn't-change-ranking (4 tests)
- **Phase-gate check:** `pytest` 251 passed · `ruff` clean · `train` + `scan --ml` run end-to-end. ✅
- **Result:** on synthetic data ML AUC ~0.72–0.74 vs rules ~0.51–0.53 (ML beats rules OOS).
  Shadow ML disagrees with overconfident rules picks (e.g. rules 100 / ML 45) — the divergence
  shadow mode exists to surface (consistent with the health scorer's calibration finding).

### Phase-7 divergences (recorded)
| Plan | MVP choice | Why | Revisit |
|---|---|---|---|
| LightGBM + SHAP | **numpy LogisticRegression** default; LightGBM optional (lazy) | No native deps/network; deterministic; identical pipeline | `pip install lightgbm shap` |
| Months-deep real data | **synthetic multi-day** labeled data | Offline; real corpus accrues live (PLAN §3.5/§3.7) | when live data accrues |
- ML is SHADOW-only: it never changes ranking/decisions until it beats rules OOS *and* in
  forward paper-trading (PLAN §4.7/§8). Promotion is a deliberate future manual step.

## Phase 6 — Vercel dashboard + WhatsApp  ✅ DONE (frontend unbuilt here — no Node)
FastAPI engine API (read-only) + Next.js/Lightweight-Charts dashboard scaffold + WhatsApp alerter.
- [x] `done` FastAPI API: /api/leaderboard, /api/premarket, /api/backtest, /api/chart/{sym}, /ws/chart (bar replay), token auth + CORS (9 tests, TestClient)
- [x] `done` WhatsApp Cloud API alerter (env-gated, stdlib, fail-safe) + factory wiring (4 tests, mocked)
- [x] `done` CLI `serve` (uvicorn); `.env.example` WHATSAPP_*/SE_API_TOKEN/SE_CORS_ORIGINS; pyproject [api] extra
- [x] `done` Next.js 14 dashboard scaffold under `web/`: Leaderboard (hero, rules+ML conf, health badge),
      Pre-market, Backtest+Health, per-stock candlestick + VWAP/EMA overlays + live WS push
- **Phase-gate check:** `pytest` 265 passed · `ruff` clean · API boots + serves (verified via ASGI). ✅

### Phase-6 caveats / what's NOT verified here
- **No Node/npm in this environment** → the `web/` Next.js app is reviewed, contract-aligned code but
  was NOT `npm install`/built/run here. Run it on a machine with Node: `cd web && npm install && npm run dev`
  (the Python API must be running: `./run.sh serve`). The FastAPI backend IS fully tested.
- **WhatsApp + Vercel are credential-gated to the user** (Meta Business account, Vercel deploy, Cloudflare
  Tunnel for the engine's public HTTPS). No real external calls are made anywhere in code/tests.
- API computes per-request (run_scan ~0.4s); add caching if the dashboard polls hard (future).

## Phase 8 — Hardening (automation NOT implemented, by ground rule)  ✅ DONE
- [x] `done` Structured logging (`obs/logging_setup.py`)
- [x] `done` Data-freshness fail-safe (`obs/freshness.py`) — suppress signals on a stale/dead feed (PLAN §9.3); opt-in (live only, replay uses past ts)
- [x] `done` Reconnect backoff policy (`obs/backoff.py`) for the live websocket layer
- [x] `done` Engine error-resilience: per-bar try/except → log + warn alert, one symbol can't crash the run
- [x] `done` **Safety tests** (`test_safety.py`): no order method on `BrokerAdapter`, no broker supports live orders, Dhan refuses to connect, no order-placement code anywhere, `allow_live_orders` defaults False
- [x] `done` Tests: obs utilities (6) + safety (5) → **276 total green**
- ❌ **NOT implemented (intentional, ground rule):** automated live-money order execution. The tool
  is decision-support only; the human places every order. Live Dhan feed + any execution path remain
  gated behind explicit user go-ahead and are not built.
- **Phase-gate check:** `pytest` 276 passed · `ruff` clean · engine + API run. ✅

---

## Production integrations (post-phase, credential-free) ✅ DONE
Built after the phases, to take the system toward real use without needing any accounts:
- [x] `done` **Real RSS news** (`news/rss.py`) — Moneycontrol/ET; live-verified (69 items). `SE_NEWS_SOURCE=rss`.
- [x] `done` **Real Yahoo cues** (`premarket/yahoo_cues.py`) — yfinance; live-verified. `SE_CUES_SOURCE=yahoo`.
- [x] `done` **Yahoo NSE broker** (`brokers/yahoo_nse.py`) — free, 15-min delayed, no account. `SE_DATA_SOURCE=yahoo_nse`. **Default.**
- [x] `done` **Angel One broker** (`brokers/angelone.py`) — free real-time via SmartAPI (needs account). `SE_DATA_SOURCE=angelone`.
- [x] `done` **Dhan adapter** (`brokers/dhan.py`) — market-data only, DI-tested (http_post injectable). Data API subscription (~₹500/mo) needed for live.
- [x] `done` **Instrument masters** — Dhan CSV (`universe/instruments.py`) + Angel One JSON (`brokers/angelone.py`).
- [x] `done` **CallMeBot alerter** (`alerts/callmebot.py`) — free personal WhatsApp, no Meta Business account. `SE_ALERTER=callmebot`.
- [x] `done` **Docker** (`Dockerfile` + `docker-compose.yml`: API + scheduler, persists data/).
- [x] `done` **Daily scheduler** (`scheduler.py` + CLI `schedule`): pre-market/scan/archive, IST, holiday-aware.
- [x] `done` **Vercel dashboard** — deployed at https://web-beta-beige-60.vercel.app (Next.js, Hobby/free).
- [x] `done` **Cloudflare tunnel script** (`run-with-tunnel.sh`) — one command: starts API + tunnel + updates Vercel env.
- [x] `done` Config/factory wiring for all sources; `.env.example` updated with all options.
- [x] `done` **Full-NSE real-data pipeline** (`data/yahoo_batch.py`, `universe/nse.py`, `scan/real_harness.py`): once-daily scan + nightly archive over all ~2,000 listed EQ names on free Yahoo bars; scheduler rewired (08:30 briefing / 15:45 scan / 16:10 archive, IST). Verified end-to-end on real data (premarket→Telegram, archive→Parquet round-trip).
- [x] `done` **Dhan v2 live-feed WebSocket scaffold** (`brokers/dhan_ws.py` + `DhanBroker.run()`): binary packet parser (Ticker/Quote/Full, little-endian, 5-level depth + OI), sharded subscribe (≤100/msg, RequestCode 15/17/21), reconnect/heartbeat, injectable transport. All offline-tested (struct-packed frames). Constants verified against official v2 docs (June 2026).
- [x] `done` **Dhan Data API LIVE-VERIFIED** (subscription active, 2026-06-23): REST quote + historical (1500 1m bars/5d) ✓; WebSocket Quote (50B) & Full (162B) packets parse correctly against real bytes (LTP/volume matched REST). Added `EngineRunner.live()` + `signal-engine live` CLI streaming the Dhan feed through the pipeline until market close (paper only). `SE_DATA_SOURCE=dhan` now active.
- [x] `done` **Scheduler live-loop integration**: `live_job` (09:15→close, streams Dhan) + `renew_token_job` (06:00) added to the daily scheduler alongside premarket/scan/archive. Live freshness fail-safe moved from a data_source proxy to the `live()` path (replay/backtest never enforce it).
- [x] `done` **5-year history backfill** (`data/backfill.py` + `signal-engine backfill`): walks Dhan's 90-day intraday windows back 5y, consolidated Parquet per symbol/year (~6 B/bar, ~20 GB for the full NSE universe vs ~35 GB + 2.5M tiny files naive). Concurrent, resumable (`skip_existing`), best-effort. Verified live: 1.69M bars over 5 symbols.
- [x] `done` **In-dashboard Dhan OTP login** (token gate): backend `/api/auth/status` + `/api/auth/dhan/start` + `/api/auth/dhan/consume` (consent flow: generate-consent → Dhan OTP page → consume-consent → persist token, TTL+single-use guarded). Frontend `AuthGate` blocks the dashboard when the Dhan token is expired and offers a one-tap reconnect; Vercel route `/api/dhan/callback` captures the post-OTP tokenId and forwards it to the backend. Token exchange/secrets stay on the backend. Offline-tested (consent fns + endpoints); live OTP round-trip pending user portal setup (API key/secret + register redirect URL).
- [~] `token`: Dhan tokens are 24h max. `RenewToken` returns DH-905 for **portal-generated** tokens (confirmed live + open community issue) — only the API-key/secret consent flow renews, and its Step-2 browser+2FA can't be fully headless. `dhan_auth.renew_token` + `signal-engine renew-token` are wired (work for API-flow tokens); portal tokens still need a daily manual regen. No-fully-unattended path exists without storing credentials (declined on security grounds).
- [x] `done` Tests: rss (9), yahoo cues (9), dhan+instruments (10), scheduler (2), yahoo_nse (11), angelone (13), callmebot (4), yahoo_batch (5), nse_universe (4), real_harness (3), dhan_ws (13) → **358 total green**.

## BUILD COMPLETE — Phases 0–8 + all integrations
All planned phases implemented (Phase 8 hardening done; auto-execution intentionally excluded).
**358 tests green · ruff clean.** CLI: `info / scan / replay / backtest / health / news / premarket /
train / serve / schedule`. Web: FastAPI API + Next.js dashboard at https://web-beta-beige-60.vercel.app.
Default data source: Yahoo Finance (free, no account). Optional: Angel One (free real-time) or Dhan (paid).
Gated (by design): live order execution, Redis, Polars, LightGBM/FinBERT. Run: `./run-with-tunnel.sh`.

---

## Open questions carried from PLAN §10 (defaults chosen; confirm later)
1. Liquidity thresholds — default top ~300–500 liquid NSE names (Phase 2).
2. Dhan exact cost rates — using published rates in `risk.yaml`; verify vs contract notes.
3. Health-score alert sensitivity — default "sustained drop" (Phase 3).
4. Engine host — Oracle Always-Free vs local (deploy decision, not code).
5. WhatsApp vs Telegram-first — Telegram fallback wired first.
6. Paper-trading duration before real money — ≥ 1 month / 20 sessions.

## Paper-Trading Tracker & Analytics (dashboard)
- [x] `done` **Persistence** — every closed paper trade saved to SQLite (`paper_trades`, survives
  restarts); records entry/exit ts, symbol, direction, strategy, entry confidence, entry/exit
  fills, stop-loss, target, exit reason, modeled slippage (in fills) + brokerage (analytics),
  realized P&L (₹ and %). Filterable by date range / symbol / strategy (`fetch_trades`).
- [x] `done` **Analytics module** (`analytics/paper.py`, pure + unit-tested): summary (net P&L,
  win rate, profit factor, avg win/loss, max drawdown, expectancy, best/worst), equity curve,
  drawdown series, P&L histogram, by-strategy, by-symbol, by-time-of-day, factual auto-summary.
  Absolute P&L uses a fixed `reference_trade_value` notional (capital-agnostic tool).
- [x] `done` **API** — `GET /api/paper/trades` + `GET /api/paper/analytics` (filterable).
- [x] `done` **Dashboard page** (`/paper`) — summary cards, equity curve + drawdown, P&L
  histogram, by-strategy/by-symbol tables, time-of-day bars, sortable/filterable trade table,
  auto-summary panel.
- [x] `done` **Live persistence wired** — scheduler `live_job` + `replay --persist` save trades.
- [x] `done` **Tests** — P&L / win-rate / profit-factor / drawdown vs hand-computed values;
  persistence round-trip across a restart; API empty + seeded. (389 total green.)

## Live ops & roadmap (session 2026-06-24)
- [x] `done` **Task 0 — freshness fix (commit `d3061f7`).** Root cause: `FreshnessGuard` marked
  freshness with `bar.ts` (bar OPEN time); a 1-min bar is always ~60s old at close, so the live
  staleness guard (`now - bar.ts > 5s`) suppressed EVERY live entry all day → the real cause of
  "no Telegram alerts" + "no new dashboard data". Fix: mark freshness on **tick arrival**
  (wall-clock) in `on_tick`; `on_closed_bar` only *checks* `is_stale()`; default
  `max_staleness_seconds` 5s→30s. +regression test. Full suite + ruff green.
  - **Verified live:** restarted `cli live --persist` 14:10 IST; warm-start re-seeded the full
    09:15→now session (5624 bars/19 symbols) and re-resolved the 6 prior trades correctly
    (e.g. HDFCBANK premature STOP → real TARGET) with **no duplicate ids** (`paper_trades.id`
    is PK, `INSERT OR REPLACE`); **zero** "feed stale — suppressing entries" after go-live
    (vs every symbol every minute before); dashboard `/api/paper/analytics` count 6→7; Dhan
    WS feed connected; Telegram channel confirmed (alerts route to the configured private chat).
    Note: no *new* live
    setup has fired since 10:09 IST (strategy is highly selective: conf≥75, adx≥25) — a
    trade-driven alert is now gated only on a qualifying setup, not on the bug.
  - ⚠️ **Latent:** `scheduler.live_job` runs `runner.live()` in-process at 09:15 AND a manual
    `cli live` can run concurrently → two live loops persisting to the same DB on a normal day.
    Today is safe (scheduler started 11:07, missed its 09:15 cron). Pick ONE live owner before
    relying on this daily (recommend: let the scheduler own live; drop the manual nohup).
- [~] `in-progress` **Task 2 — public GitHub repo `intraday-edge-engine`.** Secret-scan gate
  re-run & **clean**: no `.env` tracked (only templates), no real token values in working tree
  or full history, the `test_dhan.py` JWT is a synthetic fixture; placeholdered the real
  `DHAN_CLIENT_ID` left in `.env.example`. `gh` installed (brew). Pending: user `gh auth login`,
  then `gh repo create … --public --source=. --push`; thereafter `git push` per change set.
- [ ] `todo` **Task 1A** — wire the existing news stack into `EngineRunner` live re-rating.
- [ ] `todo` **Task 1B** — richer ML features (microstructure / regime / frac-diff / rel-strength)
  for real edge; validate on the archive backtest (bar: **PF > 1 OOS**).

## Notes / decisions log
- (init) Feature-key contract for indicators frozen so strategy + indicator engine agree:
  close, prev_close, vwap, ema_fast(/_prev), ema_slow(/_prev), rsi, adx, atr, atr_pct, rvol,
  orb_high, orb_low, bar_count. `indicators.compute_features(bars_df, params, session_open) -> dict`.
- Break-even % uses a configurable notional `reference_trade_value` (flat brokerage is value-dependent).
