"""Parquet bar archive (PLAN §3.5): partitioned by symbol/date, columnar, free."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Optional

import pandas as pd

from signal_engine.domain.models import Bar


class ParquetBarStore:
    def __init__(self, root: str = "data/parquet"):
        self.root = Path(root)

    def _path(self, symbol: str, day: date) -> Path:
        return self.root / f"symbol={symbol}" / f"date={day.isoformat()}" / "bars.parquet"

    def save_session(self, symbol: str, day: date, df: pd.DataFrame) -> Path:
        path = self._path(symbol, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
        return path

    def load_session(self, symbol: str, day: date) -> Optional[pd.DataFrame]:
        path = self._path(symbol, day)
        if not path.exists():
            return None
        return pd.read_parquet(path)

    # --- consolidated history (for the 5y backfill corpus) -----------------
    # One file per symbol/year instead of per symbol/day: ~10k files & a few GB for the
    # whole NSE 5y minute corpus, vs ~2.5M tiny files (30x the bytes) for per-session.
    def _hist_path(self, symbol: str, year: int) -> Path:
        return self.root / f"symbol={symbol}" / f"year={year}" / "bars.parquet"

    def save_symbol_year(self, symbol: str, year: int, df: pd.DataFrame) -> Path:
        path = self._hist_path(symbol, year)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
        return path

    def load_symbol_history(self, symbol: str) -> Optional[pd.DataFrame]:
        """Concatenate all archived years for a symbol (sorted by time). None if absent."""
        base = self.root / f"symbol={symbol}"
        files = sorted(base.glob("year=*/bars.parquet"))
        if not files:
            return None
        frames = [pd.read_parquet(f) for f in files]
        return pd.concat(frames).sort_index()

    def save_bars(self, bars: List[Bar]) -> Optional[Path]:
        if not bars:
            return None
        symbol = bars[0].symbol
        day = bars[0].ts.date()
        df = pd.DataFrame(
            [
                {
                    "ts": b.ts,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
            ]
        ).set_index("ts")
        return self.save_session(symbol, day, df)
