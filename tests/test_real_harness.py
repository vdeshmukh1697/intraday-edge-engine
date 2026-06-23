"""Tests for the real-data scan harness — offline via injected intraday_fetch.

The harness is identical to the synthetic one except the survivor histories and news
come from the *configured/real* providers. Here we inject a fake intraday fetcher that
returns synthetic sessions, so the full screen -> fetch -> scan -> rank chain is
exercised with zero network and no live-market dependency (ground rule).
"""

from __future__ import annotations

from datetime import date, time

from signal_engine.config import load_config
from signal_engine.data.synthetic import generate_session
from signal_engine.scan.real_harness import run_real_scan
from signal_engine.universe.nse import NSEUniverseProvider

DAY = date(2025, 6, 23)


def _universe():
    """Two liquid names (pass screen) + one illiquid (rejected by static screen)."""
    metrics = {
        "RELIANCE": {"last_price": 2900.0, "avg_daily_turnover_cr": 800.0},
        "TCS": {"last_price": 3800.0, "avg_daily_turnover_cr": 400.0},
        "PENNY": {"last_price": 8.0, "avg_daily_turnover_cr": 0.5},
    }
    return NSEUniverseProvider(["RELIANCE", "TCS", "PENNY"], metrics)


def _fake_intraday(symbols):
    # Only liquid survivors are ever requested; hand back trending sessions.
    return {
        s: generate_session(s, DAY, start_price=2900.0, seed=hash(s) % 1000,
                            regime="trend_up")
        for s in symbols
    }


def test_run_real_scan_screens_then_ranks():
    cfg = load_config()
    res = run_real_scan(cfg, _universe(), DAY, with_news=False,
                        intraday_fetch=_fake_intraday)
    # Universe size is reported against the FULL universe, not just survivors.
    assert res.universe_size == 3
    # PENNY (0.5 cr turnover, ₹8) must never reach the leaderboard.
    assert all(e.plan.symbol != "PENNY" for e in res.leaderboard)
    # The liquid names produced a non-empty ranked leaderboard.
    assert len(res.leaderboard) >= 1


def test_run_real_scan_only_fetches_survivors():
    cfg = load_config()
    requested = []

    def spy_fetch(symbols):
        requested.extend(symbols)
        return _fake_intraday(symbols)

    run_real_scan(cfg, _universe(), DAY, with_news=False, intraday_fetch=spy_fetch)
    # Scan wide, fetch narrow: the illiquid PENNY is screened out before any fetch.
    assert "PENNY" not in requested
    assert set(requested) == {"RELIANCE", "TCS"}


def test_run_real_scan_respects_as_of_cutoff():
    cfg = load_config()
    res = run_real_scan(cfg, _universe(), DAY, as_of=time(10, 0),
                        with_news=False, intraday_fetch=_fake_intraday)
    # With a 10:00 cutoff the scan still runs on the truncated history.
    assert res.universe_size == 3
