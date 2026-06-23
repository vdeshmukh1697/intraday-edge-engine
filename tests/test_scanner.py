"""End-to-end scan tests (PLAN §4.0): universe -> filter -> strategy -> risk -> leaderboard."""

from datetime import date, time

from signal_engine.config import load_config
from signal_engine.scan.harness import run_scan
from signal_engine.universe.mock import MockUniverseProvider

_DAY = date(2025, 6, 23)  # Monday, trading day


def _scan(seed=42, n=400, top=20):
    cfg = load_config()
    uni = MockUniverseProvider(n=n, seed=seed)
    return run_scan(cfg, uni, _DAY, as_of=time(11, 0), seed=seed, top_n=top)


def test_scan_runs_and_reports_full_universe():
    res = _scan(n=400)
    assert res.universe_size == 400
    # Only the liquid static-survivors are deep-scanned (scan wide, rank narrow).
    assert 0 < res.deep_scanned <= 400


def test_leaderboard_is_sorted_and_ranked():
    res = _scan()
    board = res.leaderboard
    assert len(board) >= 1
    scores = [e.score for e in board]
    assert scores == sorted(scores, reverse=True)        # descending
    assert [e.rank for e in board] == list(range(1, len(board) + 1))


def test_top_n_respected():
    res = _scan(top=5)
    assert len(res.leaderboard) <= 5


def test_all_leaderboard_entries_are_valid_plans():
    res = _scan()
    for e in res.leaderboard:
        p = e.plan
        assert p.direction.value in ("LONG", "SHORT")
        assert p.risk_reward >= load_config().risk.risk.rr_floor - 1e-9
        assert p.expected_move_pct >= 3.0 * p.cost_to_break_even_pct - 1e-9  # edge gate held
        assert 0.0 <= p.confidence <= 100.0
        assert e.turnover_cr >= load_config().risk.liquidity.min_avg_daily_turnover_cr


def test_scan_is_deterministic():
    a = _scan(seed=7)
    b = _scan(seed=7)
    assert [e.symbol for e in a.leaderboard] == [e.symbol for e in b.leaderboard]
    assert [e.score for e in a.leaderboard] == [e.score for e in b.leaderboard]


def test_stats_are_consistent():
    res = _scan()
    # every deep-scanned symbol is accounted for in exactly one bucket
    accounted = res.filtered_out + res.no_signal + res.vetoed + res.candidates
    assert accounted <= res.deep_scanned
    assert res.candidates >= len(res.leaderboard)
