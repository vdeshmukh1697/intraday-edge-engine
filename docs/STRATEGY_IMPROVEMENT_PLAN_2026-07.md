# Strategy Improvement Plan — 8-session review (2026-07-07)

**Book state:** 136 closed trades over 8 sessions (Jun-24 → Jul-07), all `vwap_ema_adx`.
Net **−4.77%** (sumR −27.55, WR 29.4% vs break-even 38.2% at the realized 1.62:1 payoff).
Ledger equity ₹99,886 (−0.11% since the ₹-book went live Jul-03), maxDD −2.13%.
avgR = **−0.203**, bootstrap 95% CI **[−0.41, +0.02]** — P(true avgR<0) ≈ 96% in-sample, but
**~318 trades** are needed for a powered verdict on the signal itself. The cost findings below
need no such wait: they are arithmetic.

Method note: sessions 1–4 were the June analysis window; **sessions 5–8 are pseudo-out-of-sample**
(same config — frequency caps and allowlist stayed OFF), so every June hypothesis got a clean test.

---

## 1. June findings scoreboard (what reproduced on sessions 5–8)

| June claim | 8-session verdict |
|---|---|
| Cost is the deterministic leak; gross ≈ break-even | **PROVEN, now in ₹**: Jul 3–7 (45 ₹-tracked trades): gross **+₹1,376**, charges **₹1,491**, net **−₹115**. The book's entire loss IS the charge bill. Charges = ₹2.7 flat + **9.2bps × notional** (so bigger-fewer trades do NOT cut the bps — count is the lever, not size). |
| Entry hit-rate is the lever, not exits | **CONFIRMED + sharpened**: WR 29.4% vs 38.2% BE. Exits are fine: TIME_STOP exits average **+0.23R** (n=27), 30–90m holds break even; all loss concentrates in <30m resolutions. |
| Stops fine, don't widen | **REVISED — the one materially new finding.** Stop exits now average **−1.38R** (n=67), not −1.09R: ~0.30R of that is measured friction drag. See §2. |
| Confidence non-predictive, range-restricted | **CONFIRMED (3rd time)**: corr(conf, win) = +0.09 in both halves; WR by bin 33/17/30/33%; 89% of trades ≥85 conf. It gates nothing and predicts nothing. |
| Same-minute bursts are the problem | **FADED**: burst trades avgR −0.18 vs non-burst −0.22 — no longer worse per-trade. The frequency-cap rationale shifts to pure count/cost, not bet quality. |
| Universe allowlist NOT justified | **RE-CONFIRMED (harder)**: OLAELEC (drop-listed smid) is again the book's best name (+7.2%, 73% WR, n=11). Ex-OLAELEC the book is −12.0%. The allowlist would have deleted the only profit. Stays OFF. |
| One name dominates each session | **CONFIRMED**: best-3 names +9.6% vs book −4.8%. Fragile, single-name-driven P&L persists. |

New structural fact: **`max_trades_per_day: 15` binds every session** — sessions 5–8 each fired
exactly 15 trades, budget exhausted by ~11:30–11:55. At ~₹33 charges/trade the engine commits to a
**~₹500/day (~50bps) deterministic charge bill** before lunch, every day. Trades #1–5 of the day:
net +2.09%; #6–10: −2.09%; #11–15: −2.04% (OOS #6–10: −3.27%). The marginal trade is negative in
both halves.

---

## 2. The new finding: friction-in-R — tight stops are unwinnable by arithmetic

Stops are ATR-based (2×ATR) with `min_stop_pct: 0.30`. Median realized stop distance = **0.37%**.
Round-trip friction ≈ 16–19bps (9.2bps charges + ~6bps modeled slippage + ~3bps measured
stop-through). Friction in R-units = friction ÷ stop distance:

| stop distance | friction cost per trade |
|---|---|
| 0.35% (median!) | **~0.46R** |
| 0.60% | ~0.27R |
| 0.90% | ~0.18R |

