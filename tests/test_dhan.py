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


def test_dhan_supports_no_live_orders():
    # This is a decision-support tool: the order path must never exist.
    assert DhanBroker.supports_live_orders is False
    assert not hasattr(DhanBroker, "place_order")


def test_dhan_run_requires_credentials():
    # The live feed needs auth (and the paid subscription); with no creds it refuses early
    # rather than silently no-op'ing. connect() guards this before any socket is opened.
    b = DhanBroker()
    b.subscribe(["RELIANCE"])
    with pytest.raises(RuntimeError, match="DHAN_CLIENT_ID"):
        b.run()


def test_dhan_run_streams_ticks_end_to_end():
    """Broker-level wiring: subscribe -> feed -> security_id resolved back to symbol -> Tick.

    Uses a fake socket emitting one Quote packet for RELIANCE's security_id (2885). No
    network, no creds beyond a dummy token (token_expiry returns None for non-JWT strings).
    """
    import struct

    from signal_engine.brokers import dhan_ws

    sid = 2885  # RELIANCE in the _CSV fixture
    body = struct.pack("<fhifiiiffff", 2950.0, 5, 1_750_000_000, 2950.0, 42000,
                       0, 0, 0.0, 0.0, 0.0, 0.0)
    frame = struct.pack("<BHBI", dhan_ws.RESP_QUOTE, len(body), 1, sid) + body

    class _FakeWS:
        def __init__(self):
            self._frames = [frame]
            self.sent = []

        def send(self, m):
            self.sent.append(m)

        def recv(self):
            return self._frames.pop(0) if self._frames else b""

        def close(self):
            pass

    b = DhanBroker(client_id="100x", access_token="dummy",
                   instruments=DhanInstrumentMaster.from_csv_text(_CSV),
                   ws_factory=lambda url: _FakeWS())
    got = []
    b.set_tick_callback(got.append)
    b.subscribe(["RELIANCE"])
    b.run()

    assert len(got) == 1
    assert got[0].symbol == "RELIANCE" and got[0].ltp == 2950.0 and got[0].volume == 42000


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
