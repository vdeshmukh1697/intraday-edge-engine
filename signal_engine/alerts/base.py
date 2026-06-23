"""Alerter contract. Implementations: ConsoleAlerter (default), TelegramAlerter (Phase 1
fallback), WhatsApp (Phase 6). Swapping the backend is a one-file change (PLAN §7.1)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Alerter(ABC):
    @abstractmethod
    def send(self, message: str, level: str = "info") -> None:
        """Deliver a message. ``level`` in {info, signal, warning, alert}."""
