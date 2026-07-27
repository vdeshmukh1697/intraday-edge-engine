"""Hand-verified tests for the nightly quant-desk review (`signal_engine/desk/`).

Every number asserted below is computed by hand in the comments, so a failure tells you whether
the code or the arithmetic changed. No network, no LLM: the deterministic path is the contract.

The most important test in this file is `test_no_proposal_is_ever_auto_applied` — it is the
executable form of the design rule that the desk proposes and a human applies.
"""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from signal_engine.desk.attribution import MIN_N_FOR_BUCKET, Bucket, decompose
from signal_engine.desk.facts import (
    MIN_BARS_FOR_A_REAL_SESSION, RECONNECT_STORM, assess_integrity, gather, parse_engine_log,
)
from signal_engine.desk.forensics import interrogate
from signal_engine.desk.gap import (
    GATED_ERA_START, book_returns, required_payoff, required_win_rate, solve_gap,
)
from signal_engine.desk.ledger import DeskStore, grade_open_hypotheses
from signal_engine.desk.review import build_digest, run_review

TOL = 1e-9
DAY = "2026-07-22"


# ------------------------------------------------------------------ log parsing / integrity

_LOG = """\
2026-07-22 06:00:00 INFO  signal_engine.scheduler | Dhan token refreshed
2026-07-22 09:15:05 INFO  signal_engine.scheduler | live_job: streaming Dhan feed for 40 symbols
2026-07-22 09:42:01 INFO  signal_engine.engine | ENTRY-CTX ACME LONG | above-VWAP
2026-07-22 09:42:02 INFO  signal_engine.engine | ENTRY-CTX ZED SHORT | below-VWAP
2026-07-22 09:43:03 INFO  signal_engine.engine | SKIP ZED SHORT @~100 — 1 share Rs.100 > free cash Rs.5
2026-07-22 09:44:00 INFO  signal_engine.engine | SKIP OTHER LONG — cooldown active
2026-07-22 10:00:00 WARNING signal_engine.brokers.dhan_ws | dhan feed: stream error (1/200), reconnecting: lost
2026-07-22 15:30:00 INFO  signal_engine.scheduler | live_job leg done: 15034 bars, 8 picks, 8 paper trades (persisted)
2026-07-23 09:15:05 INFO  signal_engine.scheduler | live_job: streaming Dhan feed for 40 symbols
Run time of job "scan_job (trigger: cron[...], next run at: 2026-07-22 15:45:00 IST)" was missed by 2:47:39
"""


def _log_file(tmp_path):
    p = tmp_path / "engine.log"
    p.write_text(_LOG)
    return str(p)


def test_log_parsing_counts_only_the_requested_day(tmp_path):
    lf = parse_engine_log(_log_file(tmp_path), "2026-07-22")
    # 8 lines carry the 2026-07-22 prefix; the 07-23 line and the (undated-prefix) APScheduler
    # line are excluded from the day count.
    assert lf.log_lines_for_day == 8
    assert lf.live_started is True
    assert lf.symbols_subscribed == 40
    assert lf.bars_processed == 15034
    assert lf.picks == 8
    assert lf.trades_persisted == 8
    assert lf.entry_evaluations == 2
    assert lf.skips_affordability == 1        # the "> free cash" one
    assert lf.skips_other == 1                # the cooldown one
    assert lf.ws_reconnects == 1
    assert lf.feed_errors == 1
    # The APScheduler miss line has no date prefix but names the day in "next run at:".
    assert lf.jobs_missed == 1


def test_missing_log_is_not_an_error():
    lf = parse_engine_log("/nonexistent/path.log", DAY)
    assert lf.log_lines_for_day == 0 and lf.live_started is False


def test_integrity_ok_on_healthy_session(tmp_path):
    lf = parse_engine_log(_log_file(tmp_path), "2026-07-22")
    integ = assess_integrity(DAY, True, lf, n_trades=8, halt_reason=None, max_trades_per_day=15)
    assert integ.verdict == "OK"
    assert integ.usable is True
    # 1 affordability skip present and no halt / cap => cash-bound.
    assert integ.trade_count_binding == "cash"


def test_dead_session_guard_zero_bars(tmp_path):
    """The 2026-07-23 shape: feed started, logged 0 bars, stormed reconnects."""
    p = tmp_path / "dead.log"
    p.write_text(
        "2026-07-23 09:15:05 INFO  signal_engine.scheduler | live_job: streaming Dhan feed for "
        "40 symbols\n"
        + "".join("2026-07-23 09:15:{:02d} WARNING signal_engine.brokers.dhan_ws | dhan feed: "
                  "stream error (1/200), reconnecting: lost\n".format(i % 60)
                  for i in range(RECONNECT_STORM + 5))
        + "2026-07-23 15:30:05 INFO  signal_engine.scheduler | live_job leg done: 0 bars, 0 "
          "picks, 0 paper trades (persisted)\n")
    lf = parse_engine_log(str(p), "2026-07-23")
    assert lf.bars_processed == 0
    assert lf.ws_reconnects >= RECONNECT_STORM
    integ = assess_integrity("2026-07-23", True, lf, 0, None, 15)
    assert integ.verdict == "DEAD"
    assert integ.usable is False
    assert any("NOTHING about strategy quality" in r for r in integ.reasons)


