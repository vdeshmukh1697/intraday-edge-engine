# Desk review — 2026-07-22 (vwap_ema_adx)

> **Paper book. No demonstrated edge. Proposals only — nothing in this review has been applied.**
> The target (+1.00% net/day) is the user's stated goal, not a forecast. It is +250% a year even without compounding, and ~12x (+1,100%) with it — a level no institutional systematic desk sustains; the best systematic funds target ~1-3% per *month* gross. Every number below is measured; where the evidence is insufficient the review says so instead of narrating.

## 1. Executive summary

- 8 trades, book -2.59% (₹97,824 -> ₹95,293); per-trade %-sum -4.43%. Since inception the ₹1L book stands at -4.71%.
- Gap to +1.00%/day (this session, n=8): +1.00%/day is UNREACHABLE at the current payoff (0.10:1) and trade count (8.0/day): even a 100% win rate yields only +0.32%/day. The gap is not a win-rate gap — it is a payoff-and-size gap.
- Gap to +1.00%/day (gated scope, the configuration actually running): +1.00%/day needs a 71% win rate at the realised 1.33:1 payoff and 5.1 trades/day; realised win rate is 27% (n=41 trades over 8 session(s)). Gap: 44pp. No parameter change bridges 44pp of win rate — this is a SIGNAL problem, not a settings problem.
- Trade count was **drawdown_halt**-bound (26 setups evaluated in the log, 18 affordability skips, 8 trades taken). This matters: a cash-bound or halt-bound day is not a measurement of the signal's selectivity.
- Microstructure shadow signal: 44.7% next-bar directional hit rate over 14894 scored bars vs the 50% coin flip. Shadow only — it never gates a trade.
- Movers sleeve (separate research book): 100 resolved predictions, ±5% hit rate 36.0% vs a 4.9% base rate; direction 32% on 19 calls; shadow legs sum -2.64%.

## 2. Session integrity

| check | value |
|---|---|
| verdict | **OK** |
| usable for conclusions | **YES** |
| NSE trading day | True |
| live session started | True |
| symbols subscribed | 40 |
| 1-min bars processed | 15034 |
| websocket reconnect attempts | 0 |
| feed stream errors | 0 |
| setups evaluated (ENTRY-CTX) | 26 |
| affordability skips | 18 |
| trades taken | 8 |
| trade count bound by | **drawdown_halt** |
| scheduler jobs missed | 5 |
| engine-log lines for the day | 168 |

- feed healthy, session ran to close

## 3. P&L attribution

| component | value |
|---|---|
| gross (sum of per-trade %) | -3.772% |
| &nbsp;&nbsp;of which index tide | +0.203% |
| &nbsp;&nbsp;of which alpha (picking) | -3.975% |
| friction (charges + slippage) | 0.659% |
| net (sum of per-trade %) | -4.432% |
| **book return (equity-weighted, the truth)** | **-2.588%** |
| realised ₹ | ₹-2,531.47 |
| of which charges | ₹332.39 |
| book equity | ₹97,824.47 -> ₹95,293.01 |
| identity residual (gross-cost-net) | +0.000000pp |

## 4. Transaction-cost analysis

| metric | value |
|---|---|
| cost share of \|gross\| | 17.5% |
| median stop width | 0.660% |
| mean friction in R (recorded charges only) | 0.127R |
| mean friction in R (all-in: charges + modelled slippage) | 0.216R |
| mean implementation shortfall | +0.0314% (n=8) |
| modelled round-trip friction | 0.1424% |
| modelled round-trip charges only | 0.0824% |
| trades where friction decided the outcome | 0 |

## 5. Risk governance

| metric | value |
|---|---|
| largest position (% of book) | 84.9% |
| mean position (% of book) | 42.4% |
| effective concurrent names | 2.36 |
| distinct symbols | 5 |
| best / worst name (₹) | OLAELEC ₹+39 / HFCL ₹-1,217 |
| same-minute entry bursts | 1 |
| intraday max drawdown (equity) | -2.59% |
| session halted | True |
| configured daily drawdown cap | 4.00% |
| configured max concurrent positions | 4 |

Halt message: `⛔ session halt — no new entries: daily drawdown limit hit (drawdown 4.43% from peak +0.00% >= 4.00%) (session -4.43%)
The paper book slipped 4.43% from its best point today, which trips the 4.00% daily safety brake, so no new trades for the rest of the day. Op`

## 6. Findings (evidence class attached to every line)

