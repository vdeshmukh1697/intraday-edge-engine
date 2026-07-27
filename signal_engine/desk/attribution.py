"""P&L attribution, TCA, risk governance and bucket analysis — with hard low-n guards.

This is the institutional half of the desk: know *where* the money came from and went before
saying anything about strategy quality. Four decompositions, in the order a real desk reads them:

1. **P&L attribution** — gross = tide (index beta) + alpha (picking), net = gross - friction.
   The columns exist on every trade (`pnl_pct_gross`, `nifty_ret_pct`, `alpha_pct`, `cost_pct`,
   backfilled 136/136 in P3.1/P3.2), so this is arithmetic, not modelling.
2. **TCA** — cost as a share of |gross|, cost in R-units against the stop (the friction-in-R
   finding), and implementation shortfall (plan price vs actual fill).
3. **Risk governance** — concentration, effective diversification, correlated-basket bursts,
   drawdown, and whether the breaker's own arithmetic matches the book's.
4. **Pre-trade vs post-trade** — planned `expected_move_pct` / `risk_reward` against realised.
   Systematic optimism is a finding in itself.

**The low-n guard is not decoration.** With 4-8 trades/day, every bucket is noise, and this
repo has already been burned three times by acting on one: the universe allowlist (killed on 3
names / 1 session, three times), the "max 5 trades/day -> +6.9%" arrival-order artifact, and
the LONG-vs-SHORT and time-of-day splits that reversed out of sample. So: no bucket below
`MIN_N_FOR_BUCKET` may be reported without the `LOW-N / NOT ACTIONABLE` tag, and
`Finding.actionable` is False for anything that fails its evidence floor. `analyst.py` prints
the tag; `ledger.py` refuses to open a hypothesis whose test cannot be met.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from signal_engine.desk.forensics import match_entry_plan

# Bucket findings need n>=20 before they may be called anything but an observation. Chosen to
# match the June/July analyses' own standard (per-bucket claims at n<20 were exactly the ones
# that reversed out of sample), not tuned.
MIN_N_FOR_BUCKET = 20

# Live-behaviour claims need >=10 sessions (the pre-registered bar in
# STRATEGY_IMPROVEMENT_PLAN_2026-07 §P0: "one look, session ~18" after 10 gated sessions).
MIN_SESSIONS_FOR_LIVE_CLAIM = 10

# Research claims need the standing edge_verdict bar. Never softened here; stated so the
# report can cite it verbatim.
EDGE_VERDICT_BAR = ("n>=2000 replayed, OOS split, PBO<0.10, plus DSR/multiple-testing "
                    "correction (signal_engine/research/overfitting.py)")

# Bucket dimensions this project has ALREADY tested and killed. When one of them lights up
# again, the finding must carry its own gravestone — otherwise the desk re-litigates a settled
# question every night and calls it an insight.
ALREADY_KILLED_DIMENSIONS = {
    "hour": (" NOTE: time-of-day was already killed as a one-session artifact in the 3- and "
             "8-session reviews (10-12 bleed, permutation p=0.27, reverses ex-2026-06-25) and "
             "was deliberately folded into a pre-registered P4 cohort rather than shipped as a "
             "live gate. It needs the standing bar, not a re-run."),
    "direction": (" NOTE: LONG-vs-SHORT was already killed as a one-session artifact (Fisher "
                  "p=0.27, sign-flips across sessions). Do not re-derive it."),
}


@dataclass
class Finding:
    """One statement the review is allowed to make, with its evidence class attached.

    ``kind``:
      * ``mechanical`` — arithmetic identity or a config fact. True at n=1. Actionable.
      * ``statistical`` — a claim about distributions. Needs n / sessions. Usually NOT actionable.
      * ``ops``        — an infrastructure fact (feed, jobs, instrumentation).
    """

    text: str
    kind: str
    n: Optional[int] = None
    sessions: Optional[int] = None
    actionable: bool = False
    tag: str = ""
    # Lower = more important. Only used to pick "the one thing that mattered" for the Telegram
    # digest, deterministically, so the push notification can never be driven by list order.
    priority: int = 50

    def rendered(self) -> str:
        bits = []
        if self.n is not None:
            bits.append("n={}".format(self.n))
        if self.sessions is not None:
            bits.append("sessions={}".format(self.sessions))
        meta = " (" + ", ".join(bits) + ")" if bits else ""
        tag = " **[{}]**".format(self.tag) if self.tag else ""
        return "[{}]{} {}{}".format(self.kind.upper(), meta, self.text, tag)


@dataclass
class Bucket:
    key: str
    n: int
    wins: int
    sum_net_pct: float
    sum_r: float
    sum_inr: float

    @property
    def win_rate(self) -> Optional[float]:
        return (self.wins / self.n) if self.n else None

    @property
    def avg_r(self) -> Optional[float]:
        return (self.sum_r / self.n) if self.n else None

    @property
    def actionable(self) -> bool:
        return self.n >= MIN_N_FOR_BUCKET

    def rendered(self) -> str:
        tag = "" if self.actionable else "  **[LOW-N / NOT ACTIONABLE]**"
        return ("{:<24} n={:<4} WR={:>5}  sumNet={:+7.2f}%  avgR={:>6}  ₹{:>9,.0f}{}".format(
            self.key, self.n,
            "{:.0f}%".format(self.win_rate * 100.0) if self.win_rate is not None else "n/a",
            self.sum_net_pct,
            "{:+.3f}".format(self.avg_r) if self.avg_r is not None else "n/a",
            self.sum_inr, tag))


@dataclass
class Attribution:
    day: str
    n_trades: int
    book: Dict = field(default_factory=dict)
    tca: Dict = field(default_factory=dict)
    governance: Dict = field(default_factory=dict)
    pre_vs_post: Dict = field(default_factory=dict)
    buckets: Dict[str, List[Bucket]] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)


def _f(row: Dict, key: str) -> Optional[float]:
    v = row.get(key)
    return None if v is None else float(v)


def _stop_pct(row: Dict) -> Optional[float]:
    e, s = row.get("entry_fill"), row.get("stop_loss")
    if not e or not s:
        return None
    return abs(float(e) - float(s)) / float(e) * 100.0


def decompose(facts, forensics: Optional[List] = None) -> Attribution:
    """Full attribution for one session, with trailing context where a claim needs it."""
    trades = facts.trades
    att = Attribution(day=facts.day, n_trades=len(trades))
    if not trades:
        att.findings.append(Finding(
            "No trades in this session — nothing to attribute.", "ops", n=0, actionable=False))
        att.book = {"n": 0}
        return att

    gross = sum((_f(t, "pnl_pct_gross") or 0.0) for t in trades)
    cost = sum((_f(t, "cost_pct") or 0.0) for t in trades)
    net = sum((_f(t, "pnl_pct_net") or 0.0) for t in trades)
    alpha = sum((_f(t, "alpha_pct") or 0.0) for t in trades
                if t.get("alpha_pct") is not None)
    tide = sum(((1.0 if t.get("direction") == "LONG" else -1.0)
                * (_f(t, "nifty_ret_pct") or 0.0)) for t in trades)
    n_alpha = len([t for t in trades if t.get("alpha_pct") is not None])
    wins = [t for t in trades if (_f(t, "pnl_pct_net") or 0.0) > 0]
    inr = sum((_f(t, "pnl_inr") or 0.0) for t in trades)
    charges = sum((_f(t, "charges_inr") or 0.0) for t in trades)
    eq = facts.equity or {}

    att.book = {
        "sum_gross_pct": gross, "sum_cost_pct": cost, "sum_net_pct": net,
        "alpha_pct": alpha, "tide_pct": tide, "n_alpha_resolved": n_alpha,
        "win_rate": len(wins) / len(trades),
        "sum_inr": inr, "charges_inr": charges,
        "book_return_pct": eq.get("book_return_pct"),
        "open_equity": eq.get("open_equity"), "close_equity": eq.get("close_equity"),
        "identity_residual_pp": (gross - cost) - net,
    }
    att.findings.append(Finding(
        "P&L decomposition: gross {:+.2f}% = tide {:+.2f}% + alpha {:+.2f}%; friction {:.2f}% "
        "-> net {:+.2f}% (per-trade sum). Book equity moved {:+.2f}% "
        "(₹{:,.0f} -> ₹{:,.0f}, realised ₹{:+,.0f} of which ₹{:,.0f} was charges).".format(
            gross, tide, alpha, cost, net, eq.get("book_return_pct") or 0.0,
            eq.get("open_equity") or 0.0, eq.get("close_equity") or 0.0, inr, charges),
        "mechanical", n=len(trades), actionable=True, priority=45))

    # The per-trade-% sum and the equity-weighted book return are DIFFERENT numbers whenever
    # positions are not all the same size. Naming the divergence prevents the review (and the
    # reader) from quoting a loss that the book did not take.
    br = eq.get("book_return_pct")
    if br is not None and abs(net - br) > 0.25:
        att.findings.append(Finding(
            "Per-trade %-sum ({:+.2f}%) and the book's equity-weighted return ({:+.2f}%) differ "
            "by {:.2f}pp because position sizes ranged widely. The book return is the true "
            "number; the %-sum is what engine/runner._LossBreaker uses for the daily drawdown "
            "cap, so the breaker is measuring a size-blind quantity.".format(
                net, br, abs(net - br)), "mechanical", n=len(trades), actionable=True,
            priority=15))

    _tca(att, facts, trades, forensics)
    _governance(att, facts, trades)
    _pre_vs_post(att, facts, trades)
    _buckets(att, facts, trades)
    _trailing_findings(att, facts)
    return att


def _tca(att: Attribution, facts, trades: List[Dict], forensics) -> None:
    """Transaction-cost analysis: cost share, cost-in-R, implementation shortfall."""
    gross_abs = abs(sum((_f(t, "pnl_pct_gross") or 0.0) for t in trades))
    cost = sum((_f(t, "cost_pct") or 0.0) for t in trades)
    stops = [s for s in (_stop_pct(t) for t in trades) if s]
    cost_r = []
    for t in trades:
        sp, cp = _stop_pct(t), _f(t, "cost_pct")
        if sp and cp is not None:
            cost_r.append(cp / sp)
    shortfalls = []
    for t in trades:
        # Nearest-in-time match: a symbol traded 3x in one session would otherwise be compared
        # against the LAST plan for that symbol, fabricating a large shortfall out of nothing.
        plan = match_entry_plan(facts.predictions, str(t.get("symbol") or ""),
                                str(t.get("entry_ts") or ""))
        if plan and plan.get("entry") and t.get("entry_fill"):
            sign = 1.0 if t.get("direction") == "LONG" else -1.0
            # Adverse shortfall is positive: a LONG that filled ABOVE the plan price paid up.
            shortfalls.append(sign * (float(t["entry_fill"]) - float(plan["entry"]))
                              / float(plan["entry"]) * 100.0)
    friction_decided = [f for f in (forensics or []) if f.friction_decided]
    att.tca = {
        "cost_share_of_abs_gross": (cost / gross_abs) if gross_abs else None,
        "median_stop_pct": sorted(stops)[len(stops) // 2] if stops else None,
        "mean_cost_in_r": (sum(cost_r) / len(cost_r)) if cost_r else None,
        "mean_impl_shortfall_pct": (sum(shortfalls) / len(shortfalls)) if shortfalls else None,
        "n_shortfall": len(shortfalls),
        "n_friction_decided": len(friction_decided),
        "friction_decided_ids": [f.trade_id for f in friction_decided],
        "modeled_friction_pct": facts.config.get("friction_pct_round_trip"),
        "modeled_charges_pct": facts.config.get("charges_pct_round_trip"),
    }
    if cost_r:
        med = att.tca["median_stop_pct"] or 0.0
        all_in = ((facts.config.get("friction_pct_round_trip") or 0.0) / med) if med else None
        att.findings.append(Finding(
            "Friction-in-R: median stop {:.2f}%; recorded charges alone cost {:.3f}R per trade, "
            "and all-in friction (charges + the {:.2f}%/side slippage already baked into the "
            "fills) costs {} per trade. The trade pays that fraction of 1R before the market "
            "moves; the P0 gate (risk.max_cost_r={}) is what holds it down.".format(
                med, att.tca["mean_cost_in_r"] or 0.0,
                facts.config.get("slippage_pct_per_side") or 0.0,
                "{:.3f}R".format(all_in) if all_in is not None else "n/a",
                facts.config.get("max_cost_r")),
            "mechanical", n=len(cost_r), actionable=True, priority=25))
        att.tca["mean_all_in_cost_in_r"] = all_in
    if gross_abs:
        share = cost / gross_abs
        att.findings.append(Finding(
            "Cost share of |gross| = {:.0f}% (pre-registered P0 bar: <40% over 10 gated "
            "sessions).".format(share * 100.0), "mechanical", n=len(trades), actionable=True,
            priority=30))
    if shortfalls:
        att.findings.append(Finding(
            "Implementation shortfall: fills landed {:+.3f}% adverse to the surfaced plan price "
            "on average — consistent with the modelled {:.2f}%/side slippage.".format(
                att.tca["mean_impl_shortfall_pct"] or 0.0,
                facts.config.get("slippage_pct_per_side") or 0.0),
            "mechanical", n=len(shortfalls), actionable=True, priority=35))
    if friction_decided:
        att.findings.append(Finding(
            "{} of {} trades were GROSS winners and NET losers — friction alone decided the "
            "outcome ({}).".format(len(friction_decided), len(trades),
                                   ", ".join(f.symbol for f in friction_decided)),
            "mechanical", n=len(trades), actionable=True, priority=20))


def _governance(att: Attribution, facts, trades: List[Dict]) -> None:
    """Concentration, effective diversification, bursts, drawdown, halt."""
    eq = facts.equity or {}
    open_eq = float(eq.get("open_equity") or 0.0)
    pcts = [(float(t["notional_entry"]) / open_eq * 100.0)
            for t in trades if t.get("notional_entry") and open_eq]
    per_symbol: Dict[str, float] = defaultdict(float)
    for t in trades:
        per_symbol[(t.get("symbol") or "?").upper()] += (_f(t, "pnl_inr") or 0.0)
    minutes: Dict[str, int] = defaultdict(int)
    for t in trades:
        minutes[str(t.get("entry_ts") or "")[:16]] += 1
    bursts = {k: v for k, v in minutes.items() if v > 1}
    worst_symbol = min(per_symbol.items(), key=lambda kv: kv[1]) if per_symbol else None
    best_symbol = max(per_symbol.items(), key=lambda kv: kv[1]) if per_symbol else None
    # Effective diversification: mean position size as a fraction of the book inverted. Two
    # 50%-of-book positions taken in sequence is a 1-name book twice, not a 2-name book.
    eff_div = (100.0 / (sum(pcts) / len(pcts))) if pcts else None
    att.governance = {
        "max_position_pct_of_book": max(pcts) if pcts else None,
        "mean_position_pct_of_book": (sum(pcts) / len(pcts)) if pcts else None,
        "effective_concurrent_names": eff_div,
        "n_symbols": len(per_symbol),
        "best_symbol": best_symbol, "worst_symbol": worst_symbol,
        "single_name_share_of_loss": (
            abs(worst_symbol[1]) / abs(sum(per_symbol.values()))
            if worst_symbol and sum(per_symbol.values()) else None),
        "burst_minutes": bursts,
        "intraday_maxdd_pct": eq.get("intraday_maxdd_pct"),
        "halted": bool(facts.halt),
        "halt_message": (facts.halt or {}).get("message"),
        "daily_loss_pct_config": facts.config.get("daily_loss_pct"),
        "max_concurrent_positions_config": facts.config.get("max_concurrent_positions"),
        "affordability_skips": facts.log.skips_affordability,
    }
    if pcts:
        att.findings.append(Finding(
            "Concentration: largest position was {:.0f}% of the book, mean {:.0f}% — an effective "
            "breadth of ~{:.1f} names. With risk_per_trade_pct={} and the P0 gate forcing stops "
            ">=~0.57%, position notional is structurally {:.0f}%-of-book scale: the book is a "
            "sequence of single-name bets, not a diversified portfolio.".format(
                max(pcts), sum(pcts) / len(pcts), eff_div or 0.0,
                facts.config.get("risk_per_trade_pct"),
                (facts.config.get("risk_per_trade_pct") or 0.5)
                / max(0.57, facts.config.get("min_stop_pct") or 0.57) * 100.0),
            "mechanical", n=len(pcts), actionable=True, priority=10))
    if facts.log.skips_affordability:
        att.findings.append(Finding(
            "{} affordability skips: the book had no free cash for even ONE share while further "
            "setups were firing. Trade selection was decided by cash exhaustion, not by signal "
            "quality — the max_concurrent_positions={} cap never bound.".format(
                facts.log.skips_affordability,
                facts.config.get("max_concurrent_positions")),
            "mechanical", n=facts.log.skips_affordability, actionable=True, priority=12))
    if facts.halt:
        att.findings.append(Finding(
            "Session HALTED: {}".format(str(facts.halt.get("message"))[:200]),
            "ops", n=len(trades), actionable=True, priority=5))
    if worst_symbol and sum(per_symbol.values()) and abs(worst_symbol[1]) > 0:
        share = abs(worst_symbol[1]) / abs(sum(per_symbol.values()))
        if share > 0.4:
            att.findings.append(Finding(
                "Single-name concentration of outcome: {} accounted for {:.0f}% of the day's ₹ "
                "move. One name drove the session — the standing 'one name dominates each "
                "session' pattern, not a strategy signal.".format(worst_symbol[0], share * 100.0),
                "statistical", n=len(trades), actionable=False,
                tag="LOW-N / NOT ACTIONABLE"))
    if bursts:
        att.findings.append(Finding(
            "Correlated-basket risk: {} minute(s) fired more than one entry ({}). Same-minute "
            "entries are one bet, not several — effective independent N is below the trade "
            "count.".format(len(bursts), ", ".join(sorted(bursts)[:4])),
            "mechanical", n=len(trades), actionable=True, priority=40))


def _pre_vs_post(att: Attribution, facts, trades: List[Dict]) -> None:
    """Did the plan's expectations survive contact with the market? Optimism is a finding."""
    exp, real, rr_planned = [], [], []
    for t in trades:
        plan = match_entry_plan(facts.predictions, str(t.get("symbol") or ""),
                                str(t.get("entry_ts") or ""))
        if plan and plan.get("expected_move_pct") is not None:
            exp.append(float(plan["expected_move_pct"]))
            real.append(abs(_f(t, "pnl_pct_gross") or 0.0))
        if plan and plan.get("risk_reward") is not None:
            rr_planned.append(float(plan["risk_reward"]))
    wins = [(_f(t, "pnl_pct_net") or 0.0) for t in trades if (_f(t, "pnl_pct_net") or 0.0) > 0]
    losses = [abs(_f(t, "pnl_pct_net") or 0.0) for t in trades
              if (_f(t, "pnl_pct_net") or 0.0) <= 0]
    realised_payoff = ((sum(wins) / len(wins)) / (sum(losses) / len(losses))
                       if wins and losses else None)
    att.pre_vs_post = {
        "mean_expected_move_pct": (sum(exp) / len(exp)) if exp else None,
        "mean_realised_abs_move_pct": (sum(real) / len(real)) if real else None,
        "mean_planned_rr": (sum(rr_planned) / len(rr_planned)) if rr_planned else None,
        "realised_payoff": realised_payoff,
        "n": len(exp),
    }
    if exp and real:
        att.findings.append(Finding(
            "Pre-trade vs post-trade: plans expected a {:.2f}% move; realised |gross| averaged "
            "{:.2f}%. Planned R:R {:.1f}:1 vs realised payoff {}.".format(
                sum(exp) / len(exp), sum(real) / len(real),
                att.pre_vs_post["mean_planned_rr"] or 0.0,
                "{:.2f}:1".format(realised_payoff) if realised_payoff else
                "n/a (no winners or no losers today)"),
            "statistical", n=len(exp), actionable=False,
            tag="" if len(exp) >= MIN_N_FOR_BUCKET else "LOW-N / NOT ACTIONABLE"))