The median trade pays **half an R before the market moves**. At a 1.4:1 realized payoff, a 0.4R
per-trade toll needs ~49% WR to break even — the signal has never shown anything near that.

Empirical check (pre-registered logic: this is arithmetic first, data second):

| cohort | n | avgR | sumR |
|---|---|---|---|
| stop < 0.5% | 92 | **−0.414** | **−38.10** |
| stop 0.5–0.8% | 30 | +0.212 | +6.36 |
| stop 0.8–1.2% | 13 | +0.393 | +5.10 |

- The **entire book loss (−27.6R) sits in the tight-stop cohort** (−38.1R); wide-stop trades sum +11.5R.
- Robust to the OLAELEC objection: ex-OLAELEC, wide avgR +0.144 vs tight −0.508, permutation **p=0.003**; the wide cohort spans 23 symbols and all 8 sessions.
- Holds in both halves independently: 1–4 wide +0.27 / tight −0.55; **OOS 5–8 wide +0.20 / tight −0.25**.
- Mechanism is visible in the exits: stop exits realize −1.38R (friction drag −0.30R measured); <10m holds (tight stops tagged by noise) lost ₹2,006 of the book's bleed.

**Why the existing gates miss it:** `edge_cost_multiple: 3.0` checks the TARGET clears 3× cost —
a reward-side gate. Nothing checks cost against the STOP (the risk side). And `min_stop_pct` was
lowered 0.50 → 0.30 (to let large-cap picks surface), which is a *floor that widens* stops, not a
*gate that rejects* structurally-unwinnable trades. A 0.35%-stop trade passes everything and pays 0.46R.

**Honest caveat:** the claim defended here is *"tight-stop trades are negative-EV by friction
arithmetic — remove them"*, NOT *"the wide cohort is proven positive"* (n=44, WR 27%, its +10.5R
leans on TIME_STOP drift and a few 1.7R targets; CI still wide). Removing a mechanically
negative-EV cohort is low-regret either way.

---

## 3. Ranked improvements

Rank = (expected ₹ impact × evidence strength) ÷ effort. Everything ships config-gated with a
pre-registered evidence bar; nothing is declared "profitable" on 8 sessions.

