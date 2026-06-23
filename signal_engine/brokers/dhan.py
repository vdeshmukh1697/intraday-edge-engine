"""Dhan market-data adapter (PLAN §3.2). MARKET-DATA ONLY — never places orders.

Implements ``BrokerAdapter`` against the official ``dhanhq`` SDK. Lazy-imported so the
package works without it; gated so it refuses to connect without credentials.

SAFETY: there is no order-placement method here or anywhere. ``supports_live_orders`` is
always False. This is a decision-support tool (PLAN §1, §9).

VERIFICATION STATUS: the historical/quote response normalization is unit-tested via an
injected fake client. The live websocket feed (``run``) and exact dhanhq method
signatures must be confirmed against the installed SDK version + a live account once
credentials exist (KYC pending). NOTE markers flag those spots.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import pytz

from signal_engine.brokers.base import BrokerAdapter, TickCallback
from signal_engine.domain.models import Bar, Tick
from signal_engine.universe.instruments import DhanInstrumentMaster

IST = pytz.timezone("Asia/Kolkata")


class DhanBroker(BrokerAdapter):
    supports_live_orders = False  # never; this is a signal tool

    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        instruments: Optional[DhanInstrumentMaster] = None,
        client: Optional[object] = None,  # injectable for tests (a dhanhq instance)
    ):
        self.client_id = client_id
        self.access_token = access_token
        self.instruments = instruments
        self._client = client
        self._cb: Optional[TickCallback] = None
        self._symbols: List[str] = []
        self._last: Dict[str, Tick] = {}

    # --- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        if self._client is not None:
            return  # injected (tests)
        if not (self.client_id and self.access_token):
            raise RuntimeError(
                "DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not set. Live data is disabled; "
                "use SE_DATA_SOURCE=mock until your Dhan API token is available."
            )
        try:
            from dhanhq import dhanhq
        except ImportError as exc:
            raise RuntimeError(
                "Dhan SDK not installed. Run `pip install dhanhq` to enable the live feed."
            ) from exc
        self._client = dhanhq(self.client_id, self.access_token)

    def disconnect(self) -> None:
        self._client = None

    def _require(self):
        if self._client is None:
            self.connect()
        return self._client

    def _sec_id(self, symbol: str) -> str:
        if self.instruments is None:
            raise RuntimeError(
                "No Dhan instrument master loaded — map symbols to security IDs first "
                "(DhanInstrumentMaster.fetch())."
            )
        ref = self.instruments.ref(symbol)
        if ref is None:
            raise KeyError(f"No Dhan security_id for symbol {symbol!r}")
        return ref.security_id

    # --- data --------------------------------------------------------------
    def historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> List[Bar]:
        """Fetch 1-minute bars and normalize to List[Bar].

        NOTE: dhanhq exposes intraday minute data as ``intraday_minute_data`` (v2). Confirm
        the method name + response shape against your installed SDK version. The normalizer
        below handles the documented dict-of-arrays response and is unit-tested.
        """
        client = self._require()
        sec_id = self._sec_id(symbol)
        seg = self.instruments.ref(symbol).exchange_segment
        # NOTE: signature per dhanhq v2; adjust if your version differs.
        resp = client.intraday_minute_data(
            security_id=sec_id, exchange_segment=seg, instrument_type="EQUITY"
        )
        return self._normalize_historical(symbol, resp)

    @staticmethod
    def _normalize_historical(symbol: str, resp: object) -> List[Bar]:
        """Convert a dhanhq historical response to List[Bar]. Tested via injected fakes.

        Accepts the documented shape: {"data": {"open":[...], "high":[...], "low":[...],
        "close":[...], "volume":[...], "timestamp":[...]}} (timestamps epoch seconds),
        and tolerates a top-level dict without the "data" wrapper.
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
            bars.append(Bar(
                symbol=symbol, ts=ts, open=float(opens[i]), high=float(highs[i]),
                low=float(lows[i]), close=float(closes[i]),
                volume=int(vols[i]) if i < len(vols) else 0, timeframe="1m",
            ))
        return bars

    def quote(self, symbols: List[str]) -> Dict[str, Tick]:
        """Latest snapshot per symbol. NOTE: confirm dhanhq quote method/shape on your SDK."""
        return {s: self._last[s] for s in symbols if s in self._last}

    # --- live feed (subscribe/run) -----------------------------------------
    def subscribe(self, symbols: List[str]) -> None:
        self._symbols = list(symbols)

    def set_tick_callback(self, callback: TickCallback) -> None:
        self._cb = callback

    def run(self) -> None:
        """Open the Dhan market-data websocket and forward ticks to the callback.

        NOT VERIFIED against a live account (KYC pending). dhanhq exposes the feed via
        ``from dhanhq import marketfeed`` -> ``marketfeed.DhanFeed(...)`` with instrument
        tuples (exchange_segment, security_id, subscription_type). This wiring must be
        validated once credentials exist; until then the engine uses the mock feed.
        """
        raise NotImplementedError(
            "Live Dhan websocket feed is not yet verified (KYC/credentials pending). "
            "Historical/quote normalization is tested; wire + verify the feed when the "
            "Dhan API token is available. Use SE_DATA_SOURCE=mock in the meantime."
        )
