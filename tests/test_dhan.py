"""Tests for the Dhan instrument master + adapter normalization (DI; no live SDK/creds)."""

import pytest

from signal_engine.brokers.dhan import DhanBroker
from signal_engine.universe.instruments import DhanInstrumentMaster

_CSV = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_TRADING_SYMBOL,SEM_SMST_SECURITY_ID\n"
    "NSE,E,RELIANCE,2885\n"
    "NSE,E,INFY,1594\n"
    "BSE,E,RELIANCE,500325\n"        # other exchange -> excluded for NSE load
    "NSE,D,NIFTY-FUT,99999\n"        # derivatives segment -> excluded
    "NSE,E,,123\n"                   # missing symbol -> skipped
)


def test_instrument_master_parses_nse_equity():
    m = DhanInstrumentMaster.from_csv_text(_CSV)
    assert m.security_id("RELIANCE") == "2885"
    assert m.security_id("INFY") == "1594"
    assert m.ref("RELIANCE").exchange_segment == "NSE_EQ"
    assert m.security_id("NIFTY-FUT") is None   # derivatives excluded
    assert "RELIANCE" in m.symbols() and len(m) == 2
    assert m.security_id("reliance") == "2885"  # case-insensitive


def test_instrument_master_unknown_symbol():
    m = DhanInstrumentMaster.from_csv_text(_CSV)
    assert m.security_id("TATASTEEL") is None
    assert m.ref("TATASTEEL") is None


def test_dhan_connect_requires_credentials():
    b = DhanBroker(client_id=None, access_token=None)
    with pytest.raises(RuntimeError):
        b.connect()


def test_dhan_supports_no_live_orders_and_no_run():
    b = DhanBroker()
    assert b.supports_live_orders is False
    # live feed is intentionally not yet verified -> guarded
    b.subscribe(["RELIANCE"])
    with pytest.raises(NotImplementedError):
        b.run()


def test_normalize_historical_to_bars():
    resp = {
        "data": {
            "open": [100.0, 101.0], "high": [101.5, 102.0], "low": [99.5, 100.5],
            "close": [101.0, 101.5], "volume": [1000, 1200],
            "timestamp": [1750650300, 1750650360],  # epoch seconds
        }
    }
    bars = DhanBroker._normalize_historical("RELIANCE", resp)
    assert len(bars) == 2
    assert bars[0].symbol == "RELIANCE"
    assert bars[0].open == 100.0 and bars[0].close == 101.0 and bars[0].volume == 1000
    assert bars[0].timeframe == "1m"
    assert str(bars[0].ts.tzinfo) == "Asia/Kolkata"
    assert bars[1].high == 102.0


def test_normalize_handles_unwrapped_and_empty():
    # no "data" wrapper
    bars = DhanBroker._normalize_historical("X", {"open": [10.0], "high": [11.0],
                                                  "low": [9.0], "close": [10.5],
                                                  "volume": [5], "timestamp": [1750650300]})
    assert len(bars) == 1 and bars[0].close == 10.5
    assert DhanBroker._normalize_historical("X", {}) == []
    assert DhanBroker._normalize_historical("X", None) == []


def test_historical_with_injected_post():
    """End-to-end historical() using an injected http_post stub + instrument master (no network)."""
    import base64
    import json as _json

    m = DhanInstrumentMaster.from_csv_text(_CSV)

    def fake_post(url, body, headers, timeout=30.0):
        assert body.get("securityId") == "2885"
        assert body.get("exchangeSegment") == "NSE_EQ"
        return 200, {"data": {"open": [100.0], "high": [101.0], "low": [99.0],
                              "close": [100.5], "volume": [10], "timestamp": [1750650300]}}

    # JWT with exp far in the future (2099-01-01)
    payload = base64.urlsafe_b64encode(
        _json.dumps({"exp": 4070908800, "iss": "dhan"}).encode()
    ).decode().rstrip("=")
    future_token = f"eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.{payload}.sig"

    b = DhanBroker(client_id="test123", access_token=future_token, instruments=m, http_post=fake_post)
    bars = b.historical("RELIANCE", "1m", None, None)
    assert len(bars) == 1 and bars[0].close == 100.5
