"""Point-in-time / no-look-ahead causality PROOF for the research dataset (additive).

Two adversarial checks the panel relies on:

1. FEATURE causality — for a few random (symbol, session, bar t), recompute the feature on a
   TRUNCATED history ``day_df[:t+1]`` (the engine literally cannot see bars > t) and assert it
   equals the vectorized full-session value at t. If any feature peeked forward, truncation
   would change its value.

2. LABEL causality — assert each forward-return label at t equals a quantity built ONLY from
   bars > t (close_{t+h}/close_t - 1), and that ``ts_exit > ts``. A label that leaked into the
   present would not match the forward-only reconstruction.

Run: python -m signal_engine.research.check_causality
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signal_engine.config import load_config
from signal_engine.research.dataset import HORIZONS, _session_features
from signal_engine.storage.bars import ParquetBarStore

# Features that are pure functions of bars <= t and are NOT session-anchored start-up windows
# we already NaN out. (or_pos/gap depend on session anchors but still only use bars <= t.)
_CHECK_FEATURES = [
    "ret_1", "ret_5", "ret_15", "rvol_realized", "vwap_dist_pct", "vwap_z",
    "rvol", "atr_pct", "adx", "ema_spread_pct", "rsi", "macd_hist",
    "or_pos", "frac_diff", "dist_round_pct",
]


def _one_session(store: ParquetBarStore, symbol: str, rng: np.random.Generator):
    hist = store.load_symbol_history(symbol)
    days = hist.index.normalize()
    uniq = np.unique(days.to_numpy())
    # pick a day with a full session and a known prior close
    for _ in range(20):
        di = int(rng.integers(1, len(uniq)))
        day = uniq[di]
        day_df = hist[days == day].sort_index()
        if len(day_df) >= 200:
            prior = hist[days == uniq[di - 1]]["close"].iloc[-1]
            day_df = day_df.copy()
            day_df.attrs["prior_close"] = float(prior)
            return symbol, pd.Timestamp(day), day_df
    return None


def check_feature_causality(n_checks: int = 30, seed: int = 7) -> bool:
    cfg = load_config()
    store = ParquetBarStore(cfg.env.parquet_dir)
    syms = ["RELIANCE", "TCS", "SBIN", "TATASTEEL", "MARUTI", "SUZLON", "TITAN", "NTPC"]
    rng = np.random.default_rng(seed)

    full_cache = {}
    n_ok = 0
    n_total = 0
    max_abs_diff = 0.0
    for _ in range(n_checks):
        sym = syms[int(rng.integers(0, len(syms)))]
        got = _one_session(store, sym, rng)
        if got is None:
            continue
        symbol, day, day_df = got
        key = (symbol, str(day))
        if key not in full_cache:
            full_cache[key] = _session_features(day_df)
        full_feat = full_cache[key]

        n = len(day_df)
        t = int(rng.integers(40, n - 5))  # skip warm-up, leave room before close

        # Recompute on TRUNCATED history [0..t] — the only data available at bar t.
        trunc = day_df.iloc[: t + 1].copy()
        trunc.attrs["prior_close"] = day_df.attrs["prior_close"]
        trunc_feat = _session_features(trunc)

        for f in _CHECK_FEATURES:
            v_full = full_feat[f].iloc[t]
            v_trunc = trunc_feat[f].iloc[t]
            n_total += 1
            if (pd.isna(v_full) and pd.isna(v_trunc)):
                n_ok += 1
                continue
            diff = abs(float(v_full) - float(v_trunc))
            max_abs_diff = max(max_abs_diff, diff)
            if diff <= 1e-9 + 1e-7 * abs(float(v_full)):
                n_ok += 1
            else:
                print(f"  LEAK? {symbol} {day.date()} t={t} {f}: full={v_full} trunc={v_trunc} diff={diff}")

    ok = (n_ok == n_total) and n_total > 0
    print(f"[feature-causality] {n_ok}/{n_total} feature-values identical under truncation; "
          f"max|diff|={max_abs_diff:.2e} -> {'PASS' if ok else 'FAIL'}")
    return ok


def check_label_causality(n_checks: int = 30, seed: int = 11) -> bool:
    cfg = load_config()
    store = ParquetBarStore(cfg.env.parquet_dir)
    syms = ["RELIANCE", "TCS", "SBIN", "TATASTEEL", "MARUTI", "SUZLON"]
    rng = np.random.default_rng(seed)
    n_ok = 0
    n_total = 0
    exit_ok = True
    for _ in range(n_checks):
        sym = syms[int(rng.integers(0, len(syms)))]
        got = _one_session(store, sym, rng)
        if got is None:
            continue
        symbol, day, day_df = got
        feat = _session_features(day_df)
        cv = day_df["close"].to_numpy()
        n = len(day_df)
        t = int(rng.integers(20, n - max(HORIZONS) - 1))
        for h in HORIZONS:
            label = feat[f"fwd_ret_{h}"].iloc[t]
            # Reconstruct from FORWARD bars only.
            recon = cv[t + h] / cv[t] - 1.0
            n_total += 1
            if abs(float(label) - recon) <= 1e-9 + 1e-7 * abs(recon):
                n_ok += 1
            else:
                print(f"  LABEL MISMATCH {symbol} {day.date()} t={t} h={h}: {label} vs {recon}")
        # ts_exit strictly after ts.
        if not (feat["ts_exit"].iloc[t] > day_df.index[t]):
            exit_ok = False
            print(f"  TS_EXIT not forward at {symbol} {day.date()} t={t}")

    ok = (n_ok == n_total) and n_total > 0 and exit_ok
    print(f"[label-causality] {n_ok}/{n_total} forward labels match forward-only reconstruction; "
          f"ts_exit>ts={exit_ok} -> {'PASS' if ok else 'FAIL'}")
    return ok


def check_no_feature_label_overlap() -> bool:
    """Sanity: no label column name leaks into the feature list."""
    from signal_engine.research.dataset import FEATURE_COLUMNS, LABEL_COLUMNS
    overlap = set(FEATURE_COLUMNS) & set(LABEL_COLUMNS)
    ok = len(overlap) == 0
    print(f"[feature/label disjoint] overlap={overlap or '{}'} -> {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    print("==== POINT-IN-TIME CAUSALITY PROOF ====")
    a = check_feature_causality()
    b = check_label_causality()
    c = check_no_feature_label_overlap()
    print("\nALL CAUSALITY CHECKS:", "PASS" if (a and b and c) else "FAIL")
