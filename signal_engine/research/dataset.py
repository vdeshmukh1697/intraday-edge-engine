"""Vectorized research dataset builder (additive; panel-only).

Builds ONE big pandas/parquet table with one row per (symbol, sampled-bar) carrying
strictly-causal point-in-time FEATURES and forward LABELS, over a tractable representative
universe (most-liquid archived names, recent ~2 years). Scan agents load the parquet once
and probe alpha hypotheses in seconds.

CARDINAL RULE — POINT-IN-TIME / NO LOOK-AHEAD
---------------------------------------------
* A FEATURE at bar ``t`` uses ONLY bars ``<= t``. Every feature here is built from causal
  primitives in :mod:`signal_engine.indicators.core` (expanding/rolling/Wilder, or shifted
  where the raw definition would otherwise peek). Session-anchored features (VWAP distance,
  opening-range position, minutes-since-open, gap) reset each trading day and never reach
  across the session boundary.
* A LABEL uses forward bars ``> t`` only and is NEVER used as a feature. Forward returns,
  MFE/MAE and the triple-barrier sign are computed WITHIN the same session (no overnight
  carry), so a horizon that would run past the close is truncated/NaN rather than leaking the
  next day's open.
* The IS/OOS split reuses the SAME global calendar-date split + interval embargo as
  :func:`signal_engine.ml.train.date_split_indices`: a row is OOS iff its entry is on/after
  ``cutoff + embargo`` and its whole label window is captured; rows straddling the cutoff are
  flagged purged.

There is NO equity index series in the archive (``list_symbols`` shows no NIFTY/SENSEX/INDEX),
so a relative-strength-vs-index spread is intentionally SKIPPED (documented, not silently).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from signal_engine.config import AppConfig, load_config
from signal_engine.indicators import core as ind
from signal_engine.ml.train import DEFAULT_EMBARGO_DAYS, date_split_indices
from signal_engine.storage.bars import ParquetBarStore

# --------------------------------------------------------------------------- #
# Column groups (exported so probes / scan agents never hard-code names).
# --------------------------------------------------------------------------- #
FEATURE_COLUMNS: List[str] = [
    "ret_1",            # 1-bar log-ish pct return (close_t / close_{t-1} - 1)
    "ret_5",            # k=5-bar trailing return
    "ret_15",           # k=15-bar trailing return
    "rvol_realized",    # realized vol of 1-bar returns over trailing 20 bars
    "vwap_dist_pct",    # (close - session VWAP) / VWAP * 100
    "vwap_z",           # (close - VWAP) / VWAP_sigma  (z within VWAP bands)
    "rvol",             # relative volume vs prior-20-bar mean (current bar excluded)
    "atr_pct",          # ATR(14) / close * 100
    "adx",              # ADX(14)
    "ema_spread_pct",   # (EMA9 - EMA21) / close * 100
    "rsi",              # RSI(14)
    "macd_hist",        # MACD histogram (normalized by close, %)
    "gap_pct",          # (session_open - prior_close) / prior_close * 100  (const within day)
    "or_pos",           # position of close within the 15-min opening range [0,1]-ish
    "minutes_since_open",
    "tod_bucket",       # 0..k time-of-day bucket
    "dow",              # day of week 0=Mon
    "frac_diff",        # frac_diff(close, d=0.5), per-session expanding
    "dist_round_pct",   # signed % distance to nearest round-number level
]

LABEL_COLUMNS: List[str] = [
    "fwd_ret_5",        # forward return over next 5 bars (within session)
    "fwd_ret_15",
    "fwd_ret_30",
    "fwd_ret_60",
    "fwd_mfe_atr",      # max favorable excursion over 30 bars, in ATR units (long-side)
    "fwd_mae_atr",      # max adverse excursion over 30 bars, in ATR units (long-side)
    "tb_sign",          # vol-scaled triple-barrier sign in {-1,0,+1} over 30 bars
]

META_COLUMNS: List[str] = ["symbol", "ts", "ts_exit", "session_date", "is_oos", "is_purged"]

# Sampling / horizon config.
SAMPLE_STRIDE = 5           # keep every 5th bar within a session (~5-min sampling)
RVOL_REALIZED_WIN = 20
HORIZONS = (5, 15, 30, 60)  # forward-return label horizons, in 1-min bars
TB_HORIZON = 30             # triple-barrier / MFE-MAE window, in bars
TB_VOL_MULT = 1.0           # barrier width = TB_VOL_MULT * atr_pct (each side)
TOD_BUCKET_MINUTES = 30     # time-of-day bucket granularity
RECENT_YEARS = 2.0          # recent window length to scope the panel

DEFAULT_OUT = "data/research/alpha_dataset.parquet"


# --------------------------------------------------------------------------- #
# Per-session causal feature/label construction.
# --------------------------------------------------------------------------- #
def _session_features(day_df: pd.DataFrame) -> pd.DataFrame:
    """Causal features for ONE trading session. All values at row t use only rows <= t,
    except the explicitly-named forward LABELS which use rows > t (never fed back as
    features). Session-anchored quantities (VWAP, opening range, gap) reset each day.
    """
    close = day_df["close"]
    n = len(day_df)
    out = pd.DataFrame(index=day_df.index)

    # --- trailing returns (causal: ratio of present to past close) ---------
    out["ret_1"] = close.pct_change(1)
    out["ret_5"] = close.pct_change(5)
    out["ret_15"] = close.pct_change(15)

    # realized vol of 1-bar returns over trailing window (rolling, ends at t).
    out["rvol_realized"] = out["ret_1"].rolling(RVOL_REALIZED_WIN).std()

    # --- VWAP distance / z (running VWAP + running sigma, expanding) -------
    bands = ind.vwap_bands(day_df, k=2.0)
    vwap_s = bands["vwap"]
    sigma = bands["vwap_sigma"]
    out["vwap_dist_pct"] = (close - vwap_s) / vwap_s * 100.0
    out["vwap_z"] = (close - vwap_s) / sigma.replace(0.0, np.nan)

    # --- volume / vol / trend indicators (all causal in core) --------------
    out["rvol"] = ind.rvol(day_df["volume"], lookback=20)
    atr_s = ind.atr(day_df, period=14)
    out["atr_pct"] = atr_s / close * 100.0
    out["adx"] = ind.adx(day_df, period=14)
    ema9 = ind.ema(close, 9)
    ema21 = ind.ema(close, 21)
    out["ema_spread_pct"] = (ema9 - ema21) / close * 100.0
    out["rsi"] = ind.rsi(close, period=14)
    macd_df = ind.macd(close)
    out["macd_hist"] = macd_df["hist"] / close * 100.0

    # --- gap from prior close: constant within the session ------------------
    # prior_close is injected on day_df as an attribute by the caller (point-in-time:
    # it is the PREVIOUS session's last close, known at this session's open).
    prior_close = day_df.attrs.get("prior_close", np.nan)
    sess_open = float(day_df["open"].iloc[0])
    out["gap_pct"] = (
        (sess_open - prior_close) / prior_close * 100.0
        if prior_close == prior_close and prior_close > 0
        else np.nan
    )

    # --- opening-range position (first 15 bars define the range; causal after) ----
    orb_minutes = 15
    if n >= orb_minutes:
        orb_high = day_df["high"].iloc[:orb_minutes].max()
        orb_low = day_df["low"].iloc[:orb_minutes].min()
        rng = orb_high - orb_low
        if rng > 0:
            or_pos = (close - orb_low) / rng
        else:
            or_pos = pd.Series(np.nan, index=close.index)
        # Range only known AFTER the opening range completes -> NaN for the first
        # orb_minutes rows so we never use a not-yet-formed range (look-ahead guard).
        or_pos = or_pos.copy()
        or_pos.iloc[:orb_minutes] = np.nan
        out["or_pos"] = or_pos
    else:
        out["or_pos"] = np.nan

    # --- clock features -----------------------------------------------------
    minutes_since_open = (day_df.index - day_df.index[0]).total_seconds() / 60.0
    out["minutes_since_open"] = minutes_since_open
    out["tod_bucket"] = (minutes_since_open // TOD_BUCKET_MINUTES).astype(int)
    out["dow"] = day_df.index.dayofweek

    # --- fractional differencing of close (expanding, causal) --------------
    out["frac_diff"] = ind.frac_diff(close, d=0.5)

    # --- distance to nearest round number (depends only on current price) --
    levels = close.apply(lambda p: ind.round_number_levels(float(p), step_pct=0.5))
    below = levels.apply(lambda x: x[0])
    above = levels.apply(lambda x: x[1])
    nearest = np.where((close - below).abs() <= (above - close).abs(), below, above)
    out["dist_round_pct"] = (close.to_numpy() - nearest) / close.to_numpy() * 100.0

    # ----------------------------------------------------------------------- #
    # FORWARD LABELS (rows > t only; within-session; never features).
    # ----------------------------------------------------------------------- #
    cv = close.to_numpy()
    high = day_df["high"].to_numpy()
    low = day_df["low"].to_numpy()
    atr_pct_arr = out["atr_pct"].to_numpy()

    for h in HORIZONS:
        fwd = np.full(n, np.nan)
        if n > h:
            fwd[: n - h] = cv[h:] / cv[: n - h] - 1.0  # close_{t+h}/close_t - 1
        out[f"fwd_ret_{h}"] = fwd

    # MFE / MAE over TB_HORIZON forward bars (long-side), in ATR-% units.
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    tb = np.full(n, np.nan)
    for t in range(n):
        end = min(t + TB_HORIZON, n - 1)
        if end <= t:
            continue
        fwd_high = high[t + 1 : end + 1]
        fwd_low = low[t + 1 : end + 1]
        if fwd_high.size == 0:
            continue
        up = (np.max(fwd_high) - cv[t]) / cv[t] * 100.0   # % favorable (long)
        dn = (np.min(fwd_low) - cv[t]) / cv[t] * 100.0    # % adverse (long, negative)
        ap = atr_pct_arr[t]
        if ap == ap and ap > 0:
            mfe[t] = up / ap
            mae[t] = dn / ap
            # Triple barrier: which side is hit FIRST within the window.
            barrier = TB_VOL_MULT * ap  # percent move each side
            sign = 0.0
            fwd_close = cv[t + 1 : end + 1]
            up_path = (fwd_close - cv[t]) / cv[t] * 100.0
            hit_up = np.where(up_path >= barrier)[0]
            hit_dn = np.where(up_path <= -barrier)[0]
            iu = hit_up[0] if hit_up.size else np.inf
            idn = hit_dn[0] if hit_dn.size else np.inf
            if iu < idn:
                sign = 1.0
            elif idn < iu:
                sign = -1.0
            tb[t] = sign
    out["fwd_mfe_atr"] = mfe
    out["fwd_mae_atr"] = mae
    out["tb_sign"] = tb

    # ts_exit = entry ts shifted forward by the longest LABEL horizon used (max of the
    # forward-return horizons and the triple-barrier window), capped at the session close.
    # This is the conservative label-window end for the interval embargo.
    max_h = max(max(HORIZONS), TB_HORIZON)
    idx_pos = np.arange(n)
    exit_pos = np.minimum(idx_pos + max_h, n - 1)
    out["ts_exit"] = day_df.index.to_numpy()[exit_pos]

    return out


def _build_symbol(store: ParquetBarStore, symbol: str, start_ts: pd.Timestamp) -> Optional[pd.DataFrame]:
    """Build the sampled feature/label table for one symbol over [start_ts, end]."""
    hist = store.load_symbol_history(symbol)
    if hist is None or hist.empty:
        return None
    hist = hist[hist.index >= start_ts]
    if hist.empty:
        return None

    # Prior session close per day (point-in-time: last close of the PREVIOUS session).
    day_key = hist.index.normalize()
    last_close_by_day = hist.groupby(day_key)["close"].last()
    prior_close_by_day = last_close_by_day.shift(1)

    frames = []
    for day, day_df in hist.groupby(day_key):
        if len(day_df) < 60:  # skip thin/half sessions
            continue
        day_df = day_df.sort_index()
        day_df.attrs["prior_close"] = float(prior_close_by_day.get(day, np.nan))
        feat = _session_features(day_df)
        feat["symbol"] = symbol
        feat["ts"] = day_df.index
        feat["session_date"] = pd.Timestamp(day).normalize().tz_localize(None)
        # Sample at stride to keep the table tractable.
        feat = feat.iloc[::SAMPLE_STRIDE]
        frames.append(feat)

    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def build_research_dataset(
    cfg: Optional[AppConfig] = None,
    symbols: Optional[Sequence[str]] = None,
    recent_years: float = RECENT_YEARS,
    test_frac: float = 0.3,
    out_path: str = DEFAULT_OUT,
    log=print,
) -> pd.DataFrame:
    """Build + persist the panel research table. Returns the in-memory DataFrame too."""
    cfg = cfg or load_config()
    store = ParquetBarStore(cfg.env.parquet_dir)
    if symbols is None:
        symbols = _default_universe(store)

    # Recent window start. Use RELIANCE's last ts as the archive 'now'.
    ref = store.load_symbol_history("RELIANCE")
    end_ts = ref.index.max()
    start_ts = end_ts - pd.Timedelta(days=int(recent_years * 365.25))
    log(f"[dataset] universe={len(symbols)} window={start_ts.date()}..{end_ts.date()} stride={SAMPLE_STRIDE}")

    t0 = time.time()
    frames = []
    for i, s in enumerate(symbols):
        df = _build_symbol(store, s, start_ts)
        if df is not None:
            frames.append(df)
        if i % 10 == 0:
            log(f"[dataset]   {i}/{len(symbols)} {s} ({time.time()-t0:.0f}s)")
    big = pd.concat(frames, ignore_index=True)

    # Drop rows with no usable features (early-session NaNs from warmups) — keep a row only
    # if its core causal features are present. Labels may be NaN near the close (truncated).
    core = ["vwap_dist_pct", "atr_pct", "adx", "ema_spread_pct", "rsi", "frac_diff"]
    big = big.dropna(subset=core).reset_index(drop=True)

    # IS/OOS split: SAME global calendar-date split + interval embargo as ml.train.
    ts = big["ts"].to_numpy()
    ts_exit = big["ts_exit"].to_numpy()
    train_idx, test_idx, cutoff = date_split_indices(
        ts, ts_exit, test_frac=test_frac, embargo_days=DEFAULT_EMBARGO_DAYS)
    is_oos = np.zeros(len(big), dtype=bool)
    is_oos[test_idx] = True
    in_train = np.zeros(len(big), dtype=bool)
    in_train[train_idx] = True
    big["is_oos"] = is_oos
    big["is_purged"] = ~(is_oos | in_train)  # straddles cutoff -> neither train nor test
    big.attrs["cutoff"] = str(cutoff)

    # Persist.
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    big.to_parquet(out, index=False)
    log(f"[dataset] wrote {out} rows={len(big)} ({time.time()-t0:.0f}s)")
    return big


def _default_universe(store: ParquetBarStore, top_n: int = 50, ref_year: int = 2025,
                      min_days: int = 200) -> List[str]:
    """Most-liquid archived names by mean daily turnover in ``ref_year``, requiring near-
    continuous listing (>= ``min_days`` sessions) so the recent window is dense.
    """
    root = Path(store.root)
    rows = []
    for s in store.list_symbols():
        f = root / f"symbol={s}" / f"year={ref_year}" / "bars.parquet"
        if not f.exists():
            continue
        try:
            df = pd.read_parquet(f, columns=["close", "volume"])
        except Exception:
            continue
        if df.empty:
            continue
        nd = df.index.normalize().nunique()
        if nd < min_days:
            continue
        rows.append((s, float((df["close"] * df["volume"]).sum()) / nd))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[:top_n]]


if __name__ == "__main__":

    cfg = load_config()
    store = ParquetBarStore(cfg.env.parquet_dir)
    syms = _default_universe(store)
    df = build_research_dataset(cfg, symbols=syms)

    print("\n================ DATASET SUMMARY ================")
    print("path        :", DEFAULT_OUT)
    print("shape       :", df.shape)
    print("date range  :", df["session_date"].min().date(), "->", df["session_date"].max().date())
    print("#symbols    :", df["symbol"].nunique())
    print("cutoff      :", df.attrs.get("cutoff"))
    print("IS rows     :", int((~df["is_oos"] & ~df["is_purged"]).sum()))
    print("OOS rows    :", int(df["is_oos"].sum()))
    print("purged rows :", int(df["is_purged"].sum()))
    print("feature cols:", FEATURE_COLUMNS)
    print("label cols  :", LABEL_COLUMNS)
    print("meta cols   :", META_COLUMNS)
