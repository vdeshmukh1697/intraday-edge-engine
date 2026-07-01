"""RecordingAlerter — mirrors every outbound alert into the predictions log.

Wraps any concrete Alerter (Telegram in production). ``send`` accepts an optional
``meta`` dict of structured parameters (symbol, entry, stop, target, confidence, …)
that call sites attach when they have them; the wrapper delegates to the real
channel, then persists the row with the observed delivery status. A logging
failure can never block the alert (log_prediction is fully best-effort), and a
channel failure still leaves a record (delivered=0).
"""

from __future__ import annotations

from typing import Optional

from signal_engine.alerts.base import Alerter


class RecordingAlerter(Alerter):
    def __init__(self, inner: Alerter, db_path: str, run_id: Optional[str] = None):
        from signal_engine.storage.repository import _default_run_id

        self.inner = inner
        self.db_path = db_path
        self.run_id = run_id or _default_run_id()

    def send(self, message: str, level: str = "info", meta: Optional[dict] = None) -> None:
        from signal_engine.storage.predictions_log import log_prediction

        delivered = True
        try:
            self.inner.send(message, level=level)
        except Exception:  # noqa: BLE001 - concrete alerters already swallow; belt+braces
            delivered = False
        log_prediction(self.db_path, message=message, level=level, meta=meta,
                       delivered=delivered, run_id=self.run_id)
