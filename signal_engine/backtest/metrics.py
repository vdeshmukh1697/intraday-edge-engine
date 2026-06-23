"""Backtest performance metrics (PLAN §6.2).

Computes a frozen :class:`BacktestMetrics` summary from a list of
:class:`~signal_engine.domain.models.PaperPosition`. Percentages stay in
**percent** units throughout (e.g. 0.5 == 0.5%), matching the model contract.

Pure computation — no I/O. Python 3.9 compatible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import List, Tuple

from signal_engine.domain.models import PaperPosition

# Trading days per year — annualization factor for Sharpe/Sortino.
_TRADING_DAYS = 252


@dataclass(frozen=True)
class BacktestMetrics:
    """Immutable summary of backtest / paper-trade performance."""

    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0          # percent
    avg_win_pct: float = 0.0       # mean of positive pnls (percent)
    avg_loss_pct: float = 0.0      # mean of negative pnls (percent, negative)
    profit_factor: float = 0.0
    expectancy_pct: float = 0.0    # mean pnl per trade (percent)
    total_net_pct: float = 0.0     # sum of pnls (percent)
    max_drawdown_pct: float = 0.0  # >= 0
    max_drawdown_days: int = 0
    sharpe: float = 0.0
    sortino: float = 0.0
    avg_hold_minutes: float = 0.0
    equity_curve: List[float] = field(default_factory=list)
    daily_returns: List[Tuple[date, float]] = field(default_factory=list)


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: List[float], ddof: int = 1) -> float:
    """Sample standard deviation (ddof=1 by default). 0.0 if undefined."""
    n = len(values)
    if n - ddof <= 0:
        return 0.0
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / (n - ddof)
    return math.sqrt(var)


def compute_metrics(positions: List[PaperPosition]) -> BacktestMetrics:
    """Compute performance metrics over positions that actually traded.

    Only positions with ``entry_fill is not None and pnl_pct_net is not None``
    are considered. Empty input returns all-zero metrics with empty lists.
    """
    traded = [
        p for p in positions
        if p.entry_fill is not None and p.pnl_pct_net is not None
    ]

    if not traded:
        return BacktestMetrics()

    pnls = [float(p.pnl_pct_net) for p in traded]
    trades = len(pnls)

    positives = [x for x in pnls if x > 0]
    negatives = [x for x in pnls if x < 0]

    wins = len(positives)
    losses = len(negatives)
    win_rate = wins / trades * 100.0

    gross_profit = sum(positives)
    gross_loss = abs(sum(negatives))
    if gross_loss == 0:
        profit_factor = float("inf") if gross_profit > 0 else 0.0
    else:
        profit_factor = gross_profit / gross_loss

    expectancy_pct = _mean(pnls)
    avg_win_pct = _mean(positives)
    avg_loss_pct = _mean(negatives)  # negative or 0.0
    total_net_pct = sum(pnls)

    # --- daily returns: sum pnl per exit date, sorted ascending by date ---
    by_day = {}  # type: ignore[var-annotated]
    for p in traded:
        d = p.exit_ts.date()
        by_day[d] = by_day.get(d, 0.0) + float(p.pnl_pct_net)
    daily_returns = [(d, by_day[d]) for d in sorted(by_day)]
    daily_pcts = [pct for _, pct in daily_returns]

    # --- equity curve: cumulative sum of daily returns ---
    equity_curve = []  # type: List[float]
    running = 0.0
    for pct in daily_pcts:
        running += pct
        equity_curve.append(running)

    # --- max drawdown (percent) and days underwater ---
    max_drawdown_pct = 0.0
    max_drawdown_days = 0
    if len(equity_curve) >= 1:
        peak = equity_curve[0]
        underwater = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd
            if eq < peak:
                underwater += 1
                if underwater > max_drawdown_days:
                    max_drawdown_days = underwater
            else:
                underwater = 0

    # --- Sharpe ---
    sharpe = 0.0
    if len(daily_pcts) >= 2:
        sd = _std(daily_pcts, ddof=1)
        if sd != 0:
            sharpe = _mean(daily_pcts) / sd * math.sqrt(_TRADING_DAYS)

    # --- Sortino (downside deviation over negative daily returns) ---
    sortino = 0.0
    downside = [x for x in daily_pcts if x < 0]
    if len(downside) >= 2:
        dsd = _std(downside, ddof=1)
        if dsd != 0:
            sortino = _mean(daily_pcts) / dsd * math.sqrt(_TRADING_DAYS)

    # --- average hold ---
    holds = [float(p.hold_minutes) for p in traded if p.hold_minutes is not None]
    avg_hold_minutes = _mean(holds)

    return BacktestMetrics(
        trades=trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        profit_factor=profit_factor,
        expectancy_pct=expectancy_pct,
        total_net_pct=total_net_pct,
        max_drawdown_pct=max_drawdown_pct,
        max_drawdown_days=max_drawdown_days,
        sharpe=sharpe,
        sortino=sortino,
        avg_hold_minutes=avg_hold_minutes,
        equity_curve=equity_curve,
        daily_returns=daily_returns,
    )
