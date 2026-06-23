"""Domain layer: frozen contracts (enums + models) shared across all modules.

Everything here is pure data + enums with no I/O. These are the interfaces the
indicator engine, strategies, risk layer, paper-trader and engine build against.
"""

from signal_engine.domain.enums import (
    Direction,
    ExitReason,
    MarketState,
    PositionStatus,
)
from signal_engine.domain.models import (
    Bar,
    CostBreakdown,
    PaperPosition,
    Signal,
    Tick,
    TradePlan,
)

__all__ = [
    "Direction",
    "ExitReason",
    "MarketState",
    "PositionStatus",
    "Bar",
    "CostBreakdown",
    "PaperPosition",
    "Signal",
    "Tick",
    "TradePlan",
]
