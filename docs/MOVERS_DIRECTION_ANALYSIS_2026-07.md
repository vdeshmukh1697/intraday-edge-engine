# Movers sleeve — the "how did you know it'd hit upper circuit?" analysis (2026-07-15)

Prompted by the 2026-07-16 BIG-MOVERS WATCH alert (BNALTD/HARDWYN/NUVOCO/SHRADHA/AJOONI…)
and the observation "4 of them hit upper circuit today." This is a data-grounded honest
read on what the alert means and whether next-day direction is forecastable.

## 0. First: that message is OUR engine, not the tip channel
The alert is our own `movers_job` (16:40 IST) format_alert output, delivered to your Telegram
by our alerter. It is NOT the STARBHAI channel talking. The "strategy behind it" is our
bucket base-rate predictor (`signal_engine/movers/`).

## 1. What the strategy actually does
No fitted model. It buckets every NSE name by **(|yesterday's move|, 20d-vol tercile, big-day
streak)** and looks up the MEASURED 16-year base rate for that bucket. The five headliners sat
in `10+|high|streak` → **37% chance of a ±5%+ move tomorrow = 7.6× the 4.9% unconditional base
rate**. That is a **magnitude** statement ("this will be volatile"), explicitly NOT a direction
statement — the footer says so.

## 2. The "4 hit upper circuit today" is selection, not prediction
As of 2026-07-15 17:51 the 2026-07-16 predictions are **unresolved** — tomorrow hasn't happened.
The 07-15 signed closes of the 10 names show why they qualified:

| name | 07-14 | 07-15 | ADV | fillable? |
|---|---|---|---|---|
| BNALTD | +10.3% | **+19.9%** | ₹0.1cr | NO |
| NUVOCO | +6.9% | **+11.2%** | ₹530cr | yes |
| SHRADHA | +31.6% | **+14.0%** | ₹1.6cr | NO |
| FILATEX | +12.0%→−4.8% | **+14.7%** | ₹73cr | yes |
| AJOONI | +0.8% | **+11.4%** | ₹0.3cr | NO |
| HARDWYN | −20.0% | **−19.3%** | ₹23cr | (down-streak) |

9 of 10 had a big UP day on 07-15. **They are on the list BECAUSE they spiked** — the spike is the
input, not the forecast coming true. Judging the alert by "they hit upper circuit today" is
grading the exam question, not the answer. (Classic selection-bias trap.)

## 3. Is next-day direction forecastable? (measured on the 738k-row, 16y panel)
Two truths, both real:

**(a) On the AVERAGE day, an up-spike slightly REVERSES.** After a ≥10% up day, next-day P(up) =
**47.6%** (below 50); after a 2-day UP-UP streak, 46.7%. This is the MAX/lottery effect — spiky
names underperform, already documented in SPIKE_HUNTER_FINDINGS.

**(b) But in the BIG-MOVE TAIL, there is a real, weak UP tilt.** Given a ≥10% up day, the next day
is:

| condition | next ≥+5% (upper-ish) | next ≤−5% (lower-ish) | edge |
|---|---|---|---|
| quiet base rate | 2.5% | 1.4% | +1.1pp |
| today UP ≥10% | **16.6%** | 11.0% | +5.6pp |
| today UP 5–10% | 9.0% | 4.4% | +4.6pp |
| today DOWN ≤−10% | 20.8% | 14.7% | +6.1pp (bounce) |

So the alert's "lean LONG 58%" is roughly right FOR THE BIG-MOVE TAIL (~60/40 up when it moves
big). It is **not** a strong edge, and it is NOT "will hit upper circuit" (that's ~17%, not 58%).

**Bottom line on "how would I have known":** you partially could — an up-spiked name that moves
big is ~60% likely to move big UP. But (i) 72% of the time it doesn't move ±5% at all, (ii) even
when it does, you're wrong on direction 40% of the time, and (iii) the down tail is a ruin event
(HARDWYN −20%/−19%).

## 4. The wall that makes it mostly un-tradeable (unchanged from SPIKE_HUNTER)
The names with the strongest effect are the un-fillable ones. At an upper circuit the book is
**buyers-only, zero sellers** — you can place a buy that never executes. BNALTD (₹0.1cr ADV),
AJOONI (₹0.3cr), SHRADHA (₹1.6cr) are un-buyable when they lock. Consecutive circuits in ₹0.1cr
names are operator-driven pump-and-dump — the SEBI-prosecuted zone with retail as exit liquidity.
Knowing it will go up does not let you buy it, and holding for the "next up day" is how you're the
one holding when the operator exits (SHRADHA already did −18.5% mid-streak).

## 5. Improvements (ranked) — MEASURE, don't believe yet
1. **Fix the direction output — sign-condition + tail-focus.** Today `p_up_given_big5` is computed
   on the ABS-move bucket, so HARDWYN (a −19% crash) still shows "lean LONG 58%" — wrong. Condition
   the direction lookup on the SIGN of the qualifying move, and report the big-move directional
   split (≈17% up / 11% down after an up-spike), not a blended 58%. ~0.5 day; corrects a misleading
   label.
2. **Make fillability the PRIMARY sort/gate, not a footnote.** The tradeable list is only the liquid
   F&O names (NUVOCO ₹530cr, FILATEX ₹73cr, ELIN/DPABHUSHAN ₹18cr). Rank those first; move the
   ₹0.1cr circuit-lockers to a clearly-labeled "research only, cannot fill" section. The shadow book
   already refuses them; the display should too.
3. **Add INFORMATION, not more price history** (the only path to real direction edge — SPIKE_HUNTER):
   - scheduled catalysts (earnings/board-meeting/index-rebalance dates) — known ahead, fillable;
   - **delivery% + volume buildup** from NSE bhavcopy (the #1 data build; not in cache yet);
   - pre-open call-auction imbalance (09:00–09:08) — the only zero-lookahead early-direction feed.
4. **Measure the tip-channel feature we just wired.** Does a `tg_mentions` spike precede a hit? The
   sleeve already logs it; after ~30 predictions, test mention→outcome. If it predicts, it earns
   weight; if not, it's decoration. (Tip channels are the pump vector — trust must be earned in data.)
5. **Report the honest scoreboard, not anecdotes.** precision@k vs the 4.9% base and direction
   accuracy on the big-move tail — the `/movers` page already does this. One "4 hit circuit" day is
   n=4; the base rate says wait for ~30.

## 6. The uncomfortable straddle truth
Because magnitude is forecastable but direction barely is, the theoretically-correct trade is a
**long straddle** (profit from a big move either way). But the names with the biggest edge have no
options, and the liquid ones that do (NUVOCO) already price the vol. So even the "right" trade is
hard — which is exactly why this stays a measured research sleeve, not a live book, until the data
earns it.
