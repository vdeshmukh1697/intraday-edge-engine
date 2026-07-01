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
)

PREDICTIONS_DDL = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,               -- IST wall-clock, ISO
    kind TEXT,             -- entry|exit|halt|premarket|scan|health|advice|error|other
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
    run_id TEXT
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
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            conn.execute(PREDICTIONS_DDL)
            conn.execute(
                """INSERT INTO predictions
                   (ts, kind, level, symbol, direction, strategy, entry, stop_loss, stop_pct,
                    target, target_pct, risk_reward, expected_move_pct, confidence,
                    qty, rupee_risk, pnl_pct_net, r_multiple, exit_reason,
                    reasons, extra, message, delivered, run_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
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
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - logging is strictly best-effort
        pass


def fetch_predictions(conn: sqlite3.Connection, *, limit: int = 200,
                      kind: Optional[str] = None, symbol: Optional[str] = None,
                      since_id: Optional[int] = None) -> List[dict]:
    """Newest-first page of the predictions log (``since_id`` for cheap live polling)."""
    conn.execute(PREDICTIONS_DDL)  # table may predate any alert on a fresh DB
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
    out = []
    for r in conn.execute(sql, args):
        row = dict(r)
        for key in ("reasons", "extra"):
            if row.get(key):
                try:
                    row[key] = json.loads(row[key])
                except Exception:  # noqa: BLE001 - tolerate legacy/hand-written rows
                    pass
        out.append(row)
    return out
