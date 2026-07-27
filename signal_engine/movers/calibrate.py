"""Movers sleeve calibration — empirical bucket lookup from the 16-year daily panel.

No fitted model, no free parameters to overfit: the "predictor" is a transparent lookup of
MEASURED base rates on `data/research/long_daily_panel.parquet` (738k rows, ~16y, survivor
large/mid caps — an OPTIMISTIC ceiling, stated on every surface). For each feature bucket we
store: P(|next-day ret| >= 5%), P(>=10%), P(up | big move), median |big move|, and n.

Buckets (all computed from data available strictly BEFORE the predicted day):
  prev_ret : yesterday's SIGNED return bucket — {0-3, 3-5} unsigned for small moves,
             {up5-10, up10+, dn5-10, dn10+} once |move|>=5% so direction is conditioned
             on the SIGN of the qualifying move (a -19% crasher lands in `dn10+`, an
             up-spike in `up10+` — they get DIFFERENT next-day directional splits)
  vol20    : 20-day realized vol tercile {low, mid, high}
  streak   : yesterday was the 2nd+ consecutive |ret|>=5% day (circuit-streak proxy)

Honesty contract baked into the output: direction probabilities are stored AS MEASURED —
the spike-hunter memo (docs/SPIKE_HUNTER_FINDINGS.md) proved signed direction is ~un-
forecastable OOS, so most buckets sit near 50% and the sleeve must SAY so rather than
manufacture conviction. Direction is an EXPLICIT tail split (p_next_up_big /
p_next_down_big = P(next ret >= +5%) / P(next ret <= -5%)) — never a blended number.

Run:  .venv/bin/python -m signal_engine.movers.calibrate
Output: data/models/movers_calibration.json
"""

from __future__ import annotations

import json
import os
from typing import Dict

PANEL = "data/research/long_daily_panel.parquet"
OUT = "data/models/movers_calibration.json"

def _signed_bucket(ret: float) -> str:
    """Bucket yesterday's SIGNED return. Small moves (<5%) stay unsigned (direction is
    noise there); once |move|>=5% the sign matters, so up-spikes and down-crashes get
    separate buckets (`up5-10`/`up10+` vs `dn5-10`/`dn10+`). Must stay identical to
    predictor._signed_bucket."""
    a = abs(ret)
    if a >= 10.0:
        return "up10+" if ret > 0 else "dn10+"
    if a >= 5.0:
        return "up5-10" if ret > 0 else "dn5-10"
    if a >= 3.0:
        return "3-5"
    return "0-3"


def build_calibration(panel_path: str = PANEL) -> Dict:
    import numpy as np
    import pandas as pd

    df = pd.read_parquet(panel_path, columns=["symbol", "session_date", "close"])
    df = df.sort_values(["symbol", "session_date"])
    g = df.groupby("symbol", sort=False)
    df["ret"] = g["close"].pct_change() * 100.0
    df["vol20"] = g["ret"].transform(lambda s: s.rolling(20, min_periods=10).std())
    # Features known at the close BEFORE the predicted day:
    df["prev_ret"] = g["ret"].shift(1)          # SIGNED — direction conditions on this
    df["prev_vol20"] = g["vol20"].shift(1)
    prev_big = (g["ret"].shift(1).abs() >= 5.0)
    prev2_big = (g["ret"].shift(2).abs() >= 5.0)
    df["streak"] = (prev_big & prev2_big)

    df = df.dropna(subset=["ret", "prev_ret", "prev_vol20"])
    lo_t, hi_t = df["prev_vol20"].quantile([1 / 3, 2 / 3])
    df["vol_bucket"] = np.where(df["prev_vol20"] < lo_t, "low",
                                np.where(df["prev_vol20"] < hi_t, "mid", "high"))
    df["abs_bucket"] = df["prev_ret"].apply(_signed_bucket)

    out: Dict = {
        "panel_rows": int(len(df)),
        "panel_span": [str(df.session_date.min())[:10], str(df.session_date.max())[:10]],
        "vol_terciles": [float(lo_t), float(hi_t)],
        "base": {
            "p_big5": float((df.ret.abs() >= 5).mean()),
            "p_big10": float((df.ret.abs() >= 10).mean()),
        },
        "buckets": {},
        "caveats": [
            "survivor large/mid-cap panel — every probability is an OPTIMISTIC ceiling",
            "direction near-unforecastable OOS (SPIKE_HUNTER_FINDINGS kill-shot #1)",
            "post-big-day demeaned drift is NEGATIVE (kill-shot #3) — this sleeve MEASURES, "
            "it does not promise",
        ],
    }
    for (ab, vb, streak), grp in df.groupby(["abs_bucket", "vol_bucket", "streak"]):
        big5 = grp[grp.ret.abs() >= 5]
        key = f"{ab}|{vb}|{'streak' if streak else 'nostreak'}"
        out["buckets"][key] = {
            "n": int(len(grp)),
            "p_big5": float((grp.ret.abs() >= 5).mean()),
            "p_big10": float((grp.ret.abs() >= 10).mean()),
            # Direction as an EXPLICIT tail split — P(next up big) vs P(next down big) — so a
            # signed bucket (e.g. dn10+) reports its own measured bounce/continuation rate.
            "p_next_up_big": float((grp.ret >= 5).mean()),
            "p_next_down_big": float((grp.ret <= -5).mean()),
            "p_up_given_big5": float((big5.ret > 0).mean()) if len(big5) >= 30 else None,
            "n_big5": int(len(big5)),
            "median_bigmove_pct": float(big5.ret.abs().median()) if len(big5) else None,
            "mean_next_ret_pct": float(grp.ret.mean()),
        }
    return out


def main() -> int:
    cal = build_calibration()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(cal, f, indent=1)
    print(f"panel: {cal['panel_rows']} rows {cal['panel_span']}")
    print(f"unconditional: P(|ret|>=5%)={cal['base']['p_big5']*100:.2f}%  "
          f"P(>=10%)={cal['base']['p_big10']*100:.3f}%")
    rows = sorted(cal["buckets"].items(), key=lambda kv: -kv[1]["p_big5"])
    print(f"{'bucket':24s} {'n':>7s} {'P(big5)':>8s} {'lift':>6s} "
          f"{'P(up|big)':>9s} {'up/dn tail':>13s}")
    for k, v in rows:
        lift = v["p_big5"] / cal["base"]["p_big5"] if cal["base"]["p_big5"] else 0
        pup = f"{v['p_up_given_big5']*100:.0f}%" if v["p_up_given_big5"] is not None else "n<30"
        tail = f"{v['p_next_up_big']*100:.0f}%/{v['p_next_down_big']*100:.0f}%"
        print(f"{k:24s} {v['n']:>7d} {v['p_big5']*100:>7.2f}% {lift:>5.1f}x {pup:>9s} {tail:>13s}")
    print(f"\nwritten -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
