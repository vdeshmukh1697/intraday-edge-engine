"""Tests for the Dhan history backfill loop — offline via a fake broker."""

from __future__ import annotations

from datetime import date, timedelta

import pytz

from signal_engine.data.backfill import backfill_symbol, backfill_universe
from signal_engine.domain.models import Bar
from signal_engine.storage.bars import ParquetBarStore

IST = pytz.timezone("Asia/Kolkata")


class _FakeBroker:
    """Returns a couple of bars per requested window; records the windows asked for."""

    def __init__(self):
        self.calls = []

    def historical(self, symbol, timeframe, start, end):
        self.calls.append((symbol, start.date(), end.date()))
        mid = start + (end - start) / 2
        return [
            Bar(symbol=symbol, ts=mid, open=1, high=2, low=0.5, close=1.5, volume=100),
            Bar(symbol=symbol, ts=mid + timedelta(minutes=1),
                open=1.5, high=2, low=1, close=1.8, volume=120),
        ]


def test_backfill_symbol_walks_windows_and_saves_by_year(tmp_path):
    broker = _FakeBroker()
    store = ParquetBarStore(str(tmp_path))
    end = date(2026, 6, 23)
    saved = backfill_symbol(broker, store, "RELIANCE", years=2, end=end,
                            window_days=90, sleep_fn=lambda *_: None)

    assert saved > 0
    # ~2 years / 90-day windows ≈ 8-9 requests
    assert 7 <= len(broker.calls) <= 10
    # windows walk strictly backwards and never start before the 2y floor
    floor = end - timedelta(days=int(2 * 365.25))
    assert all(s >= floor for _, s, _ in broker.calls)
    # data is round-trippable via the consolidated per-symbol loader
    hist = store.load_symbol_history("RELIANCE")
    assert hist is not None and len(hist) == saved


def test_backfill_symbol_skips_existing_year(tmp_path):
    broker = _FakeBroker()
    store = ParquetBarStore(str(tmp_path))
    end = date(2026, 6, 23)
    backfill_symbol(broker, store, "TCS", years=1, end=end, sleep_fn=lambda *_: None)
    n_files_first = len(list((tmp_path / "symbol=TCS").glob("year=*/bars.parquet")))

    # Second run with skip_existing should not error and not duplicate files.
    saved2 = backfill_symbol(broker, store, "TCS", years=1, end=end,
                             sleep_fn=lambda *_: None, skip_existing=True)
    n_files_second = len(list((tmp_path / "symbol=TCS").glob("year=*/bars.parquet")))
    assert saved2 == 0  # everything already on disk
    assert n_files_first == n_files_second


def test_backfill_retries_on_rate_limit_then_succeeds(tmp_path):
    """A throttled window must be retried (with backoff), not silently saved as empty."""
    from signal_engine.brokers.dhan import DhanRateLimitError

    class _ThrottledBroker(_FakeBroker):
        def __init__(self):
            super().__init__()
            self.hits = 0

        def historical(self, symbol, timeframe, start, end):
            self.hits += 1
            if self.hits == 1:  # first call throttled, retry succeeds
                raise DhanRateLimitError("DH-904")
            return super().historical(symbol, timeframe, start, end)

    broker = _ThrottledBroker()
    store = ParquetBarStore(str(tmp_path))
    saved = backfill_symbol(broker, store, "RELIANCE", years=1, end=date(2026, 6, 23),
                            sleep_fn=lambda *_: None)
    assert saved > 0  # recovered after the rate-limit retry, no silent gap
    assert broker.hits >= 2


def test_backfill_universe_best_effort_on_symbol_error(tmp_path):
    class _FlakyBroker(_FakeBroker):
        def historical(self, symbol, timeframe, start, end):
            if symbol == "BAD":
                raise RuntimeError("no security id")
            return super().historical(symbol, timeframe, start, end)

    store = ParquetBarStore(str(tmp_path))
    out = backfill_universe(_FlakyBroker(), store, ["RELIANCE", "BAD", "INFY"],
                            years=1, end=date(2026, 6, 23), sleep_fn=lambda *_: None)
    assert out["RELIANCE"] > 0 and out["INFY"] > 0
    assert out["BAD"] == 0  # failed symbol recorded as 0, run continued
