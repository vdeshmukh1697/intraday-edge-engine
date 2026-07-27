"""Deterministic fact-gathering for the nightly desk review — NO LLM, no network.

Every number the review is allowed to cite is computed here, from the live SQLite tables and
the engine log. Nothing downstream may invent a figure: `analyst.py` injects this bundle as a
"verified snapshot" and the prose layer is forbidden from reconciling anything against it.

The single most important thing this module produces is **session integrity**. On 2026-07-23
the live session logged 0 bars and 1,426 websocket reconnects (the Dhan Data-API subscription
had lapsed) while every EOD job still ran and every dashboard still rendered. A review that
silently analysed that session as if it were real would have manufactured a finding out of an
outage. So integrity is computed first and, when it fails, the orchestrator refuses to draw
conclusions (see `review.py`).

Column names here were verified against the live DB on 2026-07-26:
    paper_trades(id, symbol, strategy, direction, entry_fill, entry_ts, exit_fill, exit_ts,
                 exit_reason, pnl_pct_net, r_multiple, hold_minutes, won, confidence,
                 stop_loss, target, run_id, qty, notional_entry, charges_inr, pnl_inr,
                 pnl_pct_gross, cost_pct, nifty_ret_pct, alpha_pct)
    predictions(id, ts, kind, level, symbol, direction, strategy, entry, stop_loss, stop_pct,
                target, target_pct, risk_reward, expected_move_pct, confidence, qty,
                rupee_risk, pnl_pct_net, r_multiple, exit_reason, reasons, extra, message,
                delivered, run_id, reason_plain, portfolio_equity, notional)
    portfolio_equity(id, ts, day, kind, equity, cash, invested, unrealized_pnl, open_count,
                     run_id)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

# A session is "dead" below this many 1-min bars. The live watchlist is 40 names over a
# 375-minute session, so a healthy day logs ~15,000 bar events (2026-07-22: 15,034). Even a
# session that started late or lost the feed at lunch clears 500 easily; 0-499 means the feed
# never really ran. Deliberately generous — the guard exists to catch outages, not slow days.
MIN_BARS_FOR_A_REAL_SESSION = 500

# Reconnect storm threshold: dhan_ws logs one "stream error (n/200), reconnecting" line per
# failed attempt. A handful across a session is normal (hotspot blips); hundreds means the
# feed was never usable. 2026-07-23 logged 1,426.
RECONNECT_STORM = 100


def _path_from_url(db_url: str) -> str:
    """Accept 'sqlite:///path', 'sqlite:///:memory:' or a bare path (mirrors repository.py)."""
    return db_url[len("sqlite:///"):] if db_url.startswith("sqlite:///") else db_url


# --------------------------------------------------------------------------------------- #
# Engine-log parsing (the only source for evaluations / skips / feed health)
# --------------------------------------------------------------------------------------- #

_RE_LEG_DONE = re.compile(r"live_job leg done: (\d+) bars, (\d+) picks, (\d+) paper trades")
_RE_STREAMING = re.compile(r"live_job: streaming .* feed for (\d+) symbols")
_RE_SKIP_CASH = re.compile(r"SKIP .* > free cash")
_RE_MISSED = re.compile(r'was missed by')


@dataclass
class LogFacts:
    """What the engine log says happened during the session (counts only, no interpretation)."""

    log_path: Optional[str] = None
    log_lines_for_day: int = 0
    live_started: bool = False
    symbols_subscribed: Optional[int] = None
    bars_processed: Optional[int] = None
    picks: Optional[int] = None
    trades_persisted: Optional[int] = None
    entry_evaluations: int = 0          # ENTRY-CTX lines: a setup passed the strategy rules
    skips_affordability: int = 0        # book had no free cash for a single share
    skips_other: int = 0
    ws_reconnects: int = 0
    feed_errors: int = 0
    warnings: int = 0
    errors: int = 0
    jobs_missed: int = 0                # APScheduler "Run time of job ... was missed by"
    notable: List[str] = field(default_factory=list)


def parse_engine_log(path: Optional[str], day: str, max_notable: int = 12) -> LogFacts:
    """Count the day's engine-log evidence. Missing/unreadable log => empty LogFacts.

    Deliberately count-only: the log is evidence about the SESSION (did the feed run, what did
    the gates do), never evidence about strategy quality. Lines are matched by the ISO date
    prefix the logger writes ("2026-07-22 09:42:01 INFO  signal_engine.engine | ...").
    """
    facts = LogFacts(log_path=path)
    if not path or not os.path.exists(path):
        return facts
    try:
        handle = open(path, "r", errors="replace")
    except OSError:
        return facts
    with handle:
        for line in handle:
            if _RE_MISSED.search(line) and f"next run at: {day}" in line:
                facts.jobs_missed += 1
            if not line.startswith(day):
                continue
            facts.log_lines_for_day += 1
            if " WARNING " in line:
                facts.warnings += 1
            if " ERROR " in line:
                facts.errors += 1
            if "reconnecting" in line:
                facts.ws_reconnects += 1
            if "stream error" in line:
                facts.feed_errors += 1
            if "ENTRY-CTX " in line:
                facts.entry_evaluations += 1
            elif "SKIP " in line:
                if _RE_SKIP_CASH.search(line):
                    facts.skips_affordability += 1
                else:
                    facts.skips_other += 1
            m = _RE_STREAMING.search(line)
            if m:
                facts.live_started = True
                facts.symbols_subscribed = int(m.group(1))
            m = _RE_LEG_DONE.search(line)
            if m:
                # Last leg wins: live_job relaunches on a dead feed, each leg logs its own line.
                facts.bars_processed = int(m.group(1))
                facts.picks = int(m.group(2))
                facts.trades_persisted = int(m.group(3))
            if (" ERROR " in line or "could NOT start" in line) \
                    and len(facts.notable) < max_notable:
                facts.notable.append(line.rstrip()[:240])
    return facts


# --------------------------------------------------------------------------------------- #
# Session integrity — computed BEFORE any analysis, and gating it
# --------------------------------------------------------------------------------------- #

@dataclass
class SessionIntegrity:
    """Did the session actually happen? Verdict gates every downstream conclusion."""

    day: str
    trading_day: bool
    verdict: str                        # OK | DEGRADED | DEAD | NO_SESSION
    reasons: List[str] = field(default_factory=list)
    bars_processed: Optional[int] = None
    ws_reconnects: int = 0
    n_trades: int = 0
    entry_evaluations: int = 0
    trade_count_binding: str = "unbound"   # gate | cap | drawdown_halt | cash | unbound

    @property
    def usable(self) -> bool:
        """True only when the session's data can support a performance conclusion.

        DEGRADED is usable-with-caveat (the feed ran and trades are real); DEAD and NO_SESSION
        are not usable at all — the review must refuse to conclude. This property is what
        `review.py` branches on, and `tests/test_desk.py` pins it."""
        return self.verdict in ("OK", "DEGRADED")


def assess_integrity(day: str, trading_day: bool, log: LogFacts, n_trades: int,
                     halt_reason: Optional[str], max_trades_per_day: int) -> SessionIntegrity:
    """Classify the session. Order matters: non-trading-day, then feed, then trade count."""
    integ = SessionIntegrity(
        day=day, trading_day=trading_day, verdict="OK",
        bars_processed=log.bars_processed, ws_reconnects=log.ws_reconnects,
        n_trades=n_trades, entry_evaluations=log.entry_evaluations,
    )
    if not trading_day:
        integ.verdict = "NO_SESSION"
        integ.reasons.append("not an NSE trading day — no session was expected")
        return integ
    if not log.live_started and log.log_lines_for_day == 0:
        integ.verdict = "NO_SESSION"
        integ.reasons.append("no engine-log lines for this day — the log may be rotated or the "
                             "scheduler was down; cannot verify the session ran")
    elif not log.live_started:
        integ.verdict = "DEAD"
        integ.reasons.append("live_job never logged 'streaming feed' — the session did not start")
    bars = log.bars_processed
    if bars is not None and bars < MIN_BARS_FOR_A_REAL_SESSION:
        integ.verdict = "DEAD"
        integ.reasons.append(
            "feed logged {} bars (< {} = a real session): the market data never arrived, so "
            "NOTHING about strategy quality can be concluded from this day".format(
                bars, MIN_BARS_FOR_A_REAL_SESSION))
    if log.ws_reconnects >= RECONNECT_STORM:
        msg = "{} websocket reconnect attempts — feed was unstable".format(log.ws_reconnects)
        if integ.verdict == "OK":
            integ.verdict = "DEGRADED"
        integ.reasons.append(msg)
    if integ.verdict == "OK" and n_trades == 0 and log.entry_evaluations == 0:
        integ.verdict = "DEGRADED"
        integ.reasons.append("feed ran but ZERO setups were evaluated — suspect a gate or "
                             "watchlist problem, not a quiet market")
    # What bound the trade count? This is the difference between "the strategy found 8 setups"
    # and "the strategy found 200 and the book ran out of money at 09:44".
    if halt_reason:
        integ.trade_count_binding = "drawdown_halt"
    elif max_trades_per_day and n_trades >= max_trades_per_day:
        integ.trade_count_binding = "cap"
    elif log.skips_affordability > 0:
        integ.trade_count_binding = "cash"
    elif log.entry_evaluations > 0:
        integ.trade_count_binding = "gate"
    if not integ.reasons:
        integ.reasons.append("feed healthy, session ran to close")
    return integ


# --------------------------------------------------------------------------------------- #
# The bundle
# --------------------------------------------------------------------------------------- #

@dataclass
class SessionFacts:
    """Everything the review may cite about one session, plus its trailing context."""

    day: str
    integrity: SessionIntegrity
    log: LogFacts
    trades: List[Dict] = field(default_factory=list)
    predictions: Dict[str, List[Dict]] = field(default_factory=dict)
    plans: List[Dict] = field(default_factory=list)
    halt: Optional[Dict] = None
    equity: Dict = field(default_factory=dict)
    micro: Dict = field(default_factory=dict)
    movers: Dict = field(default_factory=dict)
    trailing: Dict = field(default_factory=dict)
    config: Dict = field(default_factory=dict)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    def sum_net_pct(self) -> float:
        """Sum of per-trade net %. NOT the book return — see equity['book_return_pct'].

        Kept because the live drawdown breaker (`engine/runner._LossBreaker`) sums exactly
        this, so the review needs it to explain why a halt fired."""
        return sum(float(t.get("pnl_pct_net") or 0.0) for t in self.trades)

    def sum_inr(self) -> float:
        return sum(float(t.get("pnl_inr") or 0.0) for t in self.trades)

    def sum_charges_inr(self) -> float:
        return sum(float(t.get("charges_inr") or 0.0) for t in self.trades)


def _rows(conn: sqlite3.Connection, sql: str, args=()) -> List[Dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, args)]


def _equity_facts(conn: sqlite3.Connection, day: str, starting_capital: float) -> Dict:
    """Book equity for the day: open, close, intraday peak/trough, true book return.

    The distinction between `book_return_pct` (equity-weighted, the truth) and
    `sum_pnl_pct_net` (the naive per-trade sum) is load-bearing. On 2026-07-22 they were
    -2.59% and -4.43% respectively, because positions ran at 30-85% of the book — and the
    drawdown breaker halted the day on the -4.43% figure."""
    marks = _rows(conn, "SELECT ts, kind, equity, cash, invested FROM portfolio_equity "
                        "WHERE day = ? ORDER BY id", (day,))
    prior = _rows(conn, "SELECT day, equity FROM portfolio_equity WHERE day < ? AND kind = 'eod' "
                        "ORDER BY day DESC LIMIT 1", (day,))
    eod = [m for m in marks if m["kind"] == "eod"]
    equities = [float(m["equity"]) for m in marks if m["equity"] is not None]
    open_eq = float(prior[0]["equity"]) if prior else (equities[0] if equities else
                                                      starting_capital)
    close_eq = float(eod[-1]["equity"]) if eod else (equities[-1] if equities else open_eq)
    peak = max(equities) if equities else open_eq
    trough = min(equities) if equities else open_eq
    out = {
        "n_marks": len(marks),
        "prior_eod_day": prior[0]["day"] if prior else None,
        "open_equity": round(open_eq, 2),
        "close_equity": round(close_eq, 2),
        "intraday_peak": round(peak, 2),
        "intraday_trough": round(trough, 2),
        "book_return_pct": ((close_eq - open_eq) / open_eq * 100.0) if open_eq else None,
        "intraday_maxdd_pct": ((trough - peak) / peak * 100.0) if peak else None,
        "since_inception_pct": ((close_eq - starting_capital) / starting_capital * 100.0)
                               if starting_capital else None,
    }
    return out


def _trailing_facts(conn: sqlite3.Connection, day: str, sessions: int = 20) -> Dict:
    """Per-session aggregates for the trailing window, newest last.

    Trailing evidence is what lets the desk say "this is a live-behaviour claim with k
    sessions" instead of extrapolating from tonight's 8 trades. `ledger.py` grades hypotheses
    against these, and refuses to grade below the pre-registered session floor."""
    days = [r["d"] for r in _rows(
        conn, "SELECT DISTINCT date(entry_ts) AS d FROM paper_trades WHERE date(entry_ts) <= ? "
              "ORDER BY d DESC LIMIT ?", (day, int(sessions)))]
    days = sorted(days)
    per_day: List[Dict] = []
    for d in days:
        rows = _rows(conn, "SELECT * FROM paper_trades WHERE date(entry_ts) = ?", (d,))
        wins = [r for r in rows if (r.get("pnl_pct_net") or 0.0) > 0]
        per_day.append({
            "day": d, "n": len(rows), "wins": len(wins),
            "sum_net_pct": sum(float(r.get("pnl_pct_net") or 0.0) for r in rows),
            "sum_gross_pct": sum(float(r.get("pnl_pct_gross") or 0.0) for r in rows),
            "sum_cost_pct": sum(float(r.get("cost_pct") or 0.0) for r in rows),
            "sum_inr": sum(float(r.get("pnl_inr") or 0.0) for r in rows),
            "charges_inr": sum(float(r.get("charges_inr") or 0.0) for r in rows),
            "sum_r": sum(float(r.get("r_multiple") or 0.0) for r in rows),
        })
    all_rows = _rows(conn, "SELECT * FROM paper_trades WHERE date(entry_ts) IN ({}) "
                           "ORDER BY entry_ts".format(",".join("?" * len(days))), days) \
        if days else []
    return {"sessions": len(days), "days": days, "per_day": per_day, "trades": all_rows}


def _micro_facts(conn: sqlite3.Connection, day: str) -> Dict:
    """Microstructure shadow signal for the day (never gates a trade — measurement only)."""
    try:
        rows = _rows(conn, "SELECT dir_correct FROM microstructure_signals WHERE date(bar_ts) = ?",
                     (day,))
    except sqlite3.Error:
        return {"n_signals": 0, "n_scored": 0, "hit_rate": None}
    scored = [r for r in rows if r.get("dir_correct") is not None]
    hits = sum(int(r["dir_correct"]) for r in scored)
    return {"n_signals": len(rows), "n_scored": len(scored),
            "hit_rate": (hits / len(scored)) if scored else None, "baseline": 0.5}


def _movers_facts(conn: sqlite3.Connection, day: str) -> Dict:
    """Movers sleeve scoreboard as of this day (honesty labels live in movers/sleeve.py)."""
    try:
        rows = _rows(conn, "SELECT hit_big5, dir_correct, sleeve_pnl_pct, resolved_ts "
                           "FROM mover_predictions WHERE for_day <= ?", (day,))
    except sqlite3.Error:
        return {"n_resolved": 0}
    resolved = [r for r in rows if r.get("resolved_ts")]
    dir_rows = [r for r in resolved if r.get("dir_correct") is not None]
    pnl_rows = [r for r in resolved if r.get("sleeve_pnl_pct") is not None]
    return {
        "n_predictions": len(rows), "n_resolved": len(resolved),
        "hit_rate_big5": (sum(int(r["hit_big5"] or 0) for r in resolved) / len(resolved))
                         if resolved else None,
        "base_rate_big5": 0.0491,
        "dir_accuracy": (sum(int(r["dir_correct"]) for r in dir_rows) / len(dir_rows))
                        if dir_rows else None,
        "n_dir_called": len(dir_rows),
        "sleeve_cum_pnl_pct": sum(float(r["sleeve_pnl_pct"]) for r in pnl_rows)
                              if pnl_rows else 0.0,
        "n_sleeve_legs": len(pnl_rows),
    }


def _decode_reasons(raw) -> List[str]:
    """`predictions.reasons` / `trade_plans.reasons` is a JSON array of rule strings."""
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return [str(raw)]
    if isinstance(val, list):
        return [str(v) for v in val]
    return [str(val)]


def _config_facts(cfg) -> Dict:
    """The live risk knobs the review is allowed to reason about. Read-only, always."""
    if cfg is None:
        return {}
    r, c, s = cfg.risk.risk, cfg.risk.costs, cfg.risk.slippage
    charges_pct = 0.0
    friction_pct = 0.0
    try:
        from signal_engine.risk.costs import CostModel

        gate = CostModel(c, s)
        charges_only = CostModel(c)
        ref = float(getattr(c, "reference_trade_value", 100000.0))
        charges_pct = charges_only.breakeven_pct(1000.0, trade_value=ref)
        friction_pct = gate.breakeven_pct(1000.0, trade_value=ref)
    except Exception:  # noqa: BLE001 - config shape drift must never kill the review
        friction_pct = 0.0
    return {
        "strategy": getattr(cfg.settings.strategy, "active", None),
        "watchlist_size": len(getattr(cfg.settings, "watchlist", []) or []),
        "max_cost_r": float(getattr(r, "max_cost_r", 0.0) or 0.0),
        "min_stop_pct": float(getattr(r, "min_stop_pct", 0.0) or 0.0),
        "atr_stop_multiple": float(getattr(r, "atr_stop_multiple", 0.0) or 0.0),
        "rr_floor": float(getattr(r, "rr_floor", 0.0) or 0.0),
        "max_trades_per_day": int(getattr(r, "max_trades_per_day", 0) or 0),
        "max_concurrent_positions": int(getattr(r, "max_concurrent_positions", 0) or 0),
        "daily_loss_pct": float(getattr(r, "daily_loss_pct", 0.0) or 0.0),
        "risk_per_trade_pct": float(getattr(r, "risk_per_trade_pct", 0.0) or 0.0),
        "starting_capital": float(getattr(cfg.risk.portfolio, "starting_capital", 100000.0)),
        "charges_pct_round_trip": charges_pct,
        "friction_pct_round_trip": friction_pct,   # charges + modeled slippage
        "slippage_pct_per_side": float(getattr(s, "pct_per_side", 0.0) or 0.0),
    }


def gather(day: str, db_url: str = "sqlite:///data/signal_engine.sqlite3",
           log_path: Optional[str] = "logs/launchd-scheduler.err.log",
           cfg=None, trailing_sessions: int = 20,
           trading_day: Optional[bool] = None) -> SessionFacts:
    """Assemble the whole fact bundle for ``day`` (YYYY-MM-DD). Read-only; never writes.

    ``trading_day`` is injectable so tests need no calendar and the review can be re-run for
    an arbitrary historical date. ``cfg`` is optional: without it the config block is empty and
    the gap model falls back to measured friction from the trades themselves.
    """
    conn = sqlite3.connect(_path_from_url(db_url), timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        trades = _rows(conn, "SELECT * FROM paper_trades WHERE date(entry_ts) = ? "
                             "ORDER BY entry_ts", (day,))
        preds = _rows(conn, "SELECT * FROM predictions WHERE date(ts) = ? ORDER BY id", (day,))
        plans = _rows(conn, "SELECT * FROM trade_plans WHERE date(ts) = ? ORDER BY id", (day,))
        conf = _config_facts(cfg)
        equity = _equity_facts(conn, day, conf.get("starting_capital", 100000.0))
        trailing = _trailing_facts(conn, day, sessions=trailing_sessions)
        micro = _micro_facts(conn, day)
        movers = _movers_facts(conn, day)
    finally:
        conn.close()

    by_kind: Dict[str, List[Dict]] = {}
    for p in preds:
        p["reasons_decoded"] = _decode_reasons(p.get("reasons"))
        by_kind.setdefault(p.get("kind") or "other", []).append(p)
    halts = by_kind.get("halt") or []
    halt = halts[0] if halts else None

    if trading_day is None:
        trading_day = _is_trading_day(day)
    log = parse_engine_log(log_path, day)
    integrity = assess_integrity(
        day, trading_day, log, len(trades),
        (halt.get("message") if halt else None),
        conf.get("max_trades_per_day", 0),
    )
    return SessionFacts(day=day, integrity=integrity, log=log, trades=trades,
                        predictions=by_kind, plans=plans, halt=halt, equity=equity,
                        micro=micro, movers=movers, trailing=trailing, config=conf)


def _is_trading_day(day: str) -> bool:
    """NSE trading-day check, best-effort (a calendar import failure must not kill the review)."""
    try:
        from signal_engine.market.calendar import NSECalendar

        return bool(NSECalendar().is_trading_day(date.fromisoformat(day)))
    except Exception:  # noqa: BLE001
        d = date.fromisoformat(day)
        return d.weekday() < 5


def previous_session(db_url: str, day: str) -> Optional[str]:
    """The most recent day BEFORE ``day`` that has paper trades (for hypothesis grading)."""
    conn = sqlite3.connect(_path_from_url(db_url), timeout=10.0)
    try:
        row = conn.execute("SELECT DISTINCT date(entry_ts) FROM paper_trades "
                           "WHERE date(entry_ts) < ? ORDER BY 1 DESC LIMIT 1", (day,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def last_session_with_trades(db_url: str, on_or_before: Optional[str] = None) -> Optional[str]:
    """Newest day with paper trades (used by the CLI when no day is given)."""
    conn = sqlite3.connect(_path_from_url(db_url), timeout=10.0)
    try:
        if on_or_before:
            row = conn.execute("SELECT DISTINCT date(entry_ts) FROM paper_trades "
                               "WHERE date(entry_ts) <= ? ORDER BY 1 DESC LIMIT 1",
                               (on_or_before,)).fetchone()
        else:
            row = conn.execute("SELECT DISTINCT date(entry_ts) FROM paper_trades "
                               "ORDER BY 1 DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def today_ist() -> str:
    """Today's IST date as ISO — the scheduler's notion of "the session just closed"."""
    import datetime as _dt

    import pytz

    return _dt.datetime.now(pytz.timezone("Asia/Kolkata")).date().isoformat()


def yesterday_ist() -> str:
    import datetime as _dt

    import pytz

    return (_dt.datetime.now(pytz.timezone("Asia/Kolkata")).date()
            - timedelta(days=1)).isoformat()
