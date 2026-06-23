"""Pre-market gap/bias scoring — pure deterministic rules (PLAN §4.8, Phase 5).

Two entry points:

* :func:`index_outlook` — turn overnight global cues into an index-level
  expected open (gap bias + risk tone).
* :func:`stock_bias` — turn per-stock pre-open inputs into an actionable
  long/short pick (or ``None`` when the signal is in the dead zone).

All math is hand-verifiable and Python 3.9 compatible.
"""

from __future__ import annotations

from typing import List, Optional

from signal_engine.domain.enums import Direction
from signal_engine.premarket.models import (
    GapBias,
    GlobalCues,
    IndexOutlook,
    PreMarketPick,
    RiskTone,
)

# Events whose news can flip prior-day momentum (drives "reversal" setups).
HIGH_IMPACT = {
    "EARNINGS",
    "ORDER_WIN",
    "BLOCK_DEAL",
    "UPGRADE",
    "DOWNGRADE",
    "LITIGATION",
}


def _clamp(x: float, lo: float, hi: float) -> float:
    """Clamp ``x`` into the inclusive range [lo, hi]."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _sign(x: float) -> int:
    """+1 if positive, -1 if negative, 0 if zero."""
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def index_outlook(cues: GlobalCues, gap_threshold: float = 0.3) -> IndexOutlook:
    """Index-level expected open from overnight global cues.

    GIFT Nifty dominates — it *is* the Nifty proxy — with US and Asia as
    secondary read-throughs.
    """
    expected_gap_pct = round(
        0.6 * cues.gift_nifty_pct + 0.25 * cues.us_pct + 0.15 * cues.asia_pct,
        3,
    )

    if expected_gap_pct > gap_threshold:
        gap_bias = GapBias.GAP_UP
        risk_tone = RiskTone.RISK_ON
    elif expected_gap_pct < -gap_threshold:
        gap_bias = GapBias.GAP_DOWN
        risk_tone = RiskTone.RISK_OFF
    else:
        gap_bias = GapBias.FLAT
        risk_tone = RiskTone.NEUTRAL

    drivers = [
        "GIFT {:+.2f}%".format(cues.gift_nifty_pct),
        "US {:+.2f}%".format(cues.us_pct),
        "Asia {:+.2f}%".format(cues.asia_pct),
    ]

    return IndexOutlook(
        expected_gap_pct=expected_gap_pct,
        gap_bias=gap_bias,
        risk_tone=risk_tone,
        drivers=drivers,
    )


def stock_bias(
    symbol: str,
    news_sentiment_avg: float = 0.0,
    news_event_type: str = "NONE",
    adr_move_pct: float = 0.0,
    index_gap_pct: float = 0.0,
    prev_return_pct: float = 0.0,
    close_position: float = 0.5,
    deadzone: float = 0.15,
) -> Optional[PreMarketPick]:
    """Per-stock pre-open bias from news, ADR, index gap and prior-day momentum.

    Returns ``None`` when the blended score falls inside the dead zone
    (no actionable bias).
    """
    s = _clamp(news_sentiment_avg, -1.0, 1.0)
    adr_n = _clamp(adr_move_pct / 2.0, -1.0, 1.0)   # 2% ADR = full
    idx_n = _clamp(index_gap_pct / 1.0, -1.0, 1.0)  # 1% index gap = full
    mom_n = _clamp(prev_return_pct / 3.0, -1.0, 1.0)  # 3% prior-day move = full

    score = 0.45 * s + 0.25 * adr_n + 0.15 * idx_n + 0.15 * mom_n

    if abs(score) < deadzone:
        return None

    direction = Direction.LONG if score > 0 else Direction.SHORT
    confidence = round(min(100.0, abs(score) * 120.0), 1)
    expected_gap_pct = round(
        0.5 * index_gap_pct + 0.4 * adr_move_pct + 0.6 * s, 2
    )

    news_dir = _sign(s)
    prior_dir = _sign(prev_return_pct)

    if (
        news_event_type in HIGH_IMPACT
        and news_dir != 0
        and prior_dir != 0
        and news_dir != prior_dir
    ):
        setup = "reversal"
    elif abs(index_gap_pct) >= 0.3 and (
        (direction == Direction.LONG and index_gap_pct > 0)
        or (direction == Direction.SHORT and index_gap_pct < 0)
    ):
        setup = "gap-up momentum" if direction == Direction.LONG else "gap-down momentum"
    else:
        setup = "momentum"

    if news_event_type != "NONE" and abs(s) > 0.1:
        catalyst = "{} ({} news)".format(news_event_type, "+ve" if s > 0 else "-ve")
    elif abs(adr_move_pct) >= 0.5:
        catalyst = "ADR {:+.1f}%".format(adr_move_pct)
    else:
        catalyst = "global cues"

    drivers: List[str] = [
        "news {:+.2f}".format(s),
        "ADR {:+.1f}%".format(adr_move_pct),
        "index {:+.2f}%".format(index_gap_pct),
        "prevday {:+.2f}%".format(prev_return_pct),
    ]

    return PreMarketPick(
        symbol=symbol,
        bias=direction,
        setup=setup,
        expected_gap_pct=expected_gap_pct,
        confidence=confidence,
        catalyst=catalyst,
        score=round(score, 4),
        drivers=drivers,
    )
