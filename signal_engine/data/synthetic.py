"""Realistic synthetic intraday data so the engine runs with no live feed (ground rule).

``generate_session`` builds a day of 1-minute OHLCV bars with intraday volatility
seasonality (U-shape) and a configurable regime. ``bars_to_ticks`` explodes each bar
into intra-minute ticks (with monotonically increasing cumulative volume) so the real
tick -> bar aggregation path is exercised.

Deterministic when ``seed`` is given.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import List

import numpy as np
import pandas as pd
import pytz

from signal_engine.domain.models import Tick

IST = pytz.timezone("Asia/Kolkata")
_SESSION_MINUTES = 375  # 09:15 .. 15:29 inclusive (375 one-minute bars)


def generate_session(
    symbol: str,
    day: date,
    start_price: float = 1000.0,
    seed: int = None,
    regime: str = "choppy",
    base_vol: float = 0.0012,
    drift_per_min: float = 0.0,
) -> pd.DataFrame:
    """Generate one trading session of 1-minute bars.

    regime: "trend_up" | "trend_down" | "choppy". Adds a deterministic drift so
    strategies have something to fire on in tests.
    """
    rng = np.random.default_rng(seed)
    n = _SESSION_MINUTES

    # Intraday volatility seasonality: higher at open/close (U-shape).
    x = np.linspace(0, 1, n)
    seasonal = 0.6 + 0.8 * ((x - 0.5) ** 2) * 4  # ~1.4 at edges, ~0.6 mid
    vol = base_vol * seasonal

    regime_drift = {
        "trend_up": 0.00035,
        "trend_down": -0.00035,
        "choppy": 0.0,
    }.get(regime, 0.0) + drift_per_min

    returns = rng.normal(loc=regime_drift, scale=vol)
    close = start_price * np.cumprod(1.0 + returns)

    # Build OHLC around the close path.
    open_ = np.empty(n)
    open_[0] = start_price
    open_[1:] = close[:-1]
    intrabar = np.abs(rng.normal(0, vol * start_price * 0.5, n)) + start_price * 1e-4
    high = np.maximum(open_, close) + intrabar
    low = np.minimum(open_, close) - intrabar
    low = np.maximum(low, 0.01)

    # Volume: U-shape too, with noise; integers.
    base_volume = 20000 * seasonal
    volume = (base_volume * (0.6 + 0.8 * rng.random(n))).astype(int) + 1

    # Timestamps: 09:15 .. for n minutes.
    start_dt = IST.localize(datetime.combine(day, time(9, 15)))
    idx = pd.date_range(start=start_dt, periods=n, freq="1min")

    df = pd.DataFrame(
        {
            "open": np.round(open_, 2),
            "high": np.round(high, 2),
            "low": np.round(low, 2),
            "close": np.round(close, 2),
            "volume": volume,
        },
        index=idx,
    )
    df.index.name = "ts"
    df["symbol"] = symbol
    return df


def bars_to_ticks(df: pd.DataFrame, symbol: str = None) -> List[Tick]:
    """Explode 1-minute bars into intra-minute ticks with cumulative day volume.

    Four ticks per bar (open, an extreme, the other extreme, close) at seconds
    0/15/30/45, so a BarAggregator rebuilds the same OHLC. Cumulative volume is
    monotonically increasing and resets to 0 at the start of the session.
    """
    sym = symbol or (df["symbol"].iloc[0] if "symbol" in df.columns else "SYM")
    ticks: List[Tick] = []
    cum_vol = 0
    for ts, row in df.iterrows():
        o, h, low_, c, v = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            int(row["volume"]),
        )
        # Order the two extremes so the path is open -> (extreme) -> (extreme) -> close.
        if c >= o:
            mid1, mid2 = low_, h  # dip then push to high
        else:
            mid1, mid2 = h, low_  # pop then fall to low
        prices = [o, mid1, mid2, c]
        # Spread the bar's volume across the 4 ticks (cumulative).
        per = [0.25, 0.25, 0.25, 0.25]
        running = cum_vol
        for i, (price, frac) in enumerate(zip(prices, per)):
            running += int(round(v * frac))
            if i == len(prices) - 1:
                running = cum_vol + v  # ensure exact total at bar end
            tick_ts = ts.to_pydatetime() + pd.Timedelta(seconds=15 * i).to_pytimedelta()
            ticks.append(Tick(symbol=sym, ts=tick_ts, ltp=round(price, 2), volume=running))
        cum_vol += v
    return ticks
