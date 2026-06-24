"""Deterministic, hand-verified tests for the indicators module."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from signal_engine.indicators import (
    bar_shape,
    compute_features,
    realized_vol_pct,
    regime_trend,
    short_window_return_pct,
)
from signal_engine.indicators.core import (
    _frac_diff_weights,
    atr,
    ema,
    frac_diff,
    opening_range,
    round_number_levels,
    rsi,
    rvol,
    vwap,
    vwap_bands,
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


# --------------------------------------------------------------------------------
# A3 — VWAP +/- k*sigma bands (point-in-time)
# --------------------------------------------------------------------------------

def test_vwap_bands_constant_price_zero_sigma():
    # All typical prices identical -> running dispersion is 0 -> bands collapse on VWAP.
    df = _ohlcv([42.0] * 10, volumes=[100, 200, 50, 300, 10, 1, 999, 7, 5, 88])
    vb = vwap_bands(df, k=2.0)
    assert vb["vwap"].iloc[-1] == pytest.approx(42.0)
    assert vb["vwap_sigma"].iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert vb["vwap_upper"].iloc[-1] == pytest.approx(42.0)
    assert vb["vwap_lower"].iloc[-1] == pytest.approx(42.0)


def test_vwap_bands_hand_computed_two_bars():
    # Equal volume, typical prices 100 then 102.
    #   VWAP = 101. mean_sq = (100^2+102^2)/2 = 10202. var = 10202 - 101^2 = 1.
    #   sigma = 1. upper = 101+2 = 103, lower = 101-2 = 99.
    df = pd.DataFrame(
        {"open": [100.0, 102.0], "high": [100.0, 102.0], "low": [100.0, 102.0],
         "close": [100.0, 102.0], "volume": [10, 10]},
        index=_index(2),
    )
    vb = vwap_bands(df, k=2.0)
    assert vb["vwap"].iloc[-1] == pytest.approx(101.0)
    assert vb["vwap_sigma"].iloc[-1] == pytest.approx(1.0)
    assert vb["vwap_upper"].iloc[-1] == pytest.approx(103.0)
    assert vb["vwap_lower"].iloc[-1] == pytest.approx(99.0)


def test_vwap_bands_point_in_time_no_lookahead():
    # Extending the session must not change earlier band values (causal/expanding).
    closes = [100.0, 101.0, 99.0, 103.0, 98.0, 104.0]
    vols = [10, 20, 30, 40, 50, 60]
    base = _ohlcv(closes[:3], volumes=vols[:3])
    ext = _ohlcv(closes, volumes=vols)
    vb_base = vwap_bands(base, k=2.0)
    vb_ext = vwap_bands(ext, k=2.0)
    for col in ("vwap", "vwap_sigma", "vwap_upper", "vwap_lower"):
        assert np.allclose(vb_base[col].to_numpy(), vb_ext[col].to_numpy()[:3])


# --------------------------------------------------------------------------------
# A3 — round-number levels (point-in-time, price-scaled)
# --------------------------------------------------------------------------------

def test_round_number_levels_brackets_price():
    # price=1003, step 0.5% of 1000-ish -> ~5; snapped to nice 5 grid -> 1000 / 1005.
    below, above = round_number_levels(1003.0, step_pct=0.5)
    assert below == pytest.approx(1000.0)
    assert above == pytest.approx(1005.0)
    assert below <= 1003.0 < above


def test_round_number_levels_scales_with_price():
    # A cheap stock gets a finer absolute grid than an expensive one (price-scaled).
    below_cheap, above_cheap = round_number_levels(50.0, step_pct=0.5)
    below_exp, above_exp = round_number_levels(5000.0, step_pct=0.5)
    assert (above_cheap - below_cheap) < (above_exp - below_exp)


def test_round_number_levels_point_in_time_pure_function_of_price():
    # Depends ONLY on the current price -> deterministic, no history.
    a = round_number_levels(2487.0, step_pct=0.5)
    b = round_number_levels(2487.0, step_pct=0.5)
    assert a == b


def test_round_number_levels_invalid_price_nan():
    for bad in (0.0, -10.0, float("nan")):
        below, above = round_number_levels(bad, step_pct=0.5)
        assert math.isnan(below) and math.isnan(above)


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
    # Task 1B richer features (always present in the output dict, nan if short history).
    "bar_range_pct",
    "body_pct",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "ret_5_pct",
    "rv_5_pct",
    "regime_trend",
    "frac_diff_close_pct",
    # A3 structure levels (point-in-time, for structure-aware targets).
    "vwap_sigma",
    "vwap_upper",
    "vwap_lower",
    "round_below",
    "round_above",
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


# --------------------------------------------------------------------------------
# Task 1B — fractional differentiation
# --------------------------------------------------------------------------------

def test_frac_diff_weights_recurrence_hand_computed():
    # d = 0.5 binomial weights via w[k] = -w[k-1]*(d-k+1)/k:
    #   w0 = 1
    #   w1 = -1 * (0.5)/1            = -0.5
    #   w2 = -(-0.5) * (0.5-1)/2     = 0.5 * (-0.5)/2 = -0.125
    #   w3 = -(-0.125) * (0.5-2)/3   = 0.125 * (-1.5)/3 = -0.0625
    w = _frac_diff_weights(0.5, 4)
    assert w[0] == pytest.approx(1.0)
    assert w[1] == pytest.approx(-0.5)
    assert w[2] == pytest.approx(-0.125)
    assert w[3] == pytest.approx(-0.0625)


def test_frac_diff_d1_is_first_difference():
    # d = 1 -> (1 - B)^1 -> ordinary first difference.
    # weights: w0=1, w1=-1, w2..=0  =>  fd[t] = x[t]-x[t-1], fd[0]=x[0].
    s = pd.Series([3.0, 5.0, 8.0, 8.0, 6.0], index=_index(5))
    fd = frac_diff(s, d=1.0)
    expected = [3.0, 2.0, 3.0, 0.0, -2.0]
    assert np.allclose(fd.to_numpy(), expected)


def test_frac_diff_d0_is_identity():
    # d = 0 -> (1 - B)^0 = identity -> output equals input.
    s = pd.Series([3.0, 5.0, 8.0, 2.0], index=_index(4))
    fd = frac_diff(s, d=0.0)
    assert np.allclose(fd.to_numpy(), s.to_numpy())


def test_frac_diff_half_hand_computed():
    # d=0.5 expanding window on x = [10, 12, 11, 14].
    #   fd[0] = 1*10                                   = 10
    #   fd[1] = 1*12 + (-0.5)*10                       = 12 - 5      = 7
    #   fd[2] = 1*11 + (-0.5)*12 + (-0.125)*10         = 11-6-1.25   = 3.75
    #   fd[3] = 1*14 + (-0.5)*11 + (-0.125)*12 + (-0.0625)*10
    #         = 14 - 5.5 - 1.5 - 0.625                 = 6.375
    s = pd.Series([10.0, 12.0, 11.0, 14.0], index=_index(4))
    fd = frac_diff(s, d=0.5)
    assert fd.iloc[0] == pytest.approx(10.0)
    assert fd.iloc[1] == pytest.approx(7.0)
    assert fd.iloc[2] == pytest.approx(3.75)
    assert fd.iloc[3] == pytest.approx(6.375)


def test_frac_diff_is_causal_point_in_time():
    # fd[t] must depend ONLY on x[0..t]: extending the series must not change
    # earlier values (no lookahead).
    base = pd.Series([10.0, 12.0, 11.0], index=_index(3))
    extended = pd.Series([10.0, 12.0, 11.0, 99.0, -7.0], index=_index(5))
    fd_base = frac_diff(base, d=0.5)
    fd_ext = frac_diff(extended, d=0.5)
    assert np.allclose(fd_base.to_numpy(), fd_ext.to_numpy()[:3])


def test_frac_diff_empty():
    s = pd.Series([], dtype=float)
    fd = frac_diff(s, d=0.5)
    assert len(fd) == 0


# --------------------------------------------------------------------------------
# Task 1B — microstructure (single-bar shape)
# --------------------------------------------------------------------------------

def test_bar_shape_hand_computed():
    # open=100, high=110, low=95, close=105.  range = 15.
    #   bar_range_pct    = 15/105*100              = 14.2857...
    #   body_pct         = (105-100)/105*100       = 4.7619...
    #   upper_wick_ratio = (110-max(100,105))/15   = 5/15  = 0.3333...
    #   lower_wick_ratio = (min(100,105)-95)/15    = 5/15  = 0.3333...
    out = bar_shape(100.0, 110.0, 95.0, 105.0)
    assert out["bar_range_pct"] == pytest.approx(15.0 / 105.0 * 100.0)
    assert out["body_pct"] == pytest.approx(5.0 / 105.0 * 100.0)
    assert out["upper_wick_ratio"] == pytest.approx(5.0 / 15.0)
    assert out["lower_wick_ratio"] == pytest.approx(5.0 / 15.0)


def test_bar_shape_doji_zero_range_safe():
    # high == low (zero range) -> wick ratios 0, body 0, range 0, no div-by-zero.
    out = bar_shape(50.0, 50.0, 50.0, 50.0)
    assert out["bar_range_pct"] == pytest.approx(0.0)
    assert out["body_pct"] == pytest.approx(0.0)
    assert out["upper_wick_ratio"] == 0.0
    assert out["lower_wick_ratio"] == 0.0


def test_bar_shape_negative_body_for_down_bar():
    # close < open -> body_pct negative.
    out = bar_shape(100.0, 101.0, 90.0, 95.0)
    assert out["body_pct"] < 0.0


def test_bar_shape_zero_close_nan():
    out = bar_shape(0.0, 0.0, 0.0, 0.0)
    assert all(math.isnan(v) for v in out.values())


# --------------------------------------------------------------------------------
# Task 1B — short-window return + realized vol
# --------------------------------------------------------------------------------

def test_short_window_return_pct_hand_computed():
    # 5-bar return uses close[-6] vs close[-1]: (110/100 - 1)*100 = 10%.
    close = np.array([100.0, 1, 2, 3, 4, 110.0])
    assert short_window_return_pct(close, window=5) == pytest.approx(10.0)


def test_short_window_return_pct_insufficient_history():
    close = np.array([100.0, 101.0, 102.0])  # need window+1 = 6 bars
    assert math.isnan(short_window_return_pct(close, window=5))


def test_realized_vol_constant_returns_zero():
    # Geometric ramp: each one-bar simple return is identical -> realized vol 0.
    close = np.array([100.0, 110.0, 121.0, 133.1, 146.41, 161.051])
    assert realized_vol_pct(close, window=5) == pytest.approx(0.0, abs=1e-9)


def test_realized_vol_nonzero_for_varying_returns():
    close = np.array([100.0, 101.0, 99.0, 103.0, 100.0, 105.0])
    assert realized_vol_pct(close, window=5) > 0.0


def test_realized_vol_insufficient_history():
    close = np.array([100.0, 101.0])
    assert math.isnan(realized_vol_pct(close, window=5))


# --------------------------------------------------------------------------------
# Task 1B — regime scalar
# --------------------------------------------------------------------------------

def test_regime_trend_uptrend_positive():
    # Strong steady uptrend + high ADX -> positive, large magnitude.
    close = np.linspace(100.0, 120.0, 20)
    val = regime_trend(close, adx_val=40.0, window=20)
    assert val > 0.0


def test_regime_trend_downtrend_negative():
    close = np.linspace(120.0, 100.0, 20)
    val = regime_trend(close, adx_val=40.0, window=20)
    assert val < 0.0


def test_regime_trend_low_adx_dampens_magnitude():
    # Same price slope, different ADX -> higher ADX yields larger |regime|.
    close = np.linspace(100.0, 120.0, 20)
    strong = regime_trend(close, adx_val=50.0, window=20)
    weak = regime_trend(close, adx_val=10.0, window=20)
    assert abs(weak) < abs(strong)


def test_regime_trend_zero_adx_is_zero():
    close = np.linspace(100.0, 120.0, 20)
    assert regime_trend(close, adx_val=0.0, window=20) == pytest.approx(0.0)


def test_regime_trend_bounded():
    # Even a violent slope stays within [-1, 1] (tanh-squashed, ADX-scaled).
    close = np.linspace(1.0, 1000.0, 20)
    val = regime_trend(close, adx_val=80.0, window=20)
    assert -1.0 <= val <= 1.0


def test_regime_trend_nan_adx_returns_nan():
    close = np.linspace(100.0, 120.0, 20)
    assert math.isnan(regime_trend(close, adx_val=float("nan"), window=20))


def test_regime_trend_insufficient_history():
    close = np.linspace(100.0, 110.0, 5)  # fewer than window=20
    assert math.isnan(regime_trend(close, adx_val=30.0, window=20))


# --------------------------------------------------------------------------------
# Task 1B — integration: new keys appear in compute_features
# --------------------------------------------------------------------------------

_NEW_KEYS = (
    "bar_range_pct",
    "body_pct",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "ret_5_pct",
    "rv_5_pct",
    "regime_trend",
    "frac_diff_close_pct",
)


def test_compute_features_emits_new_keys_on_long_frame():
    rng = np.random.default_rng(7)
    closes = list(100.0 + np.cumsum(rng.normal(0.05, 0.4, 60)))
    df = _ohlcv(closes, volumes=[1000] * 60)
    feats = compute_features(df, params={})
    for key in _NEW_KEYS:
        assert key in feats, key
        assert not math.isnan(feats[key]), key


def test_compute_features_new_keys_nan_safe_on_short_frame():
    # 2-bar frame: window-dependent features are nan, but never raise.
    df = _ohlcv([100.0, 101.0], volumes=[1000, 1100])
    feats = compute_features(df, params={})
    for key in _NEW_KEYS:
        assert key in feats, key
    # Single-bar shape is computable even on a short frame.
    assert not math.isnan(feats["bar_range_pct"])
    # Window-dependent ones need history -> nan here.
    assert math.isnan(feats["ret_5_pct"])
    assert math.isnan(feats["rv_5_pct"])
    assert math.isnan(feats["regime_trend"])


# --------------------------------------------------------------------------------
# A3 — structure-level keys emitted by compute_features
# --------------------------------------------------------------------------------

_STRUCT_KEYS = ("vwap_sigma", "vwap_upper", "vwap_lower", "round_below", "round_above")


def test_compute_features_emits_structure_levels_on_long_frame():
    rng = np.random.default_rng(11)
    closes = list(1000.0 + np.cumsum(rng.normal(0.0, 1.5, 60)))
    # Give bars a real high/low range so VWAP sigma is non-degenerate.
    n = len(closes)
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        },
        index=_index(n),
    )
    feats = compute_features(df, params={})
    for key in _STRUCT_KEYS:
        assert key in feats, key
        assert not math.isnan(feats[key]), key
    # Bands bracket the VWAP; round levels bracket the close.
    assert feats["vwap_lower"] <= feats["vwap"] <= feats["vwap_upper"]
    assert feats["round_below"] <= feats["close"] < feats["round_above"]


def test_compute_features_structure_keys_present_on_empty_frame():
    df = pd.DataFrame(
        {"open": [], "high": [], "low": [], "close": [], "volume": []},
        index=pd.DatetimeIndex([], tz="Asia/Kolkata"),
    )
    feats = compute_features(df, params={})
    for key in _STRUCT_KEYS:
        assert key in feats and math.isnan(feats[key]), key