- [MECHANICAL] (n=8) P&L decomposition: gross -3.77% = tide +0.20% + alpha -3.98%; friction 0.66% -> net -4.43% (per-trade sum). Book equity moved -2.59% (₹97,824 -> ₹95,293, realised ₹-2,531 of which ₹332 was charges).
- [MECHANICAL] (n=8) Per-trade %-sum (-4.43%) and the book's equity-weighted return (-2.59%) differ by 1.84pp because position sizes ranged widely. The book return is the true number; the %-sum is what engine/runner._LossBreaker uses for the daily drawdown cap, so the breaker is measuring a size-blind quantity.
- [MECHANICAL] (n=8) Friction-in-R: median stop 0.66%; recorded charges alone cost 0.127R per trade, and all-in friction (charges + the 0.03%/side slippage already baked into the fills) costs 0.216R per trade. The trade pays that fraction of 1R before the market moves; the P0 gate (risk.max_cost_r=0.25) is what holds it down.
- [MECHANICAL] (n=8) Cost share of |gross| = 17% (pre-registered P0 bar: <40% over 10 gated sessions).
- [MECHANICAL] (n=8) Implementation shortfall: fills landed +0.031% adverse to the surfaced plan price on average — consistent with the modelled 0.03%/side slippage.
- [MECHANICAL] (n=8) Concentration: largest position was 85% of the book, mean 42% — an effective breadth of ~2.4 names. With risk_per_trade_pct=0.5 and the P0 gate forcing stops >=~0.57%, position notional is structurally 88%-of-book scale: the book is a sequence of single-name bets, not a diversified portfolio.
- [MECHANICAL] (n=18) 18 affordability skips: the book had no free cash for even ONE share while further setups were firing. Trade selection was decided by cash exhaustion, not by signal quality — the max_concurrent_positions=4 cap never bound.
- [OPS] (n=8) Session HALTED: ⛔ session halt — no new entries: daily drawdown limit hit (drawdown 4.43% from peak +0.00% >= 4.00%) (session -4.43%)
The paper book slipped 4.43% from its best point today, which trips the 4.00% dail
- [STATISTICAL] (n=8) Single-name concentration of outcome: HFCL accounted for 48% of the day's ₹ move. One name drove the session — the standing 'one name dominates each session' pattern, not a strategy signal. **[LOW-N / NOT ACTIONABLE]**
- [MECHANICAL] (n=8) Correlated-basket risk: 1 minute(s) fired more than one entry (2026-07-22T09:42). Same-minute entries are one bet, not several — effective independent N is below the trade count.
- [STATISTICAL] (n=8) Pre-trade vs post-trade: plans expected a 1.26% move; realised |gross| averaged 0.74%. Planned R:R 2.0:1 vs realised payoff 1.28:1. **[LOW-N / NOT ACTIONABLE]**
- [STATISTICAL] (n=96, sessions=16) Trailing bucket LONG (direction): avgR -0.320 over 96 trades — n clears the n>=20 floor, so this is a candidate for a pre-registered test, NOT a change to apply. NOTE: LONG-vs-SHORT was already killed as a one-session artifact (Fisher p=0.27, sign-flips across sessions). Do not re-derive it. **[NEEDS PRE-REGISTERED TEST]**
- [STATISTICAL] (n=63, sessions=16) Trailing bucket 11:xx (hour): avgR -0.339 over 63 trades — n clears the n>=20 floor, so this is a candidate for a pre-registered test, NOT a change to apply. NOTE: time-of-day was already killed as a one-session artifact in the 3- and 8-session reviews (10-12 bleed, permutation p=0.27, reverses ex-2026-06-25) and was deliberately folded into a pre-registered P4 cohort rather than shipped as a live gate. It needs the standing bar, not a re-run. **[NEEDS PRE-REGISTERED TEST]**
- [STATISTICAL] (n=30, sessions=16) Trailing bucket 10:xx (hour): avgR -0.548 over 30 trades — n clears the n>=20 floor, so this is a candidate for a pre-registered test, NOT a change to apply. NOTE: time-of-day was already killed as a one-session artifact in the 3- and 8-session reviews (10-12 bleed, permutation p=0.27, reverses ex-2026-06-25) and was deliberately folded into a pre-registered P4 cohort rather than shipped as a live gate. It needs the standing bar, not a re-run. **[NEEDS PRE-REGISTERED TEST]**
- [STATISTICAL] (n=92, sessions=16) Trailing bucket stop 0.0-0.5% (stop_band): avgR -0.414 over 92 trades — n clears the n>=20 floor, so this is a candidate for a pre-registered test, NOT a change to apply. **[NEEDS PRE-REGISTERED TEST]**
- [STATISTICAL] (n=114, sessions=16) Trailing bucket no rules logged (rules): avgR -0.315 over 114 trades — n clears the n>=20 floor, so this is a candidate for a pre-registered test, NOT a change to apply. **[NEEDS PRE-REGISTERED TEST]**
- [STATISTICAL] (n=177, sessions=16) Trailing 16 sessions / 177 trades: gross -2.88%, friction 14.58%, net -17.46%; win rate 39.5%, avgR -0.257. Below the >=10-session floor — descriptive only, not a verdict on the strategy. CAVEAT: this window SPANS the 2026-07-13 configuration change (the P0 friction-in-R gate), so it mixes a ~15-trades/day regime with a ~5-trades/day one. It is context, NOT a verdict on the engine as configured — use the `gated` scope for that. **[MIXED REGIME]**

## 7. Per-trade forensics

| # | symbol | dir | entry | exit | reason | stop% | gross% | cost% | net% | R | MFE% | MAE% | captured | %book | ₹ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | NETWEB | LONG | 09:42 | 09:54 | STOP | 0.696 | -0.725 | 0.0824 | -0.808 | -1.24 | -0.12 | -0.69 | never favourable | 31 | -253 |
| 2 | OLAELEC | SHORT | 09:42 | 10:21 | TARGET | 0.660 | +1.070 | 0.0824 | +0.987 | +1.69 | +1.10 | -0.16 | 97% | 4 | +39 |
| 3 | HFCL | SHORT | 09:42 | 10:55 | STOP | 0.830 | -0.860 | 0.0824 | -0.942 | -1.22 | +0.88 | -0.91 | 0% (was +0.88% at best, gave it all back) | 65 | -612 |
| 4 | PPLPHARMA | SHORT | 09:55 | 09:56 | STOP | 0.802 | -0.832 | 0.0824 | -0.915 | -1.33 | -0.34 | -0.81 | never favourable | 31 | -283 |
| 5 | PPLPHARMA | SHORT | 10:07 | 10:38 | STOP | 0.616 | -0.646 | 0.0824 | -0.728 | -1.27 | +0.95 | -1.03 | 0% (was +0.95% at best, gave it all back) | 30 | -224 |
| 6 | SUZLON | LONG | 11:09 | 12:04 | STOP | 0.592 | -0.622 | 0.0824 | -0.704 | -1.22 | +0.34 | -0.74 | 0% (was +0.34% at best, gave it all back) | 85 | -593 |
| 7 | HFCL | SHORT | 11:20 | 11:24 | STOP | 0.562 | -0.592 | 0.0824 | -0.674 | -1.16 | +0.82 | -0.67 | 0% (was +0.82% at best, gave it all back) | 13 | -92 |
| 8 | HFCL | LONG | 13:24 | 13:24 | STOP | 0.536 | -0.565 | 0.0825 | -0.648 | -1.05 | n/a | n/a | n/a | 79 | -513 |

### Counterfactual exits (in-sample; `optimal` is perfect-foresight)