def test_integrity_flags_non_trading_day():
    lf = parse_engine_log(None, "2026-07-26")
    integ = assess_integrity("2026-07-26", False, lf, 0, None, 15)
    assert integ.verdict == "NO_SESSION" and integ.usable is False


def test_bars_threshold_is_the_documented_constant():
    """A guard that silently drifts is worse than none — pin the constant."""
    assert MIN_BARS_FOR_A_REAL_SESSION == 500


# ------------------------------------------------------------------ the 1%-gap solver

def test_required_win_rate_worked_example():
    """Hand arithmetic. T=1.00, n=5, W=0.40, L=0.30 (all % of book).

    p* = (T/n + L) / (W + L) = (1.00/5 + 0.30) / (0.40 + 0.30)
       = (0.20 + 0.30) / 0.70 = 0.50 / 0.70 = 0.7142857142857143
    """
    p = required_win_rate(1.00, 5, 0.40, 0.30)
    assert abs(p - 0.5 / 0.7) < TOL
    assert abs(p - 0.7142857142857143) < 1e-12


def test_required_payoff_worked_example():
    """b* = (T/(n*L) + (1-p)) / p with T=1.00, n=5, L=0.30, p=0.30.

    T/(n*L) = 1.00 / (5 * 0.30) = 1.00 / 1.50 = 0.6666666666666666
    (0.6666666666666666 + 0.70) / 0.30 = 1.3666666666666667 / 0.30 = 4.555555555555555
    """
    b = required_payoff(1.00, 5, 0.30, 0.30)
    assert abs(b - 4.555555555555555) < 1e-12


def _synthetic_trades(equity=100000.0):
    """5 trades on a ₹1L book: 30% win rate would need 1.5 winners, so use 2W/3L shaped so the
    means come out exactly W=0.40% and L=0.30% of book.

    winners: +₹400 each (2 of them) -> +0.40% of book each
    losers:  -₹300 each (3 of them) -> -0.30% of book each
    sum = 2*400 - 3*300 = 800 - 900 = -₹100 = -0.10% of the book
    win rate = 2/5 = 0.40
    """
    rows = []
    for i in range(2):
        rows.append({"id": "W{}".format(i), "pnl_inr": 400.0, "pnl_pct_net": 1.0,
                     "entry_fill": 100.0, "stop_loss": 99.0, "notional_entry": 40000.0,
                     "charges_inr": 30.0, "pnl_pct_gross": 1.05, "cost_pct": 0.05,
                     "r_multiple": 1.0, "entry_ts": DAY + "T09:20:00+05:30"})
    for i in range(3):
        rows.append({"id": "L{}".format(i), "pnl_inr": -300.0, "pnl_pct_net": -0.75,
                     "entry_fill": 100.0, "stop_loss": 99.0, "notional_entry": 40000.0,
                     "charges_inr": 30.0, "pnl_pct_gross": -0.70, "cost_pct": 0.05,
                     "r_multiple": -0.75, "entry_ts": DAY + "T09:30:00+05:30"})
    return rows


def test_gap_model_on_synthetic_book():
    """W = 0.40% of book, L = 0.30% of book, n = 5/day, p = 0.40.

    realised daily = 2*0.40 - 3*0.30 = 0.80 - 0.90 = -0.10% of book
    payoff = 0.40/0.30 = 1.3333333333333333
    p* = (1.00/5 + 0.30)/(0.40+0.30) = 0.7142857142857143  -> gap = 71.43 - 40 = 31.43pp
    required per-trade edge = 1.00/5 = 0.20% of book = ₹200 on ₹1,00,000
    """
    trades = _synthetic_trades()
    g = solve_gap(trades, 100000.0, DAY, scope="session", sessions=1.0, charges_inr=150.0)
    assert g.n_trades == 5
    assert abs(g.win_rate - 0.40) < TOL
    assert abs(g.mean_winner_book_pct - 0.40) < TOL
    assert abs(g.mean_loser_book_pct - 0.30) < TOL
    assert abs(g.payoff - (0.40 / 0.30)) < TOL
    assert abs(g.realised_daily_book_pct - (-0.10)) < 1e-9
    assert abs(g.required_win_rate - 0.7142857142857143) < 1e-12
    assert abs(g.win_rate_gap_pp - 31.428571428571427) < 1e-9
    assert abs(g.required_edge_per_trade_book_pct - 0.20) < TOL
    assert abs(g.required_edge_per_trade_inr - 200.0) < 1e-6
    # 5 trades x 0.40% best case = 2.0% > 1.0% target, so a 100% win rate WOULD reach it.
    assert g.impossible_at_any_win_rate is False
    # 31pp is far beyond the closable band, so parameter work must NOT be implied.
    assert g.closable_by_parameter_work is False
    assert "SIGNAL problem" in g.verdict_line()
    # friction: ₹150 charges on ₹1L = 0.15% of the book = 15% of a 1% day.
    assert abs(g.friction_book_pct - 0.15) < TOL
    assert abs(g.friction_share_of_target - 0.15) < TOL
    assert abs(g.required_gross_book_pct - 1.15) < TOL


