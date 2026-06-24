# SWING / DAILY RESEARCH — FINDINGS REPORT
### NSE-equities longer-horizon edge hunt, net of DELIVERY costs, out-of-sample, India-shorting-constrained

---

## 1. TL;DR

**No.** Across **8 hypothesis families** and **~195 ex-ante registered variants**, we found **NO real, delivery-cost-surviving, out-of-sample, long-only-implementable swing edge** on this daily NSE universe. **Zero variants pass `edge_verdict` out-of-sample.**

The failure mode is consistent and diagnostic, not random: **every family looks excellent in-sample (IS) and collapses out-of-sample (OOS)**, and **every family fails the PBO (overfit) gate** with PBO ≈ 0.25–0.62 against a required < 0.10. Several families produce a positive OOS *mean* return that arithmetically clears the ~0.35% delivery-cost wall — but in every such case the OOS *median* trade is ~0 or negative, the win-rate is below a coin flip, and the positive mean is carried by a thin right tail. That is undifferentiated, survivorship-inflated **market beta**, not alpha.

The single most honest number in the whole study: in the trend-following family, an **always-long (no signal) book** over the same 20-day horizon returned **+1.16% net OOS (PF 1.35)** — *better* than the best trend filter (+0.42%, PF 1.12). Our "best signal" added **negative** lift versus simply holding. That is the cleanest possible proof that the apparent edges are beta, not skill.

**Verdict: the daily-swing space on this data is ruled out for a standalone tradeable long-only edge.**

---

## 2. Dataset & Bill-of-Health

| Item | Value |
|---|---|
| Path | `/Users/vikrantdeshmukh/Personal projects/data/research/swing_dataset.parquet` |
| Shape | **247,740 rows × 37 cols** (one row per symbol-day) — verified |
| Universe | **250** most-liquid NSE names by mean 2025 daily rupee turnover, ≥200 sessions listed (scoped from 2,091 archived symbols; no silent truncation) |
| Date range | 2021-09-01 → 2026-06-23 (~4.8y daily bars) |
| Split | `ml.train.date_split_indices`, cutoff **2025-01-09**, embargo **35 calendar days** |
| IS / OOS / purged | **154,866 / 82,023 / 10,851** — verified |

**Features (≤ day d close):** trailing returns `ret_{1w,1m,3m,6m,12m}`; `rvol_20d`; `dist_ma{20,50,200}_pct`; `high_52w_prox_pct`; `rsi_14`, `adx_14`; overnight/intraday decomposition; turnover ratios; `beta_proxy_60d`, `rel_strength_20d`; calendar (`dow`, `dom`, `turn_of_month`).
**Labels (> day d):** `fwd_ret_{1,2,5,10,20}`, `fwd_mfe_20`, `fwd_mae_20`, `fwd_overnight_1`.

**Delivery cost model** (NSE cash, multi-day holds, at Rs 1,00,000/leg):
- **Round-trip statutory = 0.2874%** (STT 0.1% on **both** legs = 0.20%, dominant; + exchange txn + SEBI + stamp 0.015% buy + GST + DP charge).
- **Default applied = 0.3474%** (statutory + 3 bps/side slippage).
- Zero-brokerage floor = 0.2402%. Futures short-leg proxy ≈ 0.10%.
- This is **~2× the ~0.14% intraday wall** — a swing tilt must compound past ~29–35 bps round-trip.

**Leakage:** PASS. 720/720 feature point-in-time truncation checks (max|diff| = 0.00e+00); 200/200 forward-label reconstructions; embargo 35d ≥ 28d (covers 20-trading-day max label); 0 unpurged straddling rows; IS/OOS share zero session dates.

**Survivorship bias:** ⚠️ **FLAGGED, NOT PURGED.** The archive = today's listed survivors; delisted/merged losers are absent. This **upward-biases every long-only result** (especially momentum and 52w-high, which select strong performers). All positive numbers below are therefore *optimistic*. **No true index series** in the archive → "beta" is a universe equal-weight demean **proxy**, not real market beta.

---

## 3. Results Table — every family (OOS net of DELIVERY costs)

