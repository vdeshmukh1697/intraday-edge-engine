"""Score the microstructure shadow signal: does sign(dir_score) predict the NEXT bar?

For each logged bar, the "outcome" is the FOLLOWING bar's return for that symbol. We grade
whether the signal's sign matched the next bar's direction and accumulate a hit-rate vs the
50% coin-flip baseline — the honest test of whether order-book imbalance / volume-delta
carries any directional information BEFORE it is ever allowed near a trade.

Run:  .venv/bin/python -m signal_engine.microstructure.scorer [--day YYYY-MM-DD] [--db PATH]
"""

from __future__ import annotations

from typing import Dict, List, Optional


def score_day(store, day: str, min_ticks: int = 3) -> Dict:
    """Fill next_ret_pct/dir_correct for ``day`` and return a scoreboard dict."""
    rows = store.fetch_day(day)
    # group by symbol, ordered by bar_ts (fetch_day already orders)
    by_sym: Dict[str, List[dict]] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)

    graded = 0
    correct = 0
    nonzero = 0  # signals with a directional lean AND a non-flat next bar
    nz_correct = 0
    for sym, seq in by_sym.items():
        for i in range(len(seq) - 1):
            cur, nxt = seq[i], seq[i + 1]
            nret = nxt["bar_ret_pct"]
            if nret is None or cur["n_ticks"] is None or cur["n_ticks"] < min_ticks:
                continue
            ds = cur["dir_score"] or 0.0
            # correct = the signal's sign matched the next bar's direction (flat next bar or
            # flat signal is graded as a non-call: counted in `graded` but not in `nonzero`).
            dir_correct = int((ds > 0 and nret > 0) or (ds < 0 and nret < 0))
            store.set_outcome(sym, cur["bar_ts"], nret, dir_correct if ds != 0 else None)
            graded += 1
            if ds != 0 and nret != 0:
                nonzero += 1
                nz_correct += dir_correct
    return {
        "day": day, "graded": graded,
        "directional_calls": nonzero,
        "hit_rate": (nz_correct / nonzero) if nonzero else None,
        "baseline": 0.5,
    }


def main() -> int:
    import argparse
    from datetime import datetime

    import pytz

    from signal_engine.config import load_config
    from signal_engine.microstructure.store import MicrostructureStore

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--day", default=datetime.now(pytz.timezone("Asia/Kolkata")).date().isoformat())
    args = ap.parse_args()
    db = args.db or load_config().env.db_url
    store = MicrostructureStore(db)
    try:
        res = score_day(store, args.day)
    finally:
        store.close()
    hr = f"{res['hit_rate']*100:.1f}%" if res["hit_rate"] is not None else "n/a"
    print(f"microstructure shadow — {res['day']}: graded {res['graded']} bars, "
          f"{res['directional_calls']} directional calls, next-bar hit rate {hr} "
          f"(vs 50% coin-flip). Shadow only — never gates a trade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
