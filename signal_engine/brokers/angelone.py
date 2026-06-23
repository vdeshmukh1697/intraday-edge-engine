"""Angel One SmartAPI adapter — free real-time NSE data (no paid subscription).

Angel One SmartAPI is completely free for Angel One account holders:
  - Historical OHLCV (up to 8,000 candles per request, 1-minute resolution)
  - Real-time LTP via SmartWebSocket
  - No separate data subscription fee (unlike Dhan's ₹500/month Data API)

Account setup (free, zero-brokerage):
  1. Open an Angel One demat account at www.angelone.in (free, ~10 min online)
  2. Log in → Profile → API Access → Create App → note your API key
  3. In the API settings, enable TOTP and scan the QR code with Google Authenticator
  4. Put these in .env:
       ANGELONE_API_KEY=your_app_api_key
       ANGELONE_CLIENT_ID=your_angel_client_id       (e.g. A12345678)
       ANGELONE_PASSWORD=your_angel_password
       ANGELONE_TOTP_SECRET=your_totp_secret         (from the QR setup)
  5. Set SE_DATA_SOURCE=angelone

This adapter uses the official smartapi-python SDK (lazy-imported so you only
need it when SE_DATA_SOURCE=angelone). Install: pip install smartapi-python pyotp.

Instrument tokens: downloaded once from Angel One's free OpenAPI scrip master JSON.
Symbol mapping: "RELIANCE" → token "2885" (NSE-EQ segment).
"""

from __future__ import annotations

import json
import time as _time
import urllib.request
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pytz

from signal_engine.brokers.base import BrokerAdapter, TickCallback
from signal_engine.domain.models import Bar, Tick
from signal_engine.obs.logging_setup import get_logger

IST = pytz.timezone("Asia/Kolkata")
log = get_logger(__name__)

_SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)
_INTERVAL_MAP: Dict[str, str] = {
    "1m": "ONE_MINUTE",
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "60m": "ONE_HOUR",
    "1h": "ONE_HOUR",
    "1d": "ONE_DAY",
}


class AngelOneInstrumentMaster:
    """Token look-up for Angel One SmartAPI (NSE-EQ only).

    Symbol format in the scrip master is 'RELIANCE-EQ'; we index it as 'RELIANCE'
    (stripped of the '-EQ' suffix) for compatibility with the rest of the engine.
    """

    def __init__(self, tokens: Dict[str, str]):
        self._tokens = tokens  # upper(symbol) -> token string

    @classmethod
    def from_json(cls, text: str) -> "AngelOneInstrumentMaster":
        raw: List[Dict] = json.loads(text)
        tokens: Dict[str, str] = {}
        for item in raw:
            if item.get("exch_seg") != "NSE":
                continue
            sym = str(item.get("symbol") or "").strip()
            if not sym.endswith("-EQ"):
                continue
            name = sym[:-3].upper()  # strip '-EQ'
            tok = str(item.get("token") or "").strip()
            if name and tok:
                tokens[name] = tok
        return cls(tokens)

    @classmethod
    def fetch(cls, url: str = _SCRIP_MASTER_URL, timeout: int = 60) -> "AngelOneInstrumentMaster":
        with urllib.request.urlopen(url, timeout=timeout) as r:
            text = r.read().decode("utf-8")
        return cls.from_json(text)

    def token(self, symbol: str) -> Optional[str]:
        return self._tokens.get(symbol.upper().strip())

    def __len__(self) -> int:
        return len(self._tokens)


def _df_from_candles(symbol: str, raw_data: list, timeframe: str = "1m") -> List[Bar]:
    """Convert Angel One candle list [[ts_str, o, h, l, c, v], ...] → List[Bar]."""
    bars: List[Bar] = []
    for row in raw_data:
        try:
            ts_str, o, h, lo, c, v = row[0], row[1], row[2], row[3], row[4], row[5]
            # Timestamp: "2024-01-15T09:15:00+05:30" — parse with pytz
            ts = datetime.fromisoformat(ts_str).astimezone(IST)
            bars.append(Bar(
                symbol=symbol,
                ts=ts,
                open=float(o),
                high=float(h),
                low=float(lo),
                close=float(c),
                volume=int(v),
                timeframe=timeframe,
            ))
        except Exception:  # noqa: BLE001
            continue
    return bars