| # | symbol | recorded net% | baseline | trail | hold90 | wider stop | tighter stop | optimal |
|---|---|---|---|---|---|---|---|---|
| 1 | NETWEB | -0.808 | -0.808 | -0.808 | -0.808 | -0.209 | -0.578 | -0.204 |
| 2 | OLAELEC | +0.987 | +0.987 | +2.087 | +1.855 | +0.987 | +0.987 | +1.017 |
| 3 | HFCL | -0.942 | -0.942 | -0.064 | -0.942 | -1.357 | -0.669 | +0.796 |
| 4 | PPLPHARMA | -0.915 | -0.915 | -0.915 | -0.915 | -1.316 | -0.650 | -0.425 |
| 5 | PPLPHARMA | -0.728 | -0.728 | +0.188 | -0.728 | -1.036 | -0.525 | +0.868 |
| 6 | SUZLON | -0.704 | -0.704 | -0.360 | -0.704 | -1.000 | -0.509 | +0.262 |
| 7 | HFCL | -0.674 | -0.674 | +0.146 | -0.674 | -0.955 | -0.489 | +0.737 |
| 8 | HFCL | -0.648 | -0.648 | +0.463 | -0.648 | -0.916 | -0.471 | n/a |

Harness fidelity: `baseline` should reproduce `recorded net%` closely (same bars, same rules — residuals are live-tick-vs-bar timing). A large divergence means the replay is wrong, not that the strategy is different. **Every column here is in-sample and chosen from a fixed, unswept set; none is evidence.**

**Standing prior that outranks this table:** the `trail` column will often look good on a handful of trades. It has already been tested properly — a 108-trade archive replay (STRATEGY_IMPROVEMENT_PLAN_2026-07 §P4) found trailing exits WORSE overall (-4.97% vs -3.85% baseline) and, on the P0-gated cohort specifically, baseline exits win (avgR +0.195 vs trail +0.142). A 8-trade in-sample table does not overturn that. Exits stay unchanged until the session-18 look.

### The desk's standing questions, answered per trade

**1. NETWEB LONG** (`NETWEB-2026-07-22T09:41:00+05:30-1`)

- Why this stock: above VWAP, EMA fast>slow, ADX 26, RSI 47
- Entry timing: filled at 115% of the entry bar's range (>100% = adverse slippage carried the fill outside the bar's range, which is the modelled behaviour, not an error), 69% of the day's range so far; tape moved +0.16% in the 15min before and -0.69% in the 15min after
- Exit: MFE -0.12% (-0.18R) / MAE -0.69% (-0.99R); MFE was never positive, so the trade never went the right way at any point and no exit rule could have saved it
- Friction: cost 0.082% = 11% of |gross|
- Alpha vs tide: alpha -0.69%, index contribution -0.03%
- Sizing: 31% of the book at entry (₹30,459 notional), planned risk ₹480, realised ₹-253

**2. OLAELEC SHORT** (`OLAELEC-2026-07-22T09:41:00+05:30-2`)

- Why this stock: below VWAP, EMA fast<slow, ADX 62, RSI 43
- Entry timing: filled at 37% of the entry bar's range, 5% of the day's range so far; tape moved -0.76% in the 15min before and +0.21% in the 15min after
- Exit: MFE +1.10% (+1.66R) / MAE -0.16% (-0.24R); captured 97% of the favourable excursion
- Friction: cost 0.082% = 8% of |gross|
- Alpha vs tide: alpha +1.02%, index contribution +0.05%
- Sizing: 4% of the book at entry (₹3,997 notional), planned risk ₹489, realised ₹39

**3. HFCL SHORT** (`HFCL-2026-07-22T09:41:00+05:30-0`)

- Why this stock: below VWAP, EMA fast<slow, ADX 26, RSI 61
- Entry timing: filled at 110% of the entry bar's range (>100% = adverse slippage carried the fill outside the bar's range, which is the modelled behaviour, not an error), 29% of the day's range so far; tape moved -0.47% in the 15min before and +0.82% in the 15min after
- Exit: MFE +0.88% (+1.06R) / MAE -0.91% (-1.10R); it was up +0.88% at best and still closed negative — the whole favourable excursion was given back
- Friction: cost 0.082% = 10% of |gross|
- Alpha vs tide: alpha -0.90%, index contribution +0.04%
- Sizing: 65% of the book at entry (₹63,296 notional), planned risk ₹489, realised ₹-612

**4. PPLPHARMA SHORT** (`PPLPHARMA-2026-07-22T09:54:00+05:30-3`)

- Why this stock: below VWAP, EMA fast<slow, ADX 50, RVOL 1.4x
- Entry timing: filled at -8% of the entry bar's range, 1% of the day's range so far; tape moved -1.20% in the 15min before and -0.73% in the 15min after
- Exit: MFE -0.34% (-0.43R) / MAE -0.81% (-1.01R); MFE was never positive, so the trade never went the right way at any point and no exit rule could have saved it
- Friction: cost 0.082% = 10% of |gross|
- Alpha vs tide: alpha -0.85%, index contribution +0.02%
- Sizing: 31% of the book at entry (₹30,128 notional), planned risk ₹488, realised ₹-283

**5. PPLPHARMA SHORT** (`PPLPHARMA-2026-07-22T10:06:00+05:30-4`)

- Why this stock: below VWAP, EMA fast<slow, ADX 37, RSI 46
- Entry timing: filled at -3% of the entry bar's range, 18% of the day's range so far; tape moved +0.39% in the 15min before and +0.41% in the 15min after
- Exit: MFE +0.95% (+1.54R) / MAE -1.03% (-1.67R); it was up +0.95% at best and still closed negative — the whole favourable excursion was given back
- Friction: cost 0.082% = 13% of |gross|
- Alpha vs tide: alpha -0.87%, index contribution +0.23%
- Sizing: 30% of the book at entry (₹29,824 notional), planned risk ₹486, realised ₹-224

**6. SUZLON LONG** (`SUZLON-2026-07-22T11:08:00+05:30-5`)

- Why this stock: above VWAP, EMA fast>slow, ADX 48, RSI 68
- Entry timing: filled at 56% of the entry bar's range, 78% of the day's range so far; tape moved +0.71% in the 15min before and +0.21% in the 15min after
- Exit: MFE +0.34% (+0.58R) / MAE -0.74% (-1.25R); it was up +0.34% at best and still closed negative — the whole favourable excursion was given back
- Friction: cost 0.082% = 13% of |gross|
- Alpha vs tide: alpha -0.57%, index contribution -0.05%
- Sizing: 85% of the book at entry (₹83,100 notional), planned risk ₹482, realised ₹-593

**7. HFCL SHORT** (`HFCL-2026-07-22T11:19:00+05:30-6`)

