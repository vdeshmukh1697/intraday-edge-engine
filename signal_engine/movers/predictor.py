"""Movers predictor — daily big-move candidates from the real archived bars.

Maintains a rolling daily-closes cache (``data/movers_daily.parquet``) built from the
nightly 1-min archive (~2k NSE names), computes the calibration features for each symbol
(yesterday's |return|, 20d realized vol tercile, big-day streak), and looks up MEASURED
probabilities from ``data/models/movers_calibration.json`` (see calibrate.py — no fitted
model, just transparent base rates with n).

Honesty contract (docs/SPIKE_HUNTER_FINDINGS.md):
* probabilities come from a survivor large/mid-cap panel — an optimistic ceiling, said so;
* direction is near-unforecastable OOS — a side is only suggested when the SIGN-CONDITIONED
  bucket's measured P(up|big) clears 55% AND the caveat travels with it; a down-crash lands in
  a `dn*` bucket and reports THAT bucket's split (dead-cat bounce), never a blind LONG;
* names coming off a ~10-20% day may be circuit-locked (buyers-only book) — flagged
  "may be un-buyable", and the sleeve treats them as prediction-only.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Dict, List, Optional

DAILY_CACHE = "data/movers_daily.parquet"
CALIBRATION = "data/models/movers_calibration.json"
ADV_FLOOR_CR = 5.0          # ₹5cr average daily turnover -> "fillable" liquidity floor
CIRCUIT_WARN_PCT = 9.5      # |yesterday| beyond this -> possible hard-band lock
DIR_MIN_P = 0.55            # suggest a side only when measured P(up|big) clears this


def load_calibration(path: str = CALIBRATION) -> Dict:
    with open(path) as f:
        return json.load(f)


def _signed_bucket(ret: float) -> str:
    """Signed bucket for yesterday's return — MUST match calibrate._signed_bucket exactly.
    Small moves (<5%) stay unsigned; once |move|>=5% the sign splits up-spikes from
    down-crashes so direction is conditioned on the sign of the qualifying move."""
    a = abs(ret)
    if a >= 10.0:
        return "up10+" if ret > 0 else "dn10+"
    if a >= 5.0:
        return "up5-10" if ret > 0 else "dn5-10"
    if a >= 3.0:
        return "3-5"
    return "0-3"


def update_daily_cache(store, day: date, cache_path: str = DAILY_CACHE) -> "object":
    """Append ``day``'s per-symbol daily bar (from the 1-min archive) to the rolling cache.

    Returns the full cache DataFrame (symbol, day, close, turnover_cr). Idempotent per day.
    Bootstraps by scanning every archived session if the cache doesn't exist yet.
    """
    import pandas as pd

    cache = None
    if os.path.exists(cache_path):
        cache = pd.read_parquet(cache_path)
        if (cache["day"] == day.isoformat()).any():
            return cache  # already ingested — idempotent

    days_to_scan: List[date] = [day]
    if cache is None:
        # Bootstrap: walk the archive for every session date present on disk.
        import glob
        import re

        found = set()
        root = str(getattr(store, "root", "data/parquet"))
        for p in glob.glob(os.path.join(root, "symbol=*", "date=*", "bars.parquet")):
            m = re.search(r"date=(\d{4}-\d{2}-\d{2})", p)
            if m:
                found.add(m.group(1))
        days_to_scan = sorted(date.fromisoformat(d) for d in found)

    rows = []
    for d in days_to_scan:
        for sym in store.list_symbols():
            try:
                df = store.load_session(sym, d)
            except Exception:  # noqa: BLE001
                continue
            if df is None or len(df) == 0:
                continue
            close = float(df["close"].iloc[-1])
            turnover = float((df["close"] * df["volume"]).sum()) / 1e7  # ₹ crore
            rows.append({"symbol": sym, "day": d.isoformat(), "close": close,
                         "turnover_cr": turnover})
    new = pd.DataFrame(rows)
    cache = new if cache is None else pd.concat([cache, new], ignore_index=True)
    cache = cache.drop_duplicates(subset=["symbol", "day"], keep="last")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    cache.to_parquet(cache_path)
    return cache


def build_predictions(daily: "object", cal: Dict, for_day: date,
                      top_n: int = 10) -> List[Dict]:
    """Rank symbols for ``for_day`` using data strictly before it, FILLABILITY-FIRST.

    ``daily``: DataFrame(symbol, day, close, turnover_cr) — the rolling cache.
    Returns the tradeable (fillable, ADV>=₹5cr) names first — ranked 1..k by p_big5, the
    pre-registered shadow-book universe — then the un-fillable circuit-lockers as a clearly
    separable research-only tail (``fillable``==0), so they are RELOCATED (never dropped just
    for being un-buyable). Up to ``top_n`` names in each group.
    """
    import numpy as np
    import pandas as pd

    df = daily[daily["day"] < for_day.isoformat()].sort_values(["symbol", "day"]).copy()
    if df.empty:
        return []
    g = df.groupby("symbol", sort=False)
    df["ret"] = g["close"].pct_change() * 100.0
    # min_periods=6: the archive currently holds ~8 sessions (7 returns); the estimate is
    # noisy at first and converges to a true 20d vol as the archive grows. Honest tradeoff:
    # a short-window vol still separates quiet from wild names, which is all the bucket needs.
    df["vol20"] = g["ret"].transform(lambda s: s.rolling(20, min_periods=6).std())
    df["prev_big"] = df["ret"].abs() >= 5.0
    df["streak"] = df["prev_big"] & g["prev_big"].shift(1).fillna(False)
    df["adv_cr"] = g["turnover_cr"].transform(lambda s: s.rolling(10, min_periods=3).mean())

    last = df.groupby("symbol").tail(1).dropna(subset=["ret", "vol20"])
    if last.empty:
        return []
    lo_t, hi_t = cal["vol_terciles"]
    out: List[Dict] = []
    for _, r in last.iterrows():
        vb = "low" if r.vol20 < lo_t else ("mid" if r.vol20 < hi_t else "high")
        ab = _signed_bucket(r.ret)              # SIGNED — conditions direction on sign of move
        key = f"{ab}|{vb}|{'streak' if r.streak else 'nostreak'}"
        b = cal["buckets"].get(key)
        if b is None or b["n"] < 100:  # never quote a probability off a tiny bucket
            continue
        # Direction lean comes from the SIGN-CONDITIONED bucket: a down-crash lands in a `dn*`
        # bucket and gets THAT bucket's measured P(up|big) (typically a weak dead-cat bounce),
        # never a blind LONG pooled from up-spikes.
        p_up = b.get("p_up_given_big5")
        pred_dir, p_dir = "NONE", None
        if p_up is not None:
            if p_up >= DIR_MIN_P:
                pred_dir, p_dir = "LONG", p_up
            elif (1.0 - p_up) >= DIR_MIN_P:
                pred_dir, p_dir = "SHORT", 1.0 - p_up
        qual_dir = "up" if ab.startswith("up") else ("down" if ab.startswith("dn") else "flat")
        fillable = bool(r.adv_cr and r.adv_cr >= ADV_FLOOR_CR)
        warn = []
        if abs(r.ret) >= CIRCUIT_WARN_PCT:
            warn.append("near circuit band — may be un-buyable/un-sellable at open")
        if not fillable:
            warn.append(f"thin liquidity (ADV ₹{(r.adv_cr or 0):.1f}cr < ₹{ADV_FLOOR_CR:.0f}cr)")
        if pred_dir == "SHORT":
            warn.append("SHORT is hypothetical — no overnight cash shorts in India")
        out.append({
            "for_day": for_day.isoformat(), "symbol": r.symbol,
            "p_big5": b["p_big5"], "p_big10": b["p_big10"],
            "lift": b["p_big5"] / cal["base"]["p_big5"] if cal["base"]["p_big5"] else None,
            "pred_dir": pred_dir, "p_dir": p_dir,
            # Explicit next-day tail split (sign-conditioned) — the honest direction picture.
            "p_next_up_big": b.get("p_next_up_big"), "p_next_down_big": b.get("p_next_down_big"),
            "qual_dir": qual_dir,
            "exp_move_pct": b.get("median_bigmove_pct"),
            "basis": key, "n_bucket": b["n"],
            "fillable": int(fillable), "warn": "; ".join(warn),
            "prev_close": float(r.close),
        })
    # Fillability is the PRIMARY gate: only ADV>=₹5cr names are actually tradeable, so they take
    # ranks 1..k (the pre-registered shadow-book universe). Un-fillable circuit-lockers are KEPT
    # as a research-only tail (ranked strictly after every fillable name, split-able by the
    # `fillable` flag) — RELOCATED, never dropped just because they can't be bought.
    fillable = sorted((d for d in out if d["fillable"]), key=lambda d: -d["p_big5"])[:top_n]
    research = sorted((d for d in out if not d["fillable"]), key=lambda d: -d["p_big5"])[:top_n]
    ranked = fillable + research
    for i, d in enumerate(ranked, start=1):
        d["rank"] = i
    return ranked
