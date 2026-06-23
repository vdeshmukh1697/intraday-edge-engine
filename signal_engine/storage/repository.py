"""SQLite repository for surfaced trade plans and closed paper trades (PLAN §6.5).

SQLite needs no server, so the MVP runs and tests anywhere. Postgres is the production
target (PLAN §3.6); this class is the interface both share. ``:memory:`` is supported
for tests.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

from signal_engine.domain.models import PaperPosition, TradePlan


def _path_from_url(db_url: str) -> str:
    """Accept 'sqlite:///path', 'sqlite:///:memory:' or a bare path."""
    if db_url.startswith("sqlite:///"):
        return db_url[len("sqlite:///"):]
    return db_url


class SignalRepository:
    def __init__(self, db_url: str = "sqlite:///data/signal_engine.sqlite3"):
        path = _path_from_url(db_url)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
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
        # Forward-compatible migration: add columns that predate this schema.
        existing = {r["name"] for r in cur.execute("PRAGMA table_info(paper_trades)")}
        for col in ("stop_loss", "target"):
            if col not in existing:
                cur.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} REAL")
        self.conn.commit()

    def save_plan(self, plan: TradePlan) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO trade_plans
               (symbol, ts, direction, strategy, entry, stop_loss, stop_pct,
                targets, target_pcts, expected_move_pct, risk_reward,
                cost_to_break_even_pct, confidence, reasons, time_validity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                plan.symbol, plan.ts.isoformat(), plan.direction.value, plan.strategy,
                plan.entry, plan.stop_loss, plan.stop_pct,
                json.dumps(plan.targets), json.dumps(plan.target_pcts),
                plan.expected_move_pct, plan.risk_reward, plan.cost_to_break_even_pct,
                plan.confidence, json.dumps(plan.reasons),
                plan.time_validity.isoformat() if plan.time_validity else None,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_position(self, pos: PaperPosition) -> None:
        cur = self.conn.cursor()
        target = pos.plan.t1 if pos.plan.targets else None
        cur.execute(
            """INSERT OR REPLACE INTO paper_trades
               (id, symbol, strategy, direction, entry_fill, entry_ts, exit_fill,
                exit_ts, exit_reason, pnl_pct_net, r_multiple, hold_minutes, won, confidence,
                stop_loss, target)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pos.id, pos.symbol, pos.plan.strategy, pos.direction.value,
                pos.entry_fill, pos.entry_ts.isoformat() if pos.entry_ts else None,
                pos.exit_fill, pos.exit_ts.isoformat() if pos.exit_ts else None,
                pos.exit_reason.value, pos.pnl_pct_net, pos.r_multiple,
                pos.hold_minutes, int(pos.won) if pos.won is not None else None,
                pos.plan.confidence, pos.plan.stop_loss, target,
            ),
        )
        self.conn.commit()

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
