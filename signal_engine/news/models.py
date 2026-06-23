"""News domain models (frozen contracts) — PLAN §3.7/§4.6.

A NewsItem is point-in-time: ``ts`` is the REAL publish time and the item may only be
used from that instant forward (anti-lookahead; PLAN §6.3/§9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List


class EventType(str, Enum):
    """Coarse market-moving event categories (PLAN §4.6)."""

    EARNINGS = "EARNINGS"
    ORDER_WIN = "ORDER_WIN"
    BLOCK_DEAL = "BLOCK_DEAL"
    UPGRADE = "UPGRADE"
    DOWNGRADE = "DOWNGRADE"
    LITIGATION = "LITIGATION"
    CORP_ACTION = "CORP_ACTION"
    MANAGEMENT = "MANAGEMENT"
    GENERIC = "GENERIC"
    NONE = "NONE"

    @property
    def is_high_impact(self) -> bool:
        """Events worth an 'event guard' around (don't fire blind into these)."""
        return self in (
            EventType.EARNINGS, EventType.ORDER_WIN, EventType.BLOCK_DEAL,
            EventType.UPGRADE, EventType.DOWNGRADE, EventType.LITIGATION,
        )


@dataclass(frozen=True)
class NewsItem:
    """A single enriched news headline mapped to symbol(s).

    ``sentiment`` in [-1, +1] (negative..positive). ``symbols`` may be empty if no symbol
    could be mapped (kept as market-wide context).
    """

    id: str
    ts: datetime                          # tz-aware IST publish time (point-in-time!)
    headline: str
    source: str
    symbols: List[str] = field(default_factory=list)
    sentiment: float = 0.0
    event_type: EventType = EventType.GENERIC


# Frozen feature-key contract produced by the news-feature engine (news/features.py)
# and consumed by the NewsOverlay + strategies. Mirrors the technical feature-key pattern.
NEWS_FEATURE_KEYS = [
    "news_sentiment",       # latest mapped item's sentiment for the symbol (0.0 if none)
    "news_sentiment_avg",   # time-decayed average sentiment over the window
    "news_count_recent",    # number of items in the lookback window
    "news_volume_spike",    # recent count / baseline (>1 = unusual coverage)
    "news_minutes_since",   # minutes since the most recent item (large if none)
    "news_has_event",       # 1.0 if a high-impact event occurred in the window else 0.0
    "news_event_type",      # most-recent event type value (string) or "NONE"
]
