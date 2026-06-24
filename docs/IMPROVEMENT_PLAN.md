# Signal Engine — Prediction & Execution Improvement Plan **FINAL** (v3)

*Lead author: senior quant-engineer synthesis of v1 + v2 + two red-team passes. Status: PLANNING ONLY (no code changed; this doc lives in /tmp, not the repo). System is paper / decision-support — a human places every order, it NEVER auto-trades. Honest baseline: the VWAP/EMA/ADX entry has **NO proven edge** (real-archive PF ~0.45–0.61 < 1; rules-confidence is non-predictive). Organizing principle (unchanged from v2): **prove (or disprove) an edge cheaply FIRST; gate all execution/UX/model build-out on that result.** What this version adds: a **real critical-path DAG**, a **hard MUST-HAVE / NICE-TO-HAVE cut**, a **code-enforced + pre-committed** go/no-go, **quantitative per-hypothesis edge gates**, and a **live-slippage audit** before any paper→live step.*

---

## 0. What changed from v2 (and from v1)

v1's flaw: polished machinery around an edgeless strategy. v2 fixed the strategy by adding edge-discovery (S), capital-preservation (M), and a go/no-go gate — strategically correct. But v2 was **operationally undercooked**: the "~1 week" Phase 0 hid a serial critical path, listed 8 V-items + 6 S-items + 4 M-items as if all were blocking, left the go/no-go gate soft (three vague pivots, no commitment), and gave the edge search no sample-size discipline. v3 keeps v2's structure and closes those holes.

| # | Change from v2 | Why (which red-team finding) |
|---|---|---|
| F1 | **Phase 0 re-scoped into a critical-path DAG, relabeled "~1.5–2 weeks" (10 working days).** V1 (wire `walk_forward_windows`) is genuinely L on the current code (it is dead: referenced only in `backtest/__init__.py` + tests — verified) and blocks everything. v2's "1 week" was a fiction that derails by Wednesday. | RT1: timeline understated; critical path hidden. |
| F2 | **V1 now runs BEFORE V0.** Wire the harness on the UNPATCHED index-split first so you SEE the leak (AUC inflated, label-shuffle still high), then apply V0 and watch AUC collapse. This is concrete proof the fix worked, not a synthetic before/after. | RT1: V0 can't be validated until V1 is live. |
| F3 | **S-workstream split into MANDATORY {S0, S2, S5} and OPTIONAL {S1, S3, S4}.** Mandatory = ~1 day total. Optional only run if a mandatory probe shows a signal worth conditioning on. Locks the S critical path at 1 day, not 4. | RT1/RT2: S scope ambiguous; 6 items dressed as "3–4 days." |
| F4 | **Per-hypothesis edge gate made quantitative and code-enforced.** An edge is "found" ONLY if: n ≥ 2,000 post-embargo samples, win-rate ≥ 52% (or PF ≥ 1.10), PBO < 10%, AND it holds on a recent trailing-6-month holdout. Implemented as `ml/evaluate.py::edge_verdict()`, called after every S-item. No clearance ⇒ "no edge." | RT2 (blocker): S had no sample-size discipline → a weak "no edge" that invites "we didn't search hard enough." |
| F5 | **V5 (trial registry + PBO) runs BEFORE S0–S6, ex-ante.** Hypotheses registered in append-only JSONL before they're tested; PBO computed per item. This is pre-registration, not post-hoc multiple-testing patchwork. | RT1: V5 ordering unspecified (ex-ante vs post-hoc). |
| F6 | **Go/no-go is PRE-COMMITTED and code-enforced.** Before Phase 0 starts, the user picks ONE pivot (a/b/c) and it is written to `config.py` as `PHASE0_DECISION: Enum(GO \| NO_GO_NARROW \| NO_GO_SWING \| NO_GO_PAPER)`, committed to `main`. A NO-GO mechanically activates that branch with a 3-month moratorium; no re-evaluation. | RT1 + RT2 (blocker): soft gate with three escape hatches → sunk-cost iteration under the guise of "narrowing." |
| F7 | **M-layer re-sequenced. M0+M1 = Phase 0. M2 (beta) needs NIFTY ingestion → moved to Phase 1. M3 (kill-switch) is live-only → Phase 1, automated in code (not advisory).** v2 falsely listed all four as Phase-0 work. | RT1: M2 needs NIFTY (not in code), M3 needs 20 live trades that don't exist yet. |
| F8 | **A4, A5, A6 CUT until the system is actually profitable.** A4 (prior-day levels) is incremental on A3; A5 (trailing) is L for marginal gain; A6 (partials) doubles costs on an unproven book. Each carries an explicit re-entry gate (below). | Both RT passes: knobs on an unproven signal; A6 doubles costs for no proven edge. |
| F9 | **D3 (unify alert paths) + D4 (structured content) demoted to Phase 2 optional. D1 + D1b + D2 are the only Phase-1 alert MUST-HAVEs.** D1+D2 fix the echo symptom alone; D3 is refactor. | Both RT passes: D3/D4 are refactor/UX, not the fix. |
| F10 | **B1/B3 explicitly scheduled to Phase 3+ (week 6+), unblocked only by ≥30 days of recorded ticks.** Tick recorder still built in Phase 0 and run live in parallel, but B1/B3 validation is not scheduled until the data exists — and is deferred entirely if NO-GO. | RT1: 30 days of ticks = 4–5 weeks; B1/B3 timing was floating. |
| F11 | **V3 cost wiring + V8 promotion gate given concrete implementation + thresholds.** V3 = modify `breakeven_pct()` to add 2×slippage, add `slippage_scalar` knob, require PF>1 at scalar=2.0 (0.06%). V8 = numeric forward-shadow protocol (below). Plus a **live-slippage audit** (10–20 shadow trades, re-baseline) before paper→live. | RT1/RT2: V3 had no impl/test plan; V8 thresholds vague; 0.06% was an unvalidated guess. |
| F12 | **D1b ranking formula specified.** `rank = (t1_pct − cost_pct) / stop_pct × confidence`; surface top-N where N = `max_trades_per_day`; deterministic tie-break; snapshot-tested. | RT2: ranking left unspecified → unimplementable/unvalidatable. |
| F13 | **V0 hardened with a post-fix temporal-integrity check.** After the global date split: Spearman autocorr of sample index vs `ts` ≈ 0, and label-shuffle AUC must land in [0.48, 0.52]. Optional deterministic-seed shuffle if latent symbol-order correlation remains. | RT2: simple date split may leave symbol-order clustering. |

