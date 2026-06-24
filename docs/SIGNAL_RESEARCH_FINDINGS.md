# Intraday NSE-Equity Alpha Study — Honest Findings Report

**Universe:** 50 most-liquid NSE equities · **Period:** 2024-06-24 → 2026-06-23 (~2y) · **Bar:** 1-min archive, ~5-min sampling stride · **Sample:** 1,751,035 rows
**Validation:** global calendar-date IS/OOS split + 1-day interval embargo · **Costs:** 0.1424% round-trip (statutory + 2×0.03% slippage), applied once per trade
**Edge gate (`ml.evaluate.edge_verdict`):** n ≥ 2,000 AND (win-rate ≥ 0.52 OR profit-factor ≥ 1.10) AND PBO < 0.10

---

## 1. TL;DR

**No.** We did not find a real, cost-surviving, out-of-sample intraday edge.

Across **9 distinct hypothesis families** and **~1,693 pre-registered variants**, **zero** passed `edge_verdict` out-of-sample (and none even passed in-sample net of costs). The result is uniform and robust, not a thin-sample fluke.

The honest, important nuance: several families carry a **genuine but microscopic gross signal** — intraday momentum/continuation among extreme-ranked names, opening-range breakout continuation, cross-sectional short-horizon mean-reversion, and a joint ML model (OOS AUC ≈ 0.52, label-shuffle control confirms it is real signal). In every case the gross per-trade edge is **~0.01–0.04% (1–4 bps)**, which is **3–15× smaller than the 0.1424% (14.2 bps) round-trip cost wall**. The edges are real; they are simply far too thin to survive transaction costs at these horizons and turnover on liquid large-caps.

This confirms and extends the established prior: base intraday rules on this universe have no cost-surviving edge, and the broader space of standard intraday alphas does not rescue them.

---

## 2. The Research Dataset

### Scope (stated — no silent truncation)
- **Universe:** 50 most-liquid archived NSE names by mean daily turnover in 2025 (RELIANCE, TCS, SBIN, TMPV, MAZDOCK, LT, TRENT, DIXON, MARUTI, … TVSMOTOR), filtered to ≥200 sessions for dense 2-year coverage.
- **Period:** 2024-06-24 → 2026-06-23 (recent ~2 years).
- **Sampling:** every 5th 1-min bar within each session (~5-min stride); sessions with < 60 bars skipped.
- **Shape:** 1,751,035 rows × 32 columns.
- **Index relative-strength: SKIPPED, not silently dropped** — `store.list_symbols()` confirms no NIFTY/SENSEX/INDEX series in the archive. Cross-sectional studies used universe-demeaned rank as a documented beta-neutral proxy.

### Features (causal, ≤ t — 19 columns)
`ret_1, ret_5, ret_15, rvol_realized, vwap_dist_pct, vwap_z, rvol, atr_pct, adx, ema_spread_pct, rsi, macd_hist, gap_pct, or_pos, minutes_since_open, tod_bucket, dow, frac_diff, dist_round_pct`

### Labels (forward > t, never features — 7 columns)
`fwd_ret_5, fwd_ret_15, fwd_ret_30, fwd_ret_60, fwd_mfe_atr, fwd_mae_atr, tb_sign` (vol-scaled triple-barrier, ~50/50 ±1)

### IS/OOS split (reuses `ml.train.date_split_indices`)
- Cutoff date **2025-11-14**, 1-day interval embargo.
- **IS:** 1,214,646 rows (… → 2025-11-13) · **OOS:** 532,689 rows (2025-11-17 → 2026-06-23) · **Purged straddlers:** 3,700.
- **Zero date overlap** between IS and OOS confirmed empirically.

### Leakage bill-of-health — CLEAN ✓
Independent adversarial audit returned a **clean, zero-leakage** verdict:
- **Feature causality:** 450/450 spot-checks identical when recomputed on truncated history `day_df[:t+1]`; max|diff| = **0.00e+00**.
- **Label causality:** 120/120 forward labels match forward-only reconstruction `close_{t+h}/close_t − 1`; `ts_exit > ts` always holds.
- **Feature/label name sets disjoint.**
- **Split:** non-overlapping with interval embargo; purged rows excluded everywhere.
- **Cost:** applied once per round-trip, never written to parquet, never fed back as a feature.
- **Sanity:** always-long h15 baseline → gross mean ≈ 0, net PF 0.28, verdict FAIL — the harness penalizes correctly.

