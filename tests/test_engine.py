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


# =========================================================================== #
# Capital-preservation / UX layer (plan D1, D1b, D2, D4, M0, M1).
# These are UNIT-level tests that construct plans directly, so they are independent
# of whatever the synthetic strategy happens to surface on a given day.
# =========================================================================== #
import pytz  # noqa: E402

from signal_engine.config import RiskParams  # noqa: E402
from signal_engine.domain.enums import Direction  # noqa: E402
from signal_engine.domain.models import TradePlan  # noqa: E402
from signal_engine.engine.runner import _LossBreaker  # noqa: E402
from signal_engine.risk.sizing import position_size, size_plan  # noqa: E402

_IST = pytz.timezone("Asia/Kolkata")


def _mk_plan(symbol="X", direction=Direction.LONG, entry=100.0, stop_pct=1.0,
             t1_pct=2.0, conf=70.0, cost=0.1, minute=10):
    ts = _IST.localize(__import__("datetime").datetime(2025, 6, 23, 10, minute))
    sl = entry * (1 - stop_pct / 100) if direction == Direction.LONG else entry * (1 + stop_pct / 100)
    t1 = entry * (1 + t1_pct / 100) if direction == Direction.LONG else entry * (1 - t1_pct / 100)
    return TradePlan(
        symbol=symbol, ts=ts, direction=direction, strategy="s", entry=entry,
        stop_loss=sl, stop_pct=stop_pct, targets=[t1, t1], target_pcts=[t1_pct, t1_pct],
        expected_move_pct=t1_pct, risk_reward=t1_pct / stop_pct,
        cost_to_break_even_pct=cost, confidence=conf,
    )


# -- M0: sizing -------------------------------------------------------------------------
def test_size_plan_fixed_fractional_math():
    """capital=100000, risk%=0.5 -> ₹500 budget; stop distance = 1 (entry 100, stop 99) ->
    qty 500; rupee_risk ₹500; notional ₹50000."""
    risk = RiskParams(account_capital=100000.0, risk_per_trade_pct=0.5,
                      kelly_fraction_cap=1.0)  # disable kelly cap for the clean check
    p = _mk_plan(entry=100.0, stop_pct=1.0)   # stop_loss=99 -> distance 1.0
    out = size_plan(p, risk)
    assert out["qty"] == 500
    assert abs(out["rupee_risk"] - 500.0) < 1e-6
    assert abs(out["notional"] - 50000.0) < 1e-6


def test_size_plan_kelly_fraction_caps_risk():
    """A tiny stop would risk more than kelly_fraction_cap of capital -> qty capped."""
    # risk% budget = 5% of cap = ₹5000; but kelly cap = 1% of cap = ₹1000.
    risk = RiskParams(account_capital=100000.0, risk_per_trade_pct=5.0,
                      kelly_fraction_cap=0.01)
    p = _mk_plan(entry=100.0, stop_pct=1.0)  # distance 1.0
    out = size_plan(p, risk)
    # Capped at ₹1000 risk / ₹1 = 1000 shares (not the 5000 the raw risk% would allow).
    assert out["qty"] == 1000
    assert out["rupee_risk"] <= 100000.0 * 0.01 + 1e-6


def test_size_plan_notional_cap_no_leverage():
    """qty * entry can never exceed capital (no implicit leverage)."""
    risk = RiskParams(account_capital=10000.0, risk_per_trade_pct=50.0,
                      kelly_fraction_cap=1.0)
    p = _mk_plan(entry=100.0, stop_pct=0.1)  # huge budget vs tiny stop
    out = size_plan(p, risk)
    assert out["notional"] <= 10000.0 + 1e-6
    assert out["qty"] == 100  # floor(10000/100)


def test_size_plan_zero_risk_distance_is_safe():
    risk = RiskParams()
    p = _mk_plan(entry=100.0)
    p = TradePlan(**{**p.__dict__, "stop_loss": 100.0})  # entry == stop
    out = size_plan(p, risk)
    assert out["qty"] == 0 and out["rupee_risk"] == 0.0


def test_position_size_primitive_unchanged():
    """The original primitive keeps its exact contract (backward-compat)."""
    out = position_size(100000.0, 1.0, 1000.0, 985.0)
    assert out["qty"] == 66


# -- M1: daily-loss circuit breaker -----------------------------------------------------
def test_breaker_halts_on_daily_loss():
    b = _LossBreaker(daily_max_loss_pct=2.0, max_consecutive_losses=99)
    b.record(-1.0)
    assert not b.halted
    b.record(-1.5)            # drawdown 2.5% from a 0 peak >= 2.0% -> halt
    assert b.halted
    assert "drawdown" in b.halt_reason


