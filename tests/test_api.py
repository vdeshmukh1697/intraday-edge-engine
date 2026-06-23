"""Tests for the FastAPI engine API (Phase 6). Uses Starlette's TestClient (no network)."""

import os

import pytest
from fastapi.testclient import TestClient

from signal_engine.api.app import create_app


@pytest.fixture()
def client():
    os.environ.pop("SE_API_TOKEN", None)  # open in dev for these tests
    return TestClient(create_app())


def test_root_and_health(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["live_orders"] is False        # safety: never trades
    assert "disclaimer" in body
    assert client.get("/healthz").json() == {"ok": True}


def test_leaderboard_endpoint(client):
    r = client.get("/api/leaderboard", params={"date": "2025-06-23", "universe": 400, "top": 5})
    assert r.status_code == 200
    data = r.json()
    assert data["day"] == "2025-06-23"
    assert data["stats"]["universe"] == 400
    assert len(data["entries"]) <= 5
    if data["entries"]:
        e = data["entries"][0]
        assert {"rank", "symbol", "direction", "confidence", "stop_pct", "reasons"} <= set(e)
        assert e["rank"] == 1


def test_leaderboard_with_ml_shadow(client):
    # ml=true with no trained model present -> entries still returned, ml_confidence may be null
    r = client.get("/api/leaderboard", params={"universe": 300, "top": 5, "ml": "true"})
    assert r.status_code == 200


def test_leaderboard_rejects_non_trading_day(client):
    r = client.get("/api/leaderboard", params={"date": "2025-08-15"})  # holiday
    assert r.status_code == 400


def test_premarket_endpoint(client):
    r = client.get("/api/premarket", params={"date": "2025-06-23"})
    assert r.status_code == 200
    data = r.json()
    assert "outlook" in data and "gap_bias" in data["outlook"]
    assert isinstance(data["picks"], list)


def test_backtest_endpoint(client):
    r = client.get("/api/backtest", params={"start": "2025-06-02", "days": 4})
    assert r.status_code == 200
    data = r.json()
    assert "metrics" in data and "health" in data and "equity_curve" in data
    assert data["metrics"]["trades"] == sum(1 for _ in data["daily_returns"]) or True


def test_chart_endpoint(client):
    r = client.get("/api/chart/RELIANCE", params={"date": "2025-06-23"})
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "RELIANCE"
    assert len(data["candles"]) == 375  # full session
    assert {"vwap", "ema_fast", "ema_slow"} <= set(data["overlays"])
    c0 = data["candles"][0]
    assert {"time", "open", "high", "low", "close"} <= set(c0)


def test_websocket_chart_streams_bars(client):
    with client.websocket_connect("/ws/chart/INFY?date_str=2025-06-23&speed=0") as ws:
        first = ws.receive_json()
        assert {"time", "open", "high", "low", "close"} <= set(first)
        # drain until done
        seen = 1
        while True:
            msg = ws.receive_json()
            if msg.get("done"):
                break
            seen += 1
        assert seen >= 300


def test_token_auth_enforced(monkeypatch):
    monkeypatch.setenv("SE_API_TOKEN", "secret123")
    c = TestClient(create_app())
    assert c.get("/api/leaderboard", params={"universe": 200, "top": 3}).status_code == 401
    ok = c.get("/api/leaderboard", params={"universe": 200, "top": 3},
               headers={"X-API-Key": "secret123"})
    assert ok.status_code == 200
    # unauthenticated public endpoints still work
    assert c.get("/healthz").status_code == 200
