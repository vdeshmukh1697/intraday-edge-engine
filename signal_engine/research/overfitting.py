"""Multiple-testing / selection-bias correction — the harness gap the 2026-06 deep-research named.

Our `edge_verdict` gate does PBO + survivorship demeaning but does NOT correct for HOW MANY variants
we searched. Across the arc we tried ~1,900 configs and reported the best; Harvey, Liu & Zhu (2016)
show a single backtest then needs t well above the usual ~2 to be real. This module adds the
de Prado / HLZ instruments and applies them to our best candidate (the jump-drift):

  * probabilistic_sharpe_ratio (PSR)  — Bailey & López de Prado (2012), "The Sharpe Ratio Efficient
    Frontier": P(true SR > benchmark), adjusted for finite sample + skew + kurtosis.
  * deflated_sharpe_ratio (DSR)       — Bailey & López de Prado (2014), "The Deflated Sharpe Ratio":
    PSR against the EXPECTED-MAXIMUM Sharpe across N independent trials under the null — i.e. the
    selection-bias-corrected significance. DSR > 0.95 ⇒ survives the search.
  * bonferroni_required_t / haircut    — Harvey, Liu & Zhu (2016), "…and the Cross-Section of Expected
    Returns": the t a single strategy must clear after M trials.

No scipy (norm CDF via erf; inverse-CDF via Acklam). Read-only, additive. Reuses cached parquets.
Run:  .venv/bin/python -m signal_engine.research.overfitting
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from signal_engine.research.delivery_costs import futures_short_leg_pct
from signal_engine.research.pead_robustness import FNO_CONFIDENT, _symbol_liquidity
from signal_engine.research.pead_spread import build_event_panel, _pooled

EULER = 0.5772156649015329  # Euler–Mascheroni γ


# --------------------------------------------------------------------------- normal CDF / inverse
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation, |err|<1.15e-9)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# --------------------------------------------------------------------------- the instruments
def probabilistic_sharpe_ratio(sr: float, n: int, skew: float, kurt: float,
                               sr_benchmark: float = 0.0) -> float:
    """PSR = P(true SR > sr_benchmark). sr / sr_benchmark in the SAME per-period units; ``kurt`` is
    NON-excess (normal = 3). Bailey & López de Prado (2012)."""
    if n < 2:
        return float("nan")
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom <= 0:
        return float("nan")
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return _norm_cdf(z)


def expected_max_sharpe(var_sr: float, n_trials: int) -> float:
    """Expected MAXIMUM Sharpe across ``n_trials`` independent strategies under the null (true SR=0),
    per-period units. Bailey & López de Prado (2014)."""
    if n_trials < 2 or var_sr <= 0:
        return 0.0
    sd = math.sqrt(var_sr)
    a = _norm_ppf(1.0 - 1.0 / n_trials)
    b = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return sd * ((1.0 - EULER) * a + EULER * b)


def deflated_sharpe_ratio(sr: float, n: int, skew: float, kurt: float,
                          var_sr: float, n_trials: int) -> Tuple[float, float]:
    """DSR = PSR evaluated against the selection-adjusted benchmark (expected max Sharpe over
    ``n_trials``). Returns (DSR, benchmark_SR0). DSR > 0.95 ⇒ significant after the search."""
    sr0 = expected_max_sharpe(var_sr, n_trials)
    return probabilistic_sharpe_ratio(sr, n, skew, kurt, sr_benchmark=sr0), sr0


def bonferroni_required_t(n_trials: int, alpha: float = 0.05) -> float:
    """Two-sided single-test t-stat required for family-wise significance after ``n_trials`` (HLZ 2016)."""
    return _norm_ppf(1.0 - (alpha / n_trials) / 2.0)


# --------------------------------------------------------------------------- apply to jump-drift
@dataclass
class Trial:
    label: str
    sr: float          # monthly Sharpe
    t: float           # monthly t = sr * sqrt(T)
    months: int
    skew: float
    kurt_nonexcess: float
    n_pos: int


def _monthly(net: np.ndarray, dates: np.ndarray) -> pd.Series:
    return (pd.DataFrame({"mo": pd.to_datetime(dates).to_period("M"), "net": net})
            .groupby("mo")["net"].mean())


def _trial(panel, beat, miss, horizon, leg, label, min_months=12) -> Optional[Trial]:
    net, dates = _pooled(panel, beat, miss, horizon, leg)
    if len(net) == 0:
        return None
    mm = _monthly(net, dates)
    T = len(mm)
    if T < min_months or mm.std(ddof=1) == 0:
        return None
    sr = float(mm.mean() / mm.std(ddof=1))
    return Trial(label, sr, sr * math.sqrt(T), T, float(mm.skew()), float(mm.kurtosis()) + 3.0, len(net))


def build_trial_grid(panel, leg) -> List[Trial]:
    """The jump-drift variants we genuinely searched this session — thresholds × holds × subsets."""
    surp, jump = panel["surprise_at_entry"], panel["jump"]
    ev = surp.notna()
    liq = _symbol_liquidity()
    names = sorted(set(panel["symbol"].unique()) & set(liq.index))
    liq = liq.loc[names]; cut = liq.median()
    subsets = {
        "all": pd.Series(True, index=panel.index),
        "F&O": panel["symbol"].isin(set(panel["symbol"].unique()) & FNO_CONFIDENT),
        "midcap": panel["symbol"].isin(set(liq[liq <= cut].index)),
        "largecap": panel["symbol"].isin(set(liq[liq > cut].index)),
    }
    trials: List[Trial] = []
    for thr in (0.0, 0.01, 0.02, 0.03, 0.05, 0.07):
        for hold in (5, 10, 20):
            for sname, smask in subsets.items():
                b = ev & (jump >= thr) & smask
                m = ev & (jump <= -thr) & smask
                tr = _trial(panel, b, m, hold, leg, f"j>={thr*100:.0f}% h{hold} {sname}")
                if tr:
                    trials.append(tr)
    return trials


def main() -> None:
    panel = build_event_panel()
    leg = futures_short_leg_pct()
    trials = build_trial_grid(panel, leg)
    if not trials:
        print("no trials")
        return
    srs = np.array([t.sr for t in trials])
    var_sr = float(np.var(srs, ddof=1))
    N_grid = len(trials)
    best = max(trials, key=lambda t: t.sr)

    print("=== Deflated Sharpe Ratio — re-grading the jump-drift under multiple testing ===")
    print(f"Trial grid (this session's jump search): {N_grid} variants "
          f"(6 thresholds × 3 holds × 4 subsets). Var(SR_monthly across trials)={var_sr:.4f}.")
    print(f"Best variant by monthly Sharpe: '{best.label}'  SR_m={best.sr:.3f} "
          f"(ann≈{best.sr*math.sqrt(12):.2f})  t={best.t:.2f}  T={best.months}mo "
          f"skew={best.skew:.2f} kurt={best.kurt_nonexcess:.1f} n_pos={best.n_pos}\n")

    # Headline full-universe variant (what we reported), for reference.
    surp, jump = panel["surprise_at_entry"], panel["jump"]; ev = surp.notna()
    head = _trial(panel, ev & (jump > 0), ev & (jump < 0), 20, leg, "headline j>0 h20 all")
    for tr in (head, best):
        psr0 = probabilistic_sharpe_ratio(tr.sr, tr.months, tr.skew, tr.kurt_nonexcess, 0.0)
        print(f"  [{tr.label}]  PSR(vs 0)={psr0:.3f}")
        for N in (N_grid, 200, 1900):
            dsr, sr0 = deflated_sharpe_ratio(tr.sr, tr.months, tr.skew, tr.kurt_nonexcess, var_sr, N)
            treq = bonferroni_required_t(N)
            verdict = "✅ survives" if dsr > 0.95 else "✗ fails"
            print(f"     N={N:>4} trials → deflated benchmark SR0={sr0:.3f}  DSR={dsr:.3f}  {verdict}"
                  f"   | Bonferroni req. t={treq:.2f} vs observed {tr.t:.2f} "
                  f"{'✅' if tr.t >= treq else '✗'}")
        print()

    print("Reading: DSR>0.95 = the Sharpe beats what the BEST of N random trials would produce by luck.")
    print("N=grid is THIS session's jump search; N≈200 the session; N≈1900 the whole arc. Honest bar.")


if __name__ == "__main__":
    main()
