"""Liquidity + %-cost filter for the full-universe scan (PLAN §4.0/§5.4).

Two layers of gating decide whether a symbol is even worth scanning:

1. **Static liquidity / hygiene** (always, from :class:`InstrumentMeta`): drop banned
   or surveillance names, penny stocks, illiquid tickers, and wide-spread tickers.
2. **Cost-viability** (only when intraday features are available): if the typical
   intraday range (``atr_pct``) does not even clear the round-trip break-even move,
   there is no edge to capture (PLAN §4.0).

A symbol is ``tradeable`` only when *no* reason fires; all failing reasons are
collected so callers can log/inspect every rejection cause at once.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class FilterResult:
    """Outcome of evaluating one instrument against the liquidity/cost filter."""

    symbol: str
    tradeable: bool
    reasons: List[str] = field(default_factory=list)


class LiquidityCostFilter:
    """Applies static liquidity checks and (optionally) a cost-viability check.

    ``liquidity`` exposes ``min_avg_daily_turnover_cr``, ``max_spread_pct`` and
    ``min_price`` (e.g. :class:`signal_engine.config.LiquidityParams`).
    ``cost_model`` is a :class:`signal_engine.risk.costs.CostModel`.
    """

    def __init__(self, liquidity, cost_model):
        self.liquidity = liquidity
        self.cost_model = cost_model

    def evaluate(self, meta, features: Optional[Mapping[str, float]] = None) -> FilterResult:
        """Return a :class:`FilterResult` for ``meta``, collecting all failing reasons."""
        liq = self.liquidity
        reasons: List[str] = []

        # 1. Static liquidity / hygiene checks (always run, from meta).
        if meta.is_banned:
            reasons.append("banned/surveillance")
        if meta.last_price < liq.min_price:
            reasons.append(f"penny (<{liq.min_price})")
        if meta.avg_daily_turnover_cr < liq.min_avg_daily_turnover_cr:
            reasons.append(f"illiquid (<{liq.min_avg_daily_turnover_cr}cr)")
        if meta.est_spread_pct > liq.max_spread_pct:
            reasons.append(f"wide spread (>{liq.max_spread_pct}%)")

        # 2. Cost-viability check (only with a finite atr_pct feature).
        if features is not None and "atr_pct" in features:
            atr_pct = features["atr_pct"]
            # NaN atr_pct => unknown range; skip the check rather than reject.
            if atr_pct is not None and math.isfinite(atr_pct):
                breakeven = self.cost_model.breakeven_pct(meta.last_price)
                if atr_pct < breakeven:
                    reasons.append("range below cost")

        return FilterResult(symbol=meta.symbol, tradeable=not reasons, reasons=reasons)

    def partition(
        self,
        metas,
        features_by_symbol: Optional[Mapping[str, Mapping[str, float]]] = None,
    ) -> Tuple[List[FilterResult], List[FilterResult]]:
        """Split ``metas`` into ``(tradeable, rejected)`` lists of FilterResult.

        ``features_by_symbol`` is an optional ``symbol -> features`` mapping; symbols
        without an entry are evaluated with static checks only.
        """
        feats: Mapping[str, Mapping[str, float]] = features_by_symbol or {}
        tradeable: List[FilterResult] = []
        rejected: List[FilterResult] = []
        for meta in metas:
            result = self.evaluate(meta, feats.get(meta.symbol))
            (tradeable if result.tradeable else rejected).append(result)
        return tradeable, rejected
