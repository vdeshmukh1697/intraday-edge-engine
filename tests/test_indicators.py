"""Deterministic, hand-verified tests for the indicators module."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from signal_engine.indicators import compute_features
from signal_engine.indicators.core import (
    atr,
    ema,
    opening_range,
    rsi,
    rvol,
    vwap,
)


def _index(n: int) -> pd.DatetimeIndex:
    """tz-aware IST 1-minute index of length n."""
    return pd.date_range("2024-01-02 09:15", periods=n, freq="1min", tz="Asia/Kolkata")


def _ohlcv(closes, volumes=None):
    """Build a DataFrame where open=high=low=close unless overridden."""
    n = len(closes)
    closes = list(closes)
    if volumes is None:
        volumes = [1000] * n
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": volumes,
        },
        index=_index(n),
    )


def test_ema_of_constant_is_constant():
    s = pd.Series([5.0] * 30, index=_index(30))
    result = ema(s, period=9)
    # EMA of a constant series must equal that constant everywhere.
    assert result.iloc[-1] == pytest.approx(5.0)
    assert np.allclose(result.to_numpy(), 5.0)


def test_rsi_increasing_approaches_100():
    s = pd.Series(np.arange(1.0, 60.0), index=_index(59))
    result = rsi(s, period=14)
    # Strictly increasing -> avg_loss = 0 -> RSI = 100.
    assert result.iloc[-1] > 99.0


def test_vwap_constant_price():
    # All OHLC identical across bars -> typical price == that price ->
    # VWAP == that price regardless of volume.
    df = _ohlcv([42.0] * 10, volumes=[100, 200, 50, 300, 10, 1, 999, 7, 5, 88])
    result = vwap(df)
    assert result.iloc[-1] == pytest.approx(42.0)
    assert np.allclose(result.to_numpy(), 42.0)


def test_atr_hand_computed():
    # Hand-built 4-bar example, period=2 (alpha = 1/2 = 0.5).
    # bars (high, low, close):
    #   b0: H=10, L=8,  C=9
    #   b1: H=12, L=9,  C=11
    #   b2: H=11, L=10, C=10
    #   b3: H=13, L=11, C=12
    #
    # True ranges:
    #   TR0 = H0-L0 = 10-8 = 2          (first bar, no prev close)
    #   TR1 = max(12-9, |12-9|, |9-9|)  = max(3, 3, 0) = 3
    #   TR2 = max(11-10,|11-11|,|10-11|)= max(1, 0, 1) = 1
    #   TR3 = max(13-11,|13-10|,|11-10|)= max(2, 3, 1) = 3
    #
    # Wilder ewm(alpha=0.5, adjust=False): s[i] = s[i-1] + 0.5*(TR[i]-s[i-1])
    #   s0 = 2
    #   s1 = 2   + 0.5*(3-2)   = 2.5
    #   s2 = 2.5 + 0.5*(1-2.5) = 1.75
    #   s3 = 1.75+ 0.5*(3-1.75)= 2.375
    df = pd.DataFrame(
        {
            "high": [10.0, 12.0, 11.0, 13.0],
            "low": [8.0, 9.0, 10.0, 11.0],
            "close": [9.0, 11.0, 10.0, 12.0],
        },
        index=_index(4),
    )
    result = atr(df, period=2)
    assert result.iloc[0] == pytest.approx(2.0)
    assert result.iloc[1] == pytest.approx(2.5)
    assert result.iloc[2] == pytest.approx(1.75)
    assert result.iloc[3] == pytest.approx(2.375)


def test_rvol_current_double_of_prior_mean():
    lookback = 5
    # Prior 5 bars all volume=100 -> prior mean = 100.
    # Current bar volume = 200 -> rvol = 2.0.
    volumes = [100] * lookback + [200]
    s = pd.Series(volumes, index=_index(len(volumes)), dtype=float)
    result = rvol(s, lookback=lookback)
    assert result.iloc[-1] == pytest.approx(2.0)
    # Bars before the window is full must be NaN.
    assert math.isnan(result.iloc[0])


def test_opening_range_first_n_bars():
    # 20 bars; ORB over first 5. Craft highs/lows so the extremes live in
    # the opening window and later bars exceed them (must be ignored).
    highs = [10, 12, 11, 15, 13] + [100] * 15  # max of first 5 = 15
    lows = [9, 5, 8, 7, 6] + [-100] * 15        # min of first 5 = 5
    closes = [9] * 20
    df = pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1] * 20,
        },
        index=_index(20),
    )
    orb_high, orb_low = opening_range(df, minutes=5)
    assert orb_high == pytest.approx(15.0)
    assert orb_low == pytest.approx(5.0)


def test_opening_range_insufficient_rows():
    df = _ohlcv([1.0, 2.0, 3.0])
    orb_high, orb_low = opening_range(df, minutes=15)
    assert math.isnan(orb_high)
    assert math.isnan(orb_low)


REQUIRED_KEYS = {
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
}


def test_compute_features_keys_and_short_frame():
    closes = [100.0, 101.5]
    df = _ohlcv(closes, volumes=[1000, 1200])
    feats = compute_features(df, params={})

    # All required keys present, nothing extra.
    assert set(feats.keys()) == REQUIRED_KEYS

    # close / prev_close / bar_count are correct on a 2-row frame.
    assert feats["close"] == pytest.approx(101.5)
    assert feats["prev_close"] == pytest.approx(100.0)
    assert feats["bar_count"] == 2

    # Long-period indicators have insufficient history -> nan.
    assert math.isnan(feats["rsi"])
    assert math.isnan(feats["adx"])
    assert math.isnan(feats["atr"])
    assert math.isnan(feats["atr_pct"])
    assert math.isnan(feats["rvol"])


def test_compute_features_empty_frame():
    df = pd.DataFrame(
        {"open": [], "high": [], "low": [], "close": [], "volume": []},
        index=pd.DatetimeIndex([], tz="Asia/Kolkata"),
    )
    feats = compute_features(df, params={})
    assert set(feats.keys()) == REQUIRED_KEYS
    assert feats["bar_count"] == 0
    for key in REQUIRED_KEYS - {"bar_count"}:
        assert math.isnan(feats[key])


def test_compute_features_values_present_on_long_frame():
    # 60 increasing bars -> all indicators should produce finite numbers.
    closes = list(np.arange(100.0, 160.0))
    df = _ohlcv(closes, volumes=[1000] * 60)
    feats = compute_features(df, params={})
    assert feats["bar_count"] == 60
    for key in ("rsi", "atr", "atr_pct", "ema_fast", "ema_slow", "vwap"):
        assert not math.isnan(feats[key]), key
    # Strictly increasing closes -> RSI near 100.
    assert feats["rsi"] > 99.0
