# End-of-Day Analysis & Improvement Plan — 2026-06-25

## 0. The one-line verdict (read this first)

Today we lost **−4.55%** (41 trades, 34% WR, PF 0.63) because a pure trend-follower bought a gap-up that faded all session. The diagnosis is solid and the code bugs are real. But this strategy has **no proven cost-surviving edge** (prior research: 1,693 intraday variants, none cleared the ~14 bps wall). Therefore the EV-dominant action is **not** to re-tune six gates hoping for green — it is to **cut gross exposure toward the floor and run the real fixes in shadow** until they clear out-of-sample. Everything below is in service of *losing less and measuring more honestly*, not manufacturing edge we have not found.

---

## 1. What happened today

The market gapped up (~+1.05%, briefing called RISK_ON) and faded all session — a textbook distribution day (HONASA 420→432→417; TCS topped 2148, closed −1.77%; RELIANCE peaked 1326 at 10h, drifted down all day). Our strategy is a pure trend/momentum follower, so it bought the morning pop, got chewed up on the reversal, then *accidentally* aligned with the established downtrend in the afternoon shorts.

**Key numbers:**
- 41 trades, **34% win rate**, **net −4.55%**, **PF 0.63**
- LONG **3/21 wins, −5.06%** vs SHORT **11/20 wins, +0.51%**
- By hour: 09–11h = 4/25, **−5.44%**; the 11:00 block alone **1/11, −4.08%**; 14h = 10/16, **+0.89%**
- Confidence is **non-predictive**: the 90–100 band (47% WR) *beat* the 75–89 band (20% WR)
- Frictionless gross ≈ **+1.29%** (≈ +0.03%/trade) — the raw price moves were ~flat; **the loss is the cost wall (~14 bps round-trip × 41 trades ≈ 5.8% of friction) plus the regime working against the longs.**

> **Caveat the eye must not skip:** every headline split here is **one observation of one gap-fade day, n=41**. The 21-vs-20 direction split is a single session. We treat it as a *hypothesis generator*, not as established fact — see §3.

---

## 2. Root causes (ranked, data-grounded)

1. **Direction-vs-regime failure (the bulk of the loss).** The system fought a mean-reverting tape with longs. LONG −5.06% vs SHORT +0.51%. Every LONG entered ≥10:30 lost (12/12, −5.87%); the only LONG winners (HONASA +1.22, +1.56) were entered 09:43–10:03, before the fade was confirmable. *Single-day caveat applies — see §3.*

2. **Correlated-basket concurrency, not oversizing.** `max_concurrent=8` is direction-blind. 8 positions fired at 09:43 (6 long), 6 at 11:14 (4 long). The 11:14 burst bought the noon top en masse and went 1/11. Eight "independent" slots were really one macro LONG bet. Per-trade sizing (0.5% risk) was fine; the damage was firing correlated losing longs together.

3. **No regime awareness in code.** The engine computes `regime_trend` (signed slope), `orb_high/low`, and `vwap_upper/lower`, but the strategy reads **none** of them (`required_indicators()`, `vwap_ema_adx.py:54-58`). Its only regime gate is a sign-blind `adx≥15`, which reads "strong" precisely during gap-up exhaustion.

4. **The cost wall on a no-edge signal.** ~14 bps × 41 trades ≈ 5.8% of guaranteed friction. Avg net win +0.552% vs avg net loss −0.455% (ratio 1.21) needs ~62% WR to break even; we printed 34%.

5. **Stops/targets are symptoms, not causes.** Stops cluster near a 2×ATR output on low-ATR names (median 0.359%); realized RR ≈ 1.3, not the nominal 2.0. Real, but secondary — the loss is direction/regime, not stop width.

6. **Confidence is noise.** It ranks scarce slots on clustered minutes, so it actively mis-allocated fills (a conf-100 pick was the single worst loser). Tuning the confidence threshold cannot fix expectancy.

---

## 3. The honest verdict (including what we do *not* know)

**This is a no-edge strategy.** Prior exhaustive research (1,693 intraday variants) found no cost-surviving edge on this universe; today's frictionless gross of ≈ +0.03%/trade confirms it. **Nothing below turns PF 0.63 into PF > 1.**

Three honesty checks that constrain the whole plan:

