"""Predictions log: RecordingAlerter persistence + /api/predictions endpoint."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from signal_engine.alerts import send_alert
from signal_engine.alerts.base import Alerter
from signal_engine.alerts.recording import RecordingAlerter
from signal_engine.api.app import create_app
from signal_engine.storage.predictions_log import fetch_predictions, log_prediction
from signal_engine.storage.repository import SignalRepository


class _Capture(Alerter):
    def __init__(self):
        self.sent = []

    def send(self, message, level="info"):
        self.sent.append((message, level))


class _Boom(Alerter):
    def send(self, message, level="info"):
        raise RuntimeError("channel down")


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "pred.sqlite3")


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return fetch_predictions(conn)
    finally:
        conn.close()


def test_recording_alerter_persists_and_delegates(db_path):
    inner = _Capture()
    alerter = RecordingAlerter(inner, db_path)
    alerter.send("RELIANCE LONG @~100", level="signal",
                 meta={"kind": "entry", "symbol": "RELIANCE", "direction": "LONG",
                       "entry": 100.0, "stop_loss": 99.0, "target": 102.0,
                       "confidence": 70.0, "qty": 10, "reasons": ["above VWAP"],
                       "custom_field": "x"})
    assert inner.sent == [("RELIANCE LONG @~100", "signal")]
    rows = _rows(db_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "entry" and r["symbol"] == "RELIANCE"
    assert r["entry"] == 100.0 and r["target"] == 102.0 and r["qty"] == 10
    assert r["reasons"] == ["above VWAP"]
    assert r["extra"] == {"custom_field": "x"}   # unmapped meta keys land in extra
    assert r["delivered"] == 1 and r["message"] == "RELIANCE LONG @~100"
    assert r["ts"] and r["run_id"]


def test_recording_alerter_records_channel_failure(db_path):
    RecordingAlerter(_Boom(), db_path).send("hello", level="warning")
    rows = _rows(db_path)
    assert len(rows) == 1
    assert rows[0]["delivered"] == 0 and rows[0]["kind"] == "other"


def test_logging_failure_never_blocks_the_alert(tmp_path):
    inner = _Capture()
    # A directory as the DB path makes sqlite fail — the send must still go through.
    alerter = RecordingAlerter(inner, str(tmp_path))
    alerter.send("still delivered", level="info", meta={"kind": "entry"})
    assert inner.sent == [("still delivered", "info")]


def test_send_alert_falls_back_for_plain_alerters(db_path):
    plain = _Capture()
    send_alert(plain, "no meta support", level="signal", meta={"kind": "entry"})
    assert plain.sent == [("no meta support", "signal")]
    recording = RecordingAlerter(_Capture(), db_path)
    send_alert(recording, "with meta", level="signal", meta={"kind": "scan"})
    assert _rows(db_path)[0]["kind"] == "scan"


def test_fetch_filters_and_since_id(db_path):
    for i, (kind, sym) in enumerate([("entry", "TCS"), ("exit", "TCS"), ("scan", "INFY")]):
        log_prediction(db_path, message=f"m{i}", level="info",
                       meta={"kind": kind, "symbol": sym})
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        assert len(fetch_predictions(conn)) == 3
        assert [r["kind"] for r in fetch_predictions(conn)] == ["scan", "exit", "entry"]
        assert len(fetch_predictions(conn, kind="entry")) == 1
        assert len(fetch_predictions(conn, symbol="tcs")) == 2   # case-insensitive
        newest = fetch_predictions(conn, limit=1)[0]
        assert fetch_predictions(conn, since_id=newest["id"]) == []
        assert len(fetch_predictions(conn, since_id=0)) == 3
    finally:
        conn.close()


def test_repository_creates_table_and_fetches(tmp_path):
    db = f"sqlite:///{tmp_path}/repo.sqlite3"
    repo = SignalRepository(db)   # init_db alone must create the predictions table
    try:
        assert repo.fetch_predictions() == []
    finally:
        repo.close()


def test_api_predictions_endpoint(tmp_path, monkeypatch):
    db_file = str(tmp_path / "api.sqlite3")
    log_prediction(db_file, message="INFY LONG @~1500", level="signal",
                   meta={"kind": "entry", "symbol": "INFY", "direction": "LONG",
                         "entry": 1500.0, "confidence": 80.0})
    log_prediction(db_file, message="INFY CLOSED TARGET_HIT net +1.20% R +1.00",
                   level="info",
                   meta={"kind": "exit", "symbol": "INFY", "pnl_pct_net": 1.2,
                         "r_multiple": 1.0, "exit_reason": "TARGET_HIT"})
    monkeypatch.delenv("SE_API_TOKEN", raising=False)
    monkeypatch.setenv("SE_DATA_SOURCE", "mock")
    monkeypatch.setenv("SE_DB_URL", f"sqlite:///{db_file}")
    c = TestClient(create_app())

    body = c.get("/api/predictions").json()
    assert body["count"] == 2
    assert body["predictions"][0]["kind"] == "exit"          # newest first
    assert body["predictions"][1]["entry"] == 1500.0

    only_entries = c.get("/api/predictions", params={"kind": "entry"}).json()
    assert only_entries["count"] == 1

    since = c.get("/api/predictions",
                  params={"since_id": body["predictions"][0]["id"]}).json()
    assert since["count"] == 0
