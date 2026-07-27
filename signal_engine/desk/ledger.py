"""Append-only hypothesis + proposal ledger — how the desk actually gets smarter each night.

This is the "intelligence improves daily" mechanism, and it is deliberately boring: a SQLite
table, not a model. Three moves, every night:

1. **Read before generating.** `dead_ideas()` returns every statement already refuted or
   already open. `open_hypothesis()` is idempotent on the statement text, so a re-proposed dead
   idea is silently folded into the existing row instead of becoming a fresh "insight". Without
   this the agent would rediscover the universe allowlist every night forever.
2. **Grade yesterday's hypotheses against what actually happened** — each one carries a
   *pre-registered* metric, threshold, direction and evidence floor, chosen when the hypothesis
   was opened. Grading is a deterministic comparison, so the desk cannot move its own goalposts.
   Below the floor the hypothesis stays `open` with an explicit "insufficient evidence (k/min)"
   note; it is never graded early.
3. **Record dead ideas as dead**, with a 2-4 sentence lesson citing the realised numbers, and
   keep a running priors section — what we now believe about this strategy, and on what evidence.

The proposals table is the *other* half of the two-clock design. A proposal is a written diff
plus a pre-registered evidence bar. `applied` is 0 on insert and this module offers **no method
that sets it to 1** — only a human editing the DB or the config can. `tests/test_desk.py` pins
that (`test_no_proposal_is_ever_auto_applied`).

Storage follows the `microstructure/store.py` pattern: same SQLite file (WAL), own tables, own
migration, so it can never collide with the trading tables or block the live writer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

DDL_HYPOTHESES = """
CREATE TABLE IF NOT EXISTS desk_hypotheses (
    id TEXT PRIMARY KEY,                -- H-YYYYMMDD-NN
    created_day TEXT, statement TEXT UNIQUE,
    rationale TEXT,
    pre_registered_test TEXT,           -- exactly how it will be graded, written up front
    falsifiable_prediction TEXT,
    metric TEXT, direction TEXT,        -- 'gt' | 'lt'
    threshold REAL,
    min_sessions INTEGER, min_n INTEGER,
    status TEXT,                        -- open | supported | refuted | stale
    graded_day TEXT, outcome_value REAL, lesson TEXT,
    updated_ts TEXT, run_id TEXT
)
"""

DDL_PROPOSALS = """
CREATE TABLE IF NOT EXISTS desk_proposals (
    id TEXT PRIMARY KEY,                -- P-YYYYMMDD-NN
    created_day TEXT,
    target_file TEXT, config_key TEXT,
    current_value TEXT, proposed_value TEXT, diff TEXT,
    rationale TEXT, evidence_bar TEXT, expected_effect TEXT,
    effort TEXT, rank INTEGER, hypothesis_id TEXT,
    status TEXT,                        -- proposed | applied_by_human | rejected | withdrawn
    applied INTEGER DEFAULT 0,          -- the desk NEVER sets this; humans do
    updated_ts TEXT, run_id TEXT
)
"""

DDL_GAP = """
CREATE TABLE IF NOT EXISTS desk_gap (
    day TEXT, scope TEXT,
    n_trades REAL, win_rate REAL, payoff REAL,
    realised_daily_book_pct REAL, friction_book_pct REAL,
    required_win_rate REAL, required_payoff REAL,
    required_edge_per_trade_book_pct REAL, required_edge_per_trade_inr REAL,
    required_edge_per_trade_r REAL, required_gross_book_pct REAL,
    win_rate_gap_pp REAL, daily_shortfall_pct REAL,
    closable_by_parameter_work INTEGER, verdict TEXT,
    created_ts TEXT, run_id TEXT,
    PRIMARY KEY (day, scope)
)
"""

DDL_REVIEWS = """
CREATE TABLE IF NOT EXISTS desk_reviews (
    day TEXT PRIMARY KEY,
    integrity_verdict TEXT, n_trades INTEGER,
    book_return_pct REAL, sum_net_pct REAL, sum_inr REAL,
    gap_verdict TEXT, report_path TEXT,
    n_findings INTEGER, n_hypotheses INTEGER, n_proposals INTEGER,
    llm_used INTEGER, llm_cost_inr REAL,
    created_ts TEXT, run_id TEXT
)
"""

# What we believe about this strategy right now, and why. Seeded from the repo's SETTLED
# verdicts (docs/STRATEGY_IMPROVEMENT_PLAN_2026-07.md, SPIKE_HUNTER_FINDINGS.md, the PEAD /
# jump-drift arc). These are priors, not hypotheses: they have already been tested, several of
# them three times. The desk must not re-open them without new evidence that clears the bar.
STANDING_PRIORS: List[str] = [
    "`vwap_ema_adx` has NO measurable gross edge: over 136 trades the book decomposed to gross "
    "+6.43% = tide +2.69% + alpha +3.74% with 11.20% friction -> net -4.77%. The picks are "
    "microscopically positive; friction ate them three times over.",
    "Friction is deterministic and arithmetic: ~9.2bps proportional charges + ~6bps modelled "
    "slippage. Size does NOT reduce it (10.4 -> 9.6bps at 2x notional). Only trade COUNT and "
    "STOP WIDTH are levers.",
    "The rule score / 'confidence' is non-predictive — corr(conf, win) ~ +0.09 in both halves "
    "over 136 trades, and 89% of trades score >=85. It gates nothing and predicts nothing.",
    "The universe allowlist is NOT justified — killed three times by data (OLAELEC, a "
    "drop-listed smid, was the book's best name both times it was tested). "
    "`live_universe.restrict_to_allowlist` stays OFF.",
    "Exit geometry is not the lever: trailing exits were WORSE (-4.97% vs -3.85% baseline) and "
    "hold-to-90m only helped inside the tight-stop cohort the P0 gate now removes. On the "
    "P0-gated cohort baseline exits win. Keep current exits.",
    "In-sample counterfactuals are scale, never proof. The 'max 5 trades/day -> +6.9%' result "
    "was a pure arrival-order artifact.",
    "Direction is ~unforecastable in this market at this horizon: the movers sleeve called "
    "direction correctly 33% of the time on 18 calls (worse than a coin flip) while magnitude "
    "prediction ran at 8.2x lift. You can predict THAT a name moves, not WHICH WAY.",
    "The best signal the research arc ever found (earnings-day jump-drift) is real but its "
    "alpha lives on the short leg of mid-caps that Indian rules make un-shortable. No sleeve.",
    "1,693 intraday + 195 swing variants have been tested; NONE cleared `edge_verdict`.",
]


def _path_from_url(db_url: str) -> str:
    return db_url[len("sqlite:///"):] if db_url.startswith("sqlite:///") else db_url


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DeskStore:
    """Own-tables SQLite store for the desk. Same DB file as the trading tables, never collides."""

    def __init__(self, db_url: str = "sqlite:///data/signal_engine.sqlite3",
                 run_id: Optional[str] = None, read_only: bool = False):
        self.conn = sqlite3.connect(_path_from_url(db_url), timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.run_id = run_id
        if read_only:
            # Dashboard reads during market hours: skip DDL (write lock) unless the tables are
            # genuinely absent, exactly as MicrostructureStore does.
            exists = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='desk_reviews'"
            ).fetchone()
            if exists is None:
                self._init()
            return
        self._init()

    def _init(self) -> None:
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        for ddl in (DDL_HYPOTHESES, DDL_PROPOSALS, DDL_GAP, DDL_REVIEWS):
            self.conn.execute(ddl)
        self.conn.commit()

    # ---------------------------------------------------------------- hypotheses
    def next_id(self, table: str, prefix: str, day: str) -> str:
        stem = "{}-{}-".format(prefix, day.replace("-", ""))
        row = self.conn.execute(
            "SELECT COUNT(*) FROM {} WHERE id LIKE ?".format(table), (stem + "%",)).fetchone()
        return "{}{:02d}".format(stem, int(row[0]) + 1)

    def open_hypothesis(self, day: str, statement: str, rationale: str,
                        pre_registered_test: str, falsifiable_prediction: str,
                        metric: str, direction: str, threshold: float,
                        min_sessions: int, min_n: int) -> str:
        """Insert a hypothesis, idempotent on ``statement``.

        Returning the EXISTING id for a repeated statement is the anti-amnesia mechanism: a
        refuted idea re-proposed tonight does not become a new open hypothesis, it just points
        back at its own gravestone."""
        existing = self.conn.execute(
            "SELECT id FROM desk_hypotheses WHERE statement = ?", (statement,)).fetchone()
        if existing:
            return existing["id"]
        hid = self.next_id("desk_hypotheses", "H", day)
        self.conn.execute(
            """INSERT INTO desk_hypotheses
               (id, created_day, statement, rationale, pre_registered_test,
                falsifiable_prediction, metric, direction, threshold, min_sessions, min_n,
                status, updated_ts, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'open',?,?)""",
            (hid, day, statement, rationale, pre_registered_test, falsifiable_prediction,
             metric, direction, float(threshold), int(min_sessions), int(min_n),
             _now(), self.run_id))
        self.conn.commit()
        return hid

    def fetch_hypotheses(self, status: Optional[str] = None, limit: int = 200) -> List[Dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM desk_hypotheses WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM desk_hypotheses ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def set_hypothesis_outcome(self, hid: str, status: str, outcome_value: Optional[float],
                               lesson: str, graded_day: str) -> None:
        self.conn.execute(
            """UPDATE desk_hypotheses SET status = ?, outcome_value = ?, lesson = ?,
                      graded_day = ?, updated_ts = ? WHERE id = ?""",
            (status, outcome_value, lesson, graded_day, _now(), hid))
        self.conn.commit()

    def dead_ideas(self) -> List[str]:
        """Statements already refuted — the generator must not re-propose these."""
        return [r["statement"] for r in self.conn.execute(
            "SELECT statement FROM desk_hypotheses WHERE status IN ('refuted','stale')")]

    def known_statements(self) -> List[str]:
        return [r["statement"] for r in self.conn.execute(
            "SELECT statement FROM desk_hypotheses")]

    def priors(self) -> List[str]:
        """Standing priors plus every lesson the desk has learned by grading its own work."""
        learned = [r["lesson"] for r in self.conn.execute(
            "SELECT lesson FROM desk_hypotheses WHERE lesson IS NOT NULL AND lesson != '' "
            "ORDER BY graded_day DESC LIMIT 20") if r["lesson"]]
        return list(STANDING_PRIORS) + learned

    # ---------------------------------------------------------------- proposals
    def add_proposal(self, day: str, target_file: str, config_key: str,
                     current_value: str, proposed_value: str, diff: str,
                     rationale: str, evidence_bar: str, expected_effect: str,
                     effort: str, rank: int, hypothesis_id: Optional[str] = None) -> str:
        """Record a PROPOSED change. `applied` is hard-coded 0 — the desk cannot apply anything.

        Idempotent on (created_day, config_key) while the row is still `proposed`: re-running the
        review for a day REPLACES that day's proposal for a key instead of stacking duplicates.
        Once a human has moved the row out of `proposed` (applied / rejected / withdrawn) it is
        history and is never touched — a fresh row is inserted alongside it instead.
        """
        existing = self.conn.execute(
            "SELECT id, status FROM desk_proposals WHERE created_day = ? AND config_key = ? "
            "ORDER BY id LIMIT 1", (day, config_key)).fetchone()
        if existing is not None and existing["status"] == "proposed":
            self.conn.execute(
                """UPDATE desk_proposals SET target_file = ?, current_value = ?,
                          proposed_value = ?, diff = ?, rationale = ?, evidence_bar = ?,
                          expected_effect = ?, effort = ?, rank = ?, hypothesis_id = ?,
                          updated_ts = ?, run_id = ? WHERE id = ?""",
                (target_file, str(current_value), str(proposed_value), diff, rationale,
                 evidence_bar, expected_effect, effort, int(rank), hypothesis_id,
                 _now(), self.run_id, existing["id"]))
            self.conn.commit()
            return existing["id"]
        pid = self.next_id("desk_proposals", "P", day)
        self.conn.execute(
            """INSERT INTO desk_proposals
               (id, created_day, target_file, config_key, current_value, proposed_value, diff,
                rationale, evidence_bar, expected_effect, effort, rank, hypothesis_id,
                status, applied, updated_ts, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'proposed',0,?,?)""",
            (pid, day, target_file, config_key, str(current_value), str(proposed_value), diff,
             rationale, evidence_bar, expected_effect, effort, int(rank), hypothesis_id,
             _now(), self.run_id))
        self.conn.commit()
        return pid

    def fetch_proposals(self, day: Optional[str] = None, status: Optional[str] = None,
                        limit: int = 200) -> List[Dict]:
        clauses, args = ["1=1"], []
        if day:
            clauses.append("created_day = ?")
            args.append(day)
        if status:
            clauses.append("status = ?")
            args.append(status)
        args.append(limit)
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM desk_proposals WHERE {} ORDER BY created_day DESC, rank ASC "
            "LIMIT ?".format(" AND ".join(clauses)), args)]

    # ---------------------------------------------------------------- gap + reviews
    def save_gap(self, row: Dict) -> None:
        cols = ("day", "scope", "n_trades", "win_rate", "payoff", "realised_daily_book_pct",
                "friction_book_pct", "required_win_rate", "required_payoff",
                "required_edge_per_trade_book_pct", "required_edge_per_trade_inr",
                "required_edge_per_trade_r", "required_gross_book_pct", "win_rate_gap_pp",
                "daily_shortfall_pct", "closable_by_parameter_work", "verdict")
        self.conn.execute(
            "INSERT OR REPLACE INTO desk_gap ({}, created_ts, run_id) VALUES ({},?,?)".format(
                ", ".join(cols), ",".join("?" * len(cols))),
            tuple(row.get(c) for c in cols) + (_now(), self.run_id))
        self.conn.commit()

    def fetch_gap_series(self, scope: str = "trailing", limit: int = 90) -> List[Dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM desk_gap WHERE scope = ? ORDER BY day DESC LIMIT ?", (scope, limit))]

    def save_review(self, row: Dict) -> None:
        cols = ("day", "integrity_verdict", "n_trades", "book_return_pct", "sum_net_pct",
                "sum_inr", "gap_verdict", "report_path", "n_findings", "n_hypotheses",
                "n_proposals", "llm_used", "llm_cost_inr")
        self.conn.execute(
            "INSERT OR REPLACE INTO desk_reviews ({}, created_ts, run_id) "
            "VALUES ({},?,?)".format(", ".join(cols), ",".join("?" * len(cols))),
            tuple(row.get(c) for c in cols) + (_now(), self.run_id))
        self.conn.commit()

    def fetch_reviews(self, limit: int = 60) -> List[Dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM desk_reviews ORDER BY day DESC LIMIT ?", (limit,))]

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------------------------- #
# Metric registry — the ONLY quantities a pre-registered test may be written against.
# Deliberately small and deterministic. A hypothesis that cannot be phrased in terms of one
# of these cannot be auto-graded, and the analyst must not open it.
# ------------------------------------------------------------------------------------- #

def _stop_pct(t: Dict) -> Optional[float]:
    e, s = t.get("entry_fill"), t.get("stop_loss")
    return abs(float(e) - float(s)) / float(e) * 100.0 if e and s else None


def _m_cost_share(trades, sessions, equity) -> Optional[float]:
    gross = abs(sum(float(t.get("pnl_pct_gross") or 0.0) for t in trades))
    cost = sum(float(t.get("cost_pct") or 0.0) for t in trades)
    return (cost / gross) if gross else None


def _m_avg_r(trades, sessions, equity) -> Optional[float]:
    return (sum(float(t.get("r_multiple") or 0.0) for t in trades) / len(trades)
            if trades else None)


def _m_book_net_pct(trades, sessions, equity) -> Optional[float]:
    if not equity:
        return None
    return sum(float(t.get("pnl_inr") or 0.0) for t in trades) / equity * 100.0


def _m_win_rate(trades, sessions, equity) -> Optional[float]:
    if not trades:
        return None
    return len([t for t in trades if (t.get("pnl_pct_net") or 0.0) > 0]) / len(trades)


def _m_trades_per_day(trades, sessions, equity) -> Optional[float]:
    return len(trades) / max(1.0, float(sessions or 1))


def _m_mean_stop_pct(trades, sessions, equity) -> Optional[float]:
    stops = [s for s in (_stop_pct(t) for t in trades) if s]
    return (sum(stops) / len(stops)) if stops else None


def _m_mean_pct_of_book(trades, sessions, equity) -> Optional[float]:
    if not equity:
        return None
    fr = [float(t["notional_entry"]) / equity * 100.0 for t in trades if t.get("notional_entry")]
    return (sum(fr) / len(fr)) if fr else None


def _m_friction_decided_share(trades, sessions, equity) -> Optional[float]:
    if not trades:
        return None
    hit = 0
    for t in trades:
        g, c = t.get("pnl_pct_gross"), t.get("cost_pct")
        if g is not None and c is not None and float(g) > 0 and float(g) - float(c) <= 0:
            hit += 1
    return hit / len(trades)


METRICS = {
    "cost_share_of_gross": _m_cost_share,
    "avg_r": _m_avg_r,
    "book_net_pct": _m_book_net_pct,
    "win_rate": _m_win_rate,
    "trades_per_day": _m_trades_per_day,
    "mean_stop_pct": _m_mean_stop_pct,
    "mean_pct_of_book": _m_mean_pct_of_book,
    "friction_decided_share": _m_friction_decided_share,
}


@dataclass
class Grade:
    hypothesis_id: str
    statement: str
    status: str
    metric: str
    value: Optional[float]
    threshold: float
    direction: str
    sessions: int
    n: int
    lesson: str


def grade_open_hypotheses(store: DeskStore, facts) -> List[Grade]:
    """Grade every open hypothesis against the GATED-ERA evidence.

    Gated-era only, deliberately: every hypothesis the analyst opens is a claim about the engine
    as currently configured, and the full trailing window still contains pre-P0 sessions
    (~15 trades/day, stops down to 0.33%). Grading a gated-era claim on mixed-regime data would
    be the same category error as grading it early.

    Below its own pre-registered floor a hypothesis STAYS OPEN with an explicit
    insufficient-evidence lesson. Grading early — the temptation the P0 evidence bar exists to
    remove — is what turns a nightly review into a forking-paths machine.
    """
    from signal_engine.desk.gap import GATED_ERA_START

    tr = facts.trailing or {}
    all_rows = tr.get("trades") or []
    trades = [r for r in all_rows if str(r.get("entry_ts") or "")[:10] >= GATED_ERA_START]
    sessions = len({str(r.get("entry_ts") or "")[:10] for r in trades})
    if not trades:  # nothing in the gated era yet (fresh DB / tests) — fall back to what we have
        trades, sessions = all_rows, int(tr.get("sessions") or 0)
    equity = float((facts.equity or {}).get("open_equity")
                   or facts.config.get("starting_capital") or 100000.0)
    out: List[Grade] = []
    for h in store.fetch_hypotheses(status="open"):
        fn = METRICS.get(h.get("metric") or "")
        if fn is None:
            continue
        value = fn(trades, sessions, equity)
        floor_ok = (sessions >= int(h.get("min_sessions") or 0)
                    and len(trades) >= int(h.get("min_n") or 0))
        if value is None or not floor_ok:
            lesson = ("Not graded on {}: evidence floor not met ({} of {} sessions, {} of {} "
                      "trades). The pre-registered test stands; no peeking-and-tweaking in "
                      "between.".format(facts.day, sessions, h.get("min_sessions"),
                                        len(trades), h.get("min_n")))
            out.append(Grade(h["id"], h["statement"], "open", h.get("metric") or "", value,
                             float(h.get("threshold") or 0.0), h.get("direction") or "gt",
                             sessions, len(trades), lesson))
            continue
        thr = float(h.get("threshold") or 0.0)
        met = (value > thr) if (h.get("direction") == "gt") else (value < thr)
        status = "supported" if met else "refuted"
        lesson = (
            "Graded {} on {}: {} = {:.4f} vs the pre-registered bar {} {:.4f} over {} sessions / "
            "{} trades. {} The bar was fixed when the hypothesis was opened on {}, so this is a "
            "clean read, not a fitted one.".format(
                status.upper(), facts.day, h.get("metric"), value,
                ">" if h.get("direction") == "gt" else "<", thr, sessions, len(trades),
                "The predicted effect held." if met else
                "The predicted effect did NOT hold — record this idea as dead and stop "
                "re-proposing it.",
                h.get("created_day")))
        store.set_hypothesis_outcome(h["id"], status, value, lesson, facts.day)
        out.append(Grade(h["id"], h["statement"], status, h.get("metric") or "", value, thr,
                         h.get("direction") or "gt", sessions, len(trades), lesson))
    return out
