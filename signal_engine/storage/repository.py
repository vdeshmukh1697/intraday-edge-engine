"""SQLite repository for surfaced trade plans and closed paper trades (PLAN §6.5).

SQLite needs no server, so the MVP runs and tests anywhere. Postgres is the production
target (PLAN §3.6); this class is the interface both share. ``:memory:`` is supported
for tests.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from signal_engine.domain.models import PaperPosition, TradePlan


def _default_run_id() -> str:
    """A stable-per-process run id: process start epoch + pid.

    Stable for the lifetime of one process (so every row a single run writes shares it) and
    distinct across runs, which is what makes cross-run rows separable (PLAN §3 "also").
    """
    return f"{int(time.time())}-{os.getpid()}"


def _now_iso() -> str:
    """Wall-clock IST timestamp for 'last updated' fields (the dashboard shows it to the user)."""
    import datetime as _dt

    import pytz

    return _dt.datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()


def _path_from_url(db_url: str) -> str:
    """Accept 'sqlite:///path', 'sqlite:///:memory:' or a bare path."""
    if db_url.startswith("sqlite:///"):
        return db_url[len("sqlite:///"):]
    return db_url


class SignalRepository:
    def __init__(self, db_url: str = "sqlite:///data/signal_engine.sqlite3",
                 run_id: Optional[str] = None):
        path = _path_from_url(db_url)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        # A stable-per-process tag for every row this repo writes, unless a call overrides it.
        self.run_id = run_id or _default_run_id()
        self.init_db()

    def init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, ts TEXT, direction TEXT, strategy TEXT,
                entry REAL, stop_loss REAL, stop_pct REAL,
                targets TEXT, target_pcts TEXT, expected_move_pct REAL,
                risk_reward REAL, cost_to_break_even_pct REAL, confidence REAL,
                reasons TEXT, time_validity TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trades (
                id TEXT PRIMARY KEY,
                symbol TEXT, strategy TEXT, direction TEXT,
                entry_fill REAL, entry_ts TEXT, exit_fill REAL, exit_ts TEXT,
                exit_reason TEXT, pnl_pct_net REAL, r_multiple REAL,
                hold_minutes REAL, won INTEGER, confidence REAL,
                stop_loss REAL, target REAL
            )
            """
        )
        # Currently-open paper positions: written on entry, updated each bar with the live mark
        # (so the dashboard can show unrealized P&L), and deleted on close. Lets the read-only API
        # surface live entries the moment they happen — closed trades alone never showed entries.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS open_positions (
                id TEXT PRIMARY KEY,
                symbol TEXT, strategy TEXT, direction TEXT,
                entry_fill REAL, entry_ts TEXT,
                stop_loss REAL, stop_pct REAL, target REAL, target_pct REAL,
                confidence REAL, expected_move_pct REAL, risk_reward REAL,
                last_price REAL, unrealized_pnl_pct REAL, updated_ts TEXT, run_id TEXT
            )
            """
        )
        # Single-row liveness beacon: the live loop upserts it each minute so the dashboard can
        # show "feed alive, last update HH:MM" and an open/closed-today count without guessing.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS live_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                updated_ts TEXT, bar_ts TEXT, bars_processed INTEGER,
                open_count INTEGER, closed_today INTEGER, watching INTEGER, run_id TEXT
            )
            """
        )
        # Forward-compatible migration: add columns that predate this schema. ALTER TABLE ...
        # ADD COLUMN is safe on an existing populated DB (existing rows get NULL).
        existing = {r["name"] for r in cur.execute("PRAGMA table_info(paper_trades)")}
        for col in ("stop_loss", "target"):
            if col not in existing:
                cur.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} REAL")
        # run_id makes cross-run rows distinguishable; added to BOTH tables (PLAN §3 "also").
        for table in ("trade_plans", "paper_trades"):
            cols = {r["name"] for r in cur.execute(f"PRAGMA table_info({table})")}
            if "run_id" not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN run_id TEXT")
        self.conn.commit()

    def save_plan(self, plan: TradePlan, run_id: Optional[str] = None) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO trade_plans
               (symbol, ts, direction, strategy, entry, stop_loss, stop_pct,
                targets, target_pcts, expected_move_pct, risk_reward,
                cost_to_break_even_pct, confidence, reasons, time_validity, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                plan.symbol, plan.ts.isoformat(), plan.direction.value, plan.strategy,
                plan.entry, plan.stop_loss, plan.stop_pct,
                json.dumps(plan.targets), json.dumps(plan.target_pcts),
                plan.expected_move_pct, plan.risk_reward, plan.cost_to_break_even_pct,
                plan.confidence, json.dumps(plan.reasons),
                plan.time_validity.isoformat() if plan.time_validity else None,
                run_id or self.run_id,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_position(self, pos: PaperPosition, run_id: Optional[str] = None) -> None:
        cur = self.conn.cursor()
        target = pos.plan.t1 if pos.plan.targets else None
        cur.execute(
            """INSERT OR REPLACE INTO paper_trades
               (id, symbol, strategy, direction, entry_fill, entry_ts, exit_fill,
                exit_ts, exit_reason, pnl_pct_net, r_multiple, hold_minutes, won, confidence,
                stop_loss, target, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pos.id, pos.symbol, pos.plan.strategy, pos.direction.value,
                pos.entry_fill, pos.entry_ts.isoformat() if pos.entry_ts else None,
                pos.exit_fill, pos.exit_ts.isoformat() if pos.exit_ts else None,
                pos.exit_reason.value, pos.pnl_pct_net, pos.r_multiple,
                pos.hold_minutes, int(pos.won) if pos.won is not None else None,
                pos.plan.confidence, pos.plan.stop_loss, target,
                run_id or self.run_id,
            ),
        )
        self.conn.commit()

    def save_open_position(self, pos: PaperPosition, last_price: Optional[float] = None,
                           unrealized_pnl_pct: Optional[float] = None,
                           run_id: Optional[str] = None) -> None:
        """Upsert a currently-open position so the dashboard can show live entries + unrealized
        P&L. Called on entry and again each bar with a fresh mark; ``remove_open_position`` on
        close. ``last_price``/``unrealized_pnl_pct`` are best-effort (None until first mark)."""
        plan = pos.plan
        target = plan.t1 if plan.targets else None
        target_pct = plan.target_pcts[0] if plan.target_pcts else None
        cur = self.conn.cursor()
        cur.execute(
            """INSERT OR REPLACE INTO open_positions
               (id, symbol, strategy, direction, entry_fill, entry_ts, stop_loss, stop_pct,
                target, target_pct, confidence, expected_move_pct, risk_reward,
                last_price, unrealized_pnl_pct, updated_ts, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pos.id, pos.symbol, plan.strategy, pos.direction.value,
                pos.entry_fill, pos.entry_ts.isoformat() if pos.entry_ts else None,
                plan.stop_loss, plan.stop_pct, target, target_pct, plan.confidence,
                plan.expected_move_pct, plan.risk_reward, last_price, unrealized_pnl_pct,
                _now_iso(), run_id or self.run_id,
            ),
        )
        self.conn.commit()

    def remove_open_position(self, pos_id: str) -> None:
        self.conn.execute("DELETE FROM open_positions WHERE id = ?", (pos_id,))
        self.conn.commit()

    def clear_open_positions(self) -> None:
        """Drop all open-position rows (called at live warm-start: any rows left over are stale
        from a prior process, and warm-start re-derives the true current set)."""
        self.conn.execute("DELETE FROM open_positions")
        self.conn.commit()

    def fetch_open_positions(self) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM open_positions ORDER BY entry_ts")]

    def delete_trades_for_day(self, day: str) -> int:
        """Delete all paper_trades whose entry falls on ``day`` (YYYY-MM-DD). Used at live
        warm-start so the latest run re-derives the whole session as the single source of truth —
        prevents duplicate rows when a restart replays trades a prior process recorded live (the
        live vs. historical bar timestamps differ slightly, so INSERT OR REPLACE can't dedupe)."""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM paper_trades WHERE date(entry_ts) = ?", (day,))
        self.conn.commit()
        return cur.rowcount

    def update_live_status(self, *, bar_ts: Optional[str], bars_processed: int,
                           open_count: int, closed_today: int, watching: int,
                           run_id: Optional[str] = None) -> None:
        """Upsert the single live-status row (the dashboard's 'feed alive' beacon)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO live_status
               (id, updated_ts, bar_ts, bars_processed, open_count, closed_today, watching, run_id)
               VALUES (1,?,?,?,?,?,?,?)""",
            (_now_iso(), bar_ts, bars_processed, open_count, closed_today, watching,
             run_id or self.run_id),
        )
        self.conn.commit()

    def fetch_live_status(self) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM live_status WHERE id = 1").fetchone()
        return dict(row) if row else None

    def fetch_plans(self) -> List[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM trade_plans ORDER BY ts")]

    def fetch_trades(self, start: str = None, end: str = None,
                     symbol: str = None, strategy: str = None) -> List[dict]:
        """Closed paper trades, optionally filtered by date range / symbol / strategy.

        ``start``/``end`` are inclusive ISO dates compared against the entry timestamp; only
        filled trades (entry_ts not null) are returned, ordered by entry time.
        """
        clauses, args = ["entry_ts IS NOT NULL"], []
        if start:
            clauses.append("entry_ts >= ?")
            args.append(start)
        if end:
            clauses.append("entry_ts <= ?")
            args.append(end + "T23:59:59")
        if symbol:
            clauses.append("symbol = ?")
            args.append(symbol.upper())
        if strategy:
            clauses.append("strategy = ?")
            args.append(strategy)
        sql = f"SELECT * FROM paper_trades WHERE {' AND '.join(clauses)} ORDER BY entry_ts"
        return [dict(r) for r in self.conn.execute(sql, args)]

    def close(self) -> None:
        self.conn.close()
