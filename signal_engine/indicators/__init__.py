"""Indicator package: pure functions + the ``compute_features`` aggregator."""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from signal_engine.indicators.core import (
    adx,
    atr,
    ema,
    macd,
    opening_range,
    rsi,
    rvol,
    supertrend,
    vwap,
)

__all__ = [
    "ema",
    "rsi",
    "atr",
    "adx",
    "vwap",
    "rvol",
    "macd",
    "supertrend",
    "opening_range",
    "compute_features",
]

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
]


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

    return out
