# Spike Hunter — desk memo: can we predict large single-day up-moves, tradeably?

> Multi-agent brainstorm (2026-07-04), five senior-analyst passes + PM synthesis. Brief:
> `docs/PROMPT_2026-07-04_SPIKE_HUNTER.md`. Grounded in web-verified NSE/BSE microstructure AND
> computations run on our own cached 16-year panel. Research only — no live orders.

## Bottom line up front
Your instinct is good and it *half-works*. Reframing spike-hunting as **long-only** genuinely defeats
the **short-sale wall** that killed this desk's best prior signal (PEAD/jump-drift, whose alpha lived on
the un-shortable short leg). **But it walks straight into a structurally symmetric wall — fillability —
and our own data already says the naive version is ≈0 alpha.** There is exactly **one thin, honest,
testable lane**, it is runnable on cached data this week, and the expected outcome is a clean
"real-but-thin" negative. That negative is worth banking. **Do NOT build small-cap / circuit-lock
spike-hunting** — those names are un-buyable when they move *and* the space is SEBI-prosecuted
manipulation with retail as the exit liquidity.

## 1. The one insight that governs everything: fillability ⊥ spike-propensity
- **F&O (derivatives-eligible) stocks have NO fixed circuit band.** They get a *dynamic operating range*
  that flexes: ±10% → 15% → 20%… stepping out after ~15-min cool-offs (NSE Price-Band Flex FAQ). They
  **keep trading** through a big move → **fillable**, but they are the **efficient** tier.
- **Non-F&O stocks hit a hard 2/5/10/20% band and LOCK** — at the upper circuit the order book is
  **buyers-only, zero sellers**, and both cash *and* derivatives halt. You can place a buy; it **cannot
  execute**. This is the **un-fillable** tier — and it is where the 10–20% spikes live.
- Surveillance moves the wrong way for a buyer: **T2T** (delivery-only, 5% band), **ASM** (band cut one
  tier + 100% margin), **GSM** (weekly-only trading + 100% deposit), **SME** (5% band, tiny float).
- **The core tension:** the more fillable a name (liquid, F&O, no hard band), the *less* it spikes; the
  more spike-prone (illiquid, tight band, low float), the *less* fillable when it does. **In PEAD the
  winners were un-shortable; here the winners are un-buyable.** Same wall, mirror image.

## 2. The numbers — computed on our own 738k-row, 16-year panel (not vibes)
| Event | Base rate | Odds |
|---|---|---|
| up-day ≥ 5% | 3.10% | 1 in 32 |
| up-day ≥ 10% | 0.448% | 1 in 223 (≈5.9σ) |
| up-day ≥ 20% | 0.0145% | 1 in ~6,900 |

54.9% of name-years have **zero** ≥10% up-days. And these are survivor-biased large/mid caps — an
*optimistic ceiling*; the small-cap tier where spikes cluster is excluded by construction.

**Four kill-shots, each computed on cached data:**
1. **Direction is unforecastable.** Ex-ante features do not predict the *signed* earnings-day jump: best
   full-sample signed corr (52wk-high proximity, r=+0.036, t=3.0) **collapses OOS to r=+0.007 (t=0.36)**;
   momentum is flat-zero OOS. You can sometimes flag "a big move is likely," essentially never "which way."
2. **The fat long-only number is a survivorship mirage.** Raw long up-jumper 20d hold looks great
   (+1.9–2.7% gross, WR 55–60%) — but **demeaned by the same-day universe it collapses to ≈+0.10%**.
   Exactly what `PEAD_SPREAD_FINDINGS.md` already found; the real alpha was the short leg (un-shortable).
3. **Chasing the print is negative-EV.** *After* a ≥10% up-day, the **demeaned** forward return is
   **negative** (−0.28%/5d, −0.50%/10d, −0.44%/20d). Entries must be *ex-ante*, before the move.
4. **The earnings calendar barely helps and can't pick a side.** Being an earnings day lifts P(≥10% up)
   only 0.448% → ~1.05% (2.3×), and the sign is a **coin flip (50.3% up / 49.7% down)**.

**Literature points the wrong way for naive spike-chasing:** the two best-replicated effects — **MAX /
lottery** (high extreme-return stocks *underperform* >1%/mo, confirmed in India) and **attention-driven
buying** (retail net-buys attention grabbers and loses; SEBI: 93% of F&O traders lost ₹1.8L cr) — both say
*don't chase the spiky name long*. "Coiling / range-compression" is folklore here (cached lift 0.85× —
coiled stocks were *less* likely to jump). The non-inverting long-only effects are **52-wk-high anchoring**
(George–Hwang) and **high-volume return premium** (Gervais–Kaniel–Mingelgrin) — both month-horizon,
modest, and volume isn't even in our cache yet.

## 3. The addressable universe (named precisely)
**forecastable ∧ fillable ∧ non-manipulative** = a thin lane:
- **F&O / liquid non-ASM names** only (fillable), **around scheduled catalysts** (earnings, board-meeting
  actions) — the one ethical, testable residue.
- **Index-rebalance inclusions** (Nifty/Sensex/MSCI/FTSE) — fully scheduled, fillable, directionally long
  — but heavily arbitraged and small.
Everything with 20%-lock magnitude (small-cap order wins, M&A pops, SME/penny pumps) is **un-fillable or
un-ethical** and is out of scope.

