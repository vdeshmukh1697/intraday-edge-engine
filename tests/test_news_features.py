"""Hand-verified, deterministic tests for the news feature engine (PLAN §4.6).

Point-in-time correctness (anti-lookahead) is the headline invariant: a feature
vector built at ``as_of`` must never see news published after ``as_of``.
"""

from datetime import datetime, timedelta

import pytest
import pytz

from signal_engine.news.features import compute_news_features
from signal_engine.news.models import NEWS_FEATURE_KEYS, EventType, NewsItem

IST = pytz.timezone("Asia/Kolkata")
AS_OF = IST.localize(datetime(2026, 6, 23, 11, 0, 0))


def item(mins_ago, sent, sym="X", ev=EventType.GENERIC, as_of=AS_OF):
    """Build a NewsItem `mins_ago` minutes before `as_of` (negative = future)."""
    return NewsItem(
        id=f"n{mins_ago}",
        ts=as_of - timedelta(minutes=mins_ago),
        headline="h",
        source="mock",
        symbols=[sym],
        sentiment=sent,
        event_type=ev,
    )


def test_keys_always_present():
    feats = compute_news_features([], "X", AS_OF)
    assert set(feats.keys()) == set(NEWS_FEATURE_KEYS)


def test_no_items_all_defaults():
    feats = compute_news_features([], "X", AS_OF)
    assert feats["news_sentiment"] == 0.0
    assert feats["news_sentiment_avg"] == 0.0
    assert feats["news_count_recent"] == 0.0
    assert feats["news_volume_spike"] == 0.0
    assert feats["news_minutes_since"] == 1e9
    assert feats["news_has_event"] == 0.0
    assert feats["news_event_type"] == "NONE"


def test_no_items_for_symbol_defaults():
    # Items exist, but none mapped to "X".
    items = [item(5, 0.9, sym="Y"), item(10, -0.5, sym="Z")]
    feats = compute_news_features(items, "X", AS_OF)
    assert feats["news_count_recent"] == 0.0
    assert feats["news_minutes_since"] == 1e9
    assert feats["news_event_type"] == "NONE"


def test_other_symbols_excluded():
    items = [item(5, 0.8, sym="X"), item(2, -0.4, sym="Y")]
    feats = compute_news_features(items, "X", AS_OF)
    # Only the X item (5 min ago, 0.8) counts; the more-recent Y item is ignored.
    assert feats["news_sentiment"] == 0.8
    assert feats["news_minutes_since"] == pytest.approx(5.0, abs=1e-4)
    assert feats["news_count_recent"] == 1.0


def test_point_in_time_future_item_ignored():
    # mins_ago = -10 => 10 minutes in the FUTURE relative to as_of.
    future = item(-10, 0.99, sym="X")
    past = item(5, 0.3, sym="X")
    feats = compute_news_features([future, past], "X", AS_OF)
    # Latest eligible item is the past one; the future item must not appear.
    assert feats["news_sentiment"] == 0.3
    assert feats["news_minutes_since"] == pytest.approx(5.0, abs=1e-4)
    assert feats["news_count_recent"] == 1.0


def test_latest_sentiment_and_minutes_since():
    items = [item(5, 0.8, sym="X"), item(15, 0.2, sym="X")]
    feats = compute_news_features(items, "X", AS_OF)
    assert feats["news_sentiment"] == 0.8
    assert feats["news_minutes_since"] == pytest.approx(5.0, abs=1e-4)


def test_count_recent_window():
    # 3 items within 30 min, 1 item 60 min ago (outside window).
    items = [
        item(2, 0.1, sym="X"),
        item(10, 0.1, sym="X"),
        item(25, 0.1, sym="X"),
        item(60, 0.1, sym="X"),
    ]
    feats = compute_news_features(items, "X", AS_OF, window_min=30)
    assert feats["news_count_recent"] == 3.0


def test_decayed_avg_hand_checked():
    # age 0 -> weight 1.0, sentiment 1.0; age 20 (half_life) -> weight 0.5, sentiment 0.0.
    # avg = (1*1.0 + 0.5*0.0) / (1.0 + 0.5) = 1.0 / 1.5 = 0.6667.
    items = [item(0, 1.0, sym="X"), item(20, 0.0, sym="X")]
    feats = compute_news_features(items, "X", AS_OF, half_life_min=20.0)
    assert feats["news_sentiment_avg"] == pytest.approx(0.6666666667, abs=1e-4)


def test_decayed_avg_uses_all_eligible_not_just_window():
    # An item 60 min ago (outside the 30-min window) still feeds the decayed avg.
    items = [item(0, 1.0, sym="X"), item(60, -1.0, sym="X")]
    feats = compute_news_features(items, "X", AS_OF, window_min=30, half_life_min=20.0)
    # weights: 1.0 and 0.5**3 = 0.125 -> (1*1 + 0.125*-1)/(1.125) = 0.875/1.125 = 0.7778
    assert feats["news_sentiment_avg"] == pytest.approx(0.7777777778, abs=1e-4)
    # but it is NOT counted in the recent window.
    assert feats["news_count_recent"] == 1.0


def test_has_event_high_impact_within_window():
    items = [item(5, 0.5, sym="X", ev=EventType.ORDER_WIN)]
    feats = compute_news_features(items, "X", AS_OF)
    assert feats["news_has_event"] == 1.0
    assert feats["news_event_type"] == "ORDER_WIN"


def test_has_event_generic_only_is_zero():
    items = [item(5, 0.5, sym="X", ev=EventType.GENERIC)]
    feats = compute_news_features(items, "X", AS_OF)
    assert feats["news_has_event"] == 0.0
    assert feats["news_event_type"] == "GENERIC"


def test_volume_spike():
    items = [item(2, 0.1, sym="X"), item(10, 0.1, sym="X")]
    feats = compute_news_features(items, "X", AS_OF, baseline_count=1.0)
    assert feats["news_volume_spike"] == 2.0


def test_volume_spike_zero_baseline_defaults_to_one():
    items = [item(2, 0.1, sym="X")]
    feats = compute_news_features(items, "X", AS_OF, baseline_count=0.0)
    assert feats["news_volume_spike"] == 1.0
