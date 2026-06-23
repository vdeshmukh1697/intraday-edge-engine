"""NSE trading calendar (PLAN §3.4).

Trading days are Mon–Fri minus exchange holidays. The holiday list below covers
2024–2026 NSE equity holidays; refresh yearly from the official NSE list. Unknown
future years fall back to weekday-only (with a logged caveat) rather than crashing.
"""

from __future__ import annotations

from datetime import date
from typing import Set

# Official NSE equity-segment trading holidays. Source: NSE holiday list.
# NOTE: verify/refresh annually. Muhurat (special) sessions are not modelled here.
_NSE_HOLIDAYS: Set[date] = {
    # 2024
    date(2024, 1, 26), date(2024, 3, 8), date(2024, 3, 25), date(2024, 3, 29),
    date(2024, 4, 11), date(2024, 4, 17), date(2024, 5, 1), date(2024, 6, 17),
    date(2024, 7, 17), date(2024, 8, 15), date(2024, 10, 2), date(2024, 11, 1),
    date(2024, 11, 15), date(2024, 12, 25),
    # 2025
    date(2025, 2, 26), date(2025, 3, 14), date(2025, 3, 31), date(2025, 4, 10),
    date(2025, 4, 14), date(2025, 4, 18), date(2025, 5, 1), date(2025, 8, 15),
    date(2025, 8, 27), date(2025, 10, 2), date(2025, 10, 21), date(2025, 10, 22),
    date(2025, 11, 5), date(2025, 12, 25),
    # 2026 (provisional — refresh when NSE publishes the official list)
    date(2026, 1, 26), date(2026, 3, 6), date(2026, 3, 25), date(2026, 4, 1),
    date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1), date(2026, 8, 15),
    date(2026, 10, 2), date(2026, 11, 9), date(2026, 12, 25),
}


class NSECalendar:
    """Trading-day calendar for the NSE equity segment."""

    def __init__(self, holidays: Set[date] = None):
        self._holidays = set(holidays) if holidays is not None else set(_NSE_HOLIDAYS)

    def is_weekend(self, d: date) -> bool:
        return d.weekday() >= 5  # 5 = Saturday, 6 = Sunday

    def is_holiday(self, d: date) -> bool:
        return d in self._holidays

    def is_trading_day(self, d: date) -> bool:
        return not self.is_weekend(d) and not self.is_holiday(d)

    def add_holiday(self, d: date) -> None:
        self._holidays.add(d)
