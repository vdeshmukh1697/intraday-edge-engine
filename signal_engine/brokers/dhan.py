"""Dhan market-data adapter (PLAN §3.2) — direct Dhan v2 REST. MARKET-DATA ONLY, no orders.

Why REST (not the dhanhq SDK): the current SDK requires Python 3.10+ (uses ``match``) and
this project targets 3.9; calling the documented v2 REST API directly is simpler, dependency-
free, and gives exact control. Verified live: auth headers accepted, instrument master loads.

SUBSCRIPTION NOTE: market-data endpoints require Dhan's paid **Data APIs** subscription. With
a token that lacks it, Dhan returns DH-902 ("not subscribed"); this adapter surfaces that as
a clear ``DhanDataNotSubscribedError`` so the cause is obvious. Order placement is never
implemented (``supports_live_orders`` is always False).

The live websocket feed (``run``) also needs the Data API subscription and is left guarded.
"""

from __future__ import annotations

import base64
import json
import time as _time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pytz

from signal_engine.brokers.base import BrokerAdapter, TickCallback
from signal_engine.domain.models import Bar, Tick
from signal_engine.universe.instruments import DhanInstrumentMaster

IST = pytz.timezone("Asia/Kolkata")
_BASE = "https://api.dhan.co/v2"
_INTERVAL = {"1m": "1", "5m": "5", "15m": "15", "25m": "25", "60m": "60"}


class DhanDataNotSubscribedError(RuntimeError):
    """Raised when the token is valid but the account lacks the paid Data API subscription."""


class DhanRateLimitError(RuntimeError):
    """Raised on DH-904 / HTTP 429. Distinct from 'no data' so callers can retry/back off
    instead of silently treating a throttled response as an empty result."""


def token_expiry(access_token: str) -> Optional[datetime]:
    """Decode the JWT ``exp`` (UTC) without verifying the signature. None if unparseable."""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.utcfromtimestamp(int(data["exp"]))
    except Exception:  # noqa: BLE001
        return None


