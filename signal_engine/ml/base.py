"""ML contracts (frozen) — PLAN §4.7.

`MLModel` is the swappable model backend (numpy LogisticModel default, LightGBM optional).
`FEATURE_COLUMNS` is the fixed, stationary ML feature schema derived from the engine's raw
feature dict (technical + news). Raw price *levels* are deliberately excluded — only ratios
/ bounded indicators / news features, so the model sees stationary inputs.

Feature derivation (from a merged technical+news feature dict ``f``), used by ml/features.py:
    vwap_dist_pct  = (f.close - f.vwap) / f.close * 100
    ema_spread_pct = (f.ema_fast - f.ema_slow) / f.close * 100
    rsi            = f.rsi
    adx            = f.adx
    atr_pct        = f.atr_pct
    rvol           = f.rvol
    news_sentiment = f.news_sentiment_avg (fallback f.news_sentiment, else 0)
    news_spike     = f.news_volume_spike (else 0)
    news_event     = f.news_has_event (else 0)

Task 1B — richer stationary features (all already price-normalized, so cross-symbol
comparable; computed point-in-time in indicators/__init__.py + ml/dataset.py, just
coerced to float here):
  Microstructure (single-bar shape / short-window dynamics, no new data):
    bar_range_pct     = (high - low) / close * 100            (current bar range)
    body_pct          = (close - open) / close * 100          (signed candle body)
    upper_wick_ratio  = (high - max(open, close)) / (high - low)   (0..1; 0 if range=0)
    lower_wick_ratio  = (min(open, close) - low) / (high - low)    (0..1; 0 if range=0)
    ret_5_pct         = (close / close[t-5] - 1) * 100        (5-bar momentum)
    rv_5_pct          = stdev of the last 5 one-bar simple returns * 100  (realized vol)
  Regime (no new data): ADX-scaled signed price slope, bounded & stationary —
    regime_trend      = tanh(slope_norm) * min(adx, 50) / 50, where slope_norm is the
                        OLS slope of close over the last N(=20) bars expressed per-bar
                        as a fraction of close (slope/close). Sign = trend direction,
                        magnitude in [0,1] grows with both slope steepness and ADX, so
                        |regime_trend|~1 is a strong clean trend and ~0 is chop.
  Fractional differentiation (López de Prado, indicators/core.frac_diff):
    frac_diff_close_pct = frac_diff(close, d=0.5)[t] / close[t] * 100. Fractionally
                        differenced close (memory-preserving, near-stationary), scaled
                        by price so it is comparable across symbols.
Missing / NaN -> 0.0.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import numpy as np

FEATURE_COLUMNS: List[str] = [
    "vwap_dist_pct",
    "ema_spread_pct",
    "rsi",
    "adx",
    "atr_pct",
    "rvol",
    "news_sentiment",
    "news_spike",
    "news_event",
    # Task 1B — richer stationary features (see module docstring for derivations).
    "bar_range_pct",
    "body_pct",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "ret_5_pct",
    "rv_5_pct",
    "regime_trend",
    "frac_diff_close_pct",
]


class MLModel(ABC):
    """Binary probabilistic classifier over FEATURE_COLUMNS. P(good trade)."""

    name: str = "base"

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray) -> "MLModel":
        """Train on X (n_samples, n_features) and binary y (0/1). Returns self."""

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return P(y=1) as a 1-D array of length n_samples, each in [0, 1]."""

    @abstractmethod
    def feature_importance(self) -> List[float]:
        """Per-column importance aligned to FEATURE_COLUMNS (non-negative)."""

    @abstractmethod
    def explain(self, x: np.ndarray) -> List[float]:
        """Per-feature signed contribution for ONE sample x (aligned to FEATURE_COLUMNS)."""

    @abstractmethod
    def save(self, path: str) -> None:
        """Persist the trained model."""

    @classmethod
    @abstractmethod
    def load(cls, path: str) -> "MLModel":
        """Load a previously saved model."""
