"""Tests for the daily job scheduler (Phase ops). Jobs are registered, not run live."""

from signal_engine.config import load_config
from signal_engine.scheduler import build_scheduler


def test_scheduler_registers_all_jobs():
    sched = build_scheduler(load_config())
    job_ids = {j.id for j in sched.get_jobs()}
    assert job_ids == {"renew_token", "premarket", "live", "scan", "archive"}
    sched.shutdown(wait=False) if sched.running else None


def test_jobs_skip_non_trading_day(monkeypatch):
    """The job bodies must no-op on a non-trading day (don't alert on holidays/weekends)."""
    from datetime import date

    import signal_engine.scheduler as s

    monkeypatch.setattr(s, "_today", lambda: date(2025, 8, 15))  # holiday
    # Should return quietly without raising or alerting.
    s.premarket_job(load_config())
    s.scan_job(load_config())
    s.archive_job(load_config())
