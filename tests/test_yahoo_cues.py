"""Offline, deterministic tests for YahooCuesProvider.

No live network: a fake ``quote_fn`` (dict lookup) is injected so behaviour is
fully controlled and deterministic.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from signal_engine.premarket.models import GlobalCues
from signal_engine.premarket.yahoo_cues import (
    ADR_TICKER_MAP,
    ASIA_TICKER,
    BRENT_TICKER,
    GIFT_NIFTY_TICKER,
    GOLD_TICKER,
    US_TICKER,
    USDINR_TICKER,
    YahooCuesProvider,
)

DAY = date(2026, 6, 23)

FAKE_QUOTES = {
    US_TICKER: 0.8,
    ASIA_TICKER: 0.4,
    GIFT_NIFTY_TICKER: 0.5,
    USDINR_TICKER: -0.1,
    BRENT_TICKER: 1.2,
    GOLD_TICKER: 0.3,
    "INFY": 1.1,
    "IBN": 0.6,
    "HDB": 0.5,
    "WIT": -0.2,
}


def make_quote_fn(quotes):
    def quote_fn(ticker):
        return quotes[ticker]

    return quote_fn


def test_get_cues_maps_each_field():
    provider = YahooCuesProvider(quote_fn=make_quote_fn(FAKE_QUOTES))
    cues = provider.get_cues(DAY)

    assert cues.us_pct == 0.8
    assert cues.asia_pct == 0.4
    assert cues.gift_nifty_pct == 0.5
    assert cues.usdinr_pct == -0.1
    assert cues.brent_pct == 1.2
    assert cues.gold_pct == 0.3


def test_adr_moves_default_symbols():
    provider = YahooCuesProvider(quote_fn=make_quote_fn(FAKE_QUOTES))
    cues = provider.get_cues(DAY)

    assert cues.adr_moves == {
        "INFY": 1.1,
        "ICICIBANK": 0.6,
        "HDFCBANK": 0.5,
        "WIPRO": -0.2,
    }


def test_quote_fn_raising_for_one_ticker_is_resilient():
    def quote_fn(ticker):
        if ticker == US_TICKER:
            raise RuntimeError("boom")
        return FAKE_QUOTES[ticker]

    provider = YahooCuesProvider(quote_fn=quote_fn)
    cues = provider.get_cues(DAY)

    assert cues.us_pct == 0.0  # failed ticker degrades to 0.0
    # others still correct
    assert cues.asia_pct == 0.4
    assert cues.gift_nifty_pct == 0.5
    assert cues.usdinr_pct == -0.1
    assert cues.brent_pct == 1.2
    assert cues.gold_pct == 0.3


def test_adr_failure_is_resilient():
    def quote_fn(ticker):
        if ticker == "INFY":
            raise ValueError("no data")
        return FAKE_QUOTES[ticker]

    provider = YahooCuesProvider(quote_fn=quote_fn)
    cues = provider.get_cues(DAY)

    assert cues.adr_moves["INFY"] == 0.0
    assert cues.adr_moves["ICICIBANK"] == 0.6


def test_adr_symbols_subset():
    provider = YahooCuesProvider(
        adr_symbols=["INFY"], quote_fn=make_quote_fn(FAKE_QUOTES)
    )
    cues = provider.get_cues(DAY)

    assert cues.adr_moves == {"INFY": 1.1}


def test_unknown_adr_symbol_is_skipped():
    provider = YahooCuesProvider(
        adr_symbols=["INFY", "RELIANCE"], quote_fn=make_quote_fn(FAKE_QUOTES)
    )
    cues = provider.get_cues(DAY)

    # RELIANCE has no ADR ticker -> skipped silently.
    assert cues.adr_moves == {"INFY": 1.1}


def test_returns_globalcues_with_finite_floats():
    provider = YahooCuesProvider(quote_fn=make_quote_fn(FAKE_QUOTES))
    cues = provider.get_cues(DAY)

    assert isinstance(cues, GlobalCues)
    for value in (
        cues.gift_nifty_pct,
        cues.us_pct,
        cues.asia_pct,
        cues.usdinr_pct,
        cues.brent_pct,
        cues.gold_pct,
    ):
        assert isinstance(value, float)
        assert math.isfinite(value)
    for value in cues.adr_moves.values():
        assert isinstance(value, float)
        assert math.isfinite(value)


def test_values_are_rounded_to_2dp():
    quotes = dict(FAKE_QUOTES)
    quotes[US_TICKER] = 0.123456
    provider = YahooCuesProvider(quote_fn=make_quote_fn(quotes))
    cues = provider.get_cues(DAY)

    assert cues.us_pct == 0.12


def test_default_adr_symbols_match_map_keys():
    provider = YahooCuesProvider(quote_fn=make_quote_fn(FAKE_QUOTES))
    assert provider.adr_symbols == list(ADR_TICKER_MAP.keys())