| # | Family | Best variant | Horizon | Long-only? | IS PF / WR | **OOS PF / WR / mean-net%** | **PBO** | edge_verdict | Survives cost (arith.) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Cross-sectional momentum | 12m formation, top-decile | 20d | Yes (L/S short flagged) | 3.19 / 0.616 | **1.66 / 0.533 / +2.41** | **0.375** | **FAIL** | yes |
| 2 | Short-term reversal | hivol losers, q=0.10 | 5d | Yes | 1.43 / 0.489 | **1.02 / 0.453 / +0.04** | **0.429** | **FAIL** | no |
| 3 | Time-series / trend-following | close>MA50 & MA200 | 20d | Yes | 2.40 / 0.587 | **1.12 / 0.483 / +0.42** | **0.417** | **FAIL** | no |
| 4 | Overnight drift | top-decile 20d gap | 1 (o/n) | Yes | 1.49 / 0.537 | **0.75 / 0.460 / −0.11** | **0.464** | **FAIL** | no |
| 5 | Low-volatility anomaly | bottom-30% rvol | 20d | Yes | 1.62 / 0.549 | **1.27 / 0.515 / +0.73** | **0.250** | **FAIL** | yes |
| 6 | Mean-reversion / Bollinger | RSI≤30, uncond. | 10d | Yes | 2.26 / 0.607 | **1.22 / 0.497 / +0.60** | **0.467** | **FAIL** | no |
| 7 | Calendar / seasonality | expiry last-5-td | 5d | Yes | 2.11 / 0.602 | **0.89 / 0.444 / −0.23** | **0.482** | **FAIL** | no |
| 8 | 52-week-high proximity | within 1% of 52w high | 20d | Yes | 1.90 / 0.556 | **1.05 / 0.483 / +0.16** | **0.464** | **FAIL** | no |

**Variants tested:** momentum 24, reversal 45, trend 30, overnight 8, low-vol 6, mean-reversion 36, calendar 38, 52w-high 8 = **~195 total.**
**Adversarial verifications:** none registered (empty) — note this as a coverage gap (see §5).
**edge_verdict gate:** n ≥ 2,000; (WR ≥ 0.52 OR PF ≥ 1.10); **PBO < 0.10**. **0 of ~195 variants pass OOS.**

---

## 4. What Survived

### Nothing survived.

