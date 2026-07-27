# BUILD PROMPT — Nightly Quant-Desk Review Agent ("the desk")

> Working brief for a fresh Claude Code session. Repo: `/Users/vikrantdeshmukh/Personal projects`
> (branch `feat/full-nse-realtime-pipeline`, venv `.venv/bin/python`, Python 3.9, SQLite+WAL).
> Written 2026-07-26. **Read `docs/MORNING_HANDOFF.md` and the memory index first.**

---

## 0. The user's ask, verbatim intent

> "An agent that runs every night, analyzes the paper trading done during the day and thinks how
> we can improve our strategy. Target 1% profit every day after brokerage and taxes. Analyze
> everything, ask intelligent questions like why we chose that particular stock and the entry/exit,
> and the agent's intelligence should improve every day. Based on the analysis the agent should
> improve the strategy every day until we achieve the daily 1% target. Think like Goldman Sachs /
> Morgan Stanley and act like them."

**Build exactly this — with one non-negotiable reframe, below. Do not silently water the ask down,
and do not pretend the target is achievable when the data says otherwise.**

---

## 1. Ground truth you must not paper over (read before designing)

Measured facts from this project's own research (`docs/STRATEGY_IMPROVEMENT_PLAN_2026-07.md`,
`docs/SPIKE_HUNTER_FINDINGS.md`, `docs/PAPER_TRADING_ANALYSIS_2026-06.md`):

| Fact | Number |
|---|---|
| Live paper book, ~19 sessions | **₹95,293** from ₹1,00,000 start (−4.7%) |
| Strategy (`vwap_ema_adx`) gross edge | ~coin-flip; **net loss ≈ the charge bill** |
| Round-trip friction | **~9.2bps charges + ~6bps slippage** (~15–19bps all-in) |
| P0 friction-in-R gate (live since 07-13) | stops must span ≥ ~0.57%; cut trades ~15/day → ~4-8/day |
| Movers sleeve, 80 resolved predictions | magnitude **8.2× lift** (40% vs 4.9% base); **direction 33% = worse than a coin flip**; long legs **−2.09%/leg** |
| Earnings jump-drift (best signal found) | real, but alpha is **short-side mid-caps** → blocked by Indian short-sale rules |
| 1,693 intraday + 195 swing variants tested | **none cleared** `edge_verdict` (n≥2000, OOS, PBO<0.10, DSR) |

**The 1%/day arithmetic.** 1%/day ≈ **+250% annualised** compounded. No institutional desk sustains
that; the best systematic funds target ~1–3% *per month* gross. To net 1%/day at our ~15bps
round-trip friction with ~5 trades/day you would need roughly **+1.08% gross/day**, i.e. a per-trade
edge of ~+0.22% net on a signal that currently has **no measurable gross edge at all**.

**So: the target is the user's stated goal and the agent must optimise toward it — but the agent's
first duty is honest measurement.** Build it to (a) compute the exact gap to 1%/day every night and
name precisely which factor (win-rate / payoff / trade count / friction) would have to change and by
how much, and (b) say plainly, with numbers, when the gap is not closable by parameter work. An
agent that reports fake progress toward 1%/day is worse than useless — it would justify risking real
money. **The honesty is the product.** This mirrors what real desks do: attribution, TCA, and
killing strategies that don't clear costs.

## 2. The trap you must design against (most important section)

The ask says *"improve the strategy every day."* Taken literally — re-tune live parameters nightly on
4–8 trades — that is **a guaranteed overfitting machine**, and this repo exists partly to prevent it:
we already killed a universe gate that looked great on 3 names / 1 session (`live_universe.
restrict_to_allowlist` stays OFF), and a "max 5 trades/day → +6.9%" counterfactual that was pure
arrival-order artifact.

**Therefore split "improve" into two clocks:**

- **Nightly (fast, safe, always runs):** forensic analysis, per-trade interrogation, attribution,
  hypothesis generation, lesson grading, and **written proposals**. Zero live parameter changes.
- **Gated (slow, evidence-bound):** any change to `config/risk.yaml` / `config/settings.yaml` /
  strategy params must (i) be proposed with a **pre-registered** test + evidence bar, (ii) ship
  **config-gated, DEFAULT OFF**, (iii) be validated on the archive replay and/or ≥10 sessions of
  shadow evidence, and (iv) be applied **only by the human** (the agent writes the diff, never
  applies it). Follow the exact precedent of the P0 gate in
  `docs/STRATEGY_IMPROVEMENT_PLAN_2026-07.md` §3.

