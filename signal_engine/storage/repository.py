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


def _round2(value: Optional[float]) -> Optional[float]:
    """Round ₹ to 2dp at the persistence boundary (PORTFOLIO §3: internal math stays unrounded;
    only what hits disk is paise-rounded). None passes through (legacy / not-applicable)."""
    return None if value is None else round(float(value), 2)


class SignalRepository:
    def __init__(self, db_url: str = "sqlite:///data/signal_engine.sqlite3",
                 run_id: Optional[str] = None, read_only: bool = False):
        path = _path_from_url(db_url)
        # A stable-per-process tag for every row this repo writes, unless a call overrides it.
        self.run_id = run_id or _default_run_id()
        # Read-only mode for the dashboard's polling endpoints: open the connection but SKIP init_db.
        # init_db issues schema/PRAGMA statements that take a WRITE lock; during market hours that
        # blocks ~10s behind the live engine's per-bar writes, so /api/portfolio etc. hang and the
        # dashboard shows a perpetual "Loading…". A SELECT-only reader under WAL never takes a write
        # lock and reads a consistent snapshot without waiting on the writer — so reads stay instant.
        if read_only and path != ":memory:":
            self.conn = sqlite3.connect(path, timeout=5.0)
            self.conn.row_factory = sqlite3.Row
            # Skip init_db when the schema already exists (established DB) — that's the whole point,
            # so market-hours reads don't block on the live writer's lock. On a fresh/partial DB
            # (new deploy, tests) there is NO live writer to contend with, so create the schema once.
            # Probe the newest table (portfolio_equity): present => current init_db has already run.
            exists = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='portfolio_equity'"
            ).fetchone()
            if exists is None:
                self.init_db()
            return
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self) -> None:
        cur = self.conn.cursor()
        # WAL: readers never block writers (and vice versa). The dashboard API polls this DB
        # (predictions/status/positions every few seconds) while the live engine writes every
        # bar — under the default DELETE journal a read could make the engine's commit lose a
        # busy-timeout race and drop a trade row. WAL is persistent (database-level), local-disk
        # only (true here), and safe across our multi-process readers/writers.
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")  # durable enough with WAL; much faster
        except sqlite3.OperationalError:
            pass  # e.g. read-only mounts in odd test setups — never fatal
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
        # The one persistent ₹1,00,000 PAPER book (PORTFOLIO §2): a single row holding free cash
        # + lifetime realized ₹. Written only by the live engine's PortfolioLedger (single-writer
        # rule); the API and scheduler read it. All money here is paper — legibility, not edge.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                starting_capital REAL, cash REAL,
                realized_pnl_total REAL, updated_ts TEXT, run_id TEXT
            )
            """
        )
        # Equity time series for the dashboard's portfolio curve: intraday 'mark' snapshots on a
        # cadence plus one 'eod' row after square-off (PORTFOLIO §2/§3 snapshot()).
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, day TEXT, kind TEXT,        -- kind: 'mark' (intraday) | 'eod'
                equity REAL, cash REAL, invested REAL, unrealized_pnl REAL,
                open_count INTEGER, run_id TEXT
            )
            """
        )
        # Predictions log: every alert pushed to the user (Telegram), with its structured
        # parameters — written by RecordingAlerter, read by GET /api/predictions. Created
        # here too so a fresh DB serves the dashboard before the first alert fires.
        from signal_engine.storage.predictions_log import PREDICTIONS_DDL

        cur.execute(PREDICTIONS_DDL)
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
        # ₹-book columns (PORTFOLIO §2). Legacy rows keep NULL (they predate the ledger and are
        # excluded from ₹ sums — the analytics fallback still models them at a fixed notional).
        for table, new_cols in (
            ("paper_trades", (("qty", "INTEGER"), ("notional_entry", "REAL"),
                              ("charges_inr", "REAL"), ("pnl_inr", "REAL"))),
            ("open_positions", (("qty", "INTEGER"), ("notional", "REAL"),
                                ("unrealized_pnl_inr", "REAL"))),
            ("predictions", (("reason_plain", "TEXT"), ("portfolio_equity", "REAL"),
                             ("notional", "REAL"))),
        ):
            cols = {r["name"] for r in cur.execute(f"PRAGMA table_info({table})")}
            for col, decl in new_cols:
                if col not in cols:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
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
        # ₹-book fields (PORTFOLIO §2) — getattr with defaults: the runner may pass legacy
        # position objects that predate the ledger; those persist as 0/NULL ("unsized").
        qty = int(getattr(pos, "qty", 0) or 0)
        cur.execute(
            """INSERT OR REPLACE INTO paper_trades
               (id, symbol, strategy, direction, entry_fill, entry_ts, exit_fill,
                exit_ts, exit_reason, pnl_pct_net, r_multiple, hold_minutes, won, confidence,
                stop_loss, target, qty, notional_entry, charges_inr, pnl_inr, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pos.id, pos.symbol, pos.plan.strategy, pos.direction.value,
                pos.entry_fill, pos.entry_ts.isoformat() if pos.entry_ts else None,
                pos.exit_fill, pos.exit_ts.isoformat() if pos.exit_ts else None,
                pos.exit_reason.value, pos.pnl_pct_net, pos.r_multiple,
                pos.hold_minutes, int(pos.won) if pos.won is not None else None,
                pos.plan.confidence, pos.plan.stop_loss, target,
                qty, _round2(getattr(pos, "notional", None)),
                _round2(getattr(pos, "charges_inr", None)), _round2(getattr(pos, "pnl_inr", None)),
                run_id or self.run_id,
            ),
        )
        self.conn.commit()

    def save_open_position(self, pos: PaperPosition, last_price: Optional[float] = None,
                           unrealized_pnl_pct: Optional[float] = None,
                           unrealized_pnl_inr: Optional[float] = None,
                           run_id: Optional[str] = None) -> None:
        """Upsert a currently-open position so the dashboard can show live entries + unrealized
        P&L. Called on entry and again each bar with a fresh mark; ``remove_open_position`` on
        close. ``last_price``/``unrealized_pnl_pct`` are best-effort (None until first mark).

        ₹-book fields (PORTFOLIO §2): ``qty``/``notional`` come off the position object (getattr —
        legacy objects persist 0/NULL). ``unrealized_pnl_inr`` is derived from ``last_price`` when
        the position is sized (sign * (last - entry_fill) * qty); pass it explicitly to override."""
        plan = pos.plan
        target = plan.t1 if plan.targets else None
        target_pct = plan.target_pcts[0] if plan.target_pcts else None
        qty = int(getattr(pos, "qty", 0) or 0)
        if unrealized_pnl_inr is None and qty > 0 and last_price is not None \
                and pos.entry_fill is not None:
            unrealized_pnl_inr = pos.direction.sign * (last_price - pos.entry_fill) * qty
        cur = self.conn.cursor()
        cur.execute(
            """INSERT OR REPLACE INTO open_positions
               (id, symbol, strategy, direction, entry_fill, entry_ts, stop_loss, stop_pct,
                target, target_pct, confidence, expected_move_pct, risk_reward,
                last_price, unrealized_pnl_pct, qty, notional, unrealized_pnl_inr,
                updated_ts, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pos.id, pos.symbol, plan.strategy, pos.direction.value,
                pos.entry_fill, pos.entry_ts.isoformat() if pos.entry_ts else None,
                plan.stop_loss, plan.stop_pct, target, target_pct, plan.confidence,
                plan.expected_move_pct, plan.risk_reward, last_price, unrealized_pnl_pct,
                qty, _round2(getattr(pos, "notional", None)), _round2(unrealized_pnl_inr),
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

    # ------------------------------------------------------------------ #
    # Portfolio book (PORTFOLIO §2/§3) — state row, equity curve, ₹ sums.
    # Single-writer rule: only the live engine's PortfolioLedger calls the
    # write half; the API/scheduler use the fetch half read-only.
    # ------------------------------------------------------------------ #
    def fetch_portfolio_state(self) -> Optional[dict]:
        """The single paper-book row (cash, lifetime realized ₹), or None on a fresh DB."""
        row = self.conn.execute("SELECT * FROM portfolio_state WHERE id = 1").fetchone()
        return dict(row) if row else None

    def save_portfolio_state(self, *, starting_capital: float, cash: float,
                             realized_pnl_total: float,
                             run_id: Optional[str] = None) -> None:
        """Upsert the single paper-book row. ₹ rounded to 2dp at this boundary (PORTFOLIO §3)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO portfolio_state
               (id, starting_capital, cash, realized_pnl_total, updated_ts, run_id)
               VALUES (1,?,?,?,?,?)""",
            (_round2(starting_capital), _round2(cash), _round2(realized_pnl_total),
             _now_iso(), run_id or self.run_id),
        )
        self.conn.commit()

    def insert_equity_snapshot(self, *, kind: str, equity: float, cash: float,
                               invested: float, unrealized_pnl: float, open_count: int,
                               ts: Optional[str] = None, day: Optional[str] = None,
                               run_id: Optional[str] = None) -> None:
        """Append one point to the portfolio equity curve (kind: 'mark' intraday | 'eod')."""
        ts = ts or _now_iso()
        self.conn.execute(
            """INSERT INTO portfolio_equity
               (ts, day, kind, equity, cash, invested, unrealized_pnl, open_count, run_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (ts, day or ts[:10], kind, _round2(equity), _round2(cash), _round2(invested),
             _round2(unrealized_pnl), int(open_count), run_id or self.run_id),
        )
        self.conn.commit()

    def fetch_equity_curve(self, days: int = 30) -> List[dict]:
        """Chronological equity snapshots for the last ``days`` days (inclusive of today)."""
        import datetime as _dt

        cutoff = (_dt.date.fromisoformat(_now_iso()[:10])
                  - _dt.timedelta(days=max(0, int(days)))).isoformat()
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM portfolio_equity WHERE day >= ? ORDER BY id", (cutoff,))]

    def sum_closed_pnl_inr(self, day: Optional[str] = None) -> float:
        """Σ realized ₹ over ledger-sized closed trades (pnl_inr NOT NULL — legacy rows are
        excluded, they predate the book). ``day`` (YYYY-MM-DD) filters by EXIT date."""
        clauses, args = ["pnl_inr IS NOT NULL"], []
        if day:
            clauses.append("date(exit_ts) = ?")
            args.append(day)
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(pnl_inr), 0.0) FROM paper_trades WHERE {' AND '.join(clauses)}",
            args).fetchone()
        return float(row[0])

    def fetch_predictions(self, *, limit: int = 200, kind: Optional[str] = None,
                          symbol: Optional[str] = None,
                          since_id: Optional[int] = None) -> List[dict]:
        """Newest-first page of the predictions (alert) log; see predictions_log.py."""
        from signal_engine.storage.predictions_log import fetch_predictions

        return fetch_predictions(self.conn, limit=limit, kind=kind, symbol=symbol,
                                 since_id=since_id)

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
