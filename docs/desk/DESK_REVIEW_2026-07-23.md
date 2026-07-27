# Desk review — 2026-07-23 (vwap_ema_adx)

> **Paper book. No demonstrated edge. Proposals only — nothing in this review has been applied.**
> The target (+1.00% net/day) is the user's stated goal, not a forecast. It is +250% a year even without compounding, and ~12x (+1,100%) with it — a level no institutional systematic desk sustains; the best systematic funds target ~1-3% per *month* gross. Every number below is measured; where the evidence is insufficient the review says so instead of narrating.

## 1. Executive summary

**No conclusions drawn.** Session integrity verdict: `DEAD`.

- **REFUSING TO CONCLUDE.** Session integrity verdict is DEAD: feed logged 0 bars (< 500 = a real session): the market data never arrived, so NOTHING about strategy quality can be concluded from this day; 1426 websocket reconnect attempts — feed was unstable. No performance, strategy or gap conclusion may be drawn from this day, and none is drawn below. The only actionable items are operational.

## 2. Session integrity

| check | value |
|---|---|
| verdict | **DEAD** |
| usable for conclusions | **NO** |
| NSE trading day | True |
| live session started | True |
| symbols subscribed | 40 |
| 1-min bars processed | 0 |
| websocket reconnect attempts | 1426 |
| feed stream errors | 1426 |
| setups evaluated (ENTRY-CTX) | 0 |
| affordability skips | 0 |
| trades taken | 0 |
| trade count bound by | **unbound** |
| scheduler jobs missed | 0 |
| engine-log lines for the day | 4508 |

- feed logged 0 bars (< 500 = a real session): the market data never arrived, so NOTHING about strategy quality can be concluded from this day
- 1426 websocket reconnect attempts — feed was unstable

Notable log lines:

```
2026-07-23 08:45:06 ERROR signal_engine.scheduler | healthcheck_job failed: Dhan token is valid but the account is NOT subscribed to Data APIs (market data is a paid add-on, ~Rs.500/mo). Subscribe in the Dhan portal, or use the free Yahoo F
```

## 3. Why no analysis follows

A review that analyses a dead session manufactures findings out of an outage. The trade table, the attribution, the bucket analysis and the gap model are therefore **deliberately omitted** — not empty, omitted. Nothing about strategy quality, win rate, friction or progress toward +1.00%/day may be inferred from this day, in either direction.

Trailing context is unchanged by today and is NOT restated here as progress; see the previous review with a usable session.

## 4. Operational items

- **signal_engine/scheduler.py** (`ops.alert_on_dead_session`) — A dead session that looks alive is the worst ops failure mode: it silently contaminates every downstream statistic and nobody is told.

## 5. Hypothesis ledger

### Grading of previously-open hypotheses

- `H-20260722-03` **OPEN** — Fixing friction is NOT sufficient: even with cost share inside the bar, the gated book's avgR stays below the -0.05 floor, which would locate the verdict on the SIGNAL rather than the plumbing.
  - Not graded on 2026-07-23: evidence floor not met (8 of 10 sessions, 41 of 40 trades). The pre-registered test stands; no peeking-and-tweaking in between.
- `H-20260722-02` **OPEN** — With the P0 friction-in-R gate live, friction's share of |gross| settles below the pre-registered 40% bar.
  - Not graded on 2026-07-23: evidence floor not met (8 of 10 sessions, 41 of 40 trades). The pre-registered test stands; no peeking-and-tweaking in between.
- `H-20260722-01` **OPEN** — Position notional averaging ~42% of the book makes daily P&L a single-name lottery; capping notional per position would cut daily variance without touching the entry signal.
  - Not graded on 2026-07-23: evidence floor not met (8 of 10 sessions, 41 of 40 trades). The pre-registered test stands; no peeking-and-tweaking in between.

### New hypotheses opened tonight (pre-registered tests, written before grading)

- none. Either nothing new was gradeable, or every candidate was already in the ledger — which is the point of reading it first.

## 6. Ranked proposals — **NOT APPLIED**

