"""Alerting backends (PLAN §6.6, §7). Behind an interface so Telegram/WhatsApp swap cleanly."""

from signal_engine.alerts.base import Alerter
from signal_engine.alerts.console import ConsoleAlerter

__all__ = ["Alerter", "ConsoleAlerter"]