No family cleared `edge_verdict` out-of-sample. The two families with a positive, cost-surviving OOS *mean* (momentum #1, low-vol #5) are **not** edges:

- **#1 momentum** clears the WR/PF performance threshold OOS (PF 1.66) but **fails PBO at 0.375** — the monthly walk-forward ranking is no better than a coin flip, the textbook overfit signature. IS PF 3.19 → OOS 1.66; WR 61.6% → 53.3%. Overlapping 20d holds rebalanced daily also inflate the nominal n far above the effective-independent count.
- **#5 low-vol** passes `edge_verdict` *in-sample* (PBO 0.0) but **fails OOS at PBO 0.25**, and is disconfirmed three independent ways: (a) the **high-vol** leg *outperforms* low-vol OOS (+1.42% vs +0.73%) — the opposite of the anomaly; (b) the low-minus-high cross-sectional spread is **negative** OOS (−0.57%, PF 0.865); (c) OOS monthly returns swing +5.2% to −6.5% with 7/16 months negative, wins concentrated in bull months. This is market beta + survivorship drift, not a volatility premium.

**The decisive baseline (family #3):** an unconditional always-long 20d book returned **+1.16% net / PF 1.35 OOS**, beating the best trend filter (+0.42% / PF 1.12) — **negative lift of −0.74pp**. The signal selected names that subsequently *underperformed* simply holding. This generalizes: the positive OOS means elsewhere are the equity-risk-premium drift over ~20 sessions, inflated by survivorship, not signal.

### Honest implication

On **daily price/volume features alone**, for this 250-name survivorship-biased universe over 2021–2026, **there is no robust, cost-surviving, out-of-sample long-only swing edge**. The doubled-STT delivery wall (~29–35 bps round-trip) plus genuine market efficiency at the daily horizon leaves the realized per-period tilt too small and too unstable to compound past costs. The IS "edges" were the 2021–24 bull regime + survivorship; neither persisted into the 2025–26 OOS.

### Best next directions (in priority order)

1. **Different information, not different price-math.** Every family here is a transform of the same OHLCV. The likely-real edges need data the bar archive does **not** contain: **fundamentals/value/quality** (P/E, P/B, accruals, ROE, earnings revisions), **events** (earnings drift/PEAD, index inclusions, corporate actions), and **alternative/flow data**. Acquire a point-in-time fundamentals + corporate-events feed first.
2. **Fix survivorship before trusting any long-only result.** Build a delisting-inclusive, point-in-time universe (NSE listing/delisting calendar). Until then, every long-only verdict is optimistically biased — a real edge must clear the gate on the *unbiased* universe.
3. **Lower the cost wall, don't fight it.** The overnight-drift anomaly (#4) is *real gross* (+0.236% mean overnight vs −0.120% intraday) but drowns under doubled STT. A **futures/options wrapper** (≈10 bps proxy, no STT-both-legs, no DP) could in principle harvest it — but carries roll/financing/margin costs out of scope here. This is the one phenomenon worth a dedicated, properly-costed derivatives study.
4. **Longer horizons / monthly rebalancing** to amortize the fixed cost over more drift, with **non-overlapping** holds so n reflects independent samples and PBO is honest.

---

## 5. Honesty Caveats

- **Multiple testing / PBO.** ~195 variants were scanned; picking the IS-best and reporting it OOS is exactly the setup PBO is designed to police. PBO ≈ 0.25–0.62 everywhere says the IS ranking does **not** transfer — these are overfit selections, not edges. We report PBO for every family and reject on it. This is the load-bearing result of the study.
- **Survivorship bias.** Universe = today's listed survivors; delisted losers absent. Upward-biases all long-only momentum/mean-reversion/52w-high. Even our IS numbers are optimistic; the OOS collapse is therefore *unsurprising* and the rule-outs are *strengthened*, not weakened.
- **Overnight-shorting constraint (India).** Cash-equity shorts cannot be carried overnight. We prioritized long-only (the implementable headline) throughout. Where long-short was tested (momentum #1, low-vol #5), the short leg was priced at the futures proxy and **flagged non-implementable in cash**; in both cases the short leg *lost* money OOS (momentum short PF 0.76, low-vol spread PF 0.865), so the constraint costs us nothing here.
- **Regime coverage.** IS (2021-09→2024-12) is a strong bull market; OOS (2025-02→2026-06) is a different, choppier regime. ~4.8y total spans essentially one full IS bull + one OOS transition — **thin regime diversity**. A signal that survived would still need a bear-market / high-vol OOS slice before being trusted.
- **Overlapping holds.** Multi-day labels rebalanced daily autocorrelate returns; reported n overstates effective-independent trades and per-trade Sharpe is not annualized. Significance is weaker than nominal n implies — another reason to distrust the IS strength.
- **"Beta" is a proxy.** No index series in the archive; `beta_proxy_60d` is a universe equal-weight demean, not true market beta.
- **No adversarial verification was run** (the adversarial-verifications input was empty). The rule-outs rest on the primary scans + the always-long baseline check. Given all verdicts are FAIL/rule-out, the risk of a *false positive* is nil; the residual risk is a *false negative* (a real edge missed), which the next-directions in §4 address.
- **What was NOT tested (and likely needs non-bar data):** fundamentals/value/quality factors, earnings/PEAD and event studies, index-rebalance flows, options-implied signals, intraday-microstructure swing entries. These are the most credible places a real Indian-equity swing edge would live, and none are derivable from this OHLCV archive.

---

*Harness (additive, read-only on production): `signal_engine/research/swing_dataset.py`, `swing_probe.py`, `delivery_costs.py`, `check_causality_swing.py`. Dataset: `data/research/swing_dataset.parquet`. All verdicts gated through `ml.evaluate.edge_verdict`; PBO via monthly walk-forward.*
