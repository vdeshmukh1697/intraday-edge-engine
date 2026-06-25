# Step-2 Edge Experiments — Findings (2026-06-25)

Ran the two free, zero-new-data candidates from `EDGE_ROADMAP.md` through the same honest harness
that produced the no-edge verdict — net of real cost, OOS headline, monthly walk-forward PBO, gated
by `edge_verdict` (n≥2000, WR≥52% OR PF≥1.10, **PBO<0.10**). Dataset: 247,740 rows · 250 NSE names ·
2021-09 → 2026-06 · 82,023 OOS rows. Reproduce: `.venv/bin/python -m signal_engine.research.edge_experiments`.

## Result in one line
**No confirmed edge yet — but two honest, useful findings.** Nothing cleared the full gate; the
factors are positive-on-average but fail the overfit/robustness check, and the overnight drift is a
large *real* gross signal whose cheap capture is the open problem.

## A. Low-volatility long-only (hold 20d)
| variant | n | WR | PF | net μ | PBO | gate |
|---|---|---|---|---|---|---|
| long bottom-10% vol | 7,634 | 51.2% | 1.34 | +0.84%/20d | 0.43 | ✗ PBO |
| long bottom-20% vol | 15,332 | 51.4% | 1.28 | +0.72% | 0.39 | ✗ PBO |
| bottom-20%, monthly rebalance | 735 | 55.2% | 1.53 | +1.32% | 0.52 | ✗ n & PBO |
| hold 10d / 5d | — | ~48% | ~1.0 / 0.91 | ~0 / neg | — | ✗ |
→ Positive net over 20d, but **fails PBO (0.39–0.52)** — the monthly performance is not stable. The
clean monthly version has *higher* PBO and too few independent samples (735), so the failure is real,
not an overlapping-label artifact. Edge does not transfer reliably month-to-month over this sample.

## B. Overnight drift (close→open)
- **GROSS: +0.139%/night, 60.7% win rate, n=81,773** — a large, real overnight effect (matches the
  documented India overnight-positive / intraday-negative literature).
- net @ **daily futures round-trip** (0.134%): **+0.005%/night, PF 1.02** — pure overnight capture
  (go flat intraday, pay a round-trip every night) nets to **break-even**.
- net @ monthly-roll amortised (0.007%): +0.132%/night, PF 1.52 ✅ — *but optimistic*: continuous
  hold also eats the documented-negative intraday drift, which this framing ignores.
- selective: **high-momentum names overnight = +0.162%/night, 62% WR** (vs +0.139% baseline) —
  conditioning helps; low-vol names are worse (+0.104%).
→ The drift is real and strong gross, but capturing *only* the overnight leg costs ~a daily
round-trip that eats it to break-even. The cheap-capture mechanism is unresolved — this is the most
promising thread to pursue.

## C. Cross-sectional momentum long-only (bonus)
| variant | n | WR | PF | net μ | PBO | gate |
|---|---|---|---|---|---|---|
| top-10% momentum, 20d | 7,973 | 52.4% | 1.37 | +1.39%/20d | 0.46 | ✗ PBO |
| top-20% momentum, 20d | 15,664 | 51.7% | 1.32 | +1.15% | 0.46 | ✗ PBO |
→ Same shape as low-vol: positive average, fails the robustness gate.

## Honest verdict & caveats
- **Nothing is a confirmed, deployable edge.** The strict gate held — exactly as it should.
- The long-only factor returns are **upward-biased by survivorship** (the archive is today's 250
  survivors; delisted losers are absent). Real numbers are lower.
- The 20d-overlap inflates n in the all-days variants; the clean monthly variant lacks samples over
  just ~5 years. A longer, delisting-inclusive history is needed to judge the factors properly.

## Highest-value next threads (in order)
1. **Overnight drift, properly costed via futures carry** — model continuous-hold (overnight +
   intraday) net of monthly roll, and a *selective* overnight (high-momentum / event-conditioned)
   that may beat the daily round-trip. This is the strongest real signal found.
2. **Survivorship-clean + longer data** before trusting any long-only factor (low-vol / momentum).
3. **PEAD / events** (roadmap Step 3) — a different information source, not another price transform.