Net effect: v2's optimistic "~1 week" Phase 0 becomes an honest **~2-week** edge-first go/no-go with a hard MUST-HAVE core and a binding abort. We still save the 2+ months v1 would have spent polishing a non-edge.

---

## 1. TL;DR + the single decisive next step

**TL;DR.** Three observed symptoms (clustered ~1% targets, echo alerts, late entries) all have confirmed, cheap root-cause fixes — but they are cosmetic relative to the real problem: **the entry has no demonstrated edge, and the only metric that suggested otherwise (archive ML AUC) is contaminated by a cross-symbol temporal leak.** Verified: `ml/dataset.py::build_dataset_from_archive` appends each symbol's entire multi-year history in list order; `train.py::_train_on_dataset` (lines 73–78) then splits by sample *index* (`n_train = n*(1-test_frac)`), with `embargo_frac=0.01` against a ~90-bar label horizon. The docstring claims "chronological out-of-sample split" — the archive path violates it. Until the split is fixed and a disciplined edge search is run, every downstream number is noise.

**The single decisive next step (DO THIS FIRST — it is the critical-path root, ~3–5 days, item V1 then V0):**

> **Wire the dead `walk_forward_windows` harness into a real archive gate FIRST — on the current UNPATCHED index split — so the leak is visible (inflated AUC, label-shuffle that does NOT collapse to 0.5). THEN apply the global date split (V0) and re-run: AUC should crash and label-shuffle should fall into [0.48, 0.52]. In parallel (day 1, cheap), register hypotheses in the trial registry (V5) and run the mandatory edge probes S0 (does the OPPOSITE of the rules signal win?) + S2 (time-of-day) + S5 (volume-spike) through `edge_verdict()`.**
>
> This answers the only question that matters: *is there an edge to build on, or not?* I expect the current config to **FAIL** (PF < 1) on the corrected gate — that failure is the gate working, not a setback.

Everything in §3–§4 is gated on that answer, and on a pivot you pre-commit **now** (§6 Q1).

---

## 2. Confirmed root causes (verified this session)

### 2.1 Symptom 1 — clustered, formula-driven targets *(confirmed)*
`risk/manager.py:64-77`: `stop_pct = clamp(2.0×atr_pct, min_stop_pct=0.50, 3.0)`; `t1_pct = stop_pct × target_rr(2.0)`; `expected_move_pct = t1_pct` (line 77 — an output, never an input). NSE liquid names have ATR% ~0.15–0.25%, so `2.0×atr_pct` ≈ 0.30–0.50% → floored to 0.50% → every target = 0.50×2.0 = **1.0%**. The `min_stop_pct` floor quantizes the 19-name watchlist onto one bucket. **Fix: A1.**

