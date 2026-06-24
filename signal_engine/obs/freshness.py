"""Data-freshness fail-safe (PLAN §9.3).

'If data is stale or the connection drops, suppress signals and alert loudly — never trade
on stale data.' This guard tracks the timestamp of the last received market data and reports
staleness against a wall clock, so the engine can refuse to surface signals on a dead feed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from signal_engine.market.clock import Clock, RealClock


class FreshnessGuard:
    def __init__(self, max_staleness_seconds: float = 30.0, clock: Optional[Clock] = None):
        self.max_staleness_seconds = max_staleness_seconds
        self.clock = clock or RealClock()
        self._last_data_ts: Optional[datetime] = None

    def mark(self, data_ts: datetime) -> None:
        """Record the timestamp of freshly received data (tick/bar)."""
        self._last_data_ts = data_ts

    def seconds_since(self, now: Optional[datetime] = None) -> Optional[float]:
        if self._last_data_ts is None:
            return None
        now = now or self.clock.now()
        return (now - self._last_data_ts).total_seconds()

    def is_stale(self, now: Optional[datetime] = None) -> bool:
        """True if no data yet, or the last data is older than the threshold."""
        gap = self.seconds_since(now)
        if gap is None:
            return True  # no data received -> treat as stale (fail safe)
        return gap > self.max_staleness_seconds
