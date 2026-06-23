"""Hand-verified tests for ML feature vectorization (PLAN §4.7)."""

import math

import numpy as np

from signal_engine.ml.base import FEATURE_COLUMNS
from signal_engine.ml.features import build_matrix, feature_row, vectorize

TOL = 1e-9


def test_full_raw_dict_hand_verified():
    raw = {
        "close": 1000,
        "vwap": 990,
        "ema_fast": 1005,
        "ema_slow": 1000,
        "rsi": 55,
        "adx": 25,
        "atr_pct": 0.8,
        "rvol": 1.4,
        "news_sentiment_avg": 0.6,
        "news_volume_spike": 2.0,
        "news_has_event": 1.0,
    }
    row = feature_row(raw)

    expected = {
        "vwap_dist_pct": 1.0,      # (1000-990)/1000*100
        "ema_spread_pct": 0.5,     # (1005-1000)/1000*100
        "rsi": 55.0,
        "adx": 25.0,
        "atr_pct": 0.8,
        "rvol": 1.4,
        "news_sentiment": 0.6,
        "news_spike": 2.0,
        "news_event": 1.0,
    }
    for col, val in expected.items():
        assert abs(row[col] - val) < TOL, col

    # vectorize order matches FEATURE_COLUMNS
    vec = vectorize(raw)
    assert vec.shape == (9,)
    for i, col in enumerate(FEATURE_COLUMNS):
        assert abs(vec[i] - expected[col]) < TOL, col


def test_all_keys_present_and_float():
    row = feature_row({"close": 1000, "vwap": 990})
    assert set(row.keys()) == set(FEATURE_COLUMNS)
    assert all(isinstance(v, float) for v in row.values())


def test_missing_keys_yield_zeros():
    row = feature_row({})
    assert set(row.keys()) == set(FEATURE_COLUMNS)
    assert all(v == 0.0 for v in row.values())

    vec = vectorize({})
    assert vec.shape == (9,)
    assert np.allclose(vec, 0.0)


def test_nan_close_yields_zero_ratios():
    raw = {
        "close": float("nan"),
        "vwap": 990,
        "ema_fast": 1005,
        "ema_slow": 1000,
    }
    row = feature_row(raw)
    assert row["vwap_dist_pct"] == 0.0
    assert row["ema_spread_pct"] == 0.0


def test_zero_close_no_zero_division():
    raw = {"close": 0, "vwap": 990, "ema_fast": 1005, "ema_slow": 1000}
    row = feature_row(raw)
    assert row["vwap_dist_pct"] == 0.0
    assert row["ema_spread_pct"] == 0.0


def test_none_values_coerce_to_zero():
    raw = {"close": 1000, "vwap": 990, "rsi": None, "adx": None}
    row = feature_row(raw)
    assert row["rsi"] == 0.0
    assert row["adx"] == 0.0
    assert abs(row["vwap_dist_pct"] - 1.0) < TOL


def test_nan_indicator_coerces_to_zero():
    raw = {"close": 1000, "vwap": 990, "rvol": float("nan")}
    row = feature_row(raw)
    assert row["rvol"] == 0.0


def test_news_sentiment_fallback():
    # falls back to "news_sentiment" when "news_sentiment_avg" absent
    row = feature_row({"news_sentiment": -0.3})
    assert abs(row["news_sentiment"] - (-0.3)) < TOL

    # avg takes precedence when both present
    row2 = feature_row({"news_sentiment_avg": 0.7, "news_sentiment": -0.3})
    assert abs(row2["news_sentiment"] - 0.7) < TOL

    # neither present -> 0.0
    assert feature_row({})["news_sentiment"] == 0.0


def test_build_matrix_shape():
    raws = [
        {"close": 1000, "vwap": 990},
        {"close": 2000, "vwap": 1980},
        {"close": 500, "vwap": 510},
    ]
    mat = build_matrix(raws)
    assert mat.shape == (3, 9)
    assert mat.dtype == float


def test_build_matrix_empty():
    mat = build_matrix([])
    assert mat.shape == (0, 9)


def test_build_matrix_rows_match_vectorize():
    raws = [{"close": 1000, "vwap": 990, "rsi": 60}, {}]
    mat = build_matrix(raws)
    assert np.allclose(mat[0], vectorize(raws[0]))
    assert np.allclose(mat[1], vectorize(raws[1]))