class AngelOneBroker(BrokerAdapter):
    """Real-time and historical NSE data via Angel One SmartAPI (free with account)."""

    supports_live_orders = False

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        password: Optional[str] = None,
        totp_secret: Optional[str] = None,
        instruments: Optional[AngelOneInstrumentMaster] = None,
        smart_api_factory: Optional[Callable] = None,  # injectable for tests
    ):
        self.api_key = api_key
        self.client_id = client_id
        self.password = password
        self.totp_secret = totp_secret
        self.instruments = instruments
        self._factory = smart_api_factory  # () -> SmartConnect instance (for DI)
        self._sc = None  # authenticated SmartConnect session
        self._cb: Optional[TickCallback] = None
        self._symbols: List[str] = []

    # --- lifecycle -----------------------------------------------------------

    def _make_totp(self) -> str:
        import pyotp
        return pyotp.TOTP(self.totp_secret).now()

    def connect(self) -> None:
        if not all([self.api_key, self.client_id, self.password, self.totp_secret]):
            raise RuntimeError(
                "AngelOneBroker requires ANGELONE_API_KEY, ANGELONE_CLIENT_ID, "
                "ANGELONE_PASSWORD, and ANGELONE_TOTP_SECRET in .env. "
                "See brokers/angelone.py docstring for setup steps."
            )
        if self._sc is not None:
            return  # already connected
        if self._factory is not None:
            self._sc = self._factory()
            return
        from SmartApi import SmartConnect  # lazy — only when SE_DATA_SOURCE=angelone
        sc = SmartConnect(api_key=self.api_key)
        totp = self._make_totp()
        data = sc.generateSession(self.client_id, self.password, totp)
        if not data.get("status"):
            raise RuntimeError(
                f"Angel One login failed: {data.get('message', 'unknown error')}. "
                "Check ANGELONE_CLIENT_ID / ANGELONE_PASSWORD / ANGELONE_TOTP_SECRET."
            )
        self._sc = sc
        log.info("Angel One connected as %s", self.client_id)

    def disconnect(self) -> None:
        if self._sc is not None:
            try:
                self._sc.terminateSession(self.client_id)
            except Exception:  # noqa: BLE001
                pass
            self._sc = None

    def _require_instruments(self, symbol: str) -> str:
        if self.instruments is None:
            raise RuntimeError(
                "No AngelOneInstrumentMaster loaded. "
                "Call AngelOneInstrumentMaster.fetch() and pass as instruments=."
            )
        tok = self.instruments.token(symbol)
        if tok is None:
            raise KeyError(f"No Angel One token for symbol {symbol!r}")
        return tok

    # --- data ----------------------------------------------------------------

    def historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> List[Bar]:
        self.connect()
        token = self._require_instruments(symbol)
        interval = _INTERVAL_MAP.get(timeframe, "ONE_MINUTE")
        now = datetime.now(IST)
        from_dt = start or (now - timedelta(days=5))
        to_dt = end or now
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": interval,
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        resp = self._sc.getCandleData(params)
        if not resp.get("status"):
            raise RuntimeError(
                f"Angel One getCandleData failed for {symbol}: {resp.get('message')}"
            )
        raw = resp.get("data") or []
        return _df_from_candles(symbol, raw, timeframe)

    def quote(self, symbols: List[str]) -> Dict[str, Tick]:
        self.connect()
        now = datetime.now(IST)
        out: Dict[str, Tick] = {}
        for sym in symbols:
            try:
                token = self._require_instruments(sym)
                resp = self._sc.ltpData("NSE", sym, token)
                if resp.get("status") and resp.get("data"):
                    # ltpData REST returns price in rupees (not paisa)
                    ltp = float(resp["data"].get("ltp", 0))
                    out[sym] = Tick(symbol=sym, ts=now, ltp=ltp, volume=int(resp["data"].get("tradedQty", 0)))
            except Exception as exc:  # noqa: BLE001
                log.warning("quote failed for %s: %s", sym, exc)
        return out

    # --- live feed -----------------------------------------------------------

    def subscribe(self, symbols: List[str]) -> None:
        self._symbols = list(symbols)

    def set_tick_callback(self, callback: TickCallback) -> None:
        self._cb = callback

    def run(self) -> None:
        """SmartWebSocket live feed — drives tick callback until interrupted.

        Requires the SmartAPI websocket subscription. If you hit auth errors,
        ensure your Angel One account has API access enabled and TOTP is correct.
        """
        self.connect()
        if not self._cb or not self._symbols:
            return
        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        except ImportError as exc:
            raise RuntimeError(
                "smartapi-python websocket module not available. "
                "pip install smartapi-python websocket-client"
            ) from exc
        feed_token = self._sc.getfeedToken()
        correlation_id = "signal_engine"
        mode = 1  # LTP mode
        token_list = [
            {"exchangeType": 1, "tokens": [self._require_instruments(s) for s in self._symbols]}
        ]

        def on_data(wsapp, message):
            try:
                data = json.loads(message) if isinstance(message, str) else message
                sym = data.get("tradingSymbol", "")
                ltp = data.get("lastTradedPrice", 0) / 100.0  # paisa → rupees
                ts = datetime.now(IST)
                if sym and ltp:
                    self._cb(Tick(symbol=sym, ts=ts, ltp=ltp, volume=0))
            except Exception:  # noqa: BLE001
                pass

        def on_error(wsapp, error):
            log.error("Angel One WS error: %s", error)

        def on_close(wsapp):
            log.info("Angel One WS closed")

        def on_open(wsapp):
            wsapp.subscribe(correlation_id, mode, token_list)

        sws = SmartWebSocketV2(
            self._sc.generateUserSession().get("data", {}).get("jwtToken", ""),
            self.api_key,
            self.client_id,
            feed_token,
        )
        sws.on_open = on_open
        sws.on_data = on_data
        sws.on_error = on_error
        sws.on_close = on_close
        sws.connect()
        while True:
            _time.sleep(1)
