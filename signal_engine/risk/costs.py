"""Percentage-based transaction cost model for intraday equity (PLAN §5.4).

All charges are computed for a round-trip (one buy + one sell) and returned as a
``CostBreakdown``. Percentages are stored as *fractions* in CostParams (e.g.
``stt_pct = 0.00025`` means 0.025%), so we multiply directly by rupee values.
"""

from __future__ import annotations

from typing import Optional

from signal_engine.domain.models import CostBreakdown


class CostModel:
    """Computes round-trip charges and the break-even move for a trade.

    ``costs`` must expose: ``brokerage_flat``, ``brokerage_pct``, ``stt_pct``,
    ``exchange_txn_pct``, ``gst_pct``, ``sebi_pct``, ``stamp_pct`` and
    ``reference_trade_value`` (any object with those attributes works, e.g.
    :class:`signal_engine.config.CostParams`).

    ``slippage`` is accepted for forward-compatibility (PLAN §5.4 models entry/exit
    slippage separately) but is not applied to the statutory cost breakdown here.
    """

    def __init__(self, costs, slippage=None):
        self.costs = costs
        self.slippage = slippage

    def charges(self, buy_price: float, sell_price: float, qty: float) -> CostBreakdown:
        """Itemized round-trip charges for buying ``qty`` at ``buy_price`` and
        selling ``qty`` at ``sell_price`` (intraday equity)."""
        c = self.costs
        buy_value = buy_price * qty
        sell_value = sell_price * qty

        brokerage = min(c.brokerage_flat, c.brokerage_pct * buy_value) + min(
            c.brokerage_flat, c.brokerage_pct * sell_value
        )
        stt = c.stt_pct * sell_value  # sell side only
        exchange_txn = c.exchange_txn_pct * (buy_value + sell_value)
        gst = c.gst_pct * (brokerage + exchange_txn)
        sebi = c.sebi_pct * (buy_value + sell_value)
        stamp = c.stamp_pct * buy_value  # buy side only

        return CostBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_txn=exchange_txn,
            gst=gst,
            sebi=sebi,
            stamp=stamp,
        )

    def breakeven_pct(self, price: float, trade_value: Optional[float] = None) -> float:
        """Percentage move (round-trip, same price both sides) needed to clear all
        charges, sized off ``trade_value`` (defaults to ``reference_trade_value``)."""
        if trade_value is None:
            trade_value = self.costs.reference_trade_value
        qty = max(1, round(trade_value / price))
        total = self.charges(price, price, qty).total
        return total / (price * qty) * 100.0
