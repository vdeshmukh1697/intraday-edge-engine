"""WhatsApp Cloud API alerter (PLAN §7.1 — the Phase-6 alert channel).

Uses Meta's WhatsApp Cloud API (free tier) via stdlib urllib (no extra deps). Credentials
come from the environment; missing creds raise at construction (loud, not silent). Sends
go ONLY to your own configured number (personal-use, PLAN §9.1). Behind the same `Alerter`
interface, so it is a drop-in for ConsoleAlerter/TelegramAlerter.

Setup (one-time, ~1 hr): create a Meta Business app, enable WhatsApp, get a phone-number ID
+ access token, set WHATSAPP_PHONE_ID / WHATSAPP_TOKEN / WHATSAPP_TO in .env.
"""

from __future__ import annotations

import json
import urllib.request

from signal_engine.alerts.base import Alerter

_API = "https://graph.facebook.com/v18.0"


class WhatsAppAlerter(Alerter):
    def __init__(self, phone_id: str, token: str, to: str, timeout: float = 6.0):
        if not (phone_id and token and to):
            raise ValueError(
                "WhatsAppAlerter requires WHATSAPP_PHONE_ID, WHATSAPP_TOKEN and WHATSAPP_TO."
            )
        self.phone_id = phone_id
        self.token = token
        self.to = to
        self.timeout = timeout

    def send(self, message: str, level: str = "info") -> None:
        emoji = {"signal": "📈", "warning": "⚠️", "alert": "🚨"}.get(level, "ℹ️")
        payload = {
            "messaging_product": "whatsapp",
            "to": self.to,
            "type": "text",
            "text": {"body": f"{emoji} {message}"},
        }
        req = urllib.request.Request(
            f"{_API}/{self.phone_id}/messages",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:  # never let a failed alert crash the engine
            urllib.request.urlopen(req, timeout=self.timeout).read()
        except Exception as exc:  # pragma: no cover - network
            print(f"[whatsapp-send-failed] {exc}: {message}")
