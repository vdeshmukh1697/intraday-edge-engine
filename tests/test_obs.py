"""Tests for observability + resilience utilities (PLAN §9.3)."""

from datetime import datetime

import pytest
import pytz

from signal_engine.market.clock import FakeClock
from signal_engine.obs.backoff import ReconnectPolicy
from signal_engine.obs.freshness import FreshnessGuard
from signal_engine.obs.logging_setup import configure_logging, get_logger

IST = pytz.timezone("Asia/Kolkata")


def test_freshness_no_data_is_stale():
    g = FreshnessGuard(max_staleness_seconds=5.0, clock=FakeClock(datetime(2025, 6, 23, 10, 0)))
    assert g.is_stale() is True          # nothing received yet -> fail safe
    assert g.seconds_since() is None


def test_freshness_fresh_then_stale():
    clock = FakeClock(datetime(2025, 6, 23, 10, 0, 0))
    g = FreshnessGuard(max_staleness_seconds=5.0, clock=clock)
    g.mark(clock.now())
    assert g.is_stale() is False
    clock.advance(3)
    assert g.is_stale() is False         # within threshold
    clock.advance(5)                     # now 8s since data
    assert g.is_stale() is True
    assert g.seconds_since() == pytest.approx(8.0)


def test_reconnect_backoff_grows_and_caps():
    p = ReconnectPolicy(base_seconds=1.0, factor=2.0, max_seconds=10.0, max_attempts=0)
    assert p.delay(1) == 1.0
    assert p.delay(2) == 2.0
    assert p.delay(3) == 4.0
    assert p.delay(4) == 8.0
    assert p.delay(5) == 10.0            # 16 capped to 10
    assert p.delay(10) == 10.0
    assert p.should_retry(100) is True   # unlimited


def test_reconnect_attempt_limit():
    p = ReconnectPolicy(max_attempts=3)
    assert p.should_retry(3) is True
    assert p.should_retry(4) is False
    with pytest.raises(ValueError):
        p.delay(0)


def test_reconnect_invalid_params():
    with pytest.raises(ValueError):
        ReconnectPolicy(base_seconds=0)
    with pytest.raises(ValueError):
        ReconnectPolicy(factor=0.5)


def test_logging_configures_and_emits(capsys):
    import io

    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    get_logger("test").info("hello world")
    assert "hello world" in stream.getvalue()
    assert "signal_engine.test" in stream.getvalue()