- Why this stock: below VWAP, EMA fast<slow, ADX 27, RVOL 2.5x
- Entry timing: filled at 59% of the entry bar's range, 13% of the day's range so far; tape moved -1.83% in the 15min before and -1.28% in the 15min after
- Exit: MFE +0.82% (+1.46R) / MAE -0.67% (-1.19R); it was up +0.82% at best and still closed negative — the whole favourable excursion was given back
- Friction: cost 0.082% = 14% of |gross|
- Alpha vs tide: alpha -0.55%, index contribution -0.05%
- Sizing: 13% of the book at entry (₹13,205 notional), planned risk ₹481, realised ₹-92

**8. HFCL LONG** (`HFCL-2026-07-22T13:23:00+05:30-7`)

- Why this stock: above VWAP, EMA fast>slow, ADX 37, RVOL 4.9x
- Entry timing: filled at 88% of the entry bar's range, 78% of the day's range so far; tape moved +0.54% in the 15min before and -0.27% in the 15min after
- Friction: cost 0.082% = 15% of |gross|
- Alpha vs tide: alpha -0.57%, index contribution +0.00%
- Sizing: 79% of the book at entry (₹77,560 notional), planned risk ₹479, realised ₹-513

## 8. What the gates and the book rejected

- skip alerts: 18 | advice alerts (setups surfaced): 27 | setups evaluated in log: 26 | affordability skips: 18
- skips by symbol: {'PPLPHARMA': 13, 'ZENTEC': 5}
- surfaced but never traded: ZENTEC
- **Gate-level rejections inside RiskManager.build_trade_plan are NOT persisted, and skip rows carry no stop/target, so the archive cannot price what the gates threw away. Counterfactual P&L for rejects is UNAVAILABLE, not zero.**

## 9. Bucket analysis

Session buckets are structurally low-n (4-8 trades/day) and are shown for completeness only. Trailing buckets are where n can eventually clear the >=20 floor. Anything tagged NOT ACTIONABLE must not be acted on.

**session:direction**

```
SHORT                    n=5    WR=  20%  sumNet=  -2.27%  avgR=-0.660  ₹   -1,173  **[LOW-N / NOT ACTIONABLE]**
LONG                     n=3    WR=   0%  sumNet=  -2.16%  avgR=-1.172  ₹   -1,359  **[LOW-N / NOT ACTIONABLE]**
```

**session:exit_reason**

```
STOP                     n=7    WR=   0%  sumNet=  -5.42%  avgR=-1.215  ₹   -2,570  **[LOW-N / NOT ACTIONABLE]**
TARGET                   n=1    WR= 100%  sumNet=  +0.99%  avgR=+1.689  ₹       39  **[LOW-N / NOT ACTIONABLE]**
```

**session:hour**

```
09:xx                    n=4    WR=  25%  sumNet=  -1.68%  avgR=-0.527  ₹   -1,109  **[LOW-N / NOT ACTIONABLE]**
11:xx                    n=2    WR=   0%  sumNet=  -1.38%  avgR=-1.192  ₹     -685  **[LOW-N / NOT ACTIONABLE]**
10:xx                    n=1    WR=   0%  sumNet=  -0.73%  avgR=-1.272  ₹     -224  **[LOW-N / NOT ACTIONABLE]**
13:xx                    n=1    WR=   0%  sumNet=  -0.65%  avgR=-1.052  ₹     -513  **[LOW-N / NOT ACTIONABLE]**
```

**session:rules**

```
ADX 26|EMA fast<slow|RSI 61|below VWAP n=1    WR=   0%  sumNet=  -0.94%  avgR=-1.222  ₹     -612  **[LOW-N / NOT ACTIONABLE]**
ADX 50|EMA fast<slow|RVOL 1.4x|below VWAP n=1    WR=   0%  sumNet=  -0.91%  avgR=-1.329  ₹     -283  **[LOW-N / NOT ACTIONABLE]**
ADX 26|EMA fast>slow|RSI 47|above VWAP n=1    WR=   0%  sumNet=  -0.81%  avgR=-1.243  ₹     -253  **[LOW-N / NOT ACTIONABLE]**
ADX 37|EMA fast<slow|RSI 46|below VWAP n=1    WR=   0%  sumNet=  -0.73%  avgR=-1.272  ₹     -224  **[LOW-N / NOT ACTIONABLE]**
ADX 48|EMA fast>slow|RSI 68|above VWAP n=1    WR=   0%  sumNet=  -0.70%  avgR=-1.220  ₹     -593  **[LOW-N / NOT ACTIONABLE]**
ADX 27|EMA fast<slow|RVOL 2.5x|below VWAP n=1    WR=   0%  sumNet=  -0.67%  avgR=-1.163  ₹      -92  **[LOW-N / NOT ACTIONABLE]**
ADX 37|EMA fast>slow|RVOL 4.9x|above VWAP n=1    WR=   0%  sumNet=  -0.65%  avgR=-1.052  ₹     -513  **[LOW-N / NOT ACTIONABLE]**
ADX 62|EMA fast<slow|RSI 43|below VWAP n=1    WR= 100%  sumNet=  +0.99%  avgR=+1.689  ₹       39  **[LOW-N / NOT ACTIONABLE]**
```

**session:stop_band**

```
stop 0.5-0.8%            n=6    WR=  17%  sumNet=  -2.57%  avgR=-0.710  ₹   -1,637  **[LOW-N / NOT ACTIONABLE]**
stop 0.8-1.2%            n=2    WR=   0%  sumNet=  -1.86%  avgR=-1.276  ₹     -895  **[LOW-N / NOT ACTIONABLE]**
```

**session:symbol**

```
HFCL                     n=3    WR=   0%  sumNet=  -2.26%  avgR=-1.146  ₹   -1,217  **[LOW-N / NOT ACTIONABLE]**
PPLPHARMA                n=2    WR=   0%  sumNet=  -1.64%  avgR=-1.301  ₹     -507  **[LOW-N / NOT ACTIONABLE]**
NETWEB                   n=1    WR=   0%  sumNet=  -0.81%  avgR=-1.243  ₹     -253  **[LOW-N / NOT ACTIONABLE]**
SUZLON                   n=1    WR=   0%  sumNet=  -0.70%  avgR=-1.220  ₹     -593  **[LOW-N / NOT ACTIONABLE]**
OLAELEC                  n=1    WR= 100%  sumNet=  +0.99%  avgR=+1.689  ₹       39  **[LOW-N / NOT ACTIONABLE]**
```

