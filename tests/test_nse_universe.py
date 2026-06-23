"""Tests for the real NSE universe provider — offline via injected fetchers."""

from __future__ import annotations

from signal_engine.universe.nse import (
    NSEUniverseProvider,
    _estimate_spread_pct,
    load_nse_equity_symbols,
)

# NSE's CSV carries leading spaces in headers (e.g. ' SERIES') — mirror that here.
SAMPLE_CSV = (
    "SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE\n"
    "RELIANCE,Reliance Industries, EQ, 29-NOV-1995, 10\n"
    "TCS,Tata Consultancy, EQ, 25-AUG-2004, 1\n"
    "SOMEBOND,Some Bond Ltd, BE, 01-JAN-2020, 10\n"
    "INFY,Infosys Limited, EQ, 08-FEB-1995, 5\n"
)


def test_load_nse_equity_symbols_filters_eq_only():
    syms = load_nse_equity_symbols(csv_fetch=lambda: SAMPLE_CSV)
    assert syms == ["RELIANCE", "TCS", "INFY"]  # BE series dropped


def test_estimate_spread_monotonic_and_clamped():
    assert _estimate_spread_pct(0) == 5.0  # no data -> wide -> fails screen
    tight = _estimate_spread_pct(1000.0)
    wide = _estimate_spread_pct(30.0)
    assert tight < wide  # more turnover -> tighter spread
    assert 0.01 <= tight <= 2.0


def test_build_enriches_with_real_metrics():
    metrics = {
        "RELIANCE": {"last_price": 2900.0, "avg_daily_turnover_cr": 800.0},
        "TCS": {"last_price": 3800.0, "avg_daily_turnover_cr": 300.0},
        # INFY intentionally missing -> should be zeroed and rejectable.
    }
    uni = NSEUniverseProvider.build(
        csv_fetch=lambda: SAMPLE_CSV,
        metrics_fetch=lambda syms: metrics,
    )
    metas = {m.symbol: m for m in uni.instruments()}
    assert set(metas) == {"RELIANCE", "TCS", "INFY"}
    assert metas["RELIANCE"].avg_daily_turnover_cr == 800.0
    assert metas["RELIANCE"].last_price == 2900.0
    # Missing-metric symbol is zeroed -> wide spread -> will fail the liquidity screen.
    assert metas["INFY"].avg_daily_turnover_cr == 0.0
    assert metas["INFY"].est_spread_pct == 5.0


def test_build_respects_limit():
    uni = NSEUniverseProvider.build(
        limit=2,
        csv_fetch=lambda: SAMPLE_CSV,
        metrics_fetch=lambda syms: {},
    )
    assert uni.symbols() == ["RELIANCE", "TCS"]