---

## 3. Results Table — All Hypothesis Families

All stats **net of 0.1424% round-trip cost**. OOS is the headline; IS shown alongside. WR = win-rate, PF = profit factor.

| # | Hypothesis family | Best variant | IS PF | IS WR | OOS PF | OOS WR | OOS Sharpe | OOS PBO | Verdict | Survives costs |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Momentum vs mean-reversion (ADX regime) | MOM adx>30 & \|ema_spread\|>0.05, sign(ret_15), h60 | 0.623 | 0.386 | 0.592 | 0.389 | −0.178 | 0.167 | **FAIL** | No |
| 2 | VWAP-distance reversion | fade-long when ≤ −1.0% from VWAP, h60 | 0.619 | 0.390 | 0.643 | 0.392 | −0.151 | 0.500 | **FAIL** | No |
| 3 | Opening-range breakout/reversion | continuation long, break above 15m OR-high + gap-up, h60 | 0.686 | 0.378 | 0.590 | 0.376 | −0.179 | 0.417 | **FAIL** | No |
| 4 | Overnight gap continuation vs fade | fade large up-gap (\|gap\|≥1.5%), first 30m, h60 | 1.021 | 0.497 | 0.890 | 0.471 | −0.042 | 0.500 | **FAIL** | No |
| 5 | Cross-sectional relative-strength | long top-/short bottom-decile ret_15 momentum, h60 | 0.609 | 0.378 | 0.598 | 0.380 | −0.171 | — | **FAIL** | No |
| 6 | Time-of-day × volume/vol interactions | post-lunch & atr_pct≥p90, long, h60 | 1.071 | 0.469 | 1.036 | 0.434 | 0.011 | 0.750 | **FAIL** (n<2000 OOS) | No |
| 7 | Volatility-regime conditioning | VWAP-trend long, ATR-volatile tercile, h30 | 0.606 | 0.368 | 0.559 | 0.366 | −0.204 | 0.417 | **FAIL** | No |
| 8 | Cross-sectional intraday mean-reversion | long losers/short winners by ret_1 decile, h30 | 0.529 | 0.363 | 0.479 | 0.354 | −0.237 | 0.167 | **FAIL** | No |
| 9 | ML multi-factor logistic (19 features) | top-decile long, h15 (OOS AUC 0.5215) | 0.374 | 0.294 | 0.343 | 0.288 | ≈ −0.05 | 0.250 | **FAIL** | No |

**Gross-signal honesty annex** (per-trade gross mean, OOS, before costs — the "whisper" each family carries):

| # | Family | OOS gross mean/trade | vs 14.2 bps cost |
|---|---|---|---|
| 1 | ADX-momentum | +0.0144% (1.4 bps) | ~10× too small |
| 3 | ORB continuation | +0.0172% (1.7 bps), gross PF 1.076 | ~8× too small |
| 5 | XS relative-strength | +0.0141% (1.4 bps) | ~10× too small |
| 8 | XS mean-reversion | +0.0099% (1.0 bps), gross PF 1.057 | ~14× too small |
| 9 | ML logistic | +0.0085% (0.85 bps), AUC 0.52, shuffle-control passes | ~17× too small |

**Adversarial verifications:** none performed — **there were no survivors to verify.** No variant in any family cleared `edge_verdict` OOS (or even IS net of costs), so the adversarial-verification queue is empty by construction.

---

## 4. What Survived

**Nothing survived.** This is an unambiguous, honest negative result.

No rule from any of the 9 families clears the `edge_verdict` gate out-of-sample. The few variants that look marginal in-sample (gap-fade PF 1.021; ToD×vol-interaction PF 1.071) collapse OOS to PF < 1.05 with PBO 0.50–0.75 — coin-flip-to-worse overfit signatures — and the strongest gross signals (ORB continuation gross PF 1.076; XS reversion gross PF 1.057) fail the gate **even at zero cost**, because the gross effect itself (~1 bp) is below the WR ≥ 0.52 / PF ≥ 1.10 bar before a single rupee of cost is charged.

