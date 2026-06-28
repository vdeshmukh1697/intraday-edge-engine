# Research Handoff — Continue the PEAD Edge Work (next session)

> Self-contained context to resume the alpha-research arc in a fresh session with zero re-derivation.
> Written 2026-06-25 EOD. Repo: `/Users/vikrantdeshmukh/Personal projects`. Branch:
> `feat/full-nse-realtime-pipeline`. Python: `.venv/bin/python`. Paper/research only — no live orders.

## ⏩ UPDATE — Option C executed (2026-06-25, session 2). See `docs/PEAD_SPREAD_FINDINGS.md`.
New module `signal_engine/research/pead_spread.py` (run: `.venv/bin/python -m signal_engine.research.pead_spread`).
Result, in one line: **the EPS surprise is inert; the announcement-day price JUMP is the real signal.**
- Plain market-neutral surprise spread (Step 1): dead — net/pos +0.10%, **monthly-clustered t=+0.05**.
- **Jump-sign, market-neutral, 20d, OOS** (the carrier): **n=3,019, WR 52.5%, PF 1.18, +0.51%/pos net of
  1-leg futures cost (+0.37% at conservative 2-leg), t=+2.91.** Passes n / WR / PF / significance.
- SUE-with-jump "confirmed" (Step 2): only marginally sharper (+0.70%/pos) and **halves n** to 1,385 —
  the jump *subsumes* the surprise rather than being sharpened by it. Contradicted set *reverses* (−0.77%).
- **Earnings-specific** (the decisive control): same-size jump on earnings drifts +2.0–2.8%; a clean
  non-earnings jump reverts/does-nothing (+0.17%/+0.37%/−0.11%). Genuine PEAD-via-price, not momentum.
- Robust: both OOS halves +, 13/17 years +, not tail-driven (median≈mean), magnitude-graded (|jump|≥3%
  → WR 56.2% PF 1.36 +1.00%/pos PBO 0.35).
- **TIGHTENING (user asked, before any sleeve) → the implementability wall (`pead_robustness.py`):**
  - Magnitude×hold frontier: clean & monotone (bigger jump→bigger drift, →20d best). Real.
  - Size tier: drift lives in the **mid-cap (less-liquid) half** (WR 55.3% PF 1.35 +1.02%/pos) vs dead
    large-cap half (PF 1.06). Textbook PEAD.
  - **F&O-restricted + leg decomposition = the killer:** the edge is ~ALL the **SHORT leg** (down-jumpers
    keep falling, demeaned α −1 to −2%), concentrated in **mid-caps / non-F&O names which CANNOT be
    shorted** (no single-stock futures; cash shorts illegal overnight). Long leg ≈0. On the *shortable*
    F&O subset, jump-sign drops to **PF 1.08 / WR 51.7% / t 1.67 — fails the gate.** Only a thin,
    under-powered, directional residue is executable: short large-cap F&O down-jumpers |jump|≥3% (n=414,
    PF 1.30, t 2.70). The strong stuff (non-F&O short, +1.92%/pos) is walled off.
- **MULTIPLE-TESTING RE-GRADE (`signal_engine/research/overfitting.py`, Deflated Sharpe + HLZ Bonferroni
  haircut, 2026-06-26):** our harness never corrected for the ~1,900 variants searched. Re-grading the
  jump-drift: the **headline (t=2.91) does NOT survive** (DSR→0.03; Bonferroni req. t 3.4–4.2). **Every
  TRADEABLE/shortable variant collapses** (best F&O DSR 0.04, large-cap 0.01); the *only* selection-robust
  variant is the **un-shortable mid-cap** (t=4.53, DSR 0.60). Third independent method → same verdict.
- **FINAL VERDICT: most real signal of the arc, but does NOT clear honestly as a tradeable edge** (proven
  3 ways: leg-decomposition, F&O-restriction, Deflated-Sharpe). Alpha sits on the short side of mid-caps,
  where India's short-sale constraints wall it off. **No paper sleeve. The PEAD/jump-drift line is now
  honestly EXHAUSTED — more variant-mining is −EV (each test raises the selection bar).**
