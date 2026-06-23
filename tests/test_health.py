"""Deterministic, hand-verified tests for the Strategy Health Scorer (PLAN §6.6).

Every metric below is computed by hand in the comments. Composite weights:
    overall = 100 * (0.30*hit + 0.25*pf + 0.20*exp + 0.15*cal + 0.10*dd)
with each component clamped to [0, 1]:
    hit = hit_rate/60 ; pf = (min(PF,3)-1)/2 ; exp = expectancy/0.5
    cal = 1 - Brier/0.25 ; dd = 1 - maxDD/10
Absolute tolerance 1e-9 for exact checks, looser for composites.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
from signal_engine.domain.models import PaperPosition, TradePlan
from signal_engine.health.scorer import (
    HealthScore,
    compute_health,
    detect_degradation,
)

IST = pytz.timezone("Asia/Kolkata")


def make_closed(pnl, won=True, conf=80, day=1, stop_pct=1.0):
    ts = IST.localize(datetime(2025, 6, day, 10, 0))
    plan = TradePlan(
        symbol="X", ts=ts, direction=Direction.LONG, strategy="s",
        entry=100, stop_loss=99, stop_pct=stop_pct, targets=[101.5],
        target_pcts=[1.5], expected_move_pct=1.5, risk_reward=1.5,
        cost_to_break_even_pct=0.08, confidence=conf, reasons=[],
    )
    return PaperPosition(
        id=f"X-{day}-{pnl}-{conf}", plan=plan, status=PositionStatus.CLOSED,
        entry_fill=100.0, entry_ts=ts, exit_fill=100 * (1 + pnl / 100),
        exit_ts=ts + timedelta(minutes=30),
        exit_reason=ExitReason.TARGET if won else ExitReason.STOP,
        pnl_pct_net=pnl, r_multiple=pnl / stop_pct, hold_minutes=30, won=won,
    )


EXACT = 1e-9


def test_insufficient_trades():
    # 4 < min_trades(5) -> insufficient, no metrics.
    book = [make_closed(1.5, day=d) for d in range(1, 5)]
    hs = compute_health(book, min_trades=5)
    assert hs.status == "insufficient"
    assert hs.overall == 0.0
    assert hs.window_trades == 4
    assert hs.components == {}
    assert hs.drift is None


def test_untraded_positions_are_excluded():
    # Positions without entry_fill / pnl don't count toward the window.
    ts = IST.localize(datetime(2025, 6, 1, 10, 0))
    plan = TradePlan(
        symbol="X", ts=ts, direction=Direction.LONG, strategy="s",
        entry=100, stop_loss=99, stop_pct=1.0, targets=[101.5],
        target_pcts=[1.5], expected_move_pct=1.5, risk_reward=1.5,
        cost_to_break_even_pct=0.08, confidence=80, reasons=[],
    )
    untraded = PaperPosition(id="U", plan=plan, status=PositionStatus.CANCELLED)
    book = [untraded] + [make_closed(1.5, day=d) for d in range(1, 6)]
    hs = compute_health(book, min_trades=5)
    assert hs.window_trades == 5  # the cancelled one is dropped


def test_strong_book_green():
    # 8 wins of +1.5 (conf 80), then 2 losses of -1.0 (conf 80).
    # hit_rate = 8/10 = 80.0
    # gross_profit = 12.0, gross_loss = -2.0 -> PF = 6.0 -> norm capped 3 -> c_pf 1.0
    # expectancy = (12-2)/10 = 1.0 -> c_exp clamps to 1.0
    # Brier = (8*(0.8-1)^2 + 2*(0.8-0)^2)/10 = (8*0.04 + 2*0.64)/10
    #       = (0.32 + 1.28)/10 = 0.16 -> c_cal = 1 - 0.16/0.25 = 0.36
    # equity: rises to 12 (8 wins) then back to 10 -> maxDD = 2.0 -> c_dd = 0.8
    # c_hit = 80/60 -> clamps to 1.0
    # overall = 100*(0.30 + 0.25 + 0.20 + 0.15*0.36 + 0.10*0.8)
    #         = 100*(0.75 + 0.054 + 0.08) = 88.4
    book = [make_closed(1.5, won=True, conf=80, day=d) for d in range(1, 9)]
    book += [make_closed(-1.0, won=False, conf=80, day=d) for d in range(9, 11)]
    hs = compute_health(book)
    assert hs.hit_rate == 80.0
    assert abs(hs.expectancy_pct - 1.0) < EXACT
    assert abs(hs.profit_factor - 6.0) < EXACT
    assert abs(hs.calibration_error - 0.16) < EXACT
    assert abs(hs.max_drawdown_pct - 2.0) < EXACT
    assert abs(hs.overall - 88.4) < 1e-6
    assert hs.status in {"green", "amber"}
    assert hs.overall > 70.0
    assert hs.drift is None


def test_all_losing_book_red():
    # 6 losses of -1.0 (conf 70). No profit -> PF = 0.0 -> c_pf 0.0
    # hit_rate = 0 ; expectancy = -1.0 -> c_exp clamps to 0
    # Brier = (0.7-0)^2 = 0.49 -> c_cal clamps to 0
    # equity falls monotonically -6 -> maxDD = 6.0 -> c_dd = 1 - 0.6 = 0.4
    # overall = 100*(0 + 0 + 0 + 0 + 0.10*0.4) = 4.0 -> red
    book = [make_closed(-1.0, won=False, conf=70, day=d) for d in range(1, 7)]
    hs = compute_health(book)
    assert hs.hit_rate == 0.0
    assert hs.profit_factor == 0.0
    assert abs(hs.max_drawdown_pct - 6.0) < EXACT
    assert abs(hs.overall - 4.0) < 1e-6
    assert hs.status == "red"


def test_profit_factor_infinite_when_no_losses():
    # 5 wins, no losses, some profit -> PF = inf, norm treated as 3.0.
    book = [make_closed(1.5, won=True, conf=80, day=d) for d in range(1, 6)]
    hs = compute_health(book)
    assert hs.profit_factor == float("inf")
    assert abs(hs.components["pf"] - 1.0) < EXACT  # capped at 3 -> full credit


def test_brier_handcheck_well_calibrated():
    # 5 identical trades conf=80, all won -> outcome 1.
    # Brier = (0.8 - 1)^2 = 0.04 for each -> mean 0.04.
    book = [make_closed(1.5, won=True, conf=80, day=d) for d in range(1, 6)]
    hs = compute_health(book)
    assert abs(hs.calibration_error - 0.04) < EXACT
    # cal component = 1 - 0.04/0.25 = 0.84
    assert abs(hs.components["cal"] - 0.84) < EXACT


def test_brier_handcheck_miscalibrated():
    # 5 trades conf=90 that all LOSE -> outcome 0.
    # Brier = (0.9 - 0)^2 = 0.81 -> cal component clamps to 0.
    book = [make_closed(-1.0, won=False, conf=90, day=d) for d in range(1, 6)]
    hs = compute_health(book)
    assert abs(hs.calibration_error - 0.81) < EXACT
    assert hs.components["cal"] == 0.0
    # Compare against a same-P&L book that lost with a humble 10% confidence:
    # Brier = (0.1-0)^2 = 0.01 -> cal = 1 - 0.04 = 0.96, so overall is higher.
    humble = [make_closed(-1.0, won=False, conf=10, day=d) for d in range(1, 6)]
    hs_humble = compute_health(humble)
    assert hs_humble.overall > hs.overall


def test_drift_against_baseline():
    # Book expectancy = 0.2 ; baseline expectancy = 1.0 -> drift = -0.8.
    # 5 trades each +0.2 net.
    book = [make_closed(0.2, won=True, conf=80, day=d) for d in range(1, 6)]
    hs = compute_health(book, baseline={"expectancy_pct": 1.0})
    assert abs(hs.expectancy_pct - 0.2) < EXACT
    assert hs.drift is not None
    assert abs(hs.drift - (-0.8)) < EXACT


def test_detect_degradation_red_book_alerts():
    book = [make_closed(-1.0, won=False, conf=70, day=d) for d in range(1, 7)]
    hs = compute_health(book)
    msg = detect_degradation(hs, threshold=50.0)
    assert msg is not None
    assert "Health degraded" in msg
    assert "{:.1f}".format(hs.overall) in msg


def test_detect_degradation_green_book_quiet():
    book = [make_closed(1.5, won=True, conf=80, day=d) for d in range(1, 9)]
    book += [make_closed(-1.0, won=False, conf=80, day=d) for d in range(9, 11)]
    hs = compute_health(book)  # overall 88.4 -> green
    assert detect_degradation(hs, threshold=50.0) is None


def test_detect_degradation_baseline_drop_fires():
    # overall 88.4, above the 50 floor, but baseline was 110 (synthetic) and
    # drop=15 -> 88.4 < 110 - 15 = 95 -> fires.
    book = [make_closed(1.5, won=True, conf=80, day=d) for d in range(1, 9)]
    book += [make_closed(-1.0, won=False, conf=80, day=d) for d in range(9, 11)]
    hs = compute_health(book)
    assert detect_degradation(hs, threshold=50.0) is None  # absolute floor OK
    msg = detect_degradation(
        hs, threshold=50.0, baseline_overall=110.0, drop=15.0
    )
    assert msg is not None
    assert "Health degraded" in msg


def test_detect_degradation_insufficient_is_quiet():
    book = [make_closed(-1.0, won=False, day=d) for d in range(1, 4)]
    hs = compute_health(book, min_trades=5)
    assert hs.status == "insufficient"
    assert detect_degradation(hs, threshold=50.0) is None


def test_window_takes_last_n():
    # 40 trades, window 30 -> only the last 30 score.
    book = [make_closed(1.5, won=True, day=(d % 28) + 1) for d in range(40)]
    hs = compute_health(book, window=30)
    assert hs.window_trades == 30
