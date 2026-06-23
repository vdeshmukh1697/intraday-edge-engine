"""Strategy interface + registry (PLAN §4.3).

A Strategy is given a ``StrategyContext`` on each CLOSED bar and returns a ``Signal``
or ``None``. Strategies are pure decision logic — no I/O. Indicators are precomputed
by the feature engine and passed in ``ctx.features`` (latest scalar per key) so
strategies stay short and declarative.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Type

import pandas as pd

from signal_engine.domain.enums import MarketState
from signal_engine.domain.models import Signal


@dataclass
class StrategyContext:
    """Everything a strategy needs to decide on the current closed bar.

    ``features`` holds the LATEST value of each indicator at ``ts`` (keys defined in
    ``signal_engine.indicators``: vwap, ema_fast, ema_slow, rsi, adx, atr, rvol,
    orb_high, orb_low, close, prev_close, etc.). ``bars`` is the full closed-bar
    history up to and including the current bar, if a strategy needs more.
    """

    symbol: str
    ts: datetime
    features: Dict[str, float]
    bars: pd.DataFrame
    session_state: MarketState = MarketState.OPEN
    params: Dict[str, float] = field(default_factory=dict)


class Strategy(ABC):
    """Base class. Subclasses set ``name`` and implement ``on_bar``."""

    name: str = "base"

    def __init__(self, params: Optional[Dict[str, float]] = None):
        self.params: Dict[str, float] = dict(params or {})

    def required_indicators(self) -> List[str]:
        """Indicator feature-keys this strategy reads. Used by the feature engine."""
        return []

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> Optional[Signal]:
        """Return a Signal (or None) for the closed bar described by ``ctx``."""


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
_REGISTRY: Dict[str, Type[Strategy]] = {}


def register_strategy(cls: Type[Strategy]) -> Type[Strategy]:
    """Class decorator: register a strategy under its ``name``."""
    if not getattr(cls, "name", None):
        raise ValueError(f"Strategy {cls!r} must define a non-empty 'name'")
    _REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str) -> Type[Strategy]:
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown strategy '{name}'. Registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def create_strategy(name: str, params: Optional[Dict[str, float]] = None) -> Strategy:
    return get_strategy(name)(params)


def registered_strategies() -> List[str]:
    return sorted(_REGISTRY)


# Convenience alias for typing callers
StrategyFactory = Callable[[Dict[str, float]], Strategy]
