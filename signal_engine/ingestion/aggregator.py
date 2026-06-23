"""Tick -> bar aggregation with closed-bar discipline (PLAN §3.3).

A ``BarAggregator`` consumes ticks (carrying cumulative day volume) and emits a CLOSED
``Bar`` when the timeframe boundary rolls over. The still-forming bar is available via
``current_partial()`` flagged ``is_provisional=True`` — the signal engine MUST ignore it.

Tick volume is cumulative-for-the-day (resets to 0 each session), so a bar's volume is
the difference between the cumulative volume at the end of this bucket and the previous.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from signal_engine.domain.models import Bar, Tick


def _floor_to_bucket(ts: datetime, minutes: int) -> datetime:
    """Floor a timestamp down to the start of its timeframe bucket."""
    discard = (ts.minute % minutes) * 60 + ts.second
    floored = ts - timedelta(seconds=discard, microseconds=ts.microsecond)
    return floored


class BarAggregator:
    def __init__(self, symbol: str, timeframe_minutes: int = 1):
        self.symbol = symbol
        self.tf = timeframe_minutes
        self.timeframe = f"{timeframe_minutes}m"
        self._bucket_start: Optional[datetime] = None
        self._o = self._h = self._low = self._c = None
        self._bucket_end_cum: int = 0      # cumulative volume at last tick of current bucket
        self._prev_bucket_end_cum: int = 0  # cumulative at end of previous closed bucket

    def add_tick(self, tick: Tick) -> Optional[Bar]:
        """Feed a tick. Returns a CLOSED bar if this tick rolled the bucket, else None."""
        bucket = _floor_to_bucket(tick.ts, self.tf)
        closed: Optional[Bar] = None

        if self._bucket_start is None:
            self._start_bucket(bucket, tick)
            return None

        if bucket > self._bucket_start:
            # New bucket -> close the previous one.
            closed = self._close_current()
            self._start_bucket(bucket, tick)
            return closed

        # Same bucket -> update aggregates.
        self._h = max(self._h, tick.ltp)
        self._low = min(self._low, tick.ltp)
        self._c = tick.ltp
        self._bucket_end_cum = tick.volume
        return None

    def _start_bucket(self, bucket: datetime, tick: Tick) -> None:
        self._bucket_start = bucket
        self._o = self._h = self._low = self._c = tick.ltp
        self._bucket_end_cum = tick.volume

    def _close_current(self) -> Bar:
        vol = max(0, self._bucket_end_cum - self._prev_bucket_end_cum)
        bar = Bar(
            symbol=self.symbol,
            ts=self._bucket_start,
            open=self._o,
            high=self._h,
            low=self._low,
            close=self._c,
            volume=int(vol),
            timeframe=self.timeframe,
            is_provisional=False,
        )
        self._prev_bucket_end_cum = self._bucket_end_cum
        return bar

    def current_partial(self) -> Optional[Bar]:
        """The still-forming bar (provisional). Engine ignores provisional bars."""
        if self._bucket_start is None:
            return None
        vol = max(0, self._bucket_end_cum - self._prev_bucket_end_cum)
        return Bar(
            symbol=self.symbol,
            ts=self._bucket_start,
            open=self._o,
            high=self._h,
            low=self._low,
            close=self._c,
            volume=int(vol),
            timeframe=self.timeframe,
            is_provisional=True,
        )

    def flush(self) -> Optional[Bar]:
        """Close and return the final forming bar (call at end of feed)."""
        if self._bucket_start is None:
            return None
        bar = self._close_current()
        self._bucket_start = None
        return bar


def roll_up(bars: List[Bar], minutes: int) -> List[Bar]:
    """Aggregate a list of 1-minute bars into ``minutes``-minute bars (PLAN §3.3)."""
    if not bars:
        return []
    out: List[Bar] = []
    symbol = bars[0].symbol
    cur_start = None
    o = h = low_ = c = None
    vol = 0
    for b in bars:
        bucket = _floor_to_bucket(b.ts, minutes)
        if cur_start is None:
            cur_start, o, h, low_, c, vol = bucket, b.open, b.high, b.low, b.close, b.volume
        elif bucket > cur_start:
            out.append(Bar(symbol, cur_start, o, h, low_, c, vol, f"{minutes}m", False))
            cur_start, o, h, low_, c, vol = bucket, b.open, b.high, b.low, b.close, b.volume
        else:
            h = max(h, b.high)
            low_ = min(low_, b.low)
            c = b.close
            vol += b.volume
    out.append(Bar(symbol, cur_start, o, h, low_, c, vol, f"{minutes}m", False))
    return out
