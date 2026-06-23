"""Strategy Health Scorer (PLAN §6.6): the engine's self-monitoring conscience.

Computes a rolling 0-100 health score over the most recent closed/traded paper
positions and raises a degradation alert when the book goes sour. This module is
deliberately self-contained — it derives its own sub-metrics (hit rate, profit
factor, expectancy, Brier calibration error, drawdown) from the trade list rather
than importing the backtest metrics module, so the two can evolve independently.

Conventions
-----------
* Percentages are **percent** (e.g. 0.5 == 0.5%), matching the domain models.
* ``confidence`` is 0..100; the Brier outcome is 1.0 for a win, 0.0 for a loss.
* Only closed, actually-traded positions count (``entry_fill`` and
  ``pnl_pct_net`` both present).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


def _clamp01(x: float) -> float:
    """Clamp a value to the closed interval [0, 1]."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


@dataclass(frozen=True)
class HealthScore:
    """Snapshot of strategy health over a rolling window of closed trades.

    ``status`` is one of "green" | "amber" | "red" | "insufficient". When
    insufficient (too few trades), the metric fields are zero/None and
    ``components`` is empty.
    """

    overall: float                  # 0..100 composite score
    hit_rate: float                 # percent of trades that reached T1
    profit_factor: float            # gross profit / gross loss (inf if no losses)
    expectancy_pct: float           # mean net P&L percent per trade
    calibration_error: float        # Brier score, 0..1 (lower is better)
    max_drawdown_pct: float         # peak-to-trough of the equity curve, >= 0
    drift: Optional[float]          # expectancy delta vs baseline, if provided
    status: str
    window_trades: int
    components: Dict[str, float]     # normalized 0..1 contribution of each metric


def compute_health(
    positions: List["PaperPosition"],  # noqa: F821 - domain type, avoid import cycle
    baseline: Optional[Dict[str, float]] = None,
    window: int = 30,
    min_trades: int = 5,
) -> HealthScore:
    """Score the most recent ``window`` closed/traded positions.

    Returns an "insufficient" HealthScore when fewer than ``min_trades`` qualify.
    """
    # Filter to actually-traded, closed positions (those with realized P&L).
    traded = [
        p for p in positions
        if p.entry_fill is not None and p.pnl_pct_net is not None
    ]
    window_slice = traded[-window:] if window > 0 else traded
    n = len(window_slice)

    if n < min_trades:
        return HealthScore(
            overall=0.0,
            hit_rate=0.0,
            profit_factor=0.0,
            expectancy_pct=0.0,
            calibration_error=0.0,
            max_drawdown_pct=0.0,
            drift=None,
            status="insufficient",
            window_trades=n,
            components={},
        )

    pnls = [float(p.pnl_pct_net) for p in window_slice]

    # Hit rate (reached T1) — uses the `won` flag.
    wins = sum(1 for p in window_slice if p.won)
    hit_rate = 100.0 * wins / n

    # Profit factor = gross profit / |gross loss|.
    gross_profit = sum(x for x in pnls if x > 0.0)
    gross_loss = sum(x for x in pnls if x < 0.0)
    if gross_loss == 0.0:
        profit_factor = float("inf") if gross_profit > 0.0 else 0.0
    else:
        profit_factor = gross_profit / abs(gross_loss)

    # Expectancy = mean net P&L percent.
    expectancy_pct = sum(pnls) / n

    # Calibration: Brier score = mean((p - outcome)^2), p = confidence/100.
    brier_terms = []
    for p in window_slice:
        prob = float(p.plan.confidence) / 100.0
        outcome = 1.0 if p.won else 0.0
        brier_terms.append((prob - outcome) ** 2)
    calibration_error = sum(brier_terms) / n

    # Max drawdown on the cumulative-sum equity curve of pnl_pct_net.
    equity = 0.0
    peak = 0.0
    max_drawdown_pct = 0.0
    for x in pnls:
        equity += x
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd

    # Drift vs baseline expectancy, if a baseline was supplied.
    drift: Optional[float] = None
    if baseline is not None and "expectancy_pct" in baseline:
        drift = expectancy_pct - float(baseline["expectancy_pct"])

    # Normalized 0..1 components.
    pf_for_norm = 3.0 if profit_factor == float("inf") else min(profit_factor, 3.0)
    c_hit = _clamp01(hit_rate / 60.0)
    c_pf = _clamp01((pf_for_norm - 1.0) / (3.0 - 1.0))
    c_exp = _clamp01(expectancy_pct / 0.5)
    c_cal = _clamp01(1.0 - (calibration_error / 0.25))
    c_dd = _clamp01(1.0 - (max_drawdown_pct / 10.0))

    components = {
        "hit": c_hit,
        "pf": c_pf,
        "exp": c_exp,
        "cal": c_cal,
        "dd": c_dd,
    }

    overall = 100.0 * (
        0.30 * c_hit
        + 0.25 * c_pf
        + 0.20 * c_exp
        + 0.15 * c_cal
        + 0.10 * c_dd
    )

    if overall >= 70.0:
        status = "green"
    elif overall >= 45.0:
        status = "amber"
    else:
        status = "red"

    return HealthScore(
        overall=overall,
        hit_rate=hit_rate,
        profit_factor=profit_factor,
        expectancy_pct=expectancy_pct,
        calibration_error=calibration_error,
        max_drawdown_pct=max_drawdown_pct,
        drift=drift,
        status=status,
        window_trades=n,
        components=components,
    )


def detect_degradation(
    current: HealthScore,
    threshold: float = 50.0,
    baseline_overall: Optional[float] = None,
    drop: float = 15.0,
) -> Optional[str]:
    """Return an alert string when the current health warrants attention.

    Fires when the score is below ``threshold`` (absolute floor) OR has fallen
    more than ``drop`` points below ``baseline_overall`` (relative regression).
    Returns ``None`` for a healthy book or an "insufficient" snapshot.
    """
    if current.status == "insufficient":
        return None

    if current.overall < threshold:
        return (
            "Health degraded: overall {:.1f} < {:.1f}".format(
                current.overall, threshold
            )
        )

    if baseline_overall is not None and current.overall < baseline_overall - drop:
        return (
            "Health degraded: overall {:.1f} dropped > {:.1f} below baseline "
            "{:.1f}".format(current.overall, drop, baseline_overall)
        )

    return None