### 2.2 Symptom 2 — echo alerts *(confirmed; red herring confirmed)*
`runner.py:193-196` calls `advisor.update()` and alerts **before** the gates at `201-206` (`_symbol_free`, `max_trades_per_day`, `max_concurrent_positions`). Verified: only `max_trades_per_day` and `max_concurrent_positions` are read (runner.py:203,205); the 4×-duplicate DB rows are a cross-run sqlite artifact (no `run_id`), NOT a runner dedup bug — do not build runner-level dedup. **Deeper cause: capacity.** 19–20 names into a 4–6 trade/day cap means most candidates are structurally non-actionable. **Fix: D1 (gate-before-advisor) + D1b (ranking).**

### 2.3 Symptom 3 — late / reactive entries *(confirmed)*
Three stacked latencies: signal on the closed 1-min bar (`runner.py:145,184`), fill on next bar's open + 0.03% slippage (`trader.py`), reactive indicators. **Reframe: the retail fix is not tick-perfect fills — it is alerting the human to a level 5–10 bars early so they can rest a limit.** B2 + early-alert framing, not B1 arm-and-fire.

### 2.4 The real problem — no proven edge + contaminated validation *(the crux, verified)*
- **Circular label:** `_label` = first-touch of `plan.t1`, and `plan.t1` is itself the formula target. The model learns "did it hit the number we chose," never "how far / which way."
- **Trained only on rule-approved bars** (`dataset.py` candidate gen via `strategy.on_bar`): inherits the rules' blind spots.
- **Cross-symbol temporal leak (BLOCKER, verified):** per-symbol append + index split → train on early-list symbols' recent bars, test on late-list symbols' old bars. Temporal inversion.
- **Embargo is 1% of samples** (`train.py` `embargo_frac=0.01`) against a ~90-bar horizon — far too small, count-based not interval-based.
- **`walk_forward_windows`/`time_split` are dead code** (verified: referenced only in `backtest/__init__.py` + `tests/test_walkforward.py`; never called by train/cli/backtest).
- **Cost gate excludes slippage** (verified: `costs.py:23-29` — "accepted for forward-compatibility but is not applied"; `breakeven_pct()` omits it) → the true hurdle is understated.
- **`daily_loss_pct=2.0` defined but never read** (verified: `config.py:62`; only `max_trades_per_day`/`max_concurrent_positions` are enforced in `runner.py`). Capital preservation is unbuilt.
- **`position_size` is a bare helper** (`risk/sizing.py:14`) with no enforcement, no qty in alerts.
- **Live `ml_gate=0`** (shadow) — good: the leaked model is NOT gating live trades; do not enable it until the split is fixed.

---

## 3. Revised workstreams

Effort: S(<½d) / M(1–2d) / L(3–5d) / XL(>1wk). Impact: H/M/L.
**Acceptance bar:** median walk-forward PF > 1 AND > 60% of windows PF > 1, on the corrected split, net of **0.06%** slippage (see §5). Pure-discipline items may ship PF-neutral-or-better; sizing/target/signal items require PF **strictly** improved.

### (V) Validation & honesty — *prerequisite; nothing else is trusted without it*