## 4. Candidate approaches, ranked with feasibility verdicts
| # | Approach | Universe | Verdict | Dies on / caveat |
|---|---|---|---|---|
| 1 | **Ex-ante features → post-earnings up-drift, long-only** (the PEAD long leg, demeaned) | F&O, non-ASM | **Marginal — the one to test** | Long leg already ≈0 in PEAD (α +0.07–0.10%, PF 1.06); expected thin/null but *cleanly testable now* |
| 2 | **Index-rebalance inclusion pre-position** | large/mid F&O | Marginal | Fully arbitraged; edge decays; small n of events/yr |
| 3 | **Prior-day delivery-% + volume buildup → next-day** conditioned on catalyst | mid F&O | Promising but **not runnable this week** | Best untested long-only lever, but delivery%/volume **not in cache** — needs bhavcopy build; delivery% is EOD-only (lag-1, no same-day) |
| 4 | **Pre-open call-auction imbalance (09:00–09:08)** → intraday-early long | liquid | Experimental | Only zero-look-ahead intraday-early single-name feed; thin vs cost; needs live capture |
| ✗ | **Small-cap / SME circuit-lock hunting** | small/SME/ASM | **REJECT** | Un-fillable when it locks **and** front-running SEBI-prosecuted manipulation (222+ entities barred, ₹47.7cr) |

## 5. What to actually do — Phase-0, runnable this week on cached data (~1 day)
**Hypothesis (pre-registered):** on **F&O, non-ASM/GSM/T2T** names, do ex-ante features measured at the
**last close strictly before** the earnings announcement predict the **universe-demeaned** forward return
(the long-only spike-continuation), net of ~29–35 bps delivery cost, OOS, DSR-corrected?

- **Data:** `earnings_events.parquet` ⨝ (features from) `swing_dataset.parquet` at `ts ≤ ann_ts`, label
  from `long_daily_panel.parquet`.
- **Label / framing:** **cross-sectional daily top-k ranking** on `fwd_ret_20 − same-day universe mean`
  (ranking is intrinsically demeaned = bakes in the survivorship correction and maps 1:1 to a long book).
  Binary P(≥X%) kept only as a diagnostic. **Never** grade against a raw baseline.
- **Features (all lagged, point-in-time):** `rvol_20d` (best single, 1.37× lift), 1-month momentum
  (1.29×), `adx_14`, 52wk-high proximity *(watch: inverts OOS — include as signed, don't assume + sign)*.
- **Filters (hard gates, remove from trade set AND label universe):** circuit-lock/fillability, point-in-
  time ASM/GSM/ESM/T2T exclusion, liquidity floor (≥₹5–10 cr ADV, size ≤1–2% ADV), realistic slippage
  25–50 bps/side *on top of* the 0.30–0.42% delivery cost; 35-day interval embargo (`is_purged`).
- **Gate (two layers, matching the PEAD precedent):** `edge_verdict` (n≥2000, WR≥52% OR PF≥1.10,
  PBO<0.10, net/OOS/demeaned) → then `overfitting.py` **DSR>0.95** and t ≥ Bonferroni bar (~3.4). Report
  OOS-headline-with-IS-alongside + monthly-clustered t. **Pre-register grid size N before searching.**
- **Kill criterion (write it down first):** if the demeaned, cost-netted, OOS top-k return isn't
  materially >0 with DSR>0.95 at n≥2000 → **negative result, no sleeve.** Do not threshold-shop.

Class-imbalance math to keep honest: at the ≥10% cut, "always no-spike" scores 99.55% accuracy — so
**accuracy/ROC-AUC are banned as verdicts**; report PR-AUC, precision@k, lift over base rate. Break-even
long-only precision ≈7.4%, i.e. the model must lift precision ~17× over the 0.45% base rate just to
break even.

## 6. If Phase-0 fails (likely) — the ONE real untested long-only lever
**Security-wise delivery-% + volume buildup** (GKM high-volume premium / Merton investor-recognition —
both real, long-only, transferable to emerging markets). It is **not in the cache** and is **free from NSE
bhavcopy**. Building a point-in-time bhavcopy OHLC + delivery-% + ASM/F&O-flag panel is the #1 data build;
the **survivorship-clean / delisting-inclusive universe** remains the standing structural unlock (every
long-only number is optimistic until then).

## 7. Don't-do list
Sample ex-post spikers (survivorship) · believe the fat raw long number (demean it) · model "will hit
upper circuit" as fillable (no sellers when locked) · ride ASM/GSM/SME pumps (manipulation; retail = exit
liquidity) · chase the spike / high-MAX / high-attention name long (documented to underperform) ·
cost-blind small-caps (~29–35 bps eats it) · same-day-leak EOD delivery%/OI · ROC-AUC/accuracy theatre on
an imbalanced label · threshold-shop the label · deploy at n≪2000 or without DSR.

## 8. PM's honest take
This is a *slightly better lane than PEAD* purely because long-only removes the #1 blocker — that alone
makes your idea worth one disciplined test. But the clean, demeaned, fillable, long-only up-move alpha is
~0 in every cut we can already see, the literature's strongest effects point the other way, and the
spike-dense tier is un-buyable and manipulated. **Run Phase-0 to convert "probably walled" into a proven
yes/no; then either (rare) size a tiny slow long sleeve, or (likely) bank the negative and put the effort
into the delivery-%/volume data build, which is the only real long-only lever we haven't tested.**
