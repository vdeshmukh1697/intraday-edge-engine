"""Position-sizing helpers (PLAN §5.1 / §5.3 M0).

The engine itself is capital-agnostic (``TradePlan`` carries no rupee sizing), but these
helpers let a caller translate a plan into a share count given their own capital and
per-trade risk budget. Two entry points:

* ``position_size`` — the original fixed-fractional primitive (kept byte-stable; callers
  and ``tests/test_risk.py`` depend on its exact signature/output).
* ``size_plan`` — the richer M0 helper: sizes a ``TradePlan`` from ``RiskParams``
  (``account_capital`` * ``risk_per_trade_pct`` / per-share stop distance), then caps the
  result by a Kelly fraction of capital and by a notional ceiling, surfacing ``qty`` and the
  rupee risk for the alert/plan. Capital-agnostic by default (``account_capital`` is a
  reference the user overrides).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional


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


def size_plan(
    plan: Any,
    risk: Any,
    capital: Optional[float] = None,
) -> Dict:
    """Translate a ``TradePlan`` into a concrete share count under a risk budget (M0).

    Sizing logic
    ------------
    1. Fixed-fractional base qty = floor((capital * risk_per_trade_pct%) / per-share stop).
    2. Kelly-style ceiling: cap risked rupees at ``kelly_fraction_cap`` of capital so a
       single trade can never risk more than that fraction even if config is mis-set. This
       is a *cap*, not a Kelly estimator — we have no proven edge to bet a true Kelly on.
    3. Notional ceiling: qty * entry may not exceed ``capital`` (no implicit leverage).

    Reads from ``risk`` (RiskParams): ``account_capital``, ``risk_per_trade_pct``,
    ``kelly_fraction_cap``. Each has a safe fallback so older configs keep working.
    ``capital`` overrides ``risk.account_capital`` when provided.

    Returns a dict: ``qty`` (int >= 0), ``rupee_risk``, ``notional``, ``risk_pct``
    (effective % of capital risked), ``capital`` used. Never raises on degenerate inputs.
    """
    cap = float(capital if capital is not None else getattr(risk, "account_capital", 100000.0))
    risk_pct = float(getattr(risk, "risk_per_trade_pct", 0.5))
    kelly_cap = float(getattr(risk, "kelly_fraction_cap", 0.25))

    entry = float(getattr(plan, "entry", 0.0))
    stop_loss = float(getattr(plan, "stop_loss", 0.0))
    risk_per_share = abs(entry - stop_loss)

    if cap <= 0 or entry <= 0 or risk_per_share == 0:
        return {"qty": 0, "rupee_risk": 0.0, "notional": 0.0,
                "risk_pct": 0.0, "capital": cap}

    # 1) Fixed-fractional base.
    rupee_budget = cap * risk_pct / 100.0
    # 2) Kelly-fraction ceiling on risked capital (a hard cap, not a Kelly bet).
    if kelly_cap > 0:
        rupee_budget = min(rupee_budget, cap * kelly_cap)

    qty = int(math.floor(rupee_budget / risk_per_share))
    # 3) Notional ceiling — never deploy more than capital (no implicit leverage).
    if qty * entry > cap:
        qty = int(math.floor(cap / entry))
    qty = max(0, qty)

    rupee_risk = qty * risk_per_share
    return {
        "qty": qty,
        "rupee_risk": round(rupee_risk, 2),
        "notional": round(qty * entry, 2),
        "risk_pct": round(100.0 * rupee_risk / cap, 4) if cap else 0.0,
        "capital": cap,
    }