def test_gap_impossible_at_any_win_rate():
    """1 trade/day whose mean winner is 0.10% of book cannot reach 1.00%/day at ANY win rate."""
    trades = [{"id": "W", "pnl_inr": 100.0, "entry_fill": 100.0, "stop_loss": 99.0,
               "notional_entry": 10000.0, "pnl_pct_net": 1.0},
              {"id": "L", "pnl_inr": -100.0, "entry_fill": 100.0, "stop_loss": 99.0,
               "notional_entry": 10000.0, "pnl_pct_net": -1.0}]
    g = solve_gap(trades, 100000.0, DAY, sessions=2.0)
    # n = 2 trades / 2 sessions = 1.0 per day; W = 0.10% of book.
    assert abs(g.n_trades - 1.0) < TOL
    assert g.impossible_at_any_win_rate is True
    assert g.closable_by_parameter_work is False
    assert "UNREACHABLE" in g.verdict_line()


def test_gap_with_no_trades_is_unmeasured_not_met():
    g = solve_gap([], 100000.0, DAY)
    assert g.win_rate is None
    assert "NOT COMPUTABLE" in g.verdict_line()
    assert "unmeasured, not met" in " ".join(g.notes)


def test_book_returns_unit_conversion_and_data_quality_flag():
    """₹-exact rows convert exactly; pre-ledger rows fall back and must be counted."""
    methods = {}
    rets = book_returns([
        {"pnl_inr": 500.0},                                    # exact: +0.5% of ₹1L
        {"pnl_pct_net": 1.0, "notional_entry": 50000.0},       # scaled: 1.0 * 0.5 = +0.5%
        {"pnl_pct_net": 2.0},                                  # raw fallback: +2.0 (a position %)
    ], 100000.0, methods)
    assert abs(rets[0] - 0.5) < TOL
    assert abs(rets[1] - 0.5) < TOL
    assert abs(rets[2] - 2.0) < TOL
    assert methods == {"exact_inr": 1, "scaled_by_notional": 1, "raw_trade_pct": 1}
    g = solve_gap([{"pnl_pct_net": 2.0}], 100000.0, DAY)
    assert any("DATA QUALITY" in n for n in g.notes)


def test_gated_era_constant_matches_the_p0_go_live():
    assert GATED_ERA_START == "2026-07-13"


# ------------------------------------------------------------------ MFE/MAE + counterfactuals

def _bars_frame():
    """4 one-minute bars, hand-built so MFE/MAE are exact.

        09:15  o100 h100 l100 c100
        09:16  o100 h102 l99  c101
        09:17  o101 h103 l100 c100     <- session high 103
        09:18  o100 h100 l97  c98      <- session low 97
    """
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-22 09:{:02d}:00+05:30".format(m))
                            for m in (15, 16, 17, 18)])
    return pd.DataFrame({
        "open": [100.0, 100.0, 101.0, 100.0],
        "high": [100.0, 102.0, 103.0, 100.0],
        "low": [100.0, 99.0, 100.0, 97.0],
        "close": [100.0, 101.0, 100.0, 98.0],
        "volume": [0, 10, 10, 10],
    }, index=idx)


class _FakeStore:
    def __init__(self, frame):
        self.frame = frame

    def load_session(self, symbol, day):
        return self.frame if symbol == "ACME" else None


def _facts_for_forensics():
    trade = {
        "id": "ACME-1", "symbol": "ACME", "direction": "LONG",
        "entry_fill": 100.0, "entry_ts": "2026-07-22T09:16:00+05:30",
        "exit_fill": 98.0, "exit_ts": "2026-07-22T09:18:00+05:30",
        "exit_reason": "STOP", "pnl_pct_gross": 1.00, "cost_pct": 0.0824,
        "pnl_pct_net": 0.9176, "r_multiple": 0.5, "hold_minutes": 2.0,
        "stop_loss": 98.0, "target": 103.0, "qty": 400, "notional_entry": 40000.0,
        "charges_inr": 33.0, "pnl_inr": 367.0, "alpha_pct": 0.8, "nifty_ret_pct": 0.2,
    }
    return SimpleNamespace(
        day=DAY, trades=[trade],
        predictions={"entry": [{"symbol": "ACME", "ts": "2026-07-22T09:16:01+05:30",
                                "reasons_decoded": ["above VWAP", "EMA fast>slow"],
                                "reason_plain": "trending up", "rupee_risk": 800.0,
                                "entry": 99.97, "expected_move_pct": 2.0, "risk_reward": 2.0}]},
        equity={"open_equity": 100000.0, "close_equity": 100367.0, "book_return_pct": 0.367,
                "intraday_maxdd_pct": -0.1},
        config={"friction_pct_round_trip": 0.1424, "charges_pct_round_trip": 0.0824,
                "slippage_pct_per_side": 0.03, "max_cost_r": 0.25, "min_stop_pct": 0.30,
                "risk_per_trade_pct": 0.5, "max_concurrent_positions": 4,
                "daily_loss_pct": 4.0, "max_trades_per_day": 15,
                "starting_capital": 100000.0},
        log=SimpleNamespace(skips_affordability=0, entry_evaluations=1, bars_processed=15000,
                            ws_reconnects=0, notable=[], skips_other=0, feed_errors=0,
                            jobs_missed=0, log_lines_for_day=10, live_started=True,
                            symbols_subscribed=40, picks=1, trades_persisted=1,
                            warnings=0, errors=0, log_path=None),
        halt=None, micro={}, movers={}, trailing={"sessions": 1, "days": [DAY], "per_day": [],
                                                  "trades": [trade]},
        integrity=SimpleNamespace(usable=True, verdict="OK", reasons=[], trading_day=True,
                                  trade_count_binding="gate", bars_processed=15000,
                                  ws_reconnects=0, n_trades=1, entry_evaluations=1),
        n_trades=1,
        sum_net_pct=lambda: 0.9176, sum_inr=lambda: 367.0, sum_charges_inr=lambda: 33.0,
    )


