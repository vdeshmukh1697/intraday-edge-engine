"""Real RSS news provider (PLAN §3.7).

Fetches real Indian-market RSS/Atom feeds (no API key required), parses them with
``feedparser``, and enriches each headline with the existing symbol-mapping,
sentiment, and event-classification pieces — producing point-in-time
:class:`NewsItem` objects via the :class:`NewsProvider` interface.

Network access is isolated behind an injectable ``fetcher`` callable so the provider
can be exercised fully offline (tests pass a fake fetcher returning fixture XML).
The default fetcher uses ``urllib.request`` with a short timeout and a browser-like
User-Agent. Everything is resilient: a slow/failing feed or a malformed entry is
logged-and-skipped — ``fetch()`` never raises.
"""

from __future__ import annotations

import calendar
import hashlib
import logging
import time as _time
from datetime import datetime, timezone
from typing import Callable, List, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import feedparser
import pytz

from signal_engine.news.mapper import SymbolMapper, default_symbol_aliases
from signal_engine.news.models import EventType, NewsItem
from signal_engine.news.provider import NewsProvider
from signal_engine.news.sentiment import (
    EventClassifier,
    SentimentModel,
    default_sentiment_model,
)

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# Real Indian-market RSS feeds (no API key needed). Kept as module constants so
# they're trivial to edit/extend.
DEFAULT_FEEDS: List[str] = [
    # Economic Times — market-specific feeds (verified working 2026-06-25)
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
    # NDTV Profit markets
    "https://feeds.feedburner.com/ndtvprofit-latest",
    # The Hindu Business/Markets
    "https://www.thehindu.com/business/markets/?service=rss",
    # Moneycontrol feeds removed — returning HTTP 403 as of 2026-06-25
]

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_TIMEOUT_SECS = 10


def _default_fetcher(url: str) -> str:
    """Fetch raw feed XML over HTTP with a browser-like UA and a 10s timeout."""
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(req, timeout=_TIMEOUT_SECS) as resp:  # noqa: S310 - trusted feed URLs
        raw = resp.read()
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def _struct_to_ist(parsed: _time.struct_time) -> datetime:
    """Convert a UTC ``struct_time`` (feedparser's ``*_parsed``) to tz-aware IST."""
    # feedparser normalizes published_parsed to UTC.
    epoch = calendar.timegm(parsed)
    utc_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return utc_dt.astimezone(IST)


def _feed_source(url: str) -> str:
    """Derive a short source label from a feed URL (its domain)."""
    try:
        host = urlparse(url).netloc or url
    except Exception:
        return url
    return host[4:] if host.startswith("www.") else host


class RSSNewsProvider(NewsProvider):
    """Real RSS-backed :class:`NewsProvider`.

    Reuses the project's symbol mapper, sentiment model, and event classifier to
    enrich live headlines. Network is injectable via ``fetcher`` for offline tests.
    """

    def __init__(
        self,
        feeds: Optional[List[str]] = None,
        mapper: Optional[SymbolMapper] = None,
        sentiment_model: Optional[SentimentModel] = None,
        classifier: Optional[EventClassifier] = None,
        fetcher: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.feeds = list(feeds) if feeds is not None else list(DEFAULT_FEEDS)
        self.mapper = mapper or SymbolMapper(default_symbol_aliases())
        self.sentiment = sentiment_model or default_sentiment_model()
        self.classifier = classifier or EventClassifier()
        self.fetcher = fetcher or _default_fetcher

    @staticmethod
    def _make_id(title: str, link: str) -> str:
        """Stable dedupe/id hash from (title + link)."""
        digest = hashlib.sha1(f"{title}\n{link}".encode("utf-8")).hexdigest()
        return digest[:16]

    def _parse_feed(self, url: str) -> List[NewsItem]:
        """Fetch + parse a single feed into enriched NewsItems. Never raises."""
        raw = self.fetcher(url)
        parsed = feedparser.parse(raw)
        source = _feed_source(url)
        items: List[NewsItem] = []
        for entry in getattr(parsed, "entries", []) or []:
            try:
                item = self._build_item(entry, source)
            except Exception:  # noqa: BLE001 - one bad entry must not kill the feed
                logger.warning("Skipping malformed entry in feed %s", url, exc_info=True)
                continue
            if item is not None:
                items.append(item)
        return items

    def _build_item(self, entry, source: str) -> Optional[NewsItem]:
        title = (getattr(entry, "title", "") or "").strip()
        if not title:
            return None  # nothing to enrich

        # Point-in-time requires a real publish timestamp.
        parsed_time = getattr(entry, "published_parsed", None)
        if parsed_time is None:
            logger.debug("Skipping entry without published_parsed: %r", title)
            return None
        ts = _struct_to_ist(parsed_time)

        link = (getattr(entry, "link", "") or "").strip()
        summary = (getattr(entry, "summary", "") or "").strip()
        # Use summary as extra context for enrichment, but keep headline = title.
        enrich_text = title if not summary else f"{title}. {summary}"

        symbols = self.mapper.map(enrich_text)
        sentiment = self.sentiment.score(enrich_text)
        event: EventType = self.classifier.classify(enrich_text)

        return NewsItem(
            id=self._make_id(title, link),
            ts=ts,
            headline=title,
            source=source,
            symbols=symbols,
            sentiment=sentiment,
            event_type=event,
        )

    def fetch(self, as_of: Optional[datetime] = None) -> List[NewsItem]:
        """Fetch all feeds, enrich, dedupe, point-in-time filter, sort by ts asc."""
        by_id = {}
        for url in self.feeds:
            try:
                feed_items = self._parse_feed(url)
            except Exception:  # noqa: BLE001 - a failing/slow feed is skipped
                logger.warning("Skipping feed (fetch/parse failed): %s", url, exc_info=True)
                continue
            for item in feed_items:
                # Dedupe across feeds by id hash of (title + link); first wins.
                if item.id not in by_id:
                    by_id[item.id] = item

        items = list(by_id.values())
        if as_of is not None:
            items = [it for it in items if it.ts <= as_of]  # point-in-time
        items.sort(key=lambda it: it.ts)
        return items
