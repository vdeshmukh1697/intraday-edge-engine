"""ML evaluation metrics — PLAN §4.7.

Pure numpy metric functions over arrays. Decoupled from the model: callers pass
already-computed probabilities and ground-truth labels. No imports from
ml/features.py or ml/model.py.
"""

from __future__ import annotations

from typing import Dict

import numpy as np


def _roc_auc(y_true: np.ndarray, probs: np.ndarray) -> float:
    """ROC AUC via the rank/pairs method.

    Over all (positive, negative) pairs, the fraction where prob_pos > prob_neg,
    counting ties (prob_pos == prob_neg) as 0.5. If only one class is present the
    AUC is undefined -> return 0.5 (neutral).
    """
    pos = probs[y_true == 1]
    neg = probs[y_true == 0]
    n_pos = pos.shape[0]
    n_neg = neg.shape[0]
    if n_pos == 0 or n_neg == 0:
        return 0.5
    # Compare every positive against every negative.
    diff = pos.reshape(-1, 1) - neg.reshape(1, -1)
    wins = np.sum(diff > 0.0)
    ties = np.sum(diff == 0.0)
    return float((wins + 0.5 * ties) / (n_pos * n_neg))


def evaluate(y_true: np.ndarray, probs: np.ndarray, threshold: float = 0.5) -> Dict[str, float]:
    """Compute classification metrics for binary labels and predicted probabilities.

    Returns a dict with keys: accuracy, auc, brier, base_rate, n.
    Empty input yields zeros / n=0 without crashing.
    """
    y_true = np.asarray(y_true, dtype=float)
    probs = np.asarray(probs, dtype=float)
    n = int(y_true.shape[0])

    if n == 0:
        return {"accuracy": 0.0, "auc": 0.0, "brier": 0.0, "base_rate": 0.0, "n": 0}

    preds = (probs >= threshold).astype(int)
    accuracy = float(np.mean(preds == y_true.astype(int)))
    auc = _roc_auc(y_true, probs)
    brier = float(np.mean((probs - y_true) ** 2))
    base_rate = float(np.mean(y_true))

    return {
        "accuracy": accuracy,
        "auc": auc,
        "brier": brier,
        "base_rate": base_rate,
        "n": n,
    }


def compare(
    y_true: np.ndarray, ml_probs: np.ndarray, baseline_probs: np.ndarray
) -> Dict[str, object]:
    """Compare ML probabilities against a baseline on the same labels.

    auc_gain  = ml.auc - baseline.auc        (positive == ML better)
    brier_gain = baseline.brier - ml.brier   (positive == ML better; lower brier is better)
    """
    m = evaluate(y_true, ml_probs)
    b = evaluate(y_true, baseline_probs)
    return {
        "ml": m,
        "baseline": b,
        "auc_gain": m["auc"] - b["auc"],
        "brier_gain": b["brier"] - m["brier"],
    }