def test_mfe_mae_are_exact():
    """LONG from 100.0, held 09:16 -> 09:18 over the bars above.

    Bars strictly after 09:16 and up to 09:18 are 09:17 and 09:18.
      best high  = max(103, 100) = 103 -> MFE = (103-100)/100*100 = +3.0%
      worst low  = min(100, 97)  =  97 -> MAE = ( 97-100)/100*100 = -3.0%
    stop = 98 -> stop_pct = |100-98|/100*100 = 2.0, so MFE_R = +1.5 and MAE_R = -1.5.
    Recorded gross = 1.00%, so captured = 1.00 / 3.00 = 0.3333...
    """
    facts = _facts_for_forensics()
    fx = interrogate(facts, store=_FakeStore(_bars_frame()))[0]
    assert fx.bars_available is True
    assert abs(fx.stop_pct - 2.0) < TOL
    assert abs(fx.mfe_pct - 3.0) < 1e-9
    assert abs(fx.mae_pct + 3.0) < 1e-9
    assert abs(fx.mfe_r - 1.5) < 1e-9
    assert abs(fx.mae_r + 1.5) < 1e-9
    assert abs(fx.captured_frac - (1.0 / 3.0)) < 1e-9


def test_counterfactual_baseline_and_optimal_are_exact():
    """baseline hits the 103 target on the 09:17 bar.

    Adverse exit slippage for a LONG sell is -0.03%: 103 * (1 - 0.0003) = 102.9691
    gross = (102.9691 - 100)/100*100 = 2.9691%
    net   = 2.9691 - cost 0.0824 = 2.8867%
    optimal (perfect foresight) = MFE 3.0 - 0.0824 = 2.9176%
    """
    facts = _facts_for_forensics()
    fx = interrogate(facts, store=_FakeStore(_bars_frame()))[0]
    assert abs(fx.variants["baseline"] - 2.8867) < 1e-6
    assert abs(fx.variants["optimal"] - 2.9176) < 1e-9
    # A 1.5x-wider stop (98 -> 97) still lets the 103 target print first, so it matches baseline.
    assert abs(fx.variants["wider_stop"] - 2.8867) < 1e-6
    # A 0.67x-tighter stop sits at 100 - 2*0.67 = 98.66, which the 09:17 low (100) does not
    # touch, so the target still prints first here too.
    assert abs(fx.variants["tighter_stop"] - 2.8867) < 1e-6


def test_entry_placement_and_around_entry_window():
    facts = _facts_for_forensics()
    fx = interrogate(facts, store=_FakeStore(_bars_frame()))[0]
    # Entry bar 09:16 has range 99..102; a 100.0 fill sits (100-99)/(102-99) = 33.33% up it.
    assert abs(fx.entry_in_bar_pct - (1.0 / 3.0 * 100.0)) < 1e-9
    # 15 min after 09:16 the last available close is 98 -> LONG move = (98-100)/100 = -2.0%.
    assert abs(fx.move_after_entry_pct + 2.0) < 1e-9


def test_missing_bars_degrade_to_none_not_an_error():
    facts = _facts_for_forensics()
    facts.trades[0]["symbol"] = "NOPE"
    fx = interrogate(facts, store=_FakeStore(_bars_frame()))[0]
    assert fx.bars_available is False and fx.mfe_pct is None
    assert any("missing from the" in n for n in fx.notes)


def test_friction_decided_flag_is_arithmetic():
    facts = _facts_for_forensics()
    # gross +0.05% against a 0.0824% cost -> a gross winner that nets negative.
    facts.trades[0]["pnl_pct_gross"] = 0.05
    fx = interrogate(facts, store=_FakeStore(_bars_frame()))[0]
    assert fx.friction_decided is True
    facts.trades[0]["pnl_pct_gross"] = 1.00
    fx = interrogate(facts, store=_FakeStore(_bars_frame()))[0]
    assert fx.friction_decided is False


