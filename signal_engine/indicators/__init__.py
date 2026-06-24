"""Indicator package: pure functions + the ``compute_features`` aggregator."""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from signal_engine.indicators.core import (
    _frac_diff_weights,
    adx,
    atr,
    ema,
    frac_diff,
    macd,
    opening_range,
    round_number_levels,
    rsi,
    rvol,
    supertrend,
    vwap,
    vwap_bands,
)

__all__ = [
    "ema",
    "rsi",
    "atr",
    "adx",
    "vwap",
    "vwap_bands",
    "rvol",
    "macd",
    "supertrend",
    "opening_range",
    "round_number_levels",
    "frac_diff",
    "compute_features",
    "bar_shape",
    "short_window_return_pct",
    "realized_vol_pct",
    "regime_trend",
    "frac_diff_close_pct",
]

# Tunables for the Task 1B short-window / regime features (kept in one place so the
# live path and the ML dataset builder use identical windows).
_RET_WINDOW = 5      # bars for short-window return + realized vol
_REGIME_WINDOW = 20  # bars for the OLS slope behind the regime scalar
_FRAC_DIFF_D = 0.5   # fractional-difference order for the frac_diff feature

_NAN = float("nan")

_FEATURE_KEYS = [
    "close",
    "prev_close",
    "vwap",
    "ema_fast",
    "ema_slow",
    "ema_fast_prev",
    "ema_slow_prev",
    "rsi",
    "adx",
    "atr",
    "atr_pct",
    "rvol",
    "orb_high",
    "orb_low",
    "bar_count",
    # Task 1B — richer stationary features (see ml/base.py for ML-column derivations).
    "bar_range_pct",
    "body_pct",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "ret_5_pct",
    "rv_5_pct",
    "regime_trend",
    "frac_diff_close_pct",
    # A3 — point-in-time structure levels for de-clustered, structure-aware targets.
    "vwap_sigma",
    "vwap_upper",
    "vwap_lower",
    "round_below",
    "round_above",
]

# Default VWAP-band width (k * sigma) and round-number grid step (% of price) used
# when compute_features is called without overriding params. The RiskManager reads
# its own cfg values; these only shape the *features dict* the live aggregator emits.
_VWAP_BAND_MULT = 2.0
_ROUND_STEP_PCT = 0.5


def _last(series: pd.Series) -> float:
    """Latest scalar value of a series as a float (nan if empty/missing)."""
    if series is None or len(series) == 0:
        return _NAN
    val = series.iloc[-1]
    return float(val) if pd.notna(val) else _NAN


def _prev(series: pd.Series) -> float:
    """Value one bar before the last as a float (nan if unavailable)."""
    if series is None or len(series) < 2:
        return _NAN
    val = series.iloc[-2]
    return float(val) if pd.notna(val) else _NAN


# --- Task 1B shared feature math --------------------------------------------------
# These pure helpers operate on plain floats / 1-D numpy arrays so the live aggregator
# (compute_features) and the vectorized ML builder (ml/dataset._raw_at) compute the
# IDENTICAL value for a given bar. All are point-in-time: they look only at the bar
# itself or a trailing window ending at that bar.


def bar_shape(o: float, h: float, lo: float, c: float) -> Dict[str, float]:
    """Single-bar microstructure ratios (range/body as % of close, wick ratios 0..1).

    Returns nan for every key if close is non-positive/NaN. Wick ratios are 0.0 when
    the bar has zero range (a doji with high==low) — there is no wick to measure.
    """
    if not (c == c) or c == 0.0:
        return {k: _NAN for k in
                ("bar_range_pct", "body_pct", "upper_wick_ratio", "lower_wick_ratio")}
    rng = h - lo
    if rng <= 0.0:
        upper = lower = 0.0
    else:
        upper = (h - max(o, c)) / rng
        lower = (min(o, c) - lo) / rng
    return {
        "bar_range_pct": rng / c * 100.0,
        "body_pct": (c - o) / c * 100.0,
        "upper_wick_ratio": upper,
        "lower_wick_ratio": lower,
    }