def _bucket(rows: List[Dict], key: str) -> Bucket:
    return Bucket(
        key=key, n=len(rows),
        wins=len([r for r in rows if (_f(r, "pnl_pct_net") or 0.0) > 0]),
        sum_net_pct=sum((_f(r, "pnl_pct_net") or 0.0) for r in rows),
        sum_r=sum((_f(r, "r_multiple") or 0.0) for r in rows),
        sum_inr=sum((_f(r, "pnl_inr") or 0.0) for r in rows),
    )


def _stop_band(row: Dict) -> str:
    sp = _stop_pct(row)
    if sp is None:
        return "stop: unknown"
    for lo, hi in ((0.0, 0.5), (0.5, 0.8), (0.8, 1.2)):
        if lo <= sp < hi:
            return "stop {:.1f}-{:.1f}%".format(lo, hi)
    return "stop >=1.2%"


def _buckets(att: Attribution, facts, trades: List[Dict]) -> None:
    """Bucket the session AND the trailing window. Session buckets are always low-n by design.

    Trailing buckets are where a bucket can eventually earn n>=20 — which is exactly why they
    are computed here and the session ones are labelled, not acted on.
    """
    dims = {
        "exit_reason": lambda r: str(r.get("exit_reason") or "?"),
        "direction": lambda r: str(r.get("direction") or "?"),
        "hour": lambda r: str(r.get("entry_ts") or "")[11:13] + ":xx",
        "symbol": lambda r: str(r.get("symbol") or "?"),
        "stop_band": _stop_band,
        "rules": lambda r: "|".join(sorted(_rule_key(facts, r))) or "no rules logged",
    }
    for scope, rows in (("session", trades), ("trailing", facts.trailing.get("trades") or [])):
        for dim, fn in dims.items():
            groups: Dict[str, List[Dict]] = defaultdict(list)
            for r in rows:
                groups[fn(r)].append(r)
            buckets = sorted((_bucket(v, k) for k, v in groups.items()),
                             key=lambda b: b.sum_net_pct)
            att.buckets["{}:{}".format(scope, dim)] = buckets
    for name, buckets in att.buckets.items():
        if not name.startswith("trailing:"):
            continue
        dim = name.split(":", 1)[1]
        # `exit_reason` is definitionally circular (a STOP exit loses ~1R BECAUSE it is a stop),
        # and `symbol` is the survivorship trap that killed the universe allowlist three times.
        # Both stay in the tables below but must never become a "finding".
        if dim in ("exit_reason", "symbol"):
            continue
        for b in buckets:
            if b.actionable and b.avg_r is not None and abs(b.avg_r) > 0.25:
                prior = ALREADY_KILLED_DIMENSIONS.get(dim, "")
                att.findings.append(Finding(
                    "Trailing bucket {} ({}): avgR {:+.3f} over {} trades — n clears the n>=20 "
                    "floor, so this is a candidate for a pre-registered test, NOT a change to "
                    "apply.{}".format(b.key, dim, b.avg_r, b.n, prior),
                    "statistical", n=b.n,
                    sessions=facts.trailing.get("sessions"), actionable=False,
                    tag="NEEDS PRE-REGISTERED TEST"))


