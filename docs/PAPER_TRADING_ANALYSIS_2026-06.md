# Paper-Trading Analysis — first 2 live sessions (2026-06-24/25, 48 trades)

Adversarial multi-lens analysis of the live `vwap_ema_adx` intraday strategy's first 48 paper trades.
**Honest frame:** 48 trades over 2 sessions — Jun-25 alone is 41/48 trades and ~100% of the loss — so
this establishes almost **nothing** statistically. Only **mechanical, sample-independent** facts are
trustworthy; everything per-symbol/hour/direction/confidence is single-session noise.

## The two robust (mechanical) findings — anchor decisions to these
1. **Transaction cost is the dominant, deterministic leak.** Round-trip cost reconstructs from fills as a
   near-constant **~8.2 bps/trade** (identical 0.0824% on both days) = **+0.217R drag/trade**, ≈ **10.4R of
   the 11.8R net loss**. 48 trades paid ~4% in cost alone. This converts a **near-break-even GROSS book**
   (gross WR 41.7%, gross total −1.39R) into the −11.8R / −2.96% net loss.
2. **The binding constraint is entry hit-rate, not exit geometry.** At the realized 1.39:1 net payoff,
   break-even WR is **41.9%**; actual net WR is **31.2%**. Gross WR (41.7%) sits ~at break-even — so the
   whole net deficit is the cost-driven asymmetry. **Stops are fine** (true overshoot past −1R only −0.09R;
   do NOT widen) and **TIME_STOP is immaterial** (7 trades, −0.07% each).

→ Net = gross-coinflip − cost-tax. This independently re-confirms the arc-wide lesson: **price-only
intraday strategies are ~gross-break-even and the cost wall is what makes them lose.** See
[[edge-research-arc-and-next-step]].

## Suggestive but UNPROVEN (do not act on blindly)
- **Overtrading / burst-clustering:** entries fire in synchronized same-minute bursts (8@09:43, 6@11:14,
  8@14:13) → effective independent bets ≈ 6–10, not 48 → most cost is spent on correlated re-bets.
- **LONG (23% WR) vs SHORT (41% WR):** **not significant** (permutation p≈0.22). The "late-day longs drove
  the loss / removing them flips to +1.7%" is post-hoc selection of a near-zero book — shows where the loss
  sat, not a forward edge.
- **smid/new-IPO names bled, large-caps ~break-even** (within Jun-25): the only credible structural lead,
  but entangled with one afternoon's correlated smid sell-off (13 simultaneous stop-outs ≈ 1 event).
- **Confidence score is non-predictive AND range-restricted** (45/48 ≥70 → gates nothing; corr 0.20, n.s.).
- **ADX was NOT the problem** (Jun-25 was high-ADX, median 35) — so any fix is a *direction/regime* gate,
  not a trend-strength gate.

## Ranked improvements (low-regret first; all need 10–30 more sessions to validate)
1. **[HIGH] Cut trade frequency** — cap entries per burst-minute and max ~2/symbol/day. Directly attacks
   the robust cost leak + the non-independence. Target: cost as % of |gross| from ~80% → <40%.
2. **[HIGH] Treat entry hit-rate (or payoff) as the only lever** — raise the entry bar or widen the target
   so break-even WR drops; do NOT widen stops or extend holds (those are non-problems that only add cost).
3. **[MED] Structural universe gate to large-cap/liquid names** (NOT a confidence/IPO flag) — cheap,
   targets the one suggestive structural lead. Pre-register the large-only cohort to avoid forking-paths.
4. **[MED] Intraday regime/direction filter** — stop firing long breakouts into a falling tape (suppress
   longs below VWAP / negative breadth, vice-versa). Mechanism-level, not yet measured.
5. **[LOW] Demote/recalibrate the confidence score** — it passes 94% of signals and doesn't discriminate.

## What to watch in TODAY's session (3rd data point)
gross-vs-net spread (is the ~8.2bps tax reproduced? is gross actually >0?); same-minute entry bursts &
shared direction; direction-vs-tape alignment; large-cap vs smid avgR on an independent tape; realized WR
vs the 42% break-even; fast-stop (<10min) share; whether high-confidence trades actually win more.

**Bottom line:** the only honest call is *"need ~30+ sessions before edge or its absence is established."*
Until then, make only the low-regret structural moves (cut frequency/cost, gate the universe, leave the
sane stops alone) — don't chase the per-bucket patterns.

## Implemented (config-gated, DEFAULT OFF — 2026-06-26)
Improvements #1 and #3 are wired in but **disabled by default**, so today's session is unchanged. Flip
them on only after a few more clean sessions confirm the watchlist patterns, then restart the scheduler
(`launchctl kickstart -k gui/$(id -u)/com.vikrant.signal-engine-scheduler`) to load the new config.
- **Frequency cap** (`config/risk.yaml` → `risk:`): set `max_entries_per_minute: 2` and
  `max_entries_per_symbol_per_day: 2` (both default 0 = off). Caps live in `EngineRunner._gate_ok` /
  `_flush_pending`; tests in `tests/test_engine.py`.
