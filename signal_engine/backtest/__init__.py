"""Backtesting (PLAN §6): event-driven multi-day replay over the shared core + metrics."""

from signal_engine.backtest.engine import BacktestResult, run_backtest, trading_days
from signal_engine.backtest.metrics import BacktestMetrics, compute_metrics
from signal_engine.backtest.walkforward import time_split, walk_forward_windows

__all__ = [
    "BacktestResult",
    "run_backtest",
    "trading_days",
    "BacktestMetrics",
    "compute_metrics",
    "time_split",
    "walk_forward_windows",
]
