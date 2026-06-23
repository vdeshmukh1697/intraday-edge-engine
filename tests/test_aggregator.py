"""Tests for tick->bar aggregation and roll-up (PLAN §3.3)."""

from datetime import date, datetime

import pytz

from signal_engine.data.synthetic import bars_to_ticks, generate_session
from signal_engine.domain.models import Tick
from signal_engine.ingestion.aggregator import BarAggregator, roll_up

IST = pytz.timezone("Asia/Kolkata")


def test_roundtrip_bars_ticks_bars_exact():
    """bars -> ticks -> aggregator must reconstruct the original OHLCV exactly."""
    df = generate_session("TEST", date(2025, 6, 23), seed=1, regime="trend_up")
    ticks = bars_to_ticks(df)
    agg = BarAggregator("TEST", 1)
    bars = [b for t in ticks if (b := agg.add_tick(t)) is not None]
    last = agg.flush()
    if last:
        bars.append(last)

    assert len(bars) == len(df)
    for i, b in enumerate(bars):
        row = df.iloc[i]
        assert abs(b.open - row.open) < 1e-6
        assert abs(b.high - row.high) < 1e-6
        assert abs(b.low - row.low) < 1e-6
        assert abs(b.close - row.close) < 1e-6
    # Cumulative volume is conserved.
    assert sum(b.volume for b in bars) == int(df.volume.sum())


def test_closed_bar_only_on_rollover():
    """A new bar is emitted only when the minute bucket changes; partial is provisional."""
    agg = BarAggregator("X", 1)
    t0 = IST.localize(datetime(2025, 6, 23, 9, 15, 0))
    assert agg.add_tick(Tick("X", t0, 100.0, 10)) is None
    assert agg.add_tick(Tick("X", t0.replace(second=30), 101.0, 20)) is None
    partial = agg.current_partial()
    assert partial is not None and partial.is_provisional is True
    assert partial.high == 101.0
    # tick in next minute closes the first bar
    t1 = IST.localize(datetime(2025, 6, 23, 9, 16, 0))
    closed = agg.add_tick(Tick("X", t1, 102.0, 25))
    assert closed is not None and closed.is_provisional is False
    assert closed.open == 100.0 and closed.high == 101.0 and closed.low == 100.0
    assert closed.volume == 20  # cum 20 at close of minute, started from 0


def test_rollup_5m_counts_and_ohlc():
    df = generate_session("R", date(2025, 6, 23), seed=2)
    ticks = bars_to_ticks(df)
    agg = BarAggregator("R", 1)
    bars = [b for t in ticks if (b := agg.add_tick(t)) is not None]
    bars.append(agg.flush())
    r5 = roll_up([b for b in bars if b], 5)
    assert len(r5) == 75  # 375 / 5
    # First 5m bar open == first 1m open; high == max of first five 1m highs.
    assert abs(r5[0].open - bars[0].open) < 1e-6
    assert abs(r5[0].high - max(b.high for b in bars[:5])) < 1e-6
    assert abs(r5[0].low - min(b.low for b in bars[:5])) < 1e-6
