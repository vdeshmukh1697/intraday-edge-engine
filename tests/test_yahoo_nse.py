"""Tests for YahooNSEBroker (no network — all yfinance calls injected via DI)."""

import pandas as pd
import pytz

from signal_engine.brokers.yahoo_nse import YahooNSEBroker, _nse_ticker

IST = pytz.timezone("Asia/Kolkata")


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a yfinance-style DataFrame with tz-aware UTC index."""
    idx = pd.to_datetime([r["ts"] for r in rows], utc=True)
    df = pd.DataFrame(
        {
            "Open": [r["o"] for r in rows],
            "High": [r["h"] for r in rows],
            "Low": [r["l"] for r in rows],
            "Close": [r["c"] for r in rows],
            "Volume": [r["v"] for r in rows],
        },
        index=idx,
    )
    return df


_ROWS = [
    {"ts": "2026-06-23 03:45:00", "o": 1300.0, "h": 1305.0, "l": 1299.0, "c": 1303.0, "v": 10000},
    {"ts": "2026-06-23 03:46:00", "o": 1303.0, "h": 1310.0, "l": 1302.0, "c": 1308.0, "v": 12000},
]


def make_broker(rows=None) -> YahooNSEBroker:
    data = _make_df(rows or _ROWS)

    def fake_fetch(ticker: str, interval: str):
        assert ticker == "RELIANCE.NS"
        return data

    return YahooNSEBroker(yf_fetch=fake_fetch)


# --- ticker helper ---

def test_nse_ticker_appends_ns():
    assert _nse_ticker("RELIANCE") == "RELIANCE.NS"
    assert _nse_ticker("reliance") == "RELIANCE.NS"
    assert _nse_ticker("RELIANCE.NS") == "RELIANCE.NS"


# --- historical ---

def test_historical_returns_bars():
    b = make_broker()
    bars = b.historical("RELIANCE", "1m", None, None)
    assert len(bars) == 2
    assert bars[0].symbol == "RELIANCE"
    assert bars[0].open == 1300.0
    assert bars[0].close == 1303.0
    assert bars[0].volume == 10000
    assert bars[0].timeframe == "1m"
    assert str(bars[0].ts.tzinfo) == "Asia/Kolkata"


def test_historical_5m_interval_passes_through():
    fetched = {}

    def fake_fetch(ticker, interval):
        fetched["interval"] = interval
        return _make_df(_ROWS)

    b = YahooNSEBroker(yf_fetch=fake_fetch)
    b.historical("RELIANCE", "5m", None, None)
    assert fetched["interval"] == "5m"


def test_historical_empty_df():
    def fake_fetch(ticker, interval):
        return pd.DataFrame()

    b = YahooNSEBroker(yf_fetch=fake_fetch)
    assert b.historical("RELIANCE", "1m", None, None) == []


def test_historical_none_df():
    def fake_fetch(ticker, interval):
        return None

    b = YahooNSEBroker(yf_fetch=fake_fetch)
    assert b.historical("RELIANCE", "1m", None, None) == []


# --- quote ---

def test_quote_returns_tick():
    b = make_broker()
    ticks = b.quote(["RELIANCE"])
    assert "RELIANCE" in ticks
    t = ticks["RELIANCE"]
    assert t.ltp == 1308.0  # last close
    assert t.volume == 12000
    assert str(t.ts.tzinfo) == "Asia/Kolkata"


def test_quote_skips_failed_symbol():
    def fake_fetch(ticker, interval):
        if ticker == "BAD.NS":
            raise ValueError("bad ticker")
        return _make_df(_ROWS)

    b = YahooNSEBroker(yf_fetch=fake_fetch)
    ticks = b.quote(["BAD", "RELIANCE"])
    assert "BAD" not in ticks
    assert "RELIANCE" in ticks


# --- lifecycle ---

def test_connect_and_disconnect_are_noops():
    b = YahooNSEBroker()
    b.connect()   # no exception
    b.disconnect()


def test_supports_no_live_orders():
    assert YahooNSEBroker.supports_live_orders is False


def test_run_replays_today_bars():
    """run() should invoke the callback for each of today's bars."""
    import datetime

    import pytz

    # run() keeps bars whose IST date == today's IST date. Stamp rows off IST "today"
    # (03:45 UTC == 09:15 IST, same calendar day) so the test is robust around the UTC/IST
    # midnight boundary (IST is +5:30, so utcnow()'s date lags IST's between 00:00–05:30 IST).
    today_ist = datetime.datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
    rows = [
        {"ts": f"{today_ist} 03:45:00", "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 500},
        {"ts": f"{today_ist} 03:46:00", "o": 100.5, "h": 102.0, "l": 100.0, "c": 101.0, "v": 600},
    ]
    received = []
    b = make_broker(rows)
    b.subscribe(["RELIANCE"])
    b.set_tick_callback(received.append)
    b.run()
    assert len(received) == 2
    assert received[0].ltp == 100.5
    assert received[1].ltp == 101.0


def test_run_noop_without_callback():
    b = make_broker()
    b.subscribe(["RELIANCE"])
    b.run()  # no crash; no callback set
