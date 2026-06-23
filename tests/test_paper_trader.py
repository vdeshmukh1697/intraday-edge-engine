"""Deterministic, hand-verified tests for the live paper-trader (PLAN §6.5).

All P&L numbers below are computed by hand in the comments and asserted with an
absolute tolerance of 1e-6. The cost model is a tiny stub (fixed breakeven) so this
test never imports the real CostModel — the trader module stays decoupled.
"""

from __future__ import annotations

from datetime import datetime

import pytz

from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
from signal_engine.domain.models import Bar, TradePlan
from signal_engine.paper import PaperTrader

IST = pytz.timezone("Asia/Kolkata")
TOL = 1e-6


class StubCostModel:
    """Returns a fixed round-trip breakeven percentage regardless of price."""

    def __init__(self, breakeven: float = 0.10) -> None:
        self._breakeven = breakeven

    def breakeven_pct(self, price: float) -> float:  # noqa: D401
        return self._breakeven


def ts(hour: int, minute: int) -> datetime:
    return IST.localize(datetime(2026, 6, 23, hour, minute))


def make_plan(direction=Direction.LONG, entry=100.0, stop_loss=99.0,
              stop_pct=1.0, targets=None, plan_ts=None):
    if targets is None:
        targets = [102.0]
    if plan_ts is None:
        plan_ts = ts(9, 15)
    return TradePlan(
        symbol="ACME",
        ts=plan_ts,
        direction=direction,
        strategy="unit-test",
        entry=entry,
        stop_loss=stop_loss,
        stop_pct=stop_pct,
        targets=targets,
        target_pcts=[(targets[0] - entry) / entry * 100],
        expected_move_pct=(targets[0] - entry) / entry * 100,
        risk_reward=2.0,
        cost_to_break_even_pct=0.10,
        confidence=80.0,
    )


def bar(open_, high, low, close, bar_ts, symbol="ACME"):
    return Bar(symbol=symbol, ts=bar_ts, open=open_, high=high, low=low,
               close=close, volume=1000)


# --------------------------------------------------------------------------- #
# 1. PENDING fills at the NEXT bar open, with adverse slippage.
# --------------------------------------------------------------------------- #
def test_pending_fills_at_next_bar_open_with_slippage():
    trader = PaperTrader(StubCostModel(0.10), slippage_pct=0.03, max_hold_minutes=90)
    plan = make_plan(plan_ts=ts(9, 15))
    pos = trader.open_from_plan(plan)
    assert pos.status == PositionStatus.PENDING

    # Same-ts bar must NOT fill (need bar.ts > plan.ts).
    closed = trader.on_bar(bar(100.0, 100.5, 99.8, 100.2, ts(9, 15)))
    assert closed == []
    assert pos.status == PositionStatus.PENDING

    # Next bar fills at open*(1+slip/100) = 100*(1+0.03/100) = 100.03.
    trader.on_bar(bar(100.0, 100.4, 99.9, 100.1, ts(9, 16)))
    assert pos.status == PositionStatus.OPEN
    assert abs(pos.entry_fill - 100.03) < TOL
    assert pos.entry_ts == ts(9, 16)


