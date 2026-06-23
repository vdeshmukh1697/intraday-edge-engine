"""News providers (PLAN §3.7). MockNewsProvider (synthetic, default) + RSS stub (gated).

Real RSS/NSE-filing ingestion is an external network integration and is gated (like the
live Dhan feed) — it stays a documented stub. Dev/test use the synthetic provider so
nothing depends on the network or live news.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from typing import List, Optional

import numpy as np
import pytz

from signal_engine.news.models import EventType, NewsItem
from signal_engine.news.sentiment import EventClassifier, SentimentModel, default_sentiment_model

IST = pytz.timezone("Asia/Kolkata")

# (template, event hint) — symbol name is substituted in; sentiment is SCORED by the model.
_TEMPLATES = [
    ("{name} bags large order win, profit jumps", EventType.ORDER_WIN),
    ("{name} surges after strong Q4 results beat estimates", EventType.EARNINGS),
    ("Brokerage upgrades {name}, sees strong growth", EventType.UPGRADE),
    ("{name} misses profit estimates, revenue falls", EventType.EARNINGS),
    ("{name} plunges as brokerage downgrades stock", EventType.DOWNGRADE),
    ("SEBI probe into {name} over alleged fraud", EventType.LITIGATION),
    ("{name} board declares dividend and buyback", EventType.CORP_ACTION),
]


class NewsProvider(ABC):
    @abstractmethod
    def fetch(self, as_of: Optional[datetime] = None) -> List[NewsItem]:
        """Return enriched NewsItems known at or before ``as_of`` (point-in-time)."""


class MockNewsProvider(NewsProvider):
    """Deterministic synthetic news: emits ~`prob` of symbols a headline during the morning."""

    def __init__(
        self,
        symbols: List[str],
        day: date,
        seed: int = 42,
        prob: float = 0.35,
        sentiment_model: Optional[SentimentModel] = None,
        classifier: Optional[EventClassifier] = None,
    ):
        self.symbols = list(symbols)
        self.day = day
        self.seed = seed
        self.prob = prob
        self.sentiment = sentiment_model or default_sentiment_model()
        self.classifier = classifier or EventClassifier()
        self._items: List[NewsItem] = self._generate()

    def _generate(self) -> List[NewsItem]:
        rng = np.random.default_rng(self.seed)
        items: List[NewsItem] = []
        open_dt = IST.localize(datetime.combine(self.day, time(9, 15)))
        for i, sym in enumerate(self.symbols):
            if rng.random() > self.prob:
                continue
            tmpl, ev_hint = _TEMPLATES[int(rng.integers(0, len(_TEMPLATES)))]
            headline = tmpl.format(name=sym)
            # minute within the first ~2 hours of the session
            minute = int(rng.integers(0, 120))
            ts = open_dt + timedelta(minutes=minute)
            sentiment = self.sentiment.score(headline)
            event = self.classifier.classify(headline)
            items.append(
                NewsItem(
                    id=f"{sym}-{self.day.isoformat()}-{i}",
                    ts=ts, headline=headline, source="mock",
                    symbols=[sym], sentiment=sentiment, event_type=event,
                )
            )
        items.sort(key=lambda it: it.ts)
        return items

    def fetch(self, as_of: Optional[datetime] = None) -> List[NewsItem]:
        if as_of is None:
            return list(self._items)
        return [it for it in self._items if it.ts <= as_of]  # point-in-time


class RSSNewsProvider(NewsProvider):
    """DATA-ONLY stub for real RSS / NSE-filing ingestion (gated, deferred — PLAN §3.7)."""

    def fetch(self, as_of: Optional[datetime] = None) -> List[NewsItem]:
        raise RuntimeError(
            "Live RSS/NSE news ingestion is not enabled in this build (gated external "
            "integration). Use MockNewsProvider for development and testing."
        )
