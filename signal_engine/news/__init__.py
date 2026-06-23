"""News & sentiment (PLAN §3.7, §4.6): ingest -> map -> sentiment/event -> features -> rules.

Exports wired at integration; submodules imported directly during parallel development.
"""

from signal_engine.news.models import NEWS_FEATURE_KEYS, EventType, NewsItem

__all__ = ["NewsItem", "EventType", "NEWS_FEATURE_KEYS"]
