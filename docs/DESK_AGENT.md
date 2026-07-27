# The Desk — nightly quant-desk review agent

> Built 2026-07-26 from `docs/PROMPT_2026-07-26_NIGHTLY_QUANT_DESK_AGENT.md`.
> Code: `signal_engine/desk/`. Tests: `tests/test_desk.py`. Job: `desk_review` @ 17:15 IST mon-fri.
> Outputs: `docs/desk/DESK_REVIEW_<YYYY-MM-DD>.md`, a Telegram digest, `GET /api/desk`, and rows
> in `desk_hypotheses` / `desk_proposals` / `desk_gap` / `desk_reviews`.

---

## 1. What it is

Every trading night at 17:15 IST the desk reads the day's paper session and writes an
institutional-format review: session integrity, P&L attribution, TCA, risk governance, per-trade
forensics with counterfactual exits, bucket analysis, the arithmetic gap to the user's +1.00%/day
target, a graded hypothesis ledger, and ranked **proposals**.

It is the answer to: *"an agent that runs every night, analyses the paper trading, asks
intelligent questions like why we chose that stock and the entry/exit, gets smarter every day, and
improves the strategy until we hit 1% net per day — think like Goldman/Morgan Stanley and act like
them."*

Acting like an institutional desk means attribution, TCA, kill criteria, and retiring strategies
that do not clear costs. It does **not** mean promising 1%/day. The one reframe applied to the ask
is the two-clock split below; everything else is built as specified.

## 2. The two clocks (the core design decision)

| | Nightly clock | Gated clock |
|---|---|---|
| Cadence | Every trading night, 17:15 | Only when an evidence bar is met |
| Scope | Analysis, interrogation, attribution, hypotheses, **written proposals** | Changes to `config/risk.yaml`, `config/settings.yaml`, strategy params |
| Who acts | The agent | **The human, always** |
| Guarantees | Deterministic, no network, no LLM required, cannot break the scheduler | Config-gated, DEFAULT OFF, validated on archive replay and/or >=10 shadow sessions |

Taken literally, *"improve the strategy every day"* means re-tuning live parameters nightly on
4-8 trades — a guaranteed overfitting machine. This repo has the scars to prove it: the universe
allowlist looked great on 3 names / 1 session and was killed **three times**; the
"max 5 trades/day -> +6.9%" counterfactual was pure arrival-order artifact; the LONG-vs-SHORT and
time-of-day splits both reversed out of sample. So the nightly clock produces *proposals with
pre-registered tests*, and the gated clock (a human, following the P0 precedent in
`docs/STRATEGY_IMPROVEMENT_PLAN_2026-07.md` §3) applies them.

**The desk auto-applies nothing.** Enforcement is structural, not aspirational:

* `desk_proposals.applied` is hard-coded to `0` on insert and `DeskStore` exposes **no method
  that sets it to 1**.
* `signal_engine/desk/` contains no YAML write, no `yaml.dump`, no `os.environ[...] = ...`, no
  `setattr(cfg, ...)`, and exactly one write-mode file open (the markdown report).
* `analyst.FORBIDDEN_PROPOSAL_KEYS` additionally drops any proposal touching `risk.max_cost_r`
  (the live P0 gate), `live_universe.restrict_to_allowlist` (killed three times), or the movers
  honesty labels.
* `tests/test_desk.py::test_no_proposal_is_ever_auto_applied` asserts all three by scanning the
  package source with regexes and reading back the persisted rows.

## 3. Module layout

```
signal_engine/desk/
  facts.py        deterministic fact-gathering (NO LLM, no network) + SESSION INTEGRITY
  forensics.py    per-trade interrogation: MFE/MAE + counterfactual exits from the bar archive
  attribution.py  P&L decomposition, TCA, risk governance, buckets — with hard low-n guards
  gap.py          the +1.00%/day solver: required WR / payoff / per-trade edge, three scopes
  ledger.py       append-only hypothesis + proposal store, pre-registered grading, priors
  analyst.py      the reasoning layer: deterministic reviewer + ONE optional LLM call
  review.py       orchestration -> markdown report + Telegram digest + persistence
signal_engine/obs/llm_cost.py    ₹-denominated LLM cost meter
```

