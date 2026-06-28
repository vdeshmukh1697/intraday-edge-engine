# Market-Neutral PEAD Spread + SUE-with-Jump — Findings (2026-06-25)

Execution of **Option C** (`docs/RESEARCH_HANDOFF.md`): take the directionally-real PEAD signal that
the long-only survivorship correction left standing (the beat-vs-miss *spread*), implement it
**market-neutral** (long beats / short misses via futures), add the literature's **SUE-with-jump**
sharpener, and run it through the honest gate. Reproduce:
`.venv/bin/python -m signal_engine.research.pead_spread` (module: `signal_engine/research/pead_spread.py`).

Data: cached 16y panel (`long_daily_panel.parquet`, 738k rows, 230 names, 2010–2026) + Yahoo earnings
events (`earnings_events.parquet`, 7,229 with surprise%). **3,053 OOS post-earnings entries** (OOS =
recent ~30%, 2021-08 → 2026-06, ~4.8y). Every return is **demeaned by the same-day universe mean**
(strips the survivor + market drift — the discipline that exposed the long-only mirage). Net of
single-stock **futures** cost (~13.4 bps/leg). Headline = OOS. Gate: n≥2000, WR≥52% OR PF≥1.10,
PBO<0.10.

## Headline: the EPS surprise is inert; the announcement-day price JUMP is the real signal
The most important result of this study is a **decomposition**, run market-neutral and demeaned, 20d, OOS:

| signal (long / short) | n (OOS) | WR | PF | net/pos | monthly-clustered t | gate |
|---|---|---|---|---|---|---|
| **EPS surprise ≥+5% / ≤−5%** (plain PEAD) | 2,339 | 50.2% | 1.03 | +0.10% | **+0.05** | dead |
| **announcement jump >0 / <0** (price drift) | **3,019** | **52.5%** | **1.18** | **+0.51%** | **+2.91** | n✓ WR✓ PF✓ |
| surprise AND jump agree (confirmed) | 1,385 | 53.1% | 1.24 | +0.70% | +2.88 | WR✓ PF✓ n✗ |
| surprise vs jump **disagree** (contradicted) | 936 | 46.2% | 0.78 | −0.77% | — | reverses |

- **The EPS surprise alone carries no tradeable alpha** after demeaning (t=+0.05 — statistical zero).
  Conditioning on a "beat" does nothing; the plain-PEAD spread is a coin flip net of cost.
- **The announcement-day price jump IS the signal.** Going long names that jumped up on earnings and
  short names that jumped down (market-neutral) earns +0.51%/position net over 20d, WR 52.5%, PF 1.18,
  with a **monthly-clustered t of +2.91** (p≈0.006 — the fair significance test for an overlapping,
  episodic signal; the naive per-position t overstates).
- The "confirmed" (surprise **and** jump agree) version is marginally sharper per-position (+0.70%) but
  the surprise filter mainly just **halves the sample** (n=1,385). The literature's "confirmation
  sharpens it" holds only weakly here: the jump *subsumes* the surprise, it doesn't get sharpened by it.
- The **contradicted** set (beat but price fell / miss but price rose) **reverses** to −0.77%, PF 0.78
  — exactly what you'd expect if the price reaction, not the accounting number, is what drifts.

## It is earnings-SPECIFIC, not generic momentum (the decisive control)
Short-term price moves normally *reverse*; post-earnings moves *drift*. Same jump-sign→20d demeaned
spread, OOS, **earnings-window jumps vs clean non-earnings jumps** (matched on |jump|):

| |jump| ≥ | earnings-jump spread | clean non-earnings jump spread |
|---|---|---|
| 2% | **+2.02%** (n 1,695) | +0.17% (n 56,429) |
| 3% | **+2.26%** (n 1,227) | +0.37% (n 30,568) |
| 5% | **+2.79%** (n 622) | **−0.11%** (n 8,920) |

A big move *with no earnings behind it* mean-reverts or does nothing; the **same-size move on earnings
drifts hard**, and the gap *widens* with jump magnitude. This is the textbook continuation-vs-reversal
signature and is strong evidence the effect is a genuine **post-earnings-announcement drift** carried by
the price reaction — not generic price momentum.

## Robustness of the headline candidate (jump-sign, market-neutral, 20d)
- **Cost**: survives the conservative **2-leg** futures cost (each position individually index-hedged):
  WR 51.7%, PF 1.13, +0.37%/pos. Still passes performance at the pessimistic cost.
