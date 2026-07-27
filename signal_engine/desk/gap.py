"""The 1%/day gap model — what would have to be true, and how far we are.

The user's stated target is **+1.00% net per day on the ₹1,00,000 paper book**, after brokerage,
statutory charges and slippage. This module solves for what each factor would have to be, every
night, and tracks the answer as a time series so progress (or its absence) is visible over weeks.

The arithmetic, stated once so the report can cite it:

    daily_net_book_pct = n * (p * W - (1 - p) * L)

with `n` trades/day, win rate `p`, mean winner `W` and mean loser `L`, all expressed as a
percentage **of the book** (not of the position). Solving for each factor at a target `T`:

    required win rate      p* = (T/n + L) / (W + L)
    required payoff        b* = (T/(n*L) + (1 - p)) / p          where b = W/L
    required per-trade edge e* = T/n                              (book-%; x equity/100 for ₹)
    required gross         G* = T + daily_friction_book_pct

Book-% is the right unit because the target is a book target. Per-trade percentages are NOT
interchangeable with it: on 2026-07-22 the eight trades summed to -4.43% per-trade while the
book moved -2.59%, because positions ran at 30-85% of equity. Mixing the two units is the
easiest way to accidentally overstate both the loss and the progress.

**Context the model must never be allowed to hide.** 1%/day compounds to roughly +250%/year.
No institutional systematic desk sustains that; the best target ~1-3% per *month* gross. So the
honest output of this module is usually a large, named, non-closable gap — and saying so is the
product. `verdict_line()` is deliberately blunt, and `closable_by_parameter_work` is False
unless the gap is genuinely small.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

TARGET_DAILY_NET_PCT = 1.00

# A win-rate gap this small could plausibly be closed by parameter/selection work. Anything
# larger is a signal problem, and the model says so rather than implying a tuning path exists.
# Chosen (not fitted) to be roughly the width of a 95% bootstrap CI on ~150 trades' win rate.
CLOSABLE_WR_GAP_PP = 5.0

TRADING_DAYS_PER_YEAR = 250

# First session with the P0 friction-in-R gate live (risk.max_cost_r: 0.25 — see
# STRATEGY_IMPROVEMENT_PLAN_2026-07 §6 "gated era day-1"). Trades before this date came from a
# materially different configuration (~15 trades/day, stops down to 0.33%), so a trailing window
# that spans the boundary mixes two regimes. The `gated` scope exists to avoid quoting that mix
# as if it described today's engine.
GATED_ERA_START = "2026-07-13"


@dataclass
class GapModel:
    """The solved gap for one session (or a trailing window). All percentages are BOOK-%."""

    scope: str                      # "session" | "trailing"
    day: str
    n_trades: float
    win_rate: Optional[float]
    mean_winner_book_pct: Optional[float]
    mean_loser_book_pct: Optional[float]        # positive magnitude
    payoff: Optional[float]
    realised_daily_book_pct: Optional[float]
    friction_book_pct: Optional[float]
    required_win_rate: Optional[float] = None
    required_payoff: Optional[float] = None
    required_edge_per_trade_book_pct: Optional[float] = None
    required_edge_per_trade_inr: Optional[float] = None
    required_edge_per_trade_r: Optional[float] = None
    required_gross_book_pct: Optional[float] = None
    friction_share_of_target: Optional[float] = None
    win_rate_gap_pp: Optional[float] = None
    daily_shortfall_pct: Optional[float] = None
    annualised_target_pct: float = 0.0
    closable_by_parameter_work: bool = False
    impossible_at_any_win_rate: bool = False
    notes: List[str] = field(default_factory=list)
    inputs: Dict = field(default_factory=dict)

    def verdict_line(self) -> str:
        """One sentence, with n and numbers, that a sceptic could not call optimistic."""
        if self.n_trades <= 0 or self.win_rate is None:
            return ("Gap to +1.00%/day: NOT COMPUTABLE — no trades in scope. The target is "
                    "unmeasured, not met.")
        if self.impossible_at_any_win_rate:
            return ("+1.00%/day is UNREACHABLE at the current payoff ({:.2f}:1) and trade count "
                    "({:.1f}/day): even a 100% win rate yields only {:+.2f}%/day. The gap is not a "
                    "win-rate gap — it is a payoff-and-size gap.".format(
                        self.payoff or 0.0, self.n_trades,
                        self.n_trades * (self.mean_winner_book_pct or 0.0)))
        gap = self.win_rate_gap_pp or 0.0
        return ("+1.00%/day needs a {:.0f}% win rate at the realised {:.2f}:1 payoff and {:.1f} "
                "trades/day; realised win rate is {:.0f}% (n={} trades over {:.0f} session(s)). "
                "Gap: {:.0f}pp. {}".format(
                    (self.required_win_rate or 0.0) * 100.0, self.payoff or 0.0, self.n_trades,
                    (self.win_rate or 0.0) * 100.0, int(self.inputs.get("n_returns") or 0),
                    float(self.inputs.get("sessions") or 1), gap,
                    "Within parameter-tuning reach." if self.closable_by_parameter_work else
                    "No parameter change bridges {:.0f}pp of win rate — this is a SIGNAL "
                    "problem, not a settings problem.".format(gap)))

    def as_row(self) -> Dict:
        """Flat dict for persistence in `desk_gap` and for the /api/desk time series."""
        return {
            "day": self.day, "scope": self.scope, "n_trades": self.n_trades,
            "win_rate": self.win_rate, "payoff": self.payoff,
            "realised_daily_book_pct": self.realised_daily_book_pct,
            "friction_book_pct": self.friction_book_pct,
            "required_win_rate": self.required_win_rate,
            "required_payoff": self.required_payoff,
            "required_edge_per_trade_book_pct": self.required_edge_per_trade_book_pct,
            "required_edge_per_trade_inr": self.required_edge_per_trade_inr,
            "required_edge_per_trade_r": self.required_edge_per_trade_r,
            "required_gross_book_pct": self.required_gross_book_pct,
            "win_rate_gap_pp": self.win_rate_gap_pp,
            "daily_shortfall_pct": self.daily_shortfall_pct,
            "closable_by_parameter_work": int(self.closable_by_parameter_work),
            "verdict": self.verdict_line(),
        }


def required_win_rate(target: float, n: float, winner: float, loser: float) -> Optional[float]:
    """p* = (T/n + L) / (W + L). None when the denominator vanishes."""
    if n <= 0 or (winner + loser) == 0:
        return None
    return (target / n + loser) / (winner + loser)


def required_payoff(target: float, n: float, p: float, loser: float) -> Optional[float]:
    """b* = (T/(n*L) + (1-p)) / p, i.e. the W/L ratio that hits the target at the current p."""
    if n <= 0 or p <= 0 or loser <= 0:
        return None
    return (target / (n * loser) + (1.0 - p)) / p


def book_returns(trades: List[Dict], equity: float,
                 methods: Optional[Dict[str, int]] = None) -> List[float]:
    """Each trade's contribution to the BOOK in %, i.e. ₹P&L / opening equity x 100.

    Falls back to `pnl_pct_net x notional/equity` for legacy rows with no ₹ columns, and to the
    raw per-trade % as a last resort. ``methods`` (if given) accumulates how many rows used each
    path, so the caller can state the approximation instead of hiding it: pre-ledger rows
    (before 2026-07-03) have no ₹ columns at all and their "book %" is really a position %,
    which OVERSTATES their weight in a book-% average.
    """
    out: List[float] = []
    for t in trades:
        inr = t.get("pnl_inr")
        if inr is not None and equity:
            out.append(float(inr) / equity * 100.0)
            if methods is not None:
                methods["exact_inr"] = methods.get("exact_inr", 0) + 1
            continue
        pct, notional = t.get("pnl_pct_net"), t.get("notional_entry")
        if pct is not None and notional and equity:
            out.append(float(pct) * float(notional) / equity)
            if methods is not None:
                methods["scaled_by_notional"] = methods.get("scaled_by_notional", 0) + 1
        elif pct is not None:
            out.append(float(pct))
            if methods is not None:
                methods["raw_trade_pct"] = methods.get("raw_trade_pct", 0) + 1
    return out


def _mean_stop_pct(trades: List[Dict]) -> Optional[float]:
    stops = []
    for t in trades:
        e, s = t.get("entry_fill"), t.get("stop_loss")
        if e and s:
            stops.append(abs(float(e) - float(s)) / float(e) * 100.0)
    return (sum(stops) / len(stops)) if stops else None


def _mean_size_frac(trades: List[Dict], equity: float) -> Optional[float]:
    fracs = [float(t["notional_entry"]) / equity for t in trades
             if t.get("notional_entry") and equity]
    return (sum(fracs) / len(fracs)) if fracs else None


def solve_gap(trades: List[Dict], equity: float, day: str, scope: str = "session",
              sessions: float = 1.0, charges_inr: float = 0.0,
              target: float = TARGET_DAILY_NET_PCT) -> GapModel:
    """Solve the 1%/day gap from a set of trades.

    ``sessions`` lets the same solver serve a trailing window: trade count is divided by it so
    ``n`` is always *trades per day*, and the realised daily figure is a per-day average.
    """
    equity = float(equity or 0.0)
    methods: Dict[str, int] = {}
    rets = book_returns(trades, equity, methods)
    sessions = max(1.0, float(sessions or 1.0))
    n_per_day = len(rets) / sessions
    model = GapModel(
        scope=scope, day=day, n_trades=n_per_day, win_rate=None,
        mean_winner_book_pct=None, mean_loser_book_pct=None, payoff=None,
        realised_daily_book_pct=(sum(rets) / sessions) if rets else None,
        friction_book_pct=(charges_inr / equity * 100.0 / sessions) if equity else None,
        annualised_target_pct=((1.0 + target / 100.0) ** TRADING_DAYS_PER_YEAR - 1.0) * 100.0,
    )
    model.inputs = {"n_returns": len(rets), "sessions": sessions, "equity": equity,
                    "charges_inr": charges_inr, "target": target, "methods": methods}
    if not rets:
        model.notes.append("No trades in scope — the gap is unmeasured, not met.")
        return model
    if methods.get("raw_trade_pct"):
        model.notes.append(
            "DATA QUALITY: {} of {} trades in scope predate the ₹ ledger and have no notional, so "
            "their contribution is approximated by the raw per-trade % — which OVERSTATES their "
            "weight. Treat this scope's win rate and payoff as indicative; the `gated` scope is "
            "the clean read.".format(methods["raw_trade_pct"], len(rets)))

    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r <= 0]
    model.win_rate = len(wins) / len(rets)
    model.mean_winner_book_pct = (sum(wins) / len(wins)) if wins else 0.0
    model.mean_loser_book_pct = (sum(losses) / len(losses)) if losses else 0.0
    if model.mean_loser_book_pct:
        model.payoff = model.mean_winner_book_pct / model.mean_loser_book_pct

    W, L, p = model.mean_winner_book_pct, model.mean_loser_book_pct, model.win_rate
    model.required_win_rate = required_win_rate(target, n_per_day, W, L)
    model.required_payoff = required_payoff(target, n_per_day, p, L)
    model.required_edge_per_trade_book_pct = target / n_per_day if n_per_day else None
    if model.required_edge_per_trade_book_pct is not None and equity:
        model.required_edge_per_trade_inr = \
            model.required_edge_per_trade_book_pct / 100.0 * equity
    size_frac, stop_pct = _mean_size_frac(trades, equity), _mean_stop_pct(trades)
    if model.required_edge_per_trade_book_pct is not None and size_frac and stop_pct:
        # book-% -> trade-% (divide by position size as a fraction of the book) -> R (divide by
        # the stop distance in %). This is the number a signal would have to produce per trade.
        trade_pct = model.required_edge_per_trade_book_pct / size_frac
        model.required_edge_per_trade_r = trade_pct / stop_pct
    if model.friction_book_pct is not None:
        model.required_gross_book_pct = target + model.friction_book_pct
        model.friction_share_of_target = model.friction_book_pct / target if target else None
    if model.realised_daily_book_pct is not None:
        model.daily_shortfall_pct = target - model.realised_daily_book_pct

    # Feasibility. If every single trade won and the day still fell short, no win rate suffices.
    if n_per_day > 0 and n_per_day * W < target:
        model.impossible_at_any_win_rate = True
        model.notes.append(
            "Even at a 100% win rate, {:.1f} trades/day x {:.3f}% mean winner = {:.2f}%/day < "
            "{:.2f}% target. Win rate cannot close this; only bigger winners or more trades "
            "can — and both raise the friction bill.".format(n_per_day, W, n_per_day * W, target))
    if model.required_win_rate is not None:
        model.win_rate_gap_pp = (model.required_win_rate - p) * 100.0
        if model.required_win_rate > 1.0:
            model.impossible_at_any_win_rate = True
        model.closable_by_parameter_work = bool(
            not model.impossible_at_any_win_rate
            and model.win_rate_gap_pp is not None
            and model.win_rate_gap_pp <= CLOSABLE_WR_GAP_PP)
    model.notes.append(
        "+{:.2f}%/day compounds to {:+.0f}%/year over {} sessions. For scale: the best "
        "systematic funds target ~1-3% per MONTH gross.".format(
            target, model.annualised_target_pct, TRADING_DAYS_PER_YEAR))
    if model.friction_share_of_target:
        model.notes.append(
            "Friction alone consumed {:.0f}% of a 1% day ({:.3f}% of the book in charges + "
            "modelled slippage over {:.1f} trades/day).".format(
                model.friction_share_of_target * 100.0, model.friction_book_pct, n_per_day))
    return model


def solve_from_facts(facts, target: float = TARGET_DAILY_NET_PCT) -> Dict[str, GapModel]:
    """Session, trailing and gated-era gap models straight off a `SessionFacts` bundle.

    Three scopes on purpose. `session` is tonight (always low-n). `trailing` is the last ~20
    sessions and spans the pre-P0 configuration, so it is context, not a verdict on today's
    engine. `gated` is the P0-era subset — the only window that describes the engine as it is
    actually configured, and the window the pre-registered P0 bar is written against.
    """
    equity = float((facts.equity or {}).get("open_equity")
                   or facts.config.get("starting_capital") or 100000.0)
    session = solve_gap(facts.trades, equity, facts.day, scope="session", sessions=1.0,
                        charges_inr=facts.sum_charges_inr(), target=target)
    tr = facts.trailing or {}
    all_rows = tr.get("trades") or []
    trailing = solve_gap(
        all_rows, equity, facts.day, scope="trailing",
        sessions=float(tr.get("sessions") or 1),
        charges_inr=sum(float(r.get("charges_inr") or 0.0) for r in all_rows),
        target=target)
    gated_rows = [r for r in all_rows if str(r.get("entry_ts") or "")[:10] >= GATED_ERA_START]
    gated_days = len({str(r.get("entry_ts") or "")[:10] for r in gated_rows})
    gated = solve_gap(
        gated_rows, equity, facts.day, scope="gated", sessions=float(gated_days or 1),
        charges_inr=sum(float(r.get("charges_inr") or 0.0) for r in gated_rows), target=target)
    gated.notes.insert(0, "Scope = sessions on/after {} only (the P0 friction-in-R gate went "
                          "live that day); {} session(s) in window.".format(
                              GATED_ERA_START, gated_days))
    return {"session": session, "trailing": trailing, "gated": gated}


def what_must_be_true(model: GapModel) -> List[str]:
    """The explicit "what would have to change" list — one bullet per factor, with numbers."""
    if model.n_trades <= 0 or model.win_rate is None:
        return ["No trades in scope: nothing to solve. Do not read this as progress."]
    out = []
    if model.required_win_rate is not None:
        if model.required_win_rate > 1.0:
            out.append("Win rate: would need {:.0f}% — impossible. Rules out the win-rate "
                       "route entirely at this payoff and trade count.".format(
                           model.required_win_rate * 100.0))
        else:
            out.append("Win rate: {:.0f}% -> {:.0f}% (a {:.0f}pp improvement) at the current "
                       "{:.2f}:1 payoff and {:.1f} trades/day.".format(
                           model.win_rate * 100.0, model.required_win_rate * 100.0,
                           model.win_rate_gap_pp or 0.0, model.payoff or 0.0, model.n_trades))
    if model.required_payoff is not None:
        out.append("Payoff: {:.2f}:1 -> {:.2f}:1 at the current {:.0f}% win rate — winners would "
                   "have to be {:.1f}x their current size relative to losers.".format(
                       model.payoff or 0.0, model.required_payoff, model.win_rate * 100.0,
                       (model.required_payoff / model.payoff) if model.payoff else 0.0))
    if model.required_edge_per_trade_book_pct is not None:
        r_txt = ("" if model.required_edge_per_trade_r is None
                 else " = {:+.2f}R per trade at the realised stop width and position size".format(
                     model.required_edge_per_trade_r))
        out.append("Per-trade net edge: {:+.3f}% of the book (~₹{:,.0f}) on every trade{}. "
                   "The realised per-trade average is {:+.3f}% of the book.".format(
                       model.required_edge_per_trade_book_pct,
                       model.required_edge_per_trade_inr or 0.0, r_txt,
                       (model.realised_daily_book_pct / model.n_trades)
                       if model.n_trades else 0.0))
    if model.required_gross_book_pct is not None:
        out.append("Gross: {:+.2f}% of the book per day before friction (target {:.2f}% + "
                   "{:.3f}% friction). Realised gross is the only thing that can pay for "
                   "friction; friction is deterministic.".format(
                       model.required_gross_book_pct, model.inputs.get("target", 1.0),
                       model.friction_book_pct or 0.0))
    if model.impossible_at_any_win_rate:
        out.append("VERDICT: not closable by parameter work. The binding constraint is the size "
                   "of the edge per trade, and there is no measured gross edge to scale.")
    return out
