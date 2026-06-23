"""Yahoo Finance NSE adapter — free real 1-minute NSE bars via yfinance.

No API key or subscription needed. Tickers are NSE-quoted: RELIANCE → RELIANCE.NS.
Data is delayed ~15 minutes relative to NSE official feed; suitable for signal
research, pre-market review, and same-day historical replay.

Limitations:
  - 1-min bars limited to ~7 trading days (Yahoo Finance hard limit).
  - No true live WebSocket; run() replays today's bars bar-by-bar.
  - Rate-limited by Yahoo if called in rapid bursts — add per-symbol jitter
    when scanning the full universe (~150 ms between tickers is safe).
"""

from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Callable, Dict, List, Optional

import pytz
import yfinance as yf

from signal_engine.brokers.base import BrokerAdapter, TickCallback
from signal_engine.domain.models import Bar, Tick

IST = pytz.timezone("Asia/Kolkata")
_INTERVAL_MAP: Dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m", "60m": "60m", "1h": "60m",
}


def _nse_ticker(symbol: str) -> str:
    """RELIANCE → RELIANCE.NS"""
    s = symbol.upper().strip()
    return s if s.endswith(".NS") else f"{s}.NS"


def _df_to_bars(symbol: str, df, timeframe: str = "1m") -> List[Bar]:
    bars: List[Bar] = []
    for ts, row in df.iterrows():
        try:
            if ts.tzinfo is None:
                ts = pytz.utc.localize(ts)
            ts_ist = ts.astimezone(IST)
            bars.append(Bar(
                symbol=symbol,
                ts=ts_ist,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row.get("Volume", 0)),
                timeframe=timeframe,
            ))
        except Exception:  # noqa: BLE001
            continue
    return bars


class YahooNSEBroker(BrokerAdapter):
    """Free real NSE data via Yahoo Finance — no API key, no subscription."""

    supports_live_orders = False

    def __init__(
        self,
        yf_fetch: Optional[Callable[[str, str], object]] = None,
    ):
        # yf_fetch(ticker_str, interval_str) -> DataFrame — injectable for tests
        self._fetch = yf_fetch
        self._cb: Optional[TickCallback] = None
        self._symbols: List[str] = []

    # --- lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        return None  # no auth needed

    def disconnect(self) -> None:
        return None

    def subscribe(self, symbols: List[str]) -> None:
        self._symbols = list(symbols)

    def set_tick_callback(self, callback: TickCallback) -> None:
        self._cb = callback

    # --- data ----------------------------------------------------------------

    def _get_df(self, ticker: str, interval: str):
        if self._fetch is not None:
            return self._fetch(ticker, interval)
        return yf.Ticker(ticker).history(period="5d", interval=interval, auto_adjust=True)

    def historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> List[Bar]:
        interval = _INTERVAL_MAP.get(timeframe, "1m")
        df = self._get_df(_nse_ticker(symbol), interval)
        if df is None or df.empty:
            return []
        # Ensure tz-aware index, then filter to caller's window
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(IST)
        if start is not None:
            s = start.astimezone(IST) if start.tzinfo else IST.localize(start)
            df = df[df.index >= s]
        if end is not None:
            e = end.astimezone(IST) if end.tzinfo else IST.localize(end)
            df = df[df.index <= e]
        return _df_to_bars(symbol, df, timeframe)

    def quote(self, symbols: List[str]) -> Dict[str, Tick]:
        now = datetime.now(IST)
        out: Dict[str, Tick] = {}
        for sym in symbols:
            try:
                df = self._get_df(_nse_ticker(sym), "1m")
                if df is not None and not df.empty:
                    ltp = float(df["Close"].iloc[-1])
                    vol = int(df["Volume"].iloc[-1])
                    out[sym] = Tick(symbol=sym, ts=now, ltp=ltp, volume=vol)
            except Exception:  # noqa: BLE001
                continue
        return out

    def run(self) -> None:
        """Replay today's fetched bars bar-by-bar into the tick callback.

        Not a real-time feed — useful for offline replay and integration testing.
        """
        if not self._cb or not self._symbols:
            return
        today = datetime.now(IST).date()
        for sym in self._symbols:
            for bar in self.historical(sym, "1m", None, None):
                if bar.ts.date() == today:
                    self._cb(Tick(symbol=sym, ts=bar.ts, ltp=bar.close, volume=bar.volume))
                    _time.sleep(0.01)
