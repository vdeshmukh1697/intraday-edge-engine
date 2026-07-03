"""Predictions plain-reason columns (PORTFOLIO §2): DDL, defensive migration, pass-through.

``log_prediction`` opens its own connection per write, so it can hit a DB created before
``reason_plain``/``portfolio_equity``/``notional`` existed — the ALTER-and-retry inside it
must upgrade such a DB on the fly without ever breaking an alert (best-effort contract).
"""

from __future__ import annotations

import sqlite3

import pytest

from signal_engine.alerts.base import Alerter
from signal_engine.alerts.recording import RecordingAlerter
from signal_engine.storage.predictions_log import (
    _META_COLUMNS,
    fetch_predictions,
    log_prediction,
)
from signal_engine.storage.repository import SignalRepository

# The predictions schema as it shipped BEFORE the portfolio columns (for legacy-DB tests).
_LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, kind TEXT, level TEXT,
    symbol TEXT, direction TEXT, strategy TEXT,
    entry REAL, stop_loss REAL, stop_pct REAL,
    target REAL, target_pct REAL,
    risk_reward REAL, expected_move_pct REAL, confidence REAL,
    qty INTEGER, rupee_risk REAL,
    pnl_pct_net REAL, r_multiple REAL, exit_reason TEXT,
    reasons TEXT, extra TEXT, message TEXT, delivered INTEGER, run_id TEXT
)
"""

PLAIN = "RELIANCE is trending up; buying 17 shares (~₹48,450 of the ₹1,00,000 paper book)."


class _Capture(Alerter):
    def __init__(self):
        self.sent = []

    def send(self, message, level="info"):
        self.sent.append((message, level))


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "pred.sqlite3")


def _legacy_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(_LEGACY_DDL)
    conn.execute("INSERT INTO predictions (ts, kind, message) VALUES (?,?,?)",
                 ("2026-07-02T10:00:00+05:30", "entry", "pre-migration row"))
    conn.commit()
    conn.close()


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return fetch_predictions(conn)
    finally:
        conn.close()


def _cols(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    finally:
        conn.close()


def test_meta_columns_include_portfolio_keys():
    assert {"reason_plain", "portfolio_equity", "notional"} <= set(_META_COLUMNS)


def test_fresh_db_ddl_has_columns_and_maps_meta(db_path):
    log_prediction(db_path, message="INFY LONG @~1500", level="signal",
                   meta={"kind": "entry", "symbol": "INFY", "entry": 1500.0,
                         "reason_plain": PLAIN, "portfolio_equity": 100000.0,
                         "notional": 48450.0, "custom": "x"})
    (row,) = _rows(db_path)
    assert row["reason_plain"] == PLAIN
    assert row["portfolio_equity"] == 100000.0
    assert row["notional"] == 48450.0
    assert row["extra"] == {"custom": "x"}  # mapped keys must NOT leak into extra


def test_legacy_db_migrates_inside_log_prediction(db_path):
    """An old-schema DB gains the three columns via the defensive ALTER-and-retry."""
    _legacy_db(db_path)
    assert "reason_plain" not in _cols(db_path)
    log_prediction(db_path, message="NTPC exit", level="info",
                   meta={"kind": "exit", "symbol": "NTPC",
                         "reason_plain": "Sold at the profit target.",
                         "portfolio_equity": 100569.0, "notional": 24000.0})
    assert {"reason_plain", "portfolio_equity", "notional"} <= _cols(db_path)
    rows = _rows(db_path)
    assert len(rows) == 2                       # pre-migration row survived untouched
    newest, legacy = rows                       # newest-first
    assert newest["reason_plain"] == "Sold at the profit target."
    assert newest["portfolio_equity"] == 100569.0 and newest["notional"] == 24000.0
    assert legacy["message"] == "pre-migration row" and legacy["reason_plain"] is None

    # The columns now exist, so the next write takes the fast path (no ALTER needed).
    log_prediction(db_path, message="second", meta={"kind": "scan", "reason_plain": "ok"})
    assert _rows(db_path)[0]["reason_plain"] == "ok"


def test_legacy_db_write_without_new_meta_still_migrates(db_path):
    """The INSERT always names the new columns, so even a meta-less write upgrades the DB."""
    _legacy_db(db_path)
    log_prediction(db_path, message="halt", meta={"kind": "halt"})
    assert {"reason_plain", "portfolio_equity", "notional"} <= _cols(db_path)
    assert _rows(db_path)[0]["reason_plain"] is None


def test_repository_init_db_migrates_legacy_predictions(db_path):
    """repository.init_db ALTER-adds the same columns for DBs opened through the repo."""
    _legacy_db(db_path)
    repo = SignalRepository(f"sqlite:///{db_path}")
    try:
        assert {"reason_plain", "portfolio_equity", "notional"} <= _cols(db_path)
        (row,) = repo.fetch_predictions()
        assert row["reason_plain"] is None      # legacy row: NULL, not dropped
    finally:
        repo.close()


def test_recording_alerter_passes_reason_plain_through(db_path):
    inner = _Capture()
    alerter = RecordingAlerter(inner, db_path)
    alerter.send("RELIANCE LONG @~2850 | qty 17 (~₹48,450)\nWhy: " + PLAIN,
                 level="signal",
                 meta={"kind": "entry", "symbol": "RELIANCE", "qty": 17,
                       "reason_plain": PLAIN, "portfolio_equity": 100000.0,
                       "notional": 48450.0})
    assert len(inner.sent) == 1                 # channel delivery unaffected
    (row,) = _rows(db_path)
    assert row["reason_plain"] == PLAIN and row["qty"] == 17
    assert row["portfolio_equity"] == 100000.0 and row["notional"] == 48450.0
    assert row["delivered"] == 1


def test_logging_stays_best_effort_on_legacy_db(tmp_path):
    """Even the migration path must never raise out of log_prediction (a directory as the
    DB path fails before/independently of the ALTER; the alert flow must survive)."""
    log_prediction(str(tmp_path), message="still fine",
                   meta={"kind": "entry", "reason_plain": PLAIN})  # no raise == pass
