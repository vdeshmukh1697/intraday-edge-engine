"""Tests for the live re-rating advisor — deterministic change detection."""

from __future__ import annotations

from datetime import datetime

import pytz

from signal_engine.domain.enums import Direction
from signal_engine.domain.models import TradePlan
from signal_engine.engine.advisor import LiveAdvisor

IST = pytz.timezone("Asia/Kolkata")
TS = IST.localize(datetime(2026, 6, 24, 10, 0))


def _plan(direction=Direction.LONG, entry=100.0, stop_pct=0.5, t1_pct=1.0, conf=70.0):
    return TradePlan(
        symbol="X", ts=TS, direction=direction, strategy="s", entry=entry,
        stop_loss=entry * (1 - stop_pct / 100), stop_pct=stop_pct,
        targets=[entry * (1 + t1_pct / 100)], target_pcts=[t1_pct],
        expected_move_pct=t1_pct, risk_reward=t1_pct / stop_pct,
        cost_to_break_even_pct=0.1, confidence=conf,
    )


def test_new_setup_then_no_change():
    a = LiveAdvisor()
    assert "NEW LONG" in a.update("X", _plan())
    assert a.update("X", _plan()) is None  # identical -> no alert


def test_target_expansion_alerts_with_direction():
    a = LiveAdvisor()
    a.update("X", _plan(t1_pct=1.0))
    msg = a.update("X", _plan(t1_pct=3.0))   # 1% -> 3% (the user's example)
    assert msg is not None
    assert "+1.00% → +3.00%" in msg and "strengthening" in msg
    assert "📈" in msg


def test_target_contraction_alerts():
    a = LiveAdvisor()
    a.update("X", _plan(t1_pct=1.0))
    msg = a.update("X", _plan(t1_pct=0.5))   # 1% -> 0.5%
    assert "cooling" in msg and "📉" in msg


def test_small_target_move_is_not_material():
    a = LiveAdvisor()
    a.update("X", _plan(t1_pct=1.0))
    assert a.update("X", _plan(t1_pct=1.1)) is None  # below abs + rel thresholds


def test_direction_flip_is_a_reversal():
    a = LiveAdvisor()
    a.update("X", _plan(direction=Direction.LONG))
    msg = a.update("X", _plan(direction=Direction.SHORT))
    assert "REVERSAL" in msg and "LONG → SHORT" in msg


def test_conviction_jump_alerts():
    a = LiveAdvisor()
    a.update("X", _plan(conf=64))
    msg = a.update("X", _plan(conf=85))
    assert "conviction rising" in msg and "64 → 85" in msg


def test_invalidation_when_setup_disappears():
    a = LiveAdvisor()
    a.update("X", _plan())
    msg = a.update("X", None)
    assert "invalidated" in msg
    assert a.update("X", None) is None  # already cleared


def test_on_price_fires_once_near_target():
    a = LiveAdvisor()
    a.update("X", _plan(entry=100.0, t1_pct=1.0))     # target at 101.0
    assert a.on_price("X", 100.2) is None              # only 20% of the way
    msg = a.on_price("X", 100.8)                        # 80% of the way (>=75%)
    assert msg is not None and "approaching T1" in msg
    assert a.on_price("X", 100.9) is None               # already flagged, no repeat
