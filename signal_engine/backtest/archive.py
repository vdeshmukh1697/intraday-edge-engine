"""Backtest the SAME paper-trade engine over the REAL backfilled archive (not synthetic).

Feeds each archived 1-minute session through ``EngineRunner.on_closed_bar`` (the identical
Indicator -> Signal -> Risk -> Paper-trade path the live engine uses), squares off at the
session close, and aggregates closed trades into the standard metrics. A fresh runner per
session keeps sessions independent (intraday indicators reset daily), which is what we want
when measuring whether a stop/target/cost config actually improves per-trade edge on real data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Set, Tuple

from signal_engine.alerts.null import NullAlerter
from signal_engine.backtest.metrics import BacktestMetrics, compute_metrics
from signal_engine.backtest.walkforward import walk_forward_windows
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
    only_days: Optional[Set[date]] = None,
) -> Tuple[BacktestMetrics, List[PaperPosition]]:
    """Replay up to ``max_sessions`` most-recent real sessions per symbol; return (metrics, ledger).

    Pass ``ml_scorer`` + ``ml_gate`` (0..1) to only take signals the model scores above the gate.

    ``only_days`` (optional, additive): if given, restrict the replay to sessions whose calendar
    date is in this set — used by :func:`run_archive_walkforward` to backtest a single
    out-of-sample window. When set, the ``max_sessions`` recency cap is NOT applied (the window
    set is the scope).
    """
    cal = NSECalendar()
    session = MarketSession(cfg.settings.market, cal)
    ledger: List[PaperPosition] = []

    for sym in symbols:
        hist = store.load_symbol_history(sym)
        if hist is None or hist.empty:
            continue
        grouped = list(hist.groupby(hist.index.normalize()))
        if only_days is not None:
            days = [(d, df) for d, df in grouped if d.date() in only_days]
        else:
            days = grouped[-max_sessions:]
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


@dataclass
class WalkForwardResult:
    """Per-window walk-forward summary over the real archive (V1)."""

    windows: List[Tuple[List[date], List[date], BacktestMetrics]] = field(default_factory=list)

    @property
    def window_pfs(self) -> List[float]:
        """Profit factor of each window's TEST segment, in window order."""
        return [m.profit_factor for _tr, _te, m in self.windows]

    @property
    def median_pf(self) -> float:
        """Median test-window PF (0.0 if no windows). ``inf`` PFs sort to the top."""
        pfs = sorted(self.window_pfs)
        n = len(pfs)
        if n == 0:
            return 0.0
        mid = n // 2
        return pfs[mid] if n % 2 else (pfs[mid - 1] + pfs[mid]) / 2.0

    @property
    def pct_windows_pf_gt_1(self) -> float:
        """Fraction of windows with test PF > 1 (0.0 if no windows)."""
        pfs = self.window_pfs
        if not pfs:
            return 0.0
        return sum(1 for pf in pfs if pf > 1.0) / len(pfs)


def _archive_session_dates(store, symbols: List[str], min_bars: int) -> List[date]:
    """Sorted unique calendar dates that have >= ``min_bars`` for ANY of ``symbols``."""
    days: Set[date] = set()
    for sym in symbols:
        hist = store.load_symbol_history(sym)
        if hist is None or hist.empty:
            continue
        for d, df in hist.groupby(hist.index.normalize()):
            if len(df) >= min_bars:
                days.add(d.date())
    return sorted(days)


def run_archive_walkforward(
    cfg: AppConfig,
    store,
    symbols: List[str],
    train_size: int = 60,
    test_size: int = 20,
    step: Optional[int] = None,
    min_bars: int = 40,
    ml_scorer=None,
    ml_gate: float = 0.0,
) -> WalkForwardResult:
    """Wire the (previously dead) ``walk_forward_windows`` into a REAL archive gate (V1).

    Builds the global sorted list of session dates across ``symbols``, rolls
    ``walk_forward_windows`` over it, and replays each window's TEST dates through
    :func:`run_archive_backtest` (same Indicator->Signal->Risk->Paper path the live engine
    uses). The train segment is reported for provenance but, because the rules engine has no
    fitted parameters, no per-window fitting happens here — the gate measures whether the
    CONFIG holds up out-of-sample across rolling windows. Pass an ``ml_scorer`` to gate signals.

    Returns a :class:`WalkForwardResult`; read ``.median_pf`` and ``.pct_windows_pf_gt_1`` for
    the plan's acceptance bar (median PF > 1 AND > 60% of windows PF > 1).
    """
    all_days = _archive_session_dates(store, symbols, min_bars)
    windows = walk_forward_windows(all_days, train_size=train_size, test_size=test_size, step=step)

    result = WalkForwardResult()
    for train_days, test_days in windows:
        metrics, _ledger = run_archive_backtest(
            cfg, store, symbols, min_bars=min_bars, ml_scorer=ml_scorer, ml_gate=ml_gate,
            only_days=set(test_days))
        result.windows.append((train_days, test_days, metrics))
    return result
