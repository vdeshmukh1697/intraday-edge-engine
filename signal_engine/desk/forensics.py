"""Per-trade interrogation: the "intelligent questions", answered with data not opinion.

For every trade the desk answers, from the archived 1-min bars and the persisted decision
record:

* **Why this stock?**  decode the `reasons` JSON the strategy stamped on the plan, plus the
  ENTRY-CTX features, plus whether a tip-channel mention was involved.
* **Was the entry timing good?**  where the fill sat inside its own bar's range and inside the
  day's range, and what the tape did in the +-15 minutes around it.
* **Was the exit right?**  MFE/MAE (max favourable / adverse excursion) from entry to
  square-off, the fraction of MFE actually captured, and counterfactual exits.
* **Did friction decide the outcome?**  `cost_pct` vs `|pnl_pct_gross|` — would this trade have
  won gross and lost net?
* **Was it alpha or the tide?**  `alpha_pct` vs `sign x nifty_ret_pct`.
* **Was sizing right?**  planned `rupee_risk` vs realised ₹, and position as a % of the book.

The counterfactual replay REUSES `signal_engine/research/exit_variants.simulate` — the harness
already validated against recorded outcomes (median |diff| 0.000pp over 108 trades, see
STRATEGY_IMPROVEMENT_PLAN_2026-07 §P4). Re-implementing the fill conventions here would risk a
second, unvalidated pessimism model, so we import theirs and only vary the stop distance.

**Every counterfactual is in-sample and hindsight-contaminated by construction.** `optimal`
in particular is an unattainable upper bound (it exits at the best bar of the window with
perfect foresight). They are scale, never proof; `attribution.py` refuses to promote any of
them into a finding without n and a pre-registered test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

_SIGN = {"LONG": 1.0, "SHORT": -1.0}

# Stop-width counterfactual multipliers. 1.5x / 0.67x are round, pre-chosen numbers — not
# swept — precisely so this cannot become a stop-width optimiser. The question being asked is
# "was the stop obviously mis-placed?", not "what stop maximises the in-sample book?".
WIDER_MULT = 1.5
TIGHTER_MULT = 0.67

AROUND_ENTRY_MIN = 15   # window for the "what happened around the entry" read


@dataclass
class TradeForensics:
    """One trade, fully interrogated. Fields are None when the archive lacks the bars."""

    trade_id: str
    symbol: str
    direction: str
    entry_ts: str
    exit_ts: Optional[str]
    exit_reason: Optional[str]
    stop_pct: Optional[float]
    hold_minutes: Optional[float]
    pnl_pct_gross: Optional[float]
    cost_pct: Optional[float]
    pnl_pct_net: Optional[float]
    r_multiple: Optional[float]
    pnl_inr: Optional[float]
    charges_inr: Optional[float]
    notional_entry: Optional[float]
    pct_of_book: Optional[float] = None
    rupee_risk_planned: Optional[float] = None
    why: List[str] = field(default_factory=list)
    reason_plain: Optional[str] = None
    tg_mentions: Optional[int] = None
    bars_available: bool = False
    entry_in_bar_pct: Optional[float] = None     # 0 = bar low, 100 = bar high
    entry_in_day_pct: Optional[float] = None     # position in the day's range at entry
    move_before_entry_pct: Optional[float] = None
    move_after_entry_pct: Optional[float] = None
    mfe_pct: Optional[float] = None              # max favourable excursion, gross %
    mae_pct: Optional[float] = None              # max adverse excursion, gross % (negative)
    mfe_r: Optional[float] = None
    mae_r: Optional[float] = None
    captured_frac: Optional[float] = None        # realised gross / MFE
    variants: Dict[str, Optional[float]] = field(default_factory=dict)
    friction_decided: bool = False
    friction_share_of_gross: Optional[float] = None
    alpha_pct: Optional[float] = None
    tide_pct: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    def question_answers(self) -> List[str]:
        """The desk's standing questions, answered in one line each (report-ready)."""
        out: List[str] = []
        out.append("Why this stock: " + (", ".join(self.why) if self.why else "no reasons logged"))
        if self.entry_in_bar_pct is not None:
            slipped = (" (>100% = adverse slippage carried the fill outside the bar's range, "
                       "which is the modelled behaviour, not an error)"
                       if self.entry_in_bar_pct > 100 else "")
            out.append("Entry timing: filled at {:.0f}% of the entry bar's range{}, {:.0f}% of "
                       "the day's range so far; tape moved {:+.2f}% in the {}min before and "
                       "{:+.2f}% in the {}min after".format(
                           self.entry_in_bar_pct, slipped, self.entry_in_day_pct or 0.0,
                           self.move_before_entry_pct or 0.0, AROUND_ENTRY_MIN,
                           self.move_after_entry_pct or 0.0, AROUND_ENTRY_MIN))
        else:
            out.append("Entry timing: no archived bars for this symbol/day — not assessable")
        if self.mfe_pct is not None:
            if self.mfe_pct <= 0:
                captured = ("MFE was never positive, so the trade never went the right way at any "
                            "point and no exit rule could have saved it")
            elif self.captured_frac is None or self.captured_frac <= 0:
                captured = ("it was up {:+.2f}% at best and still closed negative — the whole "
                            "favourable excursion was given back".format(self.mfe_pct))
            else:
                captured = "captured {:.0f}% of the favourable excursion".format(
                    self.captured_frac * 100.0)
            out.append("Exit: MFE {:+.2f}% ({:+.2f}R) / MAE {:+.2f}% ({:+.2f}R); {}".format(
                self.mfe_pct, self.mfe_r or 0.0, self.mae_pct or 0.0, self.mae_r or 0.0,
                captured))
        if self.friction_decided:
            out.append("Friction DECIDED this trade: gross {:+.3f}% vs cost {:.3f}% — a gross "
                       "winner turned into a net loser".format(self.pnl_pct_gross or 0.0,
                                                               self.cost_pct or 0.0))
        elif self.friction_share_of_gross is not None:
            out.append("Friction: cost {:.3f}% = {:.0f}% of |gross|".format(
                self.cost_pct or 0.0, self.friction_share_of_gross * 100.0))
        if self.alpha_pct is not None:
            out.append("Alpha vs tide: alpha {:+.2f}%, index contribution {:+.2f}%".format(
                self.alpha_pct, self.tide_pct or 0.0))
        if self.pct_of_book is not None:
            out.append("Sizing: {:.0f}% of the book at entry (₹{:,.0f} notional), planned risk "
                       "₹{:,.0f}, realised ₹{:,.0f}".format(
                           self.pct_of_book, self.notional_entry or 0.0,
                           self.rupee_risk_planned or 0.0, self.pnl_inr or 0.0))
        return out


