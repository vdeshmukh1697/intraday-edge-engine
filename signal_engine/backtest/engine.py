"""Backtest engine (PLAN §6.1) — multi-day event replay through the SHARED core.

Crucially, this reuses the *exact* live ``EngineRunner`` pipeline (tick -> bar ->
features -> strategy -> risk -> paper-trade), one trading day at a time. Because it is
the same code path as live, backtest results cannot silently diverge from live behaviour,
and closed-bar / next-bar-fill discipline (anti-lookahead) is inherited for free.

Synthetic multi-day data (no live feed). The health score is computed over the resulting
paper-trade ledger so a backtest also tells you how healthy the strategy looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional, Tuple

from signal_engine.alerts.null import NullAlerter
from signal_engine.backtest.metrics import BacktestMetrics, compute_metrics
from signal_engine.config import AppConfig
from signal_engine.domain.models import PaperPosition
from signal_engine.engine.runner import EngineRunner
from signal_engine.health.scorer import HealthScore, compute_health
from signal_engine.market.calendar import NSECalendar
from signal_engine.market.session import MarketSession
from signal_engine.strategies.base import create_strategy

_REGIMES = ["trend_up", "trend_down", "choppy"]


def trading_days(start: date, n: int, calendar: Optional[NSECalendar] = None) -> List[date]:
    """The next ``n`` NSE trading days on/after ``start`` (skips weekends/holidays)."""
    cal = calendar or NSECalendar()
    days: List[date] = []
    d = start
    guard = 0
    while len(days) < n and guard < n * 10 + 30:
        if cal.is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
        guard += 1
    return days


@dataclass
class BacktestResult:
    days: List[date]
    ledger: List[PaperPosition]
    metrics: BacktestMetrics
    health: HealthScore
    picks: int = 0
    per_day_pnl: List[Tuple[date, float]] = field(default_factory=list)


def run_backtest(
    cfg: AppConfig,
    symbols: List[str],
    start: date,
    n_days: int,
    seed: int = 42,
) -> BacktestResult:
    cal = NSECalendar()
    days = trading_days(start, n_days, cal)

    ledger: List[PaperPosition] = []
    picks = 0
    per_day: List[Tuple[date, float]] = []

    for i, d in enumerate(days):
        # Rotate regimes per day so symbols trend on some days (deterministic).
        regime_map = {sym: _REGIMES[(i + j) % len(_REGIMES)] for j, sym in enumerate(symbols)}
        # Lazy import to avoid a hard dependency cycle at module import time.
        from signal_engine.brokers.mock import MockBroker

        broker = MockBroker(day=d, seed=seed + i, regime_map=regime_map)
        strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
        session = MarketSession(cfg.settings.market, cal)
        runner = EngineRunner(cfg, broker, strategy, session, NullAlerter())
        summary = runner.replay(symbols)

        day_closed = [p for p in summary.closed if p.entry_fill is not None]
        ledger.extend(day_closed)
        picks += len(summary.picks)
        per_day.append((d, round(sum(p.pnl_pct_net or 0.0 for p in day_closed), 4)))

    metrics = compute_metrics(ledger)
    # Health over the whole ledger (window = all; min_trades=1 so it always returns).
    health = compute_health(ledger, window=max(len(ledger), 1), min_trades=1)

    return BacktestResult(
        days=days, ledger=ledger, metrics=metrics, health=health,
        picks=picks, per_day_pnl=per_day,
    )
