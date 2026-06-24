"""Live re-rating advisor — emits an alert the moment a stock's outlook MATERIALLY changes.

The base engine surfaces a plan when a setup first fires. This layer watches the *evolving*
plan on every closed bar (and throttled intra-bar) and tells you when the picture changes the
way a live trader would flag it:

  * a brand-new setup appears, or an existing one is invalidated,
  * the direction flips (long <-> short),
  * the target expands or contracts past a threshold ("target +1.0% -> +3.0%, momentum
    strengthening" / "+1.0% -> +0.5%, cooling"),
  * conviction jumps or fades,
  * price runs most of the way to the target / back toward the stop (intra-bar).

Pure + deterministic: feed it the current plan (or None) per symbol; it returns an alert
string when there's a material change versus the last alerted state, else None. Thresholds are
configurable so it flags real shifts, not noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from signal_engine.domain.enums import Direction
from signal_engine.domain.models import TradePlan


@dataclass
class _Outlook:
    direction: Direction
    entry: float
    stop_pct: float
    target_pct: float
    confidence: float
    ts: datetime


class LiveAdvisor:
    """Per-symbol outlook tracker that emits change alerts (decision-support only)."""

    def __init__(self, target_abs: float = 0.4, target_rel: float = 0.35,
                 conf_delta: float = 12.0, near_target_frac: float = 0.75,
                 min_realert_seconds: float = 0.0, entry_band_bps: float = 0.0):
        # A change is "material" if the target moves >= target_abs (absolute %) OR by
        # >= target_rel (relative), or confidence moves >= conf_delta. near_target_frac drives
        # the intra-bar "approaching T1" alert.
        self.target_abs = target_abs
        self.target_rel = target_rel
        self.conf_delta = conf_delta
        self.near_target_frac = near_target_frac
        # D2 debounce / hysteresis: suppress a repeat alert within min_realert_seconds when
        # the entry has drifted less than entry_band_bps AND the target change isn't material.
        # REVERSAL / INVALIDATION / NEW are always exempt. Defaults (0/0) keep the legacy
        # behaviour byte-identical for callers (and the existing advisor tests) that don't
        # pass these — debounce only engages when the runner wires real config in.
        self.min_realert_seconds = float(min_realert_seconds)
        self.entry_band_bps = float(entry_band_bps)
        self._last: Dict[str, _Outlook] = {}
        self._near_flagged: Dict[str, bool] = {}
        # ts of the last EMITTED alert per symbol (event time) — drives the debounce window.
        self._last_alert_ts: Dict[str, datetime] = {}

    @staticmethod
    def _ml(plan: TradePlan) -> _Outlook:
        return _Outlook(plan.direction, plan.entry, plan.stop_pct,
                        plan.target_pcts[0], plan.confidence, plan.ts)

    def update(self, symbol: str, plan: Optional[TradePlan],
               actionable: bool = True) -> Optional[str]:
        """Feed the current best plan (or None) for ``symbol``; return an alert on material change.

        ``actionable`` (D1, gate-before-advisor): when False, a brand-NEW setup is NOT
        announced and NOT recorded — a symbol that can't be entered (already held, daily
        limit hit, breaker tripped, etc.) must not emit a fresh-looking NEW alert, and must
        still be free to fire NEW once it becomes actionable. Re-rating of an ALREADY-tracked
        symbol (reversal/target/conviction) and INVALIDATION are independent of ``actionable``
        — a thesis change on a position we already hold is always worth flagging. Defaults to
        True so existing callers/tests are unchanged.
        """
        prev = self._last.get(symbol)

        if plan is None:
            if prev is not None:  # a setup we were tracking is no longer valid
                self._last.pop(symbol, None)
                self._near_flagged.pop(symbol, None)
                self._last_alert_ts.pop(symbol, None)
                return (f"⚪ {symbol}: setup invalidated — stand aside "
                        f"(was {prev.direction.value} target +{prev.target_pct:.2f}%).")
            return None

        cur = self._ml(plan)
        if prev is None:
            # Gate-before-advisor: don't announce (or latch) a NEW setup we can't act on.
            if not actionable:
                return None
            self._last[symbol] = cur
            self._last_alert_ts[symbol] = cur.ts
            return (f"🟢 {symbol}: NEW {cur.direction.value} setup — entry ~{cur.entry:.2f}, "
                    f"target +{cur.target_pct:.2f}%, stop -{cur.stop_pct:.2f}%, conf {cur.confidence:.0f}.")

        # Direction flip is always material — exempt from debounce.
        if cur.direction != prev.direction:
            self._last[symbol] = cur
            self._near_flagged.pop(symbol, None)
            self._last_alert_ts[symbol] = cur.ts
            return (f"🔄 {symbol}: REVERSAL {prev.direction.value} → {cur.direction.value} — "
                    f"new entry ~{cur.entry:.2f}, target +{cur.target_pct:.2f}%, conf {cur.confidence:.0f}.")

        # Target expansion / contraction.
        d_abs = abs(cur.target_pct - prev.target_pct)
        d_rel = d_abs / prev.target_pct if prev.target_pct else 0.0
        target_material = d_abs >= self.target_abs or d_rel >= self.target_rel

        # D2 debounce/hysteresis: inside the re-alert window, suppress an echo when the entry
        # has barely drifted and the target change isn't material. Hard-material target moves
        # and reversals/invalidation (handled above) are never debounced.
        if self._debounced(symbol, prev, cur, target_material):
            self._last[symbol] = cur
            return None

        if target_material:
            self._last[symbol] = cur
            self._near_flagged.pop(symbol, None)
            self._last_alert_ts[symbol] = cur.ts
            up = cur.target_pct > prev.target_pct
            why = "momentum strengthening, room to run" if up else "momentum cooling, edge shrinking"
            arrow = "📈" if up else "📉"
            return (f"{arrow} {symbol}: target revised +{prev.target_pct:.2f}% → +{cur.target_pct:.2f}% "
                    f"({why}); stop -{cur.stop_pct:.2f}%, conf {cur.confidence:.0f}.")

        # Conviction shift (target roughly unchanged).
        if abs(cur.confidence - prev.confidence) >= self.conf_delta:
            self._last[symbol] = cur
            self._last_alert_ts[symbol] = cur.ts
            rising = cur.confidence > prev.confidence
            return (f"{'🔼' if rising else '🔽'} {symbol}: conviction {'rising' if rising else 'fading'} "
                    f"{prev.confidence:.0f} → {cur.confidence:.0f} (target +{cur.target_pct:.2f}%).")

        # No material change — keep the freshest numbers without alerting.
        self._last[symbol] = cur
        return None

    def _debounced(self, symbol: str, prev: _Outlook, cur: _Outlook,
                   target_material: bool) -> bool:
        """True if this re-rate should be suppressed as a near-duplicate echo (D2).

        Only suppresses when ALL hold: debounce is configured (min_realert_seconds > 0),
        we alerted recently (within the window, using event time), entry drift is below the
        hysteresis band, and the target move is NOT hard-material. Returns False (no
        suppression) for the legacy default config so behaviour is unchanged.
        """
        if self.min_realert_seconds <= 0:
            return False
        if target_material:
            return False  # a real target move is always worth surfacing
        last_ts = self._last_alert_ts.get(symbol)
        if last_ts is None:
            return False
        elapsed = (cur.ts - last_ts).total_seconds()
        if elapsed >= self.min_realert_seconds:
            return False
        # Entry drift in basis points relative to the previously alerted entry.
        if prev.entry <= 0:
            return False
        drift_bps = abs(cur.entry - prev.entry) / prev.entry * 10000.0
        return drift_bps < self.entry_band_bps

    def on_price(self, symbol: str, price: float) -> Optional[str]:
        """Intra-bar: alert once when price has run most of the way to the tracked target."""
        o = self._last.get(symbol)
        if o is None or self._near_flagged.get(symbol):
            return None
        move = (price - o.entry) / o.entry * 100.0
        progress = (move if o.direction == Direction.LONG else -move)
        if o.target_pct > 0 and progress >= self.near_target_frac * o.target_pct:
            self._near_flagged[symbol] = True
            return (f"🎯 {symbol}: {progress:.2f}% of the +{o.target_pct:.2f}% target reached — "
                    f"approaching T1, consider trailing/booking.")
        return None
