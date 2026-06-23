"""Paper-trading analytics + persistence tests.

The metric math is checked against a tiny HAND-COMPUTED trade set — a wrong P&L/PF/drawdown
number would defeat the whole tracker, so these are exact assertions, not smoke tests.
"""

from __future__ import annotations

from datetime import datetime

import pytz

from signal_engine.analytics import paper
from signal_engine.config import CostParams
from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
from signal_engine.domain.models import PaperPosition, TradePlan
from signal_engine.risk.costs import CostModel
from signal_engine.storage.repository import SignalRepository

IST = pytz.timezone("Asia/Kolkata")


def _t(net, symbol="X", strategy="s", entry_ts="2026-06-23T09:30:00", direction="LONG"):
    """A pre-enriched trade carrying a known net P&L."""
    return {"net_pnl_abs": net, "net_pnl_pct": net / 1000.0, "symbol": symbol,
            "strategy": strategy, "direction": direction, "entry_ts": entry_ts,
            "exit_ts": entry_ts, "tod": paper._tod_bucket(entry_ts)}


# --- hand-checked metric math ----------------------------------------------

def test_summary_matches_hand_computed_values():
    # net P&Ls: +100, -50, +200, -80, +30
    trades = [_t(100), _t(-50), _t(200), _t(-80), _t(30)]
    s = paper.summary(trades)
    assert s["n_trades"] == 5
    assert s["wins"] == 3 and s["losses"] == 2
    assert s["win_rate"] == 60.0                       # 3/5
    assert s["total_pnl_abs"] == 200.0                 # 100-50+200-80+30
    assert s["avg_win"] == 110.0                       # (100+200+30)/3
    assert s["avg_loss"] == -65.0                      # (-50-80)/2
    assert s["profit_factor"] == 2.5385                # 330/130
    assert s["expectancy"] == 40.0                     # 200/5
    assert s["max_drawdown"] == 80.0                   # equity 100,50,250,170,200 -> dd 80
    assert s["best_trade"]["net_pnl_abs"] == 200.0
    assert s["worst_trade"]["net_pnl_abs"] == -80.0


def test_max_drawdown_explicit():
    # equity peaks at 250 then drops to 170 -> 80; recovers; later 300 -> 120 is the max
    assert paper.max_drawdown([100, 250, 170, 220, 300, 180]) == 120.0
    assert paper.max_drawdown([10, 20, 30]) == 0.0     # monotonic up -> no drawdown


def test_profit_factor_edge_cases():
    assert paper.profit_factor([10, -5, 20, -5]) == 3.0   # 30 / 10
    assert paper.profit_factor([10, 20, 30]) is None       # no losers -> undefined


def test_equity_curve_is_cumulative_in_time_order():
    trades = [_t(100, entry_ts="2026-06-23T10:00:00"),
              _t(-30, entry_ts="2026-06-23T09:20:00"),
              _t(50, entry_ts="2026-06-23T11:00:00")]
    curve = paper.equity_curve(trades)
    assert [p["cum_pnl"] for p in curve] == [-30.0, 70.0, 120.0]  # sorted by ts then cumulative


def test_win_rate_uses_pnl_not_target_hit():
    # a tiny positive square-off is a WIN; a tiny negative is a LOSS
    s = paper.summary([_t(0.01), _t(-0.01)])
    assert s["wins"] == 1 and s["losses"] == 1 and s["win_rate"] == 50.0


# --- enrichment money-math (with the real cost model) ----------------------

def test_enrich_trade_long_quantity_costs_and_net_pnl():
    cm = CostModel(CostParams())
    trade = {"direction": "LONG", "entry_fill": 100.0, "exit_fill": 102.0,
             "symbol": "ABC", "entry_ts": "2026-06-23T09:30:00"}
    e = paper.enrich_trade(trade, notional=100_000.0, cost_model=cm)
    assert e["qty"] == 1000                       # 100000 / 100
    assert e["gross_pnl_abs"] == 2000.0           # 1000 * (102-100)
    assert abs(e["costs_abs"] - 82.98) < 0.05     # hand-computed round-trip charges
    assert abs(e["net_pnl_abs"] - 1917.02) < 0.05
    assert abs(e["net_pnl_pct"] - 1.91702) < 0.001


