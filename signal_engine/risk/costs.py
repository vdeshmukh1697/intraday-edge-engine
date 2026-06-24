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

    ``slippage`` (any object exposing ``pct_per_side``, e.g.
    :class:`signal_engine.config.SlippageParams`) models execution slippage. It is NOT part
    of the itemized statutory :meth:`charges` breakdown, but IS folded into
    :meth:`breakeven_pct` as a round-trip add-on (PLAN V3) — see :meth:`slippage_pct`.
    """

    def __init__(self, costs, slippage=None):
        self.costs = costs
        self.slippage = slippage

    def slippage_pct(self) -> float:
        """Round-trip slippage as a percentage move (entry + exit), scaled by the configured
        ``slippage_scalar``. Returns 0.0 when no slippage model is supplied.

        ``slippage.pct_per_side`` is a per-side percentage (e.g. 0.03 == 0.03%); a round trip
        crosses the spread twice, so the move that must be cleared is ``2 * pct_per_side``.
        ``slippage_scalar`` (default 1.0) lets us stress-test wider/tighter execution.
        """
        if self.slippage is None:
            return 0.0
        per_side = getattr(self.slippage, "pct_per_side", 0.0) or 0.0
        scalar = getattr(self.costs, "slippage_scalar", 1.0)
        if scalar is None:
            scalar = 1.0
        return 2.0 * per_side * scalar

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
        charges PLUS round-trip slippage, sized off ``trade_value`` (defaults to
        ``reference_trade_value``).

        Statutory charges are computed off the notional; round-trip slippage
        (:meth:`slippage_pct`) is added directly as a percentage move (it scales with price,
        not notional). When no slippage model is supplied the add-on is 0.0, preserving the
        prior behaviour.
        """
        # Guard against a degenerate price (0/negative/NaN): the qty division below would
        # crash or produce nonsense. A non-positive price has no meaningful break-even.
        try:
            if price is None or price <= 0 or price != price:  # price != price => NaN
                return 0.0
        except TypeError:
            return 0.0
        if trade_value is None:
            trade_value = self.costs.reference_trade_value
        qty = max(1, round(trade_value / price))
        total = self.charges(price, price, qty).total
        return total / (price * qty) * 100.0 + self.slippage_pct()
