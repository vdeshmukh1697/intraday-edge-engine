#!/usr/bin/env python
"""Live-session monitor for the P0-gated era (STRATEGY_IMPROVEMENT_PLAN_2026-07).

One-shot snapshot of TODAY's session: feed liveness, gated trade behaviour, and the
gate-effect summary that proves P0 is actually firing (fewer trades, no stop < the
~0.57% friction floor, cost share of gross under control).

Run:  .venv/bin/python scripts/session_monitor.py
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytz

IST = pytz.timezone("Asia/Kolkata")
DB = "data/signal_engine.sqlite3"
COST_FLOOR = 0.1424 / 0.25  # max_cost_r=0.25 at ref friction 0.1424% -> stop must be >= 0.57%


def main() -> int:
    now = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    print(f"=== SESSION MONITOR {now:%Y-%m-%d %H:%M:%S IST} ===")

    ls = con.execute("SELECT * FROM live_status WHERE id=1").fetchone()
    if ls:
        bar_ts = ls["bar_ts"] or "—"
        age = "—"
        if ls["updated_ts"]:
            try:
                upd = datetime.fromisoformat(ls["updated_ts"])
                age = f"{(now - upd).total_seconds():.0f}s ago"
            except ValueError:
                pass
        print(f"feed: last update {age} | bar {bar_ts} | bars={ls['bars_processed']} "
              f"| open={ls['open_count']} closed_today={ls['closed_today']} watching={ls['watching']}")
    else:
        print("feed: no live_status row yet (live_job not started)")

    trades = con.execute(
        """SELECT symbol, direction, entry_ts, exit_reason, pnl_pct_net, r_multiple,
                  ROUND(ABS(entry_fill-stop_loss)/entry_fill*100, 3) AS stop_pct,
                  pnl_pct_gross, cost_pct, alpha_pct
           FROM paper_trades WHERE date(entry_ts)=? ORDER BY entry_ts""",
        (today,),
    ).fetchall()
    entries = con.execute(
        "SELECT COUNT(*) FROM predictions WHERE date(ts)=? AND kind='entry'", (today,)
    ).fetchone()[0]
    skips = con.execute(
        "SELECT COUNT(*) FROM predictions WHERE date(ts)=? AND kind='skip'", (today,)
    ).fetchone()[0]

    print(f"\nTODAY: {len(trades)} closed trades | {entries} entry alerts | {skips} skip alerts")
    if trades:
        stops = [t["stop_pct"] for t in trades if t["stop_pct"] is not None]
        net = sum(t["pnl_pct_net"] or 0 for t in trades)
        min_stop = min(stops) if stops else None
        print(f"  net {net:+.2f}% | narrowest stop taken: {min_stop:.3f}% "
              f"(P0 floor {COST_FLOOR:.2f}%) -> {'OK ✓' if (min_stop or 9) >= COST_FLOOR - 1e-9 else 'BELOW FLOOR ✗'}")
        print(f"  {'symbol':10s} {'dir':5s} {'entry':8s} {'stop%':>6s} {'exit':10s} {'netR':>6s} {'net%':>7s}")
        for t in trades:
            et = (t["entry_ts"] or "")[11:16]
            print(f"  {t['symbol']:10s} {t['direction']:5s} {et:8s} {t['stop_pct'] or 0:6.3f} "
                  f"{t['exit_reason'] or '—':10s} {t['r_multiple'] or 0:+6.2f} {t['pnl_pct_net'] or 0:+7.2f}")
    else:
        print("  (no closed trades yet)")

    # Gate-effect headline vs the pre-gate baseline (15/day, stops down to 0.33%).
    if trades and now.hour >= 15:  # only meaningful once the session is done
        stops = [t["stop_pct"] for t in trades if t["stop_pct"]]
        print(f"\nGATE EFFECT: {len(trades)} trades (pre-gate ~15) | "
              f"all stops >= {min(stops):.2f}% (pre-gate min 0.33%)")

    st = con.execute("SELECT cash FROM portfolio_state WHERE id=1").fetchone()
    if st:
        print(f"\nbook cash: ₹{st['cash']:,.2f}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