- **The direction thesis rests on n=41, one regime.** LONG 3/21 vs SHORT 11/20 is *one* gap-fade day. We are building B1/B3/B4 on a single-session split — the same small-n skepticism we apply to the old config's "n=4 luck."
- **The SHORT "win" is mostly a selection artifact.** The 11h–14h gap was a **feed halt** (breaker tripped on replayed warm-start losses, fixed mid-session). The 14h SHORTs (10/16, +0.89%) are a **post-restart cluster that entered after the fade was fully established** — i.e. trend-following that finally aligned with a confirmed down-leg, not evidence that "shorts have edge." Strip that cluster and the symmetric "shorts are fine, only longs are the problem" framing weakens considerably. **A direction filter on a zero-edge signal has expected value ≈ 0**, minus the cost of misclassifying the regime: it removes a left-tail on fade days *and* symmetrically removes a right-tail on gap-up-**continuation** days where those same longs would have won.

What the improvements actually do, separated honestly:

- **Real edge: none available right now.** The one *research candidate* is VWAP mean-reversion on range days — see §4(c)/C5 for the explicit caveat that it must be reconciled against the already-falsified 1,693-variant search before earning "candidate" status. Research, not plan.
- **Loss reduction / variance control (real, but not profit):** capping correlated concurrency, fewer trades, a loss breaker that actually binds, a *shadow* regime filter. These shrink **variance and drawdown** on bad days — and symmetrically clip the upside on the days the basket would have won. **Lower variance on a negative-EV signal is still negative EV.**
- **Cosmetic / observability (zero P&L, high value):** removing confidence from ranking, logging entry context, gross-vs-net reporting. Worth doing for honesty and to make any future edge *findable*.

**Bottom line: we can lose less and lose more legibly. We cannot promise profit.** The honest baseline option — which dominates any config tweak on EV grounds — is **§4(d): flat / minimum-size / research-only until B1 and C5 clear OOS.**

---

## 4. Improvement plan for tomorrow

> **Process-safety rule (binds everything below):** measurement is this strategy's only asset. **Change one decision variable per session** where possible. If we change the slot *count* (A1/A2) *and* the slot *selector* (B2) on the same day, we cannot attribute the result to either. Stagger them — see §4(a) note.

### (a) Ship tonight — config & discipline (low effort, variance-reduction only)

| # | Change | Why | Honest effect | Type |
|---|--------|-----|---------------|------|
| A1 | **`max_concurrent_positions: 8 → 4`** (`config/risk.yaml`) | Bounds the documented 6–8 simultaneous correlated entries (09:43/11:14). The most *robust*, regime-agnostic lever. | Caps single-minute cluster size — **but also clips the upside** on days the basket wins. Variance ↓, EV unchanged. | variance control |
| A2 | **`max_trades_per_day: 25 → 15`** (round default, **not** 12) | Fewer trades → less guaranteed friction. **Caveat:** a hard *clock-order* count cap inherits the same defect as max_trades=4 — it clips whichever trades come last (today that was the *green* 14h block). So this is a crude friction limiter, **not** a quality filter; the real "fewer, better trades" lever is the B1 gate, not a count cap. | Less cost bleed; possible mis-clip of late trades. | exposure control (crude) |
| A3 | **Switch the breaker to drawdown-from-peak** (`_LossBreaker.record`, `runner.py:59-71`), cap **`daily_loss_pct: 6.0 → 4.0`** | The 6% cap never fired despite a −7.81% intra-session trough because it reads *netted realized* PnL. Trip on `(peak − realized) ≥ cap` instead. **Use 4.0 as a round, robustness-justified value — NOT 3.0.** 3.0 was explicitly tuned to *permit today's specific recovery path*, which is single-path overfitting and a mild risk-*loosening* on a no-edge book. Prefer a breaker that binds sooner over one tuned to a hoped-for rebound. | Bounds worst-day drawdown; mechanism is the win, the constant is just "round + binds." | catastrophe-cap |
| A3′ | **(Preferred over A3 if cheap to build): size-down on drawdown** instead of a hard halt | On a no-edge book you want to *de-risk* as drawdown grows, not bet on your own P&L mean-reverting. Halve size at −2% peak-drawdown, quarter at −3.5%. | Smooth de-risking dominates a single tuned cutoff. | catastrophe-cap (preferred) |
| A4 | **Cooldown arms on ANY losing exit** (`_on_position_closed`, `runner.py:458`) | Bug: cooldown only arms on `STOP`, so SWIGGY re-entered 1 min after a TIME_STOP loss. Keep the window at **10 min** (don't widen — that's overfit). | Stops same-name cost-doubling churn. | discipline |
| A5 | **Keep `confidence_threshold` at 75 as a default — do NOT credit it.** | Non-predictive (90–100 band beat 75–89). At conf≥75 the day is still −4.39%. | None. Anti-cosmetic-fix safeguard. | discipline |

