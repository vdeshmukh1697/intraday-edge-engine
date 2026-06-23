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

## Phase 0 — Foundations & data spine
- [x] `done` Repo scaffold (git, .gitignore, pyproject, requirements, .env.example)
- [x] `done` Config: YAML (settings.yaml, risk.yaml) + pydantic loader (`config.py`)
- [x] `done` Domain contracts: enums + models (`domain/`)
- [x] `done` Market calendar + clock + session state machine (`market/`)
- [x] `done` `BrokerAdapter` interface (data-only, no live orders) (`brokers/base.py`)
- [x] `done` `Alerter` interface + ConsoleAlerter (`alerts/`)
- [x] `done` `Strategy` interface + registry (`strategies/base.py`)
- [ ] `in-progress` MockBroker (synthetic replay) + synthetic data generator
- [ ] `in-progress` Bar aggregator (tick -> 1m closed bars, roll-ups)
- [ ] `todo` Parquet bar store + SQLite repository
- [ ] `todo` Tests: calendar/session, aggregator
- News/global-cues archive: **deferred to Phases 4–5** (not needed for MVP signal loop)

## Phase 1 — MVP: live -> trade plans -> paper-trade -> alert
- [x] `done` First strategy `vwap_ema_adx` (consumes feature-key contract)
- [ ] `in-progress` Indicator engine (VWAP, EMA, RSI, ADX, ATR, RVOL, ORB, MACD, Supertrend) + `compute_features`
- [ ] `in-progress` Risk layer: %-based CostModel, RiskManager (stop/target, R:R floor, edge-after-cost gate), size calculator
- [ ] `in-progress` Live paper-trader (entry trigger, stop/target/time-stop/square-off; net-of-cost P&L)
- [ ] `todo` Engine runner (wire feed -> aggregator -> features -> strategy -> risk -> plan -> paper-trader -> alerts)
- [ ] `todo` CLI entrypoint (replay a day; emit picks)
- [ ] `todo` Minimal Streamlit dashboard (watchlist + picks + paper P&L)
- [ ] `todo` Tests: indicators, costs, risk (hand-verified), paper-trader, strategy, end-to-end replay
- [ ] `todo` README (setup, run, test, backtest)

## Phases 2–8 — not started (see PLAN.md §8)

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
