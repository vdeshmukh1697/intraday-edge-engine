"""Sentiment + event-classification module — PLAN §4.6/§4.7.

Pure, deterministic, zero-network, no heavy ML deps.

The production sentiment model is FinBERT (transformers + torch + model downloads),
but the zero-dependency, explainable default is a finance-aware ``LexiconSentiment``
bag-of-words polarity model behind the ``SentimentModel`` interface. FinBERT is exposed
as an optional, lazily-imported adapter stub (``FinBERTSentiment``) that documents the
production path without pulling in the dependency.
"""

from __future__ import annotations

import abc
import re
from typing import FrozenSet

from .models import EventType

# Word/token boundary tokenizer: keep alphanumerics, lowercase, strip punctuation.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list:
    """Lowercase + split on word boundaries, dropping punctuation."""
    return _TOKEN_RE.findall((text or "").lower())


class SentimentModel(abc.ABC):
    """Interface: map free text to a polarity score in [-1.0, +1.0]."""

    @abc.abstractmethod
    def score(self, text: str) -> float:  # pragma: no cover - interface
        """Return sentiment in [-1.0, +1.0]."""
        raise NotImplementedError


class LexiconSentiment(SentimentModel):
    """Finance-aware bag-of-words polarity model.

    Counts positive (p) and negative (n) keyword hits in the tokenized text and
    returns ``(p - n) / (p + n)`` clamped to [-1, 1]; ``0.0`` when no keyword hits.
    Simple, deterministic and fully explainable.
    """

    # +1 keywords (lowercase)
    POSITIVE: FrozenSet[str] = frozenset({
        "beats", "beat", "surges", "surge", "jumps", "jump", "wins", "win",
        "bags", "bag", "order", "orders", "upgrade", "upgraded", "profit",
        "record", "approval", "approved", "rises", "rise", "gains", "gain",
        "strong", "growth", "dividend", "bonus", "buyback",
    })

    # -1 keywords (lowercase)
    NEGATIVE: FrozenSet[str] = frozenset({
        "miss", "misses", "falls", "fall", "plunges", "plunge", "cut", "cuts",
        "downgrade", "downgraded", "loss", "losses", "probe", "fraud", "ban",
        "banned", "penalty", "resign", "resigns", "default", "recall", "weak",
        "decline", "lawsuit", "slump",
    })

    def __init__(self) -> None:
        # Exposed as instance attributes for test introspection.
        self.positive = self.POSITIVE
        self.negative = self.NEGATIVE

    def score(self, text: str) -> float:
        tokens = _tokenize(text)
        p = sum(1 for t in tokens if t in self.positive)
        n = sum(1 for t in tokens if t in self.negative)
        if p + n == 0:
            return 0.0
        s = (p - n) / (p + n)
        # Mathematically already in [-1, 1]; clamp defensively.
        return max(-1.0, min(1.0, s))


class EventClassifier:
    """Map free text to a coarse :class:`EventType` via keyword rules.

    Priority order (first match wins, default GENERIC). The order is chosen so that
    specific/high-impact categories win over broad words that also appear in them:

      1. LITIGATION   — regulatory/legal terms beat generic "penalty"/"probe" overlaps.
      2. BLOCK_DEAL   — explicit "block/bulk deal" beats the generic word "deal".
      3. DOWNGRADE    — "rating cut"/"downgrade" beats generic "cut" or "sell".
      4. UPGRADE      — "rating raised"/"upgrade".
      5. EARNINGS     — results/revenue/profit/quarter markers.
      6. ORDER_WIN    — order/contract/award (after EARNINGS so "net profit" isn't an order).
      7. CORP_ACTION  — dividend/bonus/split/buyback/rights issue.
      8. MANAGEMENT   — CEO/MD appointments and resignations.
      9. GENERIC      — fallback.
    """

    def classify(self, text: str) -> EventType:
        t = (text or "").lower()
        tokens = set(_tokenize(t))

        def has_word(*words: str) -> bool:
            return any(w in tokens for w in words)

        def has_phrase(*phrases: str) -> bool:
            return any(p in t for p in phrases)

        # 1. LITIGATION
        if has_word("probe", "lawsuit", "fraud", "penalty", "investigation", "raid") \
                or has_phrase("sebi notice"):
            return EventType.LITIGATION

        # 2. BLOCK_DEAL
        if has_phrase("block deal", "bulk deal", "stake sale", "stake buy", "block trade"):
            return EventType.BLOCK_DEAL

        # 3. DOWNGRADE
        if has_word("downgrade", "downgraded", "downgrades") \
                or has_phrase("rating cut", "underperform", "sell rating"):
            return EventType.DOWNGRADE

        # 4. UPGRADE
        if has_word("upgrade", "upgraded", "upgrades") \
                or has_phrase("rating raised", "outperform", "buy rating"):
            return EventType.UPGRADE

        # 5. EARNINGS
        if has_word("results", "revenue", "earnings", "ebitda", "q1", "q2", "q3", "q4") \
                or has_phrase("net profit") \
                or has_word("profit"):
            return EventType.EARNINGS

        # 6. ORDER_WIN
        if has_word("order", "orders", "contract", "bags", "awarded") \
                or has_phrase("wins order", "deal win"):
            return EventType.ORDER_WIN

        # 7. CORP_ACTION
        if has_word("dividend", "bonus", "split", "buyback") \
                or has_phrase("rights issue"):
            return EventType.CORP_ACTION

        # 8. MANAGEMENT
        if has_word("ceo", "md", "resign", "resigns", "appoints", "appointed") \
                or has_phrase("steps down"):
            return EventType.MANAGEMENT

        # 9. GENERIC
        return EventType.GENERIC


class FinBERTSentiment(SentimentModel):
    """Optional production sentiment adapter (stub).

    Lazily attempts to import ``transformers``. If unavailable (the working default),
    construction raises ``RuntimeError`` with guidance. No models are downloaded here;
    this only documents the production path.
    """

    _MISSING_MSG = (
        "FinBERT requires `pip install transformers torch`; "
        "using LexiconSentiment is the default"
    )

    def __init__(self) -> None:
        try:
            import transformers  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(self._MISSING_MSG) from exc
        # Production wiring (pipeline load) would go here; intentionally not implemented.
        self._initialized = False

    def score(self, text: str) -> float:  # pragma: no cover - stub never initialized
        if not getattr(self, "_initialized", False):
            raise RuntimeError(
                "FinBERTSentiment is a stub and not initialized; " + self._MISSING_MSG
            )
        raise NotImplementedError


def default_sentiment_model() -> SentimentModel:
    """Return the zero-dependency default sentiment model."""
    return LexiconSentiment()