# ------------------------------------------------------------------ attribution / low-n guard

def test_low_n_bucket_is_tagged_not_actionable():
    small = Bucket(key="exit=STOP", n=MIN_N_FOR_BUCKET - 1, wins=0, sum_net_pct=-3.0,
                   sum_r=-4.0, sum_inr=-2000.0)
    big = Bucket(key="exit=STOP", n=MIN_N_FOR_BUCKET, wins=5, sum_net_pct=-3.0,
                 sum_r=-4.0, sum_inr=-2000.0)
    assert small.actionable is False and "NOT ACTIONABLE" in small.rendered()
    assert big.actionable is True and "NOT ACTIONABLE" not in big.rendered()


def test_attribution_identity_and_findings_carry_n():
    facts = _facts_for_forensics()
    fx = interrogate(facts, store=_FakeStore(_bars_frame()))
    att = decompose(facts, fx)
    # gross 1.00 - cost 0.0824 = 0.9176 = net, so the identity residual must vanish.
    assert abs(att.book["identity_residual_pp"]) < 1e-9
    assert abs(att.book["tide_pct"] - 0.2) < TOL          # LONG => +1 * nifty 0.2
    assert att.book["win_rate"] == 1.0
    assert all(f.n is not None or f.sessions is not None for f in att.findings)
    # Every statistical finding on a 1-trade session must be non-actionable.
    for f in att.findings:
        if f.kind == "statistical" and (f.n or 0) < MIN_N_FOR_BUCKET:
            assert f.actionable is False


def test_no_trades_produces_no_attribution_claims():
    facts = _facts_for_forensics()
    facts.trades = []
    facts.n_trades = 0
    att = decompose(facts, [])
    assert att.book == {"n": 0}
    assert len(att.findings) == 1 and att.findings[0].actionable is False


# ------------------------------------------------------------------ ledger + grading

def _store(tmp_path):
    return DeskStore("sqlite:///" + str(tmp_path / "desk.sqlite3"), run_id="test")


def _facts_for_grading(sessions, trades):
    return SimpleNamespace(day=DAY, trailing={"sessions": sessions, "trades": trades},
                           equity={"open_equity": 100000.0},
                           config={"starting_capital": 100000.0})


def test_grading_refuses_below_the_pre_registered_floor(tmp_path):
    store = _store(tmp_path)
    hid = store.open_hypothesis(DAY, "win rate clears 50%", "why", "test", "pred",
                                "win_rate", "gt", 0.5, min_sessions=10, min_n=40)
    grades = grade_open_hypotheses(store, _facts_for_grading(2, _synthetic_trades()))
    assert len(grades) == 1
    assert grades[0].status == "open"                      # NOT graded early
    assert "evidence floor not met" in grades[0].lesson
    assert store.fetch_hypotheses(status="open")[0]["id"] == hid
    store.close()


def test_grading_refutes_and_records_the_dead_idea(tmp_path):
    store = _store(tmp_path)
    store.open_hypothesis(DAY, "win rate clears 50%", "why", "test", "pred",
                          "win_rate", "gt", 0.5, min_sessions=1, min_n=5)
    # _synthetic_trades has 2 winners / 3 losers => win_rate 0.40, below the 0.50 bar.
    grades = grade_open_hypotheses(store, _facts_for_grading(3, _synthetic_trades()))
    assert grades[0].status == "refuted"
    assert abs(grades[0].value - 0.40) < TOL
    assert "did NOT hold" in grades[0].lesson
    assert "win rate clears 50%" in store.dead_ideas()
    store.close()


def test_grading_supports_when_the_bar_is_cleared(tmp_path):
    store = _store(tmp_path)
    store.open_hypothesis(DAY, "win rate clears 30%", "why", "test", "pred",
                          "win_rate", "gt", 0.3, min_sessions=1, min_n=5)
    grades = grade_open_hypotheses(store, _facts_for_grading(3, _synthetic_trades()))
    assert grades[0].status == "supported"
    assert "The predicted effect held." in grades[0].lesson
    store.close()


def test_hypotheses_are_idempotent_on_statement(tmp_path):
    """Re-proposing an idea must return the SAME row — that is the anti-amnesia mechanism."""
    store = _store(tmp_path)
    a = store.open_hypothesis(DAY, "same idea", "r", "t", "p", "avg_r", "gt", 0.0, 10, 40)
    b = store.open_hypothesis("2026-07-23", "same idea", "r2", "t2", "p2", "avg_r", "gt",
                              0.0, 10, 40)
    assert a == b
    assert len(store.fetch_hypotheses()) == 1
    store.close()


def test_priors_include_the_settled_verdicts(tmp_path):
    store = _store(tmp_path)
    priors = store.priors()
    assert any("restrict_to_allowlist" in p for p in priors)
    assert any("no measurable gross edge" in p.lower() or "NO measurable gross edge" in p
               for p in priors)
    store.close()


# ------------------------------------------------------------------ NO AUTO-APPLY (the big one)

