"""Tests for the Telegram channel news provider + the movers tip-mention shadow feature.

Fixture HTML mirrors the real telegram.me/s/<handle> structure (verified live 2026-07-14:
data-post="handle/<id>", tgme_widget_message_text div, <time datetime="ISO">).
No network anywhere.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from signal_engine.factory import build_news_provider
from signal_engine.movers.sleeve import annotate_tip_mentions
from signal_engine.news.models import NewsItem
from signal_engine.news.provider import CompositeNewsProvider, NewsProvider
from signal_engine.news.telegram_channel import TelegramChannelProvider, parse_channel_html

IST = pytz.timezone("Asia/Kolkata")


def _block(post_id: str, ts: str, text: str | None) -> str:
    text_div = (f'<div class="tgme_widget_message_text js-message_text" dir="auto">'
                f"{text}</div>" if text is not None else "")
    return (f'<div class="tgme_widget_message_wrap"><div data-post="{post_id}">'
            f'{text_div}<span class="tgme_widget_message_meta">'
            f'<time datetime="{ts}">x</time></span></div></div>')


FIXTURE = "".join([
    _block("chan/101", "2026-07-14T09:05:00+05:30",
           "RELIANCE bags massive new energy order, stock buzzing \U0001f680"),
    _block("chan/102", "2026-07-14T09:10:00+05:30",
           "<b>SUZLON</b> upper circuit expected today, huge volumes"),
    _block("chan/103", "2026-07-14T09:12:00+05:30", None),          # media-only -> dropped
    '<div class="tgme_widget_message_wrap"><div data-post="chan/104">'
    '<div class="tgme_widget_message_text">undated post</div></div></div>',  # no <time> -> dropped
])


def test_parse_channel_html_extracts_dated_text_messages():
    msgs = parse_channel_html(FIXTURE)
    assert [m["id"] for m in msgs] == ["chan/101", "chan/102"]
    assert msgs[0]["text"].startswith("RELIANCE bags massive")
    assert "SUZLON" in msgs[1]["text"] and "<b>" not in msgs[1]["text"]  # tags stripped
    assert msgs[0]["ts"].tzinfo is not None


def test_provider_enriches_and_labels_source():
    prov = TelegramChannelProvider(["@chan"], fetcher=lambda h: FIXTURE)
    items = prov.fetch()
    assert len(items) == 2
    by_sym = {tuple(i.symbols): i for i in items}
    rel = next(i for i in items if "RELIANCE" in i.symbols)
    assert rel.source == "telegram:chan"          # tip-channel label travels downstream
    assert rel.id == "tg:chan/101"
    # point-in-time: as_of before the second message excludes it
    as_of = IST.localize(datetime(2026, 7, 14, 9, 6))
    assert len(prov.fetch(as_of=as_of)) == 1


def test_provider_survives_dead_channel():
    def boom(handle):
        raise RuntimeError("blocked")

    prov = TelegramChannelProvider(["chan"], fetcher=boom)
    assert prov.fetch() == []                     # never breaks the news pipeline


class _StaticProvider(NewsProvider):
    def __init__(self, items):
        self._items = items

    def fetch(self, as_of=None):
        return list(self._items)


class _BoomProvider(NewsProvider):
    def fetch(self, as_of=None):
        raise RuntimeError("dead source")


def _item(iid, sym, source="rss", ts=None):
    return NewsItem(id=iid, ts=ts or IST.localize(datetime(2026, 7, 14, 9, 0)),
                    headline=f"{sym} news", source=source, symbols=[sym])


def test_composite_merges_dedupes_and_survives_failure():
    a = _StaticProvider([_item("x1", "TCS"), _item("dup", "INFY")])
    b = _StaticProvider([_item("dup", "INFY"), _item("x2", "SBIN")])
    comp = CompositeNewsProvider([a, _BoomProvider(), b])
    items = comp.fetch()
    assert sorted(i.id for i in items) == ["dup", "x1", "x2"]


def test_factory_composes_rss_and_telegram(monkeypatch):
    from signal_engine.config import load_config

    cfg = load_config()
    cfg.env.news_source = "rss"
    cfg.env.news_telegram_channels = "chan1, chan2"
    prov = build_news_provider(cfg)
    assert isinstance(prov, CompositeNewsProvider)
    # mock mode never ingests tip channels (backtests stay synthetic)
    cfg.env.news_source = "mock"
    assert build_news_provider(cfg) is None


def test_annotate_tip_mentions_counts_only_recent_telegram_items():
    now = datetime.now(IST)
    items = [
        _item("t1", "SUZLON", source="telegram:chan", ts=now - timedelta(hours=2)),
        _item("t2", "SUZLON", source="telegram:chan", ts=now - timedelta(hours=3)),
        _item("t3", "SUZLON", source="telegram:chan", ts=now - timedelta(hours=48)),  # stale
        _item("r1", "SUZLON", source="rss", ts=now - timedelta(hours=1)),             # not a tip
    ]
    preds = [{"symbol": "SUZLON"}, {"symbol": "TCS"}]
    annotate_tip_mentions(preds, items)
    assert preds[0]["tg_mentions"] == 2
    assert preds[1]["tg_mentions"] == 0
