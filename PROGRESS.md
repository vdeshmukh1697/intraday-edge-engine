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

## Phases 4–8 — not started (see PLAN.md §8)
**Phase 4** = news & sentiment integration (FinBERT/VADER → features → rules gates).

---

## Open questions carried from PLAN §10 (defaults chosen; confirm later)
1. Liquidity thresholds — default top ~300–500 liquid NSE names (Phase 2).
2. Dhan exact cost rates — using published rates in `risk.yaml`; verify vs contract notes.
3. Health-score alert sensitivity — default "sustained drop" (Phase 3).
4. Engine host — Oracle Always-Free vs local (deploy decision, not code).
5. WhatsApp vs Telegram-first — Telegram fallback wired first.
6. Paper-trading duration before real money — ≥ 1 month / 20 sessions.

## Notes / decisions log
- (init) Feature-key contract for indicators frozen so strategy + indicator engine agree:
  close, prev_close, vwap, ema_fast(/_prev), ema_slow(/_prev), rsi, adx, atr, atr_pct, rvol,
  orb_high, orb_low, bar_count. `indicators.compute_features(bars_df, params, session_open) -> dict`.
- Break-even % uses a configurable notional `reference_trade_value` (flat brokerage is value-dependent).
