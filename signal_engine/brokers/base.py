"""BrokerAdapter contract (PLAN §3.2).

IMPORTANT SAFETY NOTE
---------------------
This interface is **market-data only**. There is intentionally NO order-placement
method. This is a decision-support tool; the human places every order (PLAN §1, §9).
``supports_live_orders`` is always False and exists purely so the property is explicit
and auditable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, Dict, List

from signal_engine.domain.models import Bar, Tick

TickCallback = Callable[[Tick], None]


class BrokerAdapter(ABC):
    """Swappable market-data source. Implementations: MockBroker, DhanBroker (data-only)."""

    #: Hard-coded False everywhere. Live order execution is not implemented.
    supports_live_orders: bool = False

    @abstractmethod
    def connect(self) -> None:
        """Establish session / auth. For mock, a no-op."""

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down cleanly."""

    @abstractmethod
    def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to live updates for the given symbols."""

    @abstractmethod
    def set_tick_callback(self, callback: TickCallback) -> None:
        """Register the function invoked for each incoming tick."""

    @abstractmethod
    def historical(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> List[Bar]:
        """Return historical OHLCV bars for [start, end]."""

    @abstractmethod
    def quote(self, symbols: List[str]) -> Dict[str, Tick]:
        """Return the latest snapshot quote for each symbol."""

    def run(self) -> None:
        """Optional: drive a (replay/live) feed loop, invoking the tick callback.

        Default is a no-op; sources that push ticks override this.
        """
        return None
