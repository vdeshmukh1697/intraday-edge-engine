"""Tests for SignalRepository run_id tagging + safe migration (PLAN §3 "also")."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytz

from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
from signal_engine.domain.models import PaperPosition, TradePlan
from signal_engine.storage.repository import SignalRepository, _default_run_id

IST = pytz.timezone("Asia/Kolkata")


def _plan(symbol="RELIANCE"):
    ts = IST.localize(datetime(2026, 6, 24, 9, 30))
    return TradePlan(
        symbol=symbol, ts=ts, direction=Direction.LONG, strategy="vwap_ema",
        entry=2900.0, stop_loss=2880.0, stop_pct=0.69, targets=[2940.0], target_pcts=[1.38],
        expected_move_pct=1.38, risk_reward=2.0, cost_to_break_even_pct=0.14, confidence=70.0,
        reasons=["t"], time_validity=None,
    )


def _position(pos_id="p1", symbol="RELIANCE"):
    ts = IST.localize(datetime(2026, 6, 24, 9, 31))
    return PaperPosition(
        id=pos_id, plan=_plan(symbol), status=PositionStatus.CLOSED,
        entry_fill=2900.0, entry_ts=ts, exit_fill=2940.0, exit_ts=ts,
        exit_reason=ExitReason.TARGET, pnl_pct_net=1.2, r_multiple=2.0,
        hold_minutes=15.0, won=True,
    )


def test_default_run_id_is_stable_within_process():
    a, b = _default_run_id(), _default_run_id()
    assert a == b  # same epoch-second + pid for back-to-back calls
    assert "-" in a


def test_run_id_column_added_to_both_tables():
    repo = SignalRepository("sqlite:///:memory:")
    cols_plans = {r[1] for r in repo.conn.execute("PRAGMA table_info(trade_plans)")}
    cols_trades = {r[1] for r in repo.conn.execute("PRAGMA table_info(paper_trades)")}
    assert "run_id" in cols_plans
    assert "run_id" in cols_trades
    repo.close()


def test_save_plan_tags_default_run_id():
    repo = SignalRepository("sqlite:///:memory:", run_id="RUN-A")
    repo.save_plan(_plan())
    (row,) = repo.fetch_plans()
    assert row["run_id"] == "RUN-A"
    repo.close()


def test_save_plan_per_call_run_id_overrides_default():
    repo = SignalRepository("sqlite:///:memory:", run_id="RUN-A")
    repo.save_plan(_plan("RELIANCE"))
    repo.save_plan(_plan("TCS"), run_id="RUN-B")
    by_symbol = {r["symbol"]: r["run_id"] for r in repo.fetch_plans()}
    assert by_symbol == {"RELIANCE": "RUN-A", "TCS": "RUN-B"}
    repo.close()


def test_save_position_tags_run_id():
    repo = SignalRepository("sqlite:///:memory:", run_id="RUN-A")
    repo.save_position(_position(), run_id="RUN-X")
    (row,) = repo.fetch_trades()
    assert row["run_id"] == "RUN-X"
    repo.close()


def test_default_run_id_used_when_none_passed():
    repo = SignalRepository("sqlite:///:memory:")
    repo.save_plan(_plan())
    (row,) = repo.fetch_plans()
    assert row["run_id"] == repo.run_id and row["run_id"]
    repo.close()


def test_migration_safe_on_preexisting_db_without_run_id(tmp_path):
    """An old DB created before run_id existed must gain the column without data loss."""
    db_path = tmp_path / "old.sqlite3"
    # Build a legacy trade_plans schema (no run_id column) with one row.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE trade_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, ts TEXT, direction TEXT, strategy TEXT,
            entry REAL, stop_loss REAL, stop_pct REAL,
            targets TEXT, target_pcts TEXT, expected_move_pct REAL,
            risk_reward REAL, cost_to_break_even_pct REAL, confidence REAL,
            reasons TEXT, time_validity TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO trade_plans (symbol, ts, direction) VALUES (?,?,?)",
        ("LEGACY", "2026-06-23T09:30:00+05:30", "LONG"),
    )
    conn.commit()
    conn.close()

    # Reopening through the repo runs init_db -> migration adds run_id.
    repo = SignalRepository(f"sqlite:///{db_path}", run_id="RUN-NEW")
    cols = {r[1] for r in repo.conn.execute("PRAGMA table_info(trade_plans)")}
    assert "run_id" in cols
    rows = repo.fetch_plans()
    legacy = [r for r in rows if r["symbol"] == "LEGACY"]
    assert len(legacy) == 1 and legacy[0]["run_id"] is None  # pre-existing row untouched
    # New writes get tagged.
    repo.save_plan(_plan("RELIANCE"))
    tagged = [r for r in repo.fetch_plans() if r["symbol"] == "RELIANCE"]
    assert tagged[0]["run_id"] == "RUN-NEW"
    repo.close()


def test_latest_ticks_upsert_and_fetch():
    """Latest-tick snapshot round-trips (one row per symbol, latest wins) and carries volume."""
    repo = SignalRepository("sqlite:///:memory:", run_id="RUN-1")
    repo.upsert_latest_ticks([
        {"symbol": "RELIANCE", "ltp": 2900.0, "volume": 1000, "tick_ts": "2026-07-02T09:30:00+05:30"},
        {"symbol": "TCS", "ltp": 3800.0, "volume": 500, "tick_ts": "2026-07-02T09:30:00+05:30"},
    ])
    # A later tick for RELIANCE replaces the prior row (PRIMARY KEY on symbol).
    repo.upsert_latest_ticks([
        {"symbol": "RELIANCE", "ltp": 2915.5, "volume": 1800, "tick_ts": "2026-07-02T09:31:00+05:30"},
    ])
    by_sym = {r["symbol"]: r for r in repo.fetch_latest_ticks()}
    assert set(by_sym) == {"RELIANCE", "TCS"}
    assert by_sym["RELIANCE"]["ltp"] == 2915.5
    assert by_sym["RELIANCE"]["volume"] == 1800  # cumulative day volume advanced
    assert by_sym["RELIANCE"]["run_id"] == "RUN-1"
    # Symbol filter returns only the requested names.
    only = repo.fetch_latest_ticks(["TCS"])
    assert len(only) == 1 and only[0]["symbol"] == "TCS"
    repo.upsert_latest_ticks([])  # empty batch is a no-op, not an error
    assert len(repo.fetch_latest_ticks()) == 2
    repo.close()


def test_latest_ticks_table_added_on_preexisting_db(tmp_path):
    """A DB created before latest_ticks existed gains the table on reopen (CREATE IF NOT EXISTS)."""
    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE trade_plans (id INTEGER PRIMARY KEY, symbol TEXT)")
    conn.commit()
    conn.close()
    repo = SignalRepository(f"sqlite:///{db_path}")
    repo.upsert_latest_ticks([{"symbol": "INFY", "ltp": 1500.0, "volume": 42}])
    assert repo.fetch_latest_ticks()[0]["symbol"] == "INFY"
    repo.close()
