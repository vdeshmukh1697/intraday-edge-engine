"""Telegram channel news provider — public-channel preview scraping, zero credentials.

Reads a PUBLIC Telegram channel's web preview (``https://telegram.me/s/<handle>``) and
enriches messages through the SAME pipeline as RSS headlines (symbol mapper -> sentiment
-> event classifier), so channel posts flow into news features, the overlay, and the
movers sleeve exactly like any other headline.

Network notes (2026-07-14, measured on this box):
* ``t.me`` is DNS-blocked by the ISP (NXDOMAIN even via DoH from here); ``telegram.me``
  — the same service — resolves and serves the preview. We use telegram.me and keep a
  pinned-IP fallback for the day that host gets blocked too.
* Only PUBLIC channels expose the preview. A private/invite-link channel cannot be read
  this way at all (it needs a logged-in Telegram user session — out of scope here).

Honesty contract (docs/SPIKE_HUNTER_FINDINGS.md §"REJECT small-cap circuit-lock"):
* Tip channels are the documented SEBI pump-and-dump vector, with retail as the exit
  liquidity. This provider treats channel posts as UNVERIFIED tips to be MEASURED, not
  trusted: the source label is ``telegram:<handle>`` so every downstream surface can
  distinguish tip-channel items from wire news, and the movers sleeve tracks
  mention->outcome hit rates before any weight is ever given to them.
"""

from __future__ import annotations

import html as _html
import logging
import re
import ssl
import urllib.request
from datetime import datetime
from typing import Callable, Dict, List, Optional

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

PREVIEW_HOST = "telegram.me"   # t.me is ISP-DNS-blocked here; telegram.me is the same service
FALLBACK_IP = "149.154.167.99"  # DoH-resolved pin, used only if telegram.me DNS dies too
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_TIMEOUT_SECS = 12

# The preview page's message blocks. Structure verified against live telegram.me HTML
# (data-post="handle/123", <time datetime="ISO">, tgme_widget_message_text div).
_BLOCK_RE = re.compile(
    r'data-post="(?P<post>[^"]+)".*?'
    r'(?:<div class="tgme_widget_message_text[^"]*"[^>]*>(?P<text>.*?)</div>.*?)?'
    r'<time datetime="(?P<ts>[^"]+)"',
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _default_fetcher(handle: str) -> str:
    """GET the channel preview HTML; fall back to a pinned IP (correct SNI) if DNS dies."""
    url = f"https://{PREVIEW_HOST}/s/{handle}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECS) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - DNS block / transient: pinned IP with real SNI
        import http.client
        import socket

        ctx = ssl.create_default_context()
        sock = socket.create_connection((FALLBACK_IP, 443), timeout=_TIMEOUT_SECS)
        try:
            tls = ctx.wrap_socket(sock, server_hostname=PREVIEW_HOST)
            conn = http.client.HTTPSConnection(PREVIEW_HOST, timeout=_TIMEOUT_SECS)
            conn.sock = tls  # pre-established TLS socket; cert validated for PREVIEW_HOST
            conn.request("GET", f"/s/{handle}",
                         headers={"User-Agent": _USER_AGENT, "Host": PREVIEW_HOST})
            return conn.getresponse().read().decode("utf-8", errors="replace")
        finally:
            sock.close()


def parse_channel_html(html: str) -> List[Dict]:
    """Extract (id, ts, text) message dicts from a channel preview page.

    Messages without a parseable timestamp are DROPPED (house anti-lookahead rule:
    undated content can never be proven point-in-time). Media-only posts (no text
    block) are dropped too — there is no headline to enrich.
    """
    out: List[Dict] = []
    for m in _BLOCK_RE.finditer(html):
        text_html = m.group("text")
        if not text_html:
            continue
        text = _html.unescape(_TAG_RE.sub(" ", text_html))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        try:
            ts = datetime.fromisoformat(m.group("ts")).astimezone(IST)
        except ValueError:
            continue
        out.append({"id": m.group("post"), "ts": ts, "text": text})
    return out


class TelegramChannelProvider(NewsProvider):
    """Public-channel preview -> enriched NewsItems (same contract as RSSNewsProvider)."""

    def __init__(
        self,
        channels: List[str],
        mapper: Optional[SymbolMapper] = None,
        sentiment_model: Optional[SentimentModel] = None,
        classifier: Optional[EventClassifier] = None,
        fetcher: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.channels = [c.strip().lstrip("@") for c in channels if c and c.strip()]
        self.mapper = mapper or SymbolMapper(default_symbol_aliases())
        self.sentiment = sentiment_model or default_sentiment_model()
        self.classifier = classifier or EventClassifier()
        self.fetcher = fetcher or _default_fetcher

    def fetch(self, as_of: Optional[datetime] = None) -> List[NewsItem]:
        by_id: Dict[str, NewsItem] = {}
        for handle in self.channels:
            try:
                messages = parse_channel_html(self.fetcher(handle))
            except Exception:  # noqa: BLE001 - a dead channel never breaks the pipeline
                logger.warning("telegram channel fetch failed: %s", handle, exc_info=True)
                continue
            for msg in messages:
                item_id = f"tg:{msg['id']}"
                if item_id in by_id:
                    continue
                text = msg["text"]
                symbols = self.mapper.map(text)
                sentiment = self.sentiment.score(text)
                event: EventType = self.classifier.classify(text)
                by_id[item_id] = NewsItem(
                    id=item_id, ts=msg["ts"], headline=text[:300],
                    source=f"telegram:{handle}",   # tip-channel label — downstream honesty
                    symbols=symbols, sentiment=sentiment, event_type=event,
                )
        items = list(by_id.values())
        if as_of is not None:
            items = [it for it in items if it.ts <= as_of]  # point-in-time
        items.sort(key=lambda it: it.ts)
        return items
