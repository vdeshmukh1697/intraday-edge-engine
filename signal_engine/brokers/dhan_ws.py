"""Dhan v2 live market-feed WebSocket — binary packet parsing + sharded subscribe.

MARKET-DATA ONLY. This module turns Dhan's binary feed into ``Tick`` objects for the
signal pipeline; it can never place an order.

Everything load-bearing here is pulled from Dhan's official v2 docs (verified June 2026):

  * Connect:  wss://api-feed.dhan.co?version=2&token=<jwt>&clientId=<id>&authType=2
              (auth is in the query string — no post-connect auth message)
  * Subscribe RequestCode (per feed mode): 15 Ticker / 17 Quote / 21 Full
              (unsubscribe: 16 / 18 / 22; disconnect: 12). Max 100 instruments per message.
  * Response codes: 2 Ticker, 4 Quote, 5 OI, 6 PrevClose, 8 Full, 50 Disconnect.
  * Header (8 bytes, LITTLE-ENDIAN): uint8 code, uint16 msg-length, uint8 exch-segment,
              uint32 security-id.
  * Heartbeat: server pings every ~10s; client must respond within 40s (websocket-client
              answers pings automatically inside recv()).

Design for the no-creds / pre-subscription state: the binary parsers and the subscribe-
message builder are PURE FUNCTIONS, unit-tested offline by packing bytes with ``struct``.
Only :func:`run_feed` touches the network, and its transport is injectable (``ws_factory``)
so tests drive it with a fake socket. The few residual doc ambiguities are flagged inline
with ``VERIFY BEFORE LIVE`` so nothing wrong ships silently.

Refs: https://dhanhq.co/docs/v2/live-market-feed/  ·  https://dhanhq.co/docs/v2/annexure/
"""

from __future__ import annotations

import json
import struct
import time as _time
from datetime import datetime
from typing import Callable, Dict, Iterator, List, Optional

import pytz

from signal_engine.domain.models import Tick
from signal_engine.obs.logging_setup import get_logger
from signal_engine.universe.instruments import InstrumentRef

IST = pytz.timezone("Asia/Kolkata")
log = get_logger(__name__)

DEFAULT_WS_BASE = "wss://api-feed.dhan.co"
MAX_INSTRUMENTS_PER_MSG = 100  # Dhan hard cap per subscribe JSON message

# Feed modes -> subscribe RequestCode (Dhan v2 Annexure "Feed Request Code" table).
TICKER, QUOTE, FULL = "ticker", "quote", "full"
SUBSCRIBE_CODE: Dict[str, int] = {TICKER: 15, QUOTE: 17, FULL: 21}
UNSUBSCRIBE_CODE: Dict[str, int] = {TICKER: 16, QUOTE: 18, FULL: 22}
DISCONNECT_CODE = 12

# Response (server -> client) feed codes.
RESP_TICKER, RESP_QUOTE, RESP_OI, RESP_PREVCLOSE, RESP_FULL, RESP_DISCONNECT = 2, 4, 5, 6, 8, 50

# Total on-wire packet size (incl. 8-byte header) per response code. We walk a frame by
# these fixed sizes (frames can pack several packets back-to-back). For any code not listed
# we fall back to the header's self-describing message-length field.
# VERIFY BEFORE LIVE: Dhan's table prints Ticker as "17 bytes" but its fields sum to 16
# (8 header + 4 LTP + 4 LTT). We use 16 (field-accurate); irrelevant in QUOTE/FULL mode,
# which is our default. Confirm against a live Ticker frame if you switch to ticker mode.
PACKET_SIZE: Dict[int, int] = {
    RESP_TICKER: 16,
    RESP_QUOTE: 50,
    RESP_FULL: 162,
    RESP_OI: 12,
    RESP_PREVCLOSE: 16,
    RESP_DISCONNECT: 8,
}

_HEADER = struct.Struct("<BHBI")          # code, msg_len, exch_segment, security_id
_DEPTH_LEVEL = struct.Struct("<iihhff")   # bidQty, askQty, bidOrders, askOrders, bidPx, askPx

NowFn = Callable[[], datetime]
ResolveFn = Callable[[int], Optional[str]]  # security_id -> trading symbol (None = unknown)


