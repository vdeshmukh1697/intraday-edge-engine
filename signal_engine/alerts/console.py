"""Console alerter — zero-setup default. Prints structured alerts to stdout."""

from __future__ import annotations

import sys

from signal_engine.alerts.base import Alerter

_PREFIX = {
    "info": "[i]",
    "signal": "[>] SIGNAL",
    "warning": "[!] WARN",
    "alert": "[!!] ALERT",
}


class ConsoleAlerter(Alerter):
    def __init__(self, stream=None):
        self._stream = stream if stream is not None else sys.stdout

    def send(self, message: str, level: str = "info") -> None:
        prefix = _PREFIX.get(level, "[i]")
        print(f"{prefix} {message}", file=self._stream, flush=True)
