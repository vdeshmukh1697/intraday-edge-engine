"""Mock universe provider — deterministic synthetic NSE-cash-like universe (PLAN §4.0).

Generates a large, realistic-looking set of :class:`InstrumentMeta` for full-universe
scan testing without any live data dependency. Same ``seed`` => identical output.
"""

from __future__ import annotations

from typing import List

import numpy as np

from signal_engine.universe.base import UniverseProvider
from signal_engine.universe.models import InstrumentMeta

# Fixed sector palette (roughly NSE-style buckets).
_SECTORS = (
    "Energy",
    "Financials",
    "IT",
    "FMCG",
    "Pharma",
    "Auto",
    "Metals",
    "Realty",
    "Telecom",
    "Utilities",
)

# A few realistic-looking names seeded as the first entries so they're present.
_SEED_NAMES = ("RELIANCE", "HDFCBANK", "INFY", "TCS", "ICICIBANK")


class MockUniverseProvider(UniverseProvider):
    """Deterministic synthetic universe of ``n`` instruments.

    Distributions (per instrument):
      * ``last_price``: log-uniform over ~[5, 5000] (penny names .. high-priced).
      * ``avg_daily_turnover_cr``: lognormal, heavy-tailed (top ~15-25% > 25 cr).
      * ``est_spread_pct``: inversely related to turnover (liquid => tight) + noise.
      * ``is_banned``: ~1.5% True.
    """

    def __init__(self, n: int = 2000, seed: int = 42) -> None:
        if n <= 0:
            raise ValueError("n must be positive")
        self.n = n
        self.seed = seed
        self._instruments = self._generate()

    # -- symbols -----------------------------------------------------------
    def _symbols(self) -> List[str]:
        names: List[str] = []
        for i in range(self.n):
            if i < len(_SEED_NAMES):
                names.append(_SEED_NAMES[i])
            else:
                names.append("SYN{:04d}".format(i))
        return names

    # -- generation --------------------------------------------------------
    def _generate(self) -> List[InstrumentMeta]:
        rng = np.random.default_rng(self.seed)
        n = self.n

        symbols = self._symbols()
        sectors = rng.integers(0, len(_SECTORS), size=n)

        # last_price: log-uniform from ~5 to ~5000.
        log_lo, log_hi = np.log(5.0), np.log(5000.0)
        last_price = np.exp(rng.uniform(log_lo, log_hi, size=n))

        # turnover (cr): lognormal, heavy-tailed. mean(log) tuned so the top
        # ~20% exceed 25 cr; minority reach hundreds to thousands of cr.
        turnover = rng.lognormal(mean=2.0, sigma=1.6, size=n)

        # spread (%): inversely related to turnover. Map turnover -> base spread
        # in [~0.01%, ~0.6%] via a decreasing function of log(turnover), + noise.
        log_t = np.log(turnover)
        # Normalize log_t to [0, 1] (high turnover -> ~1).
        lt_min, lt_max = log_t.min(), log_t.max()
        norm = (log_t - lt_min) / (lt_max - lt_min + 1e-9)
        base_spread = 0.5 - 0.49 * norm  # liquid ~0.01, illiquid ~0.5
        noise = rng.normal(0.0, 0.03, size=n)
        spread = np.clip(base_spread + noise, 0.005, 1.0)

        # is_banned: ~1.5% True.
        banned = rng.random(size=n) < 0.015

        instruments: List[InstrumentMeta] = []
        for i in range(n):
            instruments.append(
                InstrumentMeta(
                    symbol=symbols[i],
                    sector=_SECTORS[int(sectors[i])],
                    avg_daily_turnover_cr=round(float(turnover[i]), 4),
                    last_price=round(float(last_price[i]), 2),
                    est_spread_pct=round(float(spread[i]), 4),
                    is_banned=bool(banned[i]),
                )
            )
        return instruments

    # -- public API --------------------------------------------------------
    def instruments(self) -> List[InstrumentMeta]:
        return list(self._instruments)

    def liquid(self, min_turnover_cr: float = 25.0) -> List[InstrumentMeta]:
        """Instruments with average daily turnover above ``min_turnover_cr``."""
        return [
            inst
            for inst in self._instruments
            if inst.avg_daily_turnover_cr > min_turnover_cr
        ]
