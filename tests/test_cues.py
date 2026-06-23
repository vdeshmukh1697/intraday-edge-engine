"""Tests for the global-cues providers (PLAN §3.8)."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from signal_engine.premarket.cues import (
    DEFAULT_ADR_SYMBOLS,
    MockGlobalCuesProvider,
    YFinanceCuesProvider,
)
from signal_engine.premarket.models import GlobalCues


def _all_pcts(cues: GlobalCues):
    return [
        cues.gift_nifty_pct,
        cues.us_pct,
        cues.asia_pct,
        cues.usdinr_pct,
        cues.brent_pct,
        cues.gold_pct,
    ]


def test_same_seed_same_day_identical():
    a = MockGlobalCuesProvider(seed=42)
    b = MockGlobalCuesProvider(seed=42)
    day = date(2026, 6, 23)
    ca = a.get_cues(day)
    cb = b.get_cues(day)
    assert ca == cb
    assert ca.adr_moves == cb.adr_moves


def test_different_days_differ():
    p = MockGlobalCuesProvider(seed=42)
    c1 = p.get_cues(date(2026, 6, 23))
    c2 = p.get_cues(date(2026, 6, 24))
    assert c1 != c2


def test_adr_moves_keys_and_types():
    p = MockGlobalCuesProvider(seed=7)
    cues = p.get_cues(date(2026, 1, 5))
    assert set(cues.adr_moves.keys()) == set(DEFAULT_ADR_SYMBOLS)
    assert len(cues.adr_moves) == len(DEFAULT_ADR_SYMBOLS)
    for v in cues.adr_moves.values():
        assert isinstance(v, float)


def test_custom_adr_symbols():
    syms = ["WIPRO", "SBIN"]
    p = MockGlobalCuesProvider(seed=1, adr_symbols=syms)
    cues = p.get_cues(date(2026, 3, 2))
    assert set(cues.adr_moves.keys()) == set(syms)


def test_gift_correlated_with_us():
    p = MockGlobalCuesProvider(seed=42)
    start = date(2026, 1, 1).toordinal()
    us = []
    gift = []
    for i in range(200):
        cues = p.get_cues(date.fromordinal(start + i))
        us.append(cues.us_pct)
        gift.append(cues.gift_nifty_pct)
    corr = np.corrcoef(np.array(us), np.array(gift))[0, 1]
    assert corr > 0.5


def test_all_fields_finite_and_rounded():
    p = MockGlobalCuesProvider(seed=99)
    start = date(2026, 1, 1).toordinal()
    for i in range(50):
        cues = p.get_cues(date.fromordinal(start + i))
        for v in _all_pcts(cues):
            assert isinstance(v, float)
            assert math.isfinite(v)
            assert round(v, 2) == v
        for v in cues.adr_moves.values():
            assert math.isfinite(v)
            assert round(v, 2) == v


def test_yfinance_provider_gated():
    p = YFinanceCuesProvider()
    with pytest.raises(RuntimeError):
        p.get_cues(date(2026, 6, 23))