| # | What | Effort | Impact | Phase | OOS validation |
|---|---|---|---|---|---|
| **V1** | **Wire `walk_forward_windows` into a real archive gate — on the UNPATCHED split first.** Default CLI to `--source archive`. | L | H | **0 (day 1–3, BLOCKS ALL)** | `run_archive_walkforward`: replay each ≥20-day test window through a fresh runner; report **median PF + % windows PF>1**. On the unpatched split, expect inflated AUC + label-shuffle that does NOT collapse → demonstrates the leak. |
| **V0** | **Global date split + label-shuffle control + post-fix temporal check** (BLOCKER) | M | H | **0 (day 3–4)** | Thread entry-bar `ts` through `Dataset`; split by calendar date across ALL symbols (`ts < cutoff` train, `≥ cutoff+embargo` test). Re-run V1 harness: permuted-y collapses CV-AUC to [0.48, 0.52]; Spearman autocorr(index, ts) ≈ 0; if not, add deterministic-seed shuffle. Compare pre/post AUC to quantify the leak. |
| **V2** | **Embargo = per-sample label-interval purge** (not sample-%) | M | H | **0 (day 4–5)** | Carry `[t_entry, t_exit]` (loop already computes `end`); drop any sample whose label window straddles the cutoff. |
| **V5** | **Trial registry + PBO + deflated Sharpe — STAND UP BEFORE S0–S6** | M | M | **0 (day 1)** | Append-only JSONL of config-hash/knobs/PF/n_trials, written **ex-ante** per hypothesis. PBO < 10%, deflated Sharpe > 0.5 before any promotion. |
| **V3** | **Cost realism: wire slippage into breakeven; `slippage_scalar` knob** | M | H | **0 (day 5)** | Modify `CostModel.breakeven_pct()` to add **2×slippage_pct** (round-trip); add `slippage_scalar` config (default 1.0). Single config sweep scalar=[1.0, 2.0, 3.3] (=0.03/0.06/0.10%); **require PF>1 at scalar=2.0 (0.06%)**. Test: assert `breakeven_pct` rises by exactly 2×slippage. |
| **V4** | **NULL baselines: random-entry, always-long/short, always-flat** through identical machinery | S | H | **0 (day 5)** | Edge must beat random-entry by **≥0.1 PF**; same gates/limits/costs → equal opportunity. |
| **V6** | **Regime-diversity + corpus audit** | S | H | **0 (day 1, parallel)** | Verify corpus spans ≥2–3 trend/chop/vol regimes; require ≥1 train/test regime *mismatch* or "across regimes" is vacuous. |
| **V7** | **Train/serve-skew check + CA audit** | S | M | **0 (parallel, precursor)** | Single shared feature path for dataset & live; assert dataset vs live `compute_features` bit-identical. CA audit: scan parquet for day-over-day gaps >20%; **acceptance: <5% days flagged per symbol, OR all flagged days cross-checked vs known NSE split dates.** Gates A-levels and C1. |
| **V8** | **ONE promotion gate + numeric kill-criteria + live-slippage audit** | L | M | **1+ (after a found edge)** | See protocol below. |

**V8 protocol (concrete).**
1. **Offline pre-req:** median walk-forward PF > **1.02** OOS (conservative buffer over 1.0), beat rules on purged-CV AUC/Brier/ECE, PBO < 10%.
2. **Live-slippage audit (NEW, before any promotion):** shadow 10–20 live trades; log actual fill vs modeled (next-bar-open + slippage); compute realized slippage% per trade. Promote only if realized slippage ≤ modeled (model pessimistic = fine). If realized is worse, re-run backtest at the realized slippage; if PF drops below 1.0, **halt promotion**.
3. **Forward shadow:** minimum **50 live trades or 5 trading days**, whichever is greater.
4. **Kill if any 2-of-3:** rolling-10-trade PF < **0.70** for 2+ consecutive days; realized slippage > **1.5× modeled** for 2+ consecutive days; win-rate outside the backtest **95% CI** for 10+ trades.
5. **Manual re-enable only** after review; thresholds changeable only via PR.

### (S) Signal / edge discovery — *NEW; the part v1 lacked; answers "is there an edge?"*

**Mandate (locks the critical path):**
- **MANDATORY (~1 day total, run day 1 in parallel with V1):** S0, S2, S5.
- **OPTIONAL (only if a mandatory probe shows a conditionable signal; each 1–2d):** S1, S3, S4. If no mandatory edge clears `edge_verdict()`, do NOT run the optional items — that IS the answer.

| # | What | Effort | Mand? | OOS validation |
|---|---|---|---|---|
| **S0** | **Opposite-signal probe** (2h) | S | ✅ | How often does the *inverse* of the rules direction win? ~50/50 ⇒ direction is noise → reframes the whole project. |
| **S2** | **Intraday seasonality / time-of-day** (<½d) | S | ✅ | Bin: open 09:15–10:00 / mid 11:00–13:30 / close 14:30–15:30; win-rate & realized vol per bin; gate signals where bin win-rate < 52%. |
| **S5** | **Volume-spike / RVOL booster** (2h) | S | ✅ | `volume_spike_pct = today_vol/avg_20 − 1`; win-rate when >50% vs baseline. |
| S1 | Overnight-gap continuation vs fade | M | optional | `gap_pct`; long/short win-rate by gap_up/down/size on honest split. |
| S3 | Opening-range breakout / reversion | M | optional | First-30-min range; breakout vs fade win-rate by regime. |
| S4 | Momentum vs mean-reversion by regime | M | optional | ADX>25 trend vs BB-width chop; win-rate by regime. |
| S6 | Permutation feature importance (after V0) | S | optional | On rules features; does AUC collapse on top-3-only? |

