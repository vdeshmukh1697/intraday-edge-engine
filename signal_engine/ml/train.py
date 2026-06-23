"""Training harness (PLAN §4.7): build labeled data -> time-split -> train -> compare vs rules.

The split is by sample order, which is chronological (build_dataset iterates days then bars
in time order), so the model trains on older signals and is judged on newer ones — the
out-of-sample discipline of §6.4. The rules confidence is the baseline: ML must beat it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from signal_engine.backtest.engine import trading_days
from signal_engine.config import AppConfig
from signal_engine.market.calendar import NSECalendar
from signal_engine.ml.base import FEATURE_COLUMNS, MLModel
from signal_engine.ml.dataset import build_dataset
from signal_engine.ml.evaluate import compare
from signal_engine.ml.model import default_model

DEFAULT_MODEL_PATH = "data/models/signal_model.json"


@dataclass
class TrainReport:
    n_samples: int
    n_train: int
    n_test: int
    base_rate: float
    ml: Dict[str, float] = field(default_factory=dict)
    rules: Dict[str, float] = field(default_factory=dict)
    auc_gain: float = 0.0
    brier_gain: float = 0.0
    importances: Dict[str, float] = field(default_factory=dict)
    model_path: Optional[str] = None


def train_model(
    cfg: AppConfig,
    symbols: List[str],
    start: date,
    n_days: int,
    seed: int = 42,
    test_frac: float = 0.3,
    model_path: Optional[str] = DEFAULT_MODEL_PATH,
    min_samples: int = 50,
) -> Tuple[Optional[MLModel], TrainReport]:
    days = trading_days(start, n_days, NSECalendar())
    ds = build_dataset(cfg, symbols, days, seed=seed)
    n = len(ds)
    if n < min_samples:
        return None, TrainReport(n_samples=n, n_train=0, n_test=0, base_rate=0.0)

    n_train = max(1, int(n * (1.0 - test_frac)))
    Xtr, ytr = ds.X[:n_train], ds.y[:n_train]
    Xte, yte = ds.X[n_train:], ds.y[n_train:]
    rules_te = ds.rules_conf[n_train:]

    model = default_model()
    model.fit(Xtr, ytr)
    probs = model.predict_proba(Xte)
    comp = compare(yte, probs, rules_te)

    importances = dict(zip(FEATURE_COLUMNS, model.feature_importance()))

    if model_path:
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        model.save(model_path)

    report = TrainReport(
        n_samples=n, n_train=n_train, n_test=len(yte),
        base_rate=round(float(ds.y.mean()), 4),
        ml=comp["ml"], rules=comp["baseline"],
        auc_gain=round(comp["auc_gain"], 4), brier_gain=round(comp["brier_gain"], 4),
        importances={k: round(v, 4) for k, v in importances.items()},
        model_path=model_path,
    )
    return model, report
