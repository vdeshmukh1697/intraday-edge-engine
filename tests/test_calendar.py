"""Tests for the NSE calendar and session state machine (PLAN §3.4)."""

from datetime import date, datetime

import pytz

from signal_engine.config import MarketConfig
from signal_engine.domain.enums import MarketState
from signal_engine.market.calendar import NSECalendar
from signal_engine.market.session import MarketSession

IST = pytz.timezone("Asia/Kolkata")


def _dt(y, m, d, hh, mm):
    return IST.localize(datetime(y, m, d, hh, mm))


def test_calendar_weekend_and_holiday():
    cal = NSECalendar()
    assert cal.is_trading_day(date(2025, 6, 23)) is True   # Monday
    assert cal.is_trading_day(date(2025, 6, 21)) is False  # Saturday
    assert cal.is_trading_day(date(2025, 6, 22)) is False  # Sunday
    assert cal.is_holiday(date(2025, 8, 15)) is True       # Independence Day
    assert cal.is_trading_day(date(2025, 8, 15)) is False


def test_session_states_across_the_day():
    s = MarketSession(MarketConfig())
    d = (2025, 6, 23)  # Monday, trading day
    assert s.state_at(_dt(*d, 8, 0)) == MarketState.CLOSED
    assert s.state_at(_dt(*d, 9, 5)) == MarketState.PRE_OPEN
    assert s.state_at(_dt(*d, 9, 20)) == MarketState.OPEN
    assert s.state_at(_dt(*d, 14, 0)) == MarketState.OPEN
    assert s.state_at(_dt(*d, 15, 25)) == MarketState.SQUARE_OFF
    assert s.state_at(_dt(*d, 15, 45)) == MarketState.CLOSED


def test_holiday_is_closed_all_day():
    s = MarketSession(MarketConfig())
    assert s.state_at(_dt(2025, 8, 15, 10, 0)) == MarketState.CLOSED


def test_can_enter_window():
    s = MarketSession(MarketConfig())
    d = (2025, 6, 23)
    assert s.can_enter(_dt(*d, 10, 0)) is True
    assert s.can_enter(_dt(*d, 14, 59)) is True
    assert s.can_enter(_dt(*d, 15, 1)) is False   # past no_new_entry_after (15:00)
    assert s.can_enter(_dt(*d, 9, 10)) is False    # pre-open


def test_square_off_flag():
    s = MarketSession(MarketConfig())
    d = (2025, 6, 23)
    assert s.is_square_off_time(_dt(*d, 15, 19)) is False
    assert s.is_square_off_time(_dt(*d, 15, 20)) is True
    assert s.is_square_off_time(_dt(*d, 15, 35)) is True