def short_window_return_pct(close: np.ndarray, window: int = _RET_WINDOW) -> float:
    """``window``-bar simple return ending at the last element, in percent."""
    if close is None or len(close) <= window:
        return _NAN
    past = close[-window - 1]
    last = close[-1]
    if not (past == past) or past == 0.0 or not (last == last):
        return _NAN
    return (last / past - 1.0) * 100.0


def realized_vol_pct(close: np.ndarray, window: int = _RET_WINDOW) -> float:
    """Realized volatility = stdev of the last ``window`` one-bar simple returns (%).

    Uses a population stdev (ddof=0) so a single constant window is exactly 0.0.
    """
    if close is None or len(close) <= window:
        return _NAN
    seg = np.asarray(close[-window - 1:], dtype=float)
    if not np.all(np.isfinite(seg)) or np.any(seg[:-1] == 0.0):
        return _NAN
    rets = seg[1:] / seg[:-1] - 1.0
    return float(np.std(rets, ddof=0)) * 100.0


def regime_trend(close: np.ndarray, adx_val: float,
                 window: int = _REGIME_WINDOW) -> float:
    """ADX-scaled signed trend strength in [-1, 1] (stationary; ~0 == chop).

    Fits an OLS line to the last ``window`` closes, normalizes its slope to a per-bar
    fraction of price (slope / mean-close), squashes that with ``tanh`` to bound the
    sign+steepness, then scales by ``min(adx, 50)/50`` so strength only counts when ADX
    confirms directionality. |value| -> 1 is a strong clean trend, value ~ 0 is chop.
    """
    if close is None or len(close) < window or not (adx_val == adx_val):
        return _NAN
    seg = np.asarray(close[-window:], dtype=float)
    if not np.all(np.isfinite(seg)):
        return _NAN
    x = np.arange(window, dtype=float)
    # OLS slope of close vs bar index.
    x_mean = x.mean()
    denom = float(((x - x_mean) ** 2).sum())
    if denom == 0.0:
        return _NAN
    slope = float(((x - x_mean) * (seg - seg.mean())).sum() / denom)
    mean_c = float(seg.mean())
    if mean_c == 0.0:
        return _NAN
    slope_norm = slope / mean_c          # per-bar slope as a fraction of price
    adx_scale = min(max(adx_val, 0.0), 50.0) / 50.0
    return float(np.tanh(slope_norm * 100.0) * adx_scale)


def frac_diff_close_pct(close_series: pd.Series, d: float = _FRAC_DIFF_D) -> float:
    """Latest fractionally-differenced close, scaled by price (percent of close).

    The López de Prado expanding-window frac_diff yields a near-stationary,
    memory-preserving transform of close; dividing by the current close makes it
    comparable across symbols at different price levels.

    Only the LAST value is needed here (the live aggregator runs once per closed
    bar), so this computes the single dot product ``sum_k w[k] * x[-1-k]`` directly
    in O(n) rather than materializing the whole frac_diff series in O(n^2) — the
    result is identical to ``frac_diff(close_series, d).iloc[-1]``.
    """
    if close_series is None or len(close_series) == 0:
        return _NAN
    x = close_series.to_numpy(dtype=float)
    n = len(x)
    weights = _frac_diff_weights(d, n)
    fd_last = float(np.dot(weights, x[::-1]))  # newest-first weights . newest-first window
    last_c = float(x[-1])
    if not (fd_last == fd_last) or not (last_c == last_c) or last_c == 0.0:
        return _NAN
    return fd_last / last_c * 100.0


