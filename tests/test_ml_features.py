"""Hand-verified tests for ML feature vectorization (PLAN §4.7)."""


import numpy as np
import pytest

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
    assert vec.shape == (len(FEATURE_COLUMNS),)
    for i, col in enumerate(FEATURE_COLUMNS):
        # Original 9 columns have hand-checked values; Task 1B columns are absent
        # from this raw dict, so they must coerce to 0.0.
        want = expected.get(col, 0.0)
        assert abs(vec[i] - want) < TOL, col


def test_all_keys_present_and_float():
    row = feature_row({"close": 1000, "vwap": 990})
    assert set(row.keys()) == set(FEATURE_COLUMNS)
    assert all(isinstance(v, float) for v in row.values())


def test_missing_keys_yield_zeros():
    row = feature_row({})
    assert set(row.keys()) == set(FEATURE_COLUMNS)
    assert all(v == 0.0 for v in row.values())

    vec = vectorize({})
    assert vec.shape == (len(FEATURE_COLUMNS),)
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
    assert mat.shape == (3, len(FEATURE_COLUMNS))
    assert mat.dtype == float


def test_build_matrix_empty():
    mat = build_matrix([])
    assert mat.shape == (0, len(FEATURE_COLUMNS))


def test_build_matrix_rows_match_vectorize():
    raws = [{"close": 1000, "vwap": 990, "rsi": 60}, {}]
    mat = build_matrix(raws)
    assert np.allclose(mat[0], vectorize(raws[0]))
    assert np.allclose(mat[1], vectorize(raws[1]))


# --------------------------------------------------------------------------------
# Task 1B — richer feature columns flow through feature_row / vectorize
# --------------------------------------------------------------------------------

_NEW_COLUMNS = (
    "bar_range_pct",
    "body_pct",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "ret_5_pct",
    "rv_5_pct",
    "regime_trend",
    "frac_diff_close_pct",
)


def test_feature_columns_includes_new_task1b_columns():
    for col in _NEW_COLUMNS:
        assert col in FEATURE_COLUMNS, col
    # 9 original + 8 new.
    assert len(FEATURE_COLUMNS) == 17


def test_new_columns_passed_through_as_floats():
    # feature_row simply coerces these already-stationary raw values to float.
    raw = {
        "close": 1000,
        "vwap": 990,
        "ema_fast": 1005,
        "ema_slow": 1000,
        "bar_range_pct": 1.5,
        "body_pct": -0.4,
        "upper_wick_ratio": 0.3,
        "lower_wick_ratio": 0.2,
        "ret_5_pct": 0.8,
        "rv_5_pct": 0.25,
        "regime_trend": -0.6,
        "frac_diff_close_pct": 4.2,
    }
    row = feature_row(raw)
    assert row["bar_range_pct"] == pytest.approx(1.5)
    assert row["body_pct"] == pytest.approx(-0.4)
    assert row["upper_wick_ratio"] == pytest.approx(0.3)
    assert row["lower_wick_ratio"] == pytest.approx(0.2)
    assert row["ret_5_pct"] == pytest.approx(0.8)
    assert row["rv_5_pct"] == pytest.approx(0.25)
    assert row["regime_trend"] == pytest.approx(-0.6)
    assert row["frac_diff_close_pct"] == pytest.approx(4.2)
    # vectorize order tracks FEATURE_COLUMNS.
    vec = vectorize(raw)
    assert vec.shape == (17,)
    for i, col in enumerate(FEATURE_COLUMNS):
        assert vec[i] == pytest.approx(row[col]), col


def test_new_columns_nan_and_missing_safe():
    # Missing -> 0.0; NaN -> 0.0 (no propagation into the matrix).
    row_missing = feature_row({"close": 1000, "vwap": 990})
    for col in _NEW_COLUMNS:
        assert row_missing[col] == 0.0, col

    row_nan = feature_row({"close": 1000, "regime_trend": float("nan"),
                           "frac_diff_close_pct": float("nan"),
                           "ret_5_pct": float("nan")})
    assert row_nan["regime_trend"] == 0.0
    assert row_nan["frac_diff_close_pct"] == 0.0
    assert row_nan["ret_5_pct"] == 0.0


def test_new_columns_none_safe():
    row = feature_row({"close": 1000, "bar_range_pct": None, "body_pct": None})
    assert row["bar_range_pct"] == 0.0
    assert row["body_pct"] == 0.0
