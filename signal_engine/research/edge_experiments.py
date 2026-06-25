"""Step-2 edge experiments (free, zero-new-data) on the cached swing dataset.

Runs the two lowest-cost / highest-odds candidates from docs/EDGE_ROADMAP.md through the SAME
honest harness that produced the no-edge verdict — net of real cost, out-of-sample headline,
monthly walk-forward PBO, gated by ``edge_verdict`` (n>=2000, WR>=52% OR PF>=1.10, PBO<10%):

  A. Low-volatility long-only  — long the calmest cross-sectional quantile, hold 20d.
  B. Overnight drift           — capture only the close->open return (cash buy-close/sell-open vs
                                 the cheap futures-carry framing), gross + net at several costs.

Plus a momentum cross-sectional bonus (rel_strength_20d) and robustness sweeps so a single lucky
quantile/horizon can't masquerade as an edge. Research only; nothing here trades.

Run:  .venv/bin/python -m signal_engine.research.edge_experiments
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signal_engine.ml.evaluate import edge_verdict
from signal_engine.research.delivery_costs import (
    delivery_breakeven_pct,
    futures_short_leg_pct,
)
from signal_engine.research.swing_probe import evaluate_long_short, evaluate_swing_signal

DATA = "data/research/swing_dataset.parquet"


def _line(stats, label: str) -> str:
    v = stats.verdict
    flag = "✅ PASS" if v.passed else "—"
    return (f"  {label:<34} n={stats.n:>6} WR={stats.win_rate*100:>5.1f}% "
            f"PF={stats.profit_factor:>5.2f} netμ={stats.mean_fwd_ret*100:>+6.3f}% "
            f"PBO={v.pbo:>.2f}  {flag}")


def _quantile_mask(df: pd.DataFrame, col: str, q: float, low: bool) -> pd.Series:
    """Per-day cross-sectional quantile membership (low=bottom q, else top q)."""
    rank = df.groupby("session_date")[col].rank(pct=True)
    return (rank <= q) if low else (rank >= 1.0 - q)


def experiment_a_low_vol(df: pd.DataFrame) -> None:
    print("\n=== EXPERIMENT A — Low-volatility LONG-ONLY (calmest names, hold 20d) ===")
    cost = delivery_breakeven_pct()
    print(f"  cost/trade = {cost*100:.3f}% (delivery round-trip incl. slippage), horizon=20d, OOS")
    # Headline + quantile/horizon robustness sweep.
    for q in (0.10, 0.20, 0.30):
        mask = _quantile_mask(df, "rvol_20d", q, low=True)
        s = evaluate_swing_signal(df, mask, direction=1, horizon=20, cost_per_trade=cost)
        print(_line(s, f"long bottom-{int(q*100)}% vol"))
    for h in (10, 5):
        mask = _quantile_mask(df, "rvol_20d", 0.20, low=True)
        s = evaluate_swing_signal(df, mask, direction=1, horizon=h, cost_per_trade=cost)
        print(_line(s, f"long bottom-20% vol, hold {h}d"))
    # Long-short context (short leg via futures cost — flagged not cash-implementable).
    ls = evaluate_long_short(df, -df["rvol_20d"], horizon=20)  # top score = lowest vol
    print(_line(ls.long_only, "L/S long leg (low-vol)"))
    print(_line(ls.long_short, "L/S combined (futures short)"))


def experiment_b_overnight(df: pd.DataFrame) -> None:
    print("\n=== EXPERIMENT B — Overnight drift (close->open), OOS ===")
    on = df["fwd_overnight_1"].to_numpy()
    oos = df["is_oos"].to_numpy() & np.isfinite(on)
    if "is_purged" in df:
        oos &= ~df["is_purged"].to_numpy()
    g = on[oos]
    gross_mean = float(np.mean(g))
    print(f"  GROSS overnight drift (all names, all nights): "
          f"mean={gross_mean*100:+.4f}%/night  n={len(g)}  WR={float((g>0).mean())*100:.1f}%")
    # Net at three honest cost framings (per the roadmap):
    #  (1) daily futures round-trip — you go flat intraday and pay a round-trip EVERY night;
    #  (2) one monthly futures roll amortised over ~20 nights (continuous hold, but then you also
    #      eat intraday drift, which is documented NEGATIVE — shown for reference only);
    #  (3) a 1 bp floor (frictionless-ish) to see the ceiling.
    fut_rt = futures_short_leg_pct()           # ~1 futures round-trip
    for label, cost in (("net @ daily futures round-trip", fut_rt),
                        ("net @ monthly-roll amortised/night", fut_rt / 20.0),
                        ("net @ 1bp floor", 0.0001)):
        net = g - cost
        wr = float((net > 0).mean())
        pf_gains = net[net > 0].sum()
        pf_losses = -net[net < 0].sum()
        pf = float(pf_gains / pf_losses) if pf_losses > 0 else 99.0
        v = edge_verdict(len(net), wr, pf, 0.0)  # pbo not meaningful for the unconditional book
        flag = "✅" if v.passed else "—"
        print(f"  {label:<34} cost={cost*100:.3f}%  netμ={float(np.mean(net))*100:+.4f}%/night "
              f"WR={wr*100:.1f}% PF={pf:.2f} {flag}")
    # Selective overnight: does conditioning on low-vol or momentum improve the per-night drift?
    print("  -- selective overnight (condition the hold) --")
    for name, mask in (("low-vol names only", _quantile_mask(df, "rvol_20d", 0.30, low=True)),
                       ("high-momentum names only", _quantile_mask(df, "rel_strength_20d", 0.30, low=False))):
        m = mask.to_numpy() & oos
        gg = on[m]
        if len(gg) == 0:
            continue
        print(f"     {name:<26} gross μ={float(np.mean(gg))*100:+.4f}%/night n={len(gg)} "
              f"WR={float((gg>0).mean())*100:.1f}%  (vs {gross_mean*100:+.4f}% baseline)")


def experiment_c_momentum(df: pd.DataFrame) -> None:
    print("\n=== BONUS — Cross-sectional momentum LONG-ONLY (rel_strength_20d), OOS ===")
    cost = delivery_breakeven_pct()
    for q in (0.10, 0.20):
        mask = _quantile_mask(df, "rel_strength_20d", q, low=False)  # top = strongest
        s = evaluate_swing_signal(df, mask, direction=1, horizon=20, cost_per_trade=cost)
        print(_line(s, f"long top-{int(q*100)}% momentum, 20d"))
    ls = evaluate_long_short(df, df["rel_strength_20d"], horizon=20)
    print(_line(ls.long_only, "L/S long leg (momentum)"))


def experiment_d_monthly(df: pd.DataFrame) -> None:
    """Clean MONTHLY-rebalance (non-overlapping) entries — the all-days versions enter every day
    and hold 20d, so labels overlap ~20x, which inflates n and makes the monthly-PBO walk-forward
    noisy. Restricting entries to the first trading day of each month removes the overlap and gives
    a more honest robustness read of the low-vol / momentum factors."""
    print("\n=== EXPERIMENT D — MONTHLY rebalance (non-overlapping), hold 20d, OOS ===")
    cost = delivery_breakeven_pct()
    first = df.groupby(df["session_date"].dt.to_period("M"))["session_date"].transform("min")
    month_start = df["session_date"] == first
    for name, col, low in (("low-vol bottom-20%", "rvol_20d", True),
                           ("momentum top-20%", "rel_strength_20d", False)):
        mask = _quantile_mask(df, col, 0.20, low=low) & month_start
        s = evaluate_swing_signal(df, mask, direction=1, horizon=20, cost_per_trade=cost)
        print(_line(s, name + " (monthly)"))


def main() -> None:
    df = pd.read_parquet(DATA)
    print(f"Swing dataset: {df.shape[0]} rows · {df['symbol'].nunique()} names · "
          f"{df['session_date'].min().date()}→{df['session_date'].max().date()} · "
          f"OOS rows={int(df['is_oos'].sum())}")
    print("Gate: n>=2000 AND (WR>=52% OR PF>=1.10) AND PBO<0.10 — same as the no-edge study.")
    experiment_a_low_vol(df)
    experiment_b_overnight(df)
    experiment_c_momentum(df)
    experiment_d_monthly(df)
    print("\nReminder: a PASS here is a CANDIDATE, not a confirmed edge — it still needs "
          "survivorship-clean data + a recent-6-month holdout before any conviction. Paper only.")


if __name__ == "__main__":
    main()
