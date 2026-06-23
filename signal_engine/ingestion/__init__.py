"""Data ingestion: tick -> bar aggregation (PLAN §3.3)."""

from signal_engine.ingestion.aggregator import BarAggregator, roll_up

__all__ = ["BarAggregator", "roll_up"]