**trailing:direction**

```
LONG                     n=96   WR=  34%  sumNet= -11.59%  avgR=-0.320  ₹   -2,759
SHORT                    n=81   WR=  46%  sumNet=  -5.87%  avgR=-0.182  ₹   -1,948
```

**trailing:exit_reason**

```
STOP                     n=89   WR=   0%  sumNet= -53.08%  avgR=-1.339  ₹  -11,957
SQUARE_OFF               n=3    WR=  67%  sumNet=  +0.01%  avgR=+0.214  ₹     -498  **[LOW-N / NOT ACTIONABLE]**
TIME_STOP                n=40   WR=  57%  sumNet=  +3.76%  avgR=+0.171  ₹    1,660
TARGET                   n=45   WR= 100%  sumNet= +31.84%  avgR=+1.470  ₹    6,088
```

**trailing:hour**

```
11:xx                    n=63   WR=  40%  sumNet=  -7.21%  avgR=-0.339  ₹   -1,520
09:xx                    n=63   WR=  41%  sumNet=  -5.17%  avgR=-0.133  ₹   -2,205
10:xx                    n=30   WR=  27%  sumNet=  -4.95%  avgR=-0.548  ₹     -294
12:xx                    n=2    WR=   0%  sumNet=  -0.86%  avgR=-0.744  ₹     -592  **[LOW-N / NOT ACTIONABLE]**
14:xx                    n=17   WR=  59%  sumNet=  +0.31%  avgR=+0.081  ₹     -498  **[LOW-N / NOT ACTIONABLE]**
13:xx                    n=2    WR=  50%  sumNet=  +0.42%  avgR=+0.414  ₹      401  **[LOW-N / NOT ACTIONABLE]**
```

**trailing:rules**

```
no rules logged          n=114  WR=  39%  sumNet= -14.20%  avgR=-0.315  ₹   -1,958
ADX 50|EMA fast<slow|RVOL 1.4x|below VWAP n=15   WR=  33%  sumNet=  -3.54%  avgR=-0.352  ₹     -414  **[LOW-N / NOT ACTIONABLE]**
ADX 26|EMA fast>slow|RSI 47|above VWAP n=7    WR=  29%  sumNet=  -3.06%  avgR=-0.619  ₹     -812  **[LOW-N / NOT ACTIONABLE]**
ADX 48|EMA fast>slow|RSI 68|above VWAP n=6    WR=   0%  sumNet=  -2.43%  avgR=-1.097  ₹     -596  **[LOW-N / NOT ACTIONABLE]**
ADX 37|EMA fast<slow|RSI 46|below VWAP n=1    WR=   0%  sumNet=  -0.73%  avgR=-1.272  ₹     -224  **[LOW-N / NOT ACTIONABLE]**
ADX 27|EMA fast<slow|RVOL 2.5x|below VWAP n=1    WR=   0%  sumNet=  -0.67%  avgR=-1.163  ₹      -92  **[LOW-N / NOT ACTIONABLE]**
ADX 37|EMA fast>slow|RVOL 4.9x|above VWAP n=1    WR=   0%  sumNet=  -0.65%  avgR=-1.052  ₹     -513  **[LOW-N / NOT ACTIONABLE]**
ADX 26|EMA fast<slow|RSI 61|below VWAP n=13   WR=  46%  sumNet=  +1.77%  avgR=+0.082  ₹    1,051  **[LOW-N / NOT ACTIONABLE]**
ADX 62|EMA fast<slow|RSI 43|below VWAP n=19   WR=  63%  sumNet=  +6.04%  avgR=+0.477  ₹   -1,148  **[LOW-N / NOT ACTIONABLE]**
```

**trailing:stop_band**

```
stop 0.0-0.5%            n=92   WR=  38%  sumNet= -13.16%  avgR=-0.414  ₹     -854
stop 0.5-0.8%            n=62   WR=  42%  sumNet=  -3.87%  avgR=-0.110  ₹   -2,393
stop >=1.2%              n=1    WR=   0%  sumNet=  -1.23%  avgR=-0.914  ₹        0  **[LOW-N / NOT ACTIONABLE]**
stop 0.8-1.2%            n=22   WR=  41%  sumNet=  +0.80%  avgR=+0.016  ₹   -1,460
```

**trailing:symbol**

```
PPLPHARMA                n=16   WR=  31%  sumNet=  -4.27%  avgR=-0.410  ₹     -638  **[LOW-N / NOT ACTIONABLE]**
NETWEB                   n=7    WR=  29%  sumNet=  -3.06%  avgR=-0.619  ₹     -812  **[LOW-N / NOT ACTIONABLE]**
SUZLON                   n=6    WR=   0%  sumNet=  -2.43%  avgR=-1.097  ₹     -596  **[LOW-N / NOT ACTIONABLE]**
YESBANK                  n=7    WR=  29%  sumNet=  -2.08%  avgR=-0.915  ₹       59  **[LOW-N / NOT ACTIONABLE]**
BDL                      n=6    WR=  33%  sumNet=  -2.06%  avgR=-0.513  ₹   -1,257  **[LOW-N / NOT ACTIONABLE]**
KAYNES                   n=5    WR=  20%  sumNet=  -1.87%  avgR=-0.728  ₹     -212  **[LOW-N / NOT ACTIONABLE]**
GMRAIRPORT               n=8    WR=  38%  sumNet=  -1.80%  avgR=-0.387  ₹      348  **[LOW-N / NOT ACTIONABLE]**
ZENTEC                   n=8    WR=  38%  sumNet=  -1.68%  avgR=-0.622  ₹      325  **[LOW-N / NOT ACTIONABLE]**
IDEA                     n=8    WR=  25%  sumNet=  -1.62%  avgR=-0.617  ₹        0  **[LOW-N / NOT ACTIONABLE]**
ETERNAL                  n=4    WR=  25%  sumNet=  -1.22%  avgR=-0.969  ₹       18  **[LOW-N / NOT ACTIONABLE]**
DIXON                    n=6    WR=  33%  sumNet=  -1.07%  avgR=-0.483  ₹     -116  **[LOW-N / NOT ACTIONABLE]**
SWIGGY                   n=9    WR=  56%  sumNet=  -0.66%  avgR=+0.075  ₹      276  **[LOW-N / NOT ACTIONABLE]**
```