Every proposal below is a written diff for a human to apply. The desk has no code path that edits a config file or flips a flag, and `desk_proposals.applied` is hard-coded to 0 on insert.

### P1 — `ops.alert_on_dead_session` in `signal_engine/scheduler.py` (`P-20260723-01`)

- current: `(absent — a 0-bar session is silent)`
- proposed: `true`
- rationale: A dead session that looks alive is the worst ops failure mode: it silently contaminates every downstream statistic and nobody is told.
- **evidence bar (pre-registered):** Ops fix, not a strategy change: bar is a test that the alert fires at 0 bars and does not fire on a healthy session.
- expected effect: No P&L effect. Turns a silent outage into a push notification.
- effort: ~0.25 day

```diff
--- a/signal_engine/scheduler.py
+++ b/signal_engine/scheduler.py
@@ live_job
+        # DESK PROPOSAL: after the session, if bars_processed == 0 (or the
+        # reconnect count is in the hundreds), send a Telegram warning. On
+        # 2026-07-23 the feed logged 0 bars and 1426 reconnect attempts while every
+        # EOD job still ran and the dashboard still rendered — the outage was
+        # invisible until this review looked for it.
```

## 7. Kill criteria

- KILL 1 (already pre-registered, P0 §evidence bar): at the 10-gated-session look, if cost share of |gross| is under 40% AND the book is still net-negative, the verdict is on the SIGNAL, not the plumbing — stop tuning `vwap_ema_adx` gates and exits.
- KILL 2: if trailing avgR's bootstrap 95% CI remains entirely below 0 at n>=318 trades (the powered-verdict count computed in the 8-session review), retire the strategy from the live paper book and keep it only as a cost/ops harness.
- KILL 3: if the desk's own proposals produce no measured improvement over 30 sessions, the nightly review is decision-support theatre — cut it to a weekly run.

## 8. Running priors — what we believe, and on what evidence

- `vwap_ema_adx` has NO measurable gross edge: over 136 trades the book decomposed to gross +6.43% = tide +2.69% + alpha +3.74% with 11.20% friction -> net -4.77%. The picks are microscopically positive; friction ate them three times over.
- Friction is deterministic and arithmetic: ~9.2bps proportional charges + ~6bps modelled slippage. Size does NOT reduce it (10.4 -> 9.6bps at 2x notional). Only trade COUNT and STOP WIDTH are levers.
- The rule score / 'confidence' is non-predictive — corr(conf, win) ~ +0.09 in both halves over 136 trades, and 89% of trades score >=85. It gates nothing and predicts nothing.
- The universe allowlist is NOT justified — killed three times by data (OLAELEC, a drop-listed smid, was the book's best name both times it was tested). `live_universe.restrict_to_allowlist` stays OFF.
- Exit geometry is not the lever: trailing exits were WORSE (-4.97% vs -3.85% baseline) and hold-to-90m only helped inside the tight-stop cohort the P0 gate now removes. On the P0-gated cohort baseline exits win. Keep current exits.
- In-sample counterfactuals are scale, never proof. The 'max 5 trades/day -> +6.9%' result was a pure arrival-order artifact.
- Direction is ~unforecastable in this market at this horizon: the movers sleeve called direction correctly 33% of the time on 18 calls (worse than a coin flip) while magnitude prediction ran at 8.2x lift. You can predict THAT a name moves, not WHICH WAY.
- The best signal the research arc ever found (earnings-day jump-drift) is real but its alpha lives on the short leg of mid-caps that Indian rules make un-shortable. No sleeve.
- 1,693 intraday + 195 swing variants have been tested; NONE cleared `edge_verdict`.

## 9. Reasoning layer

Deterministic path only — no LLM call was made. SE_DESK_LLM is not set to 1 (the default).

LLM cost: ₹0.00 (0 calls — deterministic path only).

---

_Generated by `signal_engine/desk` (see docs/DESK_AGENT.md). Nightly analysis is one clock; evidence-gated change is the other. This document belongs entirely to the first._