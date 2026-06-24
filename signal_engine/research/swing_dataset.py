"""Vectorized DAILY / SWING research dataset builder (additive; panel-only).

Companion to :mod:`signal_engine.research.dataset` (which is INTRADAY, one row per sampled
1-min bar). This module resamples the same 1-min parquet archive to DAILY OHLCV over a broad
liquid universe and builds ONE row per (symbol, trading-day) carrying strictly-causal
point-in-time FEATURES and forward LABELS suitable for multi-hour-to-multi-day SWING study.
The intraday module is left UNTOUCHED.

CARDINAL RULE — POINT-IN-TIME / NO LOOK-AHEAD
---------------------------------------------
* A FEATURE at day ``d`` uses ONLY daily bars ``<= d`` (its own close inclusive). Trailing
  returns, MAs, vol, RSI/ADX, 52w-high proximity and the overnight/intraday decomposition are
  all built from rolling/expanding/Wilder primitives that end at ``d``. The cross-sectional
  beta-proxy demeans against the universe's SAME-DAY equal-weight return, which is also known
  at ``d``'s close (it uses no future days).
* A LABEL uses forward days ``> d`` only and is NEVER fed back as a feature. Forward returns,
  MFE/MAE and the overnight-only forward return use closes/highs/lows strictly after ``d``.
  A horizon that runs past the last available day is NaN (truncated, never wrapped).
* The IS/OOS split reuses the SAME global calendar-date split + interval embargo as
  :func:`signal_engine.ml.train.date_split_indices`, with the embargo set to >= the max label
  horizon in CALENDAR days so no label window straddles the cutoff.

UNIVERSE & SURVIVORSHIP
-----------------------
The archive universe is whatever symbols are present TODAY, so it is SURVIVORSHIP-BIASED:
names that delisted/merged over the 5y window are absent, which inflates long-only momentum /
mean-reversion results (the worst losers that left the index are missing). This is flagged in
the report and must be remembered when reading any verdict. There is also NO index series in
the archive (``list_symbols`` shows no NIFTY/SENSEX), so "beta" is a documented universe-demean
PROXY, not a true index beta.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from signal_engine.config import AppConfig, load_config
from signal_engine.indicators import core as ind
from signal_engine.ml.train import date_split_indices
from signal_engine.storage.bars import ParquetBarStore

# --------------------------------------------------------------------------- #
# Column groups (exported so probes / scan agents never hard-code names).
# --------------------------------------------------------------------------- #
FEATURE_COLUMNS: List[str] = [
    # trailing total returns (close_d / close_{d-k} - 1), causal
    "ret_1w",            # ~5 trading days
    "ret_1m",            # ~21 trading days
    "ret_3m",            # ~63
    "ret_6m",            # ~126
    "ret_12m",           # ~252
    # realized vol of daily returns over trailing 20d (annualized)
    "rvol_20d",
    # distance from moving averages, % of price
    "dist_ma20_pct",
    "dist_ma50_pct",
    "dist_ma200_pct",
    # 52-week-high proximity: close / trailing-252d-high - 1 (<=0; 0 == at new high)
    "high_52w_prox_pct",
    # daily oscillators / trend
    "rsi_14",
    "adx_14",
    # overnight vs intraday decomposition, trailing 20d averages
    "overnight_ret_20d",   # mean (open_d / close_{d-1} - 1) over trailing 20d
    "intraday_ret_20d",    # mean (close_d / open_d - 1) over trailing 20d
    "overnight_ret_1d",    # most recent single-day overnight gap (open_d/close_{d-1}-1)
    "intraday_ret_1d",     # most recent single-day intraday (close_d/open_d-1)
    # liquidity / turnover
    "turnover_ratio",      # today's turnover / trailing-20d mean turnover (RVOL of value)
    "log_turnover_20d",    # log10 of trailing-20d mean rupee turnover (size proxy)
    # cross-sectional beta proxy vs equal-weight universe (documented proxy, not index)
    "beta_proxy_60d",      # OLS beta of daily ret on universe equal-weight ret, trailing 60d
    "rel_strength_20d",    # symbol 20d ret minus universe 20d ret (cross-sectional momentum)
    # calendar
    "dow",                 # day of week 0=Mon
    "dom",                 # day of month 1..31
    "turn_of_month",       # 1 if within last 3 / first 3 trading days of the month, else 0
]

LABEL_COLUMNS: List[str] = [
    "fwd_ret_1",         # forward total return over next 1 trading day (close-to-close)
    "fwd_ret_2",
    "fwd_ret_5",
    "fwd_ret_10",
    "fwd_ret_20",
    "fwd_mfe_20",        # max favorable excursion over next 20d, % (long-side)
    "fwd_mae_20",        # max adverse excursion over next 20d, % (long-side, negative)
    "fwd_overnight_1",   # overnight-only forward return: open_{d+1}/close_d - 1
]

META_COLUMNS: List[str] = ["symbol", "ts", "ts_exit", "session_date", "is_oos", "is_purged"]

# --- horizons / windows -------------------------------------------------------------------- #
FWD_HORIZONS = (1, 2, 5, 10, 20)        # forward-return label horizons, in TRADING DAYS
MFE_MAE_HORIZON = 20                    # MFE/MAE window, trading days
MAX_LABEL_DAYS = max(max(FWD_HORIZONS), MFE_MAE_HORIZON)  # 20 trading days
# Embargo in CALENDAR days must cover the max trading-day label horizon: 20 trading days span
# ~28 calendar days; pad to 35 to be safe against holidays/long weekends.
EMBARGO_CALENDAR_DAYS = 35

VOL_WIN = 20
RSI_WIN = 14
ADX_WIN = 14
HIGH_52W_WIN = 252
BETA_WIN = 60
TURNOVER_WIN = 20
TRADING_DAYS_PER_YEAR = 252

DEFAULT_OUT = "data/research/swing_dataset.parquet"


# --------------------------------------------------------------------------- #
# 1-min -> DAILY resample.
# --------------------------------------------------------------------------- #
def resample_daily(hist: pd.DataFrame) -> pd.DataFrame:
    """Resample a symbol's 1-min OHLCV history to one row per TRADING DAY.

    open = first bar's open, high = max high, low = min low, close = last bar's close,
    volume = sum, turnover = sum(close*volume) approximated by sum(typical*volume). Days with
    no bars are dropped (no synthetic calendar fill). Index is the normalized session date.
    """
    if hist is None or hist.empty:
        return pd.DataFrame()
    day_key = hist.index.normalize()
    g = hist.groupby(day_key)
    daily = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "volume": g["volume"].sum(),
    })
    # rupee turnover ~ sum over the day of close*volume per minute (better than close*vol_day).
    turnover = (hist["close"] * hist["volume"]).groupby(day_key).sum()
    daily["turnover"] = turnover
    daily = daily.dropna(subset=["close"])
    daily.index = pd.DatetimeIndex(daily.index).tz_localize(None)
    return daily.sort_index()


# --------------------------------------------------------------------------- #
# Per-symbol causal daily features + forward labels.
# --------------------------------------------------------------------------- #
def _symbol_features(daily: pd.DataFrame) -> pd.DataFrame:
    """Causal daily features + forward labels for ONE symbol. All features at day d use only
    rows <= d; all labels use only rows > d. Cross-sectional terms (beta/rel-strength) are
    injected by the caller as the universe equal-weight daily return aligned on session date.
    """
    close = daily["close"]
    open_ = daily["open"]
    out = pd.DataFrame(index=daily.index)

    # --- daily simple return (close-to-close), the building block ----------
    daily_ret = close.pct_change(1)

    # --- trailing total returns over k trading days ------------------------
    out["ret_1w"] = close.pct_change(5)
    out["ret_1m"] = close.pct_change(21)
    out["ret_3m"] = close.pct_change(63)
    out["ret_6m"] = close.pct_change(126)
    out["ret_12m"] = close.pct_change(252)

    # --- realized vol (annualized) over trailing 20d -----------------------
    out["rvol_20d"] = daily_ret.rolling(VOL_WIN).std() * np.sqrt(TRADING_DAYS_PER_YEAR)

    # --- distance from MAs (% of price); MAs end at d (causal) -------------
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    out["dist_ma20_pct"] = (close / ma20 - 1.0) * 100.0
    out["dist_ma50_pct"] = (close / ma50 - 1.0) * 100.0
    out["dist_ma200_pct"] = (close / ma200 - 1.0) * 100.0

    # --- 52-week-high proximity: <=0, 0 == at/above a trailing-year high ---
    roll_high = close.rolling(HIGH_52W_WIN, min_periods=60).max()
    out["high_52w_prox_pct"] = (close / roll_high - 1.0) * 100.0

    # --- daily RSI / ADX (Wilder, causal) ----------------------------------
    out["rsi_14"] = ind.rsi(close, period=RSI_WIN)
    out["adx_14"] = ind.adx(daily, period=ADX_WIN)

    # --- overnight vs intraday decomposition -------------------------------
    prior_close = close.shift(1)
    overnight_ret = open_ / prior_close - 1.0     # gap: prior close -> today open
    intraday_ret = close / open_ - 1.0            # today open -> today close
    out["overnight_ret_1d"] = overnight_ret
    out["intraday_ret_1d"] = intraday_ret
    out["overnight_ret_20d"] = overnight_ret.rolling(VOL_WIN).mean()
    out["intraday_ret_20d"] = intraday_ret.rolling(VOL_WIN).mean()

    # --- liquidity / turnover ----------------------------------------------
    turnover = daily["turnover"]
    mean_turn_20d = turnover.shift(1).rolling(TURNOVER_WIN).mean()  # PRIOR 20d (exclude today)
    out["turnover_ratio"] = turnover / mean_turn_20d
    out["log_turnover_20d"] = np.log10(turnover.rolling(TURNOVER_WIN).mean().replace(0, np.nan))

    # cross-sectional placeholders (filled by caller with universe series); keep daily_ret for it
    out["_daily_ret"] = daily_ret

    # --- calendar ----------------------------------------------------------
    idx = daily.index
    out["dow"] = idx.dayofweek
    out["dom"] = idx.day
    # turn-of-month: within last 3 or first 3 TRADING days of the calendar month.
    month = idx.to_period("M")
    df_cal = pd.DataFrame({"month": month, "pos": np.arange(len(idx))}, index=idx)
    first_rank = df_cal.groupby("month")["pos"].rank(method="first")  # 1..n within month
    n_in_month = df_cal.groupby("month")["pos"].transform("count")
    last_rank = n_in_month - first_rank + 1
    out["turn_of_month"] = ((first_rank <= 3) | (last_rank <= 3)).astype(int).to_numpy()

    # ----------------------------------------------------------------------- #
    # FORWARD LABELS (rows > d only; never features).
    # ----------------------------------------------------------------------- #
    cv = close.to_numpy()
    ov = open_.to_numpy()
    hi = daily["high"].to_numpy()
    lo = daily["low"].to_numpy()
    n = len(daily)

    for h in FWD_HORIZONS:
        fwd = np.full(n, np.nan)
        if n > h:
            fwd[: n - h] = cv[h:] / cv[: n - h] - 1.0
        out[f"fwd_ret_{h}"] = fwd

    # overnight-only forward return: open_{d+1} / close_d - 1
    fwd_on = np.full(n, np.nan)
    if n > 1:
        fwd_on[: n - 1] = ov[1:] / cv[: n - 1] - 1.0
    out["fwd_overnight_1"] = fwd_on

    # MFE / MAE over next MFE_MAE_HORIZON days (long-side, % of entry close).
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    for t in range(n - 1):
        end = min(t + MFE_MAE_HORIZON, n - 1)
        fh = hi[t + 1 : end + 1]
        fl = lo[t + 1 : end + 1]
        if fh.size == 0:
            continue
        mfe[t] = (np.max(fh) - cv[t]) / cv[t] * 100.0
        mae[t] = (np.min(fl) - cv[t]) / cv[t] * 100.0
    out["fwd_mfe_20"] = mfe
    out["fwd_mae_20"] = mae

    return out


# --------------------------------------------------------------------------- #
# Universe-level build.
# --------------------------------------------------------------------------- #
def _liquid_universe(store: ParquetBarStore, top_n: int, ref_year: int,
                     min_days: int, log=print) -> List[str]:
    """Most-liquid archived names by mean daily rupee turnover in ``ref_year`` with near-
    continuous listing (>= ``min_days`` sessions). Broad by design (state the count)."""
    root = Path(store.root)
    rows = []
    syms = store.list_symbols()
    for i, s in enumerate(syms):
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
        if i % 400 == 0:
            log(f"[universe]   scanned {i}/{len(syms)}")
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[:top_n]]


def build_swing_dataset(
    cfg: Optional[AppConfig] = None,
    symbols: Optional[Sequence[str]] = None,
    top_n: int = 250,
    ref_year: int = 2025,
    min_days: int = 200,
    test_frac: float = 0.3,
    out_path: str = DEFAULT_OUT,
    log=print,
) -> pd.DataFrame:
    """Build + persist the DAILY/swing research table. Returns the in-memory DataFrame too."""
    cfg = cfg or load_config()
    store = ParquetBarStore(cfg.env.parquet_dir)
    if symbols is None:
        symbols = _liquid_universe(store, top_n=top_n, ref_year=ref_year,
                                   min_days=min_days, log=log)
    log(f"[swing] universe={len(symbols)} names (top {top_n} by {ref_year} turnover, "
        f">= {min_days} sessions)")

    t0 = time.time()

    # Pass 1: resample each symbol to daily and stash; build the universe equal-weight daily
    # return series (cross-sectional, point-in-time: day d uses only day-d returns).
    daily_by_sym = {}
    for i, s in enumerate(symbols):
        hist = store.load_symbol_history(s)
        d = resample_daily(hist)
        if d.empty or len(d) < 260:  # need ~1y for the slow features to warm up
            continue
        daily_by_sym[s] = d
        if i % 25 == 0:
            log(f"[swing]   resampled {i}/{len(symbols)} ({time.time()-t0:.0f}s)")
    log(f"[swing] resampled {len(daily_by_sym)} usable symbols ({time.time()-t0:.0f}s)")

    # Universe equal-weight daily return per session date (cross-sectional mean of daily rets).
    ret_frames = []
    for s, d in daily_by_sym.items():
        r = d["close"].pct_change(1)
        r.name = s
        ret_frames.append(r)
    ret_panel = pd.concat(ret_frames, axis=1)           # index=date, cols=symbols
    universe_ret = ret_panel.mean(axis=1)               # equal-weight, same-day -> causal

    # Pass 2: build features/labels per symbol, attach cross-sectional beta + rel-strength.
    frames = []
    for i, (s, d) in enumerate(daily_by_sym.items()):
        feat = _symbol_features(d)
        feat["symbol"] = s
        feat["ts"] = d.index
        feat["session_date"] = pd.DatetimeIndex(d.index).tz_localize(None)

        # cross-sectional beta proxy: OLS beta of symbol daily ret on universe ret over 60d.
        sym_ret = feat["_daily_ret"]
        uni = universe_ret.reindex(d.index)
        cov = sym_ret.rolling(BETA_WIN).cov(uni)
        var = uni.rolling(BETA_WIN).var()
        feat["beta_proxy_60d"] = cov / var.replace(0.0, np.nan)
        # relative strength: symbol 20d ret minus universe 20d ret (cross-sectional momentum).
        sym_20d = d["close"].pct_change(20)
        uni_cum = (uni.add(1.0)).rolling(20).apply(np.prod, raw=True) - 1.0
        feat["rel_strength_20d"] = (sym_20d - uni_cum) * 100.0

        feat = feat.drop(columns=["_daily_ret"])
        frames.append(feat)
        if i % 25 == 0:
            log(f"[swing]   featurized {i}/{len(daily_by_sym)} ({time.time()-t0:.0f}s)")

    big = pd.concat(frames, ignore_index=True)

    # Drop rows missing the core slow features (warm-up). Labels may be NaN near the end.
    core = ["ret_1m", "rvol_20d", "dist_ma50_pct", "rsi_14", "adx_14"]
    big = big.dropna(subset=core).reset_index(drop=True)

    # ts_exit = label-window end = entry day + MAX_LABEL_DAYS trading days, capped per symbol.
    # Computed within each symbol so we never reach across symbols.
    big = big.sort_values(["symbol", "ts"]).reset_index(drop=True)
    exit_ts = np.empty(len(big), dtype="datetime64[ns]")
    for _s, g in big.groupby("symbol"):
        idx = g.index.to_numpy()
        ts_arr = g["ts"].to_numpy().astype("datetime64[ns]")
        pos = np.arange(len(ts_arr))
        exit_pos = np.minimum(pos + MAX_LABEL_DAYS, len(ts_arr) - 1)
        exit_ts[idx] = ts_arr[exit_pos]
    big["ts_exit"] = exit_ts

    # IS/OOS split: SAME global calendar-date split + interval embargo as ml.train.
    train_idx, test_idx, cutoff = date_split_indices(
        big["ts"].to_numpy(), big["ts_exit"].to_numpy(),
        test_frac=test_frac, embargo_days=EMBARGO_CALENDAR_DAYS)
    is_oos = np.zeros(len(big), dtype=bool)
    is_oos[test_idx] = True
    in_train = np.zeros(len(big), dtype=bool)
    in_train[train_idx] = True
    big["is_oos"] = is_oos
    big["is_purged"] = ~(is_oos | in_train)
    big.attrs["cutoff"] = str(cutoff)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    big.to_parquet(out, index=False)
    log(f"[swing] wrote {out} rows={len(big)} symbols={big['symbol'].nunique()} "
        f"({time.time()-t0:.0f}s)")
    return big


if __name__ == "__main__":
    cfg = load_config()
    df = build_swing_dataset(cfg)

    print("\n================ SWING DATASET SUMMARY ================")
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