def build_ws_url(client_id: str, access_token: str, base: str = DEFAULT_WS_BASE) -> str:
    """Full v2 feed URL. Auth travels in the query string (Dhan's documented scheme)."""
    return f"{base}?version=2&token={access_token}&clientId={client_id}&authType=2"


def subscribe_messages(refs: List[InstrumentRef], mode: str = QUOTE) -> List[dict]:
    """Build sharded subscribe messages (≤100 instruments each) for the given mode."""
    code = SUBSCRIBE_CODE.get(mode)
    if code is None:
        raise ValueError(f"unknown feed mode {mode!r}; expected one of {list(SUBSCRIBE_CODE)}")
    msgs: List[dict] = []
    for i in range(0, len(refs), MAX_INSTRUMENTS_PER_MSG):
        chunk = refs[i:i + MAX_INSTRUMENTS_PER_MSG]
        msgs.append({
            "RequestCode": code,
            "InstrumentCount": len(chunk),
            "InstrumentList": [
                {"ExchangeSegment": r.exchange_segment, "SecurityId": str(r.security_id)}
                for r in chunk
            ],
        })
    return msgs


def disconnect_message() -> dict:
    return {"RequestCode": DISCONNECT_CODE}


def _ts_from_epoch(epoch: int, now_fn: NowFn) -> datetime:
    """Convert Dhan's last-trade-time to a tz-aware IST datetime. 0/garbage -> now.

    IMPORTANT (verified live 2026-06-24): Dhan's WebSocket LTT is an **IST-wall-clock** epoch
    (the +5:30 offset is already baked in), NOT a true UTC Unix timestamp. So we recover the
    clock numbers with utcfromtimestamp() and tag them IST. Using fromtimestamp(epoch, tz=IST)
    double-applies the offset (stamps ticks ~5.5h in the future), which made the live session
    logic think the market was closed and suppressed every paper trade.
    """
    if epoch and epoch > 0:
        try:
            return IST.localize(datetime.utcfromtimestamp(int(epoch)))
        except (ValueError, OSError, OverflowError):
            pass
    return now_fn()


def _packet_to_tick(code: int, buf: bytes, off: int, security_id: int,
                    resolve: ResolveFn, now_fn: NowFn) -> Optional[Tick]:
    """Decode one packet (at ``off``) into a Tick, or None if not a price packet / unknown id."""
    symbol = resolve(security_id)
    if symbol is None:
        return None  # not in our universe / no instrument-master entry

    if code == RESP_TICKER:
        ltp, ltt = struct.unpack_from("<fi", buf, off + 8)
        return Tick(symbol=symbol, ts=_ts_from_epoch(ltt, now_fn), ltp=float(ltp), volume=0)

    if code == RESP_QUOTE:
        # LTP, LTQ, LTT, ATP, Volume, TotalSell, TotalBuy, DayOpen, DayClose, DayHigh, DayLow
        ltp, _ltq, ltt, _atp, vol = struct.unpack_from("<fhifi", buf, off + 8)
        return Tick(symbol=symbol, ts=_ts_from_epoch(ltt, now_fn),
                    ltp=float(ltp), volume=int(vol))

    if code == RESP_FULL:
        # Quote-ish prefix + OI block, then 5 depth levels. We only need LTP/vol/time + best
        # bid/ask (level 0) for the Tick; the deeper book is available for a future upgrade.
        ltp, _ltq, ltt, _atp, vol = struct.unpack_from("<fhifi", buf, off + 8)
        bid_qty, ask_qty, _bo, _ao, bid_px, ask_px = _DEPTH_LEVEL.unpack_from(buf, off + 62)
        return Tick(
            symbol=symbol, ts=_ts_from_epoch(ltt, now_fn), ltp=float(ltp), volume=int(vol),
            bid=float(bid_px) if bid_px > 0 else None,
            ask=float(ask_px) if ask_px > 0 else None,
        )

    return None  # OI / PrevClose / Disconnect carry no standalone Tick


