"""Hand-verified tests for RiskManager.build_trade_plan and position_size (PLAN §5.1/§5.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from signal_engine.config import CostParams, RiskParams, SlippageParams
from signal_engine.domain.enums import Direction
from signal_engine.domain.models import Signal
from signal_engine.risk.costs import CostModel
from signal_engine.risk.manager import RiskManager
from signal_engine.risk.sizing import position_size

TOL = 1e-4

# IST-ish tz-aware timestamp (tz value is irrelevant to the math).
TS = datetime(2026, 6, 23, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def _cost_model() -> CostModel:
    return CostModel(CostParams(), SlippageParams())


def _signal(direction: Direction, entry: float = 1000.0) -> Signal:
    return Signal(
        symbol="ACME",
        ts=TS,
        direction=direction,
        confidence=72.0,
        strategy_name="vwap_ema_adx",
        entry_hint=entry,
        reasons=["above vwap", "adx>20"],
    )


def test_long_plan_hand_verified():
    """entry=1000, atr_pct=1.0, RiskParams defaults.

    stop_pct = clamp(1.5*1.0, 0.30, 3.0) = 1.5
    stop_loss = 1000*(1-0.015) = 985.0
    t1_pct = 1.5*1.5 = 2.25 -> t1 = 1000*1.0225 = 1022.5
    t2_pct = 1.5*2.5 = 3.75 -> t2 = 1000*1.0375 = 1037.5
    risk_reward = 2.25/1.5 = 1.5 ; expected_move_pct = 2.25
    edge gate: 2.25 >= 3 * 0.0824092 (=0.2472) -> PASSES
    """
    rm = RiskManager(RiskParams())
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr": 10, "atr_pct": 1.0}, _cost_model())
    assert plan is not None
    assert plan.direction == Direction.LONG
    assert abs(plan.stop_pct - 1.5) < TOL
    assert abs(plan.stop_loss - 985.0) < TOL
    assert abs(plan.targets[0] - 1022.5) < TOL
    assert abs(plan.targets[1] - 1037.5) < TOL
    assert abs(plan.target_pcts[0] - 2.25) < TOL
    assert abs(plan.target_pcts[1] - 3.75) < TOL
    assert abs(plan.risk_reward - 1.5) < TOL
    assert abs(plan.expected_move_pct - 2.25) < TOL
    assert abs(plan.cost_to_break_even_pct - 0.0824) < 1e-3
    assert plan.time_validity == TS + timedelta(minutes=90)
    assert plan.confidence == 72.0
    assert plan.reasons == ["above vwap", "adx>20"]
    assert plan.strategy == "vwap_ema_adx"


def test_short_plan_mirror():
    """SHORT mirrors LONG: stop above entry, targets below."""
    rm = RiskManager(RiskParams())
    plan = rm.build_trade_plan(_signal(Direction.SHORT), {"atr": 10, "atr_pct": 1.0}, _cost_model())
    assert plan is not None
    assert plan.direction == Direction.SHORT
    assert abs(plan.stop_pct - 1.5) < TOL
    assert abs(plan.stop_loss - 1015.0) < TOL  # 1000*(1+0.015)
    assert abs(plan.targets[0] - 977.5) < TOL  # 1000*(1-0.0225)
    assert abs(plan.targets[1] - 962.5) < TOL  # 1000*(1-0.0375)
    assert abs(plan.risk_reward - 1.5) < TOL


def test_atr_pct_derived_from_atr_when_missing():
    """atr_pct absent -> derived as atr/entry*100 = 10/1000*100 = 1.0 -> stop_pct 1.5."""
    rm = RiskManager(RiskParams())
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr": 10}, _cost_model())
    assert plan is not None
    assert abs(plan.stop_pct - 1.5) < TOL


def test_returns_none_when_no_atr_info():
    rm = RiskManager(RiskParams())
    assert rm.build_trade_plan(_signal(Direction.LONG), {}, _cost_model()) is None
    nan = float("nan")
    assert rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": nan, "atr": nan}, _cost_model()) is None


def test_flat_and_none_signal_return_none():
    rm = RiskManager(RiskParams())
    assert rm.build_trade_plan(None, {"atr_pct": 1.0}, _cost_model()) is None
    assert rm.build_trade_plan(_signal(Direction.FLAT), {"atr_pct": 1.0}, _cost_model()) is None


def test_rr_floor_rejection():
    """target_rr below rr_floor -> risk_reward < rr_floor -> None."""
    params = RiskParams(target_rr=1.2, rr_floor=1.5)
    rm = RiskManager(params)
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": 1.0}, _cost_model())
    assert plan is None


def test_edge_cost_gate_rejection():
    """Tiny ATR -> t1_pct below edge_cost_multiple * breakeven -> None.

    min_stop_pct=0.01, atr_pct=0.01 -> stop_pct=clamp(0.015,0.01,3.0)=0.015
    t1_pct = 0.015*1.5 = 0.0225 ; breakeven (entry=1000) = 0.0824092
    edge gate needs 0.0225 >= 3*0.0824092 (=0.2472) -> FAILS -> None.
    """
    params = RiskParams(min_stop_pct=0.01, max_stop_pct=3.0)
    rm = RiskManager(params)
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": 0.01}, _cost_model())
    assert plan is None


def test_stop_pct_clamped_to_max():
    """A huge ATR is clamped to max_stop_pct."""
    rm = RiskManager(RiskParams())
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": 10.0}, _cost_model())
    assert plan is not None
    assert abs(plan.stop_pct - 3.0) < TOL  # clamp(15.0, 0.30, 3.0)


def test_stop_pct_clamped_to_min():
    """A tiny ATR is clamped to min_stop_pct (and still clears the edge gate at defaults)."""
    rm = RiskManager(RiskParams())
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": 0.05}, _cost_model())
    assert plan is not None
    assert abs(plan.stop_pct - 0.30) < TOL  # clamp(0.075, 0.30, 3.0)


def test_position_size_hand_verified():
    """capital=100000, risk_pct=1, entry=1000, stop=985 -> qty=floor(1000/15)=66."""
    out = position_size(100000.0, 1.0, 1000.0, 985.0)
    assert out["qty"] == 66
    assert abs(out["rupee_risk"] - 66 * 15.0) < TOL
    assert abs(out["notional"] - 66 * 1000.0) < TOL


def test_position_size_zero_risk_distance():
    out = position_size(100000.0, 1.0, 1000.0, 1000.0)
    assert out["qty"] == 0
    assert out["rupee_risk"] == 0
    assert out["notional"] == 0
