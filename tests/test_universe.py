"""Deterministic tests for the mock universe provider."""

from __future__ import annotations

import numpy as np

from signal_engine.universe.mock import MockUniverseProvider


def test_count_and_unique_symbols():
    p = MockUniverseProvider()
    insts = p.instruments()
    assert len(insts) == 2000
    syms = [i.symbol for i in insts]
    assert len(set(syms)) == len(syms)


def test_count_respects_n():
    p = MockUniverseProvider(n=137, seed=7)
    assert len(p.instruments()) == 137


def test_determinism_same_seed():
    a = MockUniverseProvider(seed=42).instruments()
    b = MockUniverseProvider(seed=42).instruments()
    assert a == b


def test_different_seeds_differ():
    a = MockUniverseProvider(seed=42).instruments()
    b = MockUniverseProvider(seed=43).instruments()
    assert a != b


def test_distribution_spans_ranges():
    insts = MockUniverseProvider().instruments()
    assert any(i.last_price < 20 for i in insts)          # penny stock present
    assert any(i.avg_daily_turnover_cr > 100 for i in insts)  # liquid name present
    banned = [i.is_banned for i in insts]
    assert any(banned) and not all(banned)


def test_spread_positive_and_liquidity_relationship():
    insts = MockUniverseProvider().instruments()
    assert all(i.est_spread_pct > 0 for i in insts)

    ordered = sorted(insts, key=lambda i: i.avg_daily_turnover_cr)
    q = len(ordered) // 4
    least_liquid = ordered[:q]
    most_liquid = ordered[-q:]
    mean_spread_illiquid = np.mean([i.est_spread_pct for i in least_liquid])
    mean_spread_liquid = np.mean([i.est_spread_pct for i in most_liquid])
    assert mean_spread_liquid < mean_spread_illiquid


def test_symbols_matches_instruments():
    p = MockUniverseProvider()
    syms = p.symbols()
    assert len(syms) == p.n
    assert syms == [i.symbol for i in p.instruments()]


def test_liquid_helper():
    p = MockUniverseProvider()
    liquid = p.liquid(min_turnover_cr=25.0)
    assert all(i.avg_daily_turnover_cr > 25.0 for i in liquid)
    assert 0 < len(liquid) < p.n