**`edge_verdict()` gate (code-enforced, `ml/evaluate.py`, called after every S-item).** An edge is "found" ONLY if ALL hold:
1. **n ≥ 2,000** post-embargo samples (a 52% win-rate on 500 samples has 95% CI [48%, 56%] — indistinguishable from noise; 2k tightens it).
2. **win-rate ≥ 52% OR PF ≥ 1.10** on the honest split.
3. **PBO < 10%** (survives multiple-testing across all registered hypotheses).
4. **Holds on a trailing recent-6-month holdout** (decay check).
If NO hypothesis clears all four ⇒ declare **"no directional edge discovered"** and activate the pre-committed NO-GO branch.

**Optional post-pass (only if no single factor clears, NOT a blocker):** cross-tabulate the top-2 univariate factors (e.g. gap_up ∩ ORB_break); if the intersection shows >52% on ≥500 samples, log for Phase-1 feature engineering. This catches interaction edges univariate scans miss.

### (M) Capital preservation & portfolio risk — *NEW; survival layer*

| # | What | Effort | Impact | Phase | OOS validation |
|---|---|---|---|---|---|
| **M0** | **CapitalManager: fixed-fractional / vol-target sizing (0.25×Kelly cap)** | M | H | **0** | Extend `risk/sizing.py` (today: bare `position_size`, no enforcement); emit qty + rupee-risk + notional; show qty in Telegram alert. |
| **M1** | **Enforced daily-loss circuit breaker** | S | H | **0** | Track cumulative session PnL%; halt new entries when `daily_loss_pct=2.0` (already in `config.py:62`, currently UNREAD) breached, or rolling-10-trade PF<0.7. |
| **M2** | **Portfolio correlation / beta cap** | M | H | **1 (needs NIFTY ingestion)** | Rolling beta vs NIFTY; reject entry when one-sided concurrent beta concentration > 0.5. *Requires NIFTY symbol ingestion, which is NOT in current code → Phase 1 precursor, not Phase 0.* |
| **M3** | **Live degradation kill-switch (automated in code, not advisory)** | M | M | **1 (live-only; needs ≥20 live trades)** | Rolling-20-trade PF, realized-vs-modeled slippage, win-rate vs 95% CI; auto-halt entries on 2-of-3 breach for 5 trades; manual resume. |

### (A) Adaptive targets & exits

| # | What | Effort | Impact | Status |
|---|---|---|---|---|
| **A1** | **Decouple stop floor from target driver** | S | H | **Phase 1** |
| **A3** | **Structure-level features (VWAP±kσ, ORB, round numbers)** | M | H | **Phase 1 (feeds A2)** |
| **A2** | **Structure-capped target (`expected_move` as INPUT)** | M | H | **Phase 2, gated on A3 + proven baseline** |
| A4 | Prior-day H/L/C | M | M | **CUT.** Re-entry gate: only if A1+A3 ship AND CA audit (V7) clears AND A2 is PF-proven. Incremental on A3. |
| A5 | Structure + ATR-trailing stops, breakeven @0.6×T1 | L | M | **CUT.** Re-entry gate: ships only if **A2 improves median walk-forward PF by >0.08** (strict). |
| A6 | Partial profit-taking (half at T1) | M | M | **CUT.** Re-entry gate: only if A2 improves PF by **>0.10** AND partial-leg net PnL is positive at 0.5×T1 **after doubled costs**, AND system is at stable PF>1.2. |

**A1** — keep a hard 0.20% *safety* floor; decouple it from the target driver. The baseline every fancier idea must beat. *Validate:* target_pct stdev rises, PF not worse; first run a stop-out-frequency sensitivity (0.20–1.50%) — the equilibrium floor on 1-min is likely ~0.30–0.40%, not zero.
**A2** — `t1_pct = min(vol_target = target_atr_multiple×atr_pct, structure_distance − buffer)`; stop derived independently; edge-cost gate stays a *rejection* gate. **Requires A3 first.** *Validate:* PF **strictly** improved OOS AND realized-MFE right tail not truncated (R-multiple histogram).
**A3** — point-in-time-safe levels; *validate:* tag each trade by capping level; drop any level whose hit-rate ≈ random.
**DROPPED in (A):** per-symbol / per-regime calibration tables (19×3 = 57 tiny cells, overfit). At most a universe-wide open/mid/close vol multiplier, and only if S2 shows the structure exists.

