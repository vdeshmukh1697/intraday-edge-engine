# Intraday Signal Engine (NSE) — decision-support tool

A capital-agnostic intraday **signal** engine for Indian equities. It scans price action,
applies indicators + a pluggable strategy, gates each idea through a percentage-based risk
layer, and surfaces a ranked **trade plan** (entry / stop% / target% / R:R / confidence).
A built-in **paper-trader** tracks how the system's own predictions play out against the data.

> ⚠️ **Decision-support only — not investment advice.** This tool **never places live
> orders**; you decide and execute every trade yourself. Intraday trading carries a real,
> often total, risk of loss. Signals are probabilistic and **not guaranteed**. See PLAN.md §9.

Built per [PLAN.md](PLAN.md). Implementation status in [PROGRESS.md](PROGRESS.md).
**Phases 0–1 (MVP) are implemented.** Live data, full-universe scan, news/sentiment,
pre-market briefing, ML scorer and the Vercel dashboard are later phases.

---

## What works today (Phase 0–1 MVP)
- **Synthetic market data** — realistic intraday sessions, so everything runs anytime,
  with **no live-market dependency** and no broker account needed.
- **Tick → bar aggregation** with strict closed-bar discipline (no lookahead).
- **Indicators** (hand-rolled, tested): VWAP, EMA, RSI, ATR, ADX, RVOL, MACD, Supertrend, ORB.
- **Strategy** (`vwap_ema_adx`): weighted-rule ensemble → 0–100 confidence with reasons.
- **Risk layer**: percentage-based `CostModel` (Dhan charges), stop/target, R:R floor,
  **edge-after-cost gate** — all capital-agnostic.
- **Paper-trader**: next-bar entry, stop/target/time-stop/15:20 square-off, **net-of-cost** P&L.
- **Engine**: wires it all together for a full session replay; **NSE calendar + session state machine**.
- **CLI** + minimal **Streamlit** cockpit.
- **54 passing tests** (indicators, costs, risk, paper-trader, aggregator, calendar, strategy, end-to-end).

---

## Setup

Requires Python 3.9+ (developed on 3.9).

```bash
git clone <this-repo> && cd "Personal projects"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # core engine + tests
pip install streamlit                     # optional: dashboard
# (optional) editable install to get the `signal-engine` command:
pip install -e .
```

### Configuration
- **Non-secret settings**: `config/settings.yaml` (watchlist, market hours, strategy params)
  and `config/risk.yaml` (cost rates, R:R floor, edge gate, guardrails).
- **Secrets & runtime mode**: copy the template and edit — **never commit `.env`**:
  ```bash
  cp .env.example .env
  ```
  Key vars (see `.env.example` for all):
  - `SE_DATA_SOURCE=mock` (default; synthetic) or `dhan` (gated/disabled — see Safety).
  - `SE_ALERTER=console` (default) or `telegram` (set `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`).
  - `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` — only for future data-only Dhan use.

No API keys are required to run the MVP.

---

## Run

> **macOS note:** there is no bare `python` on macOS and the `signal-engine` command
> only exists *inside the activated venv*. Three equivalent ways to run, easiest first:
>
> 1. **`./run.sh <args>`** — wrapper that auto-activates the venv (no activation needed):
>    ```bash
>    ./run.sh replay --demo
>    ./run.sh scan --top 20
>    ```
> 2. **Activate, then use the command** (must `source` once per new terminal):
>    ```bash
>    source .venv/bin/activate     # prompt shows (.venv)
>    signal-engine replay --demo   # needs `pip install -e .` once
>    ```
> 3. **Full venv path** (no activation, no editable install):
>    ```bash
>    .venv/bin/python -m signal_engine.cli replay --demo
>    ```

The `replay` command runs a full synthetic trading session through the **exact pipeline**
the live engine would use, then prints the leaderboard + paper-trading result.

```bash
./run.sh replay --date 2025-06-23 --demo
./run.sh info        # config + safety summary
```

Useful flags: `--symbols RELIANCE,INFY`, `--seed 7`, `--demo` (bias regimes so setups appear),
`--persist` (write plans/trades to SQLite at `SE_DB_URL`).

### Full-universe scan → "best intraday stocks" leaderboard (Phase 2)
Scans a synthetic ~2,000-symbol NSE universe, applies the liquidity + %-cost filter, runs
the strategy + risk gate on survivors, and ranks the best setups:
```bash
./run.sh scan --date 2025-06-23 --as-of 11:00 --universe 2000 --top 20
```
"Scan wide, rank narrow": the full universe is screened down to the liquid, cost-viable,
ranked Top-N — each row a capital-agnostic trade plan. Runs in well under a second.

