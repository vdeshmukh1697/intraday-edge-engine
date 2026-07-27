#!/usr/bin/env python
"""Pre-registered evaluation of the P0 friction-in-R gate (STRATEGY_IMPROVEMENT_PLAN_2026-07 §3).

FROZEN 2026-07-07, evaluated ONCE after ~10 gated sessions (~session 18 of the book).
Metrics and thresholds are fixed here so the review cannot drift into forking paths:

  (a) cost share of |gross P&L| < 40%          (June target, was ~80% pre-gate)
  (b) gated-era avgR           > -0.05
  (c) gated-era book net %     >= 0

Interpretation is pre-committed: if (a) passes but (c) fails, the verdict is on the
SIGNAL (entries lack edge even at clean costs) — stop tuning vwap_ema_adx gates and
move to the P4 replay candidates. No other conclusions are licensed by this script.

Run:  .venv/bin/python scripts/preregistered_eval.py [--since 2026-07-08] [--db PATH]
"""
from __future__ import annotations

import argparse
import sqlite3

GATE_LIVE_FROM = "2026-07-08"  # first session with max_cost_r=0.25 active (frozen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/signal_engine.sqlite3")
    ap.add_argument("--since", default=GATE_LIVE_FROM,
                    help="first gated session (default: frozen 2026-07-08)")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT date(entry_ts) AS day, pnl_pct_net, pnl_pct_gross, cost_pct, r_multiple,
                  alpha_pct, nifty_ret_pct, direction, won
           FROM paper_trades WHERE date(entry_ts) >= ? ORDER BY entry_ts""",
        (args.since,),
    ).fetchall()
    con.close()

    days = sorted({r["day"] for r in rows})
    n = len(rows)
    print(f"P0 pre-registered eval — gated era since {args.since}: "
          f"{len(days)} sessions, {n} trades")
    if n == 0:
        print("No gated trades yet — nothing to evaluate.")
        return 0
    if len(days) < 10:
        print(f"NOTE: only {len(days)}/10 pre-registered sessions elapsed — "
              f"this is a PREVIEW, not the one committed look.")

    net = sum(r["pnl_pct_net"] or 0.0 for r in rows)
    gross = [r["pnl_pct_gross"] for r in rows if r["pnl_pct_gross"] is not None]
    cost = sum(r["cost_pct"] or 0.0 for r in rows)
    rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    avg_r = sum(rs) / len(rs) if rs else float("nan")
    # Denominator = |sum(gross)|: cost relative to the NET gross edge the strategy actually
    # generated. (Per-trade |g| sums would let churn inflate the denominator and pass the bar
    # trivially — the pre-gate era scores 15.7% that way vs 174% this way.) If gross sums to
    # ~0 the ratio explodes and SHOULD fail: with no gross edge, any cost is too much.
    net_gross = abs(sum(gross))
    cost_share = cost / net_gross * 100.0 if net_gross else float("inf")
    wins = [r for r in rows if r["won"]]
    wr = len(wins) / n * 100.0
    win_r = [r["r_multiple"] for r in wins if r["r_multiple"] is not None]
    loss_r = [r["r_multiple"] for r in rows if not r["won"] and r["r_multiple"] is not None]
    payoff = (abs(sum(win_r) / len(win_r)) / abs(sum(loss_r) / len(loss_r))
              if win_r and loss_r else float("nan"))
    be_wr = 100.0 / (1.0 + payoff) if payoff == payoff and payoff > 0 else float("nan")
    alpha = sum(r["alpha_pct"] or 0.0 for r in rows)

    print(f"\n  trades/day avg      : {n / len(days):.1f}   (pre-gate: ~15, cap-bound)")
    print(f"  win rate            : {wr:.1f}%  vs break-even {be_wr:.1f}% at realized {payoff:.2f}:1")
    print(f"  sum gross / cost / net : {sum(gross):+.2f}% / {cost:.2f}% / {net:+.2f}%")
    print(f"  sum alpha (vs NIFTY)   : {alpha:+.2f}%")

    a = cost_share < 40.0
    b = avg_r > -0.05
    c = net >= 0.0
    print(f"\n  (a) cost share of |gross| < 40% : {cost_share:6.1f}%  -> {'PASS' if a else 'FAIL'}")
    print(f"  (b) avgR > -0.05                : {avg_r:+.3f}   -> {'PASS' if b else 'FAIL'}")
    print(f"  (c) book net >= 0               : {net:+.2f}%  -> {'PASS' if c else 'FAIL'}")

    if len(days) >= 10:
        if a and c:
            print("\nVERDICT (pre-committed): gate did its job AND the book holds zero — "
                  "continue paper-trading unchanged; next look at ~30 sessions.")
        elif a and not c:
            print("\nVERDICT (pre-committed): costs are fixed but the book still loses — "
                  "the SIGNAL lacks edge. Stop tuning vwap_ema_adx; move to P4 replay "
                  "candidates and the survivorship-clean data priority.")
        else:
            print("\nVERDICT (pre-committed): gate failed to control cost share — inspect "
                  "whether trades bypassed the gate (config loaded? scheduler restarted?) "
                  "before drawing any signal conclusion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
