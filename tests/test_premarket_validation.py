"""Deterministic tests for pre-market open-validation (PLAN §4.8)."""

from __future__ import annotations

from signal_engine.domain.enums import Direction
from signal_engine.premarket.models import GapBias, IndexOutlook, PreMarketPick, RiskTone
from signal_engine.premarket.validation import (
    _sign,
    validate_index,
    validate_open,
    validate_pick,
)


def test_sign_helper():
    assert _sign(0) == 0
    assert _sign(0.0) == 0
    assert _sign(1.3) == 1
    assert _sign(-0.4) == -1


def test_gap_up_fully_confirmed():
    r = validate_open(0.6, +1, 0.8, 1.4)
    assert r.gap_happened is True
    assert r.direction_correct is True
    assert r.volume_confirmed is True
    assert "confirmed" in r.note


def test_wrong_direction():
    r = validate_open(0.6, +1, -0.5, 1.4)
    assert r.gap_happened is False
    assert r.direction_correct is False


def test_gap_too_small_but_direction_matches():
    r = validate_open(0.6, +1, 0.1, 1.4)
    assert r.gap_happened is False        # below gap_min
    assert r.direction_correct is True    # sign still matches
    assert "did not materialise" in r.note


def test_low_volume_fade():
    r = validate_open(0.6, +1, 0.8, 0.8)
    assert r.gap_happened is True
    assert r.direction_correct is True
    assert r.volume_confirmed is False
    assert "fade" in r.note.lower() or "low" in r.note.lower()


def test_flat_prediction():
    r = validate_open(0.0, 0, 0.8, 1.4)
    assert r.gap_happened is False        # predicted_gap_pct == 0
    assert r.direction_correct is False   # dir sign == 0


def test_actual_gap_exactly_zero():
    r = validate_open(0.6, +1, 0.0, 1.4)
    assert r.gap_happened is False        # sign(0) == 0 != predicted +1
    assert r.direction_correct is False


def test_validate_index_gap_up_all_true():
    outlook = IndexOutlook(
        expected_gap_pct=0.6,
        gap_bias=GapBias.GAP_UP,
        risk_tone=RiskTone.RISK_ON,
    )
    r = validate_index(outlook, 0.8, 1.4)
    assert r.gap_happened is True
    assert r.direction_correct is True
    assert r.volume_confirmed is True


def test_validate_index_gap_down():
    outlook = IndexOutlook(
        expected_gap_pct=-0.6,
        gap_bias=GapBias.GAP_DOWN,
        risk_tone=RiskTone.RISK_OFF,
    )
    r = validate_index(outlook, -0.7, 1.3)
    assert r.gap_happened is True
    assert r.direction_correct is True
    assert r.volume_confirmed is True


def test_validate_pick_long_direction_correct():
    pick = PreMarketPick(
        symbol="RELIANCE",
        bias=Direction.LONG,
        setup="gap-up momentum",
        expected_gap_pct=0.6,
        confidence=70.0,
        catalyst="strong ADR",
        score=2.1,
    )
    r = validate_pick(pick, 0.8)            # default vol ratio 1.0
    assert r.direction_correct is True
    assert r.gap_happened is True
    assert r.volume_confirmed is False      # 1.0 < vol_confirm 1.2


def test_kwargs_passthrough_gap_min():
    # With a lower gap_min, a 0.1% gap now counts.
    r = validate_open(0.6, +1, 0.1, 1.4, gap_min=0.05)
    assert r.gap_happened is True
