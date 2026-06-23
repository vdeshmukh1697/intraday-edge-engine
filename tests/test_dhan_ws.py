"""Tests for the Dhan v2 live-feed binary parser + sharded subscribe — fully offline.

Frames are packed by hand with ``struct`` per Dhan's documented little-endian layout, so
the parser is verified byte-for-byte without any network or paid subscription. The recv
loop is driven by a fake socket.
"""

from __future__ import annotations

import json
import struct

from signal_engine.brokers import dhan_ws
from signal_engine.universe.instruments import InstrumentRef

RESOLVE = {101: "RELIANCE", 102: "TCS"}.get
EPOCH = 1_750_000_000  # fixed last-trade-time for deterministic ts


def _header(code, payload_len, security_id, seg=1):
    return struct.pack("<BHBI", code, payload_len, seg, security_id)


def _ticker(security_id, ltp):
    return _header(dhan_ws.RESP_TICKER, 8, security_id) + struct.pack("<fi", ltp, EPOCH)


def _quote(security_id, ltp, vol):
    body = struct.pack("<fhifiiiffff", ltp, 5, EPOCH, ltp, vol, 0, 0, 0.0, 0.0, 0.0, 0.0)
    return _header(dhan_ws.RESP_QUOTE, len(body), security_id) + body


def _full(security_id, ltp, vol, bid, ask):
    head = struct.pack("<fhifiiiiiiffff",
                       ltp, 5, EPOCH, ltp, vol, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0)
    level0 = struct.pack("<iihhff", 100, 200, 3, 4, bid, ask)
    levels = level0 + b"\x00" * (4 * 20)  # 4 more empty depth levels -> 5 total
    body = head + levels
    return _header(dhan_ws.RESP_FULL, len(body), security_id) + body


# --- pure builders ---------------------------------------------------------

def test_build_ws_url_carries_auth_in_query():
    url = dhan_ws.build_ws_url("100123", "JWT.ABC")
    assert url == "wss://api-feed.dhan.co?version=2&token=JWT.ABC&clientId=100123&authType=2"


def test_subscribe_messages_shard_at_100_with_quote_code():
    refs = [InstrumentRef(symbol=f"S{i}", security_id=str(i), exchange_segment="NSE_EQ")
            for i in range(250)]
    msgs = dhan_ws.subscribe_messages(refs, mode=dhan_ws.QUOTE)
    assert [m["InstrumentCount"] for m in msgs] == [100, 100, 50]
    assert all(m["RequestCode"] == 17 for m in msgs)  # 17 == subscribe Quote
    first = msgs[0]["InstrumentList"][0]
    assert first == {"ExchangeSegment": "NSE_EQ", "SecurityId": "0"}


def test_subscribe_mode_selects_request_code():
    refs = [InstrumentRef("R", "101", "NSE_EQ")]
    assert dhan_ws.subscribe_messages(refs, "ticker")[0]["RequestCode"] == 15
    assert dhan_ws.subscribe_messages(refs, "full")[0]["RequestCode"] == 21


# --- binary parsing --------------------------------------------------------

def test_parse_ticker_packet():
    ticks = list(dhan_ws.iter_ticks(_ticker(101, 2950.5), RESOLVE))
    assert len(ticks) == 1
    t = ticks[0]
    assert t.symbol == "RELIANCE" and round(t.ltp, 1) == 2950.5
    assert t.volume == 0 and t.ts.tzinfo is not None


def test_parse_quote_packet_has_volume():
    (t,) = list(dhan_ws.iter_ticks(_quote(102, 3800.0, 123456), RESOLVE))
    assert t.symbol == "TCS" and t.ltp == 3800.0 and t.volume == 123456


def test_parse_full_packet_has_best_bid_ask():
    (t,) = list(dhan_ws.iter_ticks(_full(101, 2900.0, 9999, bid=2899.5, ask=2900.5), RESOLVE))
    assert t.ltp == 2900.0 and t.volume == 9999
    assert t.bid == 2899.5 and t.ask == 2900.5


def test_iter_ticks_walks_multiple_concatenated_packets():
    frame = _quote(101, 2900.0, 10) + _quote(102, 3800.0, 20) + _ticker(101, 2901.0)
    ticks = list(dhan_ws.iter_ticks(frame, RESOLVE))
    assert [t.symbol for t in ticks] == ["RELIANCE", "TCS", "RELIANCE"]


def test_unknown_security_id_is_skipped():
    assert list(dhan_ws.iter_ticks(_quote(999, 1.0, 1), RESOLVE)) == []


def test_truncated_trailer_does_not_overrun():
    frame = _quote(101, 2900.0, 10) + b"\x04\x10"  # valid packet + 2 junk bytes
    ticks = list(dhan_ws.iter_ticks(frame, RESOLVE))
    assert len(ticks) == 1  # the good packet parsed; trailer ignored, no crash


def test_disconnect_packet_stops_the_walk():
    frame = _quote(101, 2900.0, 10) + _header(dhan_ws.RESP_DISCONNECT, 0, 0)
    assert len(list(dhan_ws.iter_ticks(frame, RESOLVE))) == 1


# --- recv loop (fake transport) --------------------------------------------

class _FakeWS:
    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []
        self.closed = False

    def send(self, m):
        self.sent.append(m)

    def recv(self):
        return self._frames.pop(0) if self._frames else b""  # empty -> feed ends

    def close(self):
        self.closed = True


def test_run_feed_subscribes_then_streams():
    fake = _FakeWS([_quote(101, 2900.0, 10), _full(102, 3800.0, 20, 3799.0, 3801.0)])
    refs = [InstrumentRef("RELIANCE", "101", "NSE_EQ"),
            InstrumentRef("TCS", "102", "NSE_EQ")]
    got = []
    dhan_ws.run_feed(
        dhan_ws.build_ws_url("c", "t"),
        dhan_ws.subscribe_messages(refs, dhan_ws.QUOTE),
        resolve=RESOLVE, on_tick=got.append,
        ws_factory=lambda url: fake,
    )
    # subscribe message was sent as JSON with the Quote request code
    assert json.loads(fake.sent[0])["RequestCode"] == 17
    assert [t.symbol for t in got] == ["RELIANCE", "TCS"]
    assert fake.closed


def test_run_feed_reconnects_after_transport_error():
    calls = {"n": 0}

    def flaky_factory(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("dropped")
        return _FakeWS([_quote(101, 2900.0, 10)])

    got = []
    dhan_ws.run_feed(
        "wss://x", [{"RequestCode": 17, "InstrumentCount": 0, "InstrumentList": []}],
        resolve=RESOLVE, on_tick=got.append, ws_factory=flaky_factory,
        sleep_fn=lambda *_: None,  # no real backoff wait in tests
    )
    assert calls["n"] == 2 and [t.symbol for t in got] == ["RELIANCE"]


def test_run_feed_stop_callable_short_circuits():
    fake = _FakeWS([_quote(101, 2900.0, 10)])
    got = []
    dhan_ws.run_feed("wss://x", [], resolve=RESOLVE, on_tick=got.append,
                     ws_factory=lambda url: fake, stop=lambda: True)
    assert got == []  # stopped before consuming
