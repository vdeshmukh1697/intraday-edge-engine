"""News overlay — the rules-engine integration of news (PLAN §4.6).

Takes a technical Signal + the symbol's news features and applies news as a
gate / booster / veto, appending an explainable reason. News rarely fires a trade
alone in rules mode — it confirms, boosts, caps, or vetoes a technical signal.

Returns the (possibly confidence-adjusted) Signal, or None if news vetoes / guards it.
Pure logic; reuses the frozen NEWS_FEATURE_KEYS contract.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Optional

from signal_engine.domain.enums import Direction
from signal_engine.domain.models import Signal


class NewsOverlay:
    def __init__(
        self,
        recency_min: float = 30.0,     # ignore news older than this (stale)
        veto_threshold: float = 0.5,   # |sentiment| against the trade -> veto
        boost_threshold: float = 0.3,  # sentiment with the trade -> boost
        boost_points: float = 10.0,    # max confidence added on strong aligned news
        cap_points: float = 10.0,      # confidence removed on mild opposing news
        spike_min: float = 1.5,        # news-volume spike that strengthens a boost
        event_guard_min: float = 3.0,  # suppress entries within N min of a high-impact event
    ):
        self.recency_min = recency_min
        self.veto_threshold = veto_threshold
        self.boost_threshold = boost_threshold
        self.boost_points = boost_points
        self.cap_points = cap_points
        self.spike_min = spike_min
        self.event_guard_min = event_guard_min

    def apply(self, signal: Signal, nf: Dict[str, float]) -> Optional[Signal]:
        if signal is None or not nf:
            return signal

        minutes_since = nf.get("news_minutes_since", 1e9)
        # Stale news -> no influence.
        if minutes_since > self.recency_min:
            return signal

        # Event guard: don't fire blind into a just-released high-impact event.
        # Returning None suppresses the signal; the scanner logs it as news-guarded.
        if nf.get("news_has_event", 0.0) >= 1.0 and minutes_since <= self.event_guard_min:
            return None

        sentiment = float(nf.get("news_sentiment_avg", 0.0))
        spike = float(nf.get("news_volume_spike", 0.0))
        is_long = signal.direction == Direction.LONG
        aligned = sentiment if is_long else -sentiment  # >0 means news supports the trade

        new_conf = signal.confidence
        reasons = list(signal.reasons)

        if aligned <= -self.veto_threshold:
            # Strong opposing news -> veto entirely.
            return None
        elif aligned >= self.boost_threshold:
            bump = self.boost_points * (1.0 + (0.5 if spike >= self.spike_min else 0.0))
            new_conf = min(100.0, new_conf + bump)
            tag = f"+ve news {sentiment:+.2f}" if is_long else f"-ve news {sentiment:+.2f}"
            if spike >= self.spike_min:
                tag += f" (x{spike:.1f} vol)"
            reasons.append(tag)
        elif aligned < 0:
            # Mild opposing news -> cap confidence.
            new_conf = max(0.0, new_conf - self.cap_points)
            reasons.append(f"news caution {sentiment:+.2f}")

        return replace(signal, confidence=round(new_conf, 1), reasons=reasons)
