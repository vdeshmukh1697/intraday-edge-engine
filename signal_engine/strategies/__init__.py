"""Pluggable strategies (PLAN §4.3). Register a strategy, toggle it via config."""

from signal_engine.strategies.base import (
    Strategy,
    StrategyContext,
    create_strategy,
    get_strategy,
    register_strategy,
)

# Import strategy modules so they self-register on package import.
from signal_engine.strategies import vwap_ema_adx  # noqa: E402,F401

__all__ = [
    "Strategy",
    "StrategyContext",
    "register_strategy",
    "get_strategy",
    "create_strategy",
]
