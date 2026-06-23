"""Instrument metadata (frozen contract) for the full-universe scan (PLAN §3.3/§4.0)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentMeta:
    """Static per-symbol metadata used by the liquidity/cost filter and ranking.

    All non-secret, slowly-changing reference data. ``avg_daily_turnover_cr`` is in
    crore rupees; ``est_spread_pct`` is a typical bid/ask spread as a percent of price.
    """

    symbol: str
    sector: str
    avg_daily_turnover_cr: float   # average daily traded value, ₹ crore
    last_price: float              # reference / previous close (penny filter, seeding)
    est_spread_pct: float          # typical spread as % of price (liquidity proxy)
    tick_size: float = 0.05
    is_banned: bool = False        # F&O ban / surveillance / suspended
