"""Reconnect backoff policy (PLAN §9.3 — websocket reconnect on the live feed).

Pure, deterministic exponential backoff with a cap. The live Dhan/shard layer uses this to
space reconnect attempts; kept dependency-free and unit-tested (the mock feed never drops).
"""

from __future__ import annotations


class ReconnectPolicy:
    def __init__(
        self,
        base_seconds: float = 1.0,
        factor: float = 2.0,
        max_seconds: float = 60.0,
        max_attempts: int = 0,  # 0 == unlimited
    ):
        if base_seconds <= 0 or factor < 1.0:
            raise ValueError("base_seconds must be > 0 and factor >= 1.0")
        self.base_seconds = base_seconds
        self.factor = factor
        self.max_seconds = max_seconds
        self.max_attempts = max_attempts

    def delay(self, attempt: int) -> float:
        """Delay (seconds) before retry ``attempt`` (1-based), capped at max_seconds."""
        if attempt < 1:
            raise ValueError("attempt is 1-based (>= 1)")
        raw = self.base_seconds * (self.factor ** (attempt - 1))
        return min(raw, self.max_seconds)

    def should_retry(self, attempt: int) -> bool:
        """Whether to attempt reconnect number ``attempt`` (1-based)."""
        return self.max_attempts == 0 or attempt <= self.max_attempts
