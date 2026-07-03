"""Portfolio API contract (PORTFOLIO §7/§10).

Seeds a temp DB through SignalRepository + PortfolioLedger, then asserts /api/portfolio and
/api/portfolio/equity serve the frozen contract the web page reads — including fresh-book
defaults (never 500), the equity/invested/P&L math, and the legacy-row 'modeled' marker.
"""

import os

import pytest
from fastapi.testclient import TestClient

from signal_engine.api.app import create_app
from signal_engine.config import load_config
from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
from signal_engine.domain.models import PaperPosition, TradePlan
from signal_engine.portfolio.ledger import PortfolioLedger
from signal_engine.storage.repository import SignalRepository

import datetime as _dt

import pytz

_IST = pytz.timezone("Asia/Kolkata")


def _client(tmp_path, monkeypatch):
    db = tmp_path / "book.sqlite3"
    monkeypatch.setenv("SE_DB_URL", f"sqlite:///{db}")
    monkeypatch.delenv("SE_API_TOKEN", raising=False)  # open (no X-API-Key) in the test
    monkeypatch.setenv("SE_DATA_SOURCE", "mock")       # offline + deterministic
    return TestClient(create_app()), f"sqlite:///{db}"


def _plan(symbol, entry, stop_pct=1.0, t1_pct=2.0):
    ts = _IST.localize(_dt.datetime(2025, 6, 23, 10, 15))
    return TradePlan(symbol=symbol, ts=ts, direction=Direction.LONG, strategy="vwap_ema_adx",
                     entry=entry, stop_loss=entry * (1 - stop_pct / 100), stop_pct=stop_pct,
                     targets=[entry * (1 + t1_pct / 100)], target_pcts=[t1_pct],
                     expected_move_pct=t1_pct, risk_reward=t1_pct / stop_pct,
                     cost_to_break_even_pct=0.1, confidence=72.0)


def _open_and_close(repo, ledger, symbol, entry_fill, exit_fill, qty):
    """Push one full sized round-trip through the ledger so a real closed trade + book state land.

    Timestamps are TODAY (midday IST) so the trade lands in the API's ``today`` bucket regardless
    of when the suite runs (midday keeps the same date in UTC too, so ``date(exit_ts)`` matches)."""
    today = _dt.datetime.now(_IST).date()
    ts = _IST.localize(_dt.datetime(today.year, today.month, today.day, 11, 0))
    plan = _plan(symbol, entry_fill)
    pos = PaperPosition(id=f"{symbol}-1", plan=plan, status=PositionStatus.OPEN,
                        entry_fill=entry_fill, entry_ts=ts)
    pos.qty = qty
    ledger.on_entry(pos, qty)
    pos.status = PositionStatus.CLOSED
    pos.exit_fill = exit_fill
    pos.exit_ts = ts + _dt.timedelta(minutes=20)
    pos.exit_reason = ExitReason.TARGET
    pos.pnl_pct_net = (exit_fill - entry_fill) / entry_fill * 100
    pos.r_multiple = 1.0
    return ledger.on_exit(pos, qty, repo_cost_model())


def repo_cost_model():
    from signal_engine.risk.costs import CostModel
    return CostModel(load_config().risk.costs)


def test_fresh_book_serves_defaults(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    body = r.json()
    # Contract keys present, defaults for an untouched ₹1,00,000 book.
    for key in ("starting_capital", "equity", "cash", "invested", "unrealized_pnl_inr",
                "realized_pnl_today_inr", "realized_pnl_total_inr", "return_total_pct",
                "return_today_pct", "open_positions", "today", "per_strategy", "updated_ts"):
        assert key in body
    assert body["starting_capital"] == 100000.0
    assert body["equity"] == 100000.0
    assert body["cash"] == 100000.0
    assert body["invested"] == 0.0
    assert body["open_positions"] == []
    assert body["today"]["trades"] == 0


def test_equity_endpoint_empty(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.get("/api/portfolio/equity")
    assert r.status_code == 200
    assert r.json() == {"count": 0, "points": []}


def test_portfolio_reflects_a_closed_trade(tmp_path, monkeypatch):
    client, db_url = _client(tmp_path, monkeypatch)
    repo = SignalRepository(db_url)
    ledger = PortfolioLedger(repo, 100000.0)
    money = _open_and_close(repo, ledger, "NTPC", entry_fill=100.0, exit_fill=105.0, qty=200)
    repo.close()

    body = client.get("/api/portfolio").json()
    # equity == starting + realized ₹ (no open positions left); cash fully back.
    assert abs(body["equity"] - (100000.0 + money["pnl_inr"])) < 0.02
    assert abs(body["cash"] - (100000.0 + money["pnl_inr"])) < 0.02
    assert body["invested"] == 0.0
    assert body["today"]["trades"] == 1
    assert body["today"]["wins"] == 1
    assert abs(body["realized_pnl_total_inr"] - round(money["pnl_inr"], 2)) < 0.02
    # per-strategy carries the closed trade under its strategy name.
    strat = {s["strategy"]: s for s in body["per_strategy"]}
    assert "vwap_ema_adx" in strat
    assert strat["vwap_ema_adx"]["trades"] == 1


def test_portfolio_shows_open_position_and_invested(tmp_path, monkeypatch):
    client, db_url = _client(tmp_path, monkeypatch)
    repo = SignalRepository(db_url)
    ledger = PortfolioLedger(repo, 100000.0)
    ts = _IST.localize(_dt.datetime(2025, 6, 23, 10, 15))
    plan = _plan("TCS", 300.0)
    pos = PaperPosition(id="TCS-1", plan=plan, status=PositionStatus.OPEN,
                        entry_fill=300.0, entry_ts=ts)
    pos.qty = 100
    ledger.on_entry(pos, 100)                       # blocks 30,000
    repo.save_open_position(pos, last_price=306.0)  # +6/share mark -> +600 unrealized
    repo.close()

    body = client.get("/api/portfolio").json()
    assert len(body["open_positions"]) == 1
    op = body["open_positions"][0]
    assert op["symbol"] == "TCS" and op["qty"] == 100
    assert abs(op["notional"] - 30000.0) < 1e-6
    assert abs(op["unrealized_pnl_inr"] - 600.0) < 1e-6
    assert abs(body["invested"] - 30000.0) < 1e-6
    assert abs(body["cash"] - 70000.0) < 1e-6
    assert abs(body["equity"] - 100600.0) < 1e-6   # 70,000 cash + 30,000 notional + 600 unrealized


def test_paper_trades_flags_modeled_legacy_rows(tmp_path, monkeypatch):
    client, db_url = _client(tmp_path, monkeypatch)
    repo = SignalRepository(db_url)
    # A legacy closed trade written the pre-portfolio way (no pnl_inr) -> must be flagged modeled.
    ts = _IST.localize(_dt.datetime(2025, 6, 23, 10, 15))
    plan = _plan("OLDCO", 100.0)
    pos = PaperPosition(id="OLDCO-1", plan=plan, status=PositionStatus.CLOSED,
                        entry_fill=100.0, entry_ts=ts, exit_fill=101.0,
                        exit_ts=ts + _dt.timedelta(minutes=10), exit_reason=ExitReason.TARGET,
                        pnl_pct_net=0.9, r_multiple=0.9)
    repo.save_position(pos)  # qty defaults 0, pnl_inr NULL -> legacy
    repo.close()

    body = client.get("/api/paper/trades").json()
    assert body["count"] == 1
    assert body["trades"][0]["modeled"] is True
