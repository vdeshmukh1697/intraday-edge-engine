"""Tests for the batched Yahoo fetcher — fully offline via injected batch_fetch."""

from __future__ import annotations

import pandas as pd
import pytz

from signal_engine.data.yahoo_batch import (
    _normalize,
    fetch_daily_metrics,
    fetch_intraday,
)

IST = pytz.timezone("Asia/Kolkata")


def _yf_frame(n=5, base=100.0, vol=10000):
    """A yfinance-shaped OHLCV frame: naive UTC index, capitalized columns."""
    idx = pd.date_range("2025-06-23 03:45", periods=n, freq="1min")  # naive (UTC)
    return pd.DataFrame(
        {
            "Open": [base + i for i in range(n)],
            "High": [base + i + 0.5 for i in range(n)],
            "Low": [base + i - 0.5 for i in range(n)],
            "Close": [base + i + 0.2 for i in range(n)],
            "Volume": [vol] * n,
        },
        index=idx,
    )


def test_normalize_to_scanner_shape():
    out = _normalize("reliance", _yf_frame())
    assert list(out.columns) == ["open", "high", "low", "close", "volume", "symbol"]
    assert out.index.name == "ts"
    assert str(out.index.tz) == "Asia/Kolkata"
    assert (out["symbol"] == "RELIANCE").all()


def test_normalize_empty_returns_none():
    assert _normalize("X", None) is None
    assert _normalize("X", pd.DataFrame()) is None


def test_fetch_intraday_chunks_and_merges():
    calls = []

    def fake(symbols, interval, period):
        calls.append(list(symbols))
        return {s.upper(): _normalize(s, _yf_frame()) for s in symbols}

    syms = [f"S{i}" for i in range(250)]
    out = fetch_intraday(syms, chunk_size=100, pause_s=0, batch_fetch=fake)
    assert len(out) == 250
    assert [len(c) for c in calls] == [100, 100, 50]  # chunked


def test_fetch_intraday_best_effort_on_chunk_failure():
    def fake(symbols, interval, period):
        if "BAD" in symbols:
            raise RuntimeError("rate limited")
        return {s.upper(): _normalize(s, _yf_frame()) for s in symbols}

    out = fetch_intraday(["A", "B", "BAD", "C"], chunk_size=2, pause_s=0, batch_fetch=fake)
    # First chunk [A,B] succeeds; second chunk [BAD,C] raises and is skipped.
    assert set(out) == {"A", "B"}


def test_fetch_daily_metrics_computes_turnover():
    def fake(symbols, interval, period):
        assert interval == "1d"
        return {s.upper(): _normalize(s, _yf_frame(n=5, base=100.0, vol=10000)) for s in symbols}

    m = fetch_daily_metrics(["RELIANCE"], pause_s=0, batch_fetch=fake)
    assert "RELIANCE" in m
    # last close = 100 + 4 + 0.2 = 104.2 ; turnover_cr = mean(close*vol)/1e7
    assert round(m["RELIANCE"]["last_price"], 1) == 104.2
    assert m["RELIANCE"]["avg_daily_turnover_cr"] > 0
