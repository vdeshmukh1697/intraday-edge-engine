"""Structured logging setup (PLAN §9.3 — 'audit everything').

A single ``configure_logging`` plus ``get_logger`` so every module logs consistently.
Kept off the hot path (the engine buffers/uses logging sparingly during the session).
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False
_FORMAT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str = "INFO", stream=None) -> None:
    """Configure root logging once. Idempotent."""
    global _CONFIGURED
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root = logging.getLogger("signal_engine")
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger (auto-configures with defaults if not done yet)."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(f"signal_engine.{name}")
