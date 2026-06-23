"""Hand-verified tests for the percentage-based CostModel (PLAN §5.4)."""

from __future__ import annotations

from signal_engine.config import CostParams, SlippageParams
from signal_engine.domain.models import CostBreakdown
from signal_engine.risk.costs import CostModel

TOL = 1e-4


def _model() -> CostModel:
    # Config defaults: flat=20, pct=0.0003, stt=0.00025, exch=0.0000297,
    # gst=0.18, sebi=0.000001, stamp=0.00003, ref=100000.
    return CostModel(CostParams(), SlippageParams())


def test_charges_hand_verified():
    """price=1000, trade_value=100000 -> qty=100, buy_value=sell_value=100000.

    brokerage = min(20,30)+min(20,30) = 40
    stt       = 0.00025 * 100000      = 25
    exchange  = 0.0000297 * 200000    = 5.94
    gst       = 0.18 * (40 + 5.94)    = 8.2692
    sebi      = 0.000001 * 200000     = 0.2
    stamp     = 0.00003 * 100000      = 3.0
    total                             = 82.4092
    """
    cm = _model()
    cb = cm.charges(1000.0, 1000.0, 100)
    assert isinstance(cb, CostBreakdown)
    assert abs(cb.brokerage - 40.0) < TOL
    assert abs(cb.stt - 25.0) < TOL
    assert abs(cb.exchange_txn - 5.94) < TOL
    assert abs(cb.gst - 8.2692) < TOL
    assert abs(cb.sebi - 0.2) < TOL
    assert abs(cb.stamp - 3.0) < TOL
    assert abs(cb.total - 82.4092) < TOL


def test_breakeven_pct_hand_verified():
    """breakeven_pct = total / (price*qty) * 100 = 82.4092 / 100000 * 100 = 0.0824092%."""
    cm = _model()
    be = cm.breakeven_pct(1000.0)
    assert abs(be - 0.0824092) < TOL


def test_breakeven_default_trade_value_matches_reference():
    cm = _model()
    explicit = cm.breakeven_pct(1000.0, trade_value=100000.0)
    default = cm.breakeven_pct(1000.0)
    assert abs(explicit - default) < TOL


def test_brokerage_caps_at_flat_for_large_trade():
    """For a large notional the pct brokerage exceeds the flat cap on both sides."""
    cm = _model()
    cb = cm.charges(1000.0, 1000.0, 1000)  # buy_value = sell_value = 1,000,000
    # pct*value = 0.0003 * 1e6 = 300 > flat 20 -> capped at 20 per side.
    assert abs(cb.brokerage - 40.0) < TOL


def test_brokerage_uses_pct_for_small_trade():
    """For a tiny notional the pct brokerage is below the flat cap, so pct wins."""
    cm = _model()
    # buy_value = sell_value = 1000 -> pct = 0.0003*1000 = 0.3 < 20.
    cb = cm.charges(100.0, 100.0, 10)
    assert abs(cb.brokerage - 0.6) < TOL


def test_qty_rounds_and_floors_at_one():
    """A price above the reference trade value still yields qty >= 1."""
    cm = _model()
    # trade_value default 100000, price 250000 -> round(0.4)=0 -> max(1,0)=1.
    be = cm.breakeven_pct(250000.0)
    cb = cm.charges(250000.0, 250000.0, 1)
    assert abs(be - cb.total / 250000.0 * 100.0) < TOL


def test_accepts_duck_typed_costs_object():
    """CostModel accepts any object exposing the required attributes."""

    class FakeCosts:
        brokerage_flat = 20.0
        brokerage_pct = 0.0003
        stt_pct = 0.00025
        exchange_txn_pct = 0.0000297
        gst_pct = 0.18
        sebi_pct = 0.000001
        stamp_pct = 0.00003
        reference_trade_value = 100000.0

    cm = CostModel(FakeCosts())
    assert abs(cm.charges(1000.0, 1000.0, 100).total - 82.4092) < TOL
