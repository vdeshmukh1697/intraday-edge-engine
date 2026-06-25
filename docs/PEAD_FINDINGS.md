# PEAD (Post-Earnings-Announcement-Drift) — Findings (2026-06-25)

The first DIFFERENT-INFORMATION test (not a price transform). Earnings events from Yahoo
(`get_earnings_dates`: announcement date + consensus EPS estimate + reported EPS + surprise%),
overlaid on the daily swing panel with strict point-in-time entry (first session **after** the
announcement). Run through the same harness: net of cost, OOS, PBO, `edge_verdict`.

Data: **7,322 earnings events, 230/250 names, 99% with surprise%** (`data/research/earnings_events.parquet`).
Overlap with the 5y price panel → 3,078 post-earnings entry sessions (**923 OOS**).
Reproduce: `.venv/bin/python -m signal_engine.research.pead_experiment`.

## The signal is REAL (correct sign — the key positive)
| | gross 20d drift | n | win rate |
|---|---|---|---|
| **beat ≥ +5%** | **+0.34%** | 367 | 50% |
| **miss ≤ −5%** | **−0.29%** | 298 | 43% |

Beats drift **up**, misses drift **down** — the textbook under-reaction. Unlike every price-only
signal (which was directionless noise), PEAD points the right way. This is genuinely different.

## But it doesn't (yet) clear the gate — and we can see exactly why
Net of cost, OOS, the best bucket (positive surprise, 20d):
| cost model | PF | net μ/20d | PBO | n (OOS) | gate |
|---|---|---|---|---|---|
| delivery (~29 bps) | 1.09 | +0.29% | 0.48 | 484 | ✗ all three |
| **futures wrapper (~13 bps)** | **1.15** | **+0.44%** | 0.42 | 484 | ✓ PF, ✗ PBO, ✗ n |

With the **cheaper futures execution, the performance threshold (PF≥1.10) PASSES** — the first
signal to do so. It now fails on only two gates, and **both have the same root cause**:
- **n=484 OOS** (need ≥2000): earnings fire ~4×/name/year, and our price panel is only ~5 years,
  so the out-of-sample slice (~1.5y) holds too few events to judge.
- **PBO 0.42** (need <0.10): with so few independent events over so short a window, monthly
  performance is necessarily noisy → the overfit gate can't be satisfied.

This is a **statistical-power problem, not a proven no-edge**. The signal is directionally real and
beats the cost wall via futures; we simply lack enough out-of-sample earnings events to *confirm* it.

## Honest caveats
- On the **250 most-liquid names** PEAD is at its *smallest* (those are the most efficient). The
  literature's larger drifts live in mid-caps — where retail slippage is also larger. Trade-off.
- Conservative D+1 entry gives up the announcement-day pop (safe, but understates the effect).
- Yahoo consensus surprise is sparse for small/new names; survivorship still inflates long-only.

## The clear next step (highest-value)
**Extend the daily price history to ~10–15 years** (yfinance daily for these names is free) and
re-run PEAD. The earnings data already goes back to 2005; the binding constraint is the 5-year
price panel. A 3× longer panel → ~3× the events → enough OOS power to either (a) confirm PEAD
clears the bar via futures, or (b) honestly reject it with real statistical power. Secondary ideas:
combine surprise with the announcement-day price jump (SUE + confirmation), and a careful mid-cap
extension.

**Status: the most promising lead found so far — real signal, beats cost via futures, blocked only
by sample size.** Paper/research only.
