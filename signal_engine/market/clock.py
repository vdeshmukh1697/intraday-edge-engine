"""Clock abstraction so the whole engine is testable without real wall-clock time.

Always returns tz-aware IST datetimes. Use ``FakeClock`` in tests/replay.
"""

from __future__ import annotations

from datetime import datetime

import pytz

IST = pytz.timezone("Asia/Kolkata")


class Clock:
    """Interface: ``now()`` returns a tz-aware IST datetime."""

    def now(self) -> datetime:  # pragma: no cover - interface
        raise NotImplementedError


class RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(IST)


class FakeClock(Clock):
    """Controllable clock for tests and historical replay."""

    def __init__(self, start: datetime):
        self._now = _ensure_ist(start)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = _ensure_ist(dt)

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)


def _ensure_ist(dt: datetime) -> datetime:
    """Attach IST if naive, else convert to IST."""
    if dt.tzinfo is None:
        return IST.localize(dt)
    return dt.astimezone(IST)