def _default_post(url: str, body: dict, headers: dict, timeout: float = 30.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {}


class DhanBroker(BrokerAdapter):
    supports_live_orders = False  # never; this is a signal tool

    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        instruments: Optional[DhanInstrumentMaster] = None,
        http_post: Optional[Callable] = None,  # injectable (url, body, headers) -> (status, json)
        feed_mode: str = "quote",              # "ticker" | "quote" | "full"
        ws_factory: Optional[Callable] = None,  # injectable ws_factory(url) -> ws (tests)
    ):
        self.client_id = client_id
        self.access_token = access_token
        self.instruments = instruments
        self._post = http_post or _default_post
        self._feed_mode = feed_mode
        self._ws_factory = ws_factory
        self._cb: Optional[TickCallback] = None
        self._symbols: List[str] = []
        self._last: Dict[str, Tick] = {}

    # --- lifecycle ---------------------------------------------------------
    def _headers(self) -> dict:
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def connect(self) -> None:
        if not (self.client_id and self.access_token):
            raise RuntimeError(
                "DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not set. Use SE_DATA_SOURCE=mock."
            )
        exp = token_expiry(self.access_token)
        if exp is not None and exp < datetime.utcnow():
            raise RuntimeError(
                f"Dhan token expired at {exp} UTC — regenerate it in the Dhan web portal."
            )

    def disconnect(self) -> None:
        return None

    def token_seconds_remaining(self) -> Optional[float]:
        exp = token_expiry(self.access_token or "")
        return None if exp is None else (exp.timestamp() - _time.time())

    def _ref(self, symbol: str):
        if self.instruments is None:
            raise RuntimeError("No Dhan instrument master loaded (DhanInstrumentMaster.fetch()).")
        ref = self.instruments.ref(symbol)
        if ref is None:
            raise KeyError(f"No Dhan security_id for symbol {symbol!r}")
        return ref

    @staticmethod
    def _check_subscription(status, resp) -> None:
        text = json.dumps(resp) if isinstance(resp, dict) else str(resp)
        if "DH-902" in text or "not subscribed" in text.lower() or "not Subscribed" in text:
            raise DhanDataNotSubscribedError(
                "Dhan token is valid but the account is NOT subscribed to Data APIs "
                "(market data is a paid add-on, ~Rs.500/mo). Subscribe in the Dhan portal, "
                "or use the free Yahoo Finance NSE data source (SE_DATA_SOURCE=yahoo_nse)."
            )

    @staticmethod
    def _check_rate_limit(status, resp) -> None:
        """Raise on a throttle response so callers retry instead of seeing a false 'empty'.

        Dhan signals rate limiting as HTTP 429 and/or errorCode DH-904 (Rate_Limit). If this
        slips through as a plain dict with no OHLC arrays, the normalizer would return [] and
        the throttle would masquerade as 'no data' — exactly what silently lost symbols in the
        first full backfill.
        """
        text = json.dumps(resp) if isinstance(resp, dict) else str(resp)
        if status == 429 or "DH-904" in text or "Rate_Limit" in text:
            raise DhanRateLimitError(
                "Dhan rate limit hit (DH-904 / HTTP 429). Throttle to <=5 req/s for Data APIs."
            )

    # --- data --------------------------------------------------------------
    def historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> List[Bar]:
        self.connect()
        ref = self._ref(symbol)
        start = start or (datetime.now(IST) - timedelta(days=5))
        end = end or datetime.now(IST)
        body = {
            "securityId": ref.security_id,
            "exchangeSegment": ref.exchange_segment,
            "instrument": "EQUITY",
            "interval": _INTERVAL.get(timeframe, "1"),
            "fromDate": start.strftime("%Y-%m-%d"),
            "toDate": end.strftime("%Y-%m-%d"),
        }
        status, resp = self._post(f"{_BASE}/charts/intraday", body, self._headers())
        self._check_subscription(status, resp)
        self._check_rate_limit(status, resp)
        return self._normalize_historical(symbol, resp)

    @staticmethod
    def _normalize_historical(symbol: str, resp: object) -> List[Bar]:
        """Dhan intraday response (arrays of open/high/low/close/volume/timestamp) -> List[Bar].

        Timestamps are epoch seconds. Tolerates a top-level dict or a ``data`` wrapper.
        """
        data = resp.get("data", resp) if isinstance(resp, dict) else {}
        opens = data.get("open") or []
        highs = data.get("high") or []
        lows = data.get("low") or []
        closes = data.get("close") or []
        vols = data.get("volume") or [0] * len(opens)
        stamps = data.get("timestamp") or data.get("start_Time") or []
        bars: List[Bar] = []
        for i in range(len(opens)):
            try:
                ts = datetime.fromtimestamp(int(stamps[i]), tz=IST) if i < len(stamps) else None
            except (ValueError, TypeError, OSError):
                ts = None
            if ts is None:
                continue
            bars.append(Bar(symbol=symbol, ts=ts, open=float(opens[i]), high=float(highs[i]),
                            low=float(lows[i]), close=float(closes[i]),
                            volume=int(vols[i]) if i < len(vols) else 0, timeframe="1m"))
        return bars

    def quote(self, symbols: List[str]) -> Dict[str, Tick]:
        self.connect()
        by_seg: Dict[str, List[int]] = {}
        sid_to_sym: Dict[str, str] = {}
        for s in symbols:
            ref = self._ref(s)
            by_seg.setdefault(ref.exchange_segment, []).append(int(ref.security_id))
            sid_to_sym[str(ref.security_id)] = s
        status, resp = self._post(f"{_BASE}/marketfeed/ltp", by_seg, self._headers())
        self._check_subscription(status, resp)
        self._check_rate_limit(status, resp)
        out: Dict[str, Tick] = {}
        now = datetime.now(IST)
        data = resp.get("data", {}) if isinstance(resp, dict) else {}
        for _seg, items in data.items():
            if not isinstance(items, dict):
                continue
            for sid, payload in items.items():
                sym = sid_to_sym.get(str(sid))
                ltp = payload.get("last_price") if isinstance(payload, dict) else None
                if sym and ltp is not None:
                    out[sym] = Tick(symbol=sym, ts=now, ltp=float(ltp), volume=0)
        self._last.update(out)
        return out

    # --- live feed ---------------------------------------------------------
    def subscribe(self, symbols: List[str]) -> None:
        self._symbols = list(symbols)

    def set_tick_callback(self, callback: TickCallback) -> None:
        self._cb = callback

    def run(self, stop: Optional[Callable[[], bool]] = None) -> None:
        """Stream the live Dhan feed, invoking the tick callback for every update.

        Blocks until ``stop()`` returns True or the feed ends. Requires the paid Data API
        subscription (otherwise the connection is rejected) and the instrument master, so
        security ids can be resolved back to symbols. Never places orders.
        """
        self.connect()
        if self.instruments is None:
            raise RuntimeError("No Dhan instrument master loaded; cannot map security ids.")
        if self._cb is None:
            raise RuntimeError("No tick callback set (call set_tick_callback first).")

        from signal_engine.brokers import dhan_ws

        refs = [self._ref(s) for s in self._symbols]
        # security_id (int) -> symbol, for decoding the binary feed back to tickers.
        rev = {int(r.security_id): r.symbol for r in refs}
        url = dhan_ws.build_ws_url(self.client_id, self.access_token)
        msgs = dhan_ws.subscribe_messages(refs, mode=self._feed_mode)
        dhan_ws.run_feed(
            url, msgs, resolve=rev.get, on_tick=self._cb,
            ws_factory=self._ws_factory, stop=stop,
        )
