"""VWAP + EMA-cross + ADX-gated trend strategy (PLAN §4.4, the Phase-1 strategy).

Weighted-rule ensemble producing a 0–100 confidence with explainable reasons.
Reads precomputed indicator features from ``ctx.features`` (keys defined in
``signal_engine.indicators``). Pure decision logic — no I/O.

Long setup:  price above VWAP, fast EMA above slow EMA, trend strong (ADX>=min),
             volume confirming (RVOL>=min), RSI not already overbought.
Short setup: mirror image.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from signal_engine.domain.enums import Direction
from signal_engine.domain.models import Signal
from signal_engine.strategies.base import Strategy, StrategyContext, register_strategy

# Confidence weights (sum = 1.0) — PLAN §4.4 example model.
_WEIGHTS = {
    "vwap_side": 0.30,
    "ema_align": 0.25,
    "adx_strong": 0.20,
    "rvol_confirm": 0.15,
    "rsi_ok": 0.10,
}

_DEFAULTS = {
    "ema_fast": 9,
    "ema_slow": 21,
    "rsi_period": 14,
    "adx_period": 14,
    "adx_min": 20.0,
    "adx_hard_floor": 15.0,   # below this -> suppress entirely (PLAN §4.4 gate)
    "atr_period": 14,
    "rvol_min": 1.2,
    "rsi_overbought": 70.0,
    "rsi_oversold": 30.0,
    "confidence_threshold": 60.0,
}


@register_strategy
class VwapEmaAdxStrategy(Strategy):
    name = "vwap_ema_adx"

    def __init__(self, params: Optional[Dict[str, float]] = None):
        merged = dict(_DEFAULTS)
        if params:
            merged.update(params)
        super().__init__(merged)

    def required_indicators(self) -> List[str]:
        return [
            "close", "vwap", "ema_fast", "ema_slow", "ema_fast_prev",
            "ema_slow_prev", "rsi", "adx", "atr", "atr_pct", "rvol", "bar_count",
        ]

    def on_bar(self, ctx: StrategyContext) -> Optional[Signal]:
        f = ctx.features
        # Need a fully-formed feature set (enough history for EMAs/ADX/ATR).
        required = ("close", "vwap", "ema_fast", "ema_slow", "rsi", "adx", "atr", "rvol")
        if any(f.get(k) is None for k in required):
            return None
        for k in required:
            v = f.get(k)
            if v != v:  # NaN guard
                return None

        adx = f["adx"]
        # Hard gate: no trend strength -> no trade regardless of other signals.
        if adx < self.params["adx_hard_floor"]:
            return None

        long_conf, long_reasons = self._score(ctx, Direction.LONG)
        short_conf, short_reasons = self._score(ctx, Direction.SHORT)

        threshold = self.params["confidence_threshold"]
        if long_conf >= short_conf and long_conf >= threshold:
            return self._signal(ctx, Direction.LONG, long_conf, long_reasons)
        if short_conf > long_conf and short_conf >= threshold:
            return self._signal(ctx, Direction.SHORT, short_conf, short_reasons)
        return None

    def _score(self, ctx: StrategyContext, direction: Direction) -> Tuple[float, List[str]]:
        f = ctx.features
        p = self.params
        reasons: List[str] = []
        score = 0.0
        is_long = direction == Direction.LONG

        # 1) VWAP side
        if (is_long and f["close"] > f["vwap"]) or (not is_long and f["close"] < f["vwap"]):
            score += _WEIGHTS["vwap_side"]
            reasons.append("above VWAP" if is_long else "below VWAP")

        # 2) EMA alignment (+ note fresh cross if present)
        ema_aligned = (
            f["ema_fast"] > f["ema_slow"] if is_long else f["ema_fast"] < f["ema_slow"]
        )
        if ema_aligned:
            score += _WEIGHTS["ema_align"]
            fast_prev = f.get("ema_fast_prev")
            slow_prev = f.get("ema_slow_prev")
            fresh_cross = False
            if fast_prev is not None and slow_prev is not None:
                fresh_cross = (
                    (is_long and fast_prev <= slow_prev)
                    or (not is_long and fast_prev >= slow_prev)
                )
            reasons.append("EMA cross up" if (is_long and fresh_cross)
                           else "EMA cross down" if (not is_long and fresh_cross)
                           else ("EMA fast>slow" if is_long else "EMA fast<slow"))

        # 3) ADX trend strength
        if f["adx"] >= p["adx_min"]:
            score += _WEIGHTS["adx_strong"]
            reasons.append(f"ADX {f['adx']:.0f}")

        # 4) Volume confirmation
        if f["rvol"] >= p["rvol_min"]:
            score += _WEIGHTS["rvol_confirm"]
            reasons.append(f"RVOL {f['rvol']:.1f}x")

        # 5) RSI not stretched against the trade
        rsi = f["rsi"]
        rsi_ok = rsi < p["rsi_overbought"] if is_long else rsi > p["rsi_oversold"]
        if rsi_ok:
            score += _WEIGHTS["rsi_ok"]
            reasons.append(f"RSI {rsi:.0f}")

        return score * 100.0, reasons

    def _signal(
        self, ctx: StrategyContext, direction: Direction, confidence: float, reasons: List[str]
    ) -> Signal:
        return Signal(
            symbol=ctx.symbol,
            ts=ctx.ts,
            direction=direction,
            confidence=round(confidence, 1),
            strategy_name=self.name,
            entry_hint=ctx.features["close"],
            reasons=reasons,
        )
