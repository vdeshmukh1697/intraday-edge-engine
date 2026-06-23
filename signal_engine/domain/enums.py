"""Enumerations used across the engine. Plain `str` enums for easy serialization."""

from __future__ import annotations

from enum import Enum


class Direction(str, Enum):
    """Trade direction. FLAT means 'no actionable signal'."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"

    @property
    def sign(self) -> int:
        """+1 for LONG, -1 for SHORT, 0 for FLAT — handy for P&L math."""
        return {Direction.LONG: 1, Direction.SHORT: -1, Direction.FLAT: 0}[self]


class MarketState(str, Enum):
    """NSE session state machine (PLAN §3.4)."""

    CLOSED = "CLOSED"
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    SQUARE_OFF = "SQUARE_OFF"  # 15:20–15:30: exits only, no new entries


class ExitReason(str, Enum):
    """How a paper position closed."""

    TARGET = "TARGET"
    STOP = "STOP"
    TIME_STOP = "TIME_STOP"
    SQUARE_OFF = "SQUARE_OFF"
    OPEN = "OPEN"  # still open / not yet exited


class PositionStatus(str, Enum):
    """Lifecycle of a paper position."""

    PENDING = "PENDING"      # plan created, entry trigger not yet hit
    OPEN = "OPEN"            # entered, live
    CLOSED = "CLOSED"        # exited
    CANCELLED = "CANCELLED"  # entry never triggered before validity expired / EOD
