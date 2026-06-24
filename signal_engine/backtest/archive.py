"""Backtest the SAME paper-trade engine over the REAL backfilled archive (not synthetic).

Feeds each archived 1-minute session through ``EngineRunner.on_closed_bar`` (the identical
Indicator -> Signal -> Risk -> Paper-trade path the live engine uses), squares off at the
session close, and aggregates closed trades into the standard metrics. A fresh runner per
session keeps sessions independent (intraday indicators reset daily), which is what we want
when measuring whether a stop/target/cost config actually improves per-trade edge on real data.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from signal_engine.alerts.null import NullAlerter
from signal_engine.backtest.metrics import BacktestMetrics, compute_metrics
from signal_engine.config import AppConfig
from signal_engine.domain.models import Bar, PaperPosition
from signal_engine.engine.runner import EngineRunner
from signal_engine.market.calendar import NSECalendar
from signal_engine.market.session import MarketSession
from signal_engine.strategies.base import create_strategy


def variant_cfg(cfg: AppConfig, **risk_overrides) -> AppConfig:
    """Return a copy of ``cfg`` with cfg.risk.risk fields overridden (pydantic, immutable copy)."""
    new_risk = cfg.risk.risk.model_copy(update=risk_overrides)
    new_riskconfig = cfg.risk.model_copy(update={"risk": new_risk})
    return cfg.model_copy(update={"risk": new_riskconfig})


def run_archive_backtest(
    cfg: AppConfig,
    store,
    symbols: List[str],
    max_sessions: int = 120,
    min_bars: int = 40,
    ml_scorer=None,
    ml_gate: float = 0.0,
) -> Tuple[BacktestMetrics, List[PaperPosition]]:
    """Replay up to ``max_sessions`` most-recent real sessions per symbol; return (metrics, ledger).

    Pass ``ml_scorer`` + ``ml_gate`` (0..1) to only take signals the model scores above the gate.
    """
    cal = NSECalendar()
    session = MarketSession(cfg.settings.market, cal)
    ledger: List[PaperPosition] = []

    for sym in symbols:
        hist = store.load_symbol_history(sym)
        if hist is None or hist.empty:
            continue
        days = list(hist.groupby(hist.index.normalize()))[-max_sessions:]
        for _day, df in days:
            if len(df) < min_bars:
                continue
            strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
            runner = EngineRunner(cfg, None, strategy, session, NullAlerter(),
                                  ml_scorer=ml_scorer, ml_gate=ml_gate)
            last: Optional[Bar] = None
            for ts, row in df.iterrows():
                bar = Bar(symbol=sym, ts=ts.to_pydatetime(), open=float(row["open"]),
                          high=float(row["high"]), low=float(row["low"]),
                          close=float(row["close"]), volume=int(row["volume"]))
                runner.on_closed_bar(bar)
                last = bar
            if last is not None:  # force EOD square-off, same as a live session
                for pos in runner.paper.force_square_off(last):
                    runner._on_position_closed(pos, last)
            ledger.extend(p for p in runner.summary.closed if p.entry_fill is not None)

    return compute_metrics(ledger), ledger
