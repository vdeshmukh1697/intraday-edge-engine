"""Dependency-light tick recorder (PLAN B-tick).

Captures the raw tick stream so we can later replay/inspect microstructure (spread, last
price, traded volume) offline. It is deliberately decoupled from the live feed: you feed it
:class:`~signal_engine.domain.models.Tick` objects (plus an optional last-trade qty), it
buffers them, and flushes to a file under ``data/`` (gitignored). Nothing here connects to a
socket — the live feed wires it in via an OPTIONAL, default-OFF hook (see
:func:`signal_engine.brokers.dhan_ws.run_feed`'s ``tick_tap``), so recording never changes
the default runtime behaviour.

Storage
-------
One file per (symbol, date) under ``<root>/symbol=<SYM>/date=<YYYY-MM-DD>/ticks.<ext>``,
mirroring the bar archive layout. Parquet is used when pandas+pyarrow are importable
(columnar, compact); otherwise we fall back to plain CSV so the recorder works with zero
heavy deps. Appends are safe across flushes: parquet is read-modify-write per file, CSV is
opened in append mode with a header written only once.

Pure / unit-testable: construct with a ``root`` under a tmp dir, call :meth:`record` with
hand-built ticks, then :meth:`flush`/:meth:`close` and read the files back.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from signal_engine.domain.models import Tick

# Column order for both backends (kept identical so CSV and parquet round-trip the same).
COLUMNS = ["symbol", "ts", "ltp", "volume", "bid", "ask", "last_qty"]


def _pandas():
    """Return the pandas module if it (and a parquet engine) is importable, else None."""
    try:
        import pandas as pd  # noqa: F401

        # A parquet engine must exist for to_parquet/read_parquet to work.
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            try:
                import fastparquet  # noqa: F401
            except ImportError:
                return None
        return pd
    except ImportError:
        return None


class TickRecorder:
    """Buffers ticks and flushes them to per-symbol/day files under ``root``.

    Parameters
    ----------
    root:
        Directory under ``data/`` to write into (created lazily). Gitignored.
    fmt:
        ``"auto"`` (default) uses parquet when available else CSV; force with ``"parquet"``
        or ``"csv"``.
    flush_every:
        Auto-flush once this many ticks are buffered (0 disables auto-flush; call
        :meth:`flush` or :meth:`close` yourself). Default 500.
    """

    def __init__(self, root: str = "data/ticks", fmt: str = "auto", flush_every: int = 500):
        self.root = Path(root)
        self._pd = _pandas() if fmt in ("auto", "parquet") else None
        if fmt == "parquet" and self._pd is None:
            raise RuntimeError("parquet format requested but pandas+parquet engine unavailable")
        self.ext = "parquet" if self._pd is not None else "csv"
        self.flush_every = flush_every
        # Buffer keyed by (symbol, iso-date) so a flush writes one file per partition.
        self._buf: Dict[Tuple[str, str], List[dict]] = {}
        self._n = 0
        # Tracks CSV files we've already written a header to (this process).
        self._csv_started: set = set()

    def _row(self, tick: Tick, last_qty: Optional[int]) -> dict:
        return {
            "symbol": tick.symbol,
            "ts": tick.ts.isoformat(),
            "ltp": tick.ltp,
            "volume": tick.volume,
            "bid": tick.bid,
            "ask": tick.ask,
            "last_qty": last_qty,
        }

    def record(self, tick: Tick, last_qty: Optional[int] = None) -> None:
        """Buffer one tick. ``last_qty`` is the last-traded quantity if the feed packet carried
        it (Full mode); pass None when unavailable. Auto-flushes per ``flush_every``."""
        key = (tick.symbol, tick.ts.date().isoformat())
        self._buf.setdefault(key, []).append(self._row(tick, last_qty))
        self._n += 1
        if self.flush_every and self._n >= self.flush_every:
            self.flush()

    def _path(self, symbol: str, day: str) -> Path:
        return self.root / f"symbol={symbol}" / f"date={day}" / f"ticks.{self.ext}"

    def _flush_parquet(self, path: Path, rows: List[dict]) -> None:
        pd = self._pd
        new = pd.DataFrame(rows, columns=COLUMNS)
        if path.exists():
            old = pd.read_parquet(path)
            new = pd.concat([old, new], ignore_index=True)
        new.to_parquet(path, index=False)

    def _flush_csv(self, path: Path, rows: List[dict]) -> None:
        # Header written once per file; subsequent flushes (or an existing file) append only.
        write_header = not path.exists() and str(path) not in self._csv_started
        with path.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            if write_header:
                writer.writeheader()
                self._csv_started.add(str(path))
            writer.writerows(rows)

    def flush(self) -> List[Path]:
        """Write all buffered ticks to their files and clear the buffer. Returns paths written."""
        written: List[Path] = []
        for (symbol, day), rows in self._buf.items():
            if not rows:
                continue
            path = self._path(symbol, day)
            path.parent.mkdir(parents=True, exist_ok=True)
            if self._pd is not None:
                self._flush_parquet(path, rows)
            else:
                self._flush_csv(path, rows)
            written.append(path)
        self._buf.clear()
        self._n = 0
        return written

    def close(self) -> List[Path]:
        """Flush any remaining buffered ticks. Safe to call multiple times."""
        return self.flush()

    # Context-manager sugar so ``with TickRecorder(...) as rec:`` flushes on exit.
    def __enter__(self) -> "TickRecorder":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
