"""Tests for ML model backends (PLAN §4.7)."""

import importlib.util

import numpy as np
import pytest

from signal_engine.ml.model import (
    LightGBMModel,
    LogisticModel,
    default_model,
)

_HAS_LIGHTGBM = importlib.util.find_spec("lightgbm") is not None


def _toy_dataset(seed: int = 0):
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((400, 4))
    y = (X[:, 0] + X[:, 1] - X[:, 2] > 0).astype(int)
    Xtr, Xte = X[:300], X[300:]
    ytr, yte = y[:300], y[300:]
    return Xtr, ytr, Xte, yte


def test_separable_accuracy():
    Xtr, ytr, Xte, yte = _toy_dataset()
    model = LogisticModel().fit(Xtr, ytr)
    pred = (model.predict_proba(Xte) >= 0.5).astype(int)
    acc = (pred == yte).mean()
    assert acc > 0.9, acc


def test_predict_proba_in_range():
    Xtr, ytr, Xte, _ = _toy_dataset()
    model = LogisticModel().fit(Xtr, ytr)
    p = model.predict_proba(Xte)
    assert np.all(p >= 0.0) and np.all(p <= 1.0)


def test_determinism():
    Xtr, ytr, Xte, _ = _toy_dataset()
    m1 = LogisticModel().fit(Xtr, ytr)
    m2 = LogisticModel().fit(Xtr, ytr)
    assert np.allclose(m1.predict_proba(Xte), m2.predict_proba(Xte))


def test_feature_importance():
    Xtr, ytr, _, _ = _toy_dataset()
    model = LogisticModel().fit(Xtr, ytr)
    imp = model.feature_importance()
    assert len(imp) == model.n_features == 4
    assert all(i >= 0 for i in imp)
    # Informative features (0, 1, 2) should dominate the noise feature (3).
    assert imp[0] > imp[3]
    assert imp[1] > imp[3]
    assert imp[2] > imp[3]


def test_all_one_class_trains():
    X = np.random.default_rng(1).standard_normal((50, 4))
    y = np.ones(50, dtype=int)
    model = LogisticModel(n_iter=500).fit(X, y)
    p = model.predict_proba(X)
    assert np.all(p >= 0.0) and np.all(p <= 1.0)
    assert p.mean() > 0.5  # bias pushed toward the single class


def test_explain():
    Xtr, ytr, Xte, _ = _toy_dataset()
    model = LogisticModel().fit(Xtr, ytr)
    x = Xte[0]
    contrib = model.explain(x)
    assert len(contrib) == model.n_features
    xs0 = (x[0] - model.mean[0]) / model.std[0]
    expected_sign = np.sign(xs0 * model.w[0])
    assert np.sign(contrib[0]) == expected_sign


def test_save_load_roundtrip(tmp_path):
    Xtr, ytr, Xte, _ = _toy_dataset()
    model = LogisticModel().fit(Xtr, ytr)
    path = str(tmp_path / "model.json")
    model.save(path)
    loaded = LogisticModel.load(path)
    assert np.allclose(model.predict_proba(Xte), loaded.predict_proba(Xte))


def test_lightgbm_absent():
    if _HAS_LIGHTGBM:
        pytest.skip("lightgbm is installed; absence path not testable")
    with pytest.raises(RuntimeError):
        LightGBMModel()


def test_default_model():
    assert isinstance(default_model(), LogisticModel)
