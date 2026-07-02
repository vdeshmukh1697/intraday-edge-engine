"""Tests for the daily job scheduler (Phase ops). Jobs are registered, not run live."""

from signal_engine.config import load_config
from signal_engine.scheduler import build_scheduler


def test_scheduler_registers_all_jobs():
    sched = build_scheduler(load_config())
    job_ids = {j.id for j in sched.get_jobs()}
    assert job_ids == {"renew_token_6", "renew_token_14", "renew_token_22",
                       "archive_morning", "premarket", "healthcheck", "live", "scan", "archive"}
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


def test_renew_token_retries_next_totp_window(monkeypatch):
    """A transient 'Invalid TOTP' rejection must retry once with the next 30s window's code
    (seen 2/2 on 2026-07-02 scheduler startups); any other failure must not retry."""
    import signal_engine.scheduler as s
    from signal_engine.brokers import dhan_auth

    monkeypatch.setenv("SE_DATA_SOURCE", "dhan")
    monkeypatch.setenv("DHAN_CLIENT_ID", "cid")
    monkeypatch.setenv("DHAN_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    monkeypatch.setenv("DHAN_PIN", "1234")

    calls = {"mint": 0, "slept": []}
    monkeypatch.setattr(s._time, "sleep", lambda secs: calls["slept"].append(secs))

    def flaky_mint(client_id, pin, secret):
        calls["mint"] += 1
        if calls["mint"] == 1:
            raise RuntimeError("generateAccessToken (TOTP) failed (HTTP 200): "
                               "{'message': 'Invalid TOTP', 'status': 'error'}")
        return "fresh-token"

    saved = {}
    monkeypatch.setattr(dhan_auth, "generate_token_via_totp", flaky_mint)
    monkeypatch.setattr(dhan_auth, "update_env_token", lambda tok: saved.update(tok=tok))

    s.renew_token_job()
    assert calls["mint"] == 2                 # failed once, retried once
    assert calls["slept"] == [35]             # waited out the TOTP window
    assert saved["tok"] == "fresh-token"      # the retry's token was persisted

    # Non-TOTP failures must NOT retry (single attempt, error logged, no raise).
    calls["mint"] = 0
    def hard_fail(client_id, pin, secret):
        calls["mint"] += 1
        raise RuntimeError("network down")
    monkeypatch.setattr(dhan_auth, "generate_token_via_totp", hard_fail)
    s.renew_token_job()
    assert calls["mint"] == 1


def test_healthcheck_treats_rate_limit_probe_as_transient(monkeypatch):
    """A DH-904/429 on the 3-symbol quote probe is NOT a failed health check: the token check
    already passed and the probe merely collided with the API's _QuoteHub 1s REST polling
    (seen 2026-07-02 08:45). Retry once after ~3s; if still throttled, alert ✅-with-note.
    Any other quote failure must still alert FAILED."""
    from datetime import date, datetime, timedelta

    import signal_engine.brokers.dhan as dhan
    import signal_engine.factory as factory
    import signal_engine.scheduler as s

    monkeypatch.setenv("SE_DATA_SOURCE", "dhan")
    monkeypatch.setattr(s, "_today", lambda: date(2026, 7, 2))  # trading day
    monkeypatch.setattr(s, "refresh_runtime_env", lambda: set())
    monkeypatch.setattr(dhan, "token_expiry",
                        lambda tok: datetime.utcnow() + timedelta(hours=12))
    monkeypatch.setattr(factory, "build_alerter", lambda cfg: object())

    calls = {"quote": 0, "slept": []}
    alerts = []
    monkeypatch.setattr(s, "send_alert",
                        lambda alerter, msg, **kw: alerts.append(msg))
    monkeypatch.setattr(s._time, "sleep", lambda secs: calls["slept"].append(secs))

    class ThrottledBroker:
        def __init__(self, fail_times):
            self.fail_times = fail_times

        def quote(self, symbols):
            calls["quote"] += 1
            if calls["quote"] <= self.fail_times:
                raise dhan.DhanRateLimitError("Dhan rate limit hit (DH-904 / HTTP 429).")
            return {sym: 100.0 for sym in symbols}

    # 1) Throttled once, retry succeeds -> normal ✅ with live quote count.
    monkeypatch.setattr(factory, "build_broker", lambda cfg, day: ThrottledBroker(1))
    s.healthcheck_job()
    assert calls["quote"] == 2 and calls["slept"] == [3]
    assert alerts[-1].startswith("✅") and "3/3 quotes" in alerts[-1]

    # 2) Throttled twice -> ✅-with-note (token valid, probe throttled), never FAILED.
    calls["quote"], calls["slept"] = 0, []
    monkeypatch.setattr(factory, "build_broker", lambda cfg, day: ThrottledBroker(2))
    s.healthcheck_job()
    assert calls["quote"] == 2 and calls["slept"] == [3]
    assert alerts[-1].startswith("✅") and "throttled" in alerts[-1]
    assert "FAILED" not in alerts[-1]

    # 3) A non-rate-limit quote failure must still alert FAILED (no retry).
    calls["quote"], calls["slept"] = 0, []

    class BrokenBroker:
        def quote(self, symbols):
            calls["quote"] += 1
            raise RuntimeError("connection reset")

    monkeypatch.setattr(factory, "build_broker", lambda cfg, day: BrokenBroker())
    s.healthcheck_job()
    assert calls["quote"] == 1 and calls["slept"] == []
    assert "FAILED" in alerts[-1]
