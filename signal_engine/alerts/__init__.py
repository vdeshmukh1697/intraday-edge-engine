"""Alerting backends (PLAN §6.6, §7). Behind an interface so Telegram/WhatsApp swap cleanly."""

from typing import Optional

from signal_engine.alerts.base import Alerter
from signal_engine.alerts.console import ConsoleAlerter

__all__ = ["Alerter", "ConsoleAlerter", "send_alert"]


def send_alert(alerter: Alerter, message: str, level: str = "info",
               meta: Optional[dict] = None) -> None:
    """Send with structured ``meta`` when the alerter records it (RecordingAlerter),
    degrading to the plain 2-arg contract for any other Alerter (incl. test fakes)."""
    try:
        alerter.send(message, level=level, meta=meta)
    except TypeError:
        alerter.send(message, level=level)