> **Attribution note:** ship A1 + A3/A3′ + A4 tonight (pure risk/discipline). Hold **B2 (ranker change) for a *separate* session** so the slot-count change and the slot-selector change don't confound each other.

> **REJECTED as overfit (do not ship):** (i) reverting to selective conf-75 / **max_trades=4** — its "win" today is pure chronology (it would have capped out the green afternoon SHORTs and kept the morning losers); prior +₹3,185 was n=4 luck. (ii) **max_trades=12** — same chronology-clipping defect at lower resolution; 15 is a rounder, less day-fit value and we make no quality claim for it. (iii) **Symbol blacklists** (SWIGGY/ETERNAL) — that's a *direction* failure misattributed to the symbol; on a trend-up day those names are where the gains are.

### (b) Code changes — earn before you trust (ship default-OFF + shadow first)

| # | Change | Why | Status |
|---|--------|-----|--------|
| B1 | **FADED_GAP session classifier → suppress fresh LONGs only.** In `paper/trader.py`, tag the day FADED_GAP when, over a 30–45 min post-open window, price is **sustained** below ORB-low AND below VWAP (majority of bars) with `regime_trend<0`. While tagged, block new LONG entries; leave shorts and open positions alone. **Implementation gap to fix:** B1 reads ORB/VWAP-band primitives the strategy currently does **not** request — must add `orb_high/low`, `vwap_upper/lower`, `regime_trend` to `required_indicators()` (`vwap_ema_adx.py:54-58`) or it will read NaN at the gate. | The highest-leverage *loss-reduction* lever — it targets the −5.06% LONG-into-fade book using only causal primitives (no look-ahead). | **Shadow/log-only.** ⚠️ The often-quoted "flips −4.55% → +1.33%" counterfactual is an **in-sample artifact of a rule reverse-engineered from this one day — it is NOT an expected uplift and must not be cited as evidence of value.** Validate over *many* sessions that it (i) cuts longs on real fade days AND (ii) **stays dormant on gap-up-continuation days** before any live gating. Estimate the fade-vs-continuation base rate first — that base rate, not today's path, determines whether B1 is net-positive at all. Gap-down mirror ships **disabled**. |
| B2 | **Remove confidence from ranking/tie-break.** In `runner._rank` (`runner.py:333`) drop the `* confidence` multiplier → rank by net-of-cost `(target − breakeven)/stop`. In `scan/ranking.score_plan` drop `conf_norm`. Do **not** touch the admission gate (`confidence_threshold`). | On clustered minutes, confidence (noise) decided which fills got slots. | Ship — **but in its own session** (not the same day as A1/A2). ⚠️ The replacement selector (cost-adjusted R) is itself **unvalidated**; combined with a trade-count cap it could preferentially keep losers. **Add a measurement:** log whether the surviving (capped) trades out- or under-perform the ones cut. P&L-neutral housekeeping, not profit. |
| B3 | **Directional concurrency sub-cap** `max_concurrent_per_direction`, default = overall cap (**OFF**). | Caps the "one giant correlated bet" tail. | ⚠️ **Not a loss-reduction lever** — a symmetric per-direction cap clips a *winning* correlated basket exactly as hard as a losing one. It is **variance-neutral-to-negative**; do not file it under "safety." Leave OFF; if ever enabled, validate on a trend-up day first. |
| B4 | **Add `regime_trend` to the entry gate** (opt-in, `regime_align_min` default **0.0 = OFF**). Add to `required_indicators()`; when enabled, block LONG when `regime_trend < −k`, SHORT when `> +k`. | Wires in a computed-but-discarded feature. | **Do NOT ship a tuned k.** `regime_trend` is a 20-*minute* slope; today's fade was *hourly*, so at 11:14 the short-window slope was still positive — it would **not** have blocked the burst. Default OFF until walk-forward proves a constant. |

