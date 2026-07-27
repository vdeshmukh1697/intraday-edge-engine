"""Self-contained SQLite store for the microstructure shadow signal.

Deliberately NOT part of SignalRepository: this is a research shadow feature, and keeping
its own table + own migration means it can never collide with the trading tables or block
the live writer. Uses the SAME database file (WAL) but its own `microstructure_signals`
table. One row per (symbol, bar_ts). The `next_ret_pct` / `dir_correct` columns are filled
later by the scorer once the following bar is known.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from signal_engine.microstructure.features import BarSignal

DDL = """
CREATE TABLE IF NOT EXISTS microstructure_signals (
    symbol TEXT, bar_ts TEXT,
    n_ticks INTEGER, bar_ret_pct REAL,
    cvd REAL, signed_vol_frac REAL, ob_imbalance REAL, spread_bps REAL,
    dir_score REAL,
    next_ret_pct REAL, dir_correct INTEGER,   -- filled by the scorer
    run_id TEXT, created_ts TEXT,
    PRIMARY KEY (symbol, bar_ts)
)
"""


def _path_from_url(db_url: str) -> str:
    return db_url[len("sqlite:///"):] if db_url.startswith("sqlite:///") else db_url


class MicrostructureStore:
    def __init__(self, db_url: str = "sqlite:///data/signal_engine.sqlite3",
                 run_id: Optional[str] = None, read_only: bool = False):
        self.conn = sqlite3.connect(_path_from_url(db_url), timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self.run_id = run_id
        if read_only:
            # Dashboard reads during market hours: skip the CREATE TABLE (write lock) so a
            # SELECT never contends with the live collector's writes. If the table is missing
            # (fresh DB) create it once — there is no live writer to contend with then.
            exists = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='microstructure_signals'"
            ).fetchone()
            if exists is None:
                self.conn.execute(DDL)
                self.conn.commit()
            return
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self.conn.execute(DDL)
        self.conn.commit()

    def save(self, sig: BarSignal) -> None:
        from datetime import datetime as _dt

        self.conn.execute(
            """INSERT OR REPLACE INTO microstructure_signals
               (symbol, bar_ts, n_ticks, bar_ret_pct, cvd, signed_vol_frac, ob_imbalance,
                spread_bps, dir_score, run_id, created_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sig.symbol, sig.bar_ts.isoformat(), sig.n_ticks, sig.bar_ret_pct, sig.cvd,
             sig.signed_vol_frac, sig.ob_imbalance, sig.spread_bps, sig.dir_score,
             self.run_id, _dt.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def fetch_day(self, day: str) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM microstructure_signals WHERE date(bar_ts) = ? ORDER BY symbol, bar_ts",
            (day,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_outcome(self, symbol: str, bar_ts: str, next_ret_pct: float,
                    dir_correct: Optional[int]) -> None:
        self.conn.execute(
            """UPDATE microstructure_signals SET next_ret_pct = ?, dir_correct = ?
               WHERE symbol = ? AND bar_ts = ?""",
            (next_ret_pct, dir_correct, symbol, bar_ts),
        )
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass
