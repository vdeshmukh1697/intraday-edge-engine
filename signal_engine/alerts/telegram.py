"""Telegram alerter — Phase-1 fallback (PLAN §7.1). Stdlib only (urllib), no extra deps.

Credentials come from the environment (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). If they
are missing it raises at construction so misconfiguration is loud, not silent. WhatsApp
(Phase 6) will slot in behind the same Alerter interface.
"""

from __future__ import annotations

import urllib.parse
import urllib.request

from signal_engine.alerts.base import Alerter


class TelegramAlerter(Alerter):
    def __init__(self, bot_token: str, chat_id: str, timeout: float = 5.0):
        if not bot_token or not chat_id:
            raise ValueError(
                "TelegramAlerter requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
            )
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, message: str, level: str = "info") -> None:
        emoji = {"signal": "📈", "warning": "⚠️", "alert": "🚨"}.get(level, "ℹ️")
        text = f"{emoji} {message}"
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=data)
        try:  # never let a failed alert crash the engine
            urllib.request.urlopen(req, timeout=self.timeout).read()
        except Exception as exc:  # pragma: no cover - network
            print(f"[telegram-send-failed] {exc}: {message}")