> **REJECTED code ideas (overfit / mis-premised):** (i) **VWAP-extension veto k=1.5** — kills the day's *best* trade (HONASA 10:03 +1.56% was itself extended) and misses the worst (SWIGGY entered ~at VWAP); ship only as a *logged diagnostic*, no gating. (ii) **Hardcoded `no_entry_windows: ['11:00-11:30']`** — curve-fit to one day; keep the mechanism as an ops kill-switch, default EMPTY. (iii) **"Honest target = RR×stop + breakeven"** — false reading of the code (targets are ATR-derived; costs already gate entry); widening targets on chop just lowers hit-rate. (iv) **ORB-confirmation "fix"** — there are no breakout entries to confirm. (v) **Revocable premarket bias via `scoring.py`** — alert-text only, changes zero trades.

### (c) ML / research track (no live impact tomorrow — instrument, then test)

| # | Change | Why |
|---|--------|-----|
| C1 | **Re-label the meta-model on net-of-cost outcome**, slippage included (`CostModel(cfg.risk.costs, cfg.risk.slippage)` in `ml/dataset.py`), `min_net_edge_bps ≈ 14`. | Current label ("hit 2R before stop") is economically meaningless — it routes capital to cost-negative trades. Label-honesty, not edge. |
| C2 | **Wire the strong promotion gate.** Gate the real `model.save()` in `ml/train.py` behind `edge_verdict` (n≥2000, WR≥0.52 or PF≥1.10, PBO<0.10) over walk-forward. | Current criterion (auc>0 & brier>0 on one split) would promote noise. The expected, *correct* outcome is that the model **fails** the gate. |
| C3 | **Log per-trade entry context** into the durable SQLite row (`ALTER TABLE ADD COLUMN`): at-entry `regime_trend`, `ext_atr`, `dist_to_orb`, side-of-VWAP, `adx`, `rvol`. Stamp the `faded_gap` day-tag **end-of-day** (it's same-day lookahead). | Prerequisite to ever validate B1/B4. 41 trades is far too small; we need multi-day, multi-regime data. |
| C4 | **Add `regime_alignment = regime_trend × direction_sign`** as a feature (plumb direction into train+serve), retrain shadow-only. | Lets the linear model see "with vs against trend" directly. Shadow only. |
| C5 | **Research probe: VWAP mean-reversion on range days** via `evaluate_signal`, PBO gate, net of 14 bps, OOS. | ⚠️ **Status caveat:** before calling this an "edge candidate," confirm whether VWAP-MR was **inside or outside** the already-falsified 1,693-variant search. If it was inside, it is *already disproven* and this is a re-test of noise. Only if it is genuinely **outside** the prior search space does it qualify as an untested candidate — and even then, paper/shadow only until it clears the wall OOS across many sessions. |

### (d) The honest baseline — the option that dominates on EV

> On a strategy **proven to have no cost-surviving edge**, re-tuning six gates is optimizing the cockpit of a car with no engine. The EV-dominant move is to **reduce gross exposure toward zero** until something clears OOS:
> - **Run live at minimum size (or paper-only)** while B1 logs in shadow and C5 is tested — collect the multi-day, multi-regime evidence with the smallest possible cost bleed.
> - Promote back to normal size **only** when (a) B1 demonstrably cuts fade-day longs *and* stays dormant on continuation gap-ups across several sessions, or (b) C5 clears the 14 bps wall OOS.
>
> This is not defeatism; it is the correct sizing for a signal whose measured frictionless edge is ≈ 0. **Trading smaller is the highest-EV change on this list.**

---

## 5. Recommended config for tomorrow

```yaml
# config/risk.yaml
max_concurrent_positions: 4        # was 8 — break up correlated baskets (robust, variance-only)
max_trades_per_day: 15             # was 25 — round friction limiter, NOT a quality filter (not 12)
daily_loss_pct: 4.0                # was 6.0 — AND switch breaker to drawdown-from-peak (mechanism is the fix; 4.0 is a round, binds-sooner value, NOT tuned to today's path)
max_consecutive_losses: 10         # unchanged — redundant with daily cap; 5 is overfit
per_symbol_cooldown_minutes: 10    # unchanged — but arm cooldown on ANY losing exit (code A4)
# PREFERRED if buildable: size-down on drawdown (A3′) instead of a single hard cap

# config/settings.yaml
confidence_threshold: 75           # sane default ONLY; non-predictive, not a quality lever
adx_min: 25                        # conservative default (UNPROVEN; log adx/rvol to test, don't credit)
rvol_min: 1.3                      # conservative default (UNPROVEN)

# New filters — ship DEFAULT-OFF / shadow:
faded_gap_long_block: shadow       # B1: log-only; gate live only after multi-day validation
regime_align_min: 0.0              # B4: disabled until walk-forward proves a constant
max_concurrent_per_direction: 0    # B3: OFF (= overall cap); variance-only, not a safety lever

# Sizing posture:
position_size_mult: <=1.0          # (d) run at minimum viable size / paper-only until B1 or C5 clears OOS
```

**Reasoning:** The only levers that genuinely bind are `max_concurrent:4` (caps the correlated bet) and the **drawdown-based breaker** (bounds the worst case). `max_trades:15` trims friction but is *not* credited with picking winners. The confidence/adx/rvol values are conservative defaults, explicitly **not** credited with selecting quality. The real candidate fix (suppress counter-regime longs) runs in **shadow**, and overall **size stays minimal** — because the dominant EV move on a no-edge book is to bleed as little as possible while we gather evidence, not to curve-fit tomorrow's config to today's path.

---

## 6. What to measure tomorrow

1. **By-direction P&L and WR** (LONG vs SHORT) — but record it as *one more data point* toward a multi-day split, not as confirmation. Did blocked/fewer counter-trend longs show up?
2. **Shadow FADED_GAP log:** did the tag fire? on which bars? P&L of the LONGs it *would* have blocked vs the ones it kept — **and** did it stay dormant on any trend-up stretch? (This, across many days, is how B1 earns live status. Also begin tallying the **fade-vs-continuation base rate** of gap-ups.)
3. **Gross vs net decomposition per session:** gross PnL, statutory cost, modeled slippage, net. Confirm gross stays ≈ 0 (the no-edge signature) so we don't mistake a cost loss for a signal loss — or vice-versa.
4. **Concurrency heat:** max simultaneous positions and same-direction count per minute. Did `max_concurrent:4` actually prevent the basket?
5. **Daily-loss breaker:** did it arm? at what drawdown-from-peak? did it correctly leave open positions running? (If A3′ shipped: did size step down at −2%/−3.5%?)
6. **Cooldown:** any same-name re-entry within 10 min of a losing exit (should now be zero, incl. TIME_STOPs)?
7. **Ranker-change audit (when B2 ships, its own session):** did the cost-adjusted-R selector keep the better or the worse half of the capped candidates?
8. **Confidence vs outcome correlation** (logged, not acted on): keep confirming it's ≈ 0 so we never re-trust it.
9. **Trade count and total cost paid** vs today's 41 / ~5.8% — is the bleed materially smaller?

**Success criterion, stated honestly:** a *smaller, more legible* loss (or a small gain only if the regime cooperates), with the FADED_GAP shadow log showing it would have cut the LONG bleed **and** stayed dormant on a trend-up stretch — accumulated across enough sessions to matter. We are measuring whether we **lose less and lose more honestly**, on minimal size — **not** whether we found edge. We have not.

---

**Files referenced:** `config/risk.yaml`, `config/settings.yaml`, `signal_engine/engine/runner.py` (`_LossBreaker.record` @59-71, `_rank` @333, `_gate_ok`, `_on_position_closed` @458), `signal_engine/strategies/vwap_ema_adx.py` (`required_indicators()` @54-58), `signal_engine/risk/manager.py`, `signal_engine/paper/trader.py`, `signal_engine/scan/ranking.py`, `signal_engine/ml/dataset.py`, `signal_engine/ml/train.py`, `signal_engine/ml/evaluate.py`, `signal_engine/indicators/__init__.py`.