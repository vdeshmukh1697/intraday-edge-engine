"""PEAD (post-earnings-announcement-drift) test — the first DIFFERENT-INFORMATION signal.

Overlays Yahoo earnings events (``events_dataset``) onto the daily swing panel and asks the
documented question: after a positive earnings SURPRISE, do prices keep drifting up for weeks
(under-reaction)? Runs through the SAME honest harness as everything else — net of delivery cost,
OOS headline, monthly walk-forward PBO, gated by ``edge_verdict``.

POINT-IN-TIME DISCIPLINE: the tradeable entry is the first session STRICTLY AFTER the announcement
date (``session_date > ann_date``), so the result is fully public before entry — no look-ahead,
regardless of whether the company reported intraday or after-market. (Conservative: gives up the
announcement-day move, keeps the multi-day drift.)

Run:  .venv/bin/python -m signal_engine.research.pead_experiment
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signal_engine.research.delivery_costs import delivery_breakeven_pct
from signal_engine.research.events_dataset import load_or_build
from signal_engine.research.swing_probe import evaluate_swing_signal


def attach_pead(swing: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Add ``surprise_at_entry`` to the swing panel: for each earnings event, mark the first
    session strictly after the announcement with that event's surprise%. NaN elsewhere."""
    swing = swing.copy()
    swing["session_date"] = pd.to_datetime(swing["session_date"])
    ev = events.dropna(subset=["surprise_pct"]).copy()
    ev["ann_date"] = pd.to_datetime(ev["ann_date"])

    sessions = swing[["symbol", "session_date"]].drop_duplicates().sort_values("session_date")
    ev = ev.sort_values("ann_date")
    # First session strictly AFTER the announcement, per symbol.
    mapped = pd.merge_asof(
        ev, sessions, by="symbol", left_on="ann_date", right_on="session_date",
        direction="forward", allow_exact_matches=False,
    ).dropna(subset=["session_date"])

    # If two events map to the same entry session (rare), keep the latest announcement.
    mapped = mapped.sort_values("ann_date").drop_duplicates(["symbol", "session_date"], keep="last")
    key = mapped.set_index(["symbol", "session_date"])["surprise_pct"]
    swing["surprise_at_entry"] = swing.set_index(["symbol", "session_date"]).index.map(key)
    return swing


def _eval(swing, mask, horizon, cost, label):
    s = evaluate_swing_signal(swing, mask, direction=1, horizon=horizon, cost_per_trade=cost)
    v = s.verdict
    flag = "✅ PASS" if v.passed else "—"
    print(f"  {label:<42} n={s.n:>5} WR={s.win_rate*100:>5.1f}% PF={s.profit_factor:>5.2f} "
          f"netμ={s.mean_fwd_ret*100:>+6.3f}% PBO={v.pbo:.2f}  {flag}")
    return s


def main() -> None:
    swing = pd.read_parquet("data/research/swing_dataset.parquet")
    events = load_or_build()
    if events.empty:
        print("No earnings events available — run events_dataset first.")
        return
    swing = attach_pead(swing, events)
    cost = delivery_breakeven_pct()

    entries = swing["surprise_at_entry"].notna()
    oos_entries = entries & swing["is_oos"].to_numpy()
    print(f"PEAD overlay: {int(entries.sum())} post-earnings entry sessions "
          f"({int(oos_entries.sum())} OOS) across {events['symbol'].nunique()} names · "
          f"events {events['ann_date'].min().date()}..{events['ann_date'].max().date()}")
    print(f"Cost/trade={cost*100:.3f}% (delivery). Gate: n>=2000 AND (WR>=52% OR PF>=1.10) AND PBO<0.10.\n")

    surp = swing["surprise_at_entry"]
    # The PEAD thesis: POSITIVE surprise -> upward drift. Test sign + magnitude buckets vs the
    # all-earnings baseline (does conditioning on a BEAT actually help?).
    print("=== Long after earnings, by surprise bucket (hold 20d, OOS) ===")
    _eval(swing, entries, 20, cost, "all earnings (baseline)")
    _eval(swing, entries & (surp > 0), 20, cost, "positive surprise (beat)")
    _eval(swing, entries & (surp <= 0), 20, cost, "negative surprise (miss)")
    _eval(swing, entries & (surp >= 5), 20, cost, "beat >= +5%")
    _eval(swing, entries & (surp >= 10), 20, cost, "beat >= +10%")
    _eval(swing, entries & (surp >= 25), 20, cost, "beat >= +25% (big beat)")

    print("\n=== Best bucket across horizons (does the drift have legs?) ===")
    for h in (5, 10, 20):
        _eval(swing, entries & (surp >= 5), h, cost, f"beat >= +5%, hold {h}d")

    print("\n=== Drift direction sanity (gross, OOS — is the sign right?) ===")
    for lab, m in (("beat >=+5%", entries & (surp >= 5)),
                   ("miss <=-5%", entries & (surp <= -5))):
        sel = m.to_numpy() & swing["is_oos"].to_numpy() & np.isfinite(swing["fwd_ret_20"].to_numpy())
        g = swing["fwd_ret_20"].to_numpy()[sel]
        if len(g):
            print(f"  {lab:<14} gross 20d drift μ={g.mean()*100:+.3f}% n={len(g)} "
                  f"WR={(g>0).mean()*100:.0f}%")

    print("\nReminder: PASS = candidate, not edge. Yahoo surprise is consensus-based (sparse for "
          "small/new names); survivorship still applies. Validate on a recent holdout before conviction.")


if __name__ == "__main__":
    main()