# --------------------------------------------------------------------------- #
# 2. LONG hits target. Worked example: slippage 0, breakeven 0.10,
#    entry_fill = open = 100, t1 = 102 -> gross = 2.0, net = 1.9,
#    stop_pct = 1.0 -> r_multiple = 1.9. won = True.
# --------------------------------------------------------------------------- #
def test_long_hits_target():
    trader = PaperTrader(StubCostModel(0.10), slippage_pct=0.0, max_hold_minutes=90)
    plan = make_plan(direction=Direction.LONG, entry=100.0, stop_loss=99.0,
                     stop_pct=1.0, targets=[102.0], plan_ts=ts(9, 15))
    pos = trader.open_from_plan(plan)

    # Next bar: open 100 (entry_fill 100), high 102.5 >= t1 102 -> TARGET.
    closed = trader.on_bar(bar(100.0, 102.5, 99.5, 101.0, ts(9, 16)))
    assert closed == [pos]
    assert pos.status == PositionStatus.CLOSED
    assert pos.exit_reason == ExitReason.TARGET
    assert pos.won is True
    assert abs(pos.entry_fill - 100.0) < TOL
    assert abs(pos.exit_fill - 102.0) < TOL
    # gross = (102-100)/100*100 = 2.0 ; net = 2.0 - 0.10 = 1.9
    assert abs(pos.pnl_pct_net - 1.9) < TOL
    # r = net / stop_pct = 1.9 / 1.0 = 1.9
    assert abs(pos.r_multiple - 1.9) < TOL
    assert abs(pos.hold_minutes - 0.0) < TOL


# --------------------------------------------------------------------------- #
# 3. LONG hits stop -> negative pnl, won = False.
#    slippage 0, entry_fill = 100, stop = 99 -> gross = -1.0, net = -1.1,
#    stop_pct = 1.0 -> r_multiple = -1.1.
# --------------------------------------------------------------------------- #
def test_long_hits_stop():
    trader = PaperTrader(StubCostModel(0.10), slippage_pct=0.0, max_hold_minutes=90)
    plan = make_plan(direction=Direction.LONG, entry=100.0, stop_loss=99.0,
                     stop_pct=1.0, targets=[102.0], plan_ts=ts(9, 15))
    pos = trader.open_from_plan(plan)

    # Next bar: open 100, low 98.5 <= stop 99, high 101 (< t1) -> STOP.
    closed = trader.on_bar(bar(100.0, 101.0, 98.5, 100.0, ts(9, 16)))
    assert closed == [pos]
    assert pos.exit_reason == ExitReason.STOP
    assert pos.won is False
    assert abs(pos.exit_fill - 99.0) < TOL
    # gross = (99-100)/100*100 = -1.0 ; net = -1.0 - 0.10 = -1.1
    assert abs(pos.pnl_pct_net - (-1.1)) < TOL
    assert abs(pos.r_multiple - (-1.1)) < TOL


# --------------------------------------------------------------------------- #
# 4. Both stop and target inside one bar -> STOP wins (pessimistic).
# --------------------------------------------------------------------------- #
def test_both_stop_and_target_is_stop():
    trader = PaperTrader(StubCostModel(0.10), slippage_pct=0.0, max_hold_minutes=90)
    plan = make_plan(direction=Direction.LONG, entry=100.0, stop_loss=99.0,
                     stop_pct=1.0, targets=[102.0], plan_ts=ts(9, 15))
    pos = trader.open_from_plan(plan)

    # Next bar spans both: low 98.0 <= stop, high 103 >= t1 -> STOP wins.
    closed = trader.on_bar(bar(100.0, 103.0, 98.0, 100.0, ts(9, 16)))
    assert closed == [pos]
    assert pos.exit_reason == ExitReason.STOP
    assert pos.won is False
    assert abs(pos.exit_fill - 99.0) < TOL
    assert abs(pos.pnl_pct_net - (-1.1)) < TOL


