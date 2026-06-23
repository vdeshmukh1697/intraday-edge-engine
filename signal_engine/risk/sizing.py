"""Optional position-sizing helper (PLAN §5.1).

The engine itself is capital-agnostic (TradePlans carry no rupee sizing), but this
helper lets a caller translate a plan into a share count given their own capital and
per-trade risk budget.
"""

from __future__ import annotations

import math
from typing import Dict


def position_size(capital: float, risk_pct: float, entry: float, stop_loss: float) -> Dict:
    """Size a position by fixed-fractional risk.

    ``risk_pct`` is a percent (e.g. ``1`` == 1% of capital). Returns ``qty`` (int >= 0),
    ``rupee_risk`` and ``notional``. If entry == stop (zero risk distance), qty is 0.
    """
    risk_per_share = abs(entry - stop_loss)
    if risk_per_share == 0:
        qty = 0
    else:
        qty = int(math.floor((capital * risk_pct / 100.0) / risk_per_share))
        qty = max(0, qty)

    return {
        "qty": qty,
        "rupee_risk": qty * risk_per_share,
        "notional": qty * entry,
    }
