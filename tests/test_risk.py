"""Hand-verified tests for RiskManager.build_trade_plan and position_size (PLAN §5.1/§5.2).

Targets are structure/vol-aware (plan A1-A3): the stop is ATR-based with a hard safety
floor (decoupled from the target), and the target is the EXPECTED MOVE — the smaller of a
volatility target (``target_atr_multiple * atr_pct``) and the distance to the nearest
relevant structure level (set back by ``structure_buffer_pct``). ``rr_floor`` and the
edge-cost multiple are pure rejection gates.
"""

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


def test_long_plan_vol_target_no_structure():
    """entry=1000, atr_pct=1.0, RiskParams defaults, NO structure levels.

    stop_lo = max(hard_floor_pct=0.20, min_stop_pct=0.30) = 0.30
    stop_pct = clamp(1.5*1.0, 0.30, 3.0) = 1.5  -> stop_loss = 985.0
    vol_target = target_atr_multiple(2.0) * atr_pct(1.0) = 2.0 -> t1_pct = 2.0
    t1 = 1000*1.02 = 1020.0
    t2_pct = 2.0 * t2_extension_mult(1.6) = 3.2 -> t2 = 1032.0
    risk_reward = 2.0/1.5 = 1.3333 (>= rr_floor 1.5? NO at defaults) -> see note
    """
    # rr_floor default is 1.5; 2.0/1.5 = 1.333 < 1.5 would reject. Use a permissive floor
    # so we can assert the target math; the gate behaviour is tested separately below.
    rm = RiskManager(RiskParams(rr_floor=1.0))
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr": 10, "atr_pct": 1.0}, _cost_model())
    assert plan is not None
    assert plan.direction == Direction.LONG
    assert abs(plan.stop_pct - 1.5) < TOL
    assert abs(plan.stop_loss - 985.0) < TOL
    assert abs(plan.target_pcts[0] - 2.0) < TOL
    assert abs(plan.targets[0] - 1020.0) < TOL
    assert abs(plan.target_pcts[1] - (2.0 * 1.6)) < TOL  # T2 = T1 * t2_extension_mult
    assert abs(plan.expected_move_pct - 2.0) < TOL
    assert abs(plan.risk_reward - (2.0 / 1.5)) < TOL
    assert abs(plan.cost_to_break_even_pct - 0.1424) < 1e-3
    assert plan.time_validity == TS + timedelta(minutes=90)
    assert plan.confidence == 72.0
    assert plan.reasons == ["above vwap", "adx>20"]
    assert plan.strategy == "vwap_ema_adx"


def test_short_plan_mirror_vol_target():
    """SHORT mirrors LONG: stop above entry, targets below; same vol-target math."""
    rm = RiskManager(RiskParams(rr_floor=1.0))
    plan = rm.build_trade_plan(_signal(Direction.SHORT), {"atr": 10, "atr_pct": 1.0}, _cost_model())
    assert plan is not None
    assert plan.direction == Direction.SHORT
    assert abs(plan.stop_pct - 1.5) < TOL
    assert abs(plan.stop_loss - 1015.0) < TOL  # 1000*(1+0.015)
    assert abs(plan.targets[0] - 980.0) < TOL  # 1000*(1-0.02)
    assert abs(plan.risk_reward - (2.0 / 1.5)) < TOL


# --------------------------------------------------------------------------------
# A2 — target driven by structure level when it is the binding (nearer) constraint
# --------------------------------------------------------------------------------

def test_long_target_capped_by_structure_level():
    """A nearby resistance caps the target below the vol target.

    entry=1000, atr_pct=1.0 -> vol_target = 2.0%. vwap_upper=1012 -> structure distance
    = 1.2%, minus structure_buffer_pct(0.05) = 1.15%. t1_pct = min(2.0, 1.15) = 1.15.
    """
    rm = RiskManager(RiskParams(rr_floor=0.3, edge_cost_multiple=1.0))
    feats = {"atr": 10, "atr_pct": 1.0, "vwap_upper": 1012.0}
    plan = rm.build_trade_plan(_signal(Direction.LONG), feats, _cost_model())
    assert plan is not None
    assert abs(plan.target_pcts[0] - 1.15) < TOL
    assert abs(plan.expected_move_pct - 1.15) < TOL


