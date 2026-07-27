"""Serializer contract tests (the JSON shapes the dashboard consumes)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytz

from signal_engine.api.serializers import chart_to_json

IST = pytz.timezone("Asia/Kolkata")


def _session_df(n: int = 5) -> pd.DataFrame:
    start = IST.localize(datetime(2026, 7, 2, 9, 15))
    idx = [start + timedelta(minutes=i) for i in range(n)]
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1000 * (i + 1) for i in range(n)],
        },
        index=pd.DatetimeIndex(idx),
    )


def test_chart_to_json_includes_volume():
    out = chart_to_json("RELIANCE", _session_df(), {})
    candles = out["candles"]
    assert len(candles) == 5
    assert all("volume" in c for c in candles)
    assert candles[0]["volume"] == 1000
    assert candles[-1]["volume"] == 5000
