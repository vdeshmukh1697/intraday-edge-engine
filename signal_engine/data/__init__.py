"""Synthetic market data for development and tests (no live-market dependency)."""

from signal_engine.data.synthetic import bars_to_ticks, generate_session

__all__ = ["generate_session", "bars_to_ticks"]