def test_long_picks_nearest_resistance_among_levels():
    """Among multiple resistances above entry, the NEAREST one binds."""
    rm = RiskManager(RiskParams(rr_floor=0.3, edge_cost_multiple=1.0, structure_buffer_pct=0.0))
    feats = {
        "atr": 10, "atr_pct": 1.0,
        "vwap_upper": 1030.0,   # 3.0% away
        "orb_high": 1008.0,     # 0.8% away  <- nearest
        "round_above": 1015.0,  # 1.5% away
    }
    plan = rm.build_trade_plan(_signal(Direction.LONG), feats, _cost_model())
    assert plan is not None
    assert abs(plan.target_pcts[0] - 0.8) < TOL  # bound by orb_high


def test_short_picks_correct_side_support():
    """SHORT must look DOWN at support, ignoring resistance levels above entry."""
    rm = RiskManager(RiskParams(rr_floor=0.3, edge_cost_multiple=1.0, structure_buffer_pct=0.0))
    feats = {
        "atr": 10, "atr_pct": 1.0,
        "vwap_upper": 1020.0,   # above entry -> irrelevant to a SHORT
        "orb_high": 1015.0,     # above entry -> irrelevant
        "vwap_lower": 990.0,    # 1.0% below  <- nearest support
        "round_below": 985.0,   # 1.5% below
    }
    plan = rm.build_trade_plan(_signal(Direction.SHORT), feats, _cost_model())
    assert plan is not None
    assert abs(plan.target_pcts[0] - 1.0) < TOL  # bound by vwap_lower
    assert plan.targets[0] < 1000.0  # target below entry for a short


def test_long_ignores_levels_below_entry():
    """A 'resistance' level that is actually below entry is not a valid upside target."""
    rm = RiskManager(RiskParams(rr_floor=1.0))
    feats = {"atr": 10, "atr_pct": 1.0, "vwap_upper": 990.0}  # below entry -> ignored
    plan = rm.build_trade_plan(_signal(Direction.LONG), feats, _cost_model())
    assert plan is not None
    # Falls back to the pure vol target (2.0%), not the bogus below-entry level.
    assert abs(plan.target_pcts[0] - 2.0) < TOL


# --------------------------------------------------------------------------------
# A1 — stop has a hard safety floor, decoupled from the target
# --------------------------------------------------------------------------------

def test_hard_floor_respected_on_stop():
    """Small ATR -> stop clamps UP to the hard safety floor, never below it.

    atr_pct=0.12: atr_stop_multiple(1.5)*0.12 = 0.18 < hard_floor 0.20 -> stop floors to 0.20.
    vol_target = target_atr_multiple(2.0)*0.12 = 0.24 -> R:R = 0.24/0.20 = 1.2 >= rr_floor(1.0),
    so a plan still surfaces and we can assert the floored stop. (A genuinely tiny ATR like 0.01
    is correctly REJECTED — a 0.02% target vs a 0.20% stop is not a tradeable R:R.)
    """
    rm = RiskManager(RiskParams(min_stop_pct=0.0, hard_floor_pct=0.20, rr_floor=1.0,
                                edge_cost_multiple=1.0))
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": 0.12}, _cost_model())
    assert plan is not None
    assert abs(plan.stop_pct - 0.20) < TOL  # clamped up to the hard floor


def test_stop_independent_of_target_multiple():
    """Changing the target driver must not move the stop (decoupling)."""
    feats = {"atr": 10, "atr_pct": 1.0}
    stop_a = RiskManager(RiskParams(target_atr_multiple=2.0, rr_floor=1.0)).build_trade_plan(
        _signal(Direction.LONG), feats, _cost_model()
    ).stop_pct
    stop_b = RiskManager(RiskParams(target_atr_multiple=4.0, rr_floor=1.0)).build_trade_plan(
        _signal(Direction.LONG), feats, _cost_model()
    ).stop_pct
    assert abs(stop_a - stop_b) < TOL  # identical stop, different target multiples


