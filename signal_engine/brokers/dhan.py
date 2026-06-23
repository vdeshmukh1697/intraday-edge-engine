"""Dhan adapter — DATA-ONLY stub (PLAN §3.2, ground-rule safety).

Real Dhan integration is intentionally NOT wired up yet. Per the project's execution
rules, integrating a real broker API requires an explicit human go-ahead first, so this
adapter refuses to connect and points the user back to the mock source. Even once
enabled it will be **market-data only** — there is no order-placement path anywhere.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from signal_engine.brokers.base import BrokerAdapter, TickCallback
from signal_engine.domain.models import Bar, Tick


class DhanBroker(BrokerAdapter):
    supports_live_orders = False  # never; this is a signal tool

    def __init__(self, client_id: str = None, access_token: str = None):
        self.client_id = client_id
        self.access_token = access_token

    def _not_enabled(self):
        raise RuntimeError(
            "Live Dhan integration is not enabled in this build. It is gated behind an "
            "explicit human go-ahead (see PLAN §8 / ground rules) and will be market-data "
            "only. Use SE_DATA_SOURCE=mock for development and testing."
        )

    def connect(self) -> None:
        if not (self.client_id and self.access_token):
            raise RuntimeError(
                "DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not set. Live data is disabled; "
                "use SE_DATA_SOURCE=mock."
            )
        self._not_enabled()

    def disconnect(self) -> None:
        return None

    def subscribe(self, symbols: List[str]) -> None:
        self._not_enabled()

    def set_tick_callback(self, callback: TickCallback) -> None:
        self._cb = callback

    def historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> List[Bar]:
        self._not_enabled()

    def quote(self, symbols: List[str]) -> Dict[str, Tick]:
        self._not_enabled()
