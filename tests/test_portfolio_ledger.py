"""PortfolioLedger tests (PORTFOLIO §3/§10) — hand-computed ₹ math on the paper book.

Charges use the REAL CostModel (config defaults: flat=20, pct=0.0003, stt=0.00025,
exch=0.0000297, gst=0.18, sebi=0.000001, stamp=0.00003) so every rupee below is verified
by hand, the same way tests/test_costs.py does it. All money is paper.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytz

from signal_engine.config import CostParams
from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
from signal_engine.domain.models import PaperPosition, TradePlan
from signal_engine.portfolio import PortfolioLedger
from signal_engine.risk.costs import CostModel
from signal_engine.storage.repository import SignalRepository

IST = pytz.timezone("Asia/Kolkata")
TOL = 1e-6  # contract tolerance for the equity invariant

# LONG hand math: entry_fill=100, exit_fill=102, qty=100
#   buy_value=10000, sell_value=10200
#   brokerage = min(20, 0.0003*10000) + min(20, 0.0003*10200) = 3.00 + 3.06 = 6.06
#   stt       = 0.00025 * 10200                               = 2.55
#   exchange  = 0.0000297 * 20200                             = 0.59994
#   gst       = 0.18 * (6.06 + 0.59994)                       = 1.1987892
#   sebi      = 0.000001 * 20200                              = 0.0202
#   stamp     = 0.00003 * 10000                               = 0.3
#   total                                                     = 10.7289292
LONG_CHARGES = 10.7289292
LONG_PNL = 200.0 - LONG_CHARGES  # (102-100)*100 - charges = 189.2710708

# SHORT hand math: entry_fill=200 (sell), exit_fill=196 (buy back), qty=50 —
# charges(entry_fill, exit_fill, qty) per the contract:
#   buy_value=10000, sell_value=9800
#   brokerage = min(20, 3.0) + min(20, 2.94) = 5.94
#   stt       = 0.00025 * 9800              = 2.45
#   exchange  = 0.0000297 * 19800           = 0.58806
#   gst       = 0.18 * (5.94 + 0.58806)     = 1.1750508
#   sebi      = 0.000001 * 19800            = 0.0198
#   stamp     = 0.00003 * 10000             = 0.3
#   total                                   = 10.4729108
SHORT_CHARGES = 10.4729108
SHORT_PNL = 200.0 - SHORT_CHARGES  # -1*(196-200)*50 - charges = 189.5270892


def _repo():
    return SignalRepository("sqlite:///:memory:")


def _cost_model():
    return CostModel(CostParams())  # on_exit only uses .charges(); no slippage model needed


def _plan(symbol="RELIANCE", entry=100.0, direction=Direction.LONG):
    ts = IST.localize(datetime(2026, 7, 3, 9, 30))
    stop = entry * (0.99 if direction == Direction.LONG else 1.01)
    target = entry * (1.02 if direction == Direction.LONG else 0.98)
    return TradePlan(
        symbol=symbol, ts=ts, direction=direction, strategy="vwap_ema_adx",
        entry=entry, stop_loss=stop, stop_pct=1.0, targets=[target], target_pcts=[2.0],
        expected_move_pct=2.0, risk_reward=2.0, cost_to_break_even_pct=0.14,
        confidence=70.0, reasons=["t"], time_validity=None,
    )


def _open_position(pos_id="p1", symbol="RELIANCE", entry_fill=100.0,
                   direction=Direction.LONG):
    ts = IST.localize(datetime(2026, 7, 3, 9, 31))
    return PaperPosition(id=pos_id, plan=_plan(symbol, entry_fill, direction),
                         status=PositionStatus.OPEN, entry_fill=entry_fill, entry_ts=ts)


def _close(pos, exit_fill, reason=ExitReason.TARGET):
    pos.exit_fill = exit_fill
    pos.exit_ts = pos.entry_ts + timedelta(minutes=15)
    pos.exit_reason = reason
    pos.status = PositionStatus.CLOSED
    return pos


def _assert_equity_invariant(ledger, marks=None):
    """equity == cash + Σ_open(notional + unrealized ₹) — recomputed independently."""
    total = ledger.cash
    for row in ledger.repo.fetch_open_positions():
        qty = row.get("qty") or 0
        if not qty:
            continue
        entry = row["entry_fill"]
        last = (marks or {}).get(row["symbol"], row["last_price"])
        last = entry if last is None else last
        sign = -1 if row["direction"] == "SHORT" else 1
        total += row["notional"] + sign * (last - entry) * qty
    assert abs(ledger.equity(marks) - total) < TOL


# --------------------------------------------------------------------------- #
# Fresh book + entry/exit ₹ math (hand-computed)
# --------------------------------------------------------------------------- #
def test_fresh_book_anchors_at_starting_capital():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    assert ledger.cash == pytest.approx(100000.0)
    assert ledger.equity() == pytest.approx(100000.0)
    assert ledger.invested == 0.0 and ledger.realized_pnl_total == 0.0
    state = repo.fetch_portfolio_state()  # persisted at construction
    assert state["starting_capital"] == 100000.0 and state["cash"] == 100000.0
    repo.close()


def test_long_entry_exit_hand_computed():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    pos = _open_position(entry_fill=100.0)

    ledger.on_entry(pos, 100)
    assert pos.qty == 100 and pos.notional == pytest.approx(10000.0)
    assert ledger.cash == pytest.approx(90000.0)      # notional blocked
    assert ledger.invested == pytest.approx(10000.0)
    assert ledger.equity() == pytest.approx(100000.0, abs=TOL)  # no mark yet -> no unrealized
    _assert_equity_invariant(ledger)

    money = ledger.on_exit(_close(pos, 102.0), 100, _cost_model())
    assert money["qty"] == 100
    assert money["notional"] == pytest.approx(10000.0)
    assert money["charges_inr"] == pytest.approx(LONG_CHARGES, abs=TOL)
    assert money["pnl_inr"] == pytest.approx(LONG_PNL, abs=TOL)
    assert money["cash_after"] == pytest.approx(100000.0 + LONG_PNL, abs=TOL)
    assert money["equity_after"] == pytest.approx(100000.0 + LONG_PNL, abs=TOL)
    # Contract invariant: after any close, equity == starting + Σ pnl_inr(all closed).
    assert abs(ledger.equity() - (100000.0 + LONG_PNL)) < TOL
    _assert_equity_invariant(ledger)

    # Persisted rows: closed trade carries the ₹ fields (2dp on disk), open row is gone.
    (trade,) = repo.fetch_trades()
    assert trade["qty"] == 100
    assert trade["notional_entry"] == pytest.approx(10000.0)
    assert trade["charges_inr"] == pytest.approx(round(LONG_CHARGES, 2))
    assert trade["pnl_inr"] == pytest.approx(round(LONG_PNL, 2))
    assert repo.fetch_open_positions() == []
    state = repo.fetch_portfolio_state()
    assert state["cash"] == pytest.approx(round(100000.0 + LONG_PNL, 2))
    assert state["realized_pnl_total"] == pytest.approx(round(LONG_PNL, 2))
    repo.close()


def test_short_entry_exit_hand_computed():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    pos = _open_position(pos_id="s1", symbol="TCS", entry_fill=200.0,
                         direction=Direction.SHORT)

    ledger.on_entry(pos, 50)
    # SHORT blocks notional as margin exactly like a LONG (no leverage).
    assert ledger.cash == pytest.approx(90000.0)
    assert ledger.invested == pytest.approx(10000.0)
    _assert_equity_invariant(ledger)

    money = ledger.on_exit(_close(pos, 196.0), 50, _cost_model())
    assert money["charges_inr"] == pytest.approx(SHORT_CHARGES, abs=TOL)
    assert money["pnl_inr"] == pytest.approx(SHORT_PNL, abs=TOL)  # price fell -> short gains
    assert ledger.cash == pytest.approx(100000.0 + SHORT_PNL, abs=TOL)
    assert abs(ledger.equity() - (100000.0 + SHORT_PNL)) < TOL
    repo.close()


def test_losing_long_debits_the_book():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    pos = _open_position(entry_fill=100.0)
    ledger.on_entry(pos, 100)
    money = ledger.on_exit(_close(pos, 99.0, ExitReason.STOP), 100, _cost_model())
    # Gross -100 minus charges: the book must end BELOW starting capital.
    assert money["pnl_inr"] < -100.0
    assert ledger.cash == pytest.approx(100000.0 + money["pnl_inr"], abs=TOL)
    assert ledger.cash < 100000.0
    repo.close()


# --------------------------------------------------------------------------- #
# Affordability + no-leverage invariant
# --------------------------------------------------------------------------- #
def test_affordable_qty_caps_by_free_cash():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    plan = _plan(entry=300.0)
    assert ledger.affordable_qty(plan, 50) == 50            # affordable in full
    assert ledger.affordable_qty(plan, 400) == 333          # floor(100000/300)
    assert ledger.affordable_qty(plan, 0) == 0
    assert ledger.affordable_qty(plan, -5) == 0             # never negative
    assert ledger.affordable_qty(_plan(entry=0.0), 10) == 0  # degenerate price
    repo.close()


def test_no_leverage_with_three_concurrent_positions():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    # 30000 + 15000 + 50000 = 95000 blocked: all three fit, ₹5,000 stays free.
    for i, (symbol, entry) in enumerate([("A", 300.0), ("B", 150.0), ("C", 500.0)]):
        pos = _open_position(pos_id=f"c{i}", symbol=symbol, entry_fill=entry)
        qty = ledger.affordable_qty(pos.plan, 100)
        assert qty == 100                                    # all three affordable in full
        ledger.on_entry(pos, qty)
        assert ledger.cash >= 0                              # never below zero
        _assert_equity_invariant(ledger)
    assert ledger.invested == pytest.approx(95000.0)
    assert ledger.cash == pytest.approx(5000.0)
    repo.close()


def test_no_leverage_fourth_entry_is_capped_then_zero():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    for i, (symbol, entry) in enumerate([("A", 300.0), ("B", 300.0), ("C", 300.0)]):
        pos = _open_position(pos_id=f"c{i}", symbol=symbol, entry_fill=entry)
        ledger.on_entry(pos, ledger.affordable_qty(pos.plan, 100))
    assert ledger.cash == pytest.approx(10000.0)             # 3 * 30000 blocked
    big = _plan(symbol="D", entry=250.0)
    qty = ledger.affordable_qty(big, 100)
    assert qty == 40                                          # floor(10000/250), not 100
    pos4 = _open_position(pos_id="c3", symbol="D", entry_fill=250.0)
    ledger.on_entry(pos4, qty)
    assert ledger.cash == pytest.approx(0.0, abs=TOL)         # fully deployed, never negative
    assert ledger.affordable_qty(_plan(symbol="E", entry=50.0), 10) == 0  # book is full
    _assert_equity_invariant(ledger)
    repo.close()


# --------------------------------------------------------------------------- #
# Marks / unrealized ₹
# --------------------------------------------------------------------------- #
def test_equity_with_marks_long_and_short():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    lng = _open_position(pos_id="L", symbol="LNG", entry_fill=100.0)
    sht = _open_position(pos_id="S", symbol="SHT", entry_fill=200.0,
                         direction=Direction.SHORT)
    ledger.on_entry(lng, 100)
    ledger.on_entry(sht, 50)
    # LONG +1/share on 100 shares = +100; SHORT -2/share fall on 50 shares = +100.
    marks = {"LNG": 101.0, "SHT": 198.0}
    assert ledger.equity(marks) == pytest.approx(100000.0 + 100.0 + 100.0, abs=TOL)
    # Adverse marks subtract symmetrically.
    marks = {"LNG": 99.0, "SHT": 202.0}
    assert ledger.equity(marks) == pytest.approx(100000.0 - 100.0 - 100.0, abs=TOL)
    _assert_equity_invariant(ledger, marks)
    repo.close()


def test_equity_falls_back_to_stored_last_price():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    pos = _open_position(entry_fill=100.0)
    ledger.on_entry(pos, 100)
    repo.save_open_position(pos, last_price=103.0)  # the per-bar mark the runner writes
    assert ledger.equity() == pytest.approx(100000.0 + 300.0, abs=TOL)
    row = repo.fetch_open_positions()[0]
    assert row["unrealized_pnl_inr"] == pytest.approx(300.0)  # derived + persisted
    repo.close()


# --------------------------------------------------------------------------- #
# rebuild() — restart / warm-start double-spend guard
# --------------------------------------------------------------------------- #
def _seed_closed_trade(repo, trade_id, pnl_inr, day="2026-07-03"):
    repo.conn.execute(
        "INSERT INTO paper_trades (id, symbol, entry_ts, exit_ts, pnl_inr) VALUES (?,?,?,?,?)",
        (trade_id, "SEED", f"{day}T10:00:00+05:30", f"{day}T10:30:00+05:30", pnl_inr))
    repo.conn.commit()


def test_rebuild_derives_cash_from_tables_ignoring_legacy_rows():
    repo = _repo()
    _seed_closed_trade(repo, "t1", 189.27)
    _seed_closed_trade(repo, "t2", -50.5)
    _seed_closed_trade(repo, "t3", None)          # legacy pre-ledger row -> ignored
    # One sized open position (re-blocks its notional) + one legacy open row (no qty).
    sized = _open_position(pos_id="o1", symbol="OPN", entry_fill=200.0)
    sized.qty, sized.notional = 100, 20000.0
    repo.save_open_position(sized)
    repo.save_open_position(_open_position(pos_id="o2", symbol="LEG", entry_fill=50.0))

    ledger = PortfolioLedger(repo, 100000.0)      # no state row -> rebuild at construction
    expected_cash = 100000.0 + 189.27 - 50.5 - 20000.0
    assert ledger.cash == pytest.approx(expected_cash, abs=TOL)
    assert ledger.realized_pnl_total == pytest.approx(189.27 - 50.5, abs=TOL)
    assert ledger.invested == pytest.approx(20000.0)

    ledger.rebuild()                              # idempotent
    assert ledger.cash == pytest.approx(expected_cash, abs=TOL)
    state = repo.fetch_portfolio_state()
    assert state["cash"] == pytest.approx(round(expected_cash, 2))
    repo.close()


def test_restart_rebuild_cannot_double_spend():
    """Simulates the warm-start path: same session re-derived twice ends at the same cash."""
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    pos = _open_position(entry_fill=100.0)
    ledger.on_entry(pos, 100)
    ledger.on_exit(_close(pos, 102.0), 100, _cost_model())
    cash_after_session = ledger.cash

    # Restart: a fresh ledger loads persisted state, then live() calls rebuild().
    ledger2 = PortfolioLedger(repo, 100000.0)
    assert ledger2.cash == pytest.approx(round(cash_after_session, 2))  # 2dp on disk
    ledger2.rebuild()
    # rebuild derives from the persisted (2dp) trade rows — no drift, no double-count.
    assert ledger2.cash == pytest.approx(100000.0 + round(LONG_PNL, 2), abs=TOL)
    ledger2.rebuild()
    assert ledger2.cash == pytest.approx(100000.0 + round(LONG_PNL, 2), abs=TOL)
    repo.close()


def test_on_entry_is_idempotent_per_position():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    pos = _open_position(entry_fill=100.0)
    ledger.on_entry(pos, 100)
    ledger.on_entry(pos, 100)                     # repeated call must not double-debit
    assert ledger.cash == pytest.approx(90000.0)
    repo.close()


def test_unsized_position_moves_no_money():
    """A legacy/no-ledger position (qty 0) exits without touching the book."""
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    pos = _open_position(entry_fill=100.0)
    ledger.on_entry(pos, 0)
    assert ledger.cash == pytest.approx(100000.0)
    money = ledger.on_exit(_close(pos, 102.0), 0, _cost_model())
    assert money == {"qty": 0, "notional": 0.0, "charges_inr": 0.0, "pnl_inr": 0.0,
                     "equity_after": pytest.approx(100000.0),
                     "cash_after": pytest.approx(100000.0)}
    repo.close()


# --------------------------------------------------------------------------- #
# Snapshots (portfolio_equity)
# --------------------------------------------------------------------------- #
def test_snapshot_rows_mark_and_eod():
    repo = _repo()
    ledger = PortfolioLedger(repo, 100000.0)
    pos = _open_position(entry_fill=100.0)
    ledger.on_entry(pos, 100)
    ledger.snapshot("mark", {"RELIANCE": 101.0})
    ledger.on_exit(_close(pos, 102.0), 100, _cost_model())
    ledger.snapshot("eod", {})

    points = repo.fetch_equity_curve(days=1)
    assert [p["kind"] for p in points] == ["mark", "eod"]
    mark, eod = points
    assert mark["equity"] == pytest.approx(round(100000.0 + 100.0, 2))  # +₹100 unrealized
    assert mark["cash"] == pytest.approx(90000.0)
    assert mark["invested"] == pytest.approx(10000.0)
    assert mark["unrealized_pnl"] == pytest.approx(100.0)
    assert mark["open_count"] == 1 and mark["day"] and mark["ts"]
    assert eod["equity"] == pytest.approx(round(100000.0 + LONG_PNL, 2))
    assert eod["invested"] == 0.0 and eod["open_count"] == 0
    # sum_closed_pnl_inr filters by exit DAY (the API's "today" number).
    exit_day = repo.fetch_trades()[0]["exit_ts"][:10]
    assert repo.sum_closed_pnl_inr(day=exit_day) == pytest.approx(round(LONG_PNL, 2))
    assert repo.sum_closed_pnl_inr(day="1999-01-01") == 0.0
    repo.close()