def test_stop_pct_clamped_to_max():
    """A huge ATR is clamped to max_stop_pct."""
    rm = RiskManager(RiskParams(rr_floor=1.0))
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": 10.0}, _cost_model())
    assert plan is not None
    assert abs(plan.stop_pct - 3.0) < TOL  # clamp(15.0, 0.30, 3.0)


# --------------------------------------------------------------------------------
# A2 — targets DE-CLUSTER: they vary across ATR/levels (not a single ~1% bucket)
# --------------------------------------------------------------------------------

def test_targets_decluster_across_inputs():
    """Distinct ATR/structure inputs must produce a SPREAD of target_pcts, not one bucket.

    The old formula quantized t1 to stop_pct*target_rr where stop_pct was clamped to a
    narrow band, collapsing most targets onto ~1-2%. Here different inputs must yield
    visibly different targets.
    """
    rm = RiskManager(RiskParams(rr_floor=0.3, edge_cost_multiple=1.0, structure_buffer_pct=0.0))
    scenarios = [
        {"atr_pct": 0.5},                                  # vol target 1.0
        {"atr_pct": 1.0},                                  # vol target 2.0
        {"atr_pct": 1.5},                                  # vol target 3.0
        {"atr_pct": 2.0, "vwap_upper": 1011.0},            # capped at 1.1 by structure
        {"atr_pct": 2.0, "orb_high": 1024.0},              # capped at 2.4 by structure
    ]
    targets = []
    for feats in scenarios:
        plan = rm.build_trade_plan(_signal(Direction.LONG), feats, _cost_model())
        assert plan is not None
        targets.append(round(plan.target_pcts[0], 4))
    # All distinct -> de-clustered (no single bucket).
    assert len(set(targets)) == len(targets), targets
    assert max(targets) - min(targets) > 1.0  # meaningful spread


# --------------------------------------------------------------------------------
# Resolution / fallback / gate behaviour
# --------------------------------------------------------------------------------

def test_atr_pct_derived_from_atr_when_missing():
    """atr_pct absent -> derived as atr/entry*100 = 10/1000*100 = 1.0 -> stop_pct 1.5."""
    rm = RiskManager(RiskParams(rr_floor=1.0))
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
    """A high rr_floor relative to the vol-target/stop ratio rejects the plan.

    atr_pct=1.0 -> stop_pct=1.5, vol_target t1_pct=2.0 -> risk_reward=1.333.
    rr_floor=1.5 > 1.333 -> rejected.
    """
    rm = RiskManager(RiskParams(rr_floor=1.5))
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": 1.0}, _cost_model())
    assert plan is None


def test_edge_cost_gate_rejection():
    """Tiny expected move (after hard-floor) below edge_cost_multiple*breakeven -> None.

    atr_pct=0.01 -> vol_target ~0.02, floored to hard_floor_pct(0.20) for the gate.
    breakeven(entry=1000) ~ 0.0824; gate needs 0.20 >= 3*0.0824 (=0.247) -> FAILS.
    """
    rm = RiskManager(RiskParams(rr_floor=1.0))
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": 0.01}, _cost_model())
    assert plan is None


def test_backward_compat_legacy_params_only():
    """A RiskParams missing the new A1-A3 fields still builds a plan (safe defaults).

    Simulates an older config object that lacks hard_floor_pct/target_atr_multiple/
    structure_buffer_pct via a tiny stand-in exposing only the legacy attributes.
    """
    class LegacyRisk:
        atr_stop_multiple = 1.5
        min_stop_pct = 0.30
        max_stop_pct = 3.0
        target_rr = 2.0
        rr_floor = 1.0
        second_target_rr = 3.0
        edge_cost_multiple = 1.0
        max_hold_minutes = 90

    rm = RiskManager(LegacyRisk())
    plan = rm.build_trade_plan(_signal(Direction.LONG), {"atr_pct": 1.0}, _cost_model())
    assert plan is not None
    # target_atr_multiple defaults to legacy target_rr (2.0) -> t1_pct = 2.0.
    assert abs(plan.target_pcts[0] - 2.0) < TOL
    # hard_floor_pct defaults to 0.20; stop_lo = max(0.20, 0.30) = 0.30; stop = 1.5.
    assert abs(plan.stop_pct - 1.5) < TOL


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