## 10. The +1.00%/day gap model

### Session scope

| quantity | value |
|---|---|
| trades/day | 8.00 |
| win rate | 12.5% |
| mean winner / mean loser magnitude (% of book) | 0.039 / 0.375 |
| realised payoff | 0.10 |
| realised daily book return | -2.588 |
| friction (% of book/day) | 0.340 |
| friction as a share of the 1% target | 34.0% |
| **required win rate** | **120.6%** |
| **win-rate gap** | **+108.1 pp** |
| required payoff at the current win rate | 9.66 |
| required net edge per trade | 0.125% of book (₹122) |
| required net edge per trade in R | +0.45 |
| required gross per day | 1.340% of book |
| closable by parameter work | **NO** |

**+1.00%/day is UNREACHABLE at the current payoff (0.10:1) and trade count (8.0/day): even a 100% win rate yields only +0.32%/day. The gap is not a win-rate gap — it is a payoff-and-size gap.**

What would have to be true:

- Win rate: would need 121% — impossible. Rules out the win-rate route entirely at this payoff and trade count.
- Payoff: 0.10:1 -> 9.66:1 at the current 12% win rate — winners would have to be 92.0x their current size relative to losers.
- Per-trade net edge: +0.125% of the book (~₹122) on every trade = +0.45R per trade at the realised stop width and position size. The realised per-trade average is -0.323% of the book.
- Gross: +1.34% of the book per day before friction (target 1.00% + 0.340% friction). Realised gross is the only thing that can pay for friction; friction is deterministic.
- VERDICT: not closable by parameter work. The binding constraint is the size of the edge per trade, and there is no measured gross edge to scale.

- _Even at a 100% win rate, 8.0 trades/day x 0.039% mean winner = 0.32%/day < 1.00% target. Win rate cannot close this; only bigger winners or more trades can — and both raise the friction bill._
- _+1.00%/day compounds to +1103%/year over 250 sessions. For scale: the best systematic funds target ~1-3% per MONTH gross._
- _Friction alone consumed 34% of a 1% day (0.340% of the book in charges + modelled slippage over 8.0 trades/day)._

### Gated scope

| quantity | value |
|---|---|
| trades/day | 5.12 |
| win rate | 26.8% |
| mean winner / mean loser magnitude (% of book) | 0.404 / 0.304 |
| realised payoff | 1.33 |
| realised daily book return | -0.587 |
| friction (% of book/day) | 0.220 |
| friction as a share of the 1% target | 22.0% |
| **required win rate** | **70.6%** |
| **win-rate gap** | **+43.7 pp** |
| required payoff at the current win rate | 5.12 |
| required net edge per trade | 0.195% of book (₹191) |
| required net edge per trade in R | +0.63 |
| required gross per day | 1.220% of book |
| closable by parameter work | **NO** |

**+1.00%/day needs a 71% win rate at the realised 1.33:1 payoff and 5.1 trades/day; realised win rate is 27% (n=41 trades over 8 session(s)). Gap: 44pp. No parameter change bridges 44pp of win rate — this is a SIGNAL problem, not a settings problem.**

What would have to be true:

- Win rate: 27% -> 71% (a 44pp improvement) at the current 1.33:1 payoff and 5.1 trades/day.
- Payoff: 1.33:1 -> 5.12:1 at the current 27% win rate — winners would have to be 3.9x their current size relative to losers.
- Per-trade net edge: +0.195% of the book (~₹191) on every trade = +0.63R per trade at the realised stop width and position size. The realised per-trade average is -0.115% of the book.
- Gross: +1.22% of the book per day before friction (target 1.00% + 0.220% friction). Realised gross is the only thing that can pay for friction; friction is deterministic.

- _Scope = sessions on/after 2026-07-13 only (the P0 friction-in-R gate went live that day); 8 session(s) in window._
- _+1.00%/day compounds to +1103%/year over 250 sessions. For scale: the best systematic funds target ~1-3% per MONTH gross._
- _Friction alone consumed 22% of a 1% day (0.220% of the book in charges + modelled slippage over 5.1 trades/day)._

### Trailing scope

| quantity | value |
|---|---|
| trades/day | 11.06 |
| win rate | 39.0% |
| mean winner / mean loser magnitude (% of book) | 0.457 / 0.374 |
| realised payoff | 1.22 |
| realised daily book return | -0.554 |
| friction (% of book/day) | 0.205 |
| friction as a share of the 1% target | 20.5% |
| **required win rate** | **55.9%** |
| **win-rate gap** | **+16.9 pp** |
| required payoff at the current win rate | 2.19 |
| required net edge per trade | 0.090% of book (₹88) |
| required net edge per trade in R | +0.44 |
| required gross per day | 1.205% of book |
| closable by parameter work | **NO** |

**+1.00%/day needs a 56% win rate at the realised 1.22:1 payoff and 11.1 trades/day; realised win rate is 39% (n=177 trades over 16 session(s)). Gap: 17pp. No parameter change bridges 17pp of win rate — this is a SIGNAL problem, not a settings problem.**

What would have to be true:

- Win rate: 39% -> 56% (a 17pp improvement) at the current 1.22:1 payoff and 11.1 trades/day.
- Payoff: 1.22:1 -> 2.19:1 at the current 39% win rate — winners would have to be 1.8x their current size relative to losers.
- Per-trade net edge: +0.090% of the book (~₹88) on every trade = +0.44R per trade at the realised stop width and position size. The realised per-trade average is -0.050% of the book.
- Gross: +1.21% of the book per day before friction (target 1.00% + 0.205% friction). Realised gross is the only thing that can pay for friction; friction is deterministic.

- _DATA QUALITY: 91 of 177 trades in scope predate the ₹ ledger and have no notional, so their contribution is approximated by the raw per-trade % — which OVERSTATES their weight. Treat this scope's win rate and payoff as indicative; the `gated` scope is the clean read._
- _+1.00%/day compounds to +1103%/year over 250 sessions. For scale: the best systematic funds target ~1-3% per MONTH gross._
- _Friction alone consumed 21% of a 1% day (0.205% of the book in charges + modelled slippage over 11.1 trades/day)._

### Gap time series (trailing scope, newest first)

