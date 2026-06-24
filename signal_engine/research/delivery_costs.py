"""DELIVERY (overnight / multi-day) round-trip cost model for the SWING research harness.

The production :class:`signal_engine.risk.costs.CostModel` is an INTRADAY model (STT 0.025%
sell-only, no DP charge). Multi-day / overnight holds are DELIVERY trades on Indian cash
equity and are charged very differently — this module models them honestly. It is ADDITIVE
and READ-ONLY w.r.t. production code (the intraday model is left untouched).

Statutory / regulatory schedule for NSE cash-market DELIVERY (FY25/FY26, equity delivery):
-----------------------------------------------------------------------------------------
* STT          0.1% on BOTH the buy leg AND the sell leg (vs intraday's 0.025% sell-only).
* Exchange txn ~0.00297% (0.0000297) of turnover, charged on both legs.
* SEBI charges 0.0001% (0.000001) of turnover, both legs.
* Stamp duty   0.015% (0.00015) on the BUY leg only.
* GST          18% on (brokerage + exchange txn) only (NOT on STT / stamp).
* Brokerage    discount brokers commonly charge Rs.0 for delivery; we model Rs.20/leg by
               default to be safe (configurable; set to 0 for zero-brokerage delivery).
* DP / demat   a flat depository charge (~Rs.13-25) levied ONCE per scrip on the SELL day
               (shares debited from demat). Default Rs.18 (CDSL ~Rs.13 + DP markup).

Everything is returned as a FRACTION OF NOTIONAL at a reference trade value, so a swing
probe can subtract it ONCE per round-trip from a directional gross return (same convention
as :mod:`signal_engine.research.probe`). At a reference notional the percentage components
dominate and are ~scale-invariant; the flat components (brokerage, DP) shrink as notional
grows, so we expose the reference value explicitly and report the % at it.

NOTE on shorting: India does NOT allow carrying a short cash-equity position overnight, so a
"delivery short" is not implementable in cash. A long-short swing strategy's short leg must
be expressed in single-stock / index FUTURES or OPTIONS, which carry their OWN cost & roll
structure (futures: much lower STT 0.02% sell-side on notional, but margin + roll/financing).
``futures_short_leg_pct`` gives a rough, deliberately conservative proxy round-trip cost for
flagging long-short results; it is NOT a substitute for a real futures-cost study.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- statutory rates (delivery equity, fractions of turnover unless noted) ---
STT_DELIVERY_PCT = 0.001        # 0.1% on BUY and on SELL
EXCHANGE_TXN_PCT = 0.0000297    # ~0.00297% NSE cash, per leg
SEBI_PCT = 0.000001             # 0.0001% per leg
STAMP_BUY_PCT = 0.00015         # 0.015% buy only
GST_PCT = 0.18                  # on (brokerage + exchange txn)
DEFAULT_BROKERAGE_FLAT = 20.0   # Rs per leg (set 0.0 for zero-brokerage delivery)
DEFAULT_DP_CHARGE = 18.0        # Rs once on the sell day (depository/demat)
DEFAULT_REFERENCE_NOTIONAL = 100000.0  # Rs per leg, matches production reference_trade_value


@dataclass(frozen=True)
class DeliveryCostBreakdown:
    """Rupee breakdown of one DELIVERY round-trip at the given buy/sell notional."""

    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    brokerage: float
    gst: float
    dp_charge: float
    total: float
    notional: float

    @property
    def total_pct(self) -> float:
        """Round-trip total as a FRACTION of the (buy-leg) notional."""
        return self.total / self.notional if self.notional > 0 else 0.0


def delivery_charges(
    buy_value: float,
    sell_value: float,
    brokerage_flat: float = DEFAULT_BROKERAGE_FLAT,
    dp_charge: float = DEFAULT_DP_CHARGE,
) -> DeliveryCostBreakdown:
    """Full rupee delivery round-trip charges for a buy_value/sell_value notional pair."""
    turnover = buy_value + sell_value
    stt = STT_DELIVERY_PCT * buy_value + STT_DELIVERY_PCT * sell_value
    exchange_txn = EXCHANGE_TXN_PCT * turnover
    sebi = SEBI_PCT * turnover
    stamp = STAMP_BUY_PCT * buy_value
    brokerage = 2.0 * brokerage_flat  # both legs
    gst = GST_PCT * (brokerage + exchange_txn)
    dp = dp_charge  # once, on sell day
    total = stt + exchange_txn + sebi + stamp + brokerage + gst + dp
    return DeliveryCostBreakdown(
        stt=stt, exchange_txn=exchange_txn, sebi=sebi, stamp=stamp,
        brokerage=brokerage, gst=gst, dp_charge=dp, total=total,
        notional=buy_value,
    )


def delivery_breakeven_pct(
    reference_notional: float = DEFAULT_REFERENCE_NOTIONAL,
    slippage_pct_per_side: float = 0.0,
    brokerage_flat: float = DEFAULT_BROKERAGE_FLAT,
    dp_charge: float = DEFAULT_DP_CHARGE,
) -> float:
    """Round-trip DELIVERY break-even as a FRACTION of notional move at ``reference_notional``.

    Assumes a flat round-trip (buy_value == sell_value == reference_notional) for the
    statutory %; flat charges (brokerage, DP) are spread over that notional. Optionally adds
    ``slippage_pct_per_side`` (in PERCENT, e.g. 0.03 == 3 bps) on BOTH legs — defaults to 0
    because liquid daily-bar swing entries/exits at the close are far less slippage-prone than
    intraday, but the caller can pass the production slippage to be conservative.

    Returns a fraction (e.g. 0.0024 == 0.24%). Subtract this ONCE per round-trip trade.
    """
    bd = delivery_charges(reference_notional, reference_notional,
                          brokerage_flat=brokerage_flat, dp_charge=dp_charge)
    statutory_frac = bd.total_pct
    slippage_frac = 2.0 * (slippage_pct_per_side / 100.0)
    return statutory_frac + slippage_frac


def futures_short_leg_pct(reference_notional: float = DEFAULT_REFERENCE_NOTIONAL,
                          roll_legs_per_hold: float = 1.0) -> float:
    """Rough, deliberately conservative round-trip cost (fraction) for a FUTURES short leg
    used to implement the short side of a long-short swing strategy overnight (cash shorts
    are illegal overnight in India). Models STT 0.02% sell-side on notional, exchange txn,
    GST, plus ``roll_legs_per_hold`` extra round-trips of brokerage+txn for monthly rolls.
    This is a FLAG-AND-PENALIZE proxy, not a real futures-cost study.
    """
    stt = 0.0002  # 0.02% sell side on futures notional
    txn = 2.0 * 0.0000173  # NSE futures exchange txn ~0.00173% per side (lower than cash)
    gst = GST_PCT * txn
    brokerage = (2.0 + 2.0 * roll_legs_per_hold) * (DEFAULT_BROKERAGE_FLAT / reference_notional)
    return stt + txn + gst + brokerage


if __name__ == "__main__":
    ref = DEFAULT_REFERENCE_NOTIONAL
    bd = delivery_charges(ref, ref)
    print(f"==== DELIVERY round-trip @ Rs.{ref:,.0f}/leg ====")
    for k in ("stt", "exchange_txn", "sebi", "stamp", "brokerage", "gst", "dp_charge", "total"):
        v = getattr(bd, k)
        print(f"  {k:14s} Rs.{v:8.2f}  ({v / ref * 100:.4f}%)")
    print(f"  break-even (statutory only)        : {delivery_breakeven_pct()*100:.4f}%")
    print(f"  break-even (+3bps/side slippage)   : {delivery_breakeven_pct(slippage_pct_per_side=0.03)*100:.4f}%")
    print(f"  break-even (zero brokerage)        : {delivery_breakeven_pct(brokerage_flat=0.0)*100:.4f}%")
    print(f"  futures short-leg proxy (1 roll)   : {futures_short_leg_pct()*100:.4f}%")