def test_enrich_trade_short_profits_when_price_falls():
    cm = CostModel(CostParams())
    trade = {"direction": "SHORT", "entry_fill": 100.0, "exit_fill": 98.0, "symbol": "ABC"}
    e = paper.enrich_trade(trade, notional=100_000.0, cost_model=cm)
    assert e["gross_pnl_abs"] == 2000.0           # short gains 2/share when price drops 100->98
    assert e["net_pnl_abs"] > 1900                # net of costs, still strongly positive


def test_time_of_day_bucketing():
    assert paper._tod_bucket("2026-06-23T09:30:00").startswith("Open")
    assert paper._tod_bucket("2026-06-23T11:00:00").startswith("Late-morning")
    assert paper._tod_bucket("2026-06-23T15:10:00").startswith("Close")


def test_auto_summary_flags_losing_profit_factor():
    # 4 small wins + 1 huge loser: high win rate, profit factor < 1
    trades = [_t(20), _t(20), _t(20), _t(20), _t(-200)]
    flags = " ".join(paper.auto_summary(trades))
    assert "profit factor" in flags.lower()
    assert "net-losing" in flags.lower()


# --- persistence round-trip across a restart -------------------------------

def _make_position(pid, symbol, pnl_pct, won, entry_ts, direction=Direction.LONG):
    plan = TradePlan(
        symbol=symbol, ts=entry_ts, direction=direction, strategy="vwap_ema_adx",
        entry=100.0, stop_loss=99.0, stop_pct=1.0, targets=[102.0, 103.0],
        target_pcts=[2.0, 3.0], expected_move_pct=2.0, risk_reward=2.0,
        cost_to_break_even_pct=0.1, confidence=72.0, reasons=["test"],
    )
    return PaperPosition(
        id=pid, plan=plan, status=PositionStatus.CLOSED,
        entry_fill=100.0, entry_ts=entry_ts, exit_fill=102.0,
        exit_ts=entry_ts, exit_reason=ExitReason.TARGET, pnl_pct_net=pnl_pct,
        r_multiple=2.0, hold_minutes=12.0, won=won,
    )


def test_paper_trades_persist_and_reload_across_restart(tmp_path):
    db = f"sqlite:///{tmp_path}/trades.sqlite3"
    ts = IST.localize(datetime(2026, 6, 23, 9, 30))

    repo = SignalRepository(db)
    repo.save_position(_make_position("t1", "RELIANCE", 1.5, True, ts))
    repo.save_position(_make_position("t2", "TCS", -0.8, False, ts))
    repo.close()  # simulate process exit

    repo2 = SignalRepository(db)  # reopen the same file
    rows = repo2.fetch_trades()
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id["t1"]["symbol"] == "RELIANCE" and by_id["t1"]["won"] == 1
    assert by_id["t1"]["stop_loss"] == 99.0 and by_id["t1"]["target"] == 102.0
    assert by_id["t2"]["pnl_pct_net"] == -0.8
    repo2.close()


def test_fetch_trades_filters_by_symbol_and_strategy(tmp_path):
    db = f"sqlite:///{tmp_path}/f.sqlite3"
    ts = IST.localize(datetime(2026, 6, 23, 9, 30))
    repo = SignalRepository(db)
    repo.save_position(_make_position("a", "RELIANCE", 1.0, True, ts))
    repo.save_position(_make_position("b", "TCS", 1.0, True, ts))
    assert {r["id"] for r in repo.fetch_trades(symbol="RELIANCE")} == {"a"}
    assert len(repo.fetch_trades(start="2026-06-23", end="2026-06-23")) == 2
    assert len(repo.fetch_trades(start="2026-06-24")) == 0
    repo.close()
