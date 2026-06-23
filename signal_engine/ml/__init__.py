"""ML signal scorer (PLAN §4.7, §8 Phase 7): train on labeled trades, score in shadow mode.

Exports wired at integration; submodules imported directly during parallel development.
"""

from signal_engine.ml.base import FEATURE_COLUMNS, MLModel

__all__ = ["MLModel", "FEATURE_COLUMNS"]