def iter_ticks(frame: bytes, resolve: ResolveFn, now_fn: Optional[NowFn] = None) -> Iterator[Tick]:
    """Walk a binary frame (possibly several packets concatenated) and yield Ticks.

    Robust to partial/garbage trailers: stops cleanly if a declared packet would overrun
    the buffer. Unknown packet codes are skipped via the header's message-length field.
    """
    now_fn = now_fn or (lambda: datetime.now(IST))
    n = len(frame)
    i = 0
    while i + _HEADER.size <= n:
        code, msg_len, _seg, security_id = _HEADER.unpack_from(frame, i)
        size = PACKET_SIZE.get(code)
        if size is None:
            size = _HEADER.size + msg_len  # self-describing fallback for unlisted codes
        if size < _HEADER.size or i + size > n:
            break  # truncated / nonsense — don't read past the buffer
        if code == RESP_DISCONNECT:
            log.warning("dhan feed: server disconnect packet received")
            break
        tick = _packet_to_tick(code, frame, i, security_id, resolve, now_fn)
        if tick is not None:
            yield tick
        i += size


def run_feed(
    url: str,
    subscribe_msgs: List[dict],
    resolve: ResolveFn,
    on_tick: Callable[[Tick], None],
    ws_factory: Optional[Callable[[str], object]] = None,
    now_fn: Optional[NowFn] = None,
    stop: Optional[Callable[[], bool]] = None,
    max_reconnects: int = 200,
    backoff_s: float = 2.0,
    backoff_cap_s: float = 15.0,
    sleep_fn: Callable[[float], None] = _time.sleep,
) -> None:
    """Connect, subscribe, and stream ticks to ``on_tick`` until ``stop()`` or the feed ends.

    Transport is injected via ``ws_factory(url) -> ws`` where ``ws`` exposes ``send(str)``,
    ``recv() -> bytes|str``, and ``close()`` (the websocket-client default does). Reconnects on
    transport errors with capped linear backoff, up to ``max_reconnects`` consecutive failures —
    the high default (200 × ≤15s ≈ ~45 min of retrying) lets the live session ride through a
    multi-minute network outage and resume in place when connectivity returns. A falsy
    ``recv()`` (empty/None) ends the current connection cleanly — used by tests.
    """
    def _backoff(n: int) -> float:
        return min(backoff_s * n, backoff_cap_s)

    factory = ws_factory or _default_ws_factory
    attempts = 0
    while True:
        if stop is not None and stop():
            return
        try:
            ws = factory(url)
        except Exception as exc:  # noqa: BLE001
            attempts += 1
            if attempts > max_reconnects:
                log.error("dhan feed: giving up after %d connect failures: %s", attempts, exc)
                return
            log.warning("dhan feed: connect failed (%d/%d): %s", attempts, max_reconnects, exc)
            sleep_fn(_backoff(attempts))
            continue

        try:
            for msg in subscribe_msgs:
                ws.send(json.dumps(msg))
            log.info("dhan feed: subscribed (%d messages)", len(subscribe_msgs))
            attempts = 0  # a clean connect + subscribe resets the failure counter
            _consume(ws, resolve, on_tick, now_fn, stop)
            # recv loop returned without error -> feed ended; close and stop.
            _safe_close(ws)
            return
        except Exception as exc:  # noqa: BLE001 - transport hiccup -> reconnect
            _safe_close(ws)
            attempts += 1
            if attempts > max_reconnects:
                log.error("dhan feed: giving up after %d stream errors: %s", attempts, exc)
                return
            log.warning("dhan feed: stream error (%d/%d), reconnecting: %s",
                        attempts, max_reconnects, exc)
            sleep_fn(_backoff(attempts))


def _consume(ws, resolve: ResolveFn, on_tick, now_fn, stop) -> None:
    """Inner recv loop: pull frames, parse, fan out ticks. Returns when the feed ends."""
    while True:
        if stop is not None and stop():
            return
        msg = ws.recv()
        if not msg:          # empty/None -> connection closed (or fake feed exhausted)
            return
        if isinstance(msg, str):
            continue          # control / JSON status text — no ticks to extract
        for tick in iter_ticks(msg, resolve, now_fn):
            on_tick(tick)


def _safe_close(ws) -> None:
    try:
        ws.close()
    except Exception:  # noqa: BLE001
        pass


def _default_ws_factory(url: str):  # pragma: no cover - real network
    from websocket import create_connection

    # websocket-client answers server pings inside recv(), satisfying Dhan's 40s heartbeat.
    return create_connection(url, timeout=60, enable_multithread=True)
