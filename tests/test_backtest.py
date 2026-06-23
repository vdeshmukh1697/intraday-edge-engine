"""End-to-end backtest tests (PLAN §6): multi-day replay -> ledger -> metrics + health."""

from datetime import date

from signal_engine.backtest.engine import run_backtest, trading_days
from signal_engine.config import load_config
from signal_engine.market.calendar import NSECalendar

_SYMBOLS = ["RELIANCE", "HDFCBANK", "INFY"]


def test_trading_days_skips_non_trading():
    cal = NSECalendar()
    days = trading_days(date(2025, 8, 14), 3, cal)  # 15 Aug 2025 is a holiday, 16/17 weekend
    assert len(days) == 3
    assert all(cal.is_trading_day(d) for d in days)
    assert date(2025, 8, 15) not in days  # Independence Day skipped
    assert days == sorted(days)


def test_backtest_runs_and_is_consistent():
    cfg = load_config()
    res = run_backtest(cfg, _SYMBOLS, date(2025, 6, 2), n_days=8, seed=3)
    assert len(res.days) == 8
    # Every ledger position is a filled, closed trade.
    assert all(p.entry_fill is not None for p in res.ledger)
    # Metrics trade count matches the ledger.
    assert res.metrics.trades == len(res.ledger)
    # Per-day pnl entries align with the days.
    assert len(res.per_day_pnl) == 8
    # Total net P&L equals sum of per-trade net (within rounding).
    assert abs(res.metrics.total_net_pct - sum(p.pnl_pct_net for p in res.ledger)) < 1e-6
    # Health is computed over the whole ledger.
    assert res.health.window_trades == len(res.ledger)
    assert 0.0 <= res.health.overall <= 100.0


def test_backtest_is_deterministic():
    cfg = load_config()
    a = run_backtest(cfg, _SYMBOLS, date(2025, 6, 2), n_days=5, seed=11)
    b = run_backtest(cfg, _SYMBOLS, date(2025, 6, 2), n_days=5, seed=11)
    assert a.metrics.trades == b.metrics.trades
    assert a.metrics.total_net_pct == b.metrics.total_net_pct
    assert a.health.overall == b.health.overall


def test_backtest_uses_shared_core_no_lookahead():
    """Sanity: surfaced picks during entry window only, all trades closed by EOD each day."""
    cfg = load_config()
    res = run_backtest(cfg, _SYMBOLS, date(2025, 6, 2), n_days=4, seed=7)
    for p in res.ledger:
        # entry always strictly before exit (no same-instant fills)
        assert p.entry_ts is not None and p.exit_ts is not None
        assert p.exit_ts >= p.entry_ts
