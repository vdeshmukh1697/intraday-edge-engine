# Morning Handoff — 2026-06-26

You were away after market close on 2026-06-25. I analyzed the full day and shipped improvements.
Full analysis: **`docs/EOD_ANALYSIS_2026-06-25.md`** (a 51-agent forensic study, each fix
adversarially verified + red-teamed for overfitting).

## TL;DR of yesterday (2026-06-25)
- **Losing day: 41 trades, 34% win rate, net −4.5%, PF 0.63.**
- **Why:** a gap-up-and-fade day. The market gapped up (the 08:30 briefing correctly called
  GAP_UP +1.05%) then **faded all session**. Our momentum strategy bought the pop; it reversed.
- LONGs were 3/21 wins (−5.06%); SHORTs 11/20 (+0.51%). We fought a falling tape with longs.
- **Confidence is non-predictive** — the 90-100 band won 47% but the 75-89 band only 20%.
- This was activity-mode (loosened thresholds you asked for), which traded a lot and surfaced the
  strategy's known lack of edge as a clear loss.

## The honest verdict (unchanged)
This is still a **no-edge strategy** (prior research: 1,693 variants, none beat the ~14 bps cost
wall). My changes **reduce losses/variance and add measurement — they do NOT manufacture profit.**
Anyone who tells you a config tweak makes this green is overfitting hope.

## What I changed for today (all paper-only, reversible, tested — full suite green)
**Config → "balanced mode"** (round, robustness-justified values — NOT tuned to yesterday's path):
- `max_concurrent_positions: 8 → 4` — yesterday 6-8 correlated longs fired in the same minute and
  bought the top together. This is the single most robust lever (caps the correlated basket).
- `max_trades_per_day: 25 → 15` — fewer trades = less guaranteed cost-wall bleed. Still active.
- `daily_loss_pct: 6 → 4` AND the breaker now trips on **drawdown-from-peak** (code). Yesterday the
  6% cap never fired despite a −7.8% intra-session trough because earlier wins netted it up. Now it
  binds on actual give-back.
- Strategy thresholds reverted to conservative defaults: `confidence 62→75, adx 20→25, rvol 1.2→1.3`
  (confidence is non-predictive — these are defaults, not credited with picking winners).

**Code fixes:**
- **Cooldown bug fixed** — it now arms after ANY losing exit, not just a hard STOP (yesterday a
  TIME_STOP loss let SWIGGY re-enter the next bar and double the bleed).
- **Regime shadow-logging (measurement only, never gates a trade)** — every entry now logs its
  regime context (trend slope, side-of-VWAP, ORB, ADX, RVOL) and flags counter-regime "faded-gap
  LONG" setups a future filter WOULD block. This collects the multi-day data needed to VALIDATE a
  regime filter before we ever turn it on live. Grep the live log for `ENTRY-CTX` and `SHADOW`.

**Deliberately NOT shipped (red-team rejected as overfit to one day):** reverting to the old
4-trades-a-day config, daily-loss 3.0, max_trades 12, symbol blacklists, hard "no-trade 11am"
windows, a tuned regime-gate constant. These curve-fit to yesterday and were cut.

## ⚠️ ACTION YOU MAY NEED: Dhan token
The Dhan token expires ~00:50 IST overnight (before the 09:15 session). The 06:00 auto-renew can't
renew portal tokens. **If you want live paper-trading today, regenerate the token in the Dhan portal
and paste it into `.env` before 09:15** — the scheduler now auto-picks it up (no restart needed).
If you don't, the live session simply won't run today (no harm; nothing breaks).

## What to watch today (success = lose LESS, more legibly — not "be green")
1. By-direction P&L (LONG vs SHORT) — did fewer counter-trend longs show up?
2. The `ENTRY-CTX … SHADOW: faded-gap LONG` log lines — did they fire on a fade, stay quiet on a
   trend-up? (This is how the regime filter earns the right to go live, across many days.)
3. Did `max_concurrent:4` prevent the correlated-basket cluster?
4. Did the drawdown breaker arm (and leave open positions running)?
5. Total trades + cost paid vs yesterday's 41 — is the bleed materially smaller?

## The real path forward (not a tweak)
Genuine improvement needs a genuine **edge**, which requires *different information* than OHLCV
(fundamentals, events/earnings drift, flow) or a validated regime/mean-reversion signal that clears
the cost wall out-of-sample. The ML model is being pointed at the right job (meta-labeling, net-of-
cost labels, a real promotion gate) but is expected to fail the honest gate until such data arrives.
See `docs/EOD_ANALYSIS_2026-06-25.md` §4(c) and §4(d).

Services are up (scheduler + API + tunnel). Dashboard: https://web-beta-beige-60.vercel.app