- **If resuming:** Steps 1–4 DONE + deflation done. TWO honest paths, no middle: **(A) stop active
  signal-mining** on this data (bank the negative result + the new instrument — not deploying an overfit
  strategy IS the win); **(B) pursue the structural unlock (§4):** survivorship-clean / delisting-inclusive
  + true mid-cap universe + SLB/borrow access (data/infra project; only with appetite). Short-sale access is
  the #1 blocker, ahead of survivorship. **Durable win to lock in: wire `overfitting.py` (DSR + multiple-
  testing) into the standing harness** so every future candidate is auto-corrected for selection.

---

## 0. The decision we're executing
After the full research arc (below), the chosen direction is **Option C**:
> Build the **market-neutral PEAD spread** (long earnings-beats / short earnings-misses, executed via
> futures) and a **SUE-with-jump** signal (combine the EPS surprise with the announcement-day price
> reaction), test through the existing harness, and — if it survives honestly — paper-trade it small.
> Treat acquiring **survivorship-clean (delisting-inclusive) data** as the real long-term unlock.

## 1. The research arc so far (don't re-run these — verdicts are settled)
| Track | Verdict | Doc |
|---|---|---|
| Intraday OHLCV (1,693 variants) | NO cost-surviving edge | `docs/SIGNAL_RESEARCH_FINDINGS.md` |
| Swing/daily OHLCV (195 variants) | NO edge | `docs/SWING_RESEARCH_FINDINGS.md` |
| Low-vol / momentum (free re-test) | positive but FAIL PBO (unstable), survivorship-biased | `docs/EDGE_EXPERIMENTS_FINDINGS.md` |
| Overnight drift | real GROSS (+0.14%/night, 61% WR) but ~break-even after capture cost | `docs/EDGE_EXPERIMENTS_FINDINGS.md` |
| **PEAD (earnings drift)** | **directionally REAL; long-only is a survivorship mirage; the clean signal is the beat-vs-miss spread ~+0.4–0.5%/20d (thin, needs market-neutral)** | `docs/PEAD_FINDINGS.md` |
| Data/signal roadmap (verified, current Indian providers + costs) | — | `docs/EDGE_ROADMAP.md` |

**The meta-lesson:** the binding constraints are (1) **survivorship bias** (every long-only number is
optimistic until delisting-inclusive data), (2) the **too-efficient liquid universe** (250 most-liquid
names = smallest anomalies), (3) the **cost wall** (~14 bps intraday / ~29–35 bps delivery / ~13–27 bps
futures). The discipline that makes verdicts trustworthy: `edge_verdict` gate (n≥2000, WR≥52% OR
PF≥1.10, **PBO<0.10**), OOS headline, net of real cost, and **demeaning by the universe to strip
survivorship**.

## 2. What's already built and cached (reuse, don't rebuild)
- `signal_engine/research/events_dataset.py` — earnings events from yfinance.
  Cache: `data/research/earnings_events.parquet` (**7,322 events, 230 names, 99% surprise%, 2005–2026**).
  Cols: `symbol, ann_ts, ann_date, eps_est, eps_reported, surprise_pct`.
- `signal_engine/research/long_panel.py` — 16y daily price panel.
  Cache: `data/research/long_daily_panel.parquet` (**738k rows, 230 names, 2010–2026**).
  Cols: `symbol, session_date, close, fwd_ret_{5,10,20}, ts, ts_exit, is_oos, is_purged`.
- `signal_engine/research/pead_experiment.py` — `attach_pead(panel, events)` adds `surprise_at_entry`
  to a panel (point-in-time: entry = first session STRICTLY AFTER the announcement). Reusable on any
  panel with `symbol, session_date`.
- `signal_engine/research/swing_probe.py` — `evaluate_swing_signal(df, entry_mask, direction, horizon,
  cost_per_trade, oos_only, compute_pbo)` and `evaluate_long_short(df, score, horizon, ...)` — both net
  of cost, OOS, PBO, gated by `edge_verdict`. **This is the validation harness — route everything through it.**
