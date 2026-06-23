"""Pre-market domain models (frozen contracts) — PLAN §4.8."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List

from signal_engine.domain.enums import Direction


class GapBias(str, Enum):
    GAP_UP = "GAP_UP"
    GAP_DOWN = "GAP_DOWN"
    FLAT = "FLAT"


class RiskTone(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class GlobalCues:
    """Overnight / pre-open global inputs (PLAN §3.8). Percentages in percent units.

    ``adr_moves`` maps NSE symbol -> overnight ADR move % (only for names with ADRs).
    """

    gift_nifty_pct: float = 0.0
    us_pct: float = 0.0
    asia_pct: float = 0.0
    usdinr_pct: float = 0.0
    brent_pct: float = 0.0
    gold_pct: float = 0.0
    adr_moves: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexOutlook:
    """Index-level expected open (PLAN §4.8)."""

    expected_gap_pct: float
    gap_bias: GapBias
    risk_tone: RiskTone
    drivers: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PreMarketPick:
    """A per-stock pre-open candidate with expected bias + catalyst (PLAN §4.8)."""

    symbol: str
    bias: Direction                  # LONG / SHORT (FLAT picks are dropped)
    setup: str                       # e.g. "gap-up momentum", "reversal"
    expected_gap_pct: float
    confidence: float                # 0..100
    catalyst: str                    # human-readable reason for the bias
    score: float                     # raw signed score that produced the bias
    drivers: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PreMarketBriefing:
    """The full pre-open briefing: index outlook + ranked stock watchlist."""

    day: date
    index_outlook: IndexOutlook
    picks: List[PreMarketPick] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationResult:
    """Did the pre-market prediction pan out at the actual open (PLAN §4.8)?"""

    gap_happened: bool          # actual gap had the predicted sign and meaningful size
    direction_correct: bool     # actual open move matched predicted bias direction
    volume_confirmed: bool      # opening volume confirmed (not a low-volume fade)
    note: str = ""
