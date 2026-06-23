"""Unit tests for the vwap_ema_adx strategy decision logic (PLAN §4.4)."""

from datetime import datetime

import pandas as pd
import pytz

from signal_engine.domain.enums import Direction
from signal_engine.strategies.base import StrategyContext
from signal_engine.strategies.vwap_ema_adx import VwapEmaAdxStrategy

IST = pytz.timezone("Asia/Kolkata")
_TS = IST.localize(datetime(2025, 6, 23, 10, 0))


def _ctx(features):
    return StrategyContext(
        symbol="X", ts=_TS, features=features, bars=pd.DataFrame(),
    )


def _long_features():
    return {
        "close": 105.0, "vwap": 100.0,        # above VWAP
        "ema_fast": 104.0, "ema_slow": 102.0,  # fast > slow
        "ema_fast_prev": 101.0, "ema_slow_prev": 102.0,  # fresh cross up
        "rsi": 55.0,                            # not overbought
        "adx": 30.0,                            # strong trend
        "atr": 1.0, "atr_pct": 0.95, "rvol": 2.0,  # volume confirms
        "bar_count": 60,
    }


def test_full_confluence_long_max_confidence():
    s = VwapEmaAdxStrategy()
    sig = s.on_bar(_ctx(_long_features()))
    assert sig is not None
    assert sig.direction == Direction.LONG
    # All five weighted conditions met -> 100.
    assert sig.confidence == 100.0
    assert any("EMA cross up" in r for r in sig.reasons)


def test_adx_hard_floor_suppresses():
    s = VwapEmaAdxStrategy()
    f = _long_features()
    f["adx"] = 10.0  # below hard floor (15) -> no signal at all
    assert s.on_bar(_ctx(f)) is None


def test_below_threshold_returns_none():
    s = VwapEmaAdxStrategy()
    # Only VWAP side (0.30) + RSI ok (0.10) = 40 < 60 threshold.
    f = {
        "close": 105.0, "vwap": 100.0,
        "ema_fast": 100.0, "ema_slow": 102.0,   # NOT aligned long
        "ema_fast_prev": 100.0, "ema_slow_prev": 102.0,
        "rsi": 55.0,
        "adx": 18.0,                              # >= hard floor but < adx_min(20)
        "atr": 1.0, "atr_pct": 0.95, "rvol": 1.0,  # rvol below min
        "bar_count": 60,
    }
    assert s.on_bar(_ctx(f)) is None


def test_short_confluence():
    s = VwapEmaAdxStrategy()
    f = {
        "close": 95.0, "vwap": 100.0,             # below VWAP
        "ema_fast": 96.0, "ema_slow": 98.0,        # fast < slow
        "ema_fast_prev": 99.0, "ema_slow_prev": 98.0,  # fresh cross down
        "rsi": 45.0,                               # not oversold
        "adx": 28.0, "atr": 1.0, "atr_pct": 0.95, "rvol": 1.8,
        "bar_count": 60,
    }
    sig = s.on_bar(_ctx(f))
    assert sig is not None and sig.direction == Direction.SHORT
    assert sig.confidence == 100.0


def test_nan_features_return_none():
    s = VwapEmaAdxStrategy()
    f = _long_features()
    f["adx"] = float("nan")
    assert s.on_bar(_ctx(f)) is None
