"""Bulk historical backfill from Dhan (the 5-year minute corpus, PLAN §3.5).

Dhan's intraday endpoint serves up to 5 years of minute bars but caps each request at a
90-day window, so we walk backwards in 90-day chunks per symbol and write a consolidated
Parquet file per symbol/year. Best-effort and resumable: a symbol/year already on disk is
skipped, and a failed symbol never aborts the run.

This is a one-shot corpus builder (run overnight), distinct from the nightly ``archive_job``
which captures the current day's bars. Network goes through the injected ``DhanBroker`` so
the loop logic is testable without hitting the API.
"""

from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional

import pandas as pd
import pytz

from signal_engine.obs.logging_setup import get_logger
from signal_engine.storage.bars import ParquetBarStore

IST = pytz.timezone("Asia/Kolkata")
log = get_logger(__name__)


def _bars_to_df(bars) -> pd.DataFrame:
    df = pd.DataFrame(
        [{"ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
          "close": b.close, "volume": b.volume} for b in bars]
    )
    if df.empty:
        return df
    return df.set_index("ts").sort_index()


def backfill_symbol(
    broker,
    store: ParquetBarStore,
    symbol: str,
    years: int = 5,
    end: Optional[date] = None,
    window_days: int = 90,
    pause_s: float = 0.22,           # ~4.5 req/s, under the 5 req/s Data-API limit
    sleep_fn: Callable[[float], None] = _time.sleep,
    skip_existing: bool = True,
) -> int:
    """Fetch ``years`` of 1-min bars for one symbol and store them per year. Returns bars saved."""
    end = end or datetime.now(IST).date()
    start_floor = end - timedelta(days=int(years * 365.25))
    by_year: dict = {}

    win_end = end
    while win_end > start_floor:
        win_start = max(start_floor, win_end - timedelta(days=window_days))
        try:
            bars = broker.historical(
                symbol, "1m",
                IST.localize(datetime.combine(win_start, datetime.min.time())),
                IST.localize(datetime.combine(win_end, datetime.min.time())),
            )
            for b in bars:
                by_year.setdefault(b.ts.year, []).append(b)
        except Exception as exc:  # noqa: BLE001 - skip a bad window, keep going
            log.warning("backfill %s [%s..%s] failed: %s", symbol, win_start, win_end, exc)
        win_end = win_start
        if pause_s:
            sleep_fn(pause_s)

    saved = 0
    for year, bars in by_year.items():
        if skip_existing and store._hist_path(symbol, year).exists():
            continue
        df = _bars_to_df(bars)
        if not df.empty:
            store.save_symbol_year(symbol, year, df)
            saved += len(df)
    log.info("backfill %s: saved %d bars across %d years", symbol, saved, len(by_year))
    return saved


def backfill_universe(
    broker,
    store: ParquetBarStore,
    symbols: List[str],
    years: int = 5,
    end: Optional[date] = None,
    progress_every: int = 25,
    **kw,
) -> dict:
    """Backfill many symbols. Best-effort; logs progress. Returns {symbol: bars_saved}."""
    out: dict = {}
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        try:
            out[sym] = backfill_symbol(broker, store, sym, years=years, end=end, **kw)
        except Exception as exc:  # noqa: BLE001
            log.error("backfill %s aborted: %s", sym, exc)
            out[sym] = 0
        if i % progress_every == 0 or i == total:
            done = sum(1 for v in out.values() if v)
            log.info("backfill progress: %d/%d symbols (%d with data, %s bars so far)",
                     i, total, done, f"{sum(out.values()):,}")
    return out
