"""ML scorer — PLAN §4.7.

Thin adapter that turns an `MLModel`'s probabilities into a 0..100 confidence
score. Operates on already-vectorized numpy arrays; feature vectorization is the
caller's responsibility (no import of ml/features.py).
"""

from __future__ import annotations

from typing import List

import numpy as np

from .base import MLModel


class MLScorer:
    """Wrap an `MLModel`, exposing 0..100 confidence scores."""

    def __init__(self, model: MLModel) -> None:
        self.model = model

    def score_matrix(self, X: np.ndarray) -> np.ndarray:
        """Confidence (0..100) for each row of X (n_samples, n_features)."""
        return self.model.predict_proba(X) * 100.0

    def score_one(self, x: np.ndarray) -> float:
        """Confidence (0..100) for a single 1-D feature vector, rounded to 1 dp."""
        x = np.asarray(x, dtype=float).reshape(1, -1)
        proba = self.model.predict_proba(x)
        return round(float(proba[0]) * 100.0, 1)

    def explain_one(self, x: np.ndarray) -> List[float]:
        """Per-feature signed contributions for a single sample."""
        return self.model.explain(x)
