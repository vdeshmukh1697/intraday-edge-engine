"""Offline, deterministic tests for the real RSS news provider.

No live network: a fake ``fetcher`` returns a fixture XML string. Verifies symbol
mapping, sentiment/event enrichment, IST timestamp conversion, cross-feed dedupe,
point-in-time ``as_of`` filtering, per-feed resilience, and skipping entries with no
publish date.
"""

from __future__ import annotations

from datetime import datetime

import pytz

from signal_engine.news.models import EventType
from signal_engine.news.rss import DEFAULT_FEEDS, IST, RSSNewsProvider

# Three real-looking items: Reliance (record profit -> positive, RELIANCE),
# Infosys (large deal -> INFY), and a generic market item (no symbol).
# pubDates are explicit and in GMT so the IST conversion is checkable.
FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Market Feed</title>
    <item>
      <title>Reliance Industries posts record profit</title>
      <link>https://example.com/news/reliance-record-profit</link>
      <description>RIL beats estimates on strong refining margins.</description>
      <pubDate>Mon, 22 Jun 2026 04:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Infosys wins large deal</title>
      <link>https://example.com/news/infosys-large-deal</link>
      <description>Infosys bags a multi-year order from a global client.</description>
      <pubDate>Mon, 22 Jun 2026 05:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Market opens flat</title>
      <link>https://example.com/news/market-opens-flat</link>
      <description>Benchmark indices little changed in early trade.</description>
      <pubDate>Mon, 22 Jun 2026 03:45:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

# An entry with no pubDate -> must be skipped (point-in-time needs a real ts).
FIXTURE_NO_PUBDATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>No Date Feed</title>
    <item>
      <title>Reliance Industries posts record profit</title>
      <link>https://example.com/news/reliance-no-date</link>
      <description>No publish date present.</description>
    </item>
  </channel>
</rss>
"""


def _provider(fetcher, feeds=None):
    return RSSNewsProvider(feeds=feeds or ["http://feed-a.example/rss"], fetcher=fetcher)


def test_items_produced_with_correct_symbols():
    items = _provider(lambda url: FIXTURE_XML).fetch()
    assert len(items) == 3
    by_headline = {it.headline: it for it in items}

    assert by_headline["Reliance Industries posts record profit"].symbols == ["RELIANCE"]
    assert by_headline["Infosys wins large deal"].symbols == ["INFY"]
    # Generic market item maps to no symbol.
    assert by_headline["Market opens flat"].symbols == []


def test_sentiment_and_event_populated():
    items = _provider(lambda url: FIXTURE_XML).fetch()
    by_headline = {it.headline: it for it in items}

    reliance = by_headline["Reliance Industries posts record profit"]
    # "record"/"profit"/"beats"/"strong" -> positive.
    assert reliance.sentiment > 0
    assert reliance.event_type == EventType.EARNINGS

    infosys = by_headline["Infosys wins large deal"]
    assert infosys.event_type in (EventType.ORDER_WIN, EventType.BLOCK_DEAL)
    assert infosys.sentiment > 0


def test_ts_is_tzaware_ist_and_matches_pubdate():
    items = _provider(lambda url: FIXTURE_XML).fetch()
    reliance = next(it for it in items if it.headline.startswith("Reliance"))

    assert reliance.ts.tzinfo is not None  # tz-aware
    # 04:30 GMT -> 10:00 IST (+05:30).
    expected = IST.localize(datetime(2026, 6, 22, 10, 0, 0))
    assert reliance.ts == expected
    assert reliance.ts.utcoffset() == pytz.timezone("Asia/Kolkata").localize(
        datetime(2026, 6, 22, 10, 0, 0)
    ).utcoffset()


def test_results_sorted_by_ts_ascending():
    items = _provider(lambda url: FIXTURE_XML).fetch()
    ts_list = [it.ts for it in items]
    assert ts_list == sorted(ts_list)
    # "Market opens flat" (03:45 GMT) is earliest.
    assert items[0].headline == "Market opens flat"


def test_dedupe_across_feeds():
    # Same fixture served by two feeds -> each item appears once.
    provider = RSSNewsProvider(
        feeds=["http://feed-a.example/rss", "http://feed-b.example/rss"],
        fetcher=lambda url: FIXTURE_XML,
    )
    items = provider.fetch()
    assert len(items) == 3
    ids = [it.id for it in items]
    assert len(ids) == len(set(ids))


def test_as_of_filtering_excludes_future_items():
    # as_of between the Reliance (10:00 IST) and Infosys (10:30 IST) items.
    as_of = IST.localize(datetime(2026, 6, 22, 10, 15, 0))
    items = _provider(lambda url: FIXTURE_XML).fetch(as_of=as_of)
    headlines = {it.headline for it in items}
    assert "Infosys wins large deal" not in headlines  # 10:30 IST, after as_of
    assert "Reliance Industries posts record profit" in headlines  # 10:00 IST
    assert "Market opens flat" in headlines  # 09:15 IST


def test_resilient_to_failing_feed():
    def fetcher(url):
        if "bad" in url:
            raise RuntimeError("boom: slow/failing feed")
        return FIXTURE_XML

    provider = RSSNewsProvider(
        feeds=["http://bad.example/rss", "http://good.example/rss"],
        fetcher=fetcher,
    )
    items = provider.fetch()
    # The working feed's items still come through.
    assert len(items) == 3


def test_entry_without_pubdate_is_skipped():
    items = _provider(lambda url: FIXTURE_NO_PUBDATE).fetch()
    assert items == []


def test_default_feeds_are_real_urls():
    assert len(DEFAULT_FEEDS) >= 3
    assert all(u.startswith("http") for u in DEFAULT_FEEDS)