def test_breaker_trips_on_drawdown_from_peak():
    """The 2026-06-25 fix: a book that ran up then gave it back must halt on the give-back,
    even though cumulative realized PnL is still positive/small. The OLD cumulative test let a
    deep intra-session trough pass because earlier wins netted it up."""
    b = _LossBreaker(daily_max_loss_pct=4.0, max_consecutive_losses=99)
    b.record(+3.0)           # peak +3%
    b.record(-1.5)           # realized +1.5%, drawdown 1.5% from peak -> not yet
    assert not b.halted
    b.record(-2.6)           # realized -1.1%, drawdown 4.1% from peak +3% -> halt
    assert b.halted          # note: cumulative realized is only -1.1%, far inside a -4% cumulative cap
    assert "drawdown" in b.halt_reason


def test_breaker_halts_on_consecutive_losses():
    b = _LossBreaker(daily_max_loss_pct=99.0, max_consecutive_losses=3)
    b.record(-0.1)
    b.record(-0.1)
    assert not b.halted
    b.record(-0.1)            # 3rd straight loss -> halt
    assert b.halted
    assert "consecutive losses" in b.halt_reason


def test_breaker_win_resets_consecutive_run():
    b = _LossBreaker(daily_max_loss_pct=99.0, max_consecutive_losses=3)
    b.record(-0.1)
    b.record(-0.1)
    b.record(+0.5)            # a win clears the run
    assert b.consecutive_losses == 0
    b.record(-0.1)
    b.record(-0.1)
    assert not b.halted       # only 2 in a row again


def test_breaker_halts_entries_in_runner():
    """Once the breaker trips, the runner's read-only gate refuses new entries."""
    cfg, runner, _ = _run()
    runner.breaker._halted = True
    runner.breaker.halt_reason = "test"
    ts = _IST.localize(__import__("datetime").datetime(2025, 6, 23, 11, 0))
    assert runner._gate_ok("RELIANCE", ts) is False


# -- D1b: ranking picks top-N by (edge/stop)*conf --------------------------------------
def test_ranking_orders_by_edge_per_risk_times_conf():
    cfg, runner, _ = _run()
    # Two candidates collected in the same minute; A clearly outranks B.
    hi = _mk_plan(symbol="AAA", t1_pct=3.0, stop_pct=1.0, conf=90)   # edge big, conf high
    lo = _mk_plan(symbol="BBB", t1_pct=1.5, stop_pct=1.0, conf=50)
    assert runner._rank(hi) > runner._rank(lo)


def test_flush_surfaces_only_top_n():
    cfg, runner, _ = _run()
    runner.summary.picks.clear()
    runner._daily_trades = 0
    # Force a small N regardless of config.
    cfg.risk.alerts.top_n_alerts = 2
    # Collect 4 distinct-symbol candidates in one minute.
    for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD"]):
        runner._collect_candidate(_mk_plan(symbol=sym, t1_pct=2.0 + i, stop_pct=1.0, conf=70))
    runner._flush_pending()
    assert len(runner.summary.picks) == 2
    # The two highest-ranked (largest t1_pct) win: DDD (t1 5.0) then CCC (t1 4.0).
    surfaced = {p.symbol for p in runner.summary.picks}
    assert surfaced == {"DDD", "CCC"}


def test_ranking_tiebreak_is_deterministic():
    cfg, runner, _ = _run()
    runner.summary.picks.clear()
    runner._daily_trades = 0
    cfg.risk.alerts.top_n_alerts = 1
    # Identical rank inputs -> tie broken by symbol (alphabetical).
    runner._collect_candidate(_mk_plan(symbol="ZZZ", t1_pct=2.0, stop_pct=1.0, conf=70))
    runner._collect_candidate(_mk_plan(symbol="AAA", t1_pct=2.0, stop_pct=1.0, conf=70))
    runner._flush_pending()
    assert [p.symbol for p in runner.summary.picks] == ["AAA"]


# -- D1: gate-before-advisor (no phantom NEW alert) ------------------------------------
class _CapturingAlerter:
    def __init__(self):
        self.msgs = []

    def send(self, message, level="info"):
        self.msgs.append((level, message))


def test_gate_before_advisor_suppresses_phantom_new_alert():
    """A symbol that is non-actionable (daily cap reached) must NOT emit a NEW alert via the
    advisor, even though a valid plan exists for it."""
    from signal_engine.engine.advisor import LiveAdvisor

    cfg, runner, _ = _run()
    runner.alerter = _CapturingAlerter()
    runner.advisor = LiveAdvisor()
    # Saturate the daily-trade cap so the gate is closed for everyone.
    runner._daily_trades = cfg.risk.risk.max_trades_per_day

    plan = _mk_plan(symbol="NEWSYM", minute=11)
    actionable = runner._gate_ok("NEWSYM", plan.ts)
    assert actionable is False
    msg = runner.advisor.update("NEWSYM", plan, actionable=actionable)
    assert msg is None  # no fresh-looking NEW alert for a symbol we can't enter
    assert all("NEW" not in m for _lvl, m in runner.alerter.msgs)