### (B) Execution & alerts — *reframed for human-in-the-loop*

| # | What | Effort | Impact | Status |
|---|---|---|---|---|
| **B-tick** | **Tick/quote recorder (LTP + best bid/ask + signed vol → parquet)** | M | H | **Phase 0 precursor (no behavior change); run live in parallel** |
| **B2** | **Honest limit-fill model (trade-through, no-fill-on-gap)** | M | H | **Phase 2** |
| **B-early** | **Early-alert framing: "SETUP at <level>" + optional manual limit** | S | H | **Phase 2** |
| B1 | ~~Arm-and-fire on tick~~ | L | — | **CUT → Phase 3+; unblocked only by ≥30 days of recorded ticks (week 4–5 earliest)** |
| B3 | ~~Provisional-bar sub-minute re-eval~~ | M | — | **CUT → Phase 3+ (unvalidatable on 1-min archive; whipsaw)** |

**B-tick timing (explicit):** build in Phase 0, start running live immediately. Do NOT schedule B1/B3 validation until 30+ days of ticks exist (week 4–5+). **If go/no-go = NO-GO, the tick recorder is deferred to whichever pivot is chosen** (it is not wasted only under GO).
**Rationale:** on a 1-min OHLCV archive, "arm at 193, fire on tick" is checked as "is 193 in [low, high]?" — over-fills wicks (good fill in backtest, no-fill live). The honest, backtestable version: the **human** bears fill risk. Ship B2 (limit fills on trade-through only, report unfilled count) + "SETUP at 193 — rest a limit if you choose."

### (D) Alert quality & dedup — *highest impact-per-effort*

| # | What | Effort | Impact | Phase |
|---|---|---|---|---|
| **D1** | **Gate-before-advisor: suppress non-actionable alerts** | S | H | **1 (mandatory)** |
| **D1b** | **Candidate ranking: surface only top-N actionable** | M | H | **1 (mandatory)** |
| **D2** | **Per-symbol debounce + price-band hysteresis on RE-RATE** | S | H | **1 (mandatory)** |
| D3 | Unify both alert paths behind one per-symbol AlertState | M | M | **2 (optional refactor; waits for concurrent `ml/*`+`indicators/*` edits)** |
| D4 | Structured alert content (expected move, key level, R:R, qty) | S | M | **2 (optional UX; after D1/D2 validated)** |
| D5 | ~~Telegram editMessageText~~ | M | — | **DEFER** |

**D1** — move gate checks (`runner.py:201-206`) *before* `advisor.update()` (`193-196`); call advisor only when the plan passes all gates.
**D1b (ranking spec):** `rank = (t1_pct − cost_pct) / stop_pct × confidence` (risk-adjusted potential × conviction). Surface top-N where **N = `max_trades_per_day`**. Tie-break: reverse-symbol alphabetical (deterministic). Implement in `runner.py::_surface()`; snapshot-test the ranking order on a historical day. This addresses the *capacity* root cause.
**D2** — uses `bar.ts` (event time) for replay determinism; validate >50% RE-RATE reduction across *multiple* days with zero loss of REVERSAL/INVALIDATION.

### (C) Predictive model — *only after an edge is found*

| # | What | Effort | Impact | Status |
|---|---|---|---|---|
| C0 | Decouple candidate generation from rules | M | H | Conditional on S0/V0 |
| C1 | Vol-scaled triple-barrier + signed MFE/MAE labels | M | H | Foundation |
| C4 | Regime + rel-strength-vs-NIFTY features | L | M | Backtestable now (needs NIFTY ingestion; coordinate with M2) |
| C3 | Calibrated direction model (isotonic + ECE gate) | M | M | Payload |
| C2 | ~~Magnitude head sets target~~ | XL | — | **DROP until C3 proves a direction edge OOS** |

**C0** — only after V0 + S0. If label-shuffle AUC is ~0.5 on the rules-gated set, decoupling just gives the model more noise to fit; broaden candidates only if S found a real conditional edge.
**C1** — kill the circular label: (a) ±k·σ triple-barrier from *trailing* ATR; (b) signed MFE/MAE in ATR units; shared ATR function with live (V7).
**C3** — gate on **calibration + PnL**, not AUC: win-rate must rise monotonically with confidence decile, AND the calibrated model wired into paper must beat structure-only A2 on PF at realistic costs. Avoid the "calibrated 55/45 but 0.55×T1 − 0.45×stop − costs < 0" trap.
**C2** — DROP. Magnitude on an edgeless 1-min entry collapses to rescaled ATR (reproducing the clustering) or fits noise.

