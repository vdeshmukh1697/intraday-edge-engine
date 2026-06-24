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


# -- D1: gate-before-advisor (actionable flag) ------------------------------------------
def test_non_actionable_new_setup_is_suppressed_and_not_latched():
    """A symbol we can't enter must NOT emit a fresh-looking NEW alert, and must stay free to
    fire NEW once it becomes actionable (no silent latching)."""
    a = LiveAdvisor()
    assert a.update("X", _plan(), actionable=False) is None   # gate closed -> no NEW alert
    # Not latched: when it later becomes actionable, it fires the NEW alert.
    assert "NEW LONG" in a.update("X", _plan(), actionable=True)


def test_non_actionable_does_not_suppress_rerate_on_tracked_symbol():
    """Once tracked, a thesis change (reversal) on a held symbol is flagged even if the gate
    is closed for fresh entries."""
    a = LiveAdvisor()
    a.update("X", _plan(direction=Direction.LONG))            # now tracked
    msg = a.update("X", _plan(direction=Direction.SHORT), actionable=False)
    assert msg is not None and "REVERSAL" in msg


# -- D2: debounce / hysteresis ----------------------------------------------------------
from datetime import timedelta  # noqa: E402


def _plan_ts(ts, **kw):
    p = _plan(**kw)
    return TradePlan(
        symbol=p.symbol, ts=ts, direction=p.direction, strategy=p.strategy, entry=p.entry,
        stop_loss=p.stop_loss, stop_pct=p.stop_pct, targets=p.targets, target_pcts=p.target_pcts,
        expected_move_pct=p.expected_move_pct, risk_reward=p.risk_reward,
        cost_to_break_even_pct=p.cost_to_break_even_pct, confidence=p.confidence,
    )


def test_debounce_suppresses_echo_within_window():
    """Inside min_realert_seconds, a non-material re-rate with tiny entry drift is squelched."""
    a = LiveAdvisor(min_realert_seconds=180, entry_band_bps=25)
    a.update("X", _plan_ts(TS, t1_pct=1.0, entry=100.0))                       # NEW
    # 60s later, entry drifts 2bps (< 25), target unchanged -> echo suppressed.
    msg = a.update("X", _plan_ts(TS + timedelta(seconds=60), t1_pct=1.05, entry=100.02))
    assert msg is None


def test_debounce_does_not_suppress_after_window():
    a = LiveAdvisor(min_realert_seconds=180, entry_band_bps=25, conf_delta=12.0)
    a.update("X", _plan_ts(TS, t1_pct=1.0, conf=70))
    # After the window a conviction jump surfaces normally.
    msg = a.update("X", _plan_ts(TS + timedelta(seconds=200), t1_pct=1.0, conf=85))
    assert msg is not None and "conviction" in msg


def test_debounce_exempts_reversal():
    """REVERSAL is exempt from debounce even within the window."""
    a = LiveAdvisor(min_realert_seconds=180, entry_band_bps=25)
    a.update("X", _plan_ts(TS, direction=Direction.LONG))
    msg = a.update("X", _plan_ts(TS + timedelta(seconds=10), direction=Direction.SHORT))
    assert msg is not None and "REVERSAL" in msg


def test_debounce_exempts_material_target_move():
    """A hard-material target move is surfaced even inside the debounce window."""
    a = LiveAdvisor(min_realert_seconds=180, entry_band_bps=25)
    a.update("X", _plan_ts(TS, t1_pct=1.0))
    msg = a.update("X", _plan_ts(TS + timedelta(seconds=30), t1_pct=3.0))  # +1->+3 material
    assert msg is not None and "strengthening" in msg


def test_debounce_off_by_default():
    """Default config (no debounce args) keeps legacy behaviour: small drift still squelched
    only by the material thresholds, not by a time window — i.e. a conviction jump fires."""
    a = LiveAdvisor()
    a.update("X", _plan_ts(TS, conf=70))
    msg = a.update("X", _plan_ts(TS + timedelta(seconds=5), conf=90))
    assert msg is not None and "conviction" in msg
