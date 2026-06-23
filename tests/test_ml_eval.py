"""Hand-verified tests for ml/evaluate.py and ml/scorer.py — PLAN §4.7."""

from __future__ import annotations

from typing import List

import numpy as np
import pytest

from signal_engine.ml.base import MLModel
from signal_engine.ml.evaluate import compare, evaluate
from signal_engine.ml.scorer import MLScorer

TOL = 1e-9


class StubModel(MLModel):
    """Minimal MLModel returning a fixed probability array; explain echoes x."""

    name = "stub"

    def __init__(self, probs):
        self._probs = np.asarray(probs, dtype=float)

    def fit(self, X, y):  # pragma: no cover - not used
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        # Return a slice matching the number of input rows.
        n = np.asarray(X).shape[0]
        return self._probs[:n]

    def feature_importance(self) -> List[float]:  # pragma: no cover - not used
        return []

    def explain(self, x: np.ndarray) -> List[float]:
        return list(np.asarray(x, dtype=float))

    def save(self, path: str) -> None:  # pragma: no cover - not used
        pass

    @classmethod
    def load(cls, path: str) -> "StubModel":  # pragma: no cover - not used
        return cls([])


# --- evaluate ---------------------------------------------------------------


def test_auc_hand_check():
    y = np.array([0, 0, 1, 1])
    probs = np.array([0.1, 0.4, 0.35, 0.8])
    # pairs: 0.35>0.1 T, 0.35>0.4 F, 0.8>0.1 T, 0.8>0.4 T => 3/4
    res = evaluate(y, probs)
    assert res["auc"] == pytest.approx(0.75, abs=TOL)


def test_perfect_probs():
    y = np.array([0, 1, 0, 1])
    probs = y.astype(float)
    res = evaluate(y, probs)
    assert res["accuracy"] == pytest.approx(1.0, abs=TOL)
    assert res["auc"] == pytest.approx(1.0, abs=TOL)
    assert res["brier"] == pytest.approx(0.0, abs=TOL)


def test_brier_hand_check():
    y = np.array([1, 0])
    probs = np.array([0.8, 0.3])
    # ((0.8-1)^2 + (0.3-0)^2) / 2 = (0.04 + 0.09) / 2 = 0.065
    res = evaluate(y, probs)
    assert res["brier"] == pytest.approx(0.065, abs=TOL)


def test_single_class_auc_neutral():
    y = np.array([1, 1, 1])
    probs = np.array([0.2, 0.6, 0.9])
    res = evaluate(y, probs)
    assert res["auc"] == pytest.approx(0.5, abs=TOL)


def test_tie_handling():
    y = np.array([0, 1])
    probs = np.array([0.5, 0.5])
    # one tie pair counted as 0.5 -> auc 0.5
    res = evaluate(y, probs)
    assert res["auc"] == pytest.approx(0.5, abs=TOL)


def test_base_rate_and_n():
    y = np.array([0, 0, 1, 1])
    probs = np.array([0.1, 0.4, 0.35, 0.8])
    res = evaluate(y, probs)
    assert res["base_rate"] == pytest.approx(0.5, abs=TOL)
    assert res["n"] == 4


def test_empty_evaluate():
    res = evaluate(np.array([]), np.array([]))
    assert res["n"] == 0
    assert res["accuracy"] == pytest.approx(0.0, abs=TOL)
    assert res["auc"] == pytest.approx(0.0, abs=TOL)
    assert res["brier"] == pytest.approx(0.0, abs=TOL)
    assert res["base_rate"] == pytest.approx(0.0, abs=TOL)


# --- compare ----------------------------------------------------------------


def test_compare_ml_better():
    y = np.array([0, 0, 1, 1])
    # ML separates classes well; baseline is flat/uninformative.
    ml_probs = np.array([0.1, 0.2, 0.8, 0.9])
    baseline_probs = np.array([0.5, 0.5, 0.5, 0.5])
    res = compare(y, ml_probs, baseline_probs)
    assert res["auc_gain"] > 0.0
    assert res["brier_gain"] > 0.0
    assert res["ml"]["auc"] == pytest.approx(1.0, abs=TOL)
    assert res["baseline"]["auc"] == pytest.approx(0.5, abs=TOL)


# --- scorer -----------------------------------------------------------------


def test_score_one():
    model = StubModel([0.7])
    scorer = MLScorer(model)
    assert scorer.score_one(np.array([1.0, 2.0, 3.0])) == pytest.approx(70.0, abs=TOL)


def test_score_matrix():
    model = StubModel([0.1, 0.5, 0.9])
    scorer = MLScorer(model)
    X = np.zeros((3, 4))
    out = scorer.score_matrix(X)
    assert np.allclose(out, np.array([10.0, 50.0, 90.0]), atol=TOL)


def test_explain_one():
    model = StubModel([0.5])
    scorer = MLScorer(model)
    x = np.array([1.0, -2.0, 3.5])
    assert scorer.explain_one(x) == [1.0, -2.0, 3.5]
