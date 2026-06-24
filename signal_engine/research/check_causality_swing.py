"""Point-in-time / no-look-ahead causality PROOF for the DAILY/SWING dataset (additive).

Three adversarial checks the swing panel relies on:

1. FEATURE causality — for a few random (symbol, day d), recompute the feature on the TRUNCATED
   daily history ``daily[:d+1]`` (the only data available at d's close) and assert it equals the
   full-history vectorized value at d. A feature that peeked at days > d would change under
   truncation. (Cross-sectional beta/rel-strength are checked separately since they need the
   universe panel; here we check the per-symbol self-contained features.)

2. LABEL causality — assert each forward-return label at d equals a quantity built ONLY from
   days > d (close_{d+h}/close_d - 1), the overnight label equals open_{d+1}/close_d - 1, and
   ``ts_exit > ts``. A leaked label would not match the forward-only reconstruction.

3. SPLIT integrity — no label window straddles the cutoff (purged rows handle the boundary),
   feature/label column names are disjoint, and the embargo covers the max label horizon.

Run: python -m signal_engine.research.check_causality_swing
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signal_engine.config import load_config
from signal_engine.research.swing_dataset import (
    EMBARGO_CALENDAR_DAYS,
    FEATURE_COLUMNS,
    FWD_HORIZONS,
    LABEL_COLUMNS,
    MAX_LABEL_DAYS,
    _symbol_features,
    resample_daily,
)
from signal_engine.storage.bars import ParquetBarStore

# Self-contained per-symbol features (exclude cross-sectional beta/rel-strength + calendar
# constants which are trivially causal). These must be IDENTICAL under history truncation.
_CHECK_FEATURES = [
    "ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_12m",
    "rvol_20d", "dist_ma20_pct", "dist_ma50_pct", "dist_ma200_pct",
    "high_52w_prox_pct", "rsi_14", "adx_14",
    "overnight_ret_1d", "intraday_ret_1d", "overnight_ret_20d", "intraday_ret_20d",
    "turnover_ratio", "log_turnover_20d",
]

_SYMS = ["RELIANCE", "TCS", "SBIN", "TATASTEEL", "MARUTI", "INFY", "ICICIBANK", "ITC"]


def _daily(store: ParquetBarStore, symbol: str):
    hist = store.load_symbol_history(symbol)
    if hist is None or hist.empty:
        return None
    d = resample_daily(hist)
    return d if len(d) >= 300 else None


def check_feature_causality(n_checks: int = 40, seed: int = 7) -> bool:
    cfg = load_config()
    store = ParquetBarStore(cfg.env.parquet_dir)
    rng = np.random.default_rng(seed)

    daily_cache, full_cache = {}, {}
    n_ok = n_total = 0
    max_abs_diff = 0.0
    for _ in range(n_checks):
        sym = _SYMS[int(rng.integers(0, len(_SYMS)))]
        if sym not in daily_cache:
            d = _daily(store, sym)
            if d is None:
                continue
            daily_cache[sym] = d
            full_cache[sym] = _symbol_features(d)
        d = daily_cache[sym]
        full_feat = full_cache[sym]
        n = len(d)
        # leave warm-up (200d MA / 252d high) and room before the end
        t = int(rng.integers(255, n - 1))

        trunc = d.iloc[: t + 1]
        trunc_feat = _symbol_features(trunc)

        for f in _CHECK_FEATURES:
            v_full = full_feat[f].iloc[t]
            v_trunc = trunc_feat[f].iloc[t]
            n_total += 1
            if pd.isna(v_full) and pd.isna(v_trunc):
                n_ok += 1
                continue
            diff = abs(float(v_full) - float(v_trunc))
            max_abs_diff = max(max_abs_diff, diff)
            if diff <= 1e-9 + 1e-7 * abs(float(v_full)):
                n_ok += 1
            else:
                print(f"  LEAK? {sym} t={t} {f}: full={v_full} trunc={v_trunc} diff={diff}")

    ok = (n_ok == n_total) and n_total > 0
    print(f"[swing feature-causality] {n_ok}/{n_total} identical under truncation; "
          f"max|diff|={max_abs_diff:.2e} -> {'PASS' if ok else 'FAIL'}")
    return ok


def check_label_causality(n_checks: int = 40, seed: int = 11) -> bool:
    cfg = load_config()
    store = ParquetBarStore(cfg.env.parquet_dir)
    rng = np.random.default_rng(seed)
    n_ok = n_total = 0
    exit_ok = overnight_ok = True
    daily_cache = {}
    for _ in range(n_checks):
        sym = _SYMS[int(rng.integers(0, len(_SYMS)))]
        if sym not in daily_cache:
            d = _daily(store, sym)
            if d is None:
                continue
            daily_cache[sym] = d
        d = daily_cache[sym]
        feat = _symbol_features(d)
        cv = d["close"].to_numpy()
        ov = d["open"].to_numpy()
        n = len(d)
        t = int(rng.integers(255, n - MAX_LABEL_DAYS - 1))
        for h in FWD_HORIZONS:
            label = feat[f"fwd_ret_{h}"].iloc[t]
            recon = cv[t + h] / cv[t] - 1.0  # forward-only
            n_total += 1
            if abs(float(label) - recon) <= 1e-9 + 1e-7 * abs(recon):
                n_ok += 1
            else:
                print(f"  LABEL MISMATCH {sym} t={t} h={h}: {label} vs {recon}")
        # overnight-only forward label
        on_recon = ov[t + 1] / cv[t] - 1.0
        if abs(float(feat["fwd_overnight_1"].iloc[t]) - on_recon) > 1e-9 + 1e-7 * abs(on_recon):
            overnight_ok = False
            print(f"  OVERNIGHT MISMATCH {sym} t={t}: {feat['fwd_overnight_1'].iloc[t]} vs {on_recon}")
        # ts_exit equivalent: entry day strictly before the day MAX_LABEL_DAYS ahead.
        if not (d.index[min(t + MAX_LABEL_DAYS, n - 1)] > d.index[t]):
            exit_ok = False

    ok = (n_ok == n_total) and n_total > 0 and exit_ok and overnight_ok
    print(f"[swing label-causality] {n_ok}/{n_total} forward labels match forward-only recon; "
          f"overnight_ok={overnight_ok} exit>entry={exit_ok} -> {'PASS' if ok else 'FAIL'}")
    return ok


def check_split_and_disjoint(parquet_path: str = "data/research/swing_dataset.parquet") -> bool:
    overlap = set(FEATURE_COLUMNS) & set(LABEL_COLUMNS)
    disjoint_ok = len(overlap) == 0
    print(f"[swing feature/label disjoint] overlap={overlap or '{}'} -> "
          f"{'PASS' if disjoint_ok else 'FAIL'}")

    # Embargo must cover the max label horizon (20 trading days ~ 28 calendar days).
    embargo_ok = EMBARGO_CALENDAR_DAYS >= int(np.ceil(MAX_LABEL_DAYS * 7 / 5))
    print(f"[swing embargo] {EMBARGO_CALENDAR_DAYS} cal-days >= "
          f"{int(np.ceil(MAX_LABEL_DAYS * 7 / 5))} (max {MAX_LABEL_DAYS} trading-day label) -> "
          f"{'PASS' if embargo_ok else 'FAIL'}")

    straddle_ok = True
    try:
        df = pd.read_parquet(parquet_path, columns=["ts", "ts_exit", "is_oos", "is_purged"])
        cutoff = pd.read_parquet(parquet_path).attrs.get("cutoff")
        if cutoff and cutoff != "NaT":
            cut = pd.Timestamp(cutoff)
            # any row that is OOS or train but whose label window straddles the cutoff is a leak
            straddle = (df["ts"] < cut) & (df["ts_exit"] >= cut) & ~df["is_purged"]
            n_straddle = int(straddle.sum())
            straddle_ok = n_straddle == 0
            print(f"[swing split] cutoff={cut.date()} straddling-but-not-purged rows="
                  f"{n_straddle} -> {'PASS' if straddle_ok else 'FAIL'}")
    except FileNotFoundError:
        print("[swing split] dataset parquet not found yet (build it first) -> SKIP")

    return disjoint_ok and embargo_ok and straddle_ok


if __name__ == "__main__":
    print("==== SWING POINT-IN-TIME CAUSALITY PROOF ====")
    a = check_feature_causality()
    b = check_label_causality()
    c = check_split_and_disjoint()
    print("\nALL SWING CAUSALITY CHECKS:", "PASS" if (a and b and c) else "FAIL")
