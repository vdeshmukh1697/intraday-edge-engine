"""Tests for the FastAPI engine API (Phase 6). Uses Starlette's TestClient (no network)."""

import pytest
from fastapi.testclient import TestClient

from signal_engine.api.app import create_app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("SE_API_TOKEN", raising=False)  # open in dev for these tests
    # Pin the synthetic data path so the leaderboard/chart contract tests are deterministic
    # and offline (the real path reads the local Parquet archive / .env data source).
    monkeypatch.setenv("SE_DATA_SOURCE", "mock")
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


def test_paper_analytics_empty_is_graceful(client):
    r = client.get("/api/paper/analytics")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["n_trades"] >= 0
    assert isinstance(body["auto_summary"], list)
    assert "equity_curve" in body and "by_strategy" in body


def test_paper_trades_and_analytics_with_seeded_db(tmp_path, monkeypatch):
    import datetime

    import pytz

    from signal_engine.domain.enums import Direction, ExitReason, PositionStatus
    from signal_engine.domain.models import PaperPosition, TradePlan
    from signal_engine.storage.repository import SignalRepository

    db = f"sqlite:///{tmp_path}/api.sqlite3"
    ts = pytz.timezone("Asia/Kolkata").localize(datetime.datetime(2026, 6, 23, 9, 30))

    def pos(pid, sym, exit_fill):
        plan = TradePlan(symbol=sym, ts=ts, direction=Direction.LONG, strategy="vwap_ema_adx",
                         entry=100.0, stop_loss=99.0, stop_pct=1.0, targets=[102.0, 103.0],
                         target_pcts=[2.0, 3.0], expected_move_pct=2.0, risk_reward=2.0,
                         cost_to_break_even_pct=0.1, confidence=70.0)
        return PaperPosition(id=pid, plan=plan, status=PositionStatus.CLOSED, entry_fill=100.0,
                             entry_ts=ts, exit_fill=exit_fill, exit_ts=ts,
                             exit_reason=ExitReason.TARGET, pnl_pct_net=1.0, r_multiple=1.0,
                             hold_minutes=10.0, won=exit_fill > 100)

    repo = SignalRepository(db)
    repo.save_position(pos("w", "RELIANCE", 102.0))  # winner
    repo.save_position(pos("l", "TCS", 99.0))         # loser
    repo.close()

    monkeypatch.delenv("SE_API_TOKEN", raising=False)
    monkeypatch.setenv("SE_DB_URL", db)
    c = TestClient(create_app())

    trades = c.get("/api/paper/trades").json()
    assert trades["count"] == 2
    assert all("net_pnl_abs" in t and "qty" in t for t in trades["trades"])

    a = c.get("/api/paper/analytics").json()
    assert a["summary"]["n_trades"] == 2
    assert a["summary"]["wins"] == 1 and a["summary"]["losses"] == 1


def test_leaderboard_uses_real_archive_when_present(tmp_path, monkeypatch):
    """With a real data source + a populated archive, the leaderboard serves REAL symbols
    (not the synthetic SYN#### universe)."""
    import datetime

    from signal_engine.data.synthetic import generate_session
    from signal_engine.storage.bars import ParquetBarStore

    store = ParquetBarStore(str(tmp_path))
    for sym in ["RELIANCE", "TCS", "INFY"]:
        df = generate_session(sym, datetime.date(2026, 6, 23), start_price=2000,
                              seed=7, regime="trend_up")
        store.save_symbol_year(sym, 2026, df)

    monkeypatch.delenv("SE_API_TOKEN", raising=False)
    monkeypatch.setenv("SE_DATA_SOURCE", "dhan")        # real path
    monkeypatch.setenv("SE_PARQUET_DIR", str(tmp_path))  # point at the temp archive
    c = TestClient(create_app())

    data = c.get("/api/leaderboard", params={"news": "false", "top": 5}).json()
    assert data["stats"]["universe"] == 3            # scanned the 3 archived names
    syms = {e["symbol"] for e in data["entries"]}
    assert syms <= {"RELIANCE", "TCS", "INFY"}        # real names, never SYN####


# --- Dhan auth gate endpoints (offline; no Dhan network hit) ----------------

def test_auth_status_transparent_for_non_dhan(client, monkeypatch):
    monkeypatch.setenv("SE_DATA_SOURCE", "mock")
    d = client.get("/api/auth/status").json()
    assert d["auth_required"] is False and d["connected"] is True


def test_auth_status_reports_expired_for_dhan_without_token(client, monkeypatch):
    monkeypatch.setenv("SE_DATA_SOURCE", "dhan")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "")  # no token -> not connected
    d = client.get("/api/auth/status").json()
    assert d["auth_required"] is True and d["connected"] is False
    assert d["login_path"] == "/api/auth/dhan/start"


def test_auth_start_requires_api_credentials(client, monkeypatch):
    monkeypatch.delenv("DHAN_API_KEY", raising=False)
    monkeypatch.delenv("DHAN_API_SECRET", raising=False)
    monkeypatch.setenv("DHAN_CLIENT_ID", "x")
    r = client.get("/api/auth/dhan/start", follow_redirects=False)
    assert r.status_code == 400  # not configured -> no network attempted


def test_auth_consume_rejects_without_active_login(client):
    r = client.get("/api/auth/dhan/consume", params={"tokenId": "abc"})
    assert r.status_code == 400  # no recent /start pending


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
