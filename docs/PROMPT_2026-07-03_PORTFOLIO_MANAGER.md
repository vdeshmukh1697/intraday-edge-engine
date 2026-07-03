# Working prompt — 2026-07-03 overnight: ₹1,00,000 Paper Portfolio Manager

> **Repo:** `/Users/vikrantdeshmukh/Personal projects` · **Branch:** `feat/full-nse-realtime-pipeline`
> **Python:** `.venv/bin/python` · **Deadline:** platform restarted, verified and documented before
> 08:30 IST (premarket alert). Paper only — the engine NEVER places live orders.

## Mission (user's words, verbatim intent)

1. *"Make it like a portfolio manager which shows amount of money and everything."*
2. *"Keep the overall investment amount 1 lakh Rupees and use it for paper trading and Predictions."*
3. *"Record paper trading for all the alerts you are sending. Do proper calculations and accordingly
   invest the amount of money."*
4. *"Give more clear reason for me to understand in layman terms for any predictions and actions."*
5. Complete overnight without user involvement; platform up and running for morning paper trading.

**Interpretation locked for this build:** ONE persistent paper book of ₹1,00,000 shared by the whole
platform. The live intraday engine is the only component that actually deploys (debits/credits) cash;
premarket/scan **predictions** are sized against the same book's current equity and shown/logged in ₹
as *suggestions* (they run outside market hours and are executed, if at all, by the live engine — they
must not double-spend the cash). Every alert already lands in the `predictions` table; from tonight
every actionable alert also carries qty/₹ sizing and a plain-English "why", and every live entry/exit
moves real ledger money with exact cost-modeled arithmetic.

## Honesty constraints (do not soften)

- Research verdict stands: `vwap_ema_adx` has **no proven edge** (gross coin-flip, ~8bps cost leak).
  The portfolio view makes results *legible*, it does not imply profitability. Never label
  confidence as "win-rate". Keep all existing risk gates and defaults unchanged.
- All money is **paper**. UI/alerts may say "portfolio", never imply real funds.
- Costs are real: use `CostModel.charges(entry, exit, qty).total` (₹, round-trip) for every closed
  trade. Slippage is already inside the persisted fills.

## Architecture you are extending (read before editing)

