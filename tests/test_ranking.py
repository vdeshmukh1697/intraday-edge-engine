"""Tests for leaderboard ranking (PLAN §4.9). Includes a hand-computed score."""

from datetime import datetime

import pytz

from signal_engine.domain.enums import Direction
from signal_engine.domain.models import TradePlan
from signal_engine.scan.ranking import LeaderboardEntry, rank_plans, score_plan
from signal_engine.universe.models import InstrumentMeta

IST = pytz.timezone("Asia/Kolkata")
_TS = IST.localize(datetime(2025, 6, 23, 11, 0))


def _plan(symbol="X", confidence=100.0, rr=1.5, t1_pct=0.45, breakeven=0.0824):
    return TradePlan(
        symbol=symbol, ts=_TS, direction=Direction.LONG, strategy="s",
        entry=1000.0, stop_loss=997.0, stop_pct=0.30,
        targets=[1004.5], target_pcts=[t1_pct], expected_move_pct=t1_pct,
        risk_reward=rr, cost_to_break_even_pct=breakeven, confidence=confidence,
        reasons=["r"],
    )


def _meta(symbol="X", turnover=400.0, sector="IT"):
    return InstrumentMeta(symbol, sector, turnover, 1000.0, 0.02)


def test_score_hand_computed():
    # conf=1.0, rr_norm=0.5, liq_norm=1.0, edge_ratio=0.45/0.0824=5.46,
    # cost_factor=0.5+0.5*((5.46-3)/7)=0.6757 -> score=100*0.5*0.6757=33.79
    s = score_plan(_plan(), turnover_cr=400.0)
    assert abs(s - 33.79) < 0.1


def test_score_monotonic_in_confidence():
    lo = score_plan(_plan(confidence=70), 400.0)
    hi = score_plan(_plan(confidence=100), 400.0)
    assert hi > lo


def test_score_monotonic_in_liquidity():
    lo = score_plan(_plan(), turnover_cr=50.0)
    hi = score_plan(_plan(), turnover_cr=400.0)
    assert hi > lo


def test_score_monotonic_in_edge():
    # bigger expected move vs same cost -> higher cost factor -> higher score
    lo = score_plan(_plan(t1_pct=0.30), 400.0)
    hi = score_plan(_plan(t1_pct=1.50), 400.0)
    assert hi > lo


def test_rank_orders_and_truncates():
    items = [
        (_plan("A", confidence=100), _meta("A", 400)),
        (_plan("B", confidence=80), _meta("B", 400)),
        (_plan("C", confidence=60), _meta("C", 400)),
    ]
    board = rank_plans(items, top_n=2)
    assert len(board) == 2
    assert [e.symbol for e in board] == ["A", "B"]
    assert [e.rank for e in board] == [1, 2]
    assert board[0].score >= board[1].score


def test_rank_deterministic_tiebreak():
    # equal score -> tie-break by confidence then rr then symbol
    items = [
        (_plan("Z", confidence=85), _meta("Z", 400)),
        (_plan("A", confidence=85), _meta("A", 400)),
    ]
    board = rank_plans(items, top_n=2)
    # same everything except symbol -> 'Z' sorts before 'A' (reverse symbol order)
    assert board[0].symbol == "Z"
    assert isinstance(board[0], LeaderboardEntry)
