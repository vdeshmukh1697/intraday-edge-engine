"""Batched real NSE data fetch via Yahoo Finance (full-universe archive + scan).

Yahoo Finance is REST, not a streaming feed. It can serve the **whole NSE cash
universe** (~2,000 symbols) for a once-daily archive and an end-of-day scan, but it
**cannot** be polled every few minutes for 2,000 names without rate-limiting — that
case needs the paid Dhan sharded websocket (PLAN §3.3). So this module is built for
*batched, throttled, best-effort* sweeps: chunk the universe, fetch each chunk in one
``yf.download`` call (threaded), pause between chunks, and never let a failed chunk
abort the run. It logs how many symbols returned data so coverage is never silently
truncated.

All network access goes through an injectable ``batch_fetch`` callable so tests run
fully offline.
"""

from __future__ import annotations

import time as _time
from typing import Callable, Dict, List, Optional

import pytz

from signal_engine.obs.logging_setup import get_logger

IST = pytz.timezone("Asia/Kolkata")
log = get_logger(__name__)

# symbols, interval, period -> {plain_symbol: scanner-shaped DataFrame}
BatchFetch = Callable[[List[str], str, str], Dict[str, "object"]]


def _nse_ticker(symbol: str) -> str:
    s = symbol.upper().strip()
    return s if s.endswith(".NS") else f"{s}.NS"


def _normalize(symbol: str, df) -> Optional["object"]:
    """yfinance OHLCV frame -> scanner shape: IST 'ts' index + lowercase cols + symbol."""
    if df is None or len(df) == 0:
        return None
    out = df.rename(
        columns={"Open": "open", "High": "high", "Low": "low",
                 "Close": "close", "Volume": "volume"}
    )
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in out.columns]
    out = out[keep].dropna(subset=[c for c in ("open", "high", "low", "close") if c in keep])
    if out.empty:
        return None
    idx = out.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out.index = idx.tz_convert(IST)
    out.index.name = "ts"
    out["symbol"] = symbol.upper()
    return out


def _default_batch_fetch(symbols: List[str], interval: str, period: str) -> Dict[str, "object"]:
    """Real network fetch: one threaded ``yf.download`` for the whole chunk."""
    import yfinance as yf

    tickers = [_nse_ticker(s) for s in symbols]
    raw = yf.download(
        tickers, period=period, interval=interval, group_by="ticker",
        threads=True, progress=False, auto_adjust=True,
    )
    out: Dict[str, "object"] = {}
    # yf.download returns a single-level frame for one ticker, MultiIndex for many.
    single = len(tickers) == 1
    for sym, tk in zip(symbols, tickers):
        try:
            sub = raw if single else (raw[tk] if tk in raw.columns.get_level_values(0) else None)
            norm = _normalize(sym, sub)
            if norm is not None:
                out[sym.upper()] = norm
        except Exception:  # noqa: BLE001 - skip a bad symbol, keep the chunk
            continue
    return out


def fetch_intraday(
    symbols: List[str],
    interval: str = "1m",
    period: str = "1d",
    chunk_size: int = 120,
    pause_s: float = 0.6,
    batch_fetch: Optional[BatchFetch] = None,
) -> Dict[str, "object"]:
    """Fetch intraday bars for ``symbols`` in throttled chunks (best-effort).

    Returns ``{symbol: DataFrame}`` only for symbols that returned usable data.
    Failed chunks are logged and skipped — coverage is reported, never silently cut.
    """
    fetch = batch_fetch or _default_batch_fetch
    out: Dict[str, "object"] = {}
    n = len(symbols)
    chunks = [symbols[i:i + chunk_size] for i in range(0, n, chunk_size)]
    for ci, chunk in enumerate(chunks):
        try:
            got = fetch(chunk, interval, period)
            out.update(got)
        except Exception as exc:  # noqa: BLE001
            log.warning("intraday chunk %d/%d failed (%d syms): %s",
                        ci + 1, len(chunks), len(chunk), exc)
        if pause_s and ci < len(chunks) - 1:
            _time.sleep(pause_s)
    log.info("fetch_intraday: %d/%d symbols returned %s bars", len(out), n, interval)
    return out


def fetch_daily_metrics(
    symbols: List[str],
    period: str = "1mo",
    chunk_size: int = 200,
    pause_s: float = 0.5,
    batch_fetch: Optional[BatchFetch] = None,
) -> Dict[str, Dict[str, float]]:
    """Cheap per-symbol liquidity snapshot from daily bars (for the static screen).

    Returns ``{symbol: {"last_price", "avg_daily_turnover_cr"}}``. Turnover is the mean
    of ``close * volume`` over the window, in ₹ crore (1 cr = 1e7). This is one batched
    daily-bar sweep — far cheaper than pulling intraday history for all ~2,000 names.
    """
    fetch = batch_fetch or _default_batch_fetch
    frames = fetch_intraday(
        symbols, interval="1d", period=period,
        chunk_size=chunk_size, pause_s=pause_s, batch_fetch=fetch,
    )
    metrics: Dict[str, Dict[str, float]] = {}
    for sym, df in frames.items():
        try:
            last_price = float(df["close"].iloc[-1])
            turnover_cr = float((df["close"] * df["volume"]).mean()) / 1e7
            metrics[sym] = {
                "last_price": last_price,
                "avg_daily_turnover_cr": turnover_cr,
            }
        except Exception:  # noqa: BLE001
            continue
    return metrics
