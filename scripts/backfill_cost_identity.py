#!/usr/bin/env python
"""One-off backfill of the P3.1 cost-identity columns on historical paper_trades rows.

Rows written before 2026-07-07 predate pnl_pct_gross/cost_pct. Both are EXACTLY
recoverable from what is already stored — no cost-config assumptions:

    gross = direction_sign * (exit_fill - entry_fill) / entry_fill * 100
    cost  = gross - pnl_pct_net        # precisely what the trader subtracted at close

(paper/trader.py:_close computes net = gross - breakeven_pct(entry); solving for the
cost term uses only persisted fields, so a later change to cost config cannot skew
the backfill.)

Safety: takes a sqlite backup (data/backups/) via the online backup API before any
write; only touches rows where pnl_pct_gross IS NULL; idempotent.

Run:  .venv/bin/python scripts/backfill_cost_identity.py [--db data/signal_engine.sqlite3]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime

SIGN = {"LONG": 1.0, "SHORT": -1.0}


def backup(db_path: str) -> str:
    bdir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(bdir, f"{os.path.basename(db_path)}.{stamp}")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)  # online backup API — WAL-safe
        dst.close()
    finally:
        src.close()
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/signal_engine.sqlite3")
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}")
        return 2

    dest = backup(args.db)
    print(f"backup -> {dest}")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cols = {r["name"] for r in cur.execute("PRAGMA table_info(paper_trades)")}
    if "pnl_pct_gross" not in cols or "cost_pct" not in cols:
        print("pnl_pct_gross/cost_pct columns missing — run the engine once (migration) first.")
        return 2

    rows = cur.execute(
        """SELECT id, direction, entry_fill, exit_fill, pnl_pct_net
           FROM paper_trades WHERE pnl_pct_gross IS NULL"""
    ).fetchall()
    updated = skipped = 0
    for r in rows:
        sign = SIGN.get(r["direction"])
        if (sign is None or r["entry_fill"] in (None, 0)
                or r["exit_fill"] is None or r["pnl_pct_net"] is None):
            skipped += 1
            continue
        gross = sign * (r["exit_fill"] - r["entry_fill"]) / r["entry_fill"] * 100.0
        cost = gross - r["pnl_pct_net"]
        cur.execute(
            "UPDATE paper_trades SET pnl_pct_gross = ?, cost_pct = ? WHERE id = ?",
            (gross, cost, r["id"]),
        )
        updated += 1
    con.commit()

    # Post-checks: identity holds on every populated row; recovered cost is sane (>0, <1%).
    bad = cur.execute(
        """SELECT COUNT(*) FROM paper_trades
           WHERE pnl_pct_gross IS NOT NULL
             AND ABS(pnl_pct_gross - cost_pct - pnl_pct_net) > 1e-6"""
    ).fetchone()[0]
    stats = cur.execute(
        """SELECT COUNT(*), MIN(cost_pct), AVG(cost_pct), MAX(cost_pct)
           FROM paper_trades WHERE cost_pct IS NOT NULL"""
    ).fetchone()
    remaining = cur.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE pnl_pct_gross IS NULL"
    ).fetchone()[0]
    con.close()

    print(f"updated={updated} skipped={skipped} still_null={remaining}")
    print(f"identity violations (|gross-cost-net|>1e-6): {bad}")
    n, lo, avg, hi = stats
    print(f"cost_pct over {n} rows: min={lo:.4f} avg={avg:.4f} max={hi:.4f} (%)")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