Order of operations in `review.run_review`:

1. `gather()` — facts, **integrity first**.
2. **Grade yesterday's hypotheses before generating tonight's** (so new ideas are written in the
   light of what was just refuted — this is the "gets smarter every day" mechanism).
3. If the session is not usable: emit an ops-only review that **refuses to conclude**, and stop.
4. Otherwise: interrogate every trade, decompose, solve the gap, generate hypotheses + proposals,
   optionally add one LLM commentary block, write the report, persist, build the digest.

## 4. The honesty contract

Six rules, each enforced somewhere in code:

1. **Session integrity gates everything.** On 2026-07-23 the live session logged **0 bars and
   1,426 websocket reconnects** (the Dhan Data-API subscription had lapsed) while every EOD job
   still ran and the dashboard still rendered. A review that analysed that day would have
   manufactured a finding out of an outage. `SessionIntegrity.usable` is False for `DEAD` /
   `NO_SESSION`, and the report then **omits** the attribution, forensics, bucket and gap
   sections — not empties them, omits them, and says so.
2. **Every claim carries n** (and the session count where relevant). `Finding` has no way to
   render without it.
3. **Mechanical vs statistical is explicit.** Arithmetic identities and config facts are labelled
   `[MECHANICAL]` and are true at n=1; distributional claims are `[STATISTICAL]` and are
   non-actionable until they clear their floor.
4. **Low-n findings are tagged NOT ACTIONABLE.** Buckets need n>=20 (`MIN_N_FOR_BUCKET`);
   live-behaviour claims need >=10 sessions (`MIN_SESSIONS_FOR_LIVE_CLAIM`); research claims need
   the standing `edge_verdict` bar (n>=2000, OOS, PBO<0.10, DSR). Dimensions this project has
   already killed (`hour`, `direction`) carry their own gravestone when they light up again.
5. **The gap model says plainly when the gap is not closable by parameter work**, and
   `closable_by_parameter_work` is False unless the win-rate gap is <=5pp.
6. **No advice, no forecasts, no "profit tomorrow" language** — in the report or the digest. A
   test asserts the banned phrases are absent from the digest.

## 5. Units — the one thing that is easy to get wrong

Three different percentages appear in this system and they are not interchangeable:

| quantity | meaning | where it is right |
|---|---|---|
| `pnl_pct_net` | % of the **position**, per trade | per-trade tables, R-multiples |
| Σ `pnl_pct_net` | size-blind sum | what `engine/runner._LossBreaker` uses for the daily cap |
| **book return** | equity-weighted, `Δequity / opening equity` | the truth about the day |

On 2026-07-22 those were **-4.43%** and **-2.59%** respectively, because positions ran at 30-85%
of the book. The gap model works exclusively in **book-%** (`gap.book_returns`), because the
target is a book target; the report shows both and names the divergence.

## 6. The three gap scopes

* **session** — tonight only. Always low-n; shown because the user asked for a nightly number.
* **gated** — sessions on/after `2026-07-13` (the P0 friction-in-R gate go-live). **The only
  window that describes the engine as it is actually configured**, and the one the pre-registered
  P0 bar is written against. This is what the digest quotes and what hypotheses are graded on.
* **trailing** — the last ~20 sessions. Spans the pre-P0 regime (~15 trades/day, stops to 0.33%)
  and pre-ledger rows with no ₹ columns, so it is flagged `MIXED REGIME` / `DATA QUALITY` and is
  context, never a verdict.

## 7. The optional LLM layer

Off by default. `SE_DESK_LLM=1` enables **one** structured call, following the patterns validated
in `docs/TRADINGAGENTS_ANALYSIS_2026-07.md` §5 S2 / Appendix:

