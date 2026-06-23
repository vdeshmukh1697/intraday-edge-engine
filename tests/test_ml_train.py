"""End-to-end ML tests (PLAN §4.7): dataset -> train -> save/load -> SHADOW scan."""

from datetime import date, time

from signal_engine.backtest.engine import trading_days
from signal_engine.config import load_config
from signal_engine.market.calendar import NSECalendar
from signal_engine.ml.base import FEATURE_COLUMNS
from signal_engine.ml.dataset import build_dataset
from signal_engine.ml.model import LogisticModel
from signal_engine.ml.train import train_model
from signal_engine.scan.harness import run_scan
from signal_engine.universe.mock import MockUniverseProvider

_SYMBOLS = ["RELIANCE", "HDFCBANK", "INFY", "TCS", "ICICIBANK"]


def test_build_dataset_shape_and_labels():
    cfg = load_config()
    days = trading_days(date(2025, 6, 2), 4, NSECalendar())
    ds = build_dataset(cfg, _SYMBOLS, days, seed=42)
    assert len(ds) > 50
    assert ds.X.shape[1] == len(FEATURE_COLUMNS)
    assert ds.X.shape[0] == len(ds.y)
    assert set(ds.y.tolist()) <= {0, 1}
    assert ((ds.rules_conf >= 0.0) & (ds.rules_conf <= 1.0)).all()


def test_train_saves_loadable_model(tmp_path):
    cfg = load_config()
    out = str(tmp_path / "m.json")
    model, rep = train_model(cfg, _SYMBOLS, date(2025, 6, 2), n_days=5, seed=42, model_path=out)
    assert model is not None
    assert rep.n_test > 0
    assert 0.0 <= rep.ml["auc"] <= 1.0
    # round-trip: loaded model reproduces predictions
    import numpy as np

    loaded = LogisticModel.load(out)
    sample = np.zeros((3, len(FEATURE_COLUMNS)))
    assert np.allclose(model.predict_proba(sample), loaded.predict_proba(sample))


def test_ml_beats_rules_on_synthetic():
    """On this synthetic data the model should out-rank the rules-confidence baseline."""
    cfg = load_config()
    _, rep = train_model(cfg, _SYMBOLS, date(2025, 6, 2), n_days=12, seed=42, model_path=None)
    assert rep.ml["auc"] > rep.rules["auc"]   # ML adds predictive signal over rules conf


def test_shadow_ml_does_not_change_ranking(tmp_path):
    """SHADOW mode: ML confidence is recorded but must not change the leaderboard order."""
    cfg = load_config()
    out = str(tmp_path / "m.json")
    train_model(cfg, _SYMBOLS, date(2025, 6, 2), n_days=5, seed=42, model_path=out)

    uni = MockUniverseProvider(n=600, seed=42)
    base = run_scan(cfg, uni, date(2025, 6, 23), as_of=time(11, 0), seed=42, top_n=20, with_ml=False)
    shadow = run_scan(cfg, uni, date(2025, 6, 23), as_of=time(11, 0), seed=42, top_n=20,
                      with_ml=True, model_path=out)

    # Same ranking (decisions unchanged), but shadow run carries ML confidences.
    assert [e.symbol for e in base.leaderboard] == [e.symbol for e in shadow.leaderboard]
    assert len(shadow.ml_confidence) >= 1
    for conf in shadow.ml_confidence.values():
        assert 0.0 <= conf <= 100.0