- `signal_engine/research/delivery_costs.py` — `delivery_breakeven_pct()` (~0.287%),
  `futures_short_leg_pct()` (~0.134% one leg; futures STT already correct at 0.05%). For a market-neutral
  spread, cost ≈ **2 legs** (long future + short future) ≈ ~0.27% round-trip (+ roll if >1 expiry).
- `signal_engine/research/swing_dataset.py` / `data/research/swing_dataset.parquet` — the 5y FEATURE
  panel (has `rel_strength_20d`, `rvol_20d`, `rsi_14`, `adx_14`, etc.) if you want to add price/momentum
  features to the SUE model.

## 3. The concrete next-session plan (Option C)
**Step 1 — Formalize the market-neutral PEAD spread.**
- On `long_daily_panel.parquet` + `attach_pead`, build a daily cross-sectional book: long the
  positive-surprise names, short the negative-surprise (or rank by `surprise_pct`, long top decile /
  short bottom decile), hold 20d.
- Evaluate the **spread alpha = fwd_ret_20 − same-day universe mean** (already prototyped at the end of
  `pead_experiment` work — the demeaned check). This is the survivorship-robust number. Net of ~2-leg
  futures cost. Use `evaluate_long_short` (it nets both legs + flags the futures short) OR the demeaned
  alpha with a 2-leg cost. Report OOS, PBO, edge_verdict.
- Expected from the prototype: spread ~+0.4–0.5%/20d gross → ~+0.15–0.25% net. Confirm/with PBO.

**Step 2 — Add SUE-with-jump (the literature's sharper signal).**
- Compute the **announcement reaction**: for each event, the price move around `ann_date` (e.g.,
  `close_{first session after} / close_{last session before} − 1`, from the long panel). The PEAD drift
  is strongest when the EPS surprise AND the price jump AGREE (surprise>0 AND jump>0).
- New signal = (surprise quantile) confirmed by (jump sign/quantile). Test the long-confirmed-beats /
  short-confirmed-misses spread vs the plain-surprise version. Does confirmation sharpen it?

**Step 3 — Honest gate + sub-period robustness.**
- Run through `edge_verdict`. Also check the spread holds in BOTH halves of the OOS period (not one
  regime). Episodic event returns make PBO noisy — note that, but don't hand-wave a fail into a pass.

**Step 4 — If (and only if) it clears honestly:** wire a small, slow, market-neutral PEAD paper sleeve
(separate from the intraday engine), rebalanced around earnings, sized tiny. If it doesn't clear,
document it honestly like everything else and pivot to the data unlock (Section 4).

## 4. The real long-term unlock (parallel track)
Survivorship-clean, point-in-time data. Cheapest credible options from `EDGE_ROADMAP.md`: a
delisting-inclusive universe (CMIE Prowess via library access ideal; or rebuild a point-in-time NSE
universe from historical index constituents). Until then, every long-only result is optimistic — which
is exactly what the PEAD survivorship correction proved.

## 5. Live-system state (unrelated to research, but don't break it)
- Scheduler + API + Cloudflare tunnel running; dashboard https://web-beta-beige-60.vercel.app.
- Strategy is in **balanced mode** (post-EOD-analysis): see `MORNING_HANDOFF.md` and
  `docs/EOD_ANALYSIS_2026-06-25.md`. New runner code (drawdown breaker, cooldown fix, regime shadow-log)
  loaded for tomorrow's 09:15 session.
- ⚠️ **Dhan token** expires ~daily ~00:50 IST; user must regenerate in the portal before 09:15 for live
  trading (auto-picked-up). If absent, live just skips — nothing breaks.

## 6. Quick commands
```
.venv/bin/python -m signal_engine.research.pead_experiment        # PEAD on the 5y panel
.venv/bin/python -m signal_engine.research.long_panel             # rebuild 16y panel (force in code)
.venv/bin/python -m signal_engine.research.edge_experiments       # low-vol / overnight / momentum
.venv/bin/python -m pytest -q                                     # full suite (verify real exit code!)
```
Gate to remember: **n≥2000, (WR≥52% OR PF≥1.10), PBO<0.10, net of real cost, OOS, demeaned for survivorship.**
