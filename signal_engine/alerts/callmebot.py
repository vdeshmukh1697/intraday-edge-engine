"""CallMeBot WhatsApp alerter — free, no Meta Business account needed.

CallMeBot is a free personal-use service. One-time activation takes 2 minutes
on the user's phone — no Meta app review or business verification required.

Activation (do this once on your phone):
  1. Add +34 694 23 67 31 to your WhatsApp contacts as 'CallMeBot'.
  2. Send the message:  I allow callmebot to send me messages
  3. CallMeBot replies with your API key within seconds.
  4. Put CALLMEBOT_PHONE (+918359980149) and CALLMEBOT_APIKEY in .env.
  5. Set SE_ALERTER=callmebot in .env and restart.

API: https://api.callmebot.com/whatsapp.php?phone=PHONE&text=MSG&apikey=KEY
Docs: https://www.callmebot.com/blog/free-api-whatsapp-messages/
"""

from __future__ import annotations

import urllib.parse
import urllib.request

from signal_engine.alerts.base import Alerter

_API = "https://api.callmebot.com/whatsapp.php"


class CallMeBotAlerter(Alerter):
    """Send WhatsApp messages via CallMeBot (free, personal use only)."""

    def __init__(self, phone: str, apikey: str, timeout: float = 10.0):
        if not (phone and apikey):
            raise ValueError(
                "CallMeBotAlerter requires CALLMEBOT_PHONE and CALLMEBOT_APIKEY. "
                "Activate at callmebot.com — see module docstring."
            )
        # Ensure E.164 format (remove spaces, keep the leading +)
        self.phone = phone.replace(" ", "")
        self.apikey = apikey
        self.timeout = timeout

    def send(self, message: str, level: str = "info") -> None:
        emoji = {"signal": "📈", "warning": "⚠️", "alert": "🚨"}.get(level, "ℹ️")
        text = f"{emoji} {message}"
        url = (
            f"{_API}?"
            f"phone={urllib.parse.quote(self.phone)}"
            f"&text={urllib.parse.quote(text)}"
            f"&apikey={urllib.parse.quote(self.apikey)}"
        )
        try:
            urllib.request.urlopen(url, timeout=self.timeout).read()
        except Exception as exc:  # pragma: no cover — network
            print(f"[callmebot-send-failed] {exc}: {message}")