- **Not tail-driven**: mean +0.51% ≈ median +0.44% ≈ winsorized(1–99) +0.48%; top-5% winners are only
  **29% of gross gains** (a fat-tail artifact would be 60%+). Broad effect.
- **Time-stable**: positive in **both OOS halves** (H1 +0.47%/pos, H2 +0.92%/pos) and in **13 of 17**
  calendar years (full sample).
- **Magnitude-graded**: requiring |jump|≥3% lifts it to WR 56.2%, PF 1.36, +1.00%/pos, and even the
  PBO improves to 0.35 — at the cost of sample (n=1,227 < 2000). Clear risk/sample frontier.

On the **full 230-name universe** the headline candidate clears every gate appropriate for an episodic,
overlapping-horizon event signal — n≥2000 (3,019), WR≥52% (52.5%), PF≥1.10 (1.18), a significant
clustered-t (+2.91), both-halves and by-year stability, earnings-specificity, conservative-cost survival
— failing only the literal monthly-PBO (≈0.50), which is the wrong test here (no in-sample search for it
to validate; consecutive-month PF on a seasonal 20d-overlap signal is dominated by market regime — the
handoff anticipated this: *"episodic event returns make PBO noisy"*). **But that full-universe number is
not implementable**, and the tightening below shows exactly why.

## Tightening — the implementability wall (decisive; reproduce: `pead_robustness.py`)
Three checks the user asked for before any paper sleeve. They **revise the verdict downward, honestly.**

**(a) Jump-magnitude × hold-period frontier — clean and monotone (the real part).** Bigger jump → bigger
drift; longer hold (→20d) → bigger drift; a broad plateau, not a knife-edge (e.g. |jump|≥7%/20d → WR
55.9%, PF 1.57, +1.50%/pos). Monotonicity is strong evidence the effect is genuine.

**(b) Size tier (mid-cap proxy = median `log_turnover_20d`).** The drift lives in the *less-liquid* half:

| size half (jump-sign, 20d, OOS) | n | WR | PF | net/pos | t |
|---|---|---|---|---|---|
| large-cap half (liquid) | 1,880 | 50.7% | 1.06 | +0.17% | +1.67 |
| **mid-cap half (less liquid)** | 1,187 | **55.3%** | **1.35** | **+1.02%** | +2.33 |

Textbook PEAD (scarcer attention → slower price discovery). **But** mid-caps are the least F&O-eligible —
so the alpha sits where it's hardest to trade.

**(c) F&O-restricted re-test + leg decomposition — the wall.** The short-miss leg needs a single-stock
FUTURE (cash shorts are illegal overnight in India), so only F&O names are shortable. Two findings:

| jump-sign, 20d, OOS | n | WR | PF | net/pos (1-leg) | t |
|---|---|---|---|---|---|
| all 230 (not all tradeable) | 3,067 | 52.5% | 1.17 | +0.50% | +2.93 |
| **F&O-only (actually shortable)** | 2,208 | 51.7% | **1.08** | **+0.24%** | **+1.67** |

…and decomposing the spread into its two legs (demeaned α = what an index hedge realizes):

