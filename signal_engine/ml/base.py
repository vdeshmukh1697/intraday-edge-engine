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
