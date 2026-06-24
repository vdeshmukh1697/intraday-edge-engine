"""End-to-end engine replay test (PLAN §2 pipeline). Deterministic with fixed seed.

Asserts invariants rather than exact P&L (synthetic): the pipeline runs, gates hold,
no entries after the cutoff, and every position is accounted for by end of day.
"""

import io
from datetime import date, time

from signal_engine.alerts.console import ConsoleAlerter
from signal_engine.brokers.mock import MockBroker
from signal_engine.config import load_config
from signal_engine.domain.enums import PositionStatus
from signal_engine.engine.runner import EngineRunner
from signal_engine.market.calendar import NSECalendar
from signal_engine.market.session import MarketSession
from signal_engine.strategies.base import create_strategy

_SYMBOLS = ["RELIANCE", "HDFCBANK", "INFY", "TCS", "ICICIBANK"]
_REGIMES = {"RELIANCE": "trend_down", "HDFCBANK": "trend_up", "INFY": "trend_down",
            "TCS": "trend_down", "ICICIBANK": "trend_up"}


def _run(seed=7):
    cfg = load_config()
    day = date(2025, 6, 23)  # Monday, trading day
    broker = MockBroker(day=day, seed=seed, regime_map=_REGIMES)
    strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
    session = MarketSession(cfg.settings.market, NSECalendar())
    alerter = ConsoleAlerter(stream=io.StringIO())  # silence output
    runner = EngineRunner(cfg, broker, strategy, session, alerter)
    summary = runner.replay(_SYMBOLS)
    return cfg, runner, summary


def test_pipeline_runs_and_processes_all_bars():
    cfg, runner, summary = _run()
    # 375 one-minute bars per symbol.
    assert summary.bars_processed == 375 * len(_SYMBOLS)


def test_trending_day_surfaces_picks():
    _, _, summary = _run()
    assert len(summary.picks) >= 1


def test_live_freshness_marks_on_tick_not_bar_open():
    """Regression: a 1-min bar's OPEN ts is ~60s behind 'now' at close. Freshness must be
    marked on tick arrival (wall-clock), so an arriving tick keeps the feed 'fresh' and live
    entries are NOT suppressed. (Marking off bar.ts suppressed every live entry.)"""
    import datetime as _dt

    import pytz

    from signal_engine.domain.models import Tick

    cfg = load_config()
    broker = MockBroker(day=date(2025, 6, 23), seed=1, regime_map=_REGIMES)
    strategy = create_strategy(cfg.settings.strategy.active, cfg.settings.strategy.params)
    session = MarketSession(cfg.settings.market, NSECalendar())
    runner = EngineRunner(cfg, broker, strategy, session, ConsoleAlerter(stream=io.StringIO()))
    runner.enforce_freshness = True  # live behaviour

    now = _dt.datetime.now(pytz.timezone("Asia/Kolkata"))
    runner.on_tick(Tick(symbol="RELIANCE", ts=now, ltp=2900.0, volume=100))
    assert runner.freshness.is_stale() is False        # a tick just arrived -> fresh
    assert runner.freshness.max_staleness_seconds >= 30.0  # tolerates sparse ticks


def test_all_picks_respect_risk_gates():
    cfg, _, summary = _run()
    rr_floor = cfg.risk.risk.rr_floor
    k = cfg.risk.risk.edge_cost_multiple
    for p in summary.picks:
        assert p.risk_reward >= rr_floor - 1e-9
        # edge-after-cost gate held
        assert p.expected_move_pct >= k * p.cost_to_break_even_pct - 1e-9
        assert 0.0 <= p.confidence <= 100.0
        assert len(p.targets) == len(p.target_pcts) >= 1


def test_no_entries_after_cutoff():
    cfg, _, summary = _run()
    cutoff = time(15, 0)  # no_new_entry_after
    for p in summary.picks:
        assert p.ts.time() < cutoff


def test_all_positions_closed_by_end_of_day():
    _, runner, summary = _run()
    # No position should still be open/pending after the feed ends.
    assert len(runner.paper.open_positions) == 0
    for pos in summary.closed:
        assert pos.status in (PositionStatus.CLOSED, PositionStatus.CANCELLED)


def test_one_position_per_symbol_at_a_time():
    """Engine must not surface a new pick for a symbol with an open position."""
    _, runner, summary = _run()
    # Reconstruct: at any time, at most one OPEN position per symbol existed.
    # Proxy check: closed positions per symbol never overlap in [entry_ts, exit_ts].
    by_symbol = {}
    for p in summary.closed:
        if p.entry_ts is None:
            continue
        by_symbol.setdefault(p.symbol, []).append((p.entry_ts, p.exit_ts))
    for sym, spans in by_symbol.items():
        spans.sort()
        for (_s1, e1), (s2, _e2) in zip(spans, spans[1:]):
            assert s2 >= e1, f"overlapping positions for {sym}"