| subset | LONG up-jump α (index-hedgeable, ANY name) | SHORT down-jump α (needs single-stock future) |
|---|---|---|
| all 230 | **+0.10%** (≈0) | **−1.12%** |
| mid-cap half | +0.09% (≈0) | **−2.06%** |
| non-F&O (can't short) | +0.19% | **−2.26%** |
| F&O (can short) | +0.07% | −0.66% |

**The edge is almost entirely the SHORT leg (down-jumpers keep falling), concentrated in MID-CAPS — which
cannot be shorted.** The long leg is dead everywhere except the tiny big-jump/mid-cap corner. What is
actually executable:
- **Short large-cap F&O down-jumpers, |jump|≥3%**: n=414, WR 58.0%, PF 1.30, +0.73%/pos (net 2 legs),
  t=+2.70 — real and significant, but **severely under-powered (n≪2000)** and a **directional short book**
  (squeeze/borrow/falling-knife risk), not the clean neutral spread.
- The strong stuff — short non-F&O down-jumpers |jump|≥3% (n=209, WR 63%, PF 1.70, **+1.92%/pos**) — is
  **un-executable** (no single-stock future to short; cash short illegal overnight).

## Multiple-testing re-grade — Deflated Sharpe Ratio (`overfitting.py`, decisive)
Our harness corrects PBO + survivorship but NOT *selection across the ~1,900 variants we searched*.
Adding the de Prado / Harvey-Liu-Zhu instruments (Bailey & López de Prado 2014 Deflated Sharpe; HLZ 2016
Bonferroni t-haircut) and re-grading the jump-drift grid (72 variants = 6 thresholds × 3 holds × 4
subsets, monthly Sharpes, OOS):

| best variant per subset | monthly Sharpe | t | DSR (N=1900 trials) | Bonferroni req. t | shortable? |
|---|---|---|---|---|---|
| **headline** (j>0, 20d, all) | 0.46 | **2.91** | **0.03** | 3.4–4.2 | mixed |
| mid-cap (j≥5%, 20d) | 0.74 | 4.53 | 0.60 | 4.20 | **NO** (no SLB) |
| **F&O** (j≥3%, 20d) | 0.45 | 2.84 | **0.04** | 4.20 | YES |
| **large-cap** (j≥2%, 20d) | 0.40 | 2.55 | **0.01** | 4.20 | YES |

Two decisive reads: **(1)** the full-universe headline we reported (t=2.91) does **not** survive multiple
testing — it fails the Bonferroni bar (req. 3.4 even at N=72) and DSR collapses to 0.03; that number was
selection-inflated. **(2)** Every **tradeable** (shortable) variant collapses under deflation (DSR 0.01–
0.42); the *only* variant with selection-robust strength (mid-cap big-jump, t=4.53, DSR 0.60) is the
**un-shortable** one — and even it fails the strict DSR>0.95 bar (short T=37mo, now negative skew = short-
squeeze tail risk). Caveat: DSR assumes independent trials; ours are correlated variants of one signal, so
the strict numbers are conservative — but the headline fails so comfortably that the caveat doesn't rescue
it. This is now the **third independent method** (after leg-decomposition and F&O-restriction) reaching the
same verdict.

## Honest verdict (final)
The jump-drift PEAD is **the most real signal of the entire arc** — earnings-specific (not momentum),
monotone in jump size and horizon, mid-cap-concentrated, stable across halves/years/thresholds, significant
under a fair clustered-t. **But it does NOT clear honestly as a tradeable edge, established THREE
independent ways:** (1) leg-decomposition — the alpha is the short leg of mid-caps; (2) F&O-restriction —
the shortable subset fails the gate (PF 1.08); (3) **Deflated Sharpe / multiple-testing — every tradeable
variant collapses (DSR 0.01–0.42) and the headline t=2.91 fails the Bonferroni bar; only the un-shortable
mid-cap variant survives selection correction.** Its alpha lives on the **short side of mid-caps**,
precisely the leg India's market structure walls off — the textbook reason such anomalies *persist*. Per
the gate ("paper-trade only if it clears honestly"), **no paper sleeve, and the PEAD/jump-drift line is now
honestly exhausted** — further variant-mining is −EV (each new test raises the selection bar for all).

## Caveats / what would change this
1. **Short-sale constraint is the binding wall** (newly the #1 blocker, ahead of survivorship): the alpha
   is in names you can't short. A stock-borrow / SLB route or broader single-stock-futures eligibility
   would unlock it; neither is cheaply available.
2. **Survivor universe (230 names)** still applies — delisting-inclusive data remains the long-term unlock.
3. **The executable short book is directional and thin** (n=414) — not gate-clearing on its own.

## Recommended next step
Two honest options, no middle: **(A) stop active signal-mining on this data** — bank the negative result
and the new `overfitting.py` instrument; not deploying an overfit strategy is itself the win the whole
discipline exists to deliver. **(B) pursue the structural unlock** (survivorship-clean / delisting-inclusive
+ true mid-cap universe + SLB/borrow access) — the only thing that would let the real (mid-cap short) alpha
be measured and traded; a data/infrastructure project, worth it only with appetite to invest. **Do NOT**
keep mining PEAD variants — the deflation layer shows the tradeable corners don't survive, and every new
test raises the bar. Durable win from this session: **wire `overfitting.py` (DSR + multiple-testing
haircut) into the standing harness** so every future candidate is auto-corrected for selection.
