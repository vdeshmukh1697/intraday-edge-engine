"""MockBroker — replays synthetic intraday sessions as ticks (no live feed).

This is the default data source so the engine runs anytime, independent of market
hours (ground rule). It generates one session per subscribed symbol for a given day
and streams the ticks in time order to the registered callback.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from signal_engine.brokers.base import BrokerAdapter, TickCallback
from signal_engine.data.synthetic import bars_to_ticks, generate_session
from signal_engine.domain.models import Bar, Tick
from signal_engine.ingestion.aggregator import BarAggregator


class MockBroker(BrokerAdapter):
    supports_live_orders = False

    def __init__(
        self,
        day: date,
        seed: int = 42,
        regime_map: Optional[Dict[str, str]] = None,
        start_prices: Optional[Dict[str, float]] = None,
    ):
        self.day = day
        self.seed = seed
        self.regime_map = regime_map or {}
        self.start_prices = start_prices or {}
        self._symbols: List[str] = []
        self._sessions: Dict[str, "object"] = {}  # symbol -> DataFrame
        self._cb: Optional[TickCallback] = None
        self._last: Dict[str, Tick] = {}

    # --- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def subscribe(self, symbols: List[str]) -> None:
        self._symbols = list(symbols)
        for i, sym in enumerate(self._symbols):
            self._sessions[sym] = generate_session(
                sym,
                self.day,
                start_price=self.start_prices.get(sym, 1000.0 + 50 * i),
                seed=self.seed + i,
                regime=self.regime_map.get(sym, "choppy"),
            )

    def set_tick_callback(self, callback: TickCallback) -> None:
        self._cb = callback

    # --- data --------------------------------------------------------------
    def historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> List[Bar]:
        """Return 1-minute bars for the symbol's generated session (warmup/backtest)."""
        if symbol not in self._sessions:
            self._sessions[symbol] = generate_session(
                symbol, self.day, seed=self.seed, regime=self.regime_map.get(symbol, "choppy")
            )
        df = self._sessions[symbol]
        bars: List[Bar] = []
        for ts, row in df.iterrows():
            dt = ts.to_pydatetime()
            if start <= dt <= end:
                bars.append(
                    Bar(symbol, dt, float(row.open), float(row.high), float(row.low),
                        float(row.close), int(row.volume), "1m", False)
                )
        return bars

    def quote(self, symbols: List[str]) -> Dict[str, Tick]:
        return {s: self._last[s] for s in symbols if s in self._last}

    # --- replay ------------------------------------------------------------
    def run(self) -> None:
        """Stream every tick across all subscribed symbols in chronological order."""
        if self._cb is None:
            raise RuntimeError("No tick callback registered; call set_tick_callback first.")
        all_ticks: List[Tick] = []
        for sym in self._symbols:
            all_ticks.extend(bars_to_ticks(self._sessions[sym], sym))
        all_ticks.sort(key=lambda t: t.ts)
        for t in all_ticks:
            self._last[t.symbol] = t
            self._cb(t)

    # convenience for aggregating warmup bars
    @staticmethod
    def bars_from_ticks(symbol: str, ticks: List[Tick]) -> List[Bar]:
        agg = BarAggregator(symbol, 1)
        bars = [b for t in ticks if (b := agg.add_tick(t)) is not None]
        last = agg.flush()
        if last:
            bars.append(last)
        return bars
