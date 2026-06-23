"""Strategy Health Scorer (PLAN §6.6): rolling self-scoring + degradation alerts."""

from signal_engine.health.scorer import HealthScore, compute_health, detect_degradation

__all__ = ["HealthScore", "compute_health", "detect_degradation"]