| Piece | File | What exists |
|---|---|---|
| Sizing helper | `signal_engine/risk/sizing.py` | `size_plan(plan, risk, capital)` → qty/rupee_risk/notional; fixed-fractional + Kelly cap + notional≤capital. Keep byte-compatible. |
| Paper fills | `signal_engine/paper/trader.py` | `PaperTrader` fills/exits by %; `PaperPosition` is capital-agnostic (no qty). Pessimistic stop-first; slippage both legs. |
| Live pipeline | `signal_engine/engine/runner.py` | `_surface()` opens position + alert (`_format_alert`, `_plan_meta`); `_on_position_closed()` exit alert; `_sync_open_position()` per-bar mark → `open_positions` table; warm-start clears+re-derives today (`delete_trades_for_day`, `clear_open_positions`) with `_suppress_alerts=True`. |
| Storage | `signal_engine/storage/repository.py` | SQLite WAL; tables `trade_plans`, `paper_trades`, `open_positions`, `live_status`, `predictions`. ALTER-based forward migrations pattern at `init_db`. |
| Predictions log | `signal_engine/storage/predictions_log.py` + `alerts/recording.py` | Every alert → `predictions` row via `RecordingAlerter` (best-effort, own connection per write). `_META_COLUMNS` maps meta→columns. |
| Costs | `signal_engine/risk/costs.py` | `CostModel.charges(entry, exit, qty)` → object with `.total` (₹). `breakeven_pct` used for % net. |
| Analytics | `signal_engine/analytics/paper.py` | `enrich_trade` assumes FIXED ₹1L notional per trade (modeled, not a ledger). Becomes the legacy fallback only. |
| API | `signal_engine/api/app.py` | FastAPI `create_app()`; `/api/paper/trades`, `/api/paper/analytics`, `/api/paper/open`, `/api/predictions`, `/api/live/status`; `_QuoteHub` (don't add REST quote pollers). |
| Web | `web/` (Next.js app router) | Pages: `/` (leaderboard), `/paper`, `/predictions`, `/premarket`, `/backtest`, `/watchlist`, `/stock/[symbol]`. Components: `Nav`, `EquityChart`, `Sparkline`, `InfoTip`, `AuthGate`. Poll pattern: 5s delta-poll with `since_id` on predictions. |
| Scheduler | `signal_engine/scheduler.py` | 08:30 premarket, 08:45 healthcheck, 09:15 live, 15:45 scan, 16:10 archive. Alerts route through `build_alerter(cfg)` → RecordingAlerter(Telegram). |
| Config | `signal_engine/config.py` | `RiskParams.account_capital=100000`, `risk_per_trade_pct=0.5`, `kelly_fraction_cap=0.25`. `cfg.risk.{risk,costs,slippage,alerts}` from `config/risk.yaml`. |
| Ops | `scripts/run-scheduler-service.sh`, `scripts/run-engine-service.sh`, `run-with-tunnel.sh` | launchd `com.vikrant.signal-engine-{scheduler,tunnel}`; tunnel agent runs uvicorn + cloudflared + repoints Vercel env + `vercel deploy --prod` from `web/`. |

## Design contract (all agents build against THIS; do not renegotiate)

### 1. Config — `PortfolioParams`

In `config.py`, add to the risk config family and expose as `cfg.risk.portfolio`:

```python
class PortfolioParams(BaseModel):
    starting_capital: float = 100000.0   # ₹1,00,000 — the user's one-lakh paper book
    allow_short: bool = True             # intraday shorts allowed; notional blocked as margin
    mark_snapshot_minutes: int = 5       # intraday equity snapshot cadence
```

`config/risk.yaml` gains a `portfolio:` block with `starting_capital: 100000.0`. Sizing now uses
**live portfolio equity** as capital (compounding); `RiskParams.account_capital` stays only as the
fallback when no ledger is available (backtests/scan previews without a repo).

### 2. Schema (repository.init_db, same ALTER-migration pattern as existing)

```sql
CREATE TABLE IF NOT EXISTS portfolio_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    starting_capital REAL, cash REAL,
    realized_pnl_total REAL, updated_ts TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS portfolio_equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, day TEXT, kind TEXT,        -- kind: 'mark' (intraday) | 'eod'
    equity REAL, cash REAL, invested REAL, unrealized_pnl REAL,
    open_count INTEGER, run_id TEXT
);
-- ALTER TABLE migrations (NULL for legacy rows):
-- paper_trades   += qty INTEGER, notional_entry REAL, charges_inr REAL, pnl_inr REAL
-- open_positions += qty INTEGER, notional REAL, unrealized_pnl_inr REAL
-- predictions    += reason_plain TEXT, portfolio_equity REAL, notional REAL
```

`predictions` migration must ALSO happen defensively inside `log_prediction` (it opens its own
connection and may hit a pre-migration DB): after `CREATE IF NOT EXISTS`, try the INSERT with the new
columns; on `sqlite3.OperationalError` mentioning a missing column, ALTER-add the three columns and
retry once. Add `"reason_plain", "portfolio_equity", "notional"` to `_META_COLUMNS`.

### 3. Ledger — NEW `signal_engine/portfolio/ledger.py`

```python
class PortfolioLedger:
    def __init__(self, repo: SignalRepository, starting_capital: float = 100000.0): ...
    # Properties (all ₹): cash, equity(marks: dict[symbol, float] = None),
    #                     invested (Σ open notional), realized_pnl_total
    def rebuild(self) -> None:
        """cash = starting_capital + Σ pnl_inr over ALL closed paper_trades WHERE pnl_inr IS NOT NULL
        minus Σ notional of rows currently in open_positions with qty (re-block open money).
        Persist to portfolio_state. Idempotent; call at live() start after warm-start replay and
        at ledger construction when portfolio_state is missing."""
    def affordable_qty(self, plan, risk_qty: int) -> int:
        """min(risk_qty, floor(cash / plan.entry)); never negative."""
    def on_entry(self, pos, qty: int) -> None:
        """notional = qty * pos.entry_fill (FILL, not plan price). cash -= notional (LONG and SHORT
        both block notional — margin model, no leverage). Persist state + open_positions row fields."""
    def on_exit(self, pos, qty: int, cost_model) -> dict:
        """charges = cost_model.charges(entry_fill, exit_fill, qty).total
        pnl_inr = direction.sign * (exit_fill - entry_fill) * qty - charges
        cash += notional + pnl_inr. Persist. Returns {qty, notional, charges_inr, pnl_inr,
        equity_after, cash_after}."""
    def snapshot(self, kind: str, marks: dict[str, float]) -> None: ...
```

**Invariant (assert in tests):** `equity == cash + Σ_open(notional + unrealized_pnl_inr)` and after
any close, `equity == starting_capital + Σ pnl_inr(all closed)` exactly (float tolerance 1e-6).
Unrealized ₹: `direction.sign * (last - entry_fill) * qty`.

Single-writer rule: only the live engine mutates cash. Scheduler/API only read. SQLite WAL already
allows this safely.

### 4. Runner integration (`engine/runner.py`, `paper/trader.py`)

- `PaperPosition` gains `qty: int = 0` (+ optional `notional`, `pnl_inr`, `charges_inr` fields) —
  domain model change in `domain/models.py`, default keeps every existing test green.
- `EngineRunner.__init__` builds `self.ledger = PortfolioLedger(repo, cfg.risk.portfolio.starting_capital)`
  when `repo` is present (live/replay-with-repo); None otherwise → all ledger hooks no-op and sizing
  falls back to `account_capital` (today's behavior).
- `_surface(plan)`: `size = size_plan(plan, cfg.risk.risk, capital=ledger.equity)`;
  `qty = ledger.affordable_qty(plan, size["qty"])`.
  - `qty >= 1` → proceed; stash qty on the created position.
  - `qty == 0` → do NOT open; send alert kind `"skip"`, plain reason ("one share of X costs ₹Y but
    only ₹Z cash is free"), still logged to predictions. This replaces silently alerting unafforable
    trades. Counts toward nothing (no daily-trade slot burn).
- On actual FILL (PaperTrader `_fill_entry` happens next bar): runner detects PENDING→OPEN transition
  (it already syncs opens per bar) → `ledger.on_entry(pos, qty)` once (idempotent guard). Note entry
  alert fires at plan time with plan-price sizing; the fill adjusts notional by slippage — fine.
- `_on_position_closed`: `money = ledger.on_exit(pos, qty, cost_model)` → exit alert becomes
  ₹-denominated (see §6) and meta carries `pnl_inr`, `notional`, `portfolio_equity`.
- `_sync_open_position`: also write `unrealized_pnl_inr` and, throttled to
  `portfolio.mark_snapshot_minutes`, `ledger.snapshot('mark', marks)`.
- Warm-start: existing delete+replay already re-derives today; call `ledger.rebuild()` right after
  warm-start completes (before live bars), so restarts can never double-spend. Force square-off at
  close → after last exit, `ledger.snapshot('eod', ...)`.
- Suppressed-alert replay still moves the ledger (it IS today's session), it just doesn't re-alert.

### 5. Plain-language reasons — NEW `signal_engine/alerts/plain.py`

Pure functions, no I/O, exhaustive fallback (never raise, never emit raw codes):

```python
def explain_entry(plan, qty, notional, equity) -> str
def explain_exit(pos, money: dict) -> str          # money = ledger.on_exit(...) dict
def explain_skip(plan, price, cash) -> str
def explain_halt(reason, session_pnl_pct) -> str
def explain_premarket(outlook, top_pick) -> str
def explain_scan_pick(plan_like) -> str
def translate_reasons(reasons: list[str]) -> str   # strategy codes → one flowing clause
```

Read `strategies/vwap_ema_adx.py` (and premarket/scan reason vocab) and map every code it can emit;
unknown codes fall back to a readable generic ("the setup's other checks passed"). Tone: one or two
short sentences a non-trader follows. Money in Indian format (₹1,00,000). Examples to match:

- Entry: `Why: RELIANCE is trending up — the price is above its day-average (VWAP) and short-term
  momentum is stronger than long-term, with a solid trend reading. Buying 17 shares (~₹48,450, 48% of
  the portfolio), risking about ₹500: exit at ₹2,827 if it drops, book profit near ₹2,893.`
- Exit (target): `Sold NTPC's 120 shares at the profit target. Made ₹612 after ₹43 charges — the
  portfolio is now ₹1,00,569 (cash ₹1,00,569).`
- Skip: `Found a setup on MRF but one share costs ₹1,22,340 and only ₹51,550 cash is free, so the
  paper portfolio sits this one out.`
- Premarket: `Market looks slightly positive at open (global cues up). Best idea: INFY long — if
  taken, the plan would put about ₹22,000 of the ₹1,00,000 book on it. This is a heads-up, money
  moves only when the live engine actually enters.`

Wire-in: every alert call site sets `meta["reason_plain"]` and appends `\nWhy: <plain>` (entries/
skips) or plain-first phrasing (exits) to the Telegram text. Sites: runner `_format_alert`/
`_plan_meta`/`_on_position_closed`/halt; scheduler `premarket_job`/`scan_job`/`healthcheck_job`
(healthcheck: one plain line, e.g. "Everything is ready for today's trading. ₹X cash free.").

### 6. Alert text upgrades (runner)

- Entry alert keeps all current params, plus: `qty N (~₹NOTIONAL, R% of book) | book ₹EQUITY` and the
  `Why:` line. Keep under Telegram limits (existing sends are single messages; stay < 3500 chars).
- Exit alert: `SYMBOL CLOSED reason ₹+PNL (net, after ₹C charges) NET% R | book ₹EQUITY` + plain line.
- New kind `"skip"` (predictions.kind) — dashboard filter chip must include it.

### 7. API (`api/app.py`)

```
GET /api/portfolio →
{ "starting_capital": 100000.0, "equity": ..., "cash": ..., "invested": ...,
  "unrealized_pnl_inr": ..., "realized_pnl_today_inr": ..., "realized_pnl_total_inr": ...,
  "return_total_pct": ..., "return_today_pct": ...,
  "open_positions": [ { symbol, direction, strategy, qty, entry_fill, last_price, notional,
                        unrealized_pnl_inr, unrealized_pnl_pct, stop_loss, target, entry_ts } ],
  "today": { "trades": n, "wins": n, "pnl_inr": ..., "charges_inr": ... },
  "per_strategy": [ { strategy, trades, pnl_inr, invested_now } ],
  "updated_ts": "..." }
GET /api/portfolio/equity?days=30 → { "points": [ {ts, day, kind, equity, cash, invested} ] }
```

Compute read-only from tables (portfolio_state + open_positions + paper_trades + portfolio_equity);
NO ledger writes, NO broker calls (don't touch `_QuoteHub`). Legacy rows (pnl_inr NULL) are excluded
from ₹ sums but still count in trade counts with a `"modeled": true` marker in `/api/paper/trades`
(which keeps `enrich_trade` as fallback for them). If `portfolio_state` is missing → serve
starting-capital defaults (fresh book), never 500.

### 8. Web (`web/`)

- **New `/portfolio` page, first item in `Nav` ("Portfolio").** Sections:
  1. Money header cards: Total value ₹ (big), Cash available, Invested now, Today's P&L (₹ + %),
     Overall (₹ + % since start). Green/red per sign. `Intl.NumberFormat('en-IN')` everywhere.
  2. Equity curve (reuse `EquityChart`) from `/api/portfolio/equity`.
  3. Open positions table: symbol, side, qty, avg entry, live price, invested ₹, P&L ₹/%, stop,
     target — poll 5s (page-level `setInterval` fetch of `/api/portfolio`, matching `/paper` idiom).
  4. Today's closed trades: time, symbol, side, qty, ₹P&L after charges, reason chip, and the plain
     "why" line under each row.
  5. Per-strategy mini-table.
  6. Footer note: "Paper trading — simulated money, real prices, real cost model."
- `/predictions`: render `reason_plain` as the row's subtitle ("Why: …"); add `skip` to kind chips.
- `/paper`: switch money columns to real `pnl_inr/charges_inr` when present (fallback badge
  "modeled" for legacy rows).
- Match existing styling/components; typed fetch helpers in `web/lib` if that's the current pattern.

### 9. Scheduler predictions sizing (read-only)

`premarket_job` and `scan_job` alerts add, per pick: suggested `qty`/`₹notional` sized from **current
ledger equity** (read `portfolio_state`, fallback ₹1L) with `size_plan(..., capital=equity)`, plus
`reason_plain` + `portfolio_equity` in meta. Explicitly phrased as suggestions (§5 example).

### 10. Tests (all in `tests/`, existing style: fakes + monkeypatch, no network, no real DB files —
use `sqlite:///:memory:` or tmp_path)

- `test_portfolio_ledger.py` — entry/exit math LONG & SHORT vs hand-computed charges; affordability;
  no-leverage invariant with 3 concurrent; rebuild() from trades (incl. legacy NULL rows ignored);
  equity invariant after every op; snapshot rows.
- `test_plain_reasons.py` — every vwap_ema_adx reason code translated; unknown code fallback;
  ₹ formatting; each explain_* returns non-empty prose without raising on degenerate inputs.
- `test_runner_portfolio.py` — synthetic bars through `EngineRunner` (mock broker pattern from
  existing runner tests): entry debits fill-notional, target exit credits exactly
  `notional + qty*(exit-entry) - charges`; skip path (unaffordable) alerts kind=skip and opens
  nothing; warm-start restart double-spend guard (run twice, rebuild → same cash).
- `test_api_portfolio.py` — seed tmp DB, `create_app` TestClient: shapes above, fresh-DB defaults,
  legacy-row exclusion.
- `test_predictions_plain.py` — old-schema DB gains `reason_plain` via defensive migration on
  `log_prediction`; RecordingAlerter passes it through.
- Existing suites must stay green (notably `test_risk.py` byte-stability of `position_size`,
  runner/paper/api suites).

## Execution plan (multi-agent, tonight)

| Agent | Scope | Owns (only these) |
|---|---|---|
| A `core-ledger` | §1 §2 §3 + domain qty fields + predictions migration | `config.py`, `config/risk.yaml`, `storage/repository.py`, `storage/predictions_log.py`, `portfolio/` (new), `domain/models.py`, `tests/test_portfolio_ledger.py`, `tests/test_predictions_plain.py` |
| B `plain-reasons` | §5 module + tests (pure; no call-site edits) | `alerts/plain.py` (new), `tests/test_plain_reasons.py` |
| E `web-ui` | §8 against the contract JSON | `web/**` only |
| C `runner-wiring` | §4 §6 §9 (imports A+B) | `engine/runner.py`, `paper/trader.py`, `scheduler.py`, `alerts/recording.py` (only if needed), `tests/test_runner_portfolio.py` |
| D `api` | §7 (imports A) | `api/app.py`, `api/serializers.py`, `tests/test_api_portfolio.py` |
| Verify | full `pytest -q`, `web: tsc --noEmit && next build`, fix loop | anything, minimally |
| Review | diff review (bugs) + adversarial verify + fix confirmed | anything, minimally |

Rules for every agent: work in the main repo (NOT a worktree), never `git commit/push`, never touch
files owned by another agent, never start servers, run your own tests with `.venv/bin/python -m
pytest <your files> -q` before returning, keep the no-edge/paper-only framing.

## Morning-readiness runbook (orchestrator, after code is green)

1. Full gates: `pytest -q` all green; `cd web && npx tsc --noEmit && npm run build`.
2. Logical commits on the branch (core ledger / reasons / api / web / docs).
3. `launchctl kickstart -k gui/$(id -u)/com.vikrant.signal-engine-scheduler` then
   `...-tunnel` (tunnel restart re-points Vercel env to the fresh URL and runs
   `vercel deploy --prod` — ships the new web build).
4. Verify: `curl localhost:8000/api/portfolio` (fresh ₹1,00,000 book), `/api/predictions?limit=5`,
   `/healthz`; scheduler log shows all jobs registered; tunnel URL serves healthz; Vercel
   `/portfolio` renders money cards.
5. Update `docs/MORNING_HANDOFF.md` (new top section) + memory. Telegram self-test NOT required
   (08:30/08:45 jobs will exercise the full path; don't spam the user at night).

## Acceptance (what the user sees at 09:15)

- Telegram entry alerts read like: params + `qty/₹/book` + a `Why:` sentence in plain English.
- `/portfolio` shows ₹1,00,000 book with live cash/invested/P&L updating through the session.
- Every alert (incl. skips) appears on `/predictions` with its plain reason.
- Exits report ₹ P&L after real modeled charges; the book compounds; no leverage ever.