### News & sentiment (Phase 4)
A news pipeline (ingest → symbol-map → sentiment + event-type → **point-in-time** features)
feeds the rules engine as a gate/booster/veto, with the headline reflected in each pick's
"why". On by default in `scan`; toggle with `--no-news`. Preview the day's (synthetic) news:
```bash
./run.sh news                       # preview headlines (mapped + scored)
./run.sh scan --top 20              # leaderboard with news influence
./run.sh scan --top 20 --no-news    # technical-only, for comparison
```
Sentiment uses a zero-dependency finance **lexicon** model by default (deterministic, offline);
**FinBERT** is the optional production model behind the same `SentimentModel` interface. News
**sources** are synthetic (`MockNewsProvider`); real RSS/NSE-filing ingestion is a gated
external integration (stubbed), as is the live Dhan feed.

### Pre-market briefing (Phase 5)
Before the open, fuse global cues (GIFT Nifty / US / Asia / ADRs) + overnight news + the
prior session's technical state into an **index outlook** (expected gap, risk tone) and a
**ranked pre-open watchlist** with per-stock bias, setup, catalyst, and confidence:
```bash
./run.sh premarket --date 2025-06-23            # print the briefing
./run.sh premarket --date 2025-06-23 --alert    # also send it via the configured alerter
```
Global cues use a synthetic `MockGlobalCuesProvider` (real `yfinance` is a gated stub).
The gap/bias predictor is rules-based (PLAN §4.8: rules first, ML later). Open-validation
helpers (`signal_engine.premarket.validation`) check whether a predicted gap actually
materialised on confirming volume.

### Dashboard (optional)
```bash
pip install streamlit
streamlit run dashboard/app.py
```
Leaderboard, paper trades, and a per-stock chart with VWAP/EMA overlays.

---

## Tests

```bash
pytest                       # full suite (54 tests)
pytest tests/test_risk.py    # e.g. just the risk module
ruff check signal_engine tests   # lint
```
The risk and cost-model tests are **hand-verified against worked examples** (see
`tests/test_costs.py` / `tests/test_risk.py`) — the numbers are checked by arithmetic,
not just regression-locked.

---

## Backtesting & Strategy Health (Phase 3)

A multi-day, event-driven backtester that replays synthetic sessions through the **same**
Indicator → Signal → Risk → Paper-trade core as the live engine (so backtest and live
cannot silently diverge, and closed-bar/next-bar discipline is inherited — PLAN.md §2/§6):

```bash
./run.sh backtest --start 2025-06-02 --days 10        # metrics + health summary
./run.sh health   --start 2025-06-02 --days 10        # health breakdown + degradation alert
```

`backtest` reports net-of-cost metrics: win rate, profit factor, expectancy, max drawdown,
Sharpe/Sortino, equity curve (§6.2). `health` computes the **Strategy Health Score** (§6.6) —
a rolling composite of hit-rate, profit factor, expectancy, **confidence calibration (Brier)**,
and drawdown — and **fires an alert via the configured Alerter if the score falls below the
threshold** (the A10 "system conscience"). Walk-forward / time-split helpers
(`signal_engine.backtest.walkforward`) support train/val/test discipline (§6.4).

> The metrics math is **hand-verified** (`tests/test_metrics.py`) and was independently
> re-checked against a separate worked example during the build.

---

## Safety & scope (read this)
- **No live order execution exists anywhere in the code.** The `BrokerAdapter` interface is
  market-data only; there is no order-placement method. `SE_ALLOW_LIVE_ORDERS` is an
  explicit, audited, off-by-default no-op flag.
- The **Dhan** adapter is a data-only stub that refuses to connect — integrating a real
  broker feed is gated behind an explicit go-ahead (PLAN.md §8 / project ground rules).
- **Personal, single-user tool.** Distributing signals to others has regulatory
  implications (SEBI) — see PLAN.md §9.

## Project layout
```
signal_engine/
  domain/        # frozen contracts: enums + models
  config.py      # typed config (YAML + env)
  market/        # IST clock, NSE calendar, session state machine
  brokers/       # BrokerAdapter (data-only): MockBroker, Dhan stub
  data/          # synthetic intraday data generator
  ingestion/     # tick -> bar aggregator + roll-ups
  indicators/    # hand-rolled indicators + compute_features
  strategies/    # Strategy interface + registry + vwap_ema_adx
  risk/          # CostModel, RiskManager, position sizing
  paper/         # live paper-trader
  storage/       # Parquet bar archive + SQLite repository
  engine/        # EngineRunner orchestrator
  cli.py         # command-line entrypoint
dashboard/app.py # Streamlit cockpit
tests/           # pytest suite
config/          # settings.yaml, risk.yaml
```
