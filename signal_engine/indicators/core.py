"""Pure, deterministic technical indicator functions.

All functions operate on pandas Series / DataFrame and return pandas Series
(or DataFrame / tuple where noted). No I/O, no network, numpy + pandas only,
Python 3.9 compatible. Functions are stateless and side-effect free.

Conventions
-----------
* OHLCV DataFrames use lowercase columns: ``open/high/low/close/volume``.
* Wilder-smoothed indicators (RSI, ATR, ADX) use ``ewm(alpha=1/period,
  adjust=False)`` which is equivalent to Wilder's recursive smoothing.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (span=period, no bias adjustment)."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index.

    Uses Wilder smoothing (``ewm(alpha=1/period)``) of average gains/losses.
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss == 0 (only gains), RS -> inf -> RSI -> 100.
    out = out.where(avg_loss != 0.0, 100.0)
    # When both avg_gain and avg_loss are 0 (flat), RSI is undefined -> 50.
    out = out.mask((avg_gain == 0.0) & (avg_loss == 0.0), 50.0)
    # The very first row has NaN delta -> keep RSI NaN there.
    out.iloc[0] = np.nan
    return out


def _true_range(df: pd.DataFrame) -> pd.Series:
    """True range = max(high-low, |high-prev_close|, |low-prev_close|)."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    # First bar has no prev_close -> TR is just high-low.
    tr.iloc[0] = (high - low).iloc[0]
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range, Wilder-smoothed."""
    tr = _true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average Directional Index from +DI / -DI."""
    high = df["high"]
    low = df["low"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    tr = _true_range(df)
    atr_s = tr.ewm(alpha=1.0 / period, adjust=False).mean()

    plus_dm_s = plus_dm.ewm(alpha=1.0 / period, adjust=False).mean()
    minus_dm_s = minus_dm.ewm(alpha=1.0 / period, adjust=False).mean()

    plus_di = 100.0 * (plus_dm_s / atr_s)
    minus_di = 100.0 * (minus_dm_s / atr_s)

    di_sum = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    dx = dx.where(di_sum != 0.0, 0.0)

    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session cumulative VWAP. Assumes ``df`` is a single trading session."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"]
    cum_pv = (typical * vol).cumsum()
    cum_vol = vol.cumsum()
    return cum_pv / cum_vol


def rvol(volume: pd.Series, lookback: int = 20) -> pd.Series:
    """Relative volume = volume / mean of the PRIOR ``lookback`` bars.

    The current bar is excluded from the average (the rolling window is
    shifted by one).
    """
    prior_mean = volume.rolling(window=lookback).mean().shift(1)
    return volume / prior_mean


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD line, signal line, and histogram."""
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "hist": hist}, index=series.index
    )


def supertrend(
    df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
) -> pd.DataFrame:
    """Supertrend line and direction (+1 uptrend / -1 downtrend)."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    atr_s = atr(df, period)
    hl2 = (high + low) / 2.0

    upper_basic = hl2 + multiplier * atr_s
    lower_basic = hl2 - multiplier * atr_s

    n = len(df)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.full(n, np.nan)

    close_v = close.to_numpy()
    ub = upper_basic.to_numpy()
    lb = lower_basic.to_numpy()

    for i in range(n):
        if i == 0:
            final_upper[i] = ub[i]
            final_lower[i] = lb[i]
            # Seed direction: bullish if close above lower band.
            direction[i] = 1.0
            st[i] = final_lower[i]
            continue

        # Final upper band.
        if (ub[i] < final_upper[i - 1]) or (close_v[i - 1] > final_upper[i - 1]):
            final_upper[i] = ub[i]
        else:
            final_upper[i] = final_upper[i - 1]

        # Final lower band.
        if (lb[i] > final_lower[i - 1]) or (close_v[i - 1] < final_lower[i - 1]):
            final_lower[i] = lb[i]
        else:
            final_lower[i] = final_lower[i - 1]

        # Determine direction based on prior supertrend line.
        prev_dir = direction[i - 1]
        if prev_dir == 1.0:
            # Was uptrend (line = lower band); flip down if close breaks below.
            if close_v[i] <= final_lower[i]:
                direction[i] = -1.0
            else:
                direction[i] = 1.0
        else:
            # Was downtrend (line = upper band); flip up if close breaks above.
            if close_v[i] >= final_upper[i]:
                direction[i] = 1.0
            else:
                direction[i] = -1.0

        st[i] = final_lower[i] if direction[i] == 1.0 else final_upper[i]

    return pd.DataFrame(
        {
            "supertrend": pd.Series(st, index=df.index),
            "direction": pd.Series(direction, index=df.index),
        }
    )


def opening_range(df: pd.DataFrame, minutes: int = 15) -> Tuple[float, float]:
    """High / low of the first ``minutes`` bars (assumes 1-min bars).

    Returns ``(orb_high, orb_low)`` as floats, or ``(nan, nan)`` if there are
    fewer than ``minutes`` rows.
    """
    if len(df) < minutes:
        return (float("nan"), float("nan"))
    window = df.iloc[:minutes]
    return (float(window["high"].max()), float(window["low"].min()))


def _frac_diff_weights(d: float, size: int) -> np.ndarray:
    """Binomial expansion weights for fractional differencing of order ``d``.

    From López de Prado, *Advances in Financial Machine Learning* (2018), §5.4.
    The fractional-difference operator ``(1 - B)^d`` expands as an infinite
    binomial series whose weights obey the recurrence

        w[0] = 1,  w[k] = -w[k-1] * (d - k + 1) / k    for k >= 1

    Returned newest-first: ``w[0]`` multiplies the most recent observation and
    ``w[size-1]`` the oldest. Weights decay (for non-integer d they never reach
    zero, hence the truncation to ``size`` terms — the expanding-window variant).
    """
    w = [1.0]
    for k in range(1, size):
        w.append(-w[-1] * (d - k + 1) / k)
    return np.asarray(w, dtype=float)


def frac_diff(series: pd.Series, d: float = 0.5) -> pd.Series:
    """Fractionally differentiated series (López de Prado expanding window).

    Applies the fractional-difference operator ``(1 - B)^d`` for real ``d`` via
    its binomial-weight expansion (see ``_frac_diff_weights``). For each row ``t``
    the output is the dot product of the binomial weights with *all* prior
    observations up to and including ``t`` (the EXPANDING-window form of §5.4):

        fd[t] = sum_{k=0..t} w[k] * x[t-k]

    This keeps far more memory than an integer first difference (``d=1``) while
    still removing the unit root, so the result is (near-)stationary yet retains
    predictive level information. ``d=0`` returns the series unchanged; ``d=1``
    reduces to the ordinary first difference.

    The expansion is point-in-time and causal: ``fd[t]`` uses only ``x[0..t]``,
    so it is safe to compute over a growing live history with no lookahead. NaNs
    in the input propagate to any output term that depends on them.
    """
    x = series.to_numpy(dtype=float)
    n = len(x)
    if n == 0:
        return pd.Series([], index=series.index, dtype=float)
    weights = _frac_diff_weights(d, n)
    out = np.full(n, np.nan, dtype=float)
    for t in range(n):
        # Newest-first weights dotted with the window x[0..t] reversed (newest first).
        out[t] = float(np.dot(weights[: t + 1], x[t::-1]))
    return pd.Series(out, index=series.index)