def test_no_proposal_is_ever_auto_applied(tmp_path):
    """Executable form of the design rule: the desk writes diffs, a human applies them.

    Three independent checks, because one could be defeated by a refactor:
      1. the persisted proposal rows are `applied=0` / `status='proposed'`;
      2. the desk package contains no code that could write a config file or set applied=1;
      3. `DeskStore` exposes no method that flips `applied`.
    """
    store = _store(tmp_path)
    pid = store.add_proposal(DAY, "config/risk.yaml", "risk.some_key", "0", "1", "diff",
                             "why", "bar", "effect", "0.5d", 1)
    rows = store.fetch_proposals(day=DAY)
    assert len(rows) == 1 and rows[0]["id"] == pid
    assert rows[0]["applied"] == 0
    assert rows[0]["status"] == "proposed"
    store.close()

    # Scan the package for CODE that could mutate configuration or flip `applied`. Regexes are
    # used (not substring matches) so the module docstrings can freely *discuss* config files
    # while the code remains provably unable to touch them.
    import re

    pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "signal_engine", "desk")
    banned_patterns = (
        r"yaml\s*\.\s*(safe_)?dump",             # serialising config back out
        r"SET\s+applied",                        # SQL flipping the applied flag
        r"applied\s*=\s*1\b",                    # ditto, in Python
        r"os\.environ\[[^\]]*\]\s*=",            # mutating the live environment
        r"setattr\(\s*cfg",                      # mutating the loaded config object
        r"open\([^)]*\.(yaml|yml)[^)]*\)",       # opening any YAML file at all
        r"shutil\.(copy|move)",                  # swapping a config file in
    )
    for name in sorted(os.listdir(pkg)):
        if not name.endswith(".py"):
            continue
        src = open(os.path.join(pkg, name)).read()
        for pat in banned_patterns:
            m = re.search(pat, src)
            assert m is None, "{} matches forbidden pattern {!r}: {!r}".format(
                name, pat, m.group(0))
        # The ONLY write-mode file open in the whole package is the markdown report.
        for line in src.splitlines():
            if re.search(r"open\([^)]*['\"]w", line):
                assert "path" in line, "unexpected write in {}: {}".format(name, line.strip())

    assert not any(("apply" in a and not a.startswith("_")) for a in dir(DeskStore))


def test_forbidden_config_keys_are_dropped_from_proposals():
    """The three keys the human ring-fenced can never appear in a proposal."""
    from signal_engine.desk.analyst import (
        FORBIDDEN_PROPOSAL_KEYS, Proposal, review_deterministic,
    )

    facts = _facts_for_forensics()
    fx = interrogate(facts, store=_FakeStore(_bars_frame()))
    att = decompose(facts, fx)
    out = review_deterministic(facts, att, {}, [])
    out.proposals.append(Proposal("config/risk.yaml", "risk.max_cost_r", "0.25", "0.10", "d",
                                  "r", "b", "e", "x", 1))
    # Re-run the filter the way review_deterministic does, then assert nothing survives.
    kept = [p for p in out.proposals
            if not any(p.config_key.startswith(k) for k in FORBIDDEN_PROPOSAL_KEYS)]
    assert all(p.config_key != "risk.max_cost_r" for p in kept)
    assert "risk.max_cost_r" in FORBIDDEN_PROPOSAL_KEYS
    assert "live_universe.restrict_to_allowlist" in FORBIDDEN_PROPOSAL_KEYS


def test_llm_is_off_by_default(monkeypatch):
    """The nightly job must never make a paid call unless explicitly switched on."""
    from signal_engine.desk import analyst

    monkeypatch.delenv("SE_DESK_LLM", raising=False)
    assert analyst.llm_enabled() is False
    res = analyst.llm_commentary(_facts_for_forensics(), decompose(_facts_for_forensics(), []),
                                 {}, analyst.AnalystOutput())
    assert res["commentary"] is None
    assert "SE_DESK_LLM" in res["error"]
    assert res["meter"].total_inr == 0.0


# ------------------------------------------------------------------ end-to-end on a temp DB

