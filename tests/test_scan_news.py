"""News integration into the scan (PLAN §4.6): news changes picks + is point-in-time."""

from datetime import date, time

from signal_engine.config import load_config
from signal_engine.scan.harness import run_scan
from signal_engine.universe.mock import MockUniverseProvider

_DAY = date(2025, 6, 23)


def _scan(with_news, seed=42):
    cfg = load_config()
    uni = MockUniverseProvider(n=2000, seed=seed)
    return run_scan(cfg, uni, _DAY, as_of=time(11, 0), seed=seed, top_n=30, with_news=with_news)


def test_news_influences_leaderboard():
    with_news = _scan(True)
    no_news = _scan(False)
    # Same candidate universe/strategy/risk; news should change at least one pick's
    # reasons or confidence (boost/cap), or veto something.
    news_tagged = [
        e for e in with_news.leaderboard
        if any("news" in r.lower() for r in e.plan.reasons)
    ]
    assert len(news_tagged) >= 1
    # Confidence for a news-tagged symbol differs from the no-news run.
    base_conf = {e.plan.symbol: e.plan.confidence for e in no_news.leaderboard}
    changed = [
        e for e in news_tagged
        if e.plan.symbol in base_conf and e.plan.confidence != base_conf[e.plan.symbol]
    ]
    assert len(changed) >= 1


def test_news_scan_is_deterministic():
    a = _scan(True, seed=7)
    b = _scan(True, seed=7)
    assert [e.symbol for e in a.leaderboard] == [e.symbol for e in b.leaderboard]
    assert [e.plan.confidence for e in a.leaderboard] == [e.plan.confidence for e in b.leaderboard]


def test_news_off_has_no_news_reasons():
    res = _scan(False)
    for e in res.leaderboard:
        assert not any("news" in r.lower() for r in e.plan.reasons)