The agent may **auto-apply nothing** that touches live trading behaviour. It writes proposals,
it never flips a switch. State this in the module docstring so nobody "helpfully" changes it later.

## 3. What to build

Create **`signal_engine/desk/`** — the nightly quant-desk review. Suggested modules (your call on
exact split, but keep each file single-purpose and testable):

```
signal_engine/desk/
  facts.py        # deterministic fact-gathering (NO LLM): every number the review can cite
  forensics.py    # per-trade interrogation + counterfactual replay from the bar archive
  attribution.py  # P&L decomposition: alpha vs beta(tide) vs friction vs sizing
  gap.py          # the 1%/day gap model — what must be true, how far we are
  ledger.py       # append-only hypothesis + lesson ledger with outcome grading
  analyst.py      # the reasoning layer (LLM optional, deterministic fallback mandatory)
  review.py       # orchestrates: facts -> forensics -> analyst -> report + proposals
```

### 3.1 `facts.py` — deterministic, no LLM, the single source of truth
Pull and compute (all already persisted — verify column names against the live DB first):
- `paper_trades`: entry/exit fills+ts, direction, `pnl_pct_gross`, `cost_pct`, `pnl_pct_net`,
  `r_multiple`, `hold_minutes`, `exit_reason`, `won`, `qty`, `notional_entry`, `charges_inr`,
  `pnl_inr`, `alpha_pct`, `nifty_ret_pct`, `stop_loss`, `target`, `confidence`
- `predictions`: `kind` in (entry/exit/skip/halt/advice), `reasons` (JSON — **why the stock was
  chosen**), `reason_plain`, `stop_pct`, `target_pct`, `risk_reward`, `expected_move_pct`,
  `rupee_risk`, `notional`, `portfolio_equity`
- `trade_plans`: the plan as surfaced (incl. rejected/unfilled)
- `portfolio_equity` (equity curve), `portfolio_state` (cash), `open_positions`
- `microstructure_signals` (dir_score / CVD / OB imbalance + `dir_correct`)
- `mover_predictions` (the movers scoreboard)
- Bar archive via `ParquetBarStore` for the traded symbols/day (1-min bars)
- The engine log for that day (`logs/launchd-scheduler.err.log`) — count `ENTRY-CTX` evaluations,
  skips, gate rejections, feed-health warnings

Facts must include **session integrity**: did the live session actually run, how many bars, any feed
stalls/reconnects, whether trade count was cap-bound or gate-bound. A review that silently analyses
a dead session is a bug (this literally happened on 2026-07-23: 0 bars, 4,440 reconnect lines).

### 3.2 `forensics.py` — the "intelligent questions", answered with data
For **every trade**, answer (computed, not guessed):
- **Why this stock?** decode `reasons` JSON + the features at entry; which rules fired, what was the
  rule score, was news/tip-mention involved.
- **Was the entry timing good?** From the 1-min archive: where did the entry sit within the bar's
  range and the day's range; what happened in the ±15 min around it.
- **Was the exit right?** Compute **MFE/MAE** (max favourable/adverse excursion) from entry to
  square-off. Then the counterfactuals: what would a wider/tighter stop, a trailing stop, a
  hold-to-time-stop, and the actual optimal exit have produced? (Reuse the validated approach in
  `signal_engine/research/exit_variants.py`.)
- **Did friction decide the outcome?** `cost_pct` vs `|pnl_pct_gross|`; would this trade have won
  gross but lost net?
- **Was it alpha or the tide?** `alpha_pct` vs `sign × nifty_ret_pct`.
- **Was sizing right?** `rupee_risk` vs realised loss; did affordability skips distort the book?
- **The skipped trades matter too:** what did the gates reject, and (from the archive) what would
  those rejects have done? A gate that rejects winners is the most expensive kind of bug.

Aggregate into buckets (exit reason, stop-width band, hour, direction, symbol, rule-combination) —
**always with n, and always flagging when n is too small to conclude.** Refuse to report a
per-bucket "finding" at n<20 without an explicit `LOW-N / NOT ACTIONABLE` tag.

