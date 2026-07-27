"""Benchmark-adjusted trade outcomes — alpha vs NIFTY (P3.2, STRATEGY_IMPROVEMENT_PLAN_2026-07).

Every closed paper trade gets two columns resolved after the fact:

* ``nifty_ret_pct`` — the ^NSEI move (%) over the trade's holding window
  (nearest 1-min close at-or-before entry_ts -> same at-or-before exit_ts).
* ``alpha_pct``     — ``pnl_pct_gross - direction_sign * nifty_ret_pct``.

GROSS vs GROSS by design: both sides are pure price moves, so stock-picking is
separated from the index tide while charges stay visible in their own columns
(``cost_pct`` / ``charges_inr``). Direction-signed benchmark: a LONG that merely
rode a rising tape shows alpha ~0; a SHORT is credited for fading a falling one
(its tide contribution is ``-nifty_ret``). On a long-only-verdict book this is
the difference between "picked well" and "the market went up".

Resolution is self-healing: any closed trade with NULL ``alpha_pct`` is retried
on each run (Yahoo keeps ~30 days of 1m history), so a failed EOD run heals the
next day. Runs from the scheduler (``alpha_job``, 16:20 IST) or manually:

    .venv/bin/python -m signal_engine.analytics.alpha
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Tuple

import pytz

IST = pytz.timezone("Asia/Kolkata")
_SIGN = {"LONG": 1.0, "SHORT": -1.0}


def fetch_nifty_closes(day: date) -> "object":
    """^NSEI 1-min closes for one session as a tz-aware (IST) pandas Series.

    Isolated so tests can inject a synthetic series instead of hitting Yahoo.
    Returns an empty Series when Yahoo has nothing (holiday, >30d ago, outage).
    """
    import pandas as pd
    import yfinance as yf

    start = day.isoformat()
    end = (pd.Timestamp(day) + pd.Timedelta(days=1)).date().isoformat()
    df = yf.download("^NSEI", start=start, end=end, interval="1m",
                     progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    closes = df["Close"]
    if hasattr(closes, "columns"):  # yfinance MultiIndex frame for single ticker
        closes = closes.iloc[:, 0]
    closes.index = closes.index.tz_convert(IST)
    return closes


def index_return_pct(closes: "object", entry_ts: datetime, exit_ts: datetime) -> Optional[float]:
    """Index %-move between the closes at-or-before ``entry_ts`` and ``exit_ts``.

    ``asof`` semantics (nearest bar at-or-before) mirror how a trade experiences the
    tape: the fill happens into the bar that just closed. Returns None when either
    timestamp precedes the series (no bar to anchor on) or the series is empty.
    """
    if closes is None or len(closes) == 0:
        return None
    e0 = closes.asof(entry_ts)
    e1 = closes.asof(exit_ts)
    # pandas.Series.asof returns NaN when ts < first index entry.
    if e0 is None or e1 is None or e0 != e0 or e1 != e1 or not e0:
        return None
    return (float(e1) - float(e0)) / float(e0) * 100.0


def alpha_pct(gross_pct: float, direction: str, nifty_ret: float) -> float:
    """Direction-signed benchmark adjustment: gross - sign * index_move."""
    return gross_pct - _SIGN.get(direction, 1.0) * nifty_ret


def resolve_day(
    conn: sqlite3.Connection,
    day: date,
    closes: "object",
) -> Tuple[int, int]:
    """Resolve alpha for one session's unresolved closed trades. Returns (updated, skipped)."""
    import pandas as pd

    cur = conn.cursor()
    rows = cur.execute(
        """SELECT id, direction, entry_ts, exit_ts, entry_fill, exit_fill, pnl_pct_gross
           FROM paper_trades
           WHERE alpha_pct IS NULL AND date(entry_ts) = ? AND exit_ts IS NOT NULL""",
        (day.isoformat(),),
    ).fetchall()
    updated = skipped = 0
    for r in rows:
        trade_id, direction, entry_ts, exit_ts, entry_fill, exit_fill, gross = r
        sign = _SIGN.get(direction)
        if sign is None or not entry_ts or not exit_ts:
            skipped += 1
            continue
        if gross is None:
            # Legacy row missing P3.1 backfill: gross is exactly recoverable from fills.
            if not entry_fill or exit_fill is None:
                skipped += 1
                continue
            gross = sign * (exit_fill - entry_fill) / entry_fill * 100.0
        idx_ret = index_return_pct(
            closes, pd.Timestamp(entry_ts), pd.Timestamp(exit_ts))
        if idx_ret is None:
            skipped += 1
            continue
        cur.execute(
            "UPDATE paper_trades SET nifty_ret_pct = ?, alpha_pct = ? WHERE id = ?",
            (idx_ret, alpha_pct(float(gross), direction, idx_ret), trade_id),
        )
        updated += 1
    conn.commit()
    return updated, skipped


def resolve_all(
    db_path: str = "data/signal_engine.sqlite3",
    fetch: Optional[Callable[[date], "object"]] = None,
) -> Dict[str, Tuple[int, int]]:
    """Resolve alpha for every session that still has unresolved closed trades.

    ``fetch`` defaults to the Yahoo fetcher; tests inject synthetic series. One
    fetch per session day; days whose index data is unavailable stay unresolved
    (self-healing on the next run while Yahoo's ~30d 1m window lasts).
    """
    if fetch is None:
        fetch = fetch_nifty_closes
    conn = sqlite3.connect(db_path)
    try:
        days: List[str] = [
            r[0] for r in conn.execute(
                """SELECT DISTINCT date(entry_ts) FROM paper_trades
                   WHERE alpha_pct IS NULL AND exit_ts IS NOT NULL ORDER BY 1"""
            )
        ]
        out: Dict[str, Tuple[int, int]] = {}
        for d in days:
            day = date.fromisoformat(d)
            try:
                closes = fetch(day)
            except Exception:  # noqa: BLE001 - network hiccup: leave NULL, heal next run
                closes = None
            out[d] = resolve_day(conn, day, closes) if closes is not None else (0, -1)
        return out
    finally:
        conn.close()


if __name__ == "__main__":  # manual backfill / re-run
    results = resolve_all()
    for d, (upd, skip) in sorted(results.items()):
        note = "fetch failed" if skip == -1 else f"updated={upd} skipped={skip}"
        print(f"{d}: {note}")