---

## 4. Edge-first phased roadmap with go/no-go gates

### ★ PRE-PHASE-0 (binding, before any code) ★
**User pre-commits ONE pivot** (§6 Q1), written to `config.py` as `PHASE0_DECISION` enum (`GO | NO_GO_NARROW | NO_GO_SWING | NO_GO_PAPER`, initially unset) and committed to `main`. If Phase 0 returns NO-GO, the system executes that branch mechanically — no re-evaluation for 3 months. Also answer §6 Q3 (capital inputs) so M0/M1 are concrete.

### Phase 0 — Honesty + edge + survival (CRITICAL PATH ~10 working days; the go/no-go fortnight)

**This is a DAG, not a parallel free-for-all. The serial spine is V1 → V0 → V2.**

```
Day 1      Day 2      Day 3      Day 4      Day 5      Day 6-10
─────────────────────────────────────────────────────────────────
[V1 wire harness on UNPATCHED split ........]                        ← BLOCKS EVERYTHING
                              [V0 global split + temporal check ...]
                                            [V2 interval embargo ..]
                                                       [V3 cost][V4 null]
PARALLEL (independent, day 1+):
[V5 registry] [S0][S2][S5 mandatory probes ~1d] [V6 regime] [V7 CA audit]
[M0 sizing][M1 daily-loss breaker]   [B-tick recorder build → run live]
                                                       [DB run_id isolation]
IF mandatory S clears edge_verdict():  [S1/S3/S4 optional, 1-2d each]
```

- **Serial spine (blocks all):** V1 (day 1–3) → V0 (day 3–4) → V2 (day 4–5). V3/V4 attach at day 5.
- **Truly parallel (no dependency on the spine):** V5, V6, V7, M0, M1, B-tick, DB `run_id`.
- **S-mandatory (S0/S2/S5):** ~1 day, run day 1 once V5 registry exists; re-scored on the corrected split once V0 lands.
- **S-optional (S1/S3/S4):** only if a mandatory probe clears `edge_verdict()`.

> ### ★ GO / NO-GO DECISION GATE (end of Phase 0) — PRE-COMMITTED + CODE-ENFORCED ★
> Evaluate the current config + any discovered edge on the corrected walk-forward:
> - **GO** if: a hypothesis (rules or S-discovered) shows **median PF > 1 AND > 60% windows PF > 1** at 0.06% slippage, **beats null baselines by ≥0.1 PF**, clears `edge_verdict()` (n≥2k, WR≥52% or PF≥1.10, **PBO < 10%**, holds on recent-6m holdout), and holds across ≥2 regimes. → set `PHASE0_DECISION=GO`, proceed to Phase 1 **on that specific edge only**.
> - **NO-GO** if: best PF < 0.90 across >70% windows AND nothing clears `edge_verdict()`. → the pre-committed `PHASE0_DECISION` branch (NO_GO_NARROW / NO_GO_SWING / NO_GO_PAPER) **activates mechanically**; commit it to `main`. **Binary, not revisited for 3 months absent new data.** No "orange zone," no "narrow and iterate" — the branch was chosen before emotions were involved.

### Phase 1 — Quick wins (~3–4 days; only if GO)
`D1` → `D1b` (ranking) → `D2` → `A1` (decoupled floor) → `A3` (structure levels). `M2` (portfolio beta — includes NIFTY ingestion) and `M3` (live kill-switch, after first ~20 trades) land here. Validate each *individually* (not bundled) on multi-day walk-forward at PF-neutral-or-better.

### Phase 2 — Adaptive targets/exits & honest execution (~1–2 weeks; conditional on Phase 1)
`A2` (structure-capped target, bar = PF strictly improved) → `B2` (honest limit fills) → `B-early` (early-alert framing). Optional: `D3` unification (after concurrent `ml/*`+`indicators/*` edits settle), `D4` structured content. **A4/A5/A6 only if their re-entry gates (§3 A-table) are met. B1/B3 stay cut.**

### Phase 3 — Predictive model + microstructure (~2–4 weeks; conditional on Phase 2 + a found edge)
`C0` → `C1` (vol-scaled labels) → `C4` (regime + NIFTY rel-strength) → `C3` (calibrated direction, ECE+PnL gated). **`C2` stays dropped.** `B1`/`B3` become validatable here **only if ≥30 days of B-tick data have accrued.** Everything gated by V8 (incl. live-slippage audit).