| day | trades/day | win rate | required WR | gap pp | closable |
|---|---|---|---|---|---|
| 2026-07-23 | +11.06 | 39.0% | 55.9% | +16.9 | NO |
| 2026-07-22 | +11.06 | 39.0% | 55.9% | +16.9 | NO |

## 11. Hypothesis ledger

### Grading of previously-open hypotheses

- `H-20260722-03` **OPEN** — Fixing friction is NOT sufficient: even with cost share inside the bar, the gated book's avgR stays below the -0.05 floor, which would locate the verdict on the SIGNAL rather than the plumbing.
  - Not graded on 2026-07-22: evidence floor not met (8 of 10 sessions, 41 of 40 trades). The pre-registered test stands; no peeking-and-tweaking in between.
- `H-20260722-02` **OPEN** — With the P0 friction-in-R gate live, friction's share of |gross| settles below the pre-registered 40% bar.
  - Not graded on 2026-07-22: evidence floor not met (8 of 10 sessions, 41 of 40 trades). The pre-registered test stands; no peeking-and-tweaking in between.
- `H-20260722-01` **OPEN** — Position notional averaging ~42% of the book makes daily P&L a single-name lottery; capping notional per position would cut daily variance without touching the entry signal.
  - Not graded on 2026-07-22: evidence floor not met (8 of 10 sessions, 41 of 40 trades). The pre-registered test stands; no peeking-and-tweaking in between.

### New hypotheses opened tonight (pre-registered tests, written before grading)

- `H-20260722-01` Position notional averaging ~42% of the book makes daily P&L a single-name lottery; capping notional per position would cut daily variance without touching the entry signal.
  - rationale: risk_per_trade_pct=0.5 divided by a P0-mandated stop of >=~0.57% forces notional to ~42% of equity per trade, so the book runs 1-2 effective names and exhausts cash before the concurrency cap binds (18 affordability skips today).
  - pre-registered test: Over the next >=10 sessions, mean position notional as a % of book falls below 30% under a gated cap, AND avgR does not deteriorate. Graded on metric `mean_pct_of_book`.
  - falsifiable prediction: If the cap is enabled, mean_pct_of_book < 30 within 10 sessions; if it stays >=30 the cap is not binding and the hypothesis is refuted.
  - graded on `mean_pct_of_book` < 30.0 once >=10 sessions and >=40 trades exist
- `H-20260722-02` With the P0 friction-in-R gate live, friction's share of |gross| settles below the pre-registered 40% bar.
  - rationale: This is the frozen P0 bar from STRATEGY_IMPROVEMENT_PLAN_2026-07 §P0(a). Tonight's session share is 17%.
  - pre-registered test: cost_share_of_gross < 0.40 measured over >=10 gated sessions and >=40 trades (`scripts/preregistered_eval.py` owns the committed look; this ledger entry mirrors it).
  - falsifiable prediction: cost_share_of_gross < 0.40 at the 10-session mark.
  - graded on `cost_share_of_gross` < 0.4 once >=10 sessions and >=40 trades exist
- `H-20260722-03` Fixing friction is NOT sufficient: even with cost share inside the bar, the gated book's avgR stays below the -0.05 floor, which would locate the verdict on the SIGNAL rather than the plumbing.
  - rationale: P0's pre-registered branch: '(a) passes and (c) fails => the signal itself is dead'. Stated as a hypothesis so it gets graded rather than argued.
  - pre-registered test: avg_r > -0.05 over >=10 gated sessions and >=40 trades. SUPPORTED means the signal survives; REFUTED means move to P4/data priorities.
  - falsifiable prediction: avg_r > -0.05 at the 10-session mark.
  - graded on `avg_r` > -0.05 once >=10 sessions and >=40 trades exist

## 12. Ranked proposals — **NOT APPLIED**

Every proposal below is a written diff for a human to apply. The desk has no code path that edits a config file or flips a flag, and `desk_proposals.applied` is hard-coded to 0 on insert.

### P1 — `risk.max_position_pct_of_equity` in `config/risk.yaml` (`P-20260722-01`)

- current: `(absent — no per-position notional cap exists)`
- proposed: `0.0   # 0 == OFF (default). Try 25.0 after the evidence bar is met.`
- rationale: Sizing is derived from risk_per_trade_pct / stop_pct. With the P0 gate forcing wide stops the quotient lands at ~42% of equity, so the book holds 1-2 names and the 4 concurrency cap is decorative. This is an arithmetic observation about the sizing formula, not a fitted result.
- **evidence bar (pre-registered):** Ship gated, DEFAULT 0. Validate on the archive replay first (does a 25% cap change the retained trade set?), then >=10 shadow sessions with mean_pct_of_book < 30 AND avgR no worse than the uncapped cohort. Human applies; the desk never flips it.
- expected effect: IN-SAMPLE ONLY: would have reduced today's largest position from 85% to 25% of the book. Says nothing about P&L — smaller positions shrink both tails.
- effort: ~0.5 day incl. tests

```diff
--- a/config/risk.yaml
+++ b/config/risk.yaml
@@ risk:
   max_concurrent_positions: 4
+  # DESK PROPOSAL (default OFF). Cap one position's notional as a % of live
+  # equity. Today the largest position was 85% of the book and the mean was
+  # 42%, so max_concurrent_positions never binds — cash exhaustion picks the
+  # trades instead of the signal. 0 == off; the human enables it, not the desk.
+  max_position_pct_of_equity: 0.0
```

### P2 — `risk.drawdown_uses_book_equity` in `signal_engine/engine/runner.py` (`P-20260722-02`)

- current: `(absent — _LossBreaker sums per-trade pnl_pct_net)`
- proposed: `false   # default OFF; true switches the breaker to equity-weighted`
- rationale: The daily drawdown breaker halted the session at a summed per-trade -4.43% while the book's real give-back was -2.59%. Positions ran at 42-85% of equity, so the two quantities cannot agree. This is a measurement-correctness bug in a RISK GATE, independent of whether the strategy has edge.
- **evidence bar (pre-registered):** Not a P&L claim, so no P&L bar: correctness is demonstrated by (i) a unit test showing the breaker's drawdown equals the ledger's equity drawdown on a synthetic mixed-size session, and (ii) an archive replay of 2026-07-22 reproducing the halt at the equity-weighted threshold. Ships gated, DEFAULT OFF, because it changes live halting behaviour.
- expected effect: Would have moved today's halt: the equity-weighted drawdown was 2.59% against a 4.00% cap, so the session would NOT have halted at 13:25. That could be better or worse — untested.
- effort: ~0.5 day incl. tests + replay

