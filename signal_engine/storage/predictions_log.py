"""Predictions log — a durable record of every alert the system pushes to the user.

Every Telegram (or WhatsApp/CallMeBot) send is mirrored to the ``predictions`` table with
its timestamp, the structured parameters the sender had in hand (entry/stop/target,
confidence, sizing, exit P&L, …) and the exact message text. The dashboard reads this
back over ``GET /api/predictions`` so the user can audit what was predicted, when, and
with what parameters — without scraping chat history.

Design constraints:
- Logging must NEVER block or break an alert: every write is wrapped and best-effort.
- Writers live in short-lived processes and threads (scheduler jobs, the live engine),
  so each write opens its own connection (WAL-friendly, no shared-connection races).
"""

from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

# Structured meta keys that map 1:1 onto columns; anything else lands in ``extra`` JSON.
_META_COLUMNS = (
    "symbol", "direction", "strategy", "entry", "stop_loss", "stop_pct",
    "target", "target_pct", "risk_reward", "expected_move_pct", "confidence",
    "qty", "rupee_risk", "pnl_pct_net", "r_multiple", "exit_reason",
    "reason_plain", "portfolio_equity", "notional",
)

# Columns added after the table first shipped (PORTFOLIO §2). Fresh DBs get them via the DDL
# below; pre-portfolio DBs get them via ALTER — in repository.init_db AND defensively inside
# log_prediction (which opens its own connection and may hit a not-yet-migrated DB).
_PORTFOLIO_COLUMNS = (
    ("reason_plain", "TEXT"), ("portfolio_equity", "REAL"), ("notional", "REAL"),
)

PREDICTIONS_DDL = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,               -- IST wall-clock, ISO
    kind TEXT,             -- entry|exit|skip|halt|premarket|scan|health|advice|error|other
    level TEXT,            -- info|signal|warning|alert
    symbol TEXT, direction TEXT, strategy TEXT,
    entry REAL, stop_loss REAL, stop_pct REAL,
    target REAL, target_pct REAL,
    risk_reward REAL, expected_move_pct REAL, confidence REAL,
    qty INTEGER, rupee_risk REAL,
    pnl_pct_net REAL, r_multiple REAL, exit_reason TEXT,
    reasons TEXT,          -- JSON array
    extra TEXT,            -- JSON object: meta keys with no dedicated column
    message TEXT,          -- the exact alert text handed to the channel
    delivered INTEGER,     -- 1 = the channel send() returned without raising
    run_id TEXT,
    reason_plain TEXT,     -- layman's "why" for this alert (PORTFOLIO §5)
    portfolio_equity REAL, -- ₹ book equity the sizing was computed against (paper)
    notional REAL          -- ₹ deployed/suggested for this alert (paper)
)
"""


def log_prediction(db_path: str, *, message: str, level: str = "info",
                   meta: Optional[dict] = None, delivered: bool = True,
                   run_id: Optional[str] = None) -> None:
    """Best-effort insert; swallows every failure (an alert must never die on logging)."""
    try:
        from signal_engine.storage.repository import _now_iso

        meta = dict(meta or {})
        kind = str(meta.pop("kind", "other"))
        reasons = meta.pop("reasons", None)
        cols = {k: meta.pop(k) for k in list(meta) if k in _META_COLUMNS}
        insert_sql = """INSERT INTO predictions
               (ts, kind, level, symbol, direction, strategy, entry, stop_loss, stop_pct,
                target, target_pct, risk_reward, expected_move_pct, confidence,
                qty, rupee_risk, pnl_pct_net, r_multiple, exit_reason,
                reasons, extra, message, delivered, run_id,
                reason_plain, portfolio_equity, notional)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        params = (
            _now_iso(), kind, level,
            cols.get("symbol"), cols.get("direction"), cols.get("strategy"),
            cols.get("entry"), cols.get("stop_loss"), cols.get("stop_pct"),
            cols.get("target"), cols.get("target_pct"), cols.get("risk_reward"),
            cols.get("expected_move_pct"), cols.get("confidence"),
            cols.get("qty"), cols.get("rupee_risk"),
            cols.get("pnl_pct_net"), cols.get("r_multiple"), cols.get("exit_reason"),
            json.dumps(reasons) if reasons is not None else None,
            json.dumps(meta) if meta else None,
            message, int(delivered), run_id,
            cols.get("reason_plain"), cols.get("portfolio_equity"), cols.get("notional"),
        )
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            conn.execute(PREDICTIONS_DDL)
            try:
                conn.execute(insert_sql, params)
            except sqlite3.OperationalError as exc:
                # Defensive migration (PORTFOLIO §2): this writer opens its own connection, so
                # it can hit a DB created before the portfolio columns existed (repository
                # init_db hasn't run there yet). ALTER-add them and retry the insert ONCE.
                if "no column" not in str(exc):
                    raise
                for col, decl in _PORTFOLIO_COLUMNS:
                    try:
                        conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} {decl}")
                    except sqlite3.OperationalError:
                        pass  # e.g. duplicate column — another writer migrated first
                conn.execute(insert_sql, params)
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - logging is strictly best-effort
        pass


def fetch_predictions(conn: sqlite3.Connection, *, limit: int = 200,
                      kind: Optional[str] = None, symbol: Optional[str] = None,
                      since_id: Optional[int] = None) -> List[dict]:
    """Newest-first page of the predictions log (``since_id`` for cheap live polling).

    Read-only by design: no DDL here (a CREATE on every dashboard poll would make each
    read take the write path). A DB with no predictions table yet just returns []."""
    clauses, args = ["1=1"], []
    if kind:
        clauses.append("kind = ?")
        args.append(kind)
    if symbol:
        clauses.append("symbol = ?")
        args.append(symbol.upper())
    if since_id is not None:
        clauses.append("id > ?")
        args.append(since_id)
    args.append(max(1, min(int(limit), 1000)))
    sql = (f"SELECT * FROM predictions WHERE {' AND '.join(clauses)} "
           f"ORDER BY id DESC LIMIT ?")
    try:
        rows = list(conn.execute(sql, args))
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    out = []
    for r in rows:
        row = dict(r)
        for key in ("reasons", "extra"):
            if row.get(key):
                try:
                    row[key] = json.loads(row[key])
                except Exception:  # noqa: BLE001 - tolerate legacy/hand-written rows
                    pass
        out.append(row)
    return out
