"""Risk manager: converts a raw Signal into a capital-agnostic TradePlan (PLAN §5.1/§5.2).

Stops are ATR-based with a hard SAFETY floor (``hard_floor_pct``) that is DECOUPLED
from the target. Targets are driven by EXPECTED MOVE (plan A2): the smaller of a
volatility target (``target_atr_multiple * atr_pct``) and the distance to the nearest
relevant point-in-time structure level in the trade direction (VWAP band / opening
range / round number), set back by ``structure_buffer_pct``. ``rr_floor`` and the
edge-vs-cost multiple remain pure REJECTION gates — they never define the target.

A plan is rejected (returns ``None``) if its reward:risk falls below the floor, or if
the expected move does not clear costs by the configured edge multiple.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import List, Optional

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


def _getf(obj, name: str, default: float) -> float:
    """Read an optional float attribute, falling back to ``default`` if absent/NaN."""
    val = getattr(obj, name, default)
    if val is None or _is_nan(val):
        return default
    return float(val)


class RiskManager:
    """Builds TradePlans from Signals using the configured RiskParams.

    ``risk`` must expose: ``atr_stop_multiple``, ``min_stop_pct``, ``max_stop_pct``,
    ``rr_floor``, ``second_target_rr``, ``edge_cost_multiple`` and ``max_hold_minutes``.
    Structure-aware exits (PLAN §5.2 / plan A1-A3) additionally read, when present:
    ``hard_floor_pct``, ``target_atr_multiple`` and ``structure_buffer_pct`` (each has a
    safe default so older configs keep working).
    """

    def __init__(self, risk):
        self.risk = risk

    # -- structure-level selection ------------------------------------------------- #
    @staticmethod
    def _structure_target_pct(
        entry: float, direction: Direction, features: dict
    ) -> Optional[float]:
        """Distance (in %) from ``entry`` to the NEAREST relevant structure level in
        the trade direction, or ``None`` if the features dict carries no usable level.

        LONG looks UP at resistance (``vwap_upper``, ``orb_high``, ``round_above``);
        SHORT looks DOWN at support (``vwap_lower``, ``orb_low``, ``round_below``).
        Levels are point-in-time values produced by ``compute_features`` (A3); callers
        that do not provide them simply fall back to the pure volatility target.
        """
        if entry <= 0:
            return None
        if direction == Direction.LONG:
            keys = ("vwap_upper", "orb_high", "round_above")
            levels = [features.get(k) for k in keys]
            # Only resistance strictly above entry is a valid upside target.
            cands = [lv for lv in levels if lv is not None and not _is_nan(lv) and lv > entry]
            if not cands:
                return None
            nearest = min(cands)  # closest resistance above
        else:  # SHORT
            keys = ("vwap_lower", "orb_low", "round_below")
            levels = [features.get(k) for k in keys]
            cands = [lv for lv in levels if lv is not None and not _is_nan(lv) and 0.0 < lv < entry]
            if not cands:
                return None
            nearest = max(cands)  # closest support below
        return abs(nearest - entry) / entry * 100.0

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
        # Reject degenerate entries up front (NaN/<=0) so no downstream math runs on garbage
        # (a NaN/negative entry would otherwise produce nonsensical stops/targets).
        if entry is None or _is_nan(entry) or entry <= 0:
            return None

        # Resolve ATR% from features, falling back to atr/entry*100.
        atr_pct = features.get("atr_pct")
        if atr_pct is None or _is_nan(atr_pct):
            atr = features.get("atr")
            if atr is None or _is_nan(atr):
                return None
            atr_pct = float(atr) / entry * 100.0
            if _is_nan(atr_pct):
                return None
        atr_pct = float(atr_pct)

        # --- A1: STOP (independent of target). ATR drives sizing; a hard safety floor
        # guarantees a minimum stop distance, DECOUPLED from how the target is set. The
        # old min_stop_pct no longer quantizes the target — it only acts as the lower
        # clamp of the *stop* (alongside, and never below, the hard safety floor). ---
        hard_floor_pct = _getf(r, "hard_floor_pct", 0.20)
        # Lower clamp for the stop = the hard safety floor (min_stop_pct kept as a
        # legacy lower bound but never allowed to drop below the safety floor).
        stop_lo = max(hard_floor_pct, _getf(r, "min_stop_pct", 0.0))
        stop_pct = _clamp(r.atr_stop_multiple * atr_pct, stop_lo, r.max_stop_pct)

        # --- A2: TARGET from EXPECTED MOVE (not an R-multiple of a floored stop). ---
        # Primary driver is the VOLATILITY target (target_atr_multiple * ATR%), which de-clusters
        # naturally with each name's volatility (the fix for "every target is ~1%"). A structure
        # level only CAPS the target when it is far enough that the trade still clears the R:R
        # floor — a nearer VWAP/ORB/round level on a liquid name is treated as noise, NOT a hard
        # exit, because capping there truncates the target below the stop and destroys R:R (the
        # cause of the all-targets-collapse-to-the-floor regression).
        target_atr_multiple = _getf(r, "target_atr_multiple", getattr(r, "target_rr", 2.0))
        structure_buffer_pct = _getf(r, "structure_buffer_pct", 0.0)
        rr_floor = _getf(r, "rr_floor", 1.5)

        vol_target_pct = target_atr_multiple * atr_pct
        t1_pct = vol_target_pct
        struct_pct = self._structure_target_pct(entry, signal.direction, features)
        if struct_pct is not None:
            # Buffer the target just inside the structure level (never past it).
            struct_target_pct = struct_pct - structure_buffer_pct
            if struct_target_pct >= rr_floor * stop_pct:
                t1_pct = min(vol_target_pct, struct_target_pct)

        # Degenerate guard only (near-zero ATR with no usable structure): keep a positive
        # target so the cost gate can still reject it. This never raises t1 ABOVE an applied
        # structure cap (when a cap applied, t1 is already >= rr_floor*stop > 0).
        if t1_pct <= 0:
            t1_pct = max(vol_target_pct, hard_floor_pct)

        # T2: an informational extension beyond T1 (the paper trader exits at T1/stop/time, so
        # T2 is guidance only). A simple multiple keeps it consistent with the vol/structure T1
        # instead of re-introducing the old stop-quantized R-multiple.
        t2_pct = t1_pct * _getf(r, "t2_extension_mult", 1.6)
        if t2_pct <= t1_pct:
            t2_pct = t1_pct  # keep monotonic for degenerate configs

        if signal.direction == Direction.LONG:
            stop_loss = entry * (1 - stop_pct / 100.0)
            targets = [entry * (1 + t1_pct / 100.0), entry * (1 + t2_pct / 100.0)]
        else:  # SHORT
            stop_loss = entry * (1 + stop_pct / 100.0)
            targets = [entry * (1 - t1_pct / 100.0), entry * (1 - t2_pct / 100.0)]

        # expected_move reflects the REAL (structure/vol) target, not a fixed R-multiple.
        risk_reward = t1_pct / stop_pct if stop_pct > 0 else 0.0
        expected_move_pct = t1_pct
        cost_to_break_even_pct = cost_model.breakeven_pct(entry)

        # --- Gates (rejection only; they never set the target). ---
        if risk_reward < r.rr_floor:
            return None
        if expected_move_pct < r.edge_cost_multiple * cost_to_break_even_pct:
            return None

        time_validity = signal.ts + timedelta(minutes=r.max_hold_minutes)

        target_pcts: List[float] = [round(t1_pct, 4), round(t2_pct, 4)]
        return TradePlan(
            symbol=signal.symbol,
            ts=signal.ts,
            direction=signal.direction,
            strategy=signal.strategy_name,
            entry=round(entry, 2),
            stop_loss=round(stop_loss, 2),
            stop_pct=round(stop_pct, 4),
            targets=[round(t, 2) for t in targets],
            target_pcts=target_pcts,
            expected_move_pct=round(expected_move_pct, 4),
            risk_reward=round(risk_reward, 4),
            cost_to_break_even_pct=round(cost_to_break_even_pct, 4),
            confidence=signal.confidence,
            reasons=list(signal.reasons),
            time_validity=time_validity,
        )
