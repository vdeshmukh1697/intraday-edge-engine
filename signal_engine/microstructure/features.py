"""Per-bar microstructure features — the DIRECTIONAL shadow signal (measured, never gating).

Two independent directional reads, computed from the raw ticks that formed one 1-minute bar:

1. **Volume delta / CVD (tick-rule)** — works in Dhan QUOTE mode (LTP + cumulative volume).
   Each tick's volume increment (cum_vol_t - cum_vol_{t-1}) is signed by the *tick rule*:
   an uptick (ltp rises) is buyer-initiated (+), a downtick is seller-initiated (-), an
   unchanged tick carries the previous sign. Summed over the bar = signed volume (CVD);
   `signed_vol_frac` = CVD / total bar volume in [-1, +1]. Positive => net buying pressure.
   It is a PROXY (true buy/sell classification needs trade-condition data we don't get), but
   the tick rule is the standard, well-studied approximation.

2. **Order-book imbalance (OBI)** — needs Dhan FULL mode (best bid/ask *quantities*).
   Per tick: (bidQty - askQty) / (bidQty + askQty) in [-1, +1]; averaged over the bar.
   Positive => more size resting on the bid => buy pressure. None when depth is absent.

`dir_score` blends whatever is available into one [-1, +1] directional lean. This module is
PURE (no I/O) so every number is unit-testable by hand. Nothing here ever touches a trade
decision — the collector logs it and the scorer grades sign(dir_score) vs the NEXT bar's
realized return, exactly like the tip-mention shadow feature: trust is earned in data first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class BarSignal:
    symbol: str
    bar_ts: datetime
    n_ticks: int
    bar_ret_pct: Optional[float]      # (last ltp - first ltp) / first ltp * 100, this bar
    cvd: float                        # signed volume (tick-rule), shares
    signed_vol_frac: Optional[float]  # cvd / total volume, in [-1, +1]; None if no volume
    ob_imbalance: Optional[float]     # mean (bidQ-askQ)/(bidQ+askQ); None in QUOTE mode
    spread_bps: Optional[float]       # mean (ask-bid)/mid * 1e4; None without quotes
    dir_score: float                  # blended directional lean in [-1, +1]


def _tick_rule_signs(ltps: Sequence[float]) -> List[int]:
    """Classify each tick +1/-1/carry by the tick rule (first tick = 0, no prior)."""
    signs: List[int] = []
    prev_sign = 0
    prev_ltp: Optional[float] = None
    for ltp in ltps:
        if prev_ltp is None:
            signs.append(0)
        elif ltp > prev_ltp:
            prev_sign = 1
            signs.append(1)
        elif ltp < prev_ltp:
            prev_sign = -1
            signs.append(-1)
        else:
            signs.append(prev_sign)  # unchanged -> carry the last direction
        prev_ltp = ltp
    return signs


def compute_bar_signal(symbol: str, bar_ts: datetime, ticks: Sequence) -> Optional[BarSignal]:
    """Compute the microstructure signal for one bar from its ordered ticks.

    ``ticks`` are Tick-like objects with ``.ltp``, ``.volume`` (cumulative day volume) and
    optional ``.bid/.ask/.bid_qty/.ask_qty``. Returns None for an empty bar.
    """
    ticks = list(ticks)
    if not ticks:
        return None

    ltps = [float(t.ltp) for t in ticks if t.ltp is not None]
    bar_ret = None
    if len(ltps) >= 2 and ltps[0]:
        bar_ret = (ltps[-1] - ltps[0]) / ltps[0] * 100.0

    # --- volume delta (tick rule) ---
    signs = _tick_rule_signs([float(t.ltp) for t in ticks])
    cvd = 0.0
    total_vol = 0.0
    prev_cum: Optional[int] = None
    for t, s in zip(ticks, signs):
        cum = t.volume
        if cum is None:
            continue
        if prev_cum is not None:
            inc = max(0, int(cum) - int(prev_cum))  # cumulative day volume is monotone
            cvd += s * inc
            total_vol += inc
        prev_cum = int(cum)
    signed_vol_frac = (cvd / total_vol) if total_vol > 0 else None

    # --- order-book imbalance (FULL mode only) ---
    imbs: List[float] = []
    spreads: List[float] = []
    for t in ticks:
        bq, aq = getattr(t, "bid_qty", None), getattr(t, "ask_qty", None)
        if bq is not None and aq is not None and (bq + aq) > 0:
            imbs.append((bq - aq) / (bq + aq))
        bid, ask = getattr(t, "bid", None), getattr(t, "ask", None)
        if bid and ask and ask > 0 and bid > 0:
            mid = (bid + ask) / 2.0
            if mid > 0:
                spreads.append((ask - bid) / mid * 1e4)
    ob_imbalance = (sum(imbs) / len(imbs)) if imbs else None
    spread_bps = (sum(spreads) / len(spreads)) if spreads else None

    # --- blend into one directional lean ---
    # Average whichever components exist; both live in [-1, +1] and share a sign convention
    # (positive = up-pressure). Neither is trusted yet — the scorer decides if they predict.
    parts = [p for p in (signed_vol_frac, ob_imbalance) if p is not None]
    dir_score = (sum(parts) / len(parts)) if parts else 0.0

    return BarSignal(
        symbol=symbol, bar_ts=bar_ts, n_ticks=len(ticks), bar_ret_pct=bar_ret,
        cvd=cvd, signed_vol_frac=signed_vol_frac, ob_imbalance=ob_imbalance,
        spread_bps=spread_bps, dir_score=dir_score,
    )