def _bars(store, symbol: str, day: date, cache: Dict):
    key = (symbol, day)
    if key not in cache:
        try:
            cache[key] = store.load_session(symbol, day)
        except Exception:  # noqa: BLE001 - a missing/corrupt parquet must not kill the review
            cache[key] = None
    return cache[key]


def match_entry_plan(preds: Dict[str, List[Dict]], symbol: str,
                     entry_ts: str) -> Optional[Dict]:
    """The `entry` prediction row that produced THIS fill: same symbol, nearest ts.

    Nearest-in-time matters: a symbol can be traded several times in a session (HFCL fired three
    times on 2026-07-22 at 221.31 / 220.08 / 223.52). Taking the last match for the symbol — the
    obvious shortcut — silently compares trade 1's fill against trade 3's plan price and invents a
    huge implementation shortfall out of nothing. Shared by `forensics` and `attribution` so both
    match identically.
    """
    import datetime as _dt

    def _parse(ts: str):
        try:
            return _dt.datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            return None

    target = _parse(entry_ts)
    best, best_delta = None, None
    for p in preds.get("entry", []):
        if (p.get("symbol") or "").upper() != symbol.upper():
            continue
        pts = _parse(str(p.get("ts") or ""))
        if pts is None or target is None:
            if best is None:
                best = p
            continue
        delta = abs((pts - target).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = p, delta
    return best


# Back-compat alias for the internal call sites below.
_entry_plan = match_entry_plan


def interrogate(facts, store=None, cfg=None) -> List[TradeForensics]:
    """Interrogate every trade in ``facts``. Works with no bar store (fields stay None)."""
    import pandas as pd

    if store is None and cfg is not None:
        try:
            from signal_engine.storage.bars import ParquetBarStore

            store = ParquetBarStore(cfg.env.parquet_dir)
        except Exception:  # noqa: BLE001
            store = None
    cache: Dict = {}
    out: List[TradeForensics] = []
    open_equity = float((facts.equity or {}).get("open_equity") or 0.0)

    for t in facts.trades:
        sym = str(t.get("symbol") or "")
        direction = str(t.get("direction") or "LONG")
        sign = _SIGN.get(direction, 1.0)
        entry_fill = t.get("entry_fill")
        stop_loss = t.get("stop_loss")
        stop_pct = (abs(float(entry_fill) - float(stop_loss)) / float(entry_fill) * 100.0
                    if entry_fill and stop_loss else None)
        gross = t.get("pnl_pct_gross")
        cost = t.get("cost_pct")
        plan = _entry_plan(facts.predictions, sym, str(t.get("entry_ts") or ""))
        f = TradeForensics(
            trade_id=str(t.get("id")), symbol=sym, direction=direction,
            entry_ts=str(t.get("entry_ts") or ""), exit_ts=t.get("exit_ts"),
            exit_reason=t.get("exit_reason"), stop_pct=stop_pct,
            hold_minutes=t.get("hold_minutes"), pnl_pct_gross=gross, cost_pct=cost,
            pnl_pct_net=t.get("pnl_pct_net"), r_multiple=t.get("r_multiple"),
            pnl_inr=t.get("pnl_inr"), charges_inr=t.get("charges_inr"),
            notional_entry=t.get("notional_entry"), alpha_pct=t.get("alpha_pct"),
        )
        if t.get("nifty_ret_pct") is not None:
            f.tide_pct = sign * float(t["nifty_ret_pct"])
        if f.notional_entry and open_equity:
            f.pct_of_book = float(f.notional_entry) / open_equity * 100.0
        if plan:
            f.why = list(plan.get("reasons_decoded") or [])
            f.reason_plain = plan.get("reason_plain")
            f.rupee_risk_planned = plan.get("rupee_risk")
        # Friction verdict — arithmetic, not statistics.
        if gross is not None and cost is not None:
            g = float(gross)
            f.friction_share_of_gross = (float(cost) / abs(g)) if g else None
            f.friction_decided = bool(g > 0 and (g - float(cost)) <= 0)

        # ---- bar-archive dependent: MFE/MAE, entry placement, counterfactual exits ----
        if store is None or not entry_fill or not f.entry_ts:
            f.notes.append("no bar archive available — excursion and counterfactuals skipped")
            out.append(f)
            continue
        try:
            day = date.fromisoformat(f.entry_ts[:10])
        except ValueError:
            out.append(f)
            continue
        bars = _bars(store, sym, day, cache)
        if bars is None or len(bars) == 0:
            f.notes.append("symbol missing from the {} archive — excursion not assessable"
                           .format(day.isoformat()))
            out.append(f)
            continue
        f.bars_available = True
        ets = pd.Timestamp(f.entry_ts)
        xts = pd.Timestamp(f.exit_ts) if f.exit_ts else bars.index.max()
        entry_px = float(entry_fill)

        # Entry placement inside its own bar and inside the day's range so far.
        at_or_before = bars[bars.index <= ets]
        if len(at_or_before):
            b = at_or_before.iloc[-1]
            rng = float(b["high"]) - float(b["low"])
            if rng > 0:
                f.entry_in_bar_pct = (entry_px - float(b["low"])) / rng * 100.0
            d_hi, d_lo = float(at_or_before["high"].max()), float(at_or_before["low"].min())
            if d_hi > d_lo:
                f.entry_in_day_pct = (entry_px - d_lo) / (d_hi - d_lo) * 100.0
            pre = bars[(bars.index >= ets - timedelta(minutes=AROUND_ENTRY_MIN))
                       & (bars.index <= ets)]
            if len(pre) > 1:
                first = float(pre["close"].iloc[0])
                if first:
                    f.move_before_entry_pct = (entry_px - first) / first * 100.0
        post = bars[(bars.index > ets) & (bars.index <= ets + timedelta(minutes=AROUND_ENTRY_MIN))]
        if len(post):
            f.move_after_entry_pct = sign * (float(post["close"].iloc[-1]) - entry_px) \
                / entry_px * 100.0

        # MFE / MAE over the ACTUAL holding window (entry -> recorded exit).
        held = bars[(bars.index > ets) & (bars.index <= xts)]
        if len(held):
            if sign > 0:
                best, worst = float(held["high"].max()), float(held["low"].min())
            else:
                best, worst = float(held["low"].min()), float(held["high"].max())
            f.mfe_pct = sign * (best - entry_px) / entry_px * 100.0
            f.mae_pct = sign * (worst - entry_px) / entry_px * 100.0
            if stop_pct:
                f.mfe_r = f.mfe_pct / stop_pct
                f.mae_r = f.mae_pct / stop_pct
            if gross is not None and f.mfe_pct and f.mfe_pct > 0:
                f.captured_frac = float(gross) / f.mfe_pct

        f.variants = _counterfactuals(bars, f, t, cfg)
        out.append(f)
    return out


def _counterfactuals(bars, f: TradeForensics, trade: Dict, cfg) -> Dict[str, Optional[float]]:
    """Net-% under alternative exit rules, via the validated exit_variants harness.

    Keys: baseline (harness fidelity check), trail, hold90, wider_stop, tighter_stop, optimal.
    ALL are in-sample; `optimal` is perfect-foresight and therefore an upper bound, not a target.
    """
    from signal_engine.research.exit_variants import simulate

    out: Dict[str, Optional[float]] = {}
    entry_px = float(trade["entry_fill"])
    stop = float(trade["stop_loss"]) if trade.get("stop_loss") else None
    target = float(trade["target"]) if trade.get("target") else None
    if stop is None:
        return out
    sign = _SIGN.get(f.direction, 1.0)
    stop_dist = abs(entry_px - stop)
    # Charges-only cost: the simulated fills already carry adverse slippage (exit_variants.SLIP),
    # exactly as the live PaperTrader does. Using the slippage-inclusive gate model here would
    # double-count — the same two-model invariant pinned in tests/test_engine.py.
    cost = float(f.cost_pct) if f.cost_pct is not None else 0.0
    if cost == 0.0 and cfg is not None:
        try:
            from signal_engine.risk.costs import CostModel

            cost = CostModel(cfg.risk.costs).breakeven_pct(entry_px)
        except Exception:  # noqa: BLE001
            cost = 0.0

    import pandas as pd

    ets = pd.Timestamp(f.entry_ts)
    for name, variant, use_stop, use_target in (
        ("baseline", "baseline", stop, target),
        ("trail", "trail", stop, None),
        ("hold90", "hold90", stop, None),
        ("wider_stop", "baseline",
         entry_px - sign * stop_dist * WIDER_MULT, target),
        ("tighter_stop", "baseline",
         entry_px - sign * stop_dist * TIGHTER_MULT, target),
    ):
        try:
            sim = simulate(bars, f.direction, ets, entry_px, use_stop, use_target, variant)
        except Exception:  # noqa: BLE001
            sim = None
        if sim is None:
            out[name] = None
            continue
        gross = sign * (sim.exit_fill - entry_px) / entry_px * 100.0
        out[name] = gross - cost
    # Perfect-foresight ceiling: exit at the best price reachable inside the actual hold window.
    if f.mfe_pct is not None:
        out["optimal"] = f.mfe_pct - cost
    return out


def rejected_setups(facts) -> Dict:
    """What the gates and the book rejected, and why. A gate that rejects winners is expensive.

    We can only report the reject POPULATION honestly: `predictions.kind='skip'` rows carry the
    plain reason but not a full plan (no stop/target), and gate rejections inside
    `RiskManager.build_trade_plan` are not persisted at all — they never become a plan row. So
    this returns counts and named symbols, and states the instrumentation gap rather than
    inventing a counterfactual P&L for setups whose geometry we never recorded.
    """
    skips = facts.predictions.get("skip", []) or []
    advice = facts.predictions.get("advice", []) or []
    by_symbol: Dict[str, int] = {}
    for s in skips:
        sym = (s.get("symbol") or "?").upper()
        by_symbol[sym] = by_symbol.get(sym, 0) + 1
    traded = {(t.get("symbol") or "").upper() for t in facts.trades}
    surfaced = {(a.get("symbol") or "").upper() for a in advice}
    return {
        "n_skip_alerts": len(skips),
        "n_advice_alerts": len(advice),
        "skips_by_symbol": dict(sorted(by_symbol.items(), key=lambda kv: -kv[1])),
        "surfaced_never_traded": sorted(surfaced - traded),
        "affordability_skips_in_log": facts.log.skips_affordability,
        "entry_evaluations_in_log": facts.log.entry_evaluations,
        "instrumentation_gap": (
            "Gate-level rejections inside RiskManager.build_trade_plan are NOT persisted, and "
            "skip rows carry no stop/target, so the archive cannot price what the gates threw "
            "away. Counterfactual P&L for rejects is UNAVAILABLE, not zero."),
    }