**Dependency chains:** V1 blocks V0 blocks V2 blocks all OOS claims; A2 needs A3; B1/B3 need 30d tick data; C3/C4 need V0–V2; M2 needs NIFTY ingestion; M3 needs live trades; the whole roadmap needs the Phase-0 GO.

---

## 5. Validation, cost & risk gates

- **PF bar:** "proven" = median walk-forward PF > 1 AND > 60% windows PF > 1, corrected split, net of 0.06% slippage. Discipline items (A1, A3, D*) may ship PF-neutral-or-better; signal/sizing/target items (S*, A2, and any re-admitted A5/A6) require PF **strictly** improved.
- **Per-hypothesis edge gate (`edge_verdict()`):** n≥2,000, WR≥52% or PF≥1.10, PBO<10%, holds on recent-6m holdout. Mandatory for every S-item.
- **Walk-forward, not single split:** `test_size ≥ 20` days; corpus spans ≥2–3 regimes with ≥1 train/test mismatch (V6).
- **Leakage controls:** global date split (V0); label-shuffle collapses CV-AUC to [0.48, 0.52]; Spearman autocorr(index, ts)≈0; embargo = per-sample label-interval purge (V2).
- **Multiple-testing:** every hypothesis logged ex-ante (V5); PBO<10%, deflated Sharpe>0.5 before promotion.
- **Null baselines:** beat random-entry and always-flat through identical machinery by ≥0.1 PF.
- **Cost realism (V3):** `breakeven_pct()` adds 2×slippage; `slippage_scalar` knob; require PF>1 at scalar=2.0 (0.06%); test asserts breakeven rises by exactly 2×slippage.
- **Train/serve skew (V7):** dataset and live `compute_features` bit-identical on the same input.
- **Capital preservation (live precondition):** sizing live-validated, daily-loss breaker enforced (M1), portfolio beta cap active (M2) before any paper→live consideration.
- **Promotion (V8, single pre-registered gate):** offline (PF>1.02 OOS + beat rules) → **live-slippage audit (10–20 trades, re-baseline; halt if PF<1 at realized slippage)** → forward shadow ≥50 trades / ≥5 days → enable. **Kill (any 2-of-3):** rolling-10-trade PF<0.70 for 2+ days, slippage>1.5× modeled for 2+ days, win-rate outside 95% CI for 10+ trades. Thresholds changeable only via PR.
- **Stay paper-only** until PF>1 OOS on this gate. Run the current config through it first — it should **FAIL**; that failure confirms the gate works.

---

## 6. Open decisions for the user

1. **Phase-0 abort branch (BINDING, decide NOW before Phase 0 starts):** pre-commit ONE pivot, written to `config.py::PHASE0_DECISION` and committed — (a) **NO_GO_NARROW**: 1–2 playbook system, open-only 09:15–10:00 + close 14:30–15:30, backtest that; if PF<1 on narrow, fall to (c); (b) **NO_GO_SWING**: multi-day horizon (less timing-noise); (c) **NO_GO_PAPER**: paper-learning-only for 3 months while the tick recorder accrues data. *My recommendation: pre-commit (a), with (c) as its fallback.*
2. **Edge-search scope:** approve the **1-day mandatory** S-sprint (S0 opposite-signal, S2 time-of-day, S5 volume) before any target/timing work? Optional S1/S3/S4 run only if a mandatory probe clears `edge_verdict()`.
3. **Capital inputs (needed before M0/M1):** account capital, per-trade risk %, daily max-loss % (default 2.0)?
4. **Tick recorder — build in Phase 0, run live now?** Cheap, no behavior change, the precondition for all future microstructure work; deferred (not wasted) under NO-GO.
5. **Watchlist scope vs throughput:** 19–20 names vs 4–6 trades/day — ranking + top-N (D1b), or shrink the universe to 8–10 highest-conviction names?
6. **CA-adjusted history:** is the 5-yr parquet split/bonus-adjusted? If unknown, approve the V7 audit (acceptance: <5% days flagged/symbol). Gates A-levels, C1.
7. **DB run isolation:** add a `run_id` column (or separate replay sqlite) so `trade_plans` is analyzable and the false "dedup" artifact disappears?
8. **Concurrent-edit coordination:** another agent is editing `ml/*` and `indicators/*` — confirm ordering so D-/A- refactors of `runner.py`/`advisor.py` don't collide.
9. **Acceptance bar:** agree on **PF strictly improved** (not merely neutral) for any signal/sizing/target change, given each adds code + data risk to a marginal system?
