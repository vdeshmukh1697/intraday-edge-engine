"""Risk manager: converts a raw Signal into a capital-agnostic TradePlan (PLAN §5.1/§5.2).

Stops are ATR-based and clamped; targets are R-multiples of the stop distance. A plan
is rejected (returns ``None``) if its reward:risk falls below the floor, or if the
expected move does not clear costs by the configured edge multiple.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Optional

from signal_engine.domain.enums import Direction
from signal_engine.domain.models import Signal, TradePlan
from signal_engine.risk.costs import CostModel


def _is_nan(x) -> bool:
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return True


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class RiskManager:
    """Builds TradePlans from Signals using the configured RiskParams.

    ``risk`` must expose: ``atr_stop_multiple``, ``min_stop_pct``, ``max_stop_pct``,
    ``target_rr``, ``rr_floor``, ``second_target_rr``, ``edge_cost_multiple`` and
    ``max_hold_minutes``.
    """

    def __init__(self, risk):
        self.risk = risk

    def build_trade_plan(
        self,
        signal: Optional[Signal],
        features: dict,
        cost_model: CostModel,
    ) -> Optional[TradePlan]:
        if signal is None or signal.direction == Direction.FLAT:
            return None

        r = self.risk
        entry = signal.entry_hint

        # Resolve ATR% from features, falling back to atr/entry*100.
        atr_pct = features.get("atr_pct")
        if atr_pct is None or _is_nan(atr_pct):
            atr = features.get("atr")
            if atr is None or _is_nan(atr) or entry == 0:
                return None
            atr_pct = float(atr) / entry * 100.0
            if _is_nan(atr_pct):
                return None
        atr_pct = float(atr_pct)

        stop_pct = _clamp(r.atr_stop_multiple * atr_pct, r.min_stop_pct, r.max_stop_pct)

        t1_pct = stop_pct * r.target_rr
        t2_pct = stop_pct * r.second_target_rr

        if signal.direction == Direction.LONG:
            stop_loss = entry * (1 - stop_pct / 100.0)
            targets = [entry * (1 + t1_pct / 100.0), entry * (1 + t2_pct / 100.0)]
        else:  # SHORT
            stop_loss = entry * (1 + stop_pct / 100.0)
            targets = [entry * (1 - t1_pct / 100.0), entry * (1 - t2_pct / 100.0)]

        risk_reward = t1_pct / stop_pct  # == target_rr
        expected_move_pct = t1_pct
        cost_to_break_even_pct = cost_model.breakeven_pct(entry)

        # Gates.
        if risk_reward < r.rr_floor:
            return None
        if expected_move_pct < r.edge_cost_multiple * cost_to_break_even_pct:
            return None

        time_validity = signal.ts + timedelta(minutes=r.max_hold_minutes)

        return TradePlan(
            symbol=signal.symbol,
            ts=signal.ts,
            direction=signal.direction,
            strategy=signal.strategy_name,
            entry=round(entry, 2),
            stop_loss=round(stop_loss, 2),
            stop_pct=round(stop_pct, 4),
            targets=[round(t, 2) for t in targets],
            target_pcts=[round(t1_pct, 4), round(t2_pct, 4)],
            expected_move_pct=round(expected_move_pct, 4),
            risk_reward=round(risk_reward, 4),
            cost_to_break_even_pct=round(cost_to_break_even_pct, 4),
            confidence=signal.confidence,
            reasons=list(signal.reasons),
            time_validity=time_validity,
        )
