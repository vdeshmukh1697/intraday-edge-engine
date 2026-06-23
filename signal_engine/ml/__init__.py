"""ML signal scorer (PLAN §4.7, §8 Phase 7): train on labeled trades, score in shadow mode."""

from signal_engine.ml.base import FEATURE_COLUMNS, MLModel
from signal_engine.ml.dataset import Dataset, build_dataset
from signal_engine.ml.evaluate import compare, evaluate
from signal_engine.ml.features import build_matrix, feature_row, vectorize
from signal_engine.ml.model import LogisticModel, default_model
from signal_engine.ml.scorer import MLScorer
from signal_engine.ml.train import DEFAULT_MODEL_PATH, TrainReport, train_model

__all__ = [
    "MLModel",
    "FEATURE_COLUMNS",
    "LogisticModel",
    "default_model",
    "MLScorer",
    "vectorize",
    "feature_row",
    "build_matrix",
    "evaluate",
    "compare",
    "build_dataset",
    "Dataset",
    "train_model",
    "TrainReport",
    "DEFAULT_MODEL_PATH",
]