* pre-fetch every fact deterministically, then a **single structured call** — never a tool loop
  (their bug #984: an LLM asked to analyse sources it had no tool for fabricated them);
* a **verified-snapshot block** with every citable number computed in code, and an instruction to
  **flag** discrepancies rather than reconcile them;
* an **instrument-identity anchor** (NSE tickers are ambiguous — IDEA, the Jindal family — and
  their #814 was a silent company substitution mid-run);
* structured output (`output_config.format`) with a graceful fallback to the deterministic report;
* a quick/deep model split (`SE_DESK_LLM_QUICK` / `SE_DESK_LLM_DEEP`, default
  `claude-haiku-4-5` / `claude-opus-5`) and a **₹ cost meter** (`signal_engine/obs/llm_cost.py`)
  that prints the bill in every report and warns if it exceeds 2% of a 1%-of-book day.

The deterministic path is not a degraded fallback — it produces the entire review. The LLM adds
three paragraphs: what mattered, the strongest counterargument, and the highest-value next
evidence. If the call fails for any reason the review is byte-for-byte unaffected apart from a
one-line note.

## 8. Ops

```sh
# manual run for any day (prints report + digest, writes docs/desk/, does NOT send Telegram)
.venv/bin/python -m signal_engine.desk.review 2026-07-22 --no-send

# with the Telegram digest
.venv/bin/python -m signal_engine.desk.review 2026-07-22

# dashboard/API
curl localhost:8000/api/desk | jq '.latest_review, .gap_series.gated[0]'
```

Scheduled: `desk_review` at **17:15 IST mon-fri**, after archive (16:10), alpha (16:20) and
movers (16:40) so the bar archive and benchmark columns are already written. Guarded like every
other job — misfire grace 1h, coalesce, try/except; a failure can never kill the scheduler.
**Never restart the scheduler between 09:15 and 15:30 IST** (that killed a live session on
2026-07-14).

## 9. What the desk found on night one (2026-07-22)

Recorded here because it is the calibration point for everything after it.

* Book **-2.59%** (₹97,824 -> ₹95,293) on 8 trades; per-trade %-sum -4.43%.
* Decomposition: gross **-3.77%** = tide +0.20% + alpha **-3.98%**; friction 0.66%. The picks,
  not the tide and not the charges, lost this particular day.
* **Concentration is structural, not incidental:** largest position **85% of the book**, mean
  42%, effective breadth ~2.4 names, and **18 affordability skips** — the book ran out of cash
  while setups were still firing, so trade selection was decided by cash exhaustion, not signal
  quality. `max_concurrent_positions: 4` never bound. Cause is arithmetic:
  `risk_per_trade_pct 0.5% ÷ P0-mandated stop >=0.57% ≈ 88% of equity per position`.
* The **daily drawdown breaker halted the session at 13:25 on a size-blind number** (-4.43%
  summed per-trade) while the book's real give-back was -2.59% against a 4.00% cap. A
  measurement bug in a risk gate, independent of edge.
* Gap to +1.00%/day, gated scope (8 sessions, 41 trades): needs a **71% win rate** at the
  realised 1.33:1 payoff and 5.1 trades/day; realised is **27%**. **Gap: 44pp.**
* Friction alone eats **22%** of a 1% day. All-in friction is **0.216R per trade** at the median
  0.66% stop.

Verdict written that night: not closable by parameter work.

## 10. Kill criteria (stated up front, as a desk would)

1. At the 10-gated-session look, if cost share of |gross| is under 40% **and** the book is still
   net-negative, the verdict is on the **signal**, not the plumbing — stop tuning `vwap_ema_adx`
   gates and exits. (Already pre-registered in `scripts/preregistered_eval.py`.)
2. If trailing avgR's bootstrap 95% CI is still entirely below 0 at n>=318 trades, retire the
   strategy from the live paper book and keep it only as a cost/ops harness.
3. If the desk's own proposals produce no measured improvement over 30 sessions, the nightly
   review is decision-support theatre — cut it to weekly.
4. Live now: when the gap is unreachable at **any** win rate given the realised payoff and trade
   count, continuing to tune parameters toward +1%/day is not a plan.

## 11. Extending it safely

* **A new metric** must go in `ledger.METRICS` before a hypothesis can be graded against it. A
  hypothesis that cannot be phrased against one of those metrics must not be opened — un-gradeable
  hypotheses are how a ledger turns into a diary.
* **A new counterfactual** goes in `forensics._counterfactuals` with a fixed, pre-chosen
  parameter. Do not sweep. `WIDER_MULT`/`TIGHTER_MULT` are round numbers precisely so this cannot
  become a stop-width optimiser.
* **Never** add a code path that writes config. If a future night "needs" one, the answer is a
  proposal with a diff.