def _rule_key(facts, row: Dict) -> List[str]:
    """The rule combination that fired for this trade (from the entry alert's reasons JSON)."""
    plan = match_entry_plan(facts.predictions, str(row.get("symbol") or ""),
                            str(row.get("entry_ts") or ""))
    return [str(x) for x in ((plan or {}).get("reasons_decoded") or [])]


def _trailing_findings(att: Attribution, facts) -> None:
    """Claims that need >=10 sessions get made here, or explicitly deferred."""
    tr = facts.trailing or {}
    sessions = int(tr.get("sessions") or 0)
    rows = tr.get("trades") or []
    if not rows:
        return
    net = sum((_f(r, "pnl_pct_net") or 0.0) for r in rows)
    gross = sum((_f(r, "pnl_pct_gross") or 0.0) for r in rows)
    cost = sum((_f(r, "cost_pct") or 0.0) for r in rows)
    wins = len([r for r in rows if (_f(r, "pnl_pct_net") or 0.0) > 0])
    avg_r = sum((_f(r, "r_multiple") or 0.0) for r in rows) / len(rows)
    enough = sessions >= MIN_SESSIONS_FOR_LIVE_CLAIM
    from signal_engine.desk.gap import GATED_ERA_START

    days = [d for d in (tr.get("days") or [])]
    spans_regimes = any(d < GATED_ERA_START for d in days) and any(
        d >= GATED_ERA_START for d in days)
    caveat = ""
    if spans_regimes:
        caveat = (" CAVEAT: this window SPANS the {} configuration change (the P0 friction-in-R "
                  "gate), so it mixes a ~15-trades/day regime with a ~5-trades/day one. It is "
                  "context, NOT a verdict on the engine as configured — use the `gated` scope "
                  "for that.".format(GATED_ERA_START))
        enough = False
    att.findings.append(Finding(
        "Trailing {} sessions / {} trades: gross {:+.2f}%, friction {:.2f}%, net {:+.2f}%; "
        "win rate {:.1f}%, avgR {:+.3f}. {}{}".format(
            sessions, len(rows), gross, cost, net, wins / len(rows) * 100.0, avg_r,
            "This clears the >=10-session floor for a live-behaviour claim."
            if enough else
            "Below the >=10-session floor — descriptive only, not a verdict on the strategy.",
            caveat),
        "statistical", n=len(rows), sessions=sessions, actionable=enough,
        tag="MIXED REGIME" if spans_regimes else ("" if enough else "INSUFFICIENT SESSIONS")))
