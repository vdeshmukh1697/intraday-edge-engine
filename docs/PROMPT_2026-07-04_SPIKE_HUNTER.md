# Working brief — "Spike Hunter": predicting large single-day up-moves in Indian equities

> Role for every agent: **senior NSE/BSE trading analyst + buy-side quant researcher.** This shop
> *banks negative results* — an honest "this doesn't clear" is a win, hype is a firing offense. Be
> concrete (formulas, data sources, effect sizes), and pressure-test every idea against the walls
> below. Repo: `/Users/vikrantdeshmukh/Personal projects`. Paper/research only — no live orders.

## The question (user's words, sharpened)
"Many stocks suddenly rise 5–10% or even 20% in a day / hit the upper circuit after news or an
announcement. How do we identify **which** stocks and **which day** will give a huge up-spike?"

Formal objective: design a method to flag, **ex-ante (before the open, or intraday-early)**, names with
an elevated probability of a large **up**-move today (thresholds: ≥5%, ≥10%, ≥20% / upper-circuit), and
**honestly assess whether it is tradeable** after India's frictions.

## Why this framing is interesting (the reframe to exploit)
The project's best prior signal (announcement-day jump-drift, PEAD) was **REAL but not tradeable**: the
alpha lived on the **short leg of mid-caps**, walled off by India's short-sale constraints (no SLB depth,
no overnight cash shorts, non-F&O names un-shortable). See `docs/RESEARCH_HANDOFF.md` /
`docs/PEAD_SPREAD_FINDINGS.md`.

**Up-spike hunting is LONG-ONLY → it side-steps the #1 blocker that killed PEAD.** That is the reason to
take this seriously. But it hits a *new* wall:

## The walls this idea must clear (do not hand-wave any of these)
1. **Upper-circuit fillability.** A stock locked at the upper circuit has *no sellers* — you often
   **cannot buy** at the printed price. Predicting "will hit upper circuit" ≠ "can profit from it." The
   tradeable version must get positioned **before** the lock, or target names with *room to run but not
   lock* (wide/next-to-no band). Quantify: NSE/BSE price bands (2/5/10/20%), T2T, ASM/GSM, SME 5% bands.
2. **Surprise is by definition hard to forecast.** Scheduled catalysts (earnings, board meetings, order
   wins) are *dateable* but the *direction/magnitude* is the hard part. Separate "when a catalyst lands"
   (calendar, easy) from "which way and how big" (the actual prediction).
3. **Survivorship / selection.** Sampling ex-post spikers is trivially biased; the ex-ante base rate of a
   ≥10% up-day is low → severe class imbalance. Every number must be point-in-time and demeaned by the
   universe (the project's standing survivorship correction).
4. **Cost / slippage / impact.** Small-caps are where spikes cluster *and* where impact + delivery cost
   (~29–35 bps) + spread eat everything. Large/mid F&O names are fillable but efficient. The tradeable
   band is narrow.
5. **Manipulation & ethics.** Many low-float / SME / ASM-GSM pumps are operator-driven. "Predicting" those
   is front-running manipulation — flag the legal/ethical/risk problem explicitly; retail is the exit
   liquidity. Do NOT propose strategies that amount to riding pump-and-dumps.
6. **Multiple-testing.** The harness auto-corrects with Deflated Sharpe + HLZ Bonferroni
   (`signal_engine/research/overfitting.py`). Any candidate must be graded through it — the PEAD headline
   (t=2.91) did **not** survive DSR.

## Reuse — what already exists (don't rebuild; test on it THIS WEEK)
- `signal_engine/research/events_dataset.py` → `data/research/earnings_events.parquet`: **7,322 earnings
  events, 230 names, 2005–2026** (symbol, ann_ts, ann_date, eps_est, eps_reported, surprise_pct).
- `signal_engine/research/long_panel.py` → `long_daily_panel.parquet`: **738k rows, 230 names, 16y**
  (close, fwd_ret_{5,10,20}, is_oos, is_purged) — has same-day and forward moves to define "spike days".
- `signal_engine/research/swing_dataset.py` → `swing_dataset.parquet`: 5y **feature** panel
  (rel_strength_20d, rvol_20d, rsi_14, adx_14, …) — ex-ante features to predict the jump.
- `signal_engine/news/` — RSS + sentiment + event-type + spike features (a live news pipeline).
- `signal_engine/research/swing_probe.py` — `evaluate_swing_signal(...)` / `evaluate_long_short(...)`:
  the **validation harness** (net of cost, OOS, PBO, `edge_verdict`). Route everything through it.
- `edge_verdict` gate: **n≥2000, (WR≥52% OR PF≥1.10), PBO<0.10, net of real cost, OOS, demeaned.**
- Settled verdicts to honor (don't re-derive): intraday OHLCV no edge; swing OHLCV no edge; overnight
  drift ~break-even; PEAD real-but-walled-off. Meta-constraints: survivorship, too-efficient liquid
  universe, cost wall, short-sale wall.

## Required deliverables (what the workflow must produce)
1. **Catalyst taxonomy** — the causes of large up-days, each tagged: scheduled vs unscheduled; universe
   tier (large-F&O / mid / small-SME); **ex-ante forecastable?**; **fillable (not circuit-locked)?**;
   typical magnitude. Output the *addressable* subset = forecastable ∧ fillable ∧ non-manipulative.
2. **Ex-ante feature inventory** — every observable-before-the-spike signal, each with: data source
   (free/cheap for NSE/BSE), exact formula, point-in-time/look-ahead risk, expected predictive value,
   and which catalyst it front-runs. Candidates to cover: pre-event volume/delivery-% buildup, F&O OI
   surge + unusual options activity + IV/skew, price-range compression (coiling), promoter/bulk/block
   deals + SAST/insider disclosures, corporate-action calendar (board-meeting notices, results dates,
   ex-dates, buybacks), news/attention velocity (RSS, Google-Trends-style), ADR/GIFT/overnight, sector &
   peer momentum, 52-wk-high proximity, low-float/short-interest proxies.
3. **2–4 ranked candidate approaches**, each with an **honest feasibility verdict** against all six walls,
   the strongest tradeable residue, and what would have to be true to deploy.
4. **A concrete Phase-0 experiment** runnable on the **cached data this week** (highest signal / lowest
   effort) — e.g., "do ex-ante features predict the earnings-day up-jump magnitude, long-only, net of
   cost, OOS, DSR-corrected?" Specify dataset, label, features, model, and the exact `edge_verdict` gate.
5. **Modeling & validation design** — framing (P(up-spike≥X% today) classification / magnitude regression
   / cross-sectional ranking / hazard "day-of" model), base rates, class-imbalance handling, and the full
   tradability filter stack (circuit-lock, F&O-shortable N/A here since long-only, cost, liquidity/impact,
   ASM-GSM exclusion), plus DSR/multiple-testing.
6. **Explicit "don't do this" list** — the anti-patterns (survivorship sampling, circuit-lock fill fantasy,
   riding manipulation, cost-blind small-caps, un-corrected mining).
7. **A realistic edge assessment** — is there a plausible, honest, long-only tradeable edge here, or is
   this another "real-but-walled" result? Say so with reasons.

## Tone & anti-hype
No breathless "AI can predict the market." Quantify base rates and costs. Prefer "here is the one testable
hypothesis with the best odds and here's exactly how we'd falsify it" over a laundry list. If the honest
answer is "the fillable/forecastable subset is thin," say that and name the thin subset precisely.