### What this means
Within **this data and this approach** there is **no tradeable intraday edge** on liquid NSE large-caps at 5–60-minute horizons. The market is efficient at exactly the scale we measured: real micro-tilts exist (continuation in trends/breakouts, short-horizon microstructure reversion, a faint joint-factor direction), but they are an **order of magnitude smaller than transaction costs**. This is the textbook outcome — the most liquid names are where competition is fiercest and gross premia thinnest, while the cost wall is fixed. You cannot out-trade a 14.2 bps round-trip with a 1 bps signal.

### Highest-value next directions
1. **Lower-turnover / longer horizons.** Every family showed net loss falling as horizon rose (cost amortizes over larger moves). Test multi-hour-to-overnight or swing horizons where a 1–4 bps/bar tilt can compound past the fixed cost — accepting this leaves the strict "intraday" mandate.
2. **Microstructure once tick data accrues.** The real signals here (XS reversion strongest on ret_1, ORB continuation) are microstructure in nature. With true tick/quote/order-book data (queue position, spread, signed order flow, imbalance) and realistic per-trade cost modeling, sub-minute reversion/imbalance edges may exist that 1-min bars cannot resolve.
3. **Less-liquid / mid-cap universe.** Gross reversion premia are typically larger off the megacaps — but costs and capacity are worse, so this must be tested net with name-specific cost/borrow modeling, not assumed.
4. **Event / alternative data.** Earnings, news, corporate-action, and index-rebalance overshoots are event-driven mean-reversion not captured by the standard distance/regime features tested here.
5. **A true index for relative-strength.** Acquiring NIFTY/SENSEX/sector index series would replace the demeaned-rank proxy with a real beta-neutral spread, properly testing relative-strength stat-arb.

A concrete "survivor spec" is intentionally **not** provided — providing one would be dishonest given no rule survived.

---

## 5. Honesty Caveats

- **Multiple testing / PBO.** ~1,693 variants were tested across families. Best variants were selected ex-ante by IS PF and reported OOS, and PBO (monthly walk-forward) was computed per headline. The near-best IS marginals (families 4, 6) carry PBO 0.50–0.75 — exactly the overfit signature multiple-testing warns about. No survivor means no multiple-testing inflation to launder; the negatives are robust *to* multiple testing, not victims of it.
- **Sample sizes.** Most families had large OOS n (25k–130k) — power is not the limiting factor. The two marginal-IS families had small OOS n: family 4 (n=2,406) and family 6 (n=1,228, **below the 2,000 gate**), so their "near-1.0" PF is low-confidence and correctly fails the gate.
- **Regime coverage.** OOS is a single ~7-month window (2025-11-17 → 2026-06-23). No structural-break, crisis, or multi-regime stress test across years; PBO partially proxies regime stability but does not replace multi-year OOS.
- **Per-symbol / sub-period not exhausted.** Results are pooled across 50 names. Given uniform PF < 1 and gross signals an order of magnitude under cost, per-symbol conditioning is very unlikely to rescue any family, but it was not exhaustively tested.
- **Cross-sectional friction understated.** XS long-short verdicts (families 5, 8) assume both legs fill simultaneously at the costed price; real stat-arb adds borrow/short-availability and execution-timing frictions that would only **worsen** these results.
- **What we did NOT test:** 30-min opening range (dataset only encodes 15-min `or_pos`); barrier/MFE-based exits vs fixed-horizon close; event-driven reversion (news/earnings); sub-minute microstructure (no tick data); mid/small-caps; true index-relative spreads; heavier ML (no sklearn/scipy — logistic was plain-numpy GD, deliberately lightly tuned to avoid overfit). Overlapping samples from ~5-min stride inflate raw n but do not create edge where none exists.

---

*Bottom line: a clean, leakage-audited harness, a broad pre-registered hypothesis sweep, and an honest verdict — there is no cost-surviving intraday edge in liquid NSE large-caps over 5–60-minute horizons in this 2-year sample. The signals are real; the costs are bigger.*