def compute_features(
    bars: pd.DataFrame,
    params: Dict,
    session_open: Optional[object] = None,
) -> Dict[str, float]:
    """Compute the latest scalar feature values from closed OHLCV bars.

    Parameters
    ----------
    bars : DataFrame with columns open/high/low/close/volume, tz-aware
        DatetimeIndex, ascending, CLOSED bars only.
    params : dict of indicator parameters (defaults filled in if missing).
    session_open : currently unused; accepted for interface compatibility.

    Returns a dict with all keys in ``_FEATURE_KEYS`` as Python floats.
    Insufficient history yields ``nan`` for that feature (never raises).
    """
    params = params or {}
    ema_fast_p = int(params.get("ema_fast", 9))
    ema_slow_p = int(params.get("ema_slow", 21))
    rsi_p = int(params.get("rsi_period", 14))
    adx_p = int(params.get("adx_period", 14))
    atr_p = int(params.get("atr_period", 14))
    rvol_lb = int(params.get("rvol_lookback", 20))
    orb_min = int(params.get("opening_range_minutes", 15))
    vwap_band_mult = float(params.get("vwap_band_mult", _VWAP_BAND_MULT))
    round_step_pct = float(params.get("round_number_step_pct", _ROUND_STEP_PCT))

    out = {key: _NAN for key in _FEATURE_KEYS}
    n = 0 if bars is None else len(bars)
    out["bar_count"] = float(n)

    if n == 0:
        return out

    close = bars["close"]
    out["close"] = _last(close)
    out["prev_close"] = _prev(close)

    # VWAP — cumulative, defined from the first bar.
    try:
        out["vwap"] = _last(vwap(bars))
    except Exception:
        out["vwap"] = _NAN

    # EMAs — defined from first bar (adjust=False), but only meaningful with
    # enough history; we still surface them and let *_prev be nan if <2 bars.
    ema_fast_s = ema(close, ema_fast_p)
    ema_slow_s = ema(close, ema_slow_p)
    out["ema_fast"] = _last(ema_fast_s)
    out["ema_slow"] = _last(ema_slow_s)
    out["ema_fast_prev"] = _prev(ema_fast_s)
    out["ema_slow_prev"] = _prev(ema_slow_s)

    # RSI needs at least period+1 bars to be meaningful.
    if n >= rsi_p + 1:
        out["rsi"] = _last(rsi(close, rsi_p))

    # ADX needs roughly 2*period bars to warm up.
    if n >= 2 * adx_p:
        out["adx"] = _last(adx(bars, adx_p))

    # ATR needs at least period+1 bars.
    if n >= atr_p + 1:
        atr_val = _last(atr(bars, atr_p))
        out["atr"] = atr_val
        close_val = out["close"]
        if atr_val == atr_val and close_val == close_val and close_val != 0:
            out["atr_pct"] = atr_val / close_val * 100.0

    # rvol needs lookback prior bars plus the current bar.
    if n >= rvol_lb + 1:
        out["rvol"] = _last(rvol(bars["volume"], rvol_lb))

    # Opening range.
    orb_high, orb_low = opening_range(bars, orb_min)
    out["orb_high"] = float(orb_high)
    out["orb_low"] = float(orb_low)

    # --- A3 point-in-time structure levels (causal; only bars 0..now) ---
    # VWAP +/- k*sigma bands (running volume-weighted dispersion about VWAP).
    try:
        vb = vwap_bands(bars, vwap_band_mult)
        out["vwap_sigma"] = _last(vb["vwap_sigma"])
        out["vwap_upper"] = _last(vb["vwap_upper"])
        out["vwap_lower"] = _last(vb["vwap_lower"])
    except Exception:
        out["vwap_sigma"] = out["vwap_upper"] = out["vwap_lower"] = _NAN
    # Nearest round-number levels bracketing the latest close (price-scaled grid).
    r_below, r_above = round_number_levels(out["close"], round_step_pct)
    out["round_below"] = float(r_below)
    out["round_above"] = float(r_above)

    # --- Task 1B richer stationary features (NaN-safe on short history) ---
    # Microstructure: current-bar shape (always available once n >= 1).
    out.update(
        bar_shape(
            float(bars["open"].iloc[-1]),
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            out["close"],
        )
    )
    close_np = close.to_numpy(dtype=float)
    # Short-window momentum + realized vol (need window+1 bars).
    out["ret_5_pct"] = short_window_return_pct(close_np, _RET_WINDOW)
    out["rv_5_pct"] = realized_vol_pct(close_np, _RET_WINDOW)
    # Regime scalar (needs the ADX warm-up already gated above + window bars).
    out["regime_trend"] = regime_trend(close_np, out["adx"], _REGIME_WINDOW)
    # Fractional-difference of close, price-scaled.
    out["frac_diff_close_pct"] = frac_diff_close_pct(close, _FRAC_DIFF_D)

    return out
