"""ML feature vectorization — PLAN §4.7.

Derives the stationary FEATURE_COLUMNS schema from the engine's merged
technical + news raw feature dict. Pure numpy, deterministic, Python 3.9 safe.

Derivation rules (see ml/base.py docstring):
    vwap_dist_pct  = (close - vwap) / close * 100   (close in {0, None, NaN} -> 0.0)
    ema_spread_pct = (ema_fast - ema_slow) / close * 100   (close 0 -> 0.0)
    rsi            = raw.rsi
    adx            = raw.adx
    atr_pct        = raw.atr_pct
    rvol           = raw.rvol
    news_sentiment = raw.news_sentiment_avg (fallback news_sentiment, else 0.0)
    news_spike     = raw.news_volume_spike (else 0.0)
    news_event     = raw.news_has_event (else 0.0)

Any missing key, None, or NaN coerces to 0.0.
"""

from __future__ import annotations

import math
from typing import Dict, List

import numpy as np

from .base import FEATURE_COLUMNS


def _num(value) -> float:
    """Coerce a raw value to a finite float; None/NaN/inf/non-numeric -> 0.0."""
    if value is None:
        return 0.0
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(out):
        return 0.0
    return out


def feature_row(raw: dict) -> Dict[str, float]:
    """Derive a dict keyed by FEATURE_COLUMNS (all keys present, all floats)."""
    raw = raw or {}

    close = _num(raw.get("close"))
    vwap = _num(raw.get("vwap"))
    ema_fast = _num(raw.get("ema_fast"))
    ema_slow = _num(raw.get("ema_slow"))

    if close == 0.0:
        vwap_dist_pct = 0.0
        ema_spread_pct = 0.0
    else:
        vwap_dist_pct = (close - vwap) / close * 100.0
        ema_spread_pct = (ema_fast - ema_slow) / close * 100.0

    news_sentiment = raw.get("news_sentiment_avg", raw.get("news_sentiment", 0.0))

    row = {
        "vwap_dist_pct": vwap_dist_pct,
        "ema_spread_pct": ema_spread_pct,
        "rsi": _num(raw.get("rsi")),
        "adx": _num(raw.get("adx")),
        "atr_pct": _num(raw.get("atr_pct")),
        "rvol": _num(raw.get("rvol")),
        "news_sentiment": _num(news_sentiment),
        "news_spike": _num(raw.get("news_volume_spike")),
        "news_event": _num(raw.get("news_has_event")),
    }
    return {col: row[col] for col in FEATURE_COLUMNS}


def vectorize(raw: dict) -> np.ndarray:
    """Return a 1-D float array of length len(FEATURE_COLUMNS) in column order."""
    row = feature_row(raw)
    return np.array([row[col] for col in FEATURE_COLUMNS], dtype=float)


def build_matrix(raws: List[dict]) -> np.ndarray:
    """Stack vectorize() over a list -> shape (n, len(FEATURE_COLUMNS))."""
    n_cols = len(FEATURE_COLUMNS)
    if not raws:
        return np.empty((0, n_cols), dtype=float)
    return np.vstack([vectorize(r) for r in raws])
