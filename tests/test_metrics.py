"""Hand-verified tests for the backtest metrics suite (PLAN §6.2)."""

import math
from datetime import datetime, timedelta

import pytz

from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
from signal_engine.domain.models import TradePlan, PaperPosition
from signal_engine.backtest.metrics import BacktestMetrics, compute_metrics

IST = pytz.timezone("Asia/Kolkata")

TOL = 1e-9


def make_closed(pnl, won=True, conf=80, day=1, stop_pct=1.0, hold=30):
    ts = IST.localize(datetime(2025, 6, day, 10, 0))
    plan = TradePlan(symbol="X", ts=ts, direction=Direction.LONG, strategy="s",
        entry=100, stop_loss=99, stop_pct=stop_pct, targets=[101.5], target_pcts=[1.5],
        expected_move_pct=1.5, risk_reward=1.5, cost_to_break_even_pct=0.08, confidence=conf, reasons=[])
    return PaperPosition(id=f"X-{day}-{pnl}", plan=plan, status=PositionStatus.CLOSED,
        entry_fill=100.0, entry_ts=ts, exit_fill=100*(1+pnl/100), exit_ts=ts+timedelta(minutes=hold),
        exit_reason=ExitReason.TARGET if won else ExitReason.STOP, pnl_pct_net=pnl,
        r_multiple=pnl/stop_pct, hold_minutes=hold, won=won)


def test_basic_four_trades_same_day():
    # pnl = [+2, +1, -1, -0.5] all on the same day.
    # gross_profit = 2 + 1 = 3.0 ; gross_loss = |-1 -0.5| = 1.5
    # profit_factor = 3.0 / 1.5 = 2.0
    # expectancy = (2 + 1 - 1 - 0.5) / 4 = 1.5 / 4 = 0.375
    # win_rate = 2/4 * 100 = 50.0
    # avg_win = (2 + 1)/2 = 1.5 ; avg_loss = (-1 - 0.5)/2 = -0.75
    # total_net = 1.5
    positions = [
        make_closed(2.0, won=True, day=1),
        make_closed(1.0, won=True, day=1),
        make_closed(-1.0, won=False, day=1),
        make_closed(-0.5, won=False, day=1),
    ]
    m = compute_metrics(positions)
    assert m.trades == 4
    assert m.wins == 2
    assert m.losses == 2
    assert abs(m.win_rate - 50.0) < TOL
    assert abs(m.avg_win_pct - 1.5) < TOL
    assert abs(m.avg_loss_pct - (-0.75)) < TOL
    assert abs(m.profit_factor - 2.0) < TOL
    assert abs(m.expectancy_pct - 0.375) < TOL
    assert abs(m.total_net_pct - 1.5) < TOL
    # all same day -> single daily return point of 1.5
    assert len(m.daily_returns) == 1
    assert abs(m.daily_returns[0][1] - 1.5) < TOL
    assert len(m.equity_curve) == 1
    assert abs(m.equity_curve[0] - 1.5) < TOL


def test_profit_factor_no_losses_is_inf():
    # all wins [+1, +2] -> gross_loss == 0, gross_profit > 0 -> inf
    positions = [
        make_closed(1.0, won=True, day=1),
        make_closed(2.0, won=True, day=2),
    ]
    m = compute_metrics(positions)
    assert math.isinf(m.profit_factor)
    assert m.profit_factor > 0
    assert m.losses == 0
    assert abs(m.avg_loss_pct - 0.0) < TOL


def test_multi_day_drawdown():
    # three trades on day 1/2/3 with pnl [+1, -2, +1]
    # daily_returns pct = [1, -2, 1]
    # equity_curve = cumsum = [1, -1, 0]
    # peak = 1 at idx0 ; max drawdown = 1 - (-1) = 2.0
    # sharpe: mean([1, -2, 1]) = 0 -> 0.0
    positions = [
        make_closed(1.0, won=True, day=1),
        make_closed(-2.0, won=False, day=2),
        make_closed(1.0, won=True, day=3),
    ]
    m = compute_metrics(positions)
    assert [round(p, 9) for _, p in m.daily_returns] == [1.0, -2.0, 1.0]
    assert [round(e, 9) for e in m.equity_curve] == [1.0, -1.0, 0.0]
    assert abs(m.max_drawdown_pct - 2.0) < TOL
    # days underwater: idx1 (-1 < peak 1) and idx2 (0 < peak 1) -> 2 consecutive
    assert m.max_drawdown_days == 2
    assert abs(m.sharpe - 0.0) < TOL


def test_empty_list():
    m = compute_metrics([])
    assert isinstance(m, BacktestMetrics)
    assert m.trades == 0
    assert abs(m.win_rate - 0.0) < TOL
    assert abs(m.profit_factor - 0.0) < TOL
    assert m.equity_curve == []
    assert m.daily_returns == []
    assert abs(m.max_drawdown_pct - 0.0) < TOL
    assert m.max_drawdown_days == 0
    assert abs(m.sharpe - 0.0) < TOL
    assert abs(m.sortino - 0.0) < TOL


def test_avg_hold_minutes():
    # holds 20 and 40 -> mean 30.0
    positions = [
        make_closed(1.0, won=True, day=1, hold=20),
        make_closed(-1.0, won=False, day=2, hold=40),
    ]
    m = compute_metrics(positions)
    assert abs(m.avg_hold_minutes - 30.0) < TOL


def test_ignores_untraded_positions():
    # a position with entry_fill None / pnl None must be excluded
    traded = make_closed(1.0, won=True, day=1)
    untraded = make_closed(5.0, won=True, day=1)
    untraded.entry_fill = None
    untraded.pnl_pct_net = None
    m = compute_metrics([traded, untraded])
    assert m.trades == 1
    assert abs(m.total_net_pct - 1.0) < TOL