```diff
--- a/signal_engine/engine/runner.py
+++ b/signal_engine/engine/runner.py
@@ class _LossBreaker:
-        pnl = float(pnl_pct_net or 0.0)
-        self.realized_pnl_pct += pnl
+        # DESK PROPOSAL (gated, default OFF): the breaker currently sums
+        # per-trade percentages, which is size-blind. On 2026-07-22 the sum was
+        # -4.43% while the book moved -2.59% — the halt fired on a number
+        # 1.84pp away from the book's actual give-back. When
+        # risk.drawdown_uses_book_equity is true, fold in ledger equity instead.
+        pnl = float(pnl_pct_net or 0.0) if not self.use_book_equity \
+            else float(pnl_inr or 0.0) / max(1.0, opening_equity) * 100.0
+        self.realized_pnl_pct += pnl
```

### P3 — `observability.persist_gate_rejections` in `signal_engine/risk/manager.py` (`P-20260722-03`)

- current: `(absent — gate rejections are never persisted)`
- proposed: `true   # write-only instrumentation, no behaviour change`
- rationale: A gate that rejects winners is the most expensive kind of bug, and it is currently unmeasurable: 26 setups were evaluated and 18 affordability skips were logged, but no rejected plan's geometry is stored, so the bar archive cannot price what was thrown away.
- **evidence bar (pre-registered):** Pure instrumentation — no live behaviour changes, so the bar is a passing test that a rejection is written and that the write cannot raise into the entry path. Value is realised at the NEXT review, which can then replay rejects.
- expected effect: No P&L effect. Makes one currently-unanswerable question answerable.
- effort: ~0.5 day incl. tests

```diff
--- a/signal_engine/risk/manager.py
+++ b/signal_engine/risk/manager.py
@@ RiskManager.build_trade_plan
+        # DESK PROPOSAL: persist every rejection (symbol, ts, direction,
+        # entry, stop, target, the failing gate, and the gate's numbers) to a
+        # `plan_rejections` table. Today the desk CANNOT answer 'did the
+        # gates reject winners?' because rejected plans are discarded before
+        # they become rows — the most expensive class of bug is invisible.
```

### P4 — `research.priority` in `docs/EDGE_ROADMAP.md` (`P-20260722-04`)

- current: `survivorship-clean data build (standing #1)`
- proposed: `unchanged — reaffirmed by tonight's gap arithmetic`
- rationale: Parameter work cannot bridge the measured gap; proposing a tuning change here would be the desk manufacturing false progress.
- **evidence bar (pre-registered):** Any new signal from that data must clear the standing bar: n>=2000 replayed, OOS split, PBO<0.10, plus DSR/multiple-testing correction (signal_engine/research/overfitting.py)
- expected effect: No live change. Redirects effort.
- effort: data/infra project

```diff
(no code change proposed)
The trailing gap to +1.00%/day is 44pp of win rate. No parameter in config/risk.yaml moves win rate by 44pp; the binding constraint is the absence of a measured gross edge. The honest proposal is therefore to spend effort on the STRUCTURAL items (survivorship-clean + delisting-inclusive bhavcopy panel, delivery-%/volume features) rather than on tuning.
```

## 13. Kill criteria (stated up front, as a desk would)

- KILL 1 (already pre-registered, P0 §evidence bar): at the 10-gated-session look, if cost share of |gross| is under 40% AND the book is still net-negative, the verdict is on the SIGNAL, not the plumbing — stop tuning `vwap_ema_adx` gates and exits.
- KILL 2: if trailing avgR's bootstrap 95% CI remains entirely below 0 at n>=318 trades (the powered-verdict count computed in the 8-session review), retire the strategy from the live paper book and keep it only as a cost/ops harness.
- KILL 3: if the desk's own proposals produce no measured improvement over 30 sessions, the nightly review is decision-support theatre — cut it to a weekly run.

## 14. Running priors — what we believe, and on what evidence

- `vwap_ema_adx` has NO measurable gross edge: over 136 trades the book decomposed to gross +6.43% = tide +2.69% + alpha +3.74% with 11.20% friction -> net -4.77%. The picks are microscopically positive; friction ate them three times over.
- Friction is deterministic and arithmetic: ~9.2bps proportional charges + ~6bps modelled slippage. Size does NOT reduce it (10.4 -> 9.6bps at 2x notional). Only trade COUNT and STOP WIDTH are levers.
- The rule score / 'confidence' is non-predictive — corr(conf, win) ~ +0.09 in both halves over 136 trades, and 89% of trades score >=85. It gates nothing and predicts nothing.
- The universe allowlist is NOT justified — killed three times by data (OLAELEC, a drop-listed smid, was the book's best name both times it was tested). `live_universe.restrict_to_allowlist` stays OFF.
- Exit geometry is not the lever: trailing exits were WORSE (-4.97% vs -3.85% baseline) and hold-to-90m only helped inside the tight-stop cohort the P0 gate now removes. On the P0-gated cohort baseline exits win. Keep current exits.
- In-sample counterfactuals are scale, never proof. The 'max 5 trades/day -> +6.9%' result was a pure arrival-order artifact.
- Direction is ~unforecastable in this market at this horizon: the movers sleeve called direction correctly 33% of the time on 18 calls (worse than a coin flip) while magnitude prediction ran at 8.2x lift. You can predict THAT a name moves, not WHICH WAY.
- The best signal the research arc ever found (earnings-day jump-drift) is real but its alpha lives on the short leg of mid-caps that Indian rules make un-shortable. No sleeve.
- 1,693 intraday + 195 swing variants have been tested; NONE cleared `edge_verdict`.

## 15. Reasoning layer

Deterministic path only — no LLM call was made. SE_DESK_LLM is not set to 1 (the default).

LLM cost: ₹0.00 (0 calls — deterministic path only).

---

_Generated by `signal_engine/desk` (see docs/DESK_AGENT.md). Nightly analysis is one clock; evidence-gated change is the other. This document belongs entirely to the first._