# --------------------------------------------------------------------------- #
# 5. Time-stop: held >= max_hold_minutes with no stop/target -> TIME_STOP at close.
#    max_hold = 90. Entry at 09:16 (open 100), exit bar at 10:46 (held 90 min),
#    close 101, slippage 0 -> gross = 1.0, net = 0.9, r = 0.9.
# --------------------------------------------------------------------------- #
def test_time_stop_at_close():
    trader = PaperTrader(StubCostModel(0.10), slippage_pct=0.0, max_hold_minutes=90)
    plan = make_plan(direction=Direction.LONG, entry=100.0, stop_loss=99.0,
                     stop_pct=1.0, targets=[102.0], plan_ts=ts(9, 15))
    pos = trader.open_from_plan(plan)

    # Entry fill bar at 09:16, range never touches stop(99)/target(102).
    trader.on_bar(bar(100.0, 100.5, 99.5, 100.2, ts(9, 16)))
    assert pos.status == PositionStatus.OPEN

    # 10:46 is exactly 90 minutes after 09:16 -> time stop fires at close 101.
    closed = trader.on_bar(bar(100.5, 101.5, 99.5, 101.0, ts(10, 46)))
    assert closed == [pos]
    assert pos.exit_reason == ExitReason.TIME_STOP
    assert pos.won is False
    assert abs(pos.exit_fill - 101.0) < TOL
    assert abs(pos.hold_minutes - 90.0) < TOL
    # gross = (101-100)/100*100 = 1.0 ; net = 0.9 ; r = 0.9
    assert abs(pos.pnl_pct_net - 0.9) < TOL
    assert abs(pos.r_multiple - 0.9) < TOL


# --------------------------------------------------------------------------- #
# 6. force_square_off: OPEN -> SQUARE_OFF at close; never-filled PENDING -> CANCELLED.
# --------------------------------------------------------------------------- #
def test_force_square_off():
    trader = PaperTrader(StubCostModel(0.10), slippage_pct=0.0, max_hold_minutes=90)

    # Position A: fills and stays open.
    plan_a = make_plan(plan_ts=ts(9, 15))
    pos_a = trader.open_from_plan(plan_a)
    trader.on_bar(bar(100.0, 100.5, 99.5, 100.2, ts(9, 16)))
    assert pos_a.status == PositionStatus.OPEN

    # Position B: created late, never gets a next bar -> still PENDING.
    plan_b = make_plan(plan_ts=ts(15, 19))
    pos_b = trader.open_from_plan(plan_b)
    assert pos_b.status == PositionStatus.PENDING

    affected = trader.force_square_off(bar(100.5, 101.0, 100.0, 100.8, ts(15, 20)))
    assert pos_a in affected and pos_b in affected

    # A closed via SQUARE_OFF at close 100.8.
    assert pos_a.status == PositionStatus.CLOSED
    assert pos_a.exit_reason == ExitReason.SQUARE_OFF
    assert abs(pos_a.exit_fill - 100.8) < TOL
    # gross = (100.8-100)/100*100 = 0.8 ; net = 0.7
    assert abs(pos_a.pnl_pct_net - 0.7) < TOL
    assert pos_a.won is False

    # B never filled -> CANCELLED, no fills/metrics.
    assert pos_b.status == PositionStatus.CANCELLED
    assert pos_b.entry_fill is None
    assert pos_b.exit_fill is None

    # Nothing left active.
    assert trader.open_positions == []


# --------------------------------------------------------------------------- #
# Bonus: SHORT target exit with slippage, to exercise the mirror logic.
#   slippage 0, entry_fill = open = 100, t1 = 98 (SHORT), breakeven 0.10.
#   gross = -1 * (98-100)/100*100 = 2.0 ; net = 1.9 ; stop_pct 1 -> r = 1.9.
# --------------------------------------------------------------------------- #
def test_short_hits_target():
    trader = PaperTrader(StubCostModel(0.10), slippage_pct=0.0, max_hold_minutes=90)
    plan = make_plan(direction=Direction.SHORT, entry=100.0, stop_loss=101.0,
                     stop_pct=1.0, targets=[98.0], plan_ts=ts(9, 15))
    pos = trader.open_from_plan(plan)

    # Next bar: open 100, low 97.5 <= t1 98 -> TARGET.
    closed = trader.on_bar(bar(100.0, 100.5, 97.5, 98.5, ts(9, 16)))
    assert closed == [pos]
    assert pos.exit_reason == ExitReason.TARGET
    assert pos.won is True
    assert abs(pos.exit_fill - 98.0) < TOL
    assert abs(pos.pnl_pct_net - 1.9) < TOL
    assert abs(pos.r_multiple - 1.9) < TOL
