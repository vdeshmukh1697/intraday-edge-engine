"""Global-cues providers (PLAN §3.8).

A :class:`GlobalCuesProvider` returns the overnight / pre-open global inputs
(:class:`~signal_engine.premarket.models.GlobalCues`) for a given trading day.

``MockGlobalCuesProvider`` produces deterministic, realistically-correlated
cues for development and tests. ``YFinanceCuesProvider`` is a gated stub for the
eventual live integration and raises until explicitly enabled.
"""

from __future__ import annotations

import abc
from datetime import date
from typing import Dict, List, Optional

import numpy as np

from signal_engine.premarket.models import GlobalCues

DEFAULT_ADR_SYMBOLS: List[str] = ["INFY", "TCS", "ICICIBANK", "HDFCBANK", "RELIANCE"]


class GlobalCuesProvider(abc.ABC):
    """Abstract source of overnight global cues."""

    @abc.abstractmethod
    def get_cues(self, day: date) -> GlobalCues:
        """Return the :class:`GlobalCues` for ``day``."""
        raise NotImplementedError


class MockGlobalCuesProvider(GlobalCuesProvider):
    """Deterministic, correlated mock cues for development and tests.

    Given the same ``(seed, day)`` the returned :class:`GlobalCues` is identical;
    different days yield different cues. Correlation design (percent units):

    - ``us_pct`` ~ Normal(0, 0.8) is the overnight US driver.
    - ``gift_nifty_pct`` = 0.7 * us_pct + Normal(0, 0.3): GIFT Nifty tracks the
      US session closely (the dominant overnight signal for India's open).
    - ``asia_pct`` = 0.5 * us_pct + Normal(0, 0.4): Asia partially follows the US.
    - ``usdinr_pct`` ~ Normal(0, 0.2), ``brent_pct`` ~ Normal(0, 1.0),
      ``gold_pct`` ~ Normal(0, 0.6) are independent macro inputs.
    - each ADR move = gift_nifty_pct + Normal(0, 0.5): ADRs track the overnight tone.

    All percentages are rounded to 2 decimal places.
    """

    def __init__(self, seed: int = 42, adr_symbols: Optional[List[str]] = None) -> None:
        self.seed = int(seed)
        self.adr_symbols: List[str] = (
            list(adr_symbols) if adr_symbols is not None else list(DEFAULT_ADR_SYMBOLS)
        )

    def get_cues(self, day: date) -> GlobalCues:
        # Per-day RNG so cues are deterministic in (seed, day) and vary by day.
        rng = np.random.default_rng(self.seed + day.toordinal())

        us_pct = float(rng.normal(0.0, 0.8))
        gift_nifty_pct = 0.7 * us_pct + float(rng.normal(0.0, 0.3))
        asia_pct = 0.5 * us_pct + float(rng.normal(0.0, 0.4))
        usdinr_pct = float(rng.normal(0.0, 0.2))
        brent_pct = float(rng.normal(0.0, 1.0))
        gold_pct = float(rng.normal(0.0, 0.6))

        adr_moves: Dict[str, float] = {}
        for symbol in self.adr_symbols:
            adr_moves[symbol] = round(gift_nifty_pct + float(rng.normal(0.0, 0.5)), 2)

        return GlobalCues(
            gift_nifty_pct=round(gift_nifty_pct, 2),
            us_pct=round(us_pct, 2),
            asia_pct=round(asia_pct, 2),
            usdinr_pct=round(usdinr_pct, 2),
            brent_pct=round(brent_pct, 2),
            gold_pct=round(gold_pct, 2),
            adr_moves=adr_moves,
        )


class YFinanceCuesProvider(GlobalCuesProvider):
    """Gated stub for live cues via yfinance (not enabled)."""

    def get_cues(self, day: date) -> GlobalCues:
        raise RuntimeError(
            "Live global cues via yfinance are not enabled (gated external "
            "integration); use MockGlobalCuesProvider for development."
        )