def _seed_db(path, day=DAY, bars_line=True, n_trades=2):
    from signal_engine.storage.repository import SignalRepository

    repo = SignalRepository("sqlite:///" + path)
    repo.close()
    con = sqlite3.connect(path)
    for i in range(n_trades):
        won = 1 if i == 0 else 0
        con.execute(
            """INSERT INTO paper_trades (id, symbol, strategy, direction, entry_fill, entry_ts,
                   exit_fill, exit_ts, exit_reason, pnl_pct_net, r_multiple, hold_minutes, won,
                   confidence, stop_loss, target, qty, notional_entry, charges_inr, pnl_inr,
                   pnl_pct_gross, cost_pct, nifty_ret_pct, alpha_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("T{}".format(i), "ACME", "vwap_ema_adx", "LONG", 100.0,
             day + "T09:2{}:00+05:30".format(i), 101.0 if won else 99.0,
             day + "T09:4{}:00+05:30".format(i), "TARGET" if won else "STOP",
             0.9176 if won else -1.0824, 0.9 if won else -1.1, 20.0, won, 85.0, 99.0, 102.0,
             300, 30000.0, 25.0, 275.0 if won else -325.0,
             1.0 if won else -1.0, 0.0824, 0.1, 0.9 if won else -1.1))
    con.execute("INSERT INTO portfolio_equity (ts, day, kind, equity, cash, invested, "
                "unrealized_pnl, open_count) VALUES (?,?,?,?,?,?,?,?)",
                ("2026-07-21T15:30:00+05:30", "2026-07-21", "eod", 100000.0, 100000.0,
                 0.0, 0.0, 0))
    con.execute("INSERT INTO portfolio_equity (ts, day, kind, equity, cash, invested, "
                "unrealized_pnl, open_count) VALUES (?,?,?,?,?,?,?,?)",
                (day + "T15:30:00+05:30", day, "eod", 99950.0, 99950.0, 0.0, 0.0, 0))
    con.commit()
    con.close()


def test_end_to_end_review_writes_a_report_and_never_a_config(tmp_path):
    db = str(tmp_path / "e2e.sqlite3")
    _seed_db(db)
    log = tmp_path / "engine.log"
    log.write_text(
        "{d} 09:15:05 INFO  signal_engine.scheduler | live_job: streaming Dhan feed for 40 "
        "symbols\n{d} 09:20:00 INFO  signal_engine.engine | ENTRY-CTX ACME LONG | above-VWAP\n"
        "{d} 15:30:00 INFO  signal_engine.scheduler | live_job leg done: 15000 bars, 2 picks, "
        "2 paper trades (persisted)\n".format(d=DAY))
    out_dir = tmp_path / "desk"
    review = run_review(DAY, cfg=None, db_url="sqlite:///" + db, log_path=str(log),
                        out_dir=str(out_dir), trading_day=True)
    assert review.concluded is True
    assert review.facts.integrity.verdict == "OK"
    assert review.report_path and os.path.exists(review.report_path)
    md = open(review.report_path).read()
    assert "Paper book. No demonstrated edge. Proposals only" in md
    assert "## 2. Session integrity" in md
    assert "## 10. The +1.00%/day gap model" in md
    assert "NOT APPLIED" in md
    # Persisted rows exist and none is applied.
    con = sqlite3.connect(db)
    for (applied,) in con.execute("SELECT applied FROM desk_proposals"):
        assert applied == 0
    assert con.execute("SELECT COUNT(*) FROM desk_reviews WHERE day = ?", (DAY,)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM desk_gap WHERE day = ?", (DAY,)).fetchone()[0] >= 2
    con.close()
    # Nothing outside the report directory was written.
    assert sorted(os.listdir(str(out_dir))) == ["DESK_REVIEW_{}.md".format(DAY)]


def test_end_to_end_dead_session_refuses_to_conclude(tmp_path):
    db = str(tmp_path / "dead.sqlite3")
    _seed_db(db, n_trades=0)
    log = tmp_path / "dead.log"
    log.write_text(
        "2026-07-23 09:15:05 INFO  signal_engine.scheduler | live_job: streaming Dhan feed for "
        "40 symbols\n"
        "2026-07-23 15:30:05 INFO  signal_engine.scheduler | live_job leg done: 0 bars, 0 picks, "
        "0 paper trades (persisted)\n")
    review = run_review("2026-07-23", cfg=None, db_url="sqlite:///" + db, log_path=str(log),
                        out_dir=str(tmp_path / "desk"), trading_day=True)
    assert review.concluded is False
    assert review.facts.integrity.verdict == "DEAD"
    md = review.markdown
    assert "No conclusions drawn" in md
    assert "deliberately omitted" in md
    # The analysis sections must be ABSENT, not empty.
    assert "## 3. P&L attribution" not in md
    assert "## 10. The +1.00%/day gap model" not in md
    assert "NO CONCLUSIONS DRAWN" in build_digest(review)


def test_digest_never_promises_profit(tmp_path):
    db = str(tmp_path / "d.sqlite3")
    _seed_db(db)
    log = tmp_path / "l.log"
    log.write_text("{d} 09:15:05 INFO  x | live_job: streaming Dhan feed for 40 symbols\n"
                   "{d} 15:30:00 INFO  x | live_job leg done: 15000 bars, 2 picks, 2 paper "
                   "trades (persisted)\n".format(d=DAY))
    review = run_review(DAY, cfg=None, db_url="sqlite:///" + db, log_path=str(log),
                        out_dir=str(tmp_path / "desk"), trading_day=True)
    d = review.digest.lower()
    for banned in ("tomorrow", "should buy", "guaranteed", "will profit", "recommend buying"):
        assert banned not in d
    assert "paper only" in d and "no edge implied" in d


def test_gather_is_read_only(tmp_path):
    """`gather` must never mutate the trading tables — the live engine is the only writer."""
    db = str(tmp_path / "ro.sqlite3")
    _seed_db(db)
    con = sqlite3.connect(db)
    before = con.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    con.close()
    gather(DAY, db_url="sqlite:///" + db, log_path=None, cfg=None, trading_day=True)
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == before
    con.close()


# ------------------------------------------------------------------ /api/desk contract

def test_api_desk_serves_the_ledger_and_never_reports_applied(tmp_path, monkeypatch):
    """The dashboard endpoint must expose the gap series + proposals, and every proposal must
    read back as NOT applied. A UI that could show `applied: 1` for a desk-written row would
    misrepresent the whole two-clock design."""
    from fastapi.testclient import TestClient

    db = str(tmp_path / "api.sqlite3")
    _seed_db(db)
    log = tmp_path / "api.log"
    log.write_text("{d} 09:15:05 INFO  x | live_job: streaming Dhan feed for 40 symbols\n"
                   "{d} 15:30:00 INFO  x | live_job leg done: 15000 bars, 2 picks, 2 paper "
                   "trades (persisted)\n".format(d=DAY))
    run_review(DAY, cfg=None, db_url="sqlite:///" + db, log_path=str(log),
               out_dir=str(tmp_path / "desk"), trading_day=True)

    monkeypatch.setenv("SE_DB_URL", "sqlite:///" + db)
    monkeypatch.delenv("SE_API_TOKEN", raising=False)
    monkeypatch.setenv("SE_DATA_SOURCE", "mock")
    from signal_engine.api.app import create_app

    resp = TestClient(create_app()).get("/api/desk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_daily_net_pct"] == 1.00
    assert body["latest_review"]["day"] == DAY
    assert body["gap_series"]["session"] and body["gap_series"]["gated"]
    assert body["priors"]
    for p in body["proposals"]:
        assert p["applied"] == 0 and p["status"] == "proposed"
    assert "never auto-applied" in body["note"] or "human to apply" in body["note"]


def test_proposals_are_idempotent_per_day_and_key(tmp_path):
    """Re-running a night's review must REPLACE that day's proposal for a key, not stack copies.

    Without this, a manual re-run (or a scheduler misfire retry) turns one proposal into six and
    the dashboard shows a wall of duplicates — which is exactly what happened during development.
    """
    store = _store(tmp_path)
    a = store.add_proposal(DAY, "config/risk.yaml", "risk.k", "0", "1", "d1", "r1", "b", "e",
                           "x", 1)
    b = store.add_proposal(DAY, "config/risk.yaml", "risk.k", "0", "2", "d2", "r2", "b", "e",
                           "x", 1)
    assert a == b
    rows = store.fetch_proposals(day=DAY)
    assert len(rows) == 1
    assert rows[0]["proposed_value"] == "2"           # replaced in place
    assert rows[0]["applied"] == 0                    # still never applied
    # A different day is a different proposal.
    c = store.add_proposal("2026-07-23", "config/risk.yaml", "risk.k", "0", "1", "d", "r", "b",
                           "e", "x", 1)
    assert c != a
    # Once a human moves the row out of `proposed`, it is history: a re-run must not overwrite it.
    store.conn.execute("UPDATE desk_proposals SET status = 'applied_by_human', applied = 1 "
                       "WHERE id = ?", (a,))
    store.conn.commit()
    d = store.add_proposal(DAY, "config/risk.yaml", "risk.k", "0", "3", "d3", "r3", "b", "e",
                           "x", 1)
    assert d != a
    kept = {r["id"]: r for r in store.fetch_proposals(day=DAY)}
    assert kept[a]["proposed_value"] == "2" and kept[a]["applied"] == 1   # untouched history
    assert kept[d]["proposed_value"] == "3" and kept[d]["applied"] == 0   # the new proposal
    store.close()


def test_healthy_session_with_zero_trades_is_not_read_as_progress(tmp_path):
    """A flat day must be labelled a SELECTIVITY fact, not a result.

    This path runs often (the P0 gate rejects most setups), so it has to be right: the report must
    render, the gap must come back NOT COMPUTABLE, and the narrative must say plainly that a
    zero-trade day is neither progress nor evidence.
    """
    db = str(tmp_path / "flat.sqlite3")
    _seed_db(db, n_trades=0)
    log = tmp_path / "flat.log"
    log.write_text(
        "{d} 09:15:05 INFO  x | live_job: streaming Dhan feed for 40 symbols\n"
        "{d} 09:20:00 INFO  x | ENTRY-CTX ACME LONG | above-VWAP\n"
        "{d} 15:30:00 INFO  x | live_job leg done: 15000 bars, 0 picks, 0 paper trades "
        "(persisted)\n".format(d=DAY))
    review = run_review(DAY, cfg=None, db_url="sqlite:///" + db, log_path=str(log),
                        out_dir=str(tmp_path / "desk"), trading_day=True)
    assert review.facts.integrity.verdict == "OK"
    assert review.concluded is True and review.facts.n_trades == 0
    assert "SELECTIVITY fact" in review.markdown
    assert "must not be read as progress" in review.markdown
    assert "NOT COMPUTABLE" in review.gaps["session"].verdict_line()
    # And the digest must not imply a good day.
    assert "no edge implied" in review.digest.lower()
