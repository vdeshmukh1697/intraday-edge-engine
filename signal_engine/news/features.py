"""News feature engine — PLAN §4.6.

Pure, deterministic, zero-network. Computes a fixed-key feature dict
(:data:`signal_engine.news.models.NEWS_FEATURE_KEYS`) for one symbol as of a
given instant.

**Point-in-time correctness is critical** (anti-lookahead; PLAN §6.3/§9): only
news with ``ts <= as_of`` is ever considered, so a feature vector built at
``as_of`` can never peek at headlines that had not yet been published.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from .models import NEWS_FEATURE_KEYS, EventType, NewsItem

# Sentinel "minutes since" when there is no eligible item at all.
_NO_NEWS_MINUTES = 1e9


def _defaults() -> Dict:
    """All-zero / sentinel feature dict (no eligible news)."""
    return {
        "news_sentiment": 0.0,
        "news_sentiment_avg": 0.0,
        "news_count_recent": 0.0,
        "news_volume_spike": 0.0,
        "news_minutes_since": _NO_NEWS_MINUTES,
        "news_has_event": 0.0,
        "news_event_type": EventType.NONE.value,
    }


def compute_news_features(
    items: List[NewsItem],
    symbol: str,
    as_of: datetime,
    window_min: int = 30,
    half_life_min: float = 20.0,
    baseline_count: float = 1.0,
) -> Dict:
    """Compute the point-in-time news feature vector for ``symbol`` at ``as_of``.

    Only items mapped to ``symbol`` and published at-or-before ``as_of`` are
    eligible. The exponentially time-decayed average sentiment uses

        weight_i = 0.5 ** (age_minutes_i / half_life_min)

    where ``age_minutes_i = (as_of - item.ts) in minutes`` (a half-life of
    ``half_life_min`` minutes). Returns exactly the keys in
    :data:`NEWS_FEATURE_KEYS`. Never raises.
    """
    # Point-in-time + symbol filter: only news the symbol "knew about" by as_of.
    eligible = [
        it for it in items
        if symbol in it.symbols and it.ts <= as_of
    ]

    if not eligible:
        out = _defaults()
        assert set(out.keys()) == set(NEWS_FEATURE_KEYS)
        return out

    # Most-recent eligible item (max ts). Ties broken arbitrarily but deterministically.
    latest = max(eligible, key=lambda it: it.ts)

    minutes_since = (as_of - latest.ts).total_seconds() / 60.0

    # Recent window: items within window_min minutes of as_of.
    window_start = as_of - timedelta(minutes=window_min)
    recent = [it for it in eligible if it.ts >= window_start]

    # Exponentially time-decayed average sentiment over ALL eligible items.
    half_life = half_life_min if half_life_min > 0 else 1.0
    weight_sum = 0.0
    weighted_sent = 0.0
    for it in eligible:
        age_min = (as_of - it.ts).total_seconds() / 60.0
        w = 0.5 ** (age_min / half_life)
        weight_sum += w
        weighted_sent += w * it.sentiment
    sentiment_avg = (weighted_sent / weight_sum) if weight_sum > 0 else 0.0

    # Volume spike vs baseline coverage.
    count_recent = float(len(recent))
    volume_spike = (count_recent / baseline_count) if baseline_count > 0 else 1.0

    has_event = 1.0 if any(it.event_type.is_high_impact for it in recent) else 0.0

    out = {
        "news_sentiment": float(latest.sentiment),
        "news_sentiment_avg": float(sentiment_avg),
        "news_count_recent": count_recent,
        "news_volume_spike": float(volume_spike),
        "news_minutes_since": float(minutes_since),
        "news_has_event": float(has_event),
        "news_event_type": latest.event_type.value,
    }
    assert set(out.keys()) == set(NEWS_FEATURE_KEYS)
    return out
