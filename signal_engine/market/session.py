"""Session state machine (PLAN §3.4): CLOSED -> PRE_OPEN -> OPEN -> SQUARE_OFF -> CLOSED.

The whole engine keys off ``state_at(dt)``. New entry signals are only generated in
OPEN before ``no_new_entry_after``; SQUARE_OFF is exits-only; positions are force-flat
at ``square_off``.
"""

from __future__ import annotations

from datetime import datetime, time

from signal_engine.config import MarketConfig
from signal_engine.domain.enums import MarketState
from signal_engine.market.calendar import NSECalendar


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


class MarketSession:
    def __init__(self, market_cfg: MarketConfig, calendar: NSECalendar = None):
        self.cfg = market_cfg
        self.calendar = calendar or NSECalendar()
        self.pre_open_start = _parse_hhmm(market_cfg.pre_open_start)
        self.session_open = _parse_hhmm(market_cfg.session_open)
        self.no_new_entry_after = _parse_hhmm(market_cfg.no_new_entry_after)
        self.square_off = _parse_hhmm(market_cfg.square_off)
        self.session_close = _parse_hhmm(market_cfg.session_close)

    def state_at(self, dt: datetime) -> MarketState:
        """Return the session state for a tz-aware IST datetime."""
        if not self.calendar.is_trading_day(dt.date()):
            return MarketState.CLOSED
        t = dt.timetz().replace(tzinfo=None) if dt.tzinfo else dt.time()
        if t < self.pre_open_start:
            return MarketState.CLOSED
        if t < self.session_open:
            return MarketState.PRE_OPEN
        if t < self.square_off:
            return MarketState.OPEN
        if t < self.session_close:
            return MarketState.SQUARE_OFF
        return MarketState.CLOSED

    def can_enter(self, dt: datetime) -> bool:
        """True only during OPEN and before the no-new-entry cutoff."""
        if self.state_at(dt) != MarketState.OPEN:
            return False
        t = dt.time()
        return t < self.no_new_entry_after

    def is_square_off_time(self, dt: datetime) -> bool:
        """True once the forced square-off time has arrived (and market open that day)."""
        if not self.calendar.is_trading_day(dt.date()):
            return False
        return dt.time() >= self.square_off
