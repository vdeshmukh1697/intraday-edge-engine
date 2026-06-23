"""News & sentiment (PLAN §3.7, §4.6): ingest -> map -> sentiment/event -> features -> rules."""

from signal_engine.news.features import compute_news_features
from signal_engine.news.mapper import SymbolMapper, default_symbol_aliases
from signal_engine.news.models import NEWS_FEATURE_KEYS, EventType, NewsItem
from signal_engine.news.overlay import NewsOverlay
from signal_engine.news.provider import MockNewsProvider, NewsProvider, RSSNewsProvider
from signal_engine.news.sentiment import (
    EventClassifier,
    LexiconSentiment,
    SentimentModel,
    default_sentiment_model,
)

__all__ = [
    "NewsItem",
    "EventType",
    "NEWS_FEATURE_KEYS",
    "compute_news_features",
    "SymbolMapper",
    "default_symbol_aliases",
    "NewsOverlay",
    "NewsProvider",
    "MockNewsProvider",
    "RSSNewsProvider",
    "SentimentModel",
    "LexiconSentiment",
    "EventClassifier",
    "default_sentiment_model",
]
