"""End-to-end pre-market briefing tests (PLAN §4.8)."""

from datetime import date

from signal_engine.config import load_config
from signal_engine.domain.enums import Direction
from signal_engine.market.calendar import NSECalendar
from signal_engine.premarket.briefing import build_briefing, prior_trading_day

_SYMBOLS = ["RELIANCE", "HDFCBANK", "INFY", "TCS", "ICICIBANK"]


def test_prior_trading_day_skips_non_trading():
    cal = NSECalendar()
    # 2025-08-15 is Independence Day (holiday); prior trading day is 14 Aug (Thu).
    assert prior_trading_day(date(2025, 8, 16), cal) == date(2025, 8, 14)
    # Monday's prior trading day is the previous Friday.
    assert prior_trading_day(date(2025, 6, 23), cal) == date(2025, 6, 20)


def test_briefing_structure():
    cfg = load_config()
    b = build_briefing(cfg, symbols=_SYMBOLS, day=date(2025, 6, 23), seed=5)
    assert b.day == date(2025, 6, 23)
    assert b.index_outlook is not None
    # picks are a subset of the requested symbols, all actionable (LONG/SHORT), conf in range
    syms = {p.symbol for p in b.picks}
    assert syms.issubset(set(_SYMBOLS))
    for p in b.picks:
        assert p.bias in (Direction.LONG, Direction.SHORT)
        assert 0.0 < p.confidence <= 100.0
        assert p.setup in ("gap-up momentum", "gap-down momentum", "momentum", "reversal")


def test_briefing_picks_sorted_by_confidence():
    cfg = load_config()
    b = build_briefing(cfg, symbols=_SYMBOLS, day=date(2025, 6, 23), seed=5)
    confs = [p.confidence for p in b.picks]
    assert confs == sorted(confs, reverse=True)


def test_briefing_deterministic():
    cfg = load_config()
    a = build_briefing(cfg, symbols=_SYMBOLS, day=date(2025, 6, 23), seed=9)
    b = build_briefing(cfg, symbols=_SYMBOLS, day=date(2025, 6, 23), seed=9)
    assert [p.symbol for p in a.picks] == [p.symbol for p in b.picks]
    assert [p.confidence for p in a.picks] == [p.confidence for p in b.picks]
    assert a.index_outlook.expected_gap_pct == b.index_outlook.expected_gap_pct


def test_top_n_respected():
    cfg = load_config()
    b = build_briefing(cfg, symbols=_SYMBOLS, day=date(2025, 6, 23), seed=5, top_n=2)
    assert len(b.picks) <= 2