- **Universe gate** (`config/settings.yaml` → `live_universe`): set `restrict_to_allowlist: true` to trade
  only the pre-seeded large-cap allowlist (drops the 15 smid/new-IPO names: SWIGGY, ETERNAL, NETWEB, IDEA,
  SUZLON, ZENTEC, OLAELEC, …). Applied via `config.resolve_live_watchlist` in the live feed only; an empty
  allowlist safely falls back to the full list. **Review the allowlist before enabling** (pre-register it).
- NOT changed: stops, time-stop, targets (the analysis shows these are fine — changing them only adds cost).

## 3-session gap analysis (2026-06-29 after close, 63 trades, adversarial workflow) — CORRECTS #3
A winner-vs-loser gap analysis across all 3 sessions (incl. today) answers "why do the same rules win on
some trades and lose on others?": **no at-entry feature separates future winners from losers.** Every
feature has AUC ≈ 0.5 (confidence 0.60, RSI 0.37, ADX 0.44, R:R 0.44, stop% 0.50, rules-fired 0.58),
none significant, none survives multiple-testing; the *same stock* wins and loses under identical rules
(9/18 multi-trade names did both; GMRAIRPORT lost a long AND a short); and "won" is mechanically just
"hit TARGET before STOP" — a post-entry path event, unpredictable from the entry signal. The win/loss
split is irreducible noise around a cost-driven small loss; the whole 3-session loss reduces to ONE cell
(06-25 longs entered 10-12, n=14, −4.86% = 93% of total) — strip it and it's −0.38% / 37% WR (break-even).

**Correction to improvement #3 (universe gate): NOT justified on this evidence.** The large-vs-smid gap is
noise — permutation p=0.26/0.78, 74% of the smid loss is just 3 tickers (PPLPHARMA/GMRAIRPORT/SWIGGY), it
sign-flips across the two sessions, and HONASA (smid) was the best name (+3.9%). A large-only gate would be
overfit to 3 names + one session — **leave `live_universe.restrict_to_allowlist` OFF.** Time-of-day and
LONG-vs-SHORT splits were likewise killed as one-session artifacts. **Only the frequency cap (#1) remains
justified** (it attacks the robust ~8bps cost leak + correlated bursts). Need ~30 sessions before any
separator claim. (Workflow: `paper-gap-analysis`; dataset exported to scratchpad/gap_dataset.json.)

## Session 4 fold (2026-06-30, 13 trades) — nothing changes; one-name day, gated changes still OFF
Folded by the morning-verify task (as-of 14:35 IST; all 13 trades already closed, none after 11:46, so the
session is effectively final — any 14:35–15:30 entries, if any, are a top-up for tomorrow's run to confirm).
Dataset: `scratchpad/session_2026-06-30.json`. Strategy `vwap_ema_adx`, config-gated changes still DEFAULT OFF.

**Headline:** 13 trades, **3 wins (23% WR)**, avgR −0.22, **net +1.88%** — but the entire gain is **one name,
OLAELEC** (3/3 TARGET hits, +5.46% combined). **Strip OLAELEC → 0 wins / 10, −3.58%.** Same single-name
dominance the prior sessions showed (06-25 = one cell was 93% of the *loss*; today one name is 100%+ of the
*gain*). A coin that lands on OLAELEC. n=13, one session → noise, not edge.

What the doc said to watch in "today's session" — every prior finding **reproduced**:
- **Confidence non-predictive AND range-restricted — CONFIRMED (again).** All 13 entries are conf ≥0.85
  (gates nothing). Worse, every conf=1.00 trade (ETERNAL, IRFC, AXISBANK, KAYNES, IDEA) **lost**; the only
  winners were conf=0.90 (OLAELEC). Higher confidence did *not* predict wins — if anything inverted.
- **Correlated same-minute bursts — CONFIRMED.** 09:42 fired **4 simultaneous entries** (3 LONG + 1 SHORT);
  1 win, 3 losses. 4 of 13 entries in one minute = the non-independence the frequency cap (#1) targets. The
  `max_entries_per_minute: 2` cap would have throttled exactly this — still the one justified fix.
- **Universe gate (#3) still NOT justified — re-confirmed, harder.** OLAELEC is a smid/new-IPO name *on the
  drop-list*, and it was today's **sole winner**. `restrict_to_allowlist: true` would have deleted the day's
  entire positive contribution and left −3.58%. Leave it OFF.
- **LONG vs SHORT:** LONG 33% WR/+1.51; SHORT 0/4/+0.37. Short 0% WR is n=4 noise (shorts still net-positive
  via two positive TIME_STOPs). No signal.
- **Fast-stop <10min:** 3 trades, +3.49 net, 2 wins — fast resolution was *good* today (OLAELEC targets in
  2–3 min), not a leak. Consistent with "stops/holds are non-problems."
- **Gross-vs-net 8.2bps tax:** can't reproduce this session — DB stores only `pnl_pct_net` (no gross column),
  so the cost-spread check needs the plan-reason join used in the 3-session workflow. Prior finding stands;
  not re-measured today.

**Bottom line (4 sessions):** unchanged. No at-entry feature separates winners from losers; results are
irreducible noise around a cost-driven small P&L, periodically masked or unmasked by one dominant name. The
only justified, still-DEFAULT-OFF change is the **frequency cap (#1)**; the **universe gate (#3) stays OFF**
(today's evidence argues against it outright). Still need ~30 sessions before any edge/no-edge call is final.