### P0 — Cost-in-R rejection gate `[build now, flip ON after review]`
Add to `RiskManager.build_plan` (`signal_engine/risk/manager.py`): reject when
`round_trip_cost_pct / stop_pct > max_cost_r`. Config `risk.max_cost_r: 0.25` (0 = off), i.e.
friction may consume at most a quarter of 1R → effectively requires stop ≥ ~0.6% at current costs.
- Style-consistent: it's a pure rejection gate like `rr_floor` — do NOT widen stops via the floor instead (that's what the 0.50→0.30 history warns about: floored stops broke R:R geometry).
- Expected effect: rejects ~⅔ of current entries (92/136 retro) → trade count ~5–6/day, daily charge bill ~₹180 vs ₹500; retained cohort's retro sumR +11.5R vs −38.1R removed. In-sample numbers, stated for scale only.
- Evidence bar (pre-registered NOW): over the next 10 sessions, (a) cost share of |gross| < 40% (June's target), (b) retained-cohort avgR > −0.05, (c) book net ≥ 0. One look, session ~18. If (a) passes and (c) fails → the signal itself is dead (see P4).
- Effort: ~0.5 day incl. tests. Also fixes the "quiet name" failure mode: stop = 2×ATR, so the gate reads "don't trade low-ATR names intraday — friction eats the R."

### P1 — Cut `max_trades_per_day` 15 → 8 `[only if P0 is NOT adopted]`
The cap binds daily and the marginal trade is negative in both halves; 15/day = a deterministic
50bps/day tax on a book with no proven gross edge. With P0 on, count falls below 8 naturally and
this becomes moot. (The tempting in-sample "max 5/day → +6.9%" counterfactual is an arrival-order
artifact — first-5 keeps the 09:xx winners; do not ship a time-of-day bet disguised as a cap.)

### P2 — Demote the confidence score `[0.25 day]`
Three strikes across 8 sessions: non-predictive (corr +0.09), range-restricted (89% ≥85), inverted
on conf=100 losers in session 4. Stop rendering it as decision-relevant in alerts/dashboard
(display fine, but label it "rule count, uncalibrated"); never gate on it until recalibrated on
≥300 trades. It currently manufactures false confidence in every Telegram alert.

### P3 — Measurement plumbing (the platform half) `[~1 day total]`
1. **Persist gross P&L + charges on every trade** — only 45/136 rows have ₹ columns (ledger-era).
   The cost identity (§1) is the book's most important number and it's currently computable for 3
   sessions only. Backfill where fills allow.
2. **Alpha-vs-NIFTY on every closed trade** (the one adopted TradingAgents borrow): win-vs-index
   over the holding window separates stock-picking from tide. Long-only book → mandatory context.
3. **Pre-registered eval protocol**: metrics frozen in this doc (§P0 bar); next formal look after
   10 more sessions. No peeking-and-tweaking between — that's how forking paths ate June's
   universe gate.

### P4 — Strategy candidates (research harness ONLY — `backtest/archive.py` replay, never straight to live)
The current signal at current costs needs 38% WR and shows 29%. If P0's cleaner cost base still
doesn't lift the book to ≥0 by session ~18, stop iterating exits/gates on `vwap_ema_adx` — the
entry has no edge (consistent with the whole research arc) — and spend the effort here instead:
1. **Same entries, trailing exit instead of hard target** — at WR 29%, break-even needs 2.4:1;
   current structure pays 1.62:1. TIME_STOP drift (+0.23R avg) hints unrealized continuation.
   Replay the archive with ATR-trail and measure the payoff shift. (June's improvement #2, finally
   instrumented.)
2. **High-ATR morning cohort**: P0's gate inverted into a selector — only names whose 2×ATR stop
   ≥0.6%, entries before 10:30, cap 5/day. This is what the 8 sessions say survives friction; test
   it as its own pre-registered cohort in replay across the full archive, then paper.
3. **Hold-to-time-stop variant** (no target, exit at 90m or stop): tests whether the drift that
   makes TIME_STOP positive generalizes. Cheap replay; kills or confirms the "targets truncate
   winners" hypothesis.
All three must clear the standing `edge_verdict` gates (n≥2000 replayed, OOS split, PBO<0.10)
before any live-paper allocation — same bar as every prior experiment.

### Explicitly NOT doing
- **Universe allowlist** — killed a third time by data (OLAELEC).
- **Widening stops via `min_stop_pct`** — floors distort geometry; reject instead (P0).
- **Time-of-day filters as live gates** — 10:xx is negative in both halves (suggestive), but it
  rides on 33 OOS trades; folded into P4-2's pre-registered cohort rather than shipped.
- **Sizing up to cut cost bps** — charges are 9.2bps proportional + ₹2.7 flat; size does ~nothing
  (10.4 → 9.6bps at 2× notional). Count and stop-width are the only cost levers.
- **Trusting any in-sample counterfactual as a result** — every Δ in this doc is scale, not proof.

---

## 4. The honest bottom line

Eight sessions haven't changed the strategic picture: **the entry signal is a gross coin-flip and
friction converts it to a reliable small loss** — exactly the arc-wide lesson. What changed is
precision: we can now name the friction (9.2bps proportional charges, ~6bps slippage, 3bps
stop-through), prove the loss identity in ₹, and locate WHERE the friction bites (stops <0.5%
paying 0.3–0.5R/trade). P0 removes the mechanically-doomed cohort and cuts the daily tax ~65%;
it will make the book smaller and quieter, not rich. If the surviving cohort still can't beat
zero by session ~18, the verdict is on the signal, not the plumbing — and the effort moves to
P4 candidates and the standing research priorities (survivorship-clean data first).

---

## 5. IMPLEMENTED — 2026-07-07 evening (all of P0/P2/P3, P4 replays run)

Everything below is live in the working tree, tested, and (after the scheduler restart)
active from the 2026-07-08 session. Every number in this section was verified by running
the code, not estimated.

### P0 — friction-in-R gate: BUILT and ENABLED
- `RiskManager.build_trade_plan` rejects when `cost_to_break_even_pct / stop_pct > max_cost_r`
  (`signal_engine/risk/manager.py`); `risk.max_cost_r: 0.25` set in `config/risk.yaml`.
- **V3 regression found and fixed while implementing:** the live runner built its gate
  `CostModel` WITHOUT the slippage model, so every gate priced friction at ~8.2bps while
  fills actually pay ~14.2bps (risk.yaml's V3 comment promised slippage-inclusive gating).
  The runner now carries TWO models (`engine/runner.py`): `gate_cost_model`
  (charges+slippage) for plan building/gates, and the charges-only `cost_model` for the
  PaperTrader/ledger (fills already carry slippage — one model for both would either
  under-gate or double-count). Scan harnesses and the ML dataset builder were aligned to
  the slippage-inclusive gate. Effective P0 threshold: stop ≥ ~0.57% (0.1424/0.25).
- Tests: `tests/test_risk.py` (reject-tight/pass-wide/legacy-off), `tests/test_engine.py`
  (`test_gate_cost_model_prices_slippage_trader_model_does_not` pins the no-double-count
  invariant). Full suite green.

### P2 — rule score demoted: DONE
- Telegram entry alerts now say `rule score N (uncalibrated)` instead of `conf N`
  (`engine/runner.py:_format_alert`).
- Dashboard: headers renamed to "Rule score" (`web/app/{page,paper,premarket,predictions}`,
  stock page), and the glossary tooltip now states the measured fact: non-predictive across
  136 live trades (corr ≈ +0.09), descriptive only, never a gate (`web/lib/glossary.ts`).
  NOTE: takes effect on the dashboard's next Vercel deploy; Telegram side is live now.
- The pre-existing rank multiplier (`_rank`: edge/stop × confidence) is deliberately
  UNCHANGED — removing it mid-comparison would be a strategy change; revisit at session ~18.

### P3.1 — cost identity on every trade: DONE + backfilled
- `PaperPosition.pnl_pct_gross` / `.cost_pct` set at close (`paper/trader.py`), persisted
  (`storage/repository.py`, migration adds the columns).
- Backfill (`scripts/backfill_cost_identity.py`, DB backed up first): **136/136 rows, exact
  recovery from stored fills (cost = gross − net), 0 identity violations; recovered cost
  avg 0.0824% — matches the June 8.2bps measurement per-row.**

### P3.2 — alpha-vs-NIFTY: DONE + backfilled
- `signal_engine/analytics/alpha.py`: ^NSEI 1-min closes per session, asof-anchored index
  return over each holding window; `alpha_pct = gross − sign×nifty_ret` (direction-signed
  tide). Self-healing `alpha_job` scheduled 16:20 IST (`scheduler.py`). Tests in
  `tests/test_alpha.py` (no network).
- Backfill: **136/136 resolved.** Decomposition of the whole book:
  **gross +6.43% = tide +2.69% + picking (alpha) +3.74%; cost 11.20% → net −4.77%.**
  The picks are microscopically positive (+2.8bps/trade); friction ate the whole thing
  three times over. SHORTs carry most of the alpha (+4.7bps/trade vs LONGs +1.2bps).

### P3.3 — pre-registered eval: DONE
- `scripts/preregistered_eval.py`, frozen thresholds, gated era starts 2026-07-08, ONE
  committed look after 10 gated sessions. Cost-share denominator = |sum(gross)| (per-trade
  |g| sums would let churn pass the bar trivially: pre-gate scores 15.7% that way vs the
  honest 174%).

### P4 — exit-variant replays: RUN (research only, no live change)
Recorded entries replayed over the real archived 1-min bars, exits varied
(`signal_engine/research/exit_variants.py`; 108/136 entries covered — 2026-06-29 archive
gap excluded, stated not silent). Harness validated: baseline replay reproduces recorded
outcomes with median |diff| 0.000pp.

| variant | sum net (108 trades) | avgR | verdict |
|---|---|---|---|
| baseline (current exits) | −3.85% | −0.160 | reference |
| trail (chandelier, no target) | −4.97% | −0.154 | **worse — killed** |
| hold90 (no target, ride to time-stop) | +1.52% | −0.017 | better overall, BUT… |

…the hold90 gain lives entirely in the tight-stop cohort that **P0 removes anyway**: on the
P0-gated cohort (stop ≥0.5%, n=32) baseline exits WIN (avgR +0.195 vs hold90 +0.121, trail
+0.142). **Pre-committed conclusion: keep current exits; P0 alone captures the fixable loss.
Do not touch exit geometry before the session-18 look.** (n=32; per-trade results in
`data/research/exit_variants_2026-07.parquet`.)

### Ops
- Scheduler restart required to load `max_cost_r` + register `alpha_job` (done as part of
  this change: `launchctl kickstart -k gui/$UID/com.vikrant.signal-engine-scheduler`).
- The gated era's first session is 2026-07-08; expected footprint: ~5–6 trades/day,
  daily charge bill ~₹180 vs ~₹500.

---

## 6. Gated era day-1 + evening hardening (2026-07-13)

**Day-1 read (n=3 — mechanism proof, NOT a performance verdict):** the P0 gate worked as
designed. 3 trades vs pre-gate ~15; stops 0.61/0.73/0.90% (pre-gate min 0.33%); charge bill
~₹120 vs ~₹500. Outcomes: BDL SHORT stopped −1.01%, PPLPHARMA +0.61% and OLAELEC +0.21% on
time-stops; net −0.19%, alpha vs NIFTY +0.37%. Nothing beyond "the gate binds where intended"
may be concluded until the pre-registered look (`scripts/preregistered_eval.py`) at 10 gated
sessions.

**Incident found during the day + fixed in the evening:** the Dhan WS died silently at 14:02
(hotspot drop). THREE-LAYER fix shipped & tested:
1. `dhan_ws.run_feed(reconnect_on_close=True)` on the live path — a clean server close now
   reconnects (old behavior: ended the feed permanently, silently);
2. `runner.live` alerts via Telegram if the feed ends before the close;
3. `scheduler.live_job` relaunches a fresh broker+runner (warm-start re-derives today) up to
   8 times before giving up.
Root cause of the WHOLE outage class (Jul-09/10 missed sessions, this morning's dead
scheduler): **broken IPv6 on the iPhone-hotspot network** — fixed globally via
`signal_engine/net.py::prefer_ipv4()` in `cli.main()`.

**Dashboard permanently self-healing** (the "/paper doesn't open" recurring failure):
`run-with-tunnel.sh` rewritten as a supervisor — probes the public URL end-to-end every 30s,
restarts the whole stack on failure, re-points Vercel with retries and **bake-verification**
(the deployed JS must reference the new tunnel hostname), Telegram-alerts every heal. Two
live heals verified end-to-end today.

## 7. NEW: Big-Movers research sleeve (separate paper section) — built 2026-07-13
`signal_engine/movers/` + `/movers` dashboard page + `/api/movers` + 16:40 IST `movers_job`.
A PRE-REGISTERED prediction tracker: ranks ~2k NSE names nightly by the MEASURED probability
of a ±5%+ move next session, using transparent 16-year bucket base rates
(`data/models/movers_calibration.json`; headline bucket: after a 10%+ day with a streak in a
high-vol name → **37% P(±5%+), 7.6× lift**). Honesty contract from SPIKE_HUNTER_FINDINGS:
survivor-panel probabilities labeled as optimistic ceiling; direction leans shown only when
the measured split clears 55% and always labeled weak; circuit-band names flagged un-buyable;
SHORT calls labeled hypothetical (no overnight cash shorts). Shadow ₹1L book takes only
fillable LONG legs (open→close, net of costs) — never touches the main book. The sleeve's
JOB is to measure precision@k vs the 4.9% base rate and direction accuracy live; capital
claims only if the measured skill survives.
