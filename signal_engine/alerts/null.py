"""No-op alerter — used by the backtester so a multi-day replay stays silent."""

from __future__ import annotations

from signal_engine.alerts.base import Alerter


class NullAlerter(Alerter):
    def send(self, message: str, level: str = "info") -> None:
        return None
