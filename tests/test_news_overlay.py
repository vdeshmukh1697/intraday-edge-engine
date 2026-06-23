"""Tests for the news overlay rules (PLAN §4.6): gate / boost / cap / veto / event-guard."""

from datetime import datetime

import pytz

from signal_engine.domain.enums import Direction
from signal_engine.domain.models import Signal
from signal_engine.news.overlay import NewsOverlay

IST = pytz.timezone("Asia/Kolkata")
_TS = IST.localize(datetime(2025, 6, 23, 10, 30))


def _sig(direction=Direction.LONG, conf=80.0):
    return Signal(symbol="X", ts=_TS, direction=direction, confidence=conf,
                  strategy_name="s", entry_hint=100.0, reasons=["base"])


def _nf(sentiment_avg=0.0, minutes_since=5.0, has_event=0.0, spike=1.0, event="GENERIC"):
    return {
        "news_sentiment": sentiment_avg, "news_sentiment_avg": sentiment_avg,
        "news_count_recent": 1.0, "news_volume_spike": spike,
        "news_minutes_since": minutes_since, "news_has_event": has_event,
        "news_event_type": event,
    }


def test_aligned_positive_news_boosts_long():
    ov = NewsOverlay()
    out = ov.apply(_sig(conf=80), _nf(sentiment_avg=0.8))
    assert out is not None
    assert out.confidence == 90.0  # +10 boost, no spike
    assert any("+ve news" in r for r in out.reasons)


def test_volume_spike_strengthens_boost():
    ov = NewsOverlay()
    out = ov.apply(_sig(conf=70), _nf(sentiment_avg=0.6, spike=2.0))
    assert out.confidence == 85.0  # +10 * 1.5 = +15
    assert any("vol" in r for r in out.reasons)


def test_strong_opposing_news_vetoes_long():
    ov = NewsOverlay()
    assert ov.apply(_sig(Direction.LONG, 80), _nf(sentiment_avg=-0.6)) is None


def test_mild_opposing_news_caps_confidence():
    ov = NewsOverlay()
    out = ov.apply(_sig(Direction.LONG, 80), _nf(sentiment_avg=-0.2))
    assert out is not None and out.confidence == 70.0  # -10 cap
    assert any("caution" in r for r in out.reasons)


def test_event_guard_suppresses_fresh_event():
    ov = NewsOverlay()
    out = ov.apply(_sig(), _nf(sentiment_avg=0.8, has_event=1.0, minutes_since=2.0, event="EARNINGS"))
    assert out is None  # within event_guard_min of a high-impact event


def test_stale_news_has_no_effect():
    ov = NewsOverlay()
    out = ov.apply(_sig(conf=80), _nf(sentiment_avg=0.9, minutes_since=120.0))
    assert out is not None and out.confidence == 80.0
    assert out.reasons == ["base"]


def test_short_mirror_boost_on_negative_news():
    ov = NewsOverlay()
    out = ov.apply(_sig(Direction.SHORT, 80), _nf(sentiment_avg=-0.8))
    assert out is not None and out.confidence == 90.0  # negative news supports a short
    assert any("-ve news" in r for r in out.reasons)


def test_empty_features_passthrough():
    ov = NewsOverlay()
    s = _sig()
    assert ov.apply(s, {}) is s
