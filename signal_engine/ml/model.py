"""ML model backends — PLAN §4.7.

LogisticModel is the zero-dependency (numpy-only) default backend: a from-scratch
logistic regression with full-batch gradient descent on standardized features.
LightGBMModel is an optional, lazily-imported backend; importing/constructing it
raises a clear RuntimeError when LightGBM is not installed.
"""

from __future__ import annotations

import json
import pickle
from typing import List, Optional

import numpy as np

from .base import FEATURE_COLUMNS, MLModel


def _sigmoid(z: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid (clip z to [-30, 30] to guard overflow)."""
    z = np.clip(z, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-z))


class LogisticModel(MLModel):
    """Numpy logistic regression. P(y=1) over FEATURE_COLUMNS."""

    name = "logistic"

    def __init__(self, lr: float = 0.1, n_iter: int = 2000, l2: float = 0.0):
        self.lr = float(lr)
        self.n_iter = int(n_iter)
        self.l2 = float(l2)
        self.w: Optional[np.ndarray] = None
        self.b: float = 0.0
        self.mean: Optional[np.ndarray] = None
        self.std: Optional[np.ndarray] = None
        self.n_features: int = 0

    def _standardize(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean) / self.std

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LogisticModel":
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64).ravel()
        n_samples, n_features = X.shape
        self.n_features = n_features

        self.mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std == 0.0] = 1.0
        self.std = std

        Xs = self._standardize(X)

        # Deterministic init: zeros.
        self.w = np.zeros(n_features, dtype=np.float64)
        self.b = 0.0

        for _ in range(self.n_iter):
            z = Xs @ self.w + self.b
            p = _sigmoid(z)
            err = p - y  # gradient of log-loss wrt z
            grad_w = (Xs.T @ err) / n_samples + self.l2 * self.w
            grad_b = err.mean()
            self.w -= self.lr * grad_w
            self.b -= self.lr * grad_b

        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        Xs = self._standardize(X)
        return _sigmoid(Xs @ self.w + self.b)

    def feature_importance(self) -> List[float]:
        return [float(abs(wi)) for wi in self.w]

    def explain(self, x: np.ndarray) -> List[float]:
        x = np.asarray(x, dtype=np.float64).ravel()
        xs = (x - self.mean) / self.std
        return [float(c) for c in (xs * self.w)]

    def save(self, path: str) -> None:
        payload = {
            "name": self.name,
            "weights": self.w.tolist(),
            "bias": float(self.b),
            "mean": self.mean.tolist(),
            "std": self.std.tolist(),
            "n_features": int(self.n_features),
            "lr": self.lr,
            "n_iter": self.n_iter,
            "l2": self.l2,
        }
        with open(path, "w") as f:
            json.dump(payload, f)

    @classmethod
    def load(cls, path: str) -> "LogisticModel":
        with open(path, "r") as f:
            payload = json.load(f)
        model = cls(
            lr=payload["lr"],
            n_iter=payload["n_iter"],
            l2=payload["l2"],
        )
        model.w = np.asarray(payload["weights"], dtype=np.float64)
        model.b = float(payload["bias"])
        model.mean = np.asarray(payload["mean"], dtype=np.float64)
        model.std = np.asarray(payload["std"], dtype=np.float64)
        model.n_features = int(payload["n_features"])
        return model


class LightGBMModel(MLModel):
    """Optional LightGBM backend (lazily imported)."""

    name = "lightgbm"

    def __init__(self, **params):
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "LightGBM backend requires `pip install lightgbm`; "
                "LogisticModel is the zero-dep default."
            )
        self._lightgbm = lightgbm
        self.params = dict(params)
        self.model = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LightGBMModel":
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y).ravel()
        self.model = self._lightgbm.LGBMClassifier(**self.params)
        self.model.fit(X, y)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        return self.model.predict_proba(X)[:, 1]

    def feature_importance(self) -> List[float]:
        try:
            return list(self.model.booster_.feature_importance())
        except AttributeError:
            return list(self.model.feature_importances_)

    def explain(self, x: np.ndarray) -> List[float]:
        x = np.asarray(x, dtype=np.float64).ravel()
        imp = np.asarray(self.model.feature_importances_, dtype=np.float64)
        return [float(c) for c in (imp * x)]

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump({"params": self.params, "model": self.model}, f)

    @classmethod
    def load(cls, path: str) -> "LightGBMModel":
        with open(path, "rb") as f:
            payload = pickle.load(f)
        model = cls(**payload["params"])
        model.model = payload["model"]
        return model


def default_model() -> MLModel:
    """Return the zero-dependency default backend."""
    return LogisticModel()