### 3.3 `gap.py` — the 1%/day model (this is what the user actually wants to see)
Given the day's (and trailing) realised win-rate, payoff ratio, trades/day and friction, solve for
what each factor would need to be for **net +1.00%/day on the ₹1L book**:
- required win-rate at the current payoff & trade count
- required payoff at the current win-rate
- required per-trade net edge in R and in ₹
- friction budget: at 15bps round-trip, what fraction of a 1% day is eaten
- and the honest verdict line: e.g. *"1%/day needs 62% WR at the realised 1.5:1 payoff; trailing-20
  WR is 29% (n=61). Gap: 33pp. No parameter change bridges 33pp — this is a signal problem."*
Track the gap **as a time series** so progress (or its absence) is visible over weeks.

### 3.4 `ledger.py` — how the agent gets smarter every day
Append-only, greppable (markdown or a `desk_hypotheses` table — your call; the validated pattern is
`docs/TRADINGAGENTS_ANALYSIS_2026-07.md` §5 S1 / TradingAgents' reflection loop):
- every night's **hypotheses** get an id, a statement, a **pre-registered test**, a falsifiable
  prediction, and a status (`open` / `supported` / `refuted` / `stale`)
- on later nights the agent **grades its own prior hypotheses** against what actually happened, and
  writes a 2–4 sentence lesson citing the realised numbers
- **dead ideas are recorded as dead** so the agent stops re-proposing them (it must read the ledger
  before generating new hypotheses — that IS the "intelligence improves daily" mechanism)
- keep a running "priors" section: what we now believe about this strategy, with the evidence.

### 3.5 `analyst.py` — the reasoning layer
LLM is **optional and off by default**; a deterministic rule-based reviewer must produce a useful
report with zero API calls so the nightly job can never break or surprise-bill. When enabled
(`SE_DESK_LLM=1`), follow the anti-fabrication discipline already validated in
`docs/TRADINGAGENTS_ANALYSIS_2026-07.md` §5 S2 / Appendix:
- **pre-fetch every fact deterministically, then ONE structured call** — never a tool-calling loop
  (their documented failure: an LLM asked to analyse sources it had no tool for **fabricated** them)
- inject a **verified-snapshot block**: every number the prose may cite, computed in code, with the
  instruction that it is the only source of truth and discrepancies must be flagged, not reconciled
- **instrument-identity anchor** (NSE symbols are ambiguous: IDEA, Jindal-family names)
- structured output with a graceful fallback to the deterministic report
- **quick/deep model split + a ₹ cost meter** (`signal_engine/obs/`): if the desk can't justify its
  own API bill, that's a finding too. Use the current Claude model ids — check with the
  `claude-api` skill; do NOT hardcode from memory.
- The prompt must demand: cite n for every claim; distinguish *mechanical/arithmetic* findings from
  *statistical* ones; explicitly say "insufficient evidence" rather than inventing a narrative.

### 3.6 `review.py` — orchestration + outputs
1. `docs/desk/DESK_REVIEW_<YYYY-MM-DD>.md` — the full review (institutional format: exec summary,
   session integrity, P&L attribution, per-trade forensics table, bucket analysis, 1%-gap model,
   hypothesis grading, ranked proposals with effort + evidence bar).
2. A **Telegram digest** (short: net%, gap-to-1%, the one thing that mattered, top proposal) —
   send at **~17:15 IST** (after archive 16:10 / alpha 16:20 / movers 16:40). Reuse
   `send_alert(build_alerter(cfg), ...)`. Keep it honest: no "profit tomorrow" language.
3. `desk_proposals` — machine-readable proposed diffs (file, key, current → proposed, rationale,
   pre-registered evidence bar, expected effect with in-sample caveat). **Never auto-applied.**
4. A `GET /api/desk` endpoint + (optional, only if time) a `/desk` dashboard page showing the gap
   time series and the hypothesis ledger.

### 3.7 Scheduling
Register in `signal_engine/scheduler.py` (there are 13 jobs today; the newest are `movers_alert`
08:50, `microstructure_score` 15:50, `movers` 16:40). Add `desk_review` at **17:15 IST, mon-fri**,
guarded exactly like the others (trading-day check, try/except, never kills the scheduler).
Update `tests/test_scheduler.py`'s expected job-id set.

## 4. Institutional discipline to actually emulate (the Goldman/MS part, honestly)
Not "promise 1%/day" — real desks do this:
- **P&L attribution** every day: alpha vs market beta (we have `alpha_pct`/`nifty_ret_pct`) vs
  friction vs sizing. Know *where* the money came from and went.
- **TCA (transaction cost analysis)**: cost as % of gross, implementation shortfall (plan price vs
  fill), slippage vs model. We have `pnl_pct_gross`/`cost_pct` on every trade — use them.
- **Risk governance**: drawdown limits, per-name concentration, correlated-basket detection
  (same-minute bursts), capacity/fillability limits. Flag breaches; don't just report returns.
- **Pre-trade vs post-trade**: compare what the plan expected (`expected_move_pct`, `risk_reward`)
  with what happened. Systematic optimism is a finding.
- **Kill criteria**: state, up front, what result would make us abandon the strategy. Desks retire
  strategies; they don't nurse them forever.

## 5. Hard constraints (violating any of these is a failed build)
- **PAPER ONLY.** Never place or enable live orders (`BrokerAdapter` has no order method — keep it
  that way). No advice language.
- **Never auto-apply** a config/strategy change. Proposals only.
- **Never claim an edge that hasn't cleared the bar**: `edge_verdict` (n≥2000, OOS, PBO<0.10) +
  DSR/multiple-testing for research claims; ≥10 sessions of live evidence for live-behaviour claims.
- **Never analyse a dead session as if it were real** — session-integrity check first.
- **Python 3.9** (no PEP 604 `X | Y` annotations, no 3.10+ syntax). SQLite migrations follow the
  existing `init_db` ALTER-TABLE-if-missing pattern in `signal_engine/storage/repository.py`.
- Match house style: dense purposeful comments explaining *why*, hand-verified tests with the
  arithmetic in the comments, no emoji in code.
- **Do not restart launchd services during market hours (09:15–15:30 IST)** — that killed a live
  session on 2026-07-14. Off-hours restarts are fine and are how the scheduler picks up new jobs.
- Don't touch: the P0 `max_cost_r` gate value, `live_universe.restrict_to_allowlist` (stays false),
  or the movers sleeve's honesty labels, unless the evidence bar is met and you say so explicitly.

## 6. Definition of done
1. `signal_engine/desk/` implemented as above; **deterministic path works with no LLM**.
2. `tests/test_desk.py` — hand-verified: fact extraction, MFE/MAE + counterfactual math, the
   1%-gap solver (assert the required-WR arithmetic on a worked example), ledger grading,
   low-n guard, session-integrity guard, and a test proving **no proposal is auto-applied**.
3. **Run it for real** on the last live session with trades (**2026-07-22**, 8 trades, −4.43%) and
   for a dead session (**2026-07-23**, 0 bars) — paste both outputs in your report. The dead-session
   run must refuse to draw conclusions.
4. `scheduler` job registered at 17:15 + `tests/test_scheduler.py` updated.
5. **Full suite green** (`.venv/bin/python -m pytest tests/` — was **625 passed** before your
   changes; report the new count).
6. Docs + memory updated: a `docs/DESK_AGENT.md` explaining the design, the two clocks, and the
   honesty contract; append a short entry to `docs/MORNING_HANDOFF.md`; write/refresh a memory file
   (`~/.claude/projects/-Users-vikrantdeshmukh-Personal-projects/memory/`) + MEMORY.md index line.
7. Off-hours scheduler restart so the job is live, and confirm the 13→14 job list in the log.
8. Report: files changed, the two real review outputs, the current 1%-gap number, test counts, and
   **your honest verdict on whether 1%/day is reachable** with what would have to change.

## 7. First-night expectation (set this expectation in your report)
On day 1 the agent will most likely conclude: *the book is losing roughly its charge bill; the
signal has no measurable gross edge; the gap to 1%/day is tens of percentage points of win-rate;
and the highest-value next steps are structural (survivorship-clean data, a genuinely new
information source) rather than parameter tuning.* **If that is what the data says, say it.** The
agent's job is to be the desk's most rigorous sceptic, and to keep a durable, improving record of
what has been tried and ruled out — that is how it earns the right to eventually say
"here is a real edge."